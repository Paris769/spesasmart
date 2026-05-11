"""
Esselunga price scraper — nuova piattaforma spesaonline.esselunga.it/commerce/

Flusso:
  1. GET /commerce/nav/supermercato/store/home → sessione (cookie JSESSIONID)
  2. POST /commerce/resources/search/facet     → discovery categorie dai facet
  3. Per ogni categoria: paginazione completa  → upsert prodotti + prezzi

Nota: Esselunga non espone prezzi per-negozio via API web; tutti i prezzi
vengono associati a un negozio virtuale "Esselunga Online" (external_id=esselunga-online).
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

import asyncpg
import httpx

log = logging.getLogger("esselunga")

COMMERCE_BASE = "https://spesaonline.esselunga.it/commerce"
SESSION_URL = f"{COMMERCE_BASE}/nav/supermercato/store/home"
SEARCH_URL = f"{COMMERCE_BASE}/resources/search/facet"
PAGE_SIZE = 50
RATE = 1.5  # secondi tra una richiesta e l'altra

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": "https://spesaonline.esselunga.it/",
    "X-PAGE-PATH": "supermercato",
}

# Keyword fallback se la discovery automatica delle categorie fallisce
FALLBACK_QUERIES = [
    "latte yogurt uova",
    "pane pasta riso",
    "carne salumi affettati",
    "frutta verdura",
    "pesce frutti mare",
    "formaggi",
    "bevande acqua succhi",
    "snack biscotti cereali",
    "surgelati",
    "detersivi pulizia casa",
    "cura persona shampoo",
    "olio sale condimenti",
    "vino birra alcolici",
    "conserve sughi",
]

# Sede Esselunga (Milano) — usata come coordinate del negozio online virtuale
_ESSELUNGA_LNG = 9.1859
_ESSELUNGA_LAT = 45.4654


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
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._t_last
        if elapsed < RATE:
            await asyncio.sleep(RATE - elapsed)
        self._t_last = loop.time()

    async def _init_session(self) -> None:
        """GET homepage per stabilire il cookie di sessione (JSESSIONID)."""
        await self._throttle()
        try:
            r = await self.client.get(
                SESSION_URL, headers=HEADERS, follow_redirects=True, timeout=30
            )
            has_cookie = bool(self.client.cookies)
            log.info("Session init: HTTP %s (cookie: %s)", r.status_code,
                     "ok" if has_cookie else "assente")
        except httpx.RequestError as exc:
            log.error("Session init fallita: %s", exc)

    async def _post(self, body: dict) -> dict | None:
        """POST a SEARCH_URL con rate-limit e 3 tentativi."""
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.post(
                    SEARCH_URL, json=body, headers=HEADERS, timeout=30
                )
                if r.status_code == 200:
                    return r.json()
                log.warning(
                    "HTTP %s tentativo %d — %s",
                    r.status_code, attempt + 1, r.text[:200],
                )
                if r.status_code in (401, 403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    # ------------------------------------------------------------------
    # Store management
    # ------------------------------------------------------------------

    async def discover_stores(self) -> list[dict]:
        """Ritorna il descrittore del negozio online virtuale Esselunga."""
        return [{"id": "online", "name": "Esselunga Online", "type": "ecommerce"}]

    async def match_stores(self, es_stores: list[dict]) -> dict[str, str]:
        """
        Trova o crea lo store 'Esselunga Online' nel DB.
        Ritorna {"online": uuid}.
        """
        row = await self.conn.fetchrow(
            """
            SELECT s.id
            FROM stores s
            JOIN chains c ON s.chain_id = c.id
            WHERE c.slug = 'esselunga' AND s.external_id = 'esselunga-online'
            """
        )
        if row:
            log.info("Esselunga Online store trovato: %s", row["id"])
            return {"online": str(row["id"])}

        if self.dry_run:
            log.info("[DRY] Creerebbe Esselunga Online store")
            return {"online": "00000000-0000-0000-0000-000000000000"}

        chain_id = await self.conn.fetchval(
            "SELECT id FROM chains WHERE slug = 'esselunga'"
        )
        if not chain_id:
            log.error("Chain 'esselunga' non trovata nel DB — esegui init.sql")
            return {}

        new_id = await self.conn.fetchval(
            """
            INSERT INTO stores
                (chain_id, name, address, city, province, postal_code,
                 coordinates, external_id, has_delivery, has_click_collect, is_active)
            VALUES
                ($1, 'Esselunga Online', 'E-commerce', 'Milano', 'MI', '20100',
                 ST_SetSRID(ST_MakePoint($2, $3), 4326),
                 'esselunga-online', TRUE, TRUE, TRUE)
            RETURNING id
            """,
            chain_id, _ESSELUNGA_LNG, _ESSELUNGA_LAT,
        )
        log.info("Creato Esselunga Online store: %s", new_id)
        return {"online": str(new_id)}

    # ------------------------------------------------------------------
    # Category discovery
    # ------------------------------------------------------------------

    async def _get_categories(self) -> list[str] | None:
        """
        Recupera le categorie top-level dai facet della API.
        Ritorna lista di categoryId, oppure None se non disponibili.
        """
        data = await self._post({"length": 1, "start": 0})
        if not data:
            return None

        displayables = data.get("displayables") or {}
        facets = displayables.get("facets") or []

        for facet in facets:
            if (facet.get("type") or "").upper() in ("CATEGORY", "CATEGORIA"):
                cats = [
                    f.get("value") or f.get("id") or f.get("name")
                    for f in facet.get("filters", [])
                ]
                cats = [c for c in cats if c]
                if cats:
                    log.info("Trovate %d categorie dai facet (type match)", len(cats))
                    return cats

        # Fallback: il facet con più filtri è probabilmente quello delle categorie
        best = max(facets, key=lambda f: len(f.get("filters", [])), default=None)
        if best and len(best.get("filters", [])) > 3:
            cats = [
                f.get("value") or f.get("id")
                for f in best["filters"]
                if f.get("value") or f.get("id")
            ]
            if cats:
                log.info("Trovate %d categorie dai facet (fallback by size)", len(cats))
                return cats

        return None

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_unit_price(label: str | None) -> float | None:
        """
        Parsa il campo label: "1,65 € / l" → 1.65, "0,22 € / 100 g" → 2.20.
        Normalizza sempre a per-kg (peso) o per-litro (volume).
        """
        if not label:
            return None
        m = re.search(
            r"(\d+[,.]?\d*)\s*€\s*/\s*(\d+)?\s*(kg|g|l|cl|ml|pz|unità|conf\.?)",
            label,
            re.IGNORECASE,
        )
        if not m:
            return None
        try:
            val = float(m.group(1).replace(",", "."))
            qty = float(m.group(2)) if m.group(2) else 1.0
            unit = m.group(3).lower()
            if qty == 0:
                return None
            if unit == "g":
                return round(val / qty * 1000, 4)
            if unit == "kg":
                return round(val / qty, 4)
            if unit == "ml":
                return round(val / qty * 1000, 4)
            if unit == "cl":
                return round(val / qty * 100, 4)
            if unit == "l":
                return round(val / qty, 4)
        except (ValueError, ZeroDivisionError):
            pass
        return None

    @staticmethod
    def _extract_promo(product: dict) -> str | None:
        promos = product.get("promotionsDetail") or []
        if not promos:
            return None
        p = promos[0]
        return (
            p.get("label")
            or p.get("description")
            or p.get("title")
            or str(p)[:50]
        )

    # ------------------------------------------------------------------
    # DB upsert
    # ------------------------------------------------------------------

    async def _upsert_product_price(self, p: dict, store_uuid: str) -> bool:
        """
        Upsert prodotto e prezzo.
        Usa barcode reale se presente, altrimenti sintetizza 'esselunga-{code}'.
        Ritorna True se un prezzo è stato (o sarebbe stato) scritto.
        """
        code = str(p.get("code") or p.get("id") or "").strip()
        if not code:
            return False

        real_barcode = str(p.get("barcode") or "").strip() or None
        barcode = real_barcode or f"esselunga-{code}"

        name = (p.get("description") or "").strip()
        if not name:
            return False
        brand = (p.get("brand") or "").strip() or None
        image_url = p.get("imageURL") or p.get("imageUrl") or None

        # discountedPrice = prezzo attuale; price = prezzo di listino
        raw_current = p.get("discountedPrice")
        raw_original = p.get("price")
        if raw_current is None:
            raw_current = raw_original
            raw_original = None

        try:
            current_price = float(raw_current)
            original_price = float(raw_original) if raw_original is not None else None
        except (ValueError, TypeError):
            return False

        if current_price <= 0:
            return False

        # original_price ha senso solo se c'è effettivamente uno sconto
        if original_price is not None and original_price <= current_price:
            original_price = None

        price_per_unit = self._parse_unit_price(p.get("label"))
        promo_label = self._extract_promo(p)
        in_stock = not bool(p.get("outOfStock", False))

        if self.dry_run:
            log.info(
                "[DRY] %-50s  €%.2f%s",
                name[:50],
                current_price,
                f"  (era €{original_price:.2f})" if original_price else "",
            )
            return True

        # Upsert prodotto
        prod_id = await self.conn.fetchval(
            "SELECT id FROM products WHERE barcode = $1 LIMIT 1",
            barcode,
        )
        if prod_id is None:
            prod_id = await self.conn.fetchval(
                """
                INSERT INTO products (barcode, name, brand, image_url, source)
                VALUES ($1, $2, $3, $4, 'esselunga_web')
                RETURNING id
                """,
                barcode, name, brand, image_url,
            )
        else:
            await self.conn.execute(
                """
                UPDATE products
                SET name = $2,
                    brand = COALESCE($3, brand),
                    image_url = COALESCE($4, image_url),
                    updated_at = NOW()
                WHERE id = $1
                """,
                prod_id, name, brand, image_url,
            )

        # Invalida prezzi precedenti e inserisce il nuovo
        await self.conn.execute(
            "UPDATE prices SET is_current = FALSE WHERE product_id = $1 AND store_id = $2",
            prod_id, store_uuid,
        )
        await self.conn.execute(
            """
            INSERT INTO prices
                (product_id, store_id, price, original_price, promo_label,
                 price_per_unit, in_stock, is_current, source, scraped_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, 'esselunga_web', $8)
            """,
            prod_id, store_uuid,
            current_price, original_price, promo_label,
            price_per_unit, in_stock,
            datetime.now(timezone.utc),
        )
        return True

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def _scrape_query(
        self, query_params: dict, label: str, store_uuid: str
    ) -> int:
        """Pagina una singola query (categoria o keyword). Ritorna prezzi scritti."""
        start = 0
        total: int | None = None
        count = 0
        errors = 0

        while total is None or start < total:
            body = {**query_params, "length": PAGE_SIZE, "start": start}
            data = await self._post(body)

            if not data:
                errors += 1
                if errors >= 3:
                    log.error("'%s': 3 errori consecutivi, salto", label)
                    break
                await asyncio.sleep(5)
                continue
            errors = 0

            displayables = data.get("displayables") or {}
            if total is None:
                total = int(displayables.get("rowCount") or 0)
                if total == 0:
                    log.debug("'%s': 0 prodotti, salto", label)
                    break
                log.info("'%s': %d prodotti totali", label, total)

            entities = displayables.get("entities") or []
            if not entities:
                break

            for product in entities:
                try:
                    if await self._upsert_product_price(product, store_uuid):
                        count += 1
                except Exception as exc:
                    log.warning("Errore prodotto %s: %s", product.get("code"), exc)

            start += len(entities)
            if len(entities) < PAGE_SIZE:
                break

        return count

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> int:
        log.info("=== Esselunga spider avviato (dry_run=%s) ===", self.dry_run)

        await self._init_session()

        store_map = await self.match_stores(await self.discover_stores())
        if not store_map:
            log.error("Nessuno store disponibile — interruzione")
            return 0

        store_uuid = store_map["online"]
        log.info("Store UUID: %s", store_uuid)

        categories = await self._get_categories()
        total = 0

        if categories:
            log.info("Scraping per %d categorie", len(categories))
            for i, cat in enumerate(categories, 1):
                log.info("[%d/%d] Categoria: %s", i, len(categories), cat)
                n = await self._scrape_query({"categoryId": cat}, cat, store_uuid)
                log.info("    → %d prezzi scritti", n)
                total += n
        else:
            log.info(
                "Categorie non disponibili — fallback %d keyword queries",
                len(FALLBACK_QUERIES),
            )
            for i, kw in enumerate(FALLBACK_QUERIES, 1):
                log.info("[%d/%d] Keyword: '%s'", i, len(FALLBACK_QUERIES), kw)
                n = await self._scrape_query({"query": kw}, kw, store_uuid)
                log.info("    → %d prezzi scritti", n)
                total += n

        log.info("=== Fine. Prezzi totali scritti: %d ===", total)
        return total
