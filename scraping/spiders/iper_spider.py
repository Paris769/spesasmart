"""
IperSpider — store discovery via SSR JSON-LD on individual store pages.

Flusso:
  1. Per ogni slug nei 22 negozi noti, GET iper.it/punti-vendita/{slug}/
     La pagina è SSR (Astro) e contiene <script type="application/ld+json">
     con @type=Store, geo.latitude/longitude, address, telephone.
  2. Estrae dati dal JSON-LD e fa upsert nel DB.

Nota: la piattaforma prezzi (cataloghi.iper.it) è CSR, prezzi non implementati in v1.
Il catalogo slug è "iper-{slug}" su cataloghi.iper.it/punti-vendita/iper-{slug}/promozioni/
"""
import asyncio
import json
import logging
import re

import asyncpg
import httpx

log = logging.getLogger("iper")

BASE_URL = "https://www.iper.it"
CHAIN_SLUG = "iper"
RATE = 2.0  # secondi tra richieste

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}

# Tutti e 22 i punti vendita Iper La grande i (slug iper.it → nome)
IPER_STORES: list[tuple[str, str]] = [
    ("monza-maestoso",           "Il Mercato del Maestoso by Iper"),
    ("arese",                    "Iper La grande i - Arese"),
    ("brembate",                 "Iper La grande i - Brembate"),
    ("busnago",                  "Iper La grande i - Busnago"),
    ("castelfranco-veneto",      "Iper La grande i - Castelfranco Veneto"),
    ("cremona",                  "Iper La grande i - Cremona"),
    ("grandate",                 "Iper La grande i - Grandate"),
    ("lonato",                   "Iper La grande i - Lonato del Garda"),
    ("magenta",                  "Iper La grande i - Magenta"),
    ("milano-portello",          "Iper La grande i - Milano Portello"),
    ("montebello-della-battaglia","Iper La grande i - Montebello della Battaglia"),
    ("monza",                    "Iper La grande i - Monza"),
    ("orio",                     "Iper La grande i - Orio"),
    ("rozzano",                  "Iper La grande i - Rozzano"),
    ("savignano-sul-rubicone",   "Iper La grande i - Savignano sul Rubicone"),
    ("seriate",                  "Iper La grande i - Seriate"),
    ("serravalle",               "Iper La grande i - Serravalle"),
    ("solbiate",                 "Iper La grande i - Solbiate"),
    ("tortona",                  "Iper La grande i - Tortona"),
    ("varese",                   "Iper La grande i - Varese"),
    ("verona",                   "Iper La grande i - Verona"),
    ("vittuone",                 "Iper La grande i - Vittuone"),
]

# Regex per estrarre JSON-LD Store dallo script SSR
_LD_JSON_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>',
    re.S | re.I,
)

# Regex per separare città e provincia da streetAddress
# es. "Via Giuseppe Luraghi, 11 Arese (MI)" → city="Arese", province="MI"
_ADDR_RE = re.compile(r'^(.*?)\s+([A-ZÀ-Ü][a-zà-ü\s]+)\s+\(([A-Z]{2})\)\s*$')


def _parse_address(street_address: str) -> tuple[str, str, str]:
    """
    Split 'Via Foo 1 CittaName (MI)' into (street, city, province).
    Returns ('', '', '') if parsing fails.
    """
    m = _ADDR_RE.match(street_address.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3)
    # Fallback: last token in parentheses is province
    paren = re.search(r'\(([A-Z]{2})\)', street_address)
    if paren:
        province = paren.group(1)
        rest = street_address[: paren.start()].strip()
        parts = rest.rsplit(" ", 1)
        city = parts[-1] if parts else ""
        street = parts[0] if len(parts) > 1 else rest
        return street, city, province
    return street_address, "", ""


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

    # ── JSON-LD parsing ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_store_ld(html: str) -> dict | None:
        """Find the @type=Store JSON-LD block in the SSR page HTML."""
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

    # ── DB upsert ─────────────────────────────────────────────────────────────

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
        external_id = slug
        opening_hours = (
            json.dumps(ld["openingHoursSpecification"])
            if ld.get("openingHoursSpecification")
            else None
        )

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
                chain_id, external_id, name, street, city, province,
                lng, lat, phone, opening_hours,
            )
        log.info("Upsert: %s (%s, %s)", name, city, province)

    # ── Entry points ─────────────────────────────────────────────────────────

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

    async def run(self) -> None:
        await self.discover_stores()
