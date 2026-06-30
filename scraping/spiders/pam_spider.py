"""
Pam Panorama / Pam a Casa price scraper.

Pam a Casa renders product cards server-side. Search pages expose product id,
name, brand, price, old price, image and unit price without authentication.
The scraper samples common grocery search terms and stores prices against a
virtual Pam a Casa online store.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from typing import Optional
from urllib.parse import quote_plus, urljoin

import asyncpg
import httpx
from bs4 import BeautifulSoup

from ..aliases import resolve_existing

log = logging.getLogger("pam")

BASE_URL = "https://pamacasa.pampanorama.it"
CHAIN_SLUG = "pam"
SOURCE = "pam"
STORE_EXTERNAL_ID = "pam-a-casa"
STORE_NAME = "Pam a Casa"
STORE_CITY = "Spinea"
STORE_PROVINCE = "VE"
STORE_LAT = 45.4917
STORE_LNG = 12.1609

RATE = float(os.getenv("PAM_RATE_SECONDS", "0.8"))
MAX_TERMS = int(os.getenv("PAM_MAX_TERMS", "12"))

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

NON_FOOD_TERMS = {"detersivo", "shampoo"}
FOOD_EXCLUDED_WORDS = {
    "detergente",
    "idratante",
    "rinfrescante",
    "antistress",
    "pelli",
    "viso",
    "corpo",
    "caffeina",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[a-z0-9]+")
_EAN_RE = re.compile(r"/(\d{8,14})\.jpg(?:\?|$)", re.I)
_UNIT_RE = re.compile(r"-\s*([\d,.]+)\s*€\s+al\s+", re.I)


def _clean_text(value: object) -> str:
    return _WS_RE.sub(" ", str(value or "")).strip()


def _norm_words(value: object) -> list[str]:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return _WORD_RE.findall(text)


def _matches_phrase(product: dict, phrase: str) -> bool:
    needles = _norm_words(phrase)
    if not needles:
        return True
    words = set(_norm_words(" ".join([product.get("name") or "", product.get("brand") or ""])))
    if not any(needle in NON_FOOD_TERMS for needle in needles):
        if words.intersection(FOOD_EXCLUDED_WORDS):
            return False
    return all(needle in words for needle in needles)


def _price(value: object) -> Optional[float]:
    text = _clean_text(value).replace("€", "").replace(".", "").replace(",", ".")
    try:
        parsed = float(text)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _unit_price(meta: str | None) -> Optional[float]:
    m = _UNIT_RE.search(meta or "")
    return _price(m.group(1)) if m else None


def _barcode(product_id: str, image_url: str | None) -> str:
    if image_url:
        m = _EAN_RE.search(image_url)
        if m:
            return m.group(1)
    return f"pam_{product_id}"


class PamSpider:
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
        custom = os.getenv("PAM_SEARCH_TERMS", "").strip()
        terms = [t.strip() for t in custom.split(",") if t.strip()] if custom else DEFAULT_TERMS
        if MAX_TERMS > 0:
            terms = terms[:MAX_TERMS]
        return terms

    async def _get_search_page(self, term: str) -> str | None:
        await self._throttle()
        url = f"{BASE_URL}/ricerca?search={quote_plus(term)}"
        headers = {**HEADERS, "Referer": f"{BASE_URL}/"}
        for attempt in range(3):
            try:
                r = await self.client.get(url, headers=headers, timeout=45, follow_redirects=True)
                if r.status_code == 200:
                    return r.text
                log.warning("HTTP %s ricerca Pam '%s'", r.status_code, term)
                if r.status_code in (403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore Pam '%s': %s", attempt + 1, term, exc)
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
                ($1, $2, 'E-commerce Pam a Casa', $3, $4, NULL,
                 ST_SetSRID(ST_MakePoint($5, $6), 4326),
                 $7, TRUE, TRUE, TRUE)
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
    def _parse_products(html: str, phrase: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        products = []
        for card in soup.select("section.product"):
            data = card.select_one("[data-id][data-name][data-price]")
            if not data:
                continue
            product_id = _clean_text(data.get("data-id"))
            name = _clean_text(data.get("data-name"))
            price = _price(data.get("data-price"))
            if not product_id or not name or not price:
                continue
            brand = _clean_text(data.get("data-brand")) or None
            image_url = data.get("data-img-src") or None
            if image_url:
                image_url = urljoin(BASE_URL, image_url)
            href_el = card.select_one(".product-img a[href], a[href*='/prodotto/']")
            product_url = urljoin(BASE_URL, href_el.get("href")) if href_el else None
            original_price = _price(data.get("data-old-price"))
            if original_price and original_price <= price:
                original_price = None
            product = {
                "barcode": _barcode(product_id, image_url),
                "name": name,
                "brand": brand,
                "image_url": image_url,
                "price": price,
                "original_price": original_price,
                "promo_label": "Offerta" if original_price else None,
                "price_per_unit": _unit_price(data.get("data-meta")),
                "product_url": product_url,
            }
            if _matches_phrase(product, phrase):
                products.append(product)
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
            source_rows = await self.conn.fetch(
                "SELECT barcode, source FROM products WHERE barcode = ANY($1::text[])",
                barcodes,
            )
            source_by_bc = {r["barcode"]: r["source"] for r in source_rows}
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

            upd = [bc for bc in barcodes if bc in direct_bcs and source_by_bc.get(bc) == SOURCE]
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
        log.info("Pam a Casa: %d termini", len(terms))
        for term in terms:
            html = await self._get_search_page(term)
            if not html:
                continue
            products = []
            for product in self._parse_products(html, term):
                if product["barcode"] not in seen:
                    seen.add(product["barcode"])
                    products.append(product)
            count = await self._upsert_products_batch(products, store_id)
            total += count
            log.info("Pam '%s': %d nuovi prezzi", term, count)
        log.info("=== Pam a Casa: %d prezzi upsert ===", total)
        return total

    async def run(self) -> None:
        await self.scrape_prices()