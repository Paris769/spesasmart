"""
Aldi Italy homepage offers scraper.

Aldi renders offer/product tiles server-side on the homepage. This spider parses
those tiles and stores them as promotional prices for a virtual Aldi offers
store. The source is not a full grocery catalog and can include non-food offers.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin

import asyncpg
import httpx
from bs4 import BeautifulSoup

from ..aliases import resolve_existing

log = logging.getLogger("aldi")

BASE_URL = "https://www.aldi.it"
HOMEPAGE_URL = f"{BASE_URL}/it/homepage.html"
CHAIN_SLUG = "aldi"
SOURCE = "aldi"
STORE_EXTERNAL_ID = "aldi-offerte"
STORE_NAME = "Aldi Offerte"
STORE_CITY = "Verona"
STORE_PROVINCE = "VR"
STORE_LAT = 45.4384
STORE_LNG = 10.9916
RATE = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

_ID_RE = re.compile(r"\.(\d{8,18})\.html(?:$|[?#])")
_PRICE_RE = re.compile(r"(\d+[,.]\d{2})")
_WS_RE = re.compile(r"\s+")


def _clean_text(value: object) -> str:
    return _WS_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _price(value: object) -> Optional[float]:
    m = _PRICE_RE.search(_clean_text(value))
    if not m:
        return None
    try:
        parsed = float(m.group(1).replace(".", "").replace(",", "."))
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None



def _split_brand_title(title: str) -> tuple[str | None, str]:
    tokens = title.split()
    brand_tokens = []
    for token in tokens:
        letters = [ch for ch in token if ch.isalpha()]
        if token in {"&", "/"} or (letters and all(ch.isupper() for ch in letters)):
            brand_tokens.append(token)
            continue
        break
    if brand_tokens and len(brand_tokens) < len(tokens):
        return " ".join(brand_tokens), " ".join(tokens[len(brand_tokens):])
    return None, title

def _product_id(href: str) -> str | None:
    m = _ID_RE.search((href or "").strip())
    return m.group(1) if m else None


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

    async def _get(self, url: str) -> str | None:
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.get(
                    url,
                    headers=HEADERS,
                    timeout=45,
                    follow_redirects=True,
                )
                if r.status_code == 200:
                    return r.text
                log.warning("HTTP %s Aldi %s", r.status_code, url)
                if r.status_code in (403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore Aldi: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    async def _get_offers(self) -> str | None:
        homepage = await self._get(HOMEPAGE_URL)
        if not homepage:
            return None
        paths = sorted(set(re.findall(r"/it/offerte-settimanali/d\.\d{2}-\d{2}-\d{4}\.html", homepage)))
        if not paths:
            return homepage

        best_html = None
        best_count = -1
        for path in paths:
            html = await self._get(urljoin(BASE_URL, path))
            if not html:
                continue
            count = len(BeautifulSoup(html, "html.parser").select(".item.plp_product"))
            log.info("Aldi %s: %d prodotti", path, count)
            if count > best_count:
                best_html = html
                best_count = count
        return best_html or homepage

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

    @staticmethod
    def _parse_products(html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        products = []
        seen: set[str] = set()
        for item in soup.select(".item.plp_product"):
            link = item.find_parent("a") or item.select_one("a[href]")
            href = link.get("href") if link else ""
            product_id = _product_id(href)
            title_el = item.select_one(".product-title")
            title = _clean_text(title_el.get_text(" ", strip=True) if title_el else "")
            price_el = item.select_one(".retail_price .price, .retail_price")
            price = _price(price_el.get_text(" ", strip=True) if price_el else "")
            if not product_id or not title or not price or product_id in seen:
                continue
            seen.add(product_id)

            brand, name = _split_brand_title(title)
            unit_text = _clean_text((item.select_one(".additional-product-info") or {}).get_text(" ", strip=True) if item.select_one(".additional-product-info") else "")
            img = item.select_one("img[data-src], img[src]")
            image_url = None
            if img:
                image_url = img.get("data-src") or img.get("src")
                if image_url:
                    image_url = urljoin(BASE_URL, image_url)

            products.append({
                "barcode": f"aldi_{product_id}",
                "name": name,
                "brand": brand,
                "image_url": image_url,
                "price": price,
                "original_price": None,
                "promo_label": "Offerta Aldi",
                "price_per_unit": _price(unit_text) if "kg" in unit_text.lower() or "l" in unit_text.lower() else None,
                "product_url": urljoin(BASE_URL, href),
            })
        return products

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
        html = await self._get_offers()
        if not html:
            return 0
        products = self._parse_products(html)
        total = await self._upsert_products_batch(products, store_id)
        log.info("=== Aldi: %d prezzi upsert ===", total)
        return total

    async def run(self) -> None:
        await self.scrape_prices()