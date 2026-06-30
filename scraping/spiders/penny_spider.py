"""
Penny Italy promotion scraper.

Penny exposes the current offer products server-side on /offerte. This spider
parses the rendered product tiles and stores promotional prices against a
virtual Penny online/offers store. The catalog is promotional, not the full
assortment.
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

log = logging.getLogger("penny")

BASE_URL = "https://www.penny.it"
OFFERS_URL = f"{BASE_URL}/offerte"
CHAIN_SLUG = "penny"
SOURCE = "penny"
STORE_EXTERNAL_ID = "penny-offerte"
STORE_NAME = "Penny Offerte"
STORE_CITY = "Cernusco sul Naviglio"
STORE_PROVINCE = "MI"
STORE_LAT = 45.5240
STORE_LNG = 9.3305
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

_PRICE_RE = re.compile(r"(\d+[,.]\d{2})\s*€")
_ID_RE = re.compile(r"-(\d+)$")
_WS_RE = re.compile(r"\s+")


def _clean_text(value: object) -> str:
    return _WS_RE.sub(" ", str(value or "").replace("\xa0", " ")).strip()


def _price(value: object) -> Optional[float]:
    text = _clean_text(value).replace(".", "").replace(",", ".")
    try:
        parsed = float(text)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _first_price(text: str) -> Optional[float]:
    m = _PRICE_RE.search(_clean_text(text))
    return _price(m.group(1)) if m else None


def _product_id_from_href(href: str) -> str | None:
    m = _ID_RE.search((href or "").rstrip("/"))
    return m.group(1) if m else None


class PennySpider:
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

    async def _get_offers(self) -> str | None:
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.get(
                    OFFERS_URL,
                    headers=HEADERS,
                    timeout=45,
                    follow_redirects=True,
                )
                if r.status_code == 200:
                    return r.text
                log.warning("HTTP %s Penny offerte", r.status_code)
                if r.status_code in (403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore Penny: %s", attempt + 1, exc)
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
                ($1, $2, 'Volantino/offerte online', $3, $4, NULL,
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
        for item in soup.select("li.ws-product-item-base, .ws-product-item-base"):
            link = item.select_one("a[data-test='product-tile-link'][href]")
            href = link.get("href") if link else ""
            product_id = _product_id_from_href(href)
            title_el = item.select_one("[data-test='product-title']")
            title = _clean_text(title_el.get_text(" ", strip=True) if title_el else "")
            if not product_id or not title:
                continue

            parts = [p.strip() for p in title.split("•", 1)]
            name = parts[0]
            brand = parts[1] if len(parts) > 1 and parts[1] else None

            price_nodes = item.select("[data-test='product-price-type']")
            chosen = price_nodes[-1] if price_nodes else item.select_one("[data-test='product-price']")
            price = _first_price(chosen.get_text(" ", strip=True) if chosen else "")
            if not price:
                continue

            original_price = None
            promo_label = None
            if price_nodes and len(price_nodes) > 1:
                first = _first_price(price_nodes[0].get_text(" ", strip=True))
                if first and first > price:
                    original_price = first
                    promo_label = "PENNYCard"
            strike = item.select_one("[data-test='product-price-type-value'] s")
            if strike:
                old = _first_price(strike.get_text(" ", strip=True))
                if old and old > price:
                    original_price = old
                    promo_label = "Offerta"
            discount = _clean_text((item.select_one("[data-test='product-badge-discount']") or {}).get_text(" ", strip=True) if item.select_one("[data-test='product-badge-discount']") else "")
            badge = _clean_text((item.select_one("[data-test='product-badge-text']") or {}).get_text(" ", strip=True) if item.select_one("[data-test='product-badge-text']") else "")
            if discount:
                promo_label = discount
            elif badge and not promo_label:
                promo_label = badge

            unit = None
            labels = chosen.select("[data-test='product-price-type-label']") if chosen else []
            if labels:
                unit = _first_price(labels[-1].get_text(" ", strip=True))

            img_url = None
            for img in item.select("img[src]"):
                src = img.get("src") or ""
                if src.startswith("http") and "exclamation_mark" not in src:
                    img_url = src
                    break

            products.append({
                "barcode": f"penny_{product_id}",
                "name": name,
                "brand": brand,
                "image_url": img_url,
                "price": price,
                "original_price": original_price,
                "promo_label": promo_label,
                "price_per_unit": unit,
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
        log.info("=== Penny: %d prezzi upsert ===", total)
        return total

    async def run(self) -> None:
        await self.scrape_prices()