"""
Esselunga price scraper — chiama la stessa JSON API usata da spesaonline.esselunga.it.
Non richiede API key. Rate-limit 1.2 s/req. Retry fino a 3 tentativi.

Flusso:
  1. discover_stores()   → scarica lista negozi Esselunga con ID interno
  2. match_stores()      → abbina per cap/città ai negozi nel nostro DB, aggiorna external_id
  3. scrape_store()      → per ogni prodotto con barcode cerca il prezzo → upsert in prices
"""
import asyncio
import logging
import os
from datetime import datetime

import asyncpg
import httpx

log = logging.getLogger("esselunga")

BASE = "https://spesaonline.esselunga.it/SpesaOnline/rest"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Referer": "https://spesaonline.esselunga.it/",
    "X-Requested-With": "XMLHttpRequest",
}
RATE = 1.2  # secondi tra una richiesta e l'altra


class EsselungaSpider:
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

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    async def _get(self, path: str, **params) -> dict | list | None:
        url = f"{BASE}{path}"
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._t_last
        if elapsed < RATE:
            await asyncio.sleep(RATE - elapsed)
        self._t_last = loop.time()

        for attempt in range(3):
            try:
                r = await self.client.get(
                    url, params=params or None, headers=HEADERS, timeout=30
                )
                if r.status_code == 200:
                    return r.json()
                log.warning("HTTP %s — %s", r.status_code, url)
                if r.status_code in (403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore: %s", attempt + 1, exc)
                await asyncio.sleep(2**attempt)
        return None

    # ------------------------------------------------------------------
    # Store discovery
    # ------------------------------------------------------------------

    async def discover_stores(self) -> list[dict]:
        """GET /storeSelectionRS/getStoreList — ritorna tutti i punti vendita."""
        data = await self._get("/storeSelectionRS/getStoreList")
        if data is None:
            log.error(
                "Store discovery fallita. Verifica che BASE=%s sia ancora valido.", BASE
            )
            return []
        stores: list[dict] = data if isinstance(data, list) else data.get("stores", [])
        log.info("Esselunga: %d negozi trovati dall'API", len(stores))
        return stores

    async def match_stores(self, es_stores: list[dict]) -> dict[str, str]:
        """
        Ritorna {es_store_id: nostro_uuid}.
        Abbina per codice postale; fallback per città.
        Aggiorna external_id nel DB con l'ID reale Esselunga.
        """
        our = await self.conn.fetch(
            """
            SELECT id, city, postal_code, address, external_id
            FROM stores
            WHERE chain_id = (SELECT id FROM chains WHERE slug = 'esselunga')
            """
        )
        mapping: dict[str, str] = {}

        for row in our:
            cap = (row["postal_code"] or "").strip()
            city = (row["city"] or "").lower()

            # Cerca prima per CAP, poi per città
            match = next(
                (
                    s
                    for s in es_stores
                    if str(s.get("zipCode") or s.get("cap") or "") == cap
                ),
                None,
            ) or next(
                (
                    s
                    for s in es_stores
                    if (s.get("city") or s.get("citta") or "").lower() == city
                ),
                None,
            )

            if not match:
                log.debug("Nessun match per negozio %s (%s)", row["address"], cap)
                continue

            es_id = str(match.get("id") or match.get("storeId") or match.get("idNegozio") or "")
            if not es_id:
                continue

            our_uuid = str(row["id"])
            mapping[es_id] = our_uuid
            log.info(
                "Match: %s %s → ES id=%s", row["city"], row["address"], es_id
            )

            if not self.dry_run:
                await self.conn.execute(
                    "UPDATE stores SET external_id = $1 WHERE id = $2",
                    f"es-{es_id}",
                    row["id"],
                )

        return mapping

    # ------------------------------------------------------------------
    # Price fetch
    # ------------------------------------------------------------------

    async def _price_by_barcode(self, barcode: str, es_id: str) -> dict | None:
        return await self._get(
            "/catalogRS/getProductByEan", ean=barcode, storeId=es_id
        )

    async def _price_by_name(self, name: str, es_id: str) -> list[dict]:
        data = await self._get(
            "/catalogRS/getProducts",
            q=name[:30],
            num=5,
            start=0,
            storeId=es_id,
        )
        if not data:
            return []
        return data.get("products") or (data if isinstance(data, list) else [])

    def _extract_price(self, raw: dict, prod: asyncpg.Record) -> dict | None:
        """Estrae e normalizza i campi prezzo dalla risposta API."""
        price_val = (
            raw.get("price")
            or raw.get("currentPrice")
            or raw.get("prezzoAttuale")
            or raw.get("prezzoScontato")
        )
        if not price_val:
            return None
        try:
            price = float(str(price_val).replace(",", ".").replace("€", "").strip())
        except (ValueError, TypeError):
            return None
        if price <= 0:
            return None

        orig_val = raw.get("originalPrice") or raw.get("prezzoOriginale")
        original = None
        if orig_val:
            try:
                original = float(str(orig_val).replace(",", ".").replace("€", "").strip())
            except (ValueError, TypeError):
                pass

        promo = raw.get("promoLabel") or raw.get("offerta") or raw.get("promo")

        # price_per_unit: preso dall'API oppure calcolato
        ppu = raw.get("unitPrice") or raw.get("pricePerUnit") or raw.get("prezzoPorzione")
        if not ppu and prod["unit_quantity"]:
            qty = float(prod["unit_quantity"])
            unit = (prod["unit"] or "pz").lower()
            if unit == "g":
                qty = qty / 1000  # → kg
            elif unit == "ml":
                qty = qty / 1000  # → l
            ppu = round(price / qty, 4) if qty else None

        return {
            "price": price,
            "original_price": original if original and original > price else None,
            "promo_label": promo,
            "price_per_unit": float(ppu) if ppu else None,
        }

    # ------------------------------------------------------------------
    # Per-store scraping
    # ------------------------------------------------------------------

    async def scrape_store(self, es_id: str, our_uuid: str) -> int:
        products = await self.conn.fetch(
            "SELECT id, barcode, name, unit, unit_quantity FROM products WHERE barcode IS NOT NULL"
        )
        updated = 0

        for prod in products:
            barcode = prod["barcode"]
            raw = await self._price_by_barcode(barcode, es_id)

            # Fallback: ricerca per nome
            if not raw:
                results = await self._price_by_name(prod["name"], es_id)
                raw = next(
                    (
                        r
                        for r in results
                        if str(r.get("ean") or r.get("barcode") or "") == barcode
                    ),
                    None,
                )

            if not raw:
                continue

            price_data = self._extract_price(raw, prod)
            if not price_data:
                continue

            if self.dry_run:
                log.info(
                    "[DRY] %-40s  ES=%s  €%.2f",
                    prod["name"][:40],
                    es_id,
                    price_data["price"],
                )
                updated += 1
                continue

            # Segna vecchi prezzi come non correnti
            await self.conn.execute(
                "UPDATE prices SET is_current = FALSE WHERE product_id = $1 AND store_id = $2",
                prod["id"],
                our_uuid,
            )
            await self.conn.execute(
                """
                INSERT INTO prices
                    (product_id, store_id, price, original_price, promo_label,
                     price_per_unit, in_stock, is_current, source, scraped_at)
                VALUES ($1, $2, $3, $4, $5, $6, TRUE, TRUE, 'esselunga_direct', $7)
                """,
                prod["id"],
                our_uuid,
                price_data["price"],
                price_data["original_price"],
                price_data["promo_label"],
                price_data["price_per_unit"],
                datetime.utcnow(),
            )
            updated += 1

        return updated

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> int:
        log.info("=== Esselunga spider avviato (dry_run=%s) ===", self.dry_run)

        es_stores = await self.discover_stores()
        store_map: dict[str, str] = {}

        if es_stores:
            store_map = await self.match_stores(es_stores)
        else:
            log.warning("Store discovery vuota — recupero negozi dal DB con external_id esistente")

        # Fallback: usa external_id già nel DB (es. 'es-046' → '046')
        if not store_map:
            rows = await self.conn.fetch(
                """
                SELECT id, external_id FROM stores
                WHERE chain_id = (SELECT id FROM chains WHERE slug = 'esselunga')
                  AND external_id ~ '^es-[0-9]+'
                """
            )
            for row in rows:
                es_id = str(row["external_id"]).removeprefix("es-")
                store_map[es_id] = str(row["id"])
            if store_map:
                log.info("Fallback: uso %d negozi dal DB", len(store_map))
            else:
                log.error(
                    "Nessun negozio disponibile. Esegui prima con --discover-only "
                    "oppure verifica BASE URL."
                )
                return 0

        total = 0
        for es_id, our_uuid in store_map.items():
            log.info("--- Negozio ES=%s → DB=%s ---", es_id, our_uuid)
            n = await self.scrape_store(es_id, our_uuid)
            log.info("    %d prezzi aggiornati", n)
            total += n

        log.info("=== Fine. Prezzi totali aggiornati: %d ===", total)
        return total
