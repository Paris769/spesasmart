"""
Conad price scraper — spesaonline.conad.it

Flusso:
  1. GET /search/_jcr_content/root/search.loader.html?q=*&page=N
     Risposta: HTML con prodotti embeddati come data-product (JSON con entity encoding)
  2. Estrae prodotti da data-product attributes
  3. Salva solo prodotti con basePrice > 0 (programma "Bassi e Fissi" — prezzi
     garantiti uguali in tutti i punti vendita Conad)
  4. Upsert DB con negozio virtuale "Conad Online" (coords: sede Bologna)

Nota: prezzi store-specifici richiedono autenticazione; i "bassiFissi" sono
prezzi nazionali fissi applicabili come proxy per la comparazione.
"""
import asyncio
import html
import json
import logging
import re
from datetime import datetime, timezone

import asyncpg
import httpx

log = logging.getLogger("conad")

BASE_URL = "https://spesaonline.conad.it"
SEARCH_URL = f"{BASE_URL}/search/_jcr_content/root/search.loader.html"
PAGE_SIZE = 40
RATE = 1.5  # secondi tra le richieste

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": "https://spesaonline.conad.it/search",
}

_CONAD_LNG = 11.3426  # Bologna (sede Conad)
_CONAD_LAT = 44.4939

# Regex per estrarre tutti i data-product dall'HTML
_PRODUCT_RE = re.compile(r'data-product="([^"]+)"')
_TOTAL_RE = re.compile(r"(\d+)\s+risultati")


