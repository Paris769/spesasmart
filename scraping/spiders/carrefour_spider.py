"""
Carrefour Italy price scraper — www.carrefour.it (Salesforce Commerce Cloud)

Flusso:
  1. Itera sulle categorie di primo livello di /spesa-online/
     (ogni categoria top-level aggrega tutti i prodotti delle sub-categorie)
  2. Prima pagina: HTML della pagina categoria
     GET /spesa-online/{slug}/
  3. Pagine successive: endpoint AJAX SFCC
     GET /on/demandware.store/Sites-carrefour-IT-Site/it_IT/Search-ShowAjax
         ?cgid={cgid}&start={offset}&sz=25
  4. Parsa prodotti con BeautifulSoup (HTML server-rendered)
  5. Upsert DB con negozio virtuale "Carrefour Online" (sede Milano)

Nota: i prezzi sono visibili senza autenticazione (prezzi online nazionali).
      Il campo data-pid di ogni tile è il codice EAN del prodotto.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

import asyncpg
import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("carrefour")

BASE_URL = "https://www.carrefour.it"
AJAX_URL = (
    f"{BASE_URL}/on/demandware.store/Sites-carrefour-IT-Site/it_IT/Search-ShowAjax"
)
PAGE_SIZE = 100  # l'endpoint AJAX accetta fino a sz=100 → 4× meno richieste
RATE = 1.5  # secondi tra le richieste

_CAR_LAT = 45.4654  # Milano (HQ Carrefour Italia)
_CAR_LNG = 9.1866

# Categorie di primo livello — aggregano tutti i prodotti delle sotto-categorie.
# Ogni slug corrisponde a /spesa-online/{slug}/ e al data-option-cgid della griglia.
CATEGORIES = [
    "frutta-e-verdura",
    "carne",
    "pesce",
    "salumi-e-formaggi",
    "gastronomia",
    "uova-latte-e-latticini",
    "dolci-e-prima-colazione",
    "acqua-e-analcolici",
    "pasta-riso-e-farina",
    "condimenti-e-conserve",
    "pane-e-snack-salati",
    "gelati-e-surgelati",
    "birra-vino-e-liquori",
    "cura-della-casa",
    "cura-della-persona",
    "prodotti-prima-infanzia",
    "salute-e-benessere",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": "https://www.carrefour.it/spesa-online/",
}

# "€ 0,79 al l/1000.0 ml" → prezzo = 0.79, unità = "l"
_UNIT_PRICE_RE = re.compile(r"€\s*([\d,]+)\s+al\s+(\w+)", re.IGNORECASE)


class CarrefourSpider:
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

    async def _get(self, url: str, params: dict | None = None) -> str | None:
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.get(
                    url, params=params, headers=HEADERS, timeout=30
                )
                if r.status_code == 200:
                    return r.text
                log.warning(
                    "HTTP %s %s tentativo %d", r.status_code, url[:70], attempt + 1
                )
                if r.status_code in (403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore: %s", attempt + 1, exc)
            await asyncio.sleep(2**attempt)
        return None

    async def _get_json(self, url: str, params: dict) -> dict | None:
        """Variante di _get che parsa la risposta come JSON (endpoint Search-ShowAjax)."""
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.get(
                    url, params=params, headers=HEADERS, timeout=30
                )
                if r.status_code == 200:
                    return r.json()
                log.warning(
                    "HTTP %s %s tentativo %d", r.status_code, url[:70], attempt + 1
                )
                if r.status_code in (403, 404):
                    return None
            except (httpx.RequestError, Exception) as exc:
                log.warning("Tentativo %d errore: %s", attempt + 1, exc)
            await asyncio.sleep(2**attempt)
        return None

    # ------------------------------------------------------------------
    # Store management
    # ------------------------------------------------------------------

    async def match_stores(self) -> str | None:
        """Trova o crea il negozio virtuale 'Carrefour Online' nel DB."""
        row = await self.conn.fetchrow(
            """
            SELECT s.id FROM stores s
            JOIN chains c ON s.chain_id = c.id
            WHERE c.slug = 'carrefour' AND s.external_id = 'carrefour-online'
            """
        )
        if row:
            log.info("Carrefour Online store trovato: %s", row["id"])
            return str(row["id"])

        if self.dry_run:
            log.info("[DRY] Creerebbe Carrefour Online store")
            return "00000000-0000-0000-0000-000000000001"

        chain_id = await self.conn.fetchval(
            "SELECT id FROM chains WHERE slug = 'carrefour'"
        )
        if not chain_id:
            log.error("Chain 'carrefour' non trovata nel DB — aggiungila in init.sql")
            return None

        new_id = await self.conn.fetchval(
            """
            INSERT INTO stores
                (chain_id, name, address, city, province, postal_code,
                 coordinates, external_id, has_delivery, has_click_collect, is_active)
            VALUES
                ($1, 'Carrefour Online', 'E-commerce', 'Milano', 'MI', '20121',
                 ST_SetSRID(ST_MakePoint($2, $3), 4326),
                 'carrefour-online', TRUE, TRUE, TRUE)
            RETURNING id
            """,
            chain_id,
            _CAR_LNG,
            _CAR_LAT,
        )
        log.info("Creato Carrefour Online store: %s", new_id)
        return str(new_id)

    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _get_grid_info(html: str) -> tuple[str | None, int]:
        """Estrae cgid e total_count dalla griglia prodotti della pagina categoria."""
        soup = BeautifulSoup(html, "html.parser")
        grid = soup.select_one("[data-component='productSearchComponent']")
        if not grid:
            return None, 0
        cgid = grid.get("data-option-cgid", "").strip()
        try:
            total = int(grid.get("data-option-total-count", "0") or "0")
        except ValueError:
            total = 0
        return cgid or None, total

    @staticmethod
    def _parse_unit_price(unit_price_text: str) -> float | None:
        """Estrae il valore numerico dal testo '€ 0,79 al l/1000.0 ml'."""
        m = _UNIT_PRICE_RE.search(unit_price_text)
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None

    @staticmethod
    def _parse_ajax_products(data: dict) -> list[dict]:
        """
        Parsa i prodotti dalla risposta JSON di Search-ShowAjax.
        La struttura è: { "productIds": [ { id, productName, brand, price, unitPrice, ... } ] }
        """
        products = []
        for p in data.get("productIds") or []:
            pid = str(p.get("id") or "").strip()
            name = (p.get("productName") or "").strip()
            if not pid or not name:
                continue

            sales_obj = (p.get("price") or {}).get("sales") or {}
            sales_price = sales_obj.get("value")
            if not sales_price:
                continue

            list_obj = (p.get("price") or {}).get("list") or {}
            orig_price = list_obj.get("value") or None

            unit_sales = (p.get("unitPrice") or {}).get("sales") or {}
            price_per_unit = unit_sales.get("value") or None

            # Etichetta promo dal primo elemento di promotions (se presente)
            promos = p.get("promotions") or []
            promo_label = (promos[0].get("calloutMsg") or promos[0].get("name")) if promos else None

            products.append(
                {
                    "pid": pid,
                    "name": name,
                    "brand": p.get("brand") or None,
                    "price": float(sales_price),
                    "original_price": float(orig_price) if orig_price else None,
                    "price_per_unit": float(price_per_unit) if price_per_unit else None,
                    "image_url": None,  # non presente nel JSON AJAX
                    "promo_label": promo_label,
                }
            )
        return products

    def _parse_products(self, html: str) -> list[dict]:
        """Parsa tutti i product-item dall'HTML di una pagina/fragment."""
        soup = BeautifulSoup(html, "html.parser")
        # Esclude banner pubblicitari (citrus/content-highlighted)
        items = soup.select(".product-item:not(.content-item-highlighted)")
        products = []

        for item in items:
            # EAN barcode dal data-pid
            pid_el = item.select_one("[data-pid]")
            if not pid_el:
                continue
            pid = (pid_el.get("data-pid") or "").strip()
            if not pid:
                continue

            # Nome prodotto (include il brand nel testo)
            name_el = item.select_one(".tile-description")
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                continue

            brand_el = item.select_one(".brand")
            brand = brand_el.get_text(strip=True) if brand_el else None

            # Prezzo di vendita (attributo content="X.XX")
            sales_el = item.select_one(".sales .value")
            sales_price: float | None = None
            if sales_el:
                try:
                    sales_price = float(sales_el.get("content") or "")
                except (ValueError, TypeError):
                    pass
            if sales_price is None:
                continue

            # Prezzo originale (barrato, presente solo se in promozione)
            orig_el = item.select_one(".strike-through .value")
            orig_price: float | None = None
            if orig_el:
                try:
                    orig_price = float(orig_el.get("content") or "")
                except (ValueError, TypeError):
                    pass

            # Prezzo al kg/litro
            unit_el = item.select_one(".unit-price")
            price_per_unit = (
                self._parse_unit_price(unit_el.get_text()) if unit_el else None
            )

            # Immagine
            img_el = item.select_one(".tile-image")
            image_url: str | None = None
            if img_el:
                image_url = img_el.get("src") or img_el.get("data-src") or None

            # Etichetta promozione
            promo_el = item.select_one(".offers-label, .badge-pill")
            promo_label = promo_el.get_text(strip=True) if promo_el else None

            products.append(
                {
                    "pid": pid,
                    "name": name,
                    "brand": brand or None,
                    "price": sales_price,
                    "original_price": orig_price,
                    "price_per_unit": price_per_unit,
                    "image_url": image_url,
                    "promo_label": promo_label,
                }
            )

        return products

    # ------------------------------------------------------------------
    # DB upsert
    # ------------------------------------------------------------------

    async def _upsert_product(self, p: dict, store_uuid: str) -> bool:
        barcode = p["pid"]  # EAN barcode diretto

        if self.dry_run:
            log.info(
                "[DRY] %-55s  €%.2f%s",
                p["name"][:55],
                p["price"],
                f"  ({p['price_per_unit']:.2f}/unit)" if p.get("price_per_unit") else "",
            )
            return True

        prod_id = await self.conn.fetchval(
            "SELECT id FROM products WHERE barcode = $1 LIMIT 1", barcode
        )
        if prod_id is None:
            prod_id = await self.conn.fetchval(
                """
                INSERT INTO products (barcode, name, brand, image_url, source)
                VALUES ($1, $2, $3, $4, 'carrefour_web')
                RETURNING id
                """,
                barcode,
                p["name"],
                p.get("brand"),
                p.get("image_url"),
            )
        else:
            await self.conn.execute(
                """
                UPDATE products
                SET name       = $2,
                    brand      = COALESCE($3, brand),
                    image_url  = COALESCE($4, image_url),
                    updated_at = NOW()
                WHERE id = $1
                """,
                prod_id,
                p["name"],
                p.get("brand"),
                p.get("image_url"),
            )

        await self.conn.execute(
            "UPDATE prices SET is_current = FALSE WHERE product_id = $1 AND store_id = $2",
            prod_id,
            store_uuid,
        )
        await self.conn.execute(
            """
            INSERT INTO prices
                (product_id, store_id, price, original_price, promo_label,
                 price_per_unit, in_stock, is_current, source, scraped_at)
            VALUES ($1, $2, $3, $4, $5, $6, TRUE, TRUE, 'carrefour_web', $7)
            """,
            prod_id,
            store_uuid,
            p["price"],
            p.get("original_price"),
            p.get("promo_label"),
            p.get("price_per_unit"),
            datetime.now(timezone.utc),
        )
        return True

    # ------------------------------------------------------------------
    # Category scraping
    # ------------------------------------------------------------------

    async def scrape_category(self, slug: str, store_uuid: str) -> int:
        url = f"{BASE_URL}/spesa-online/{slug}/"
        log.info("Categoria: %s", slug)

        first_html = await self._get(url)
        if not first_html:
            log.warning("Impossibile ottenere categoria %s", slug)
            return 0

        cgid, total = self._get_grid_info(first_html)
        if not cgid or total == 0:
            log.warning(
                "Nessun prodotto in %s (cgid=%s, total=%d)", slug, cgid, total
            )
            return 0

        total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        log.info("  cgid=%s  totale=%d  pagine=%d", cgid, total, total_pages)

        grand_total = 0
        for page_num in range(total_pages):
            # Tutte le pagine via endpoint AJAX (JSON), incluso start=0.
            # La pagina categoria HTML serve solo per cgid+total (_get_grid_info).
            ajax_data = await self._get_json(
                AJAX_URL,
                params={"cgid": cgid, "start": page_num * PAGE_SIZE, "sz": PAGE_SIZE},
            )
            if not ajax_data:
                log.warning(
                    "Pagina %d non ottenuta per %s, salto", page_num + 1, slug
                )
                continue
            products = self._parse_ajax_products(ajax_data)
            page_count = 0
            for prod in products:
                try:
                    if await self._upsert_product(prod, store_uuid):
                        page_count += 1
                except Exception as exc:
                    log.warning("Errore prodotto %s: %s", prod.get("pid"), exc)

            grand_total += page_count
            if (page_num + 1) % 5 == 0 or (page_num + 1) == total_pages:
                log.info(
                    "  pagina %d/%d — questa: %d  totale: %d",
                    page_num + 1,
                    total_pages,
                    page_count,
                    grand_total,
                )

        return grand_total

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> int:
        log.info("=== Carrefour spider avviato (dry_run=%s) ===", self.dry_run)

        store_uuid = await self.match_stores()
        if not store_uuid:
            log.error("Nessuno store disponibile — interruzione")
            return 0
        log.info("Store UUID: %s", store_uuid)

        grand_total = 0
        for slug in CATEGORIES:
            try:
                n = await self.scrape_category(slug, store_uuid)
                grand_total += n
                log.info("Categoria %s: %d prodotti", slug, n)
            except Exception as exc:
                log.exception("Errore categoria %s: %s", slug, exc)

        log.info("=== Fine. Prezzi totali scritti: %d ===", grand_total)
        return grand_total
