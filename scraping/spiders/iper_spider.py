"""
IperSpider - store discovery and promotion prices via Digital Flyer API.

Flow:
  1. Store discovery keeps the existing SSR JSON-LD path on www.iper.it.
  2. Price scraping uses cataloghi.iper.it/digitalflyer public API:
     OAuth client_credentials, then promotions/{promotion}/stores/{store}/products.

The catalog contains promotional products, not the full online grocery catalog.
Runs are limited by IPER_MAX_STORES so CI and Supabase do not get overloaded.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from typing import Optional

import asyncpg
import httpx

from ..aliases import resolve_existing

log = logging.getLogger("iper")

BASE_URL = "https://www.iper.it"
CATALOG_CTX = "https://cataloghi.iper.it/digitalflyer"
CHAIN_SLUG = "iper"
SOURCE = "iper"
RATE = 0.5
STORE_LIMIT = int(os.getenv("IPER_MAX_STORES", "4"))

# Public client visible in the Nuxt runtime config of cataloghi.iper.it.
_AUTH_BASIC = "19b9f07e-1832-451f-9c31-f61f8179a0d0:E2bVX94H"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

API_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "application/json",
    "Accept-Language": "it-IT,it;q=0.9",
}

IPER_STORES: list[tuple[str, str]] = [
    ("monza-maestoso", "Il Mercato del Maestoso by Iper"),
    ("arese", "Iper La grande i - Arese"),
    ("brembate", "Iper La grande i - Brembate"),
    ("busnago", "Iper La grande i - Busnago"),
    ("castelfranco-veneto", "Iper La grande i - Castelfranco Veneto"),
    ("cremona", "Iper La grande i - Cremona"),
    ("grandate", "Iper La grande i - Grandate"),
    ("lonato", "Iper La grande i - Lonato del Garda"),
    ("magenta", "Iper La grande i - Magenta"),
    ("milano-portello", "Iper La grande i - Milano Portello"),
    ("montebello-della-battaglia", "Iper La grande i - Montebello della Battaglia"),
    ("monza", "Iper La grande i - Monza"),
    ("orio", "Iper La grande i - Orio"),
    ("rozzano", "Iper La grande i - Rozzano"),
    ("savignano-sul-rubicone", "Iper La grande i - Savignano sul Rubicone"),
    ("seriate", "Iper La grande i - Seriate"),
    ("serravalle", "Iper La grande i - Serravalle"),
    ("solbiate", "Iper La grande i - Solbiate"),
    ("tortona", "Iper La grande i - Tortona"),
    ("varese", "Iper La grande i - Varese"),
    ("verona", "Iper La grande i - Verona"),
    ("vittuone", "Iper La grande i - Vittuone"),
]

_LD_JSON_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>',
    re.S | re.I,
)
_ADDR_RE = re.compile(r"^(.*?)\s+([A-ZÀ-Ü][a-zà-ü\s]+)\s+\(([A-Z]{2})\)\s*$")
_WS_RE = re.compile(r"\s+")


def _parse_address(street_address: str) -> tuple[str, str, str]:
    m = _ADDR_RE.match(street_address.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3)
    paren = re.search(r"\(([A-Z]{2})\)", street_address)
    if paren:
        province = paren.group(1)
        rest = street_address[: paren.start()].strip()
        parts = rest.rsplit(" ", 1)
        city = parts[-1] if parts else ""
        street = parts[0] if len(parts) > 1 else rest
        return street, city, province
    return street_address, "", ""


def _clean_text(value: object) -> str:
    return _WS_RE.sub(" ", str(value or "")).strip()


def _prop(product: dict, code: str) -> dict | None:
    for item in product.get("properties") or []:
        if item.get("code") == code:
            return item
    return None


def _prop_first(product: dict, code: str) -> object | None:
    item = _prop(product, code)
    values = item.get("values") if item else None
    return values[0] if values else None


def _prop_float(product: dict, code: str) -> Optional[float]:
    value = _prop_first(product, code)
    try:
        if value is None:
            return None
        f = float(value)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _image_url(product: dict) -> str | None:
    value = _prop_first(product, "IMAGES")
    if not isinstance(value, dict):
        return None
    unique_id = value.get("uniqueId")
    basename = value.get("basename")
    extension = value.get("extension")
    if not (unique_id and basename and extension):
        return None
    return f"{CATALOG_CTX}/files/{unique_id}/{basename}.{extension}"


class IperSpider:
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
        self._token: str | None = None

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
                    url, headers=HEADERS, timeout=30, follow_redirects=True
                )
                if r.status_code == 200:
                    return r.text
                log.warning("HTTP %s %s (attempt %d)", r.status_code, url, attempt + 1)
                if r.status_code in (403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Attempt %d error: %s", attempt + 1, exc)
            await asyncio.sleep(2**attempt)
        return None

    async def _login(self) -> str:
        if self._token:
            return self._token
        basic = base64.b64encode(_AUTH_BASIC.encode()).decode()
        r = await self.client.post(
            f"{CATALOG_CTX}/oauth/token",
            headers={
                **API_HEADERS,
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": "read write"},
            timeout=30,
            follow_redirects=True,
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    async def _api_get(self, path: str, params: dict | None = None) -> dict | list | None:
        await self._throttle()
        token = await self._login()
        url = f"{CATALOG_CTX}/api/iper/iper-cataloghi/{path.lstrip('/')}"
        for attempt in range(3):
            try:
                r = await self.client.get(
                    url,
                    params=params,
                    headers={**API_HEADERS, "Authorization": f"Bearer {token}"},
                    timeout=45,
                    follow_redirects=True,
                )
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 401:
                    self._token = None
                    token = await self._login()
                    continue
                log.warning("API HTTP %s %s (attempt %d)", r.status_code, url, attempt + 1)
                if r.status_code in (403, 404):
                    return None
            except (httpx.RequestError, ValueError) as exc:
                log.warning("API attempt %d error: %s", attempt + 1, exc)
            await asyncio.sleep(2**attempt)
        return None

    @staticmethod
    def _extract_store_ld(html: str) -> dict | None:
        for m in _LD_JSON_RE.finditer(html):
            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("@type") == "Store":
                return data
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "Store":
                        return item
        return None

    async def _upsert_store(
        self, chain_id: int, slug: str, fallback_name: str, ld: dict
    ) -> None:
        geo = ld.get("geo", {})
        try:
            lat = float(geo.get("latitude") or 0)
            lng = float(geo.get("longitude") or 0)
        except (ValueError, TypeError):
            log.warning("Coordinate non valide per %s", slug)
            return
        if not lat or not lng:
            log.warning("Coordinate mancanti per %s", slug)
            return

        name = ld.get("name") or fallback_name
        phone = ld.get("telephone") or None
        street_raw = (ld.get("address") or {}).get("streetAddress", "")
        street, city, province = _parse_address(street_raw)
        opening_hours = (
            json.dumps(ld["openingHoursSpecification"])
            if ld.get("openingHoursSpecification")
            else None
        )

        if self.dry_run:
            log.info("[DRY] %s | %s, %s | lat=%.5f lng=%.5f", name, city, province, lat, lng)
            return

        existing = await self.conn.fetchval(
            "SELECT id FROM stores WHERE chain_id=$1 AND external_id=$2",
            chain_id,
            slug,
        )
        if existing:
            await self.conn.execute(
                """UPDATE stores
                   SET name=$1, address=$2, city=$3, province=$4,
                       coordinates=ST_SetSRID(ST_MakePoint($5,$6),4326),
                       phone=$7, opening_hours=$8::jsonb, last_verified=NOW()
                   WHERE id=$9""",
                name, street, city, province,
                lng, lat, phone, opening_hours, existing,
            )
        else:
            await self.conn.execute(
                """INSERT INTO stores
                   (chain_id, external_id, name, address, city, province,
                    coordinates, phone, opening_hours)
                   VALUES ($1,$2,$3,$4,$5,$6,
                           ST_SetSRID(ST_MakePoint($7,$8),4326),
                           $9,$10::jsonb)""",
                chain_id, slug, name, street, city, province,
                lng, lat, phone, opening_hours,
            )
        log.info("Upsert: %s (%s, %s)", name, city, province)

    async def discover_stores(self) -> int:
        chain_id = await self.conn.fetchval(
            "SELECT id FROM chains WHERE slug=$1", CHAIN_SLUG
        )
        if not chain_id:
            log.error("Chain '%s' non trovata nel DB", CHAIN_SLUG)
            return 0

        upserted = 0
        for slug, fallback_name in IPER_STORES:
            url = f"{BASE_URL}/punti-vendita/{slug}/"
            html = await self._get(url)
            if not html:
                log.warning("Impossibile caricare %s", url)
                continue
            ld = self._extract_store_ld(html)
            if not ld:
                log.warning("JSON-LD Store non trovato per %s", slug)
                continue
            await self._upsert_store(chain_id, slug, fallback_name, ld)
            upserted += 1

        log.info("=== Iper: %d/%d negozi upsert ===", upserted, len(IPER_STORES))
        return upserted

    async def _price_stores(self) -> list[asyncpg.Record]:
        rows = await self.conn.fetch(
            """
            SELECT s.id, s.external_id, s.name, MAX(p.scraped_at) AS last_scraped
            FROM stores s
            JOIN chains c ON c.id = s.chain_id
            LEFT JOIN prices p ON p.store_id = s.id AND p.source = $1
            WHERE c.slug = $2 AND s.is_active = TRUE
            GROUP BY s.id, s.external_id, s.name
            ORDER BY MAX(p.scraped_at) ASC NULLS FIRST, s.external_id
            LIMIT $3
            """,
            SOURCE,
            CHAIN_SLUG,
            STORE_LIMIT,
        )
        return rows

    def _normalize_product(self, raw: dict, promotion_alias: str, store_alias: str) -> dict | None:
        code = str((raw.get("code") or {}).get("value") or "").strip()
        name = _clean_text(raw.get("description"))
        price = _prop_float(raw, "END-PRICE")
        if not code or not name or not price:
            return None
        original = _prop_float(raw, "INITIAL-PRICE")
        if original and original <= price:
            original = None
        discount_rate = _prop_float(raw, "DISCOUNT-RATE")
        discount = _clean_text(_prop_first(raw, "DISCOUNT"))
        promo_label = None
        if discount_rate:
            promo_label = f"Offerta -{discount_rate:.0f}%"
        elif original:
            pct = round((original - price) / original * 100)
            promo_label = f"Offerta -{pct}%"
        elif discount:
            promo_label = discount

        alias = str(raw.get("alias") or code)
        return {
            "barcode": f"iper_{code}",
            "name": name,
            "brand": _clean_text(_prop_first(raw, "MARK")) or None,
            "image_url": _image_url(raw),
            "price": price,
            "original_price": original,
            "promo_label": promo_label,
            "price_per_unit": _prop_float(raw, "END-KG-LT-PRICE"),
            "product_url": (
                "https://cataloghi.iper.it"
                f"/punti-vendita/{store_alias}/promozioni/{promotion_alias}/prodotti/{alias}"
            ),
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

    async def _scrape_store_prices(self, store_id: str, external_id: str) -> int:
        store_alias = external_id if external_id.startswith("iper-") else f"iper-{external_id}"
        promotions = await self._api_get(f"stores/{store_alias}/promotions")
        if not isinstance(promotions, list):
            log.warning("Nessuna promozione Iper per %s", store_alias)
            return 0

        total = 0
        for promo in promotions:
            if promo.get("hidden"):
                continue
            promotion_alias = promo.get("alias")
            if not promotion_alias:
                continue
            page = 0
            while True:
                data = await self._api_get(
                    f"promotions/{promotion_alias}/stores/{store_alias}/products",
                    params={"size": 100, "page": page},
                )
                if not isinstance(data, dict):
                    break
                raw_products = data.get("elements") or []
                products = [
                    self._normalize_product(p, promotion_alias, store_alias)
                    for p in raw_products
                ]
                total += await self._upsert_products_batch(
                    [p for p in products if p], store_id
                )
                if data.get("last") or page + 1 >= int(data.get("totalPages") or 1):
                    break
                page += 1
        return total

    async def scrape_prices(self) -> int:
        stores = await self._price_stores()
        if not stores:
            log.warning("Nessun negozio Iper nel DB: eseguo discovery")
            await self.discover_stores()
            stores = await self._price_stores()
        log.info("Negozi Iper da scrapare: %d (limite %d)", len(stores), STORE_LIMIT)
        total = 0
        for i, store in enumerate(stores, start=1):
            count = await self._scrape_store_prices(str(store["id"]), store["external_id"])
            total += count
            log.info("[%d/%d] %s: %d prezzi", i, len(stores), store["name"], count)
        log.info("=== Iper: %d prezzi upsert ===", total)
        return total

    async def run(self) -> None:
        await self.scrape_prices()