"""
Lidl Italy homepage offer scraper.

Lidl embeds a small set of offer products in data-grid-data JSON attributes on
its homepage. This spider stores the products that have a visible price. It is a
promotional subset, not the full Lidl catalog.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional
from urllib.parse import urljoin

import asyncpg
import httpx
from bs4 import BeautifulSoup

from ..aliases import resolve_existing

log = logging.getLogger("lidl")

BASE_URL = "https://www.lidl.it"
OFFERS_URL = f"{BASE_URL}/"
CHAIN_SLUG = "lidl"
SOURCE = "lidl"
STORE_EXTERNAL_ID = "lidl-offerte"
STORE_NAME = "Lidl Offerte"
STORE_CITY = "Arcole"
STORE_PROVINCE = "VR"
STORE_LAT = 45.3569
STORE_LNG = 11.2864
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


class LidlSpider:
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
                log.warning("HTTP %s Lidl homepage", r.status_code)
                if r.status_code in (403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore Lidl: %s", attempt + 1, exc)
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

    @staticmethod
    def _parse_products(html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        products = []
        seen: set[str] = set()
        for node in soup.select("[data-grid-data]"):
            try:
                raw = json.loads(node.get("data-grid-data") or "{}")
            except json.JSONDecodeError:
                continue
            product_id = str(raw.get("productId") or raw.get("itemId") or "").strip()
            price_obj = raw.get("price") or {}
            price = price_obj.get("price")
            title = (raw.get("fullTitle") or raw.get("title") or "").strip()
            if not product_id or not title or not price or product_id in seen:
                continue
            seen.add(product_id)
            old_price = price_obj.get("oldPrice")
            if old_price and old_price <= price:
                old_price = None
            discount = (price_obj.get("discount") or {}).get("discountText")
            base_text = (price_obj.get("basePrice") or {}).get("text") or ""
            products.append({
                "barcode": f"lidl_{product_id}",
                "name": title,
                "brand": (raw.get("brand") or {}).get("name") or None,
                "image_url": raw.get("image") or None,
                "price": float(price),
                "original_price": float(old_price) if old_price else None,
                "promo_label": discount or "Offerta Lidl",
                "price_per_unit": None,
                "product_url": urljoin(BASE_URL, raw.get("canonicalUrl") or ""),
                "unit_text": base_text,
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
                   SELECT v.id, $2, v.price, v.orig, v.promo, NULL,
                          TRUE, TRUE, $6, v.url, NOW()
                   FROM unnest($1::uuid[], $3::numeric[], $4::numeric[], $5::text[],
                               $7::text[])
                        AS v(id, price, orig, promo, url)""",
                all_ids,
                store_id,
                [by_bc[b]["price"] for b in barcodes],
                [by_bc[b]["original_price"] for b in barcodes],
                [by_bc[b]["promo_label"] for b in barcodes],
                SOURCE,
                [by_bc[b]["product_url"] for b in barcodes],
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
        log.info("=== Lidl: %d prezzi upsert ===", total)
        return total

    async def run(self) -> None:
        await self.scrape_prices()