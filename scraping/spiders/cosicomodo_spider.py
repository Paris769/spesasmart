"""
CosiComodoSpider — prezzi Famila / gruppo Selex via OCC API.

╔══════════════════════════════════════════════════════════════════════════╗
║  STATO: NON FUNZIONANTE — manca la mappatura negozio → baseSiteId.        ║
║  Diagnosi completa 2026-05-12 (vedi sotto). Lo spider fa fail-fast        ║
║  invece di sprecare 90 min di CI. NON è incluso nel run notturno.         ║
╚══════════════════════════════════════════════════════════════════════════╝

DIAGNOSI (verificata chiamando l'API live):

  1. Base URL CORRETTO:  https://api.cosicomodo.it/occ/v2
  2. Path endpoint CORRETTO:
       /{baseSiteId}/stores/{storeAliasId}/users/{userId}/products/search-by-category
  3. baseSiteId — i 14 valori precedenti (famila, familanord, familasud,
     dok, sole365, …) erano presi dai PATH della sitemap del sito web, NON
     sono base site dell'API OCC. L'API risponde:
        "Base site <X> doesn't exist"  (HTTP 400)
     Gli UNICI base site OCC validi trovati per probe sono:
        - "cosicomodo"
        - "selex"
  4. storeAliasId — derivare città→kebab-case ("Novate Milanese" →
     "novate-milanese") NON basta. "novate-milanese" È un aliasUid valido
     ma sotto base site "cosicomodo"/"selex" risponde:
        "SelexBaseStoreNotBelongingToBaseStoreError"
     → ogni negozio appartiene a un base site SPECIFICO che non è né
       "cosicomodo" né "selex" e non è stato identificato via probe.

COSA SERVE PER COMPLETARE IL FIX:
  Catturare UNA chiamata reale di search-by-category dal sito live
  (cosicomodo.it/famila → selezione negozio → reparto). Da lì si leggono
  il baseSiteId reale e lo storeAliasId reale. Probabilmente il sito
  ottiene la coppia (baseSiteId, storeAliasId) da una GraphQL `getStores`
  su NEXT_PUBLIC_STORE_SERVICE_ENDPONT (host non identificato staticamente).

Finché la mappatura non è nota, `scrape_prices()` aborta dopo poche probe
fallite. La discovery negozi (FamilaSpider via sitemap) resta funzionante
ed è indipendente da questo spider.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import Optional

import asyncpg
import httpx

log = logging.getLogger("cosicomodo")

API_BASE = "https://api.cosicomodo.it/occ/v2"
CHAIN_SLUG = "famila"
PAGE_SIZE = 100
RATE = 0.4          # secondi tra richieste (l'API pubblica non throttla aggressivamente)
PROBE_TIMEOUT = 8   # secondi per il probe del baseSiteId
CAT_CONCURRENCY = 3 # categorie in parallelo per negozio

# Base site OCC verificati come ESISTENTI (probe live 2026-05-12).
# NB: nessuno dei due contiene i singoli negozi — vedi diagnosi nel docstring.
# I valori precedenti (famila, familanord, …) erano path web, non base site.
BASE_SITE_IDS = [
    "cosicomodo",
    "selex",
]

# Dopo quante probe consecutive fallite abortire l'intero run.
# Evita di sprecare i 90 minuti di timeout della GitHub Action.
MAX_PROBE_FAILURES = 5

# Codici categoria top-level (da __NEXT_DATA__.departments)
CATEGORY_CODES = [str(c) for c in range(10001, 10017)]  # 10001–10016

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _city_to_alias(city: str) -> str:
    """
    Normalizza il nome della città per ottenere lo storeAliasId di CosìComodo.
    Es.: "Novate Milanese" → "novate-milanese"
         "Sant'Angelo Lodigiano" → "sant-angelo-lodigiano"
         "Sàn Benedétto" → "san-benedetto"
    """
    # rimuove accenti
    nfkd = unicodedata.normalize("NFKD", city)
    ascii_city = nfkd.encode("ascii", "ignore").decode("ascii")
    # minuscolo, apostrofi e spazi → trattino, comprime trattini multipli
    slug = ascii_city.lower()
    slug = re.sub(r"['’‘]", "-", slug)   # apostrofi
    slug = re.sub(r"[^a-z0-9]+", "-", slug)         # tutto il resto → trattino
    slug = slug.strip("-")
    return slug


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
        # cache store_id → (baseSiteId, storeAlias) per evitare ri-probe
        self._store_cache: dict[str, tuple[str, str]] = {}

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
            except (httpx.RequestError, Exception) as exc:
                log.warning("Tentativo %d errore: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    # ── Store discovery ───────────────────────────────────────────────────────

    async def _probe_store(
        self, city: str
    ) -> Optional[tuple[str, str]]:
        """
        Proba tutti i baseSiteId per trovare quello che serve questo negozio.
        Ritorna (baseSiteId, storeAlias) oppure None se non trovato.
        """
        alias = _city_to_alias(city)
        for bsid in BASE_SITE_IDS:
            url = (
                f"{API_BASE}/{bsid}/stores/{alias}"
                "/users/anonymous/products/search-by-category"
            )
            data = await self._get_json(
                url,
                params={
                    "facet": ":relevance",
                    "currentPage": 0,
                    "pageSize": 1,
                    "fields": "BASIC",
                    "categoryCode": "10009",  # latte-burro-uova-yogurt
                },
                timeout=PROBE_TIMEOUT,
            )
            if data and data.get("pagination", {}).get("totalResults", 0) > 0:
                log.info("Store '%s' → baseSiteId=%s alias=%s", city, bsid, alias)
                return bsid, alias
        log.warning("Store '%s' (alias=%s) non trovato su CosìComodo", city, alias)
        return None

    # ── Products scraping ─────────────────────────────────────────────────────

    async def _scrape_category(
        self, bsid: str, alias: str, category_code: str, store_uuid: str
    ) -> int:
        """Scarica tutti i prodotti di una categoria per un negozio. Ritorna il numero upserted."""
        upserted = 0
        page = 0
        total_pages = 1  # aggiornato alla prima risposta

        while page < total_pages:
            data = await self._get_json(
                f"{API_BASE}/{bsid}/stores/{alias}"
                "/users/anonymous/products/search-by-category",
                params={
                    "facet": ":relevance",
                    "currentPage": page,
                    "pageSize": PAGE_SIZE,
                    "fields": "FULL",
                    "categoryCode": category_code,
                },
            )
            if not data:
                log.warning(
                    "Nessun dato per %s/%s cat=%s pag=%d",
                    bsid, alias, category_code, page,
                )
                break

            pagination = data.get("pagination", {})
            total_pages = pagination.get("totalPages", 1)
            products = data.get("products", [])

            for p in products:
                ok = await self._upsert_product_price(p, store_uuid)
                if ok:
                    upserted += 1

            page += 1

        return upserted

    # ── DB upsert ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_prices(p: dict) -> tuple[float, Optional[float], Optional[str]]:
        """
        Ritorna (current_price, original_price, promo_label).

        L'API usa:
          p["price"]           → prezzo di listino (o corrente se non in promo)
          p["discountedPrice"] → prezzo scontato (presente solo se in promo)
          p["stickers"]        → lista etichette promo (es. "PROMO -33%")
        """
        list_price_raw = (p.get("price") or {}).get("value")
        disc_price_raw = (p.get("discountedPrice") or {}).get("value")
        flag_promo = bool(p.get("flagPromo"))

        try:
            list_price = float(list_price_raw) if list_price_raw is not None else None
        except (ValueError, TypeError):
            list_price = None

        try:
            disc_price = float(disc_price_raw) if disc_price_raw is not None else None
        except (ValueError, TypeError):
            disc_price = None

        if flag_promo and disc_price is not None and disc_price > 0:
            current = disc_price
            original = list_price if (list_price and list_price > disc_price) else None
        elif list_price is not None and list_price > 0:
            current = list_price
            original = None
        else:
            return 0.0, None, None

        # etichetta promo dal primo sticker (es. "PROMO -33%")
        stickers = p.get("stickers") or []
        promo_label: Optional[str] = None
        if stickers:
            promo_label = stickers[0].get("label")
        if not promo_label and flag_promo:
            discounts = p.get("jsonDiscounts") or []
            if discounts:
                promo_label = discounts[0].get("discountLabel") or "Promo"

        return current, original, promo_label

    async def _upsert_product_price(self, p: dict, store_uuid: str) -> bool:
        """Upsert prodotto e prezzo. Ritorna True se scritto (o sarebbe stato scritto)."""
        # EAN dal campo 'code' (è il barcode a 13 cifre)
        barcode = str(p.get("code") or "").strip()
        if not barcode:
            return False

        name = str(p.get("name") or "").strip()
        if not name:
            return False

        brand = str(p.get("marca") or "").strip() or None
        in_stock = bool(p.get("saleable", True))

        current_price, original_price, promo_label = self._extract_prices(p)
        if current_price <= 0:
            return False

        # Prezzo unitario (€/kg o €/l) dal campo priceReferenceUnit
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

        if self.dry_run:
            log.info(
                "[DRY] %-55s  €%.2f%s  %s",
                name[:55],
                current_price,
                f" (era €{original_price:.2f})" if original_price else "",
                promo_label or "",
            )
            return True

        # ── upsert product ────────────────────────────────────────────────────
        prod_id = await self.conn.fetchval(
            "SELECT id FROM products WHERE barcode = $1 LIMIT 1",
            barcode,
        )
        if prod_id is None:
            prod_id = await self.conn.fetchval(
                """
                INSERT INTO products (barcode, name, brand, source)
                VALUES ($1, $2, $3, 'cosicomodo')
                RETURNING id
                """,
                barcode, name, brand,
            )
        else:
            await self.conn.execute(
                """
                UPDATE products
                   SET name  = $2,
                       brand = COALESCE($3, brand),
                       updated_at = NOW()
                 WHERE id = $1
                """,
                prod_id, name, brand,
            )

        # ── upsert price ──────────────────────────────────────────────────────
        await self.conn.execute(
            "UPDATE prices SET is_current = FALSE WHERE product_id = $1 AND store_id = $2",
            prod_id, store_uuid,
        )
        await self.conn.execute(
            """
            INSERT INTO prices
                (product_id, store_id, price, original_price, promo_label,
                 price_per_unit, in_stock, is_current, source, scraped_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, 'cosicomodo', NOW())
            """,
            prod_id, store_uuid,
            current_price, original_price, promo_label,
            price_per_unit, in_stock,
        )
        return True

    # ── Entry point ───────────────────────────────────────────────────────────

    async def scrape_prices(self) -> int:
        """
        Scrapa i prezzi per tutti i negozi Famila in DB.
        Ritorna il totale dei prezzi upserted.
        """
        chain_id = await self.conn.fetchval(
            "SELECT id FROM chains WHERE slug = $1", CHAIN_SLUG
        )
        if not chain_id:
            log.error("Chain '%s' non trovata nel DB", CHAIN_SLUG)
            return 0

        stores = await self.conn.fetch(
            """
            SELECT id::text, city, name
              FROM stores
             WHERE chain_id = $1
               AND is_active = TRUE
               AND city IS NOT NULL
             ORDER BY city
            """,
            chain_id,
        )
        log.info("Negozi Famila in DB: %d", len(stores))

        total_upserted = 0
        probe_failures = 0
        for store in stores:
            store_uuid = store["id"]
            city = store["city"]
            store_name = store["name"]

            # Trova baseSiteId e storeAlias
            if store_uuid in self._store_cache:
                bsid, alias = self._store_cache[store_uuid]
            else:
                result = await self._probe_store(city)
                if result is None:
                    probe_failures += 1
                    # Fail-fast: se le prime probe falliscono tutte, lo spider
                    # è strutturalmente rotto (manca la mappatura negozio →
                    # baseSiteId, vedi docstring). Inutile insistere per 90 min.
                    if probe_failures >= MAX_PROBE_FAILURES:
                        log.error(
                            "ABORT: %d probe consecutive fallite. Lo spider "
                            "CosìComodo non ha la mappatura negozio→baseSiteId. "
                            "Vedi diagnosi nel docstring del modulo. "
                            "Scrape interrotto per non sprecare CI.",
                            probe_failures,
                        )
                        return total_upserted
                    continue
                probe_failures = 0
                bsid, alias = result
                self._store_cache[store_uuid] = (bsid, alias)

            log.info("=== %s (%s/%s) ===", store_name, bsid, alias)
            store_total = 0
            # Scrapa le categorie in batch concorrenti (CAT_CONCURRENCY alla volta)
            sem = asyncio.Semaphore(CAT_CONCURRENCY)

            async def _scrape_cat_guarded(code: str) -> int:
                async with sem:
                    return await self._scrape_category(bsid, alias, code, store_uuid)

            results = await asyncio.gather(
                *[_scrape_cat_guarded(code) for code in CATEGORY_CODES],
                return_exceptions=True,
            )
            for code, res in zip(CATEGORY_CODES, results):
                if isinstance(res, Exception):
                    log.warning("  cat %s errore: %s", code, res)
                elif res > 0:
                    log.info("  cat %s → %d prodotti", code, res)
                    store_total += res
                elif res == 0:
                    store_total += 0

            log.info("  Totale negozio: %d prezzi", store_total)
            total_upserted += store_total

        log.info("=== CosìComodo: %d prezzi totali upserted ===", total_upserted)
        return total_upserted

    async def run(self) -> None:
        await self.scrape_prices()
