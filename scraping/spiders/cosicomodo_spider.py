"""
CosiComodoSpider — prezzi Famila via API OCC (SAP Commerce / CosìComodo).

Flusso:
  1. Carica la lista negozi da scraping/cosicomodo_famila_stores.json
     (74 punti vendita Famila, catturati dal selettore negozi del sito;
     ogni voce ha il baseSiteId reale e lo storeAliasId reale).
  2. Per ogni negozio: upsert nel DB (chain 'famila', click & collect).
  3. Per ogni categoria (10001-10016) e pagina:
     GET https://api.cosicomodo.it/occ/v2/{site}/stores/{alias}/
         users/anonymous/products/search-by-category
         ?facet=:relevance&currentPage={n}&pageSize=100&fields=FULL&categoryCode={code}
  4. Upsert prodotto (barcode = EAN reale) + prezzo.

L'API OCC è pubblica (users/anonymous), nessuna sessione richiesta.

Storia: la versione precedente indovinava i baseSiteId dalla sitemap
(famila, familanord, …) e derivava lo storeAlias dalla città in kebab-case.
Entrambi sbagliati: il baseSite corretto è familanord/familanordest/
familasud/familaadriatica, e l'alias NON è la città (es. Crema →
'crema-maria-gaeta'). La mappatura reale è ora nel file JSON.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import Optional

import asyncpg
import httpx

from ..ean import canonical_ean

log = logging.getLogger("cosicomodo")

API_BASE = "https://api.cosicomodo.it/occ/v2"
IMG_BASE = "https://images.cosicomodo.it"
CHAIN_SLUG = "famila"
PAGE_SIZE = 100
RATE = 0.4           # secondi tra richieste (l'API pubblica non throttla forte)
CAT_CONCURRENCY = 3  # categorie in parallelo per negozio

# Negozi scrapati per esecuzione. 74 negozi a catalogo pieno non stanno nei
# 90 min di CI: se ne fa un sottoinsieme a rotazione (seed = giorno) così
# nell'arco di pochi run si coprono tutti. Override: COSICOMODO_MAX_STORES.
MAX_STORES = int(os.getenv("COSICOMODO_MAX_STORES", "10"))

# Codici categoria top-level (reparti CosìComodo: /c/10001 … /c/10016)
CATEGORY_CODES = [str(c) for c in range(10001, 10017)]

# File con la lista negozi (site, alias, lat, lng)
_STORES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "cosicomodo_famila_stores.json",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "it-IT,it;q=0.9",
    "Origin": "https://www.cosicomodo.it",
    "Referer": "https://www.cosicomodo.it/",
}


def _load_stores() -> list[dict]:
    with open(_STORES_FILE, encoding="utf-8") as fh:
        return json.load(fh)


class CosiComodoSpider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        conn: asyncpg.Connection,
        dry_run: bool = False,
    ):
        self.client = client
        self.conn = conn
        self.dry_run = dry_run
        self._t_last = 0.0
        self._stores = _load_stores()

    # ── HTTP ─────────────────────────────────────────────────────────────────

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._t_last
        if elapsed < RATE:
            await asyncio.sleep(RATE - elapsed)
        self._t_last = loop.time()

    async def _get_json(
        self, url: str, params: dict | None = None, timeout: float = 30
    ) -> dict | None:
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.get(
                    url, headers=HEADERS, params=params,
                    timeout=timeout, follow_redirects=True,
                )
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (400, 401, 403, 404):
                    return None
                log.warning("HTTP %s %s (tentativo %d)", r.status_code, url, attempt + 1)
            except (httpx.RequestError, Exception) as exc:  # noqa: BLE001
                log.warning("Tentativo %d errore: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    # ── Store DB upsert ───────────────────────────────────────────────────────

    async def _ensure_store(self, chain_id: int, store: dict) -> Optional[str]:
        """Upsert del punto vendita Famila nel DB. Ritorna lo store uuid."""
        alias = store["alias"]
        external_id = f"famila-{alias}"
        name = "Famila " + alias.replace("-", " ").title()
        city = alias.split("-")[0].replace("_", " ").title()

        row = await self.conn.fetchrow(
            "SELECT id FROM stores WHERE chain_id = $1 AND external_id = $2",
            chain_id, external_id,
        )
        if row:
            return str(row["id"])
        if self.dry_run:
            return "00000000-0000-0000-0000-000000000000"

        new_id = await self.conn.fetchval(
            """
            INSERT INTO stores
                (chain_id, external_id, name, address, city, coordinates,
                 has_delivery, has_click_collect, is_active)
            VALUES
                ($1, $2, $3, $4, $5,
                 ST_SetSRID(ST_MakePoint($6, $7), 4326),
                 FALSE, TRUE, TRUE)
            RETURNING id
            """,
            chain_id, external_id, name, name, city,
            store["lng"], store["lat"],
        )
        log.info("Creato negozio %s", name)
        return str(new_id)

    # ── Prezzi ────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_prices(p: dict) -> tuple[float, Optional[float], Optional[str]]:
        """Ritorna (prezzo_corrente, prezzo_originale, etichetta_promo)."""
        list_raw = (p.get("price") or {}).get("value")
        disc_raw = (p.get("discountedPrice") or {}).get("value")
        flag_promo = bool(p.get("flagPromo"))
        try:
            list_price = float(list_raw) if list_raw is not None else None
        except (ValueError, TypeError):
            list_price = None
        try:
            disc_price = float(disc_raw) if disc_raw is not None else None
        except (ValueError, TypeError):
            disc_price = None

        if flag_promo and disc_price is not None and disc_price > 0:
            current = disc_price
            original = list_price if (list_price and list_price > disc_price) else None
        elif list_price is not None and list_price > 0:
            current, original = list_price, None
        else:
            return 0.0, None, None

        promo_label: Optional[str] = None
        stickers = p.get("stickers") or []
        if stickers and isinstance(stickers[0], dict):
            promo_label = stickers[0].get("label")
        if not promo_label and flag_promo:
            promo_label = "Promo"
        return current, original, promo_label

    @staticmethod
    def _extract_image(p: dict) -> Optional[str]:
        imgs = p.get("productImages") or []
        if isinstance(imgs, list) and imgs:
            first = imgs[0]
            url = first.get("url") if isinstance(first, dict) else None
            if url:
                return url if url.startswith("http") else IMG_BASE + url
        return None

    async def _upsert_product_price(self, p: dict, store_uuid: str) -> bool:
        raw_code = str(p.get("code") or "").strip()
        if not raw_code:
            return False
        barcode = canonical_ean(raw_code) or raw_code

        name = str(p.get("name") or "").strip()
        if not name:
            return False
        brand = str(p.get("marca") or "").strip() or None
        in_stock = bool(p.get("saleable", True))

        current, original, promo_label = self._extract_prices(p)
        if current <= 0:
            return False

        price_obj = p.get("discountedPrice") if p.get("flagPromo") else p.get("price")
        price_per_unit: Optional[float] = None
        if price_obj:
            raw_ppu = price_obj.get("priceReferenceUnit")
            try:
                ppu = float(raw_ppu) if raw_ppu is not None else None
                if ppu and ppu > 0:
                    price_per_unit = round(ppu, 4)
            except (ValueError, TypeError):
                pass

        image_url = self._extract_image(p)

        if self.dry_run:
            log.info("[DRY] %-55s  €%.2f", name[:55], current)
            return True

        prod_id = await self.conn.fetchval(
            "SELECT id FROM products WHERE barcode = $1 LIMIT 1", barcode
        )
        if prod_id is None:
            prod_id = await self.conn.fetchval(
                """INSERT INTO products (barcode, name, brand, image_url, source)
                   VALUES ($1, $2, $3, $4, 'cosicomodo') RETURNING id""",
                barcode, name, brand, image_url,
            )
        else:
            await self.conn.execute(
                """UPDATE products
                      SET name = $2,
                          brand = COALESCE($3, brand),
                          image_url = COALESCE(image_url, $4),
                          updated_at = NOW()
                    WHERE id = $1""",
                prod_id, name, brand, image_url,
            )

        await self.conn.execute(
            "UPDATE prices SET is_current = FALSE "
            "WHERE product_id = $1 AND store_id = $2",
            prod_id, store_uuid,
        )
        await self.conn.execute(
            """INSERT INTO prices
                   (product_id, store_id, price, original_price, promo_label,
                    price_per_unit, in_stock, is_current, source, scraped_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, 'cosicomodo', NOW())""",
            prod_id, store_uuid, current, original, promo_label,
            price_per_unit, in_stock,
        )
        return True

    async def _scrape_category(
        self, site: str, alias: str, category_code: str, store_uuid: str
    ) -> int:
        """Scarica tutte le pagine di una categoria. Ritorna i prezzi scritti."""
        url = (
            f"{API_BASE}/{site}/stores/{alias}"
            "/users/anonymous/products/search-by-category"
        )
        upserted = 0
        page = 0
        total_pages = 1
        while page < total_pages:
            data = await self._get_json(
                url,
                params={
                    "facet": ":relevance",
                    "currentPage": page,
                    "pageSize": PAGE_SIZE,
                    "fields": "FULL",
                    "categoryCode": category_code,
                },
            )
            if not data:
                break
            total_pages = (data.get("pagination") or {}).get("totalPages", 1)
            for p in data.get("products") or []:
                try:
                    if await self._upsert_product_price(p, store_uuid):
                        upserted += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("Errore prodotto %s: %s", p.get("code"), exc)
            page += 1
        return upserted

    # ── Entry point ───────────────────────────────────────────────────────────

    async def scrape_prices(self) -> int:
        chain_id = await self.conn.fetchval(
            "SELECT id FROM chains WHERE slug = $1", CHAIN_SLUG
        )
        if not chain_id:
            log.error("Chain '%s' non trovata nel DB", CHAIN_SLUG)
            return 0

        # Sottoinsieme a rotazione: i negozi scrapati cambiano ogni giorno,
        # così in pochi run si copre l'intera rete senza sforare il timeout.
        stores = list(self._stores)
        if 0 < MAX_STORES < len(stores):
            import datetime
            rnd = random.Random(datetime.date.today().toordinal())
            rnd.shuffle(stores)
            stores = stores[:MAX_STORES]
        log.info(
            "Negozi Famila da scrapare: %d (su %d totali)",
            len(stores), len(self._stores),
        )
        total = 0
        for i, store in enumerate(stores, start=1):
            store_uuid = await self._ensure_store(chain_id, store)
            if not store_uuid:
                continue
            site, alias = store["site"], store["alias"]
            sem = asyncio.Semaphore(CAT_CONCURRENCY)

            async def _scrape_cat(code: str) -> int:
                async with sem:
                    return await self._scrape_category(site, alias, code, store_uuid)

            results = await asyncio.gather(
                *[_scrape_cat(c) for c in CATEGORY_CODES],
                return_exceptions=True,
            )
            store_total = 0
            for res in results:
                if isinstance(res, Exception):
                    log.warning("  errore categoria: %s", res)
                elif isinstance(res, int):
                    store_total += res
            log.info(
                "[%d/%d] Famila %s (%s): %d prezzi",
                i, len(stores), alias, site, store_total,
            )
            total += store_total

        log.info("=== CosìComodo: %d prezzi totali ===", total)
        return total

    async def run(self) -> None:
        await self.scrape_prices()