class ConadSpider:
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
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._t_last
        if elapsed < RATE:
            await asyncio.sleep(RATE - elapsed)
        self._t_last = loop.time()

    async def _get_page(self, page: int) -> str | None:
        await self._throttle()
        params = {"q": "*", "page": page}
        for attempt in range(3):
            try:
                r = await self.client.get(
                    SEARCH_URL, params=params, headers=HEADERS, timeout=30
                )
                if r.status_code == 200:
                    return r.text
                log.warning("HTTP %s pagina %d tentativo %d", r.status_code, page, attempt + 1)
                if r.status_code in (403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    # ------------------------------------------------------------------
    # Store management
    # ------------------------------------------------------------------

    async def match_stores(self) -> str | None:
        """Trova o crea il negozio virtuale 'Conad Online' nel DB."""
        row = await self.conn.fetchrow(
            """
            SELECT s.id
            FROM stores s
            JOIN chains c ON s.chain_id = c.id
            WHERE c.slug = 'conad' AND s.external_id = 'conad-online'
            """
        )
        if row:
            log.info("Conad Online store trovato: %s", row["id"])
            return str(row["id"])

        if self.dry_run:
            log.info("[DRY] Creerebbe Conad Online store")
            return "00000000-0000-0000-0000-000000000000"

        chain_id = await self.conn.fetchval(
            "SELECT id FROM chains WHERE slug = 'conad'"
        )
        if not chain_id:
            log.error("Chain 'conad' non trovata nel DB — aggiungila in init.sql")
            return None

        new_id = await self.conn.fetchval(
            """
            INSERT INTO stores
                (chain_id, name, address, city, province, postal_code,
                 coordinates, external_id, has_delivery, has_click_collect, is_active)
            VALUES
                ($1, 'Conad Online', 'E-commerce', 'Bologna', 'BO', '40127',
                 ST_SetSRID(ST_MakePoint($2, $3), 4326),
                 'conad-online', TRUE, TRUE, TRUE)
            RETURNING id
            """,
            chain_id, _CONAD_LNG, _CONAD_LAT,
        )
        log.info("Creato Conad Online store: %s", new_id)
        return str(new_id)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_products(page_html: str) -> list[dict]:
        """Estrae e parsa i data-product dall'HTML della pagina."""
        products = []
        for m in _PRODUCT_RE.finditer(page_html):
            raw = html.unescape(m.group(1))
            try:
                obj = json.loads(raw)
                products.append(obj)
            except json.JSONDecodeError:
                pass
        return products

    @staticmethod
    def _get_total(page_html: str) -> int | None:
        m = _TOTAL_RE.search(page_html)
        return int(m.group(1)) if m else None

    @staticmethod
    def _parse_unit_price(product: dict) -> float | None:
        """
        Calcola il prezzo al kg/litro dai campi netQuantity e netQuantityUm.
        Normalizza a per-kg (solidi) o per-litro (liquidi).
        """
        base = product.get("basePrice") or 0.0
        qty = product.get("netQuantity") or 0.0
        um = (product.get("netQuantityUm") or "").upper()
        if not base or not qty:
            return None
        try:
            qty = float(qty)
            base = float(base)
            if qty <= 0:
                return None
            if um == "KG":
                return round(base / qty, 4)
            if um == "G":
                return round(base / qty * 1000, 4)
            if um == "LT":
                return round(base / qty, 4)
            if um == "ML":
                return round(base / qty * 1000, 4)
            if um == "CL":
                return round(base / qty * 100, 4)
        except (ValueError, ZeroDivisionError):
            pass
        return None

    # ------------------------------------------------------------------
    # DB upsert
    # ------------------------------------------------------------------

    async def _upsert_product_price(self, p: dict, store_uuid: str) -> bool:
        code = str(p.get("code") or "").strip()
        if not code:
            return False

        base_price = float(p.get("basePrice") or 0.0)
        if base_price <= 0:
            return False  # salta prodotti senza prezzo

        name = (p.get("nome") or "").strip()
        if not name:
            return False

        brand = (p.get("marchio") or "").strip() or None
        barcode = f"conad-{code}"

        # Costruisci URL immagine (le list page hanno già l'URL completo)
        img = p.get("defaultImgSrc") or ""
        if img and img.startswith("/"):
            img = BASE_URL + img
        image_url = img or None

        price_per_unit = self._parse_unit_price(p)
        promo_label = "Bassi e Fissi" if p.get("bassiFissi") else None

        if self.dry_run:
            log.info(
                "[DRY] %-55s  €%.2f%s",
                name[:55],
                base_price,
                f"  ({price_per_unit:.2f}/kg)" if price_per_unit else "",
            )
            return True

        prod_id = await self.conn.fetchval(
            "SELECT id FROM products WHERE barcode = $1 LIMIT 1", barcode
        )
        if prod_id is None:
            prod_id = await self.conn.fetchval(
                """
                INSERT INTO products (barcode, name, brand, image_url, source)
                VALUES ($1, $2, $3, $4, 'conad_web')
                RETURNING id
                """,
                barcode, name, brand, image_url,
            )
        else:
            await self.conn.execute(
                """
                UPDATE products
                SET name      = $2,
                    brand     = COALESCE($3, brand),
                    image_url = COALESCE($4, image_url),
                    updated_at = NOW()
                WHERE id = $1
                """,
                prod_id, name, brand, image_url,
            )

        await self.conn.execute(
            "UPDATE prices SET is_current = FALSE WHERE product_id = $1 AND store_id = $2",
            prod_id, store_uuid,
        )
        await self.conn.execute(
            """
            INSERT INTO prices
                (product_id, store_id, price, original_price, promo_label,
                 price_per_unit, in_stock, is_current, source, scraped_at)
            VALUES ($1, $2, $3, NULL, $4, $5, TRUE, TRUE, 'conad_web', $6)
            """,
            prod_id, store_uuid,
            base_price, promo_label,
            price_per_unit,
            datetime.now(timezone.utc),
        )
        return True

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> int:
        log.info("=== Conad spider avviato (dry_run=%s) ===", self.dry_run)

        store_uuid = await self.match_stores()
        if not store_uuid:
            log.error("Nessuno store disponibile — interruzione")
            return 0
        log.info("Store UUID: %s", store_uuid)

        # Pagina 1 per ottenere il totale
        first_page = await self._get_page(1)
        if not first_page:
            log.error("Impossibile ottenere la prima pagina")
            return 0

        total = self._get_total(first_page) or 0
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        log.info("Totale prodotti: %d — pagine: %d", total, total_pages)

        grand_total = 0
        all_pages = [first_page] + [None] * (total_pages - 1)

        for page_num in range(1, total_pages + 1):
            if page_num == 1:
                page_html = first_page
            else:
                page_html = await self._get_page(page_num)
                if not page_html:
                    log.warning("Pagina %d non ottenuta, salto", page_num)
                    continue

            products = self._parse_products(page_html)
            priced = 0
            for product in products:
                try:
                    if await self._upsert_product_price(product, store_uuid):
                        priced += 1
                except Exception as exc:
                    log.warning("Errore prodotto %s: %s", product.get("code"), exc)

            grand_total += priced
            if page_num % 10 == 0 or page_num == total_pages:
                log.info(
                    "Pagina %d/%d — prodotti con prezzo questa pagina: %d — totale: %d",
                    page_num, total_pages, priced, grand_total,
                )

        log.info("=== Fine. Prezzi totali scritti: %d ===", grand_total)
        return grand_total
