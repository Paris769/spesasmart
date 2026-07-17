"""
MD Italy flyer scraper.

MD exposes store metadata on mdspa.it and structured flyer product data inside
service-volantino.mdspa.it pages. This spider stores current flyer offers for a
reference MD store. It is a promotional flyer subset, not the full catalog.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from html import unescape
from typing import Optional
from urllib.parse import urljoin

import asyncpg
import httpx
from bs4 import BeautifulSoup

from ..aliases import resolve_existing

log = logging.getLogger("md")

BASE_URL = "https://www.mdspa.it"
FLYER_BASE_URL = "https://service-volantino.mdspa.it"
CHAIN_SLUG = "md"
SOURCE = "md"
REFERENCE_PV_ID = "1"
STORE_EXTERNAL_PREFIX = "md-pv"
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

_DATA_RE = re.compile(r"var\s+data\s*=\s*(\[.*?\]);\s*\r?\n", re.S)
_WS_RE = re.compile(r"\s+")


def _clean_text(value: object) -> str:
    text = BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)
    return _WS_RE.sub(" ", unescape(text).replace("\xa0", " ")).strip()


def _num(value: object) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


class MdSpider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        conn: asyncpg.Connection,
        dry_run: bool = False,
        pv_id: str = REFERENCE_PV_ID,
    ):
        self.client = client
        self.conn = conn
        self.dry_run = dry_run
        self.pv_id = pv_id
        self._t_last = 0.0

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._t_last
        if elapsed < RATE:
            await asyncio.sleep(RATE - elapsed)
        self._t_last = loop.time()

    async def _get(self, url: str) -> httpx.Response | None:
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.get(
                    url,
                    headers=HEADERS,
                    timeout=60,
                    follow_redirects=True,
                )
                if r.status_code == 200:
                    return r
                log.warning("HTTP %s MD GET %s", r.status_code, url)
                if r.status_code in (403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore MD GET: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    async def _post_pv(self) -> dict | None:
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.post(
                    f"{BASE_URL}/punti_vendita_admin/get_pv.php",
                    data={"pv": self.pv_id},
                    headers=HEADERS,
                    timeout=45,
                    follow_redirects=True,
                )
                if r.status_code == 200 and r.text.strip().startswith("{"):
                    return r.json()
                log.warning("HTTP %s MD pv %s", r.status_code, self.pv_id)
            except (httpx.RequestError, json.JSONDecodeError) as exc:
                log.warning("Tentativo %d errore MD pv: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    async def _flyer_code(self, pv: dict) -> tuple[str | None, str | None]:
        link = ((pv.get("pv") or {}).get("link_scheda") or "").replace(BASE_URL, "")
        url = f"{BASE_URL}/sfogliatore/?id_pv={self.pv_id}"
        r = await self._get(url)
        if not r:
            return None, None
        soup = BeautifulSoup(r.text, "html.parser")
        node = soup.select_one("[data-flyer][data-flyer-code]")
        if not node:
            return None, link or url
        return node.get("data-flyer-code"), link or url

    async def ensure_store(self, pv: dict) -> str | None:
        info = pv.get("pv") or {}
        external_id = f"{STORE_EXTERNAL_PREFIX}-{info.get('id') or self.pv_id}"
        row = await self.conn.fetchrow(
            """
            SELECT s.id
            FROM stores s
            JOIN chains c ON c.id = s.chain_id
            WHERE c.slug = $1 AND s.external_id = $2
            """,
            CHAIN_SLUG,
            external_id,
        )
        if row:
            return str(row["id"])

        name = f"MD {info.get('citta') or 'Volantino'}".strip()
        city = info.get("citta") or ""
        province = (info.get("link_scheda") or "").split("/")[-2][:2] if info.get("link_scheda") else ""
        address = info.get("indirizzo") or "Volantino online"
        lat = _num(info.get("latitudine")) or 41.8719
        lng = _num(info.get("longitudine")) or 12.5674

        if self.dry_run:
            log.info("[DRY] Creerebbe store %s", name)
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
                ($1, $2, $3, $4, $5, $6,
                 ST_SetSRID(ST_MakePoint($7, $8), 4326),
                 $9, FALSE, FALSE, TRUE)
            RETURNING id
            """,
            chain_id,
            name,
            address,
            city,
            province,
            info.get("cap"),
            lng,
            lat,
            external_id,
        ))

    @staticmethod
    def _image_url(product: dict) -> str | None:
        photos = product.get("photos") or []
        if not photos:
            return None
        chosen = next((p for p in photos if p.get("isDefault")), photos[0])
        image_url = chosen.get("imageUrl")
        return urljoin(FLYER_BASE_URL, image_url) if image_url else None

    @staticmethod
    def _parse_products(html: str, flyer_code: str) -> list[dict]:
        match = _DATA_RE.search(html)
        if not match:
            return []
        raw_products = json.loads(match.group(1))
        products = []
        for raw in raw_products:
            code = str(raw.get("code") or raw.get("idProduct") or "").strip()
            name = _clean_text(raw.get("name") or raw.get("title"))
            price = _num(raw.get("priceOff") or raw.get("price"))
            if not code or not name or not price:
                continue
            original = _num(raw.get("price"))
            if original and original <= price:
                original = None
            category = _clean_text(raw.get("category"))
            section = _clean_text(raw.get("section"))
            promo_parts = [p for p in [section, category] if p and p.upper() != "TUTTE"]
            promo_label = " - ".join(promo_parts) or "Volantino MD"
            description = _clean_text(raw.get("description"))
            if description:
                name = f"{name} {description}" if len(name) < 90 else name
            product_url = raw.get("webstoreUrl") or f"{FLYER_BASE_URL}/{flyer_code}"
            products.append({
                "barcode": f"md_{code}",
                "name": name[:255],
                "brand": _clean_text(raw.get("brand")) or None,
                "image_url": MdSpider._image_url(raw),
                "price": price,
                "original_price": original,
                "promo_label": promo_label[:255],
                "price_per_unit": None,
                "product_url": product_url,
            })
        return products

    async def _upsert_products_batch(self, products: list[dict], store_id: str) -> int:
        by_bc: dict[str, dict] = {p["barcode"]: p for p in products if p}
        if not by_bc:
            return 0
        if self.dry_run:
            for p in list(by_bc.values())[:25]:
                log.info("[DRY] %-60s EUR %.2f", p["name"][:60], p["price"])
            log.info("[DRY] Totale prodotti MD: %d", len(by_bc))
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
        pv = await self._post_pv()
        if not pv:
            return 0
        store_id = await self.ensure_store(pv)
        if not store_id:
            return 0
        flyer_code, _ = await self._flyer_code(pv)
        if not flyer_code:
            log.warning("Codice volantino MD non trovato")
            return 0
        r = await self._get(f"{FLYER_BASE_URL}/{flyer_code}")
        if not r:
            return 0
        products = self._parse_products(r.text, flyer_code)
        total = await self._upsert_products_batch(products, store_id)
        log.info("=== MD: %d prezzi upsert ===", total)
        return total

    async def run(self) -> None:
        await self.scrape_prices()