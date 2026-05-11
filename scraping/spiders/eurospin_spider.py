"""
Eurospin price scraper — www.eurospin.it

Flusso prezzi:
  1. GET /promozioni/ e /ribassati/
     Risposta: HTML statico con card a.sn_promo_grid_item
  2. Estrae prodotti: nome, brand, prezzo promo, prezzo originale, €/unità, immagine
  3. Usa l'ID interno dall'URL immagine come pseudo-barcode (eurospin_XXXXXXX)
  4. Upsert prodotto nel DB
  5. Inserisce un prezzo per ciascun negozio Eurospin attivo nel DB
     (Eurospin ha prezzi nazionali uniformi)

Flusso negozi (--discover-only):
  1. GET /punti-vendita/
     La pagina contiene un <script> inline da ~1.2 MB con
     var stores = [...] — 1262 negozi con lat/lng/indirizzo
  2. Upsert negozi nel DB come chain_slug='eurospin'
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

import asyncpg
import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("eurospin")

BASE_URL = "https://www.eurospin.it"
PROMO_PAGES = [
    f"{BASE_URL}/promozioni/",
    f"{BASE_URL}/ribassati/",
]
STORES_URL = f"{BASE_URL}/punti-vendita/"
CHAIN_SLUG = "eurospin"
SOURCE = "eurospin_web"
RATE = 2.0  # secondi tra le richieste

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

# Regex per estrarre l'ID interno dall'URL immagine
# es. "https://www.eurospin.it/wp-content/uploads/smt/10334001.jpg" → "10334001"
_IMG_ID_RE = re.compile(r"/smt/(\d+)\.(?:jpg|png|webp)", re.I)

# Regex per trovare il prezzo numerico (es. "1,00 €" → "1.00")
_PRICE_RE = re.compile(r"([\d]+[.,][\d]+)")


class EurospinSpider:
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

    async def _get(self, url: str) -> str | None:
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.get(url, headers=HEADERS, timeout=30)
                if r.status_code == 200:
                    return r.text
                log.warning("HTTP %s %s tentativo %d", r.status_code, url, attempt + 1)
                if r.status_code in (403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    # ------------------------------------------------------------------
    # Store discovery
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_stores_json(html: str) -> list[dict]:
        """
        Estrae il JSON stores dall'HTML grezzo.
        La pagina /punti-vendita/ contiene un <script> inline con
        'var stores = [...]' — oltre 1 MB di JSON.
        """
        # Trovare l'inizio dell'array
        m = re.search(r"var\s+stores\s*=\s*(\[)", html)
        if not m:
            log.error("Pattern 'var stores = [' non trovato nell'HTML di /punti-vendita/")
            return []

        start = m.start(1)
        depth = 0
        for i, ch in enumerate(html[start:]):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    raw = html[start : start + i + 1]
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError as exc:
                        log.error("Errore parsing stores JSON: %s", exc)
                        return []
        log.error("Array 'stores' non terminato nell'HTML")
        return []

    @staticmethod
    def _parse_store_entry(s: dict) -> dict | None:
        """
        Converte un elemento di window.stores in un dict normalizzato.
        Formato content: "VIA EXAMPLE<br>Città Provincia, CAP<div>..."
        """
        lat = s.get("lat") or 0.0
        lng = s.get("lng") or 0.0
        if not lat or not lng:
            return None

        slug = ""
        url = s.get("url") or ""
        if "/punti-vendita/" in url:
            slug = url.split("/punti-vendita/")[1].rstrip("/")

        content = s.get("content") or ""
        before_div = content.split("<div")[0]
        parts = before_div.split("<br>")
        address = (parts[0] or "").strip()
        city_cap_raw = (parts[1] or "").strip() if len(parts) > 1 else ""

        # "Cuveglio Varese, 21030" → city="Cuveglio Varese", cap="21030"
        comma_idx = city_cap_raw.rfind(",")
        cap = city_cap_raw[comma_idx + 1 :].strip() if comma_idx >= 0 else ""
        city_prov = city_cap_raw[:comma_idx].strip() if comma_idx >= 0 else city_cap_raw

        # Separa city e province (es. "Cuveglio Varese" → city="Cuveglio", prov="Varese")
        # L'ultimo token è di solito il nome della provincia
        city_parts = city_prov.rsplit(" ", 1)
        city = city_parts[0] if len(city_parts) > 1 else city_prov
        province = city_parts[1] if len(city_parts) > 1 else ""

        return {
            "slug": slug or f"eurospin-{lat:.4f}-{lng:.4f}",
            "name": s.get("name") or city,
            "address": address,
            "city": city,
            "province": province,
            "postal_code": cap,
            "lat": float(lat),
            "lng": float(lng),
        }

    async def discover_stores(self) -> int:
        """Scarica /punti-vendita/, estrae i negozi e li inserisce nel DB."""
        log.info("Scarico negozi da %s", STORES_URL)
        html = await self._get(STORES_URL)
        if not html:
            log.error("Impossibile scaricare /punti-vendita/")
            return 0

        raw_stores = self._extract_stores_json(html)
        log.info("Trovati %d negozi nel JSON", len(raw_stores))

        chain_id = await self.conn.fetchval(
            "SELECT id FROM chains WHERE slug = $1", CHAIN_SLUG
        )
        if not chain_id:
            log.error("Chain '%s' non trovata nel DB", CHAIN_SLUG)
            return 0

        inserted = 0
        for s in raw_stores:
            entry = self._parse_store_entry(s)
            if not entry:
                continue

            if self.dry_run:
                log.info(
                    "[DRY] %-40s  %s %s  lat=%.4f lng=%.4f",
                    entry["name"],
                    entry["city"],
                    entry["province"],
                    entry["lat"],
                    entry["lng"],
                )
                inserted += 1
                continue

            existing = await self.conn.fetchval(
                """
                SELECT id FROM stores
                WHERE chain_id = $1 AND external_id = $2
                """,
                chain_id,
                entry["slug"],
            )
            if existing:
                await self.conn.execute(
                    """
                    UPDATE stores
                    SET name        = $2,
                        address     = $3,
                        city        = $4,
                        province    = $5,
                        postal_code = $6,
                        coordinates = ST_SetSRID(ST_MakePoint($7, $8), 4326),
                        is_active   = TRUE
                    WHERE id = $1
                    """,
                    existing,
                    entry["name"],
                    entry["address"],
                    entry["city"],
                    entry["province"],
                    entry["postal_code"],
                    entry["lng"],
                    entry["lat"],
                )
            else:
                await self.conn.execute(
                    """
                    INSERT INTO stores
                        (chain_id, external_id, name, address, city, province,
                         postal_code, coordinates, is_active)
                    VALUES
                        ($1, $2, $3, $4, $5, $6, $7,
                         ST_SetSRID(ST_MakePoint($8, $9), 4326), TRUE)
                    """,
                    chain_id,
                    entry["slug"],
                    entry["name"],
                    entry["address"],
                    entry["city"],
                    entry["province"],
                    entry["postal_code"],
                    entry["lng"],
                    entry["lat"],
                )
                inserted += 1

        log.info("Negozi upsert completati: %d/%d", inserted, len(raw_stores))
        return inserted

    # ------------------------------------------------------------------
    # Product parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_price(text: str) -> float | None:
        """Estrae il valore float da testo come '1,00 €' o '1.49'."""
        m = _PRICE_RE.search(text)
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None

    @staticmethod
    def _parse_unit_price(text: str) -> float | None:
        """
        Estrae il prezzo/unità dal testo '.i_price_info'.
        Esempio: '150 g - 6,67 €/kg' → 6.67
        """
        if not text:
            return None
        # Cerca il secondo numero (dopo il trattino)
        nums = _PRICE_RE.findall(text)
        if len(nums) >= 2:
            try:
                return float(nums[1].replace(",", "."))
            except ValueError:
                pass
        return None

    def _parse_products(self, html: str) -> list[dict]:
        """Estrae tutti i prodotti dalla pagina promozioni/ribassati."""
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("a.sn_promo_grid_item")
        products = []

        for item in items:
            # Nome
            name_el = item.select_one(".i_title")
            name = name_el.get_text(strip=True) if name_el else None
            if not name:
                continue

            # Brand
            brand_el = item.select_one(".i_brand")
            brand = brand_el.get_text(strip=True) if brand_el else None

            # Immagine e ID interno
            img_el = item.select_one("img")
            image_url = img_el.get("src") if img_el else None
            internal_id = None
            if image_url:
                m = _IMG_ID_RE.search(image_url)
                if m:
                    internal_id = m.group(1)

            if not internal_id:
                continue  # senza ID non possiamo identificare il prodotto

            # Prezzo: il tag .i_price contiene un text node (prezzo orig)
            # e uno <span> (prezzo promo)
            price_el = item.select_one(".i_price")
            promo_price = None
            orig_price = None
            if price_el:
                span = price_el.find("span")
                if span:
                    promo_price = self._parse_price(span.get_text())
                    # Testo grezzo del contenitore (include orig + promo)
                    full_text = price_el.get_text(" ", strip=True)
                    promo_text = span.get_text(strip=True)
                    orig_text = full_text.replace(promo_text, "").strip()
                    orig_price = self._parse_price(orig_text) if orig_text else None
                else:
                    promo_price = self._parse_price(price_el.get_text())

            if not promo_price:
                continue

            # Prezzo per unità
            unit_el = item.select_one(".i_price_info")
            unit_price = self._parse_unit_price(
                unit_el.get_text(strip=True) if unit_el else ""
            )

            # Etichetta promo: se c'è un prezzo originale, il prodotto è in offerta
            promo_label = None
            if orig_price and orig_price > promo_price:
                pct = round((orig_price - promo_price) / orig_price * 100)
                promo_label = f"Offerta -{pct}%"

            products.append(
                {
                    "internal_id": internal_id,
                    "barcode": f"eurospin_{internal_id}",
                    "name": name,
                    "brand": brand,
                    "image_url": image_url,
                    "price": promo_price,
                    "original_price": orig_price,
                    "price_per_unit": unit_price,
                    "promo_label": promo_label,
                }
            )

        return products

    # ------------------------------------------------------------------
    # DB upsert
    # ------------------------------------------------------------------

    async def _get_store_ids(self) -> list[str]:
        """Restituisce gli UUID di tutti i negozi Eurospin attivi nel DB."""
        rows = await self.conn.fetch(
            """
            SELECT s.id
            FROM stores s
            JOIN chains c ON s.chain_id = c.id
            WHERE c.slug = $1 AND s.is_active = TRUE
            """,
            CHAIN_SLUG,
        )
        return [str(r["id"]) for r in rows]

    async def _upsert_product_prices(
        self, p: dict, store_ids: list[str]
    ) -> bool:
        if self.dry_run:
            log.info(
                "[DRY] %-50s  €%.2f%s",
                p["name"][:50],
                p["price"],
                f"  (orig €{p['original_price']:.2f})" if p.get("original_price") else "",
            )
            return True

        # Upsert prodotto
        prod_id = await self.conn.fetchval(
            "SELECT id FROM products WHERE barcode = $1 LIMIT 1", p["barcode"]
        )
        if prod_id is None:
            prod_id = await self.conn.fetchval(
                """
                INSERT INTO products (barcode, name, brand, image_url, source)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                p["barcode"],
                p["name"],
                p.get("brand"),
                p.get("image_url"),
                SOURCE,
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
                prod_id,
                p["name"],
                p.get("brand"),
                p.get("image_url"),
            )

        now = datetime.now(timezone.utc)

        # Batch: disattiva prezzi esistenti e inserisce nuovi per tutti i negozi
        await self.conn.execute(
            """
            UPDATE prices
            SET is_current = FALSE
            WHERE product_id = $1
              AND store_id = ANY($2::uuid[])
              AND is_current = TRUE
            """,
            prod_id,
            store_ids,
        )

        await self.conn.executemany(
            """
            INSERT INTO prices
                (product_id, store_id, price, original_price, promo_label,
                 price_per_unit, in_stock, is_current, source, scraped_at)
            VALUES ($1, $2, $3, $4, $5, $6, TRUE, TRUE, $7, $8)
            """,
            [
                (
                    prod_id,
                    sid,
                    p["price"],
                    p.get("original_price"),
                    p.get("promo_label"),
                    p.get("price_per_unit"),
                    SOURCE,
                    now,
                )
                for sid in store_ids
            ],
        )
        return True

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    async def run(self) -> None:
        store_ids = await self._get_store_ids()
        if not store_ids and not self.dry_run:
            log.warning(
                "Nessun negozio Eurospin nel DB — esegui prima con --discover-only"
            )
            return

        log.info(
            "Negozi Eurospin attivi: %d | dry_run=%s",
            len(store_ids),
            self.dry_run,
        )

        total_products = 0
        for url in PROMO_PAGES:
            log.info("Scarico %s", url)
            html = await self._get(url)
            if not html:
                log.warning("Pagina non disponibile: %s", url)
                continue

            products = self._parse_products(html)
            log.info("  Prodotti trovati: %d", len(products))

            for p in products:
                ok = await self._upsert_product_prices(
                    p, store_ids if store_ids else ["00000000-0000-0000-0000-000000000000"]
                )
                if ok:
                    total_products += 1

        log.info(
            "Eurospin completato — %d prodotti × %d negozi = %d prezzi",
            total_products,
            len(store_ids),
            total_products * len(store_ids),
        )
