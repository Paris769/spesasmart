"""
FamilaSpider — store discovery via regional sitemaps + __NEXT_DATA__.

Flusso:
  1. GET /sitemap.xml → lista partner sitemaps
     (famila, famila-nord, famila-nord-est, famila-sud, …)
  2. Per ogni partner: GET /{partner}/sitemap.xml → URL negozi individuali
     Pattern: /{partner}/punti-vendita/{regione}/{store-slug}
  3. Per ogni URL: GET pagina → estrae __NEXT_DATA__ → pageProps.store
  4. Upsert negozio nel DB

Il spider implementa solo il discovery dei negozi (nessun prezzo disponibile
nel formato HTML; la piattaforma SMT DigitalFlyer è client-side rendered).
"""
import asyncio
import json
import logging
import re
from xml.etree import ElementTree

import asyncpg
import httpx

log = logging.getLogger("famila")

BASE_URL = "https://www.famila.it"
ROOT_SITEMAP = f"{BASE_URL}/sitemap.xml"
CHAIN_SLUG = "famila"
RATE = 1.5  # secondi tra richieste

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.S,
)


class FamilaSpider:
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

    # ── HTTP ─────────────────────────────────────────────────────────────────

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

    # ── Sitemap ───────────────────────────────────────────────────────────────

    async def _collect_store_urls(self) -> list[str]:
        """Return all individual store page URLs found in sitemaps."""
        root_xml = await self._get(ROOT_SITEMAP)
        if not root_xml:
            log.error("Root sitemap unreachable")
            return []

        try:
            root = ElementTree.fromstring(root_xml)
        except ElementTree.ParseError as e:
            log.error("Root sitemap parse error: %s", e)
            return []

        # Root is a <sitemapindex>; collect child sitemap URLs.
        # Use {*}loc (namespace-agnostic) because famila partner sitemaps use a
        # custom namespace (https://www.famila.it/schemas/sitemap/0.9) instead of
        # the standard one, which breaks namespace-prefix lookups.
        child_sitemaps = [
            loc.text
            for loc in root.findall(".//{*}loc")
            if loc.text and "/sitemap.xml" in loc.text
        ]
        log.info("Root sitemap → %d partner sitemaps", len(child_sitemaps))

        store_urls: list[str] = []
        for sm_url in child_sitemaps:
            xml_text = await self._get(sm_url)
            if not xml_text:
                continue
            try:
                tree = ElementTree.fromstring(xml_text)
            except ElementTree.ParseError:
                continue
            for loc in tree.findall(".//{*}loc"):
                url = loc.text or ""
                path = url.replace(BASE_URL, "")
                parts = [p for p in path.split("/") if p]
                # Individual store pages have exactly 4 path segments:
                # /{partner}/punti-vendita/{regione}/{store-slug}
                if len(parts) == 4 and parts[1] == "punti-vendita":
                    store_urls.append(url)

        log.info("Total store URLs from sitemaps: %d", len(store_urls))
        return store_urls

    # ── Page parsing ─────────────────────────────────────────────────────────

    @staticmethod
    def _extract_store(html: str) -> dict | None:
        """Extract pageProps.store from __NEXT_DATA__ embedded in the page."""
        m = _NEXT_DATA_RE.search(html)
        if not m:
            return None
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
        pp = data.get("props", {}).get("pageProps", {})
        if pp.get("pageNotFound"):
            return None
        return pp.get("store") or None

    # ── DB upsert ─────────────────────────────────────────────────────────────

    async def _upsert_store(self, chain_id: int, s: dict) -> None:
        name = s.get("pdvName") or s.get("alias") or "?"
        external_id = s.get("identifier") or s.get("magnoliaCode")
        address = s.get("address")
        city = s.get("city")
        province = s.get("province")
        postal_code = s.get("postalCode")
        phone = s.get("phone") or None

        try:
            lat = float(s.get("latitude") or 0)
            lng = float(s.get("longitude") or 0)
        except (ValueError, TypeError):
            log.warning("Coordinate non valide per %s", name)
            return
        if not lat or not lng:
            log.warning("Coordinate mancanti per %s", name)
            return

        opening_hours = (
            json.dumps(s["openingTimes"]) if s.get("openingTimes") else None
        )
        has_delivery = bool(s.get("linkEcommerce"))
        has_click = bool(s.get("clickAndCollect"))

        if self.dry_run:
            log.info(
                "[DRY] %s | %s, %s | lat=%.5f lng=%.5f",
                name, city, province, lat, lng,
            )
            return

        existing = await self.conn.fetchval(
            "SELECT id FROM stores WHERE chain_id=$1 AND external_id=$2",
            chain_id,
            external_id,
        )
        if existing:
            await self.conn.execute(
                """UPDATE stores
                   SET name=$1, address=$2, city=$3, province=$4, postal_code=$5,
                       coordinates=ST_SetSRID(ST_MakePoint($6,$7),4326),
                       phone=$8, opening_hours=$9::jsonb,
                       has_delivery=$10, has_click_collect=$11, last_verified=NOW()
                   WHERE id=$12""",
                name, address, city, province, postal_code,
                lng, lat, phone, opening_hours, has_delivery, has_click,
                existing,
            )
        else:
            await self.conn.execute(
                """INSERT INTO stores
                   (chain_id, external_id, name, address, city, province, postal_code,
                    coordinates, phone, opening_hours, has_delivery, has_click_collect)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,
                           ST_SetSRID(ST_MakePoint($8,$9),4326),
                           $10,$11::jsonb,$12,$13)""",
                chain_id, external_id, name, address, city, province, postal_code,
                lng, lat, phone, opening_hours, has_delivery, has_click,
            )
        log.info("Upsert: %s (%s, %s)", name, city, province)

    # ── Entry points ─────────────────────────────────────────────────────────

    async def discover_stores(self) -> int:
        """Discover all Famila stores via sitemaps and upsert them. Returns count."""
        chain_id = await self.conn.fetchval(
            "SELECT id FROM chains WHERE slug=$1", CHAIN_SLUG
        )
        if not chain_id:
            log.error("Chain '%s' non trovata nel DB", CHAIN_SLUG)
            return 0

        urls = await self._collect_store_urls()
        upserted = 0
        for url in urls:
            html = await self._get(url)
            if not html:
                continue
            store = self._extract_store(html)
            if not store:
                log.debug("Nessun dato store a %s", url)
                continue
            await self._upsert_store(chain_id, store)
            upserted += 1

        log.info("=== Famila: %d/%d negozi upsert ===", upserted, len(urls))
        return upserted

    async def run(self) -> None:
        await self.discover_stores()
