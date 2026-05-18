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
import datetime
import os
from typing import Optional

import asyncpg
import httpx

from ..aliases import resolve_existing
from ..ean import canonical_ean

log = logging.getLogger("cosicomodo")

API_BASE = "https://api.cosicomodo.it/occ/v2"
IMG_BASE = "https://images.cosicomodo.it"
PAGE_SIZE = 100
RATE = 0.4           # secondi tra richieste (l'API pubblica non throttla forte)

# Negozi scrapati per esecuzione. A catalogo pieno tutti i negozi non stanno
# nei 90 min di CI: se ne fa un sottoinsieme a rotazione (seed = giorno) così
# nell'arco di pochi run si coprono tutti. Override: COSICOMODO_MAX_STORES.
MAX_STORES = int(os.getenv("COSICOMODO_MAX_STORES", "10"))

# Codici categoria top-level (reparti CosìComodo: /c/10001 … /c/10016)
CATEGORY_CODES = [str(c) for c in range(10001, 10017)]

# Catene servite da CosìComodo (slug → nome visualizzato)
CHAIN_NAMES = {
    "famila": "Famila",
    "ilgigante": "Il Gigante",
    "italmark": "Italmark",
}

# Directory con i file negozi: cosicomodo_{chain}_stores.json
_SCRAPING_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    """
    Carica tutti i file cosicomodo_{chain}_stores.json e tagga ogni negozio
    con la catena (ricavata dal nome del file).
    """
    import glob

    stores: list[dict] = []
    pattern = os.path.join(_SCRAPING_DIR, "cosicomodo_*_stores.json")
    for path in sorted(glob.glob(pattern)):
        fname = os.path.basename(path)
        chain = fname[len("cosicomodo_"):-len("_stores.json")]
        with open(path, encoding="utf-8") as fh:
            for s in json.load(fh):
                s["chain"] = chain
                stores.append(s)
    return stores


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

    async def _ensure_store(
        self, chain_id: int, chain_slug: str, store: dict
    ) -> Optional[str]:
        """Upsert del punto vendita nel DB. Ritorna lo store uuid."""
        alias = store["alias"]
        external_id = f"{chain_slug}-{alias}"
        chain_name = CHAIN_NAMES.get(chain_slug, chain_slug.title())
        name = f"{chain_name} " + alias.replace("-", " ").title()
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

    def _normalize(self, p: dict) -> Optional[dict]:
        """Estrae i campi utili da un prodotto OCC, o None se non valido."""
        raw_code = str(p.get("code") or "").strip()
        name = str(p.get("name") or "").strip()
        if not raw_code or not name:
            return None
        current, original, promo_label = self._extract_prices(p)
        if current <= 0:
            return None
        price_obj = p.get("discountedPrice") if p.get("flagPromo") else p.get("price")
        ppu: Optional[float] = None
        if price_obj:
            raw_ppu = price_obj.get("priceReferenceUnit")
            try:
                v = float(raw_ppu) if raw_ppu is not None else None
                if v and v > 0:
                    ppu = round(v, 4)
            except (ValueError, TypeError):
                pass
        return {
            "barcode": canonical_ean(raw_code) or raw_code,
            "_raw_code": raw_code,   # codice OCC reale, per il link prodotto
            "name": name,
            "brand": str(p.get("marca") or "").strip() or None,
            "image_url": self._extract_image(p),
            "price": current,
            "original_price": original,
            "promo_label": promo_label,
            "price_per_unit": ppu,
            "in_stock": bool(p.get("saleable", True)),
        }

    async def _upsert_products_batch(
        self, products: list[dict], store_uuid: str, chain_slug: str
    ) -> int:
        """
        Upsert di una pagina di prodotti in ~5 round-trip DB invece di 4 per
        prodotto (il DB remoto su Render ha ~150ms di latenza per query).
        L'intera pagina è in una transazione.
        """
        by_bc: dict[str, dict] = {}
        for raw in products:
            n = self._normalize(raw)
            if n:
                # link diretto alla scheda prodotto sul sito CosìComodo
                # (lo slug catena coincide col path del sito: famila, …)
                n["product_url"] = (
                    f"https://www.cosicomodo.it/{chain_slug}"
                    f"/p/{n['_raw_code']}"
                )
                by_bc[n["barcode"]] = n
        if not by_bc:
            return 0
        if self.dry_run:
            return len(by_bc)

        barcodes = list(by_bc.keys())
        async with self.conn.transaction():
            # barcode esistenti: prodotti veri + alias del dedup
            id_by_bc, direct_bcs = await resolve_existing(self.conn, barcodes)

            new_bcs = [bc for bc in barcodes if bc not in id_by_bc]
            if new_bcs:
                rows = await self.conn.fetch(
                    """INSERT INTO products (barcode, name, brand, image_url, source)
                       SELECT * FROM unnest($1::text[], $2::text[], $3::text[],
                                            $4::text[], $5::text[])
                       RETURNING id, barcode""",
                    new_bcs,
                    [by_bc[b]["name"] for b in new_bcs],
                    [by_bc[b]["brand"] for b in new_bcs],
                    [by_bc[b]["image_url"] for b in new_bcs],
                    ["cosicomodo"] * len(new_bcs),
                )
                for r in rows:
                    id_by_bc[r["barcode"]] = r["id"]

            # UPDATE solo per i barcode diretti; per gli alias si scrive
            # solo il prezzo, senza toccare il prodotto superstite.
            upd = [bc for bc in barcodes if bc in direct_bcs]
            if upd:
                await self.conn.execute(
                    """UPDATE products AS p SET
                           name = v.name,
                           brand = COALESCE(v.brand, p.brand),
                           image_url = COALESCE(p.image_url, v.image_url),
                           updated_at = NOW()
                       FROM unnest($1::uuid[], $2::text[], $3::text[], $4::text[])
                            AS v(id, name, brand, image_url)
                       WHERE p.id = v.id""",
                    [id_by_bc[b] for b in upd],
                    [by_bc[b]["name"] for b in upd],
                    [by_bc[b]["brand"] for b in upd],
                    [by_bc[b]["image_url"] for b in upd],
                )

            all_ids = [id_by_bc[b] for b in barcodes]
            await self.conn.execute(
                "UPDATE prices SET is_current = FALSE "
                "WHERE store_id = $1 AND product_id = ANY($2::uuid[])",
                store_uuid, all_ids,
            )
            await self.conn.execute(
                """INSERT INTO prices
                       (product_id, store_id, price, original_price, promo_label,
                        price_per_unit, in_stock, is_current, source,
                        product_url, scraped_at)
                   SELECT v.id, $2, v.price, v.orig, v.promo, v.ppu, v.instock,
                          TRUE, 'cosicomodo', v.url, NOW()
                   FROM unnest($1::uuid[], $3::numeric[], $4::numeric[], $5::text[],
                               $6::numeric[], $7::boolean[], $8::text[])
                        AS v(id, price, orig, promo, ppu, instock, url)""",
                all_ids,
                store_uuid,
                [by_bc[b]["price"] for b in barcodes],
                [by_bc[b]["original_price"] for b in barcodes],
                [by_bc[b]["promo_label"] for b in barcodes],
                [by_bc[b]["price_per_unit"] for b in barcodes],
                [by_bc[b]["in_stock"] for b in barcodes],
                [by_bc[b]["product_url"] for b in barcodes],
            )
        return len(barcodes)

    async def _scrape_category(
        self, site: str, alias: str, category_code: str, store_uuid: str,
        chain_slug: str,
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
            products = data.get("products") or []
            try:
                upserted += await self._upsert_products_batch(
                    products, store_uuid, chain_slug
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Errore batch %s/%s cat=%s pag=%d: %s",
                    site, alias, category_code, page, exc,
                )
            page += 1
        return upserted

    # ── Entry point ───────────────────────────────────────────────────────────

    async def scrape_prices(self) -> int:
        # chain_id per ogni catena CosìComodo presente
        chain_ids: dict[str, int] = {}
        for slug in {s["chain"] for s in self._stores}:
            cid = await self.conn.fetchval(
                "SELECT id FROM chains WHERE slug = $1", slug
            )
            if cid:
                chain_ids[slug] = cid
            else:
                log.warning("Chain '%s' non trovata nel DB — negozi saltati", slug)

        # Sottoinsieme a rotazione: si scrapano i negozi aggiornati MENO di
        # recente (prima i mai scrapati). Così run ripetuti — anche nello
        # stesso giorno — coprono negozi diversi e in pochi giri si completa
        # l'intera rete, senza sforare il timeout di CI.
        stores = [s for s in self._stores if s["chain"] in chain_ids]
        if 0 < MAX_STORES < len(stores):
            rows = await self.conn.fetch(
                """SELECT s.external_id, MAX(p.scraped_at) AS last
                   FROM stores s
                   LEFT JOIN prices p ON p.store_id = s.id
                   GROUP BY s.external_id"""
            )
            last_by_ext = {r["external_id"]: r["last"] for r in rows}
            epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
            stores.sort(
                key=lambda s: last_by_ext.get(f"{s['chain']}-{s['alias']}") or epoch
            )
            stores = stores[:MAX_STORES]
        log.info(
            "Negozi CosìComodo da scrapare: %d (su %d totali)",
            len(stores), len(self._stores),
        )
        total = 0
        for i, store in enumerate(stores, start=1):
            chain_slug = store["chain"]
            store_uuid = await self._ensure_store(
                chain_ids[chain_slug], chain_slug, store
            )
            if not store_uuid:
                continue
            site, alias = store["site"], store["alias"]

            # Categorie SEQUENZIALI: una connessione asyncpg non supporta
            # operazioni concorrenti ("another operation is in progress").
            store_total = 0
            for code in CATEGORY_CODES:
                try:
                    store_total += await self._scrape_category(
                        site, alias, code, store_uuid, chain_slug
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("  errore categoria %s: %s", code, exc)
            log.info(
                "[%d/%d] %s %s (%s): %d prezzi",
                i, len(stores), CHAIN_NAMES.get(chain_slug, chain_slug),
                alias, site, store_total,
            )
            total += store_total

        log.info("=== CosìComodo: %d prezzi totali ===", total)
        return total

    async def run(self) -> None:
        await self.scrape_prices()
