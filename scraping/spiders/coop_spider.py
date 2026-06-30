"""
Coop/EasyCoop price scraper via Adobe Live Search.

EasyCoop exposes product search through Adobe Commerce Live Search. The scraper
uses a virtual online store for the Emilia website and samples a curated list of
common grocery terms, enough to keep scheduled runs bounded while adding useful
priced products to SpesaSmart.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from typing import Optional

import asyncpg
import httpx

from ..aliases import resolve_existing

log = logging.getLogger("coop")

CHAIN_SLUG = "coop"
SOURCE = "coop"
BASE_URL = "https://www.easycoop.com"
SEARCH_URL = "https://catalog-service.adobe.io/graphql"
STORE_EXTERNAL_ID = "easycoop-c_901"
STORE_NAME = "EasyCoop Emilia"
STORE_CITY = "Bologna"
STORE_PROVINCE = "BO"
STORE_LAT = 44.4939
STORE_LNG = 11.3426

ENVIRONMENT_ID = "616d6486-7f01-4756-a12d-3c4a7050b3cb"
WEBSITE_CODE = "c_901"
STORE_CODE = "bologna_dark_store"
STORE_VIEW_CODE = "c_901"

RATE = float(os.getenv("COOP_RATE_SECONDS", "0.7"))
PAGE_SIZE = int(os.getenv("COOP_PAGE_SIZE", "48"))
PAGE_LIMIT = int(os.getenv("COOP_PAGE_LIMIT", "2"))
MAX_TERMS = int(os.getenv("COOP_MAX_TERMS", "12"))

NON_FOOD_TERMS = {"detersivo", "shampoo"}
FOOD_EXCLUDED_WORDS = {
    "detergente",
    "idratante",
    "rinfrescante",
    "antistress",
    "pelli",
    "viso",
    "corpo",
    "nivea",
    "clinians",
    "caffeina",
}
DEFAULT_TERMS = [
    "latte",
    "pasta",
    "riso",
    "tonno",
    "olio",
    "caffe",
    "zucchero",
    "farina",
    "uova",
    "pane",
    "acqua",
    "yogurt",
    "burro",
    "mozzarella",
    "parmigiano",
    "prosciutto",
    "pollo",
    "pomodoro",
    "passata",
    "biscotti",
    "cereali",
    "detersivo",
    "shampoo",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "it-IT,it;q=0.9",
    "Content-Type": "application/json",
    "Magento-Environment-Id": ENVIRONMENT_ID,
    "Magento-Website-Code": WEBSITE_CODE,
    "Magento-Store-Code": STORE_CODE,
    "Magento-Store-View-Code": STORE_VIEW_CODE,
    "X-Api-Key": "search_gql",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
}

SEARCH_QUERY = """
query ProductSearch($phrase: String!, $pageSize: Int!, $currentPage: Int!) {
  productSearch(phrase: $phrase, page_size: $pageSize, current_page: $currentPage) {
    total_count
    items {
      product {
        sku
        name
        canonical_url
        small_image { url }
        price_range {
          minimum_price {
            final_price { value currency }
            regular_price { value currency }
          }
        }
      }
      productView {
        attributes { name value }
      }
    }
  }
}
"""

_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[a-z0-9]+")


def _clean_text(value: object) -> str:
    return _WS_RE.sub(" ", str(value or "")).strip()


def _attr(attrs: dict[str, object], *names: str) -> str | None:
    for name in names:
        value = attrs.get(name)
        if value not in (None, ""):
            return _clean_text(value)
    return None



def _norm_words(value: object) -> list[str]:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return _WORD_RE.findall(text)


def _matches_phrase(product: dict, attrs: dict[str, object], phrase: str) -> bool:
    needles = _norm_words(phrase)
    if not needles:
        return True
    haystack = " ".join(
        str(v or "")
        for v in (
            product.get("name"),
            _attr(attrs, "manufacturer", "manufacturerName", "brand"),
            _attr(attrs, "es_category", "category"),
        )
    )
    words = set(_norm_words(haystack))
    if not any(needle in NON_FOOD_TERMS for needle in needles):
        if words.intersection(FOOD_EXCLUDED_WORDS):
            return False
    return all(needle in words for needle in needles)

def _float(value: object) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        parsed = float(str(value).replace(",", "."))
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _url(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return f"{BASE_URL}{value}"
    return value


class CoopSpider:
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

    def _terms(self) -> list[str]:
        custom = os.getenv("COOP_SEARCH_TERMS", "").strip()
        terms = [t.strip() for t in custom.split(",") if t.strip()] if custom else DEFAULT_TERMS
        if MAX_TERMS > 0:
            terms = terms[:MAX_TERMS]
        return terms

    async def _search(self, phrase: str, page: int) -> dict | None:
        await self._throttle()
        payload = {
            "query": SEARCH_QUERY,
            "variables": {
                "phrase": phrase,
                "pageSize": PAGE_SIZE,
                "currentPage": page,
            },
        }
        for attempt in range(3):
            try:
                r = await self.client.post(
                    SEARCH_URL,
                    json=payload,
                    headers=HEADERS,
                    timeout=45,
                    follow_redirects=True,
                )
                if r.status_code == 200:
                    data = r.json()
                    if data.get("errors"):
                        log.warning("EasyCoop GraphQL error per '%s': %s", phrase, data["errors"][:1])
                        return None
                    return data.get("data", {}).get("productSearch")
                log.warning("HTTP %s ricerca '%s' pagina %d", r.status_code, phrase, page)
                if r.status_code in (400, 403, 404):
                    return None
            except (httpx.RequestError, ValueError) as exc:
                log.warning("Tentativo %d errore ricerca '%s': %s", attempt + 1, phrase, exc)
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
                ($1, $2, 'E-commerce EasyCoop', $3, $4, NULL,
                 ST_SetSRID(ST_MakePoint($5, $6), 4326),
                 $7, TRUE, FALSE, TRUE)
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

    def _normalize_item(self, item: dict, phrase: str) -> dict | None:
        product = item.get("product") or {}
        sku = _clean_text(product.get("sku"))
        name = _clean_text(product.get("name"))
        if not sku or not name:
            return None

        attrs = {
            _clean_text(a.get("name")): a.get("value")
            for a in ((item.get("productView") or {}).get("attributes") or [])
            if a.get("name")
        }
        if not _matches_phrase(product, attrs, phrase):
            return None

        price_node = (
            (product.get("price_range") or {})
            .get("minimum_price") or {}
        )
        final_price = _float(((price_node.get("final_price") or {}).get("value")))
        regular_price = _float(((price_node.get("regular_price") or {}).get("value")))
        if not final_price:
            return None
        if regular_price and regular_price <= final_price:
            regular_price = None

        ean = _attr(attrs, "ean", "gtin", "barcode")
        barcode = ean if ean and ean.isdigit() else f"coop_{sku}"
        brand = _attr(attrs, "manufacturer", "manufacturerName", "brand")
        in_promo = (_attr(attrs, "in_promo") or "").lower()
        discount_percent = _float(_attr(attrs, "discount_percent", "discountPercentage"))
        promo_label = None
        if discount_percent:
            promo_label = f"Offerta -{discount_percent:.0f}%"
        elif regular_price:
            pct = round((regular_price - final_price) / regular_price * 100)
            promo_label = f"Offerta -{pct}%"
        elif in_promo in ("yes", "true", "1", "si", "sì"):
            promo_label = "Offerta"

        transparent = _float(_attr(attrs, "transparent_price", "member_transparent_price"))
        price_per_unit = None
        if transparent:
            candidate = transparent / 100 if transparent > 20 else transparent
            if 0 < candidate < 200:
                price_per_unit = round(candidate, 4)

        return {
            "barcode": barcode,
            "name": name,
            "brand": brand,
            "image_url": _url(((product.get("small_image") or {}).get("url"))),
            "price": final_price,
            "original_price": regular_price,
            "promo_label": promo_label,
            "price_per_unit": price_per_unit,
            "product_url": _url(product.get("canonical_url")),
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
        return len(by_bc)

    async def scrape_prices(self) -> int:
        store_id = await self.ensure_store()
        if not store_id:
            return 0

        seen: set[str] = set()
        total = 0
        terms = self._terms()
        log.info(
            "EasyCoop: %d termini, page_size=%d, page_limit=%d",
            len(terms), PAGE_SIZE, PAGE_LIMIT,
        )
        for term in terms:
            term_total = 0
            for page in range(1, PAGE_LIMIT + 1):
                result = await self._search(term, page)
                if not result:
                    break
                items = result.get("items") or []
                products = []
                for item in items:
                    product = self._normalize_item(item, term)
                    if product and product["barcode"] not in seen:
                        seen.add(product["barcode"])
                        products.append(product)
                count = await self._upsert_products_batch(products, store_id)
                term_total += count
                total += count
                total_count = int(result.get("total_count") or 0)
                if not items or page * PAGE_SIZE >= total_count:
                    break
            log.info("EasyCoop '%s': %d nuovi prezzi", term, term_total)

        log.info("=== EasyCoop/Coop: %d prezzi upsert ===", total)
        return total

    async def run(self) -> None:
        await self.scrape_prices()