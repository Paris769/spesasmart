"""
Aldi Italy catalog scraper (API ASL del nuovo sito).

Storia: fino ad agosto 2026 lo spider faceva parsing HTML delle tile offerta
della homepage (selettore ".item.plp_product"). Il sito è stato rifatto
(Nuxt + API asl.api.aldi.it): le vecchie tile e gli URL /it/*.html non
esistono più e l'edge Akamai risponde 403 alle richieste con set di header
minimale — risultato: 0 prodotti da fine luglio 2026. Ora si usa la stessa
API JSON pubblica chiamata dal frontend (nessuna autenticazione):

    GET https://asl.api.aldi.it/commerce/v3/product-search
        ?currency=EUR&serviceType=walk-in&limit=60&offset=N

che espone l'intero catalogo "walk-in" (~600 prodotti) con prezzo (in
centesimi), prezzo comparativo al kg/l, promo (wasPriceDisplay) e immagini.
I prodotti finiscono nel negozio virtuale "Aldi Offerte" come prima.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import asyncpg
import httpx

from ..aliases import preserve_flyer_promos, resolve_existing

log = logging.getLogger("aldi")

BASE_URL = "https://www.aldi.it"
API_URL = "https://asl.api.aldi.it/commerce/v3/product-search"
CHAIN_SLUG = "aldi"
SOURCE = "aldi"
STORE_EXTERNAL_ID = "aldi-offerte"
STORE_NAME = "Aldi Offerte"
STORE_CITY = "Verona"
STORE_PROVINCE = "VR"
STORE_LAT = 45.4384
STORE_LNG = 10.9916
RATE = 1.0

# L'API accetta solo questi limit: [12, 16, 24, 30, 32, 48, 60].
PAGE_LIMIT = 60
# Cap di sicurezza sulle pagine (catalogo attuale ~11 pagine da 60).
MAX_PAGES = 40

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "it-IT,it;q=0.9",
    "Origin": "https://www.aldi.it",
    "Referer": "https://www.aldi.it/",
}

_PRICE_RE = re.compile(r"(\d+[,.]\d{2})")
_WS_RE = re.compile(r"\s+")


def _clean_text(value: object) -> str:
    return _WS_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _display_price(value: object) -> Optional[float]:
    """Parsa un prezzo formattato tipo '1,99 €'."""
    m = _PRICE_RE.search(_clean_text(value))
    if not m:
        return None
    try:
        parsed = float(m.group(1).replace(".", "").replace(",", "."))
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _cents(value: object) -> Optional[float]:
    """Prezzo in euro da un importo API in centesimi."""
    try:
        cents = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(cents / 100, 2) if cents > 0 else None


class AldiSpider:
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

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._t_last
        if elapsed < RATE:
            await asyncio.sleep(RATE - elapsed)
        self._t_last = loop.time()

    async def _get_json(self, url: str, params: dict | None = None) -> dict | None:
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.get(
                    url,
                    headers=HEADERS,
                    params=params,
                    timeout=45,
                    follow_redirects=True,
                )
                if r.status_code == 200:
                    return r.json()
                log.warning("HTTP %s Aldi %s", r.status_code, url)
                if r.status_code in (400, 403, 404):
                    return None
            except (httpx.RequestError, ValueError) as exc:
                log.warning("Tentativo %d errore Aldi: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    async def ensure_store(self) -> str | None:
        row = await self.conn.fetchrow(
            """
            SELECT s.id
            FROM stores s
            JOIN chains c ON c.id = s.chain_id
            WHERE c.slug = $1 AND s.external_id = $2
            """,
            CHAIN_SLUG,
            STORE_EXTERNAL_ID,
        )
        if row:
            return str(row["id"])

        if self.dry_run:
            log.info("[DRY] Creerebbe store virtuale %s", STORE_NAME)
            return "00000000-0000-0000-0000-000000000000"

        chain_id = await self.conn.fetchval("SELECT id FROM chains WHERE slug=$1", CHAIN_SLUG)
        if not chain_id:
            log.error("Chain '%s' non trovata", CHAIN_SLUG)
            return None

        return str(await self.conn.fetchval(
            """
            INSERT INTO stores
                (chain_id, name, address, city, province, postal_code,
                 coordinates, external_id, has_delivery, has_click_collect, is_active)
            VALUES
                ($1, $2, 'Offerte online', $3, $4, NULL,
                 ST_SetSRID(ST_MakePoint($5, $6), 4326),
                 $7, FALSE, FALSE, TRUE)
            RETURNING id
            """,
            chain_id,
            STORE_NAME,
            STORE_CITY,
            STORE_PROVINCE,
            STORE_LNG,
            STORE_LAT,
            STORE_EXTERNAL_ID,
        ))

    def _normalize(self, p: dict) -> Optional[dict]:
        """Estrae i campi utili da un prodotto dell'API ASL, o None."""
        sku = str(p.get("sku") or "").strip()
        name = _clean_text(p.get("name"))
        if not sku or not name or p.get("discontinued"):
            return None

        price_obj = p.get("price") or {}
        price = _cents(price_obj.get("amountRelevant") or price_obj.get("amount"))
        if price is None:
            return None

        original = _display_price(price_obj.get("wasPriceDisplay"))
        if original is not None and original <= price:
            original = None
        promo_label = None
        if original is not None:
            promo_label = _clean_text(price_obj.get("savingsDisplay")) or "Offerta Aldi"

        ppu = _cents(price_obj.get("comparison"))

        slug = str(p.get("urlSlugText") or "").strip()
        image_url = None
        assets = p.get("assets") or []
        if assets and isinstance(assets[0], dict):
            template = str(assets[0].get("url") or "")
            if template:
                image_url = (
                    template
                    .replace("{width}", "400")
                    .replace("{slug}", slug or "product")
                )

        # Continuità coi barcode storici "aldi_<id>": il vecchio sito esponeva
        # l'id senza zeri iniziali negli URL scheda, la nuova API lo zero-padda
        # a 18 cifre (stesso numero SAP).
        product_id = sku.lstrip("0") or sku
        return {
            "barcode": f"aldi_{product_id}",
            "name": name,
            "brand": _clean_text(p.get("brandName")) or None,
            "image_url": image_url,
            "price": price,
            "original_price": original,
            "promo_label": promo_label,
            "price_per_unit": ppu,
            "product_url": f"{BASE_URL}/prodotto/{slug}-{sku}" if slug else BASE_URL,
        }

    async def _upsert_products_batch(self, products: list[dict], store_id: str) -> int:
        by_bc: dict[str, dict] = {p["barcode"]: p for p in products if p}
        if not by_bc:
            return 0
        if self.dry_run:
            for p in by_bc.values():
                log.info("[DRY] %-60s EUR %.2f", p["name"][:60], p["price"])
            return len(by_bc)

        barcodes = list(by_bc.keys())
        async with self.conn.transaction():
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
                    [SOURCE] * len(new_bcs),
                )
                for r in rows:
                    id_by_bc[r["barcode"]] = r["id"]

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
                store_id,
                all_ids,
            )
            await self.conn.execute(
                """INSERT INTO prices
                       (product_id, store_id, price, original_price, promo_label,
                        price_per_unit, in_stock, is_current, source,
                        product_url, scraped_at)
                   SELECT v.id, $2, v.price, v.orig, v.promo, v.ppu,
                          TRUE, TRUE, $8, v.url, NOW()
                   FROM unnest($1::uuid[], $3::numeric[], $4::numeric[], $5::text[],
                               $6::numeric[], $7::text[])
                        AS v(id, price, orig, promo, ppu, url)""",
                all_ids,
                store_id,
                [by_bc[b]["price"] for b in barcodes],
                [by_bc[b]["original_price"] for b in barcodes],
                [by_bc[b]["promo_label"] for b in barcodes],
                [by_bc[b]["price_per_unit"] for b in barcodes],
                [by_bc[b]["product_url"] for b in barcodes],
                SOURCE,
            )
            # Eredita i metadati promo dei volantini validi appena spenti
            await preserve_flyer_promos(self.conn, [store_id], all_ids)
        return len(by_bc)

    async def scrape_prices(self) -> int:
        store_id = await self.ensure_store()
        if not store_id:
            return 0

        total = 0
        seen: set[str] = set()
        offset = 0
        for _page in range(MAX_PAGES):
            data = await self._get_json(
                API_URL,
                params={
                    "currency": "EUR",
                    "serviceType": "walk-in",
                    "limit": PAGE_LIMIT,
                    "offset": offset,
                },
            )
            if not data:
                break
            items = data.get("data") or []
            batch = []
            for raw in items:
                n = self._normalize(raw)
                if n and n["barcode"] not in seen:
                    seen.add(n["barcode"])
                    batch.append(n)
            total += await self._upsert_products_batch(batch, store_id)

            pagination = (data.get("meta") or {}).get("pagination") or {}
            total_count = int(pagination.get("totalCount") or 0)
            offset += PAGE_LIMIT
            if not items or offset >= total_count:
                break

        log.info("=== Aldi: %d prezzi upsert ===", total)
        return total

    async def run(self) -> None:
        await self.scrape_prices()
