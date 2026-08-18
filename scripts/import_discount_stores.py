"""
Importer punti vendita discount (Lidl / MD / Aldi / Penny / Eurospin).

Fonti store locator pubbliche (verificate il 2026-08-07):
  md        POST https://www.mdspa.it/punti_vendita_admin/get_pv.php (data pv=<id>)
            → JSON {pv: {id, indirizzo, citta, cap, latitudine, longitudine…}}.
            Non esiste un endpoint "lista completa": si scandiscono gli id
            numerici (default 1..--md-max-id). VALIDATO (scan completo 1..1000).
  eurospin  GET https://digitalflyer.eurospin.it/api/eurospin/eurospin-italia/stores
            (Bearer token via oauth client_credentials del viewer pubblico)
            → 1337 negozi con indirizzo, città, provincia, CAP e
            gpsCoordinates. VALIDATO.
  lidl      Sitemap https://www.lidl.it/s/it-IT/ricerca-negozio/sitemap.xml
            → ~813 pagine negozio; per ognuna il payload SSR Nuxt
            <url>_payload.json (formato devalue) contiene objectNumber,
            indirizzo e coordinate. ~814 richieste a 1 req/s ≈ 14 min.
            VALIDATO senza browser il 2026-08-07.
  aldi      La pagina https://www.aldi.it/punti-vendita-e-orari-di-apertura
            embedda il widget uberall con chiave pubblica
            (WEB_UBERALL_WIDGET_KEY nel config Nuxt); l'API
            locator.uberall.com/api/storefinders/<KEY>/locations/all
            restituisce tutti i ~199 negozi in una richiesta.
            VALIDATO senza browser il 2026-08-07.
  penny     GET https://www.penny.it/api/stores (endpoint same-origin della SPA
            Nuxt, nessuna auth) → ~446 negozi con storeId, città, via, CAP e
            position.lat/lng. Il campo "province" è in realtà la regione
            (es. "Lombardia") e viene ignorato. VALIDATO senza browser il
            2026-08-07.

Default: DRY-RUN — scrive scripts/out/discount_stores.json e non tocca il DB.
Con --apply esegue l'upsert in stores (stesso pattern di eurospin_spider).
⚠ --apply NON va eseguito in questa sessione POC.

Uso:
    python scripts/import_discount_stores.py --chain eurospin
    python scripts/import_discount_stores.py --chain md --md-max-id 20
    python scripts/import_discount_stores.py --chain all --apply   # (non ora)
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("import_stores")

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "scripts" / "out" / "discount_stores.json"
MD_STATE_PATH = REPO_ROOT / "scripts" / "out" / "md_scan_state.json"
RATE = 1.0
RETRIES = 3

# Bounding box Italia (validazione coordinate)
LAT_MIN, LAT_MAX = 35.0, 47.5
LNG_MIN, LNG_MAX = 6.0, 19.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9",
}

EUROSPIN_API = "https://digitalflyer.eurospin.it"
EUROSPIN_API_PATH = "api/eurospin/eurospin-italia"
# Client OAuth PUBBLICO del viewer smt-digitalflyer (embeddato nel JS servito
# a ogni visitatore di eurospin.it/volantino/).
EUROSPIN_CLIENT_CREDS = "850bdb5c-a86d-40b2-a8fb-a7bb61823a24:Interlacedit0"

MD_PV_URL = "https://www.mdspa.it/punti_vendita_admin/get_pv.php"

LIDL_SITEMAP_URL = "https://www.lidl.it/s/it-IT/ricerca-negozio/sitemap.xml"
LIDL_STATE_PATH = REPO_ROOT / "scripts" / "out" / "lidl_scan_state.json"

ALDI_LOCATOR_URL = "https://www.aldi.it/punti-vendita-e-orari-di-apertura"
# Chiave PUBBLICA del widget uberall, embeddata nel config Nuxt servito a ogni
# visitatore della pagina (WEB_UBERALL_WIDGET_KEY). Fallback se il regex sulla
# pagina non trova più la chiave (verificata il 2026-08-07).
ALDI_UBERALL_KEY_FALLBACK = "J8f9erNQcUhg1nmo5Bhp8wy2A6mQkK"
UBERALL_LOCATIONS_URL = (
    "https://locator.uberall.com/api/storefinders/{key}/locations/all"
)

PENNY_STORES_URL = "https://www.penny.it/api/stores"


class Importer:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._t_last = 0.0

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._t_last
        if elapsed < RATE:
            await asyncio.sleep(RATE - elapsed)
        self._t_last = loop.time()

    async def _request(self, method: str, url: str, **kw) -> httpx.Response | None:
        """Richiesta throttled con retry/backoff su errori di rete e 5xx."""
        for attempt in range(1, RETRIES + 1):
            await self._throttle()
            try:
                r = await self.client.request(method, url, **kw)
            except httpx.RequestError as exc:
                if attempt == RETRIES:
                    log.warning("%s %s: errore dopo %d tentativi: %s",
                                method, url, RETRIES, exc)
                    return None
                await asyncio.sleep(2 * attempt)
                continue
            if r.status_code >= 500 and attempt < RETRIES:
                log.warning("%s %s: HTTP %d, ritento (%d/%d)",
                            method, url, r.status_code, attempt, RETRIES)
                await asyncio.sleep(2 * attempt)
                continue
            return r
        return None

    # ── Checkpoint per gli scan lunghi (resume da interruzione) ──────────────

    @staticmethod
    def _load_state(path: Path) -> dict:
        """Checkpoint scan: {chiave_scandita: store|None} per resume."""
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("checkpoint %s illeggibile (%s), riparto da zero",
                            path.name, exc)
        return {}

    @staticmethod
    def _save_state(path: Path, state: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    # ── MD: scan degli id get_pv.php ─────────────────────────────────────────

    async def fetch_md(self, max_id: int) -> list[dict]:
        state = self._load_state(MD_STATE_PATH)
        if state:
            log.info("md: resume da checkpoint (%d id già scanditi)", len(state))
        new_since_flush = 0
        for pv_id in range(1, max_id + 1):
            key = str(pv_id)
            if key in state:
                continue
            store: dict | None = None
            r = await self._request(
                "POST", MD_PV_URL, data={"pv": key},
                headers=HEADERS, timeout=30, follow_redirects=True,
            )
            if r is None:
                continue  # errore rete persistente: non checkpointo, riproverò
            if r.status_code == 200 and r.text.strip().startswith("{"):
                info = (r.json() or {}).get("pv") or {}
                lat, lng = info.get("latitudine"), info.get("longitudine")
                if info.get("id") and lat and lng:
                    store = {
                        "chain_slug": "md",
                        "external_id": f"md-pv-{info['id']}",
                        "name": f"MD {(info.get('citta') or '').title()}".strip(),
                        "address": (info.get("indirizzo") or "").title() or None,
                        "city": (info.get("citta") or "").title() or None,
                        "province": info.get("provincia") or None,
                        "postal_code": info.get("cap") or None,
                        "lat": float(lat),
                        "lng": float(lng),
                    }
            state[key] = store
            new_since_flush += 1
            if new_since_flush >= 25:
                self._save_state(MD_STATE_PATH, state)
                new_since_flush = 0
                log.info("md: checkpoint a pv=%d (%d negozi finora)",
                         pv_id, sum(1 for v in state.values() if v))
        if new_since_flush:
            self._save_state(MD_STATE_PATH, state)
        stores = [
            state[str(i)] for i in range(1, max_id + 1)
            if state.get(str(i))
        ]
        log.info("md: %d negozi trovati su %d id scanditi (%d vuoti)",
                 len(stores), max_id, max_id - len(stores))
        return stores

    # ── Eurospin: API digitalflyer ───────────────────────────────────────────

    async def fetch_eurospin(self) -> list[dict]:
        await self._throttle()
        basic = base64.b64encode(EUROSPIN_CLIENT_CREDS.encode()).decode()
        try:
            r = await self.client.post(
                f"{EUROSPIN_API}/oauth/token",
                headers={**HEADERS,
                         "Authorization": f"Basic {basic}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                content="grant_type=client_credentials&scope=read write",
                timeout=30,
            )
        except httpx.RequestError as exc:
            log.error("eurospin: oauth/token errore: %s", exc)
            return []
        if r.status_code != 200:
            log.error("eurospin: oauth/token HTTP %s", r.status_code)
            return []
        token = r.json().get("access_token")

        await self._throttle()
        try:
            r = await self.client.get(
                f"{EUROSPIN_API}/{EUROSPIN_API_PATH}/stores",
                headers={**HEADERS,
                         "Authorization": f"Bearer {token}",
                         "Accept": "application/json"},
                timeout=60,
            )
        except httpx.RequestError as exc:
            log.error("eurospin: /stores errore: %s", exc)
            return []
        if r.status_code != 200:
            log.error("eurospin: /stores HTTP %s", r.status_code)
            return []

        stores: list[dict] = []
        for s in r.json() or []:
            gps = s.get("gpsCoordinates") or {}
            lat, lng = gps.get("latitude"), gps.get("longitude")
            alias = s.get("alias") or s.get("code")
            if not alias or not lat or not lng:
                continue
            prov = s.get("province") or {}
            stores.append({
                "chain_slug": "eurospin",
                "external_id": alias,
                "name": f"Eurospin {s.get('name') or ''}".strip(),
                "address": s.get("address"),
                "city": s.get("city") or s.get("name"),
                "province": prov.get("code"),
                "postal_code": s.get("postalCode"),
                "lat": float(lat),
                "lng": float(lng),
            })
        log.info("eurospin: %d negozi con coordinate", len(stores))
        return stores

    # ── Lidl: sitemap ricerca-negozio + payload SSR Nuxt per pagina ──────────

    @staticmethod
    def _parse_lidl_payload(data: list) -> dict | None:
        """Estrae il negozio dal payload devalue di Nuxt.

        Il payload è un array di nodi; i valori int sono indici nell'array.
        Il nodo del negozio principale ha objectNumber+storeName+address
        (i nearbyStores hanno objectNumber ma NON storeName).
        """
        if not isinstance(data, list):
            return None

        def res(v):
            return data[v] if isinstance(v, int) and 0 <= v < len(data) else v

        for node in data:
            if not (isinstance(node, dict) and "objectNumber" in node
                    and "storeName" in node and "address" in node):
                continue
            addr = res(node["address"])
            if not isinstance(addr, dict):
                return None
            g = {k: res(v) for k, v in addr.items()}
            obj_num = res(node["objectNumber"])
            lat, lng = g.get("latitude"), g.get("longitude")
            if not obj_num or not isinstance(lat, (int, float)) \
                    or not isinstance(lng, (int, float)):
                return None
            store_name = res(node["storeName"]) or ""
            prov = re.search(r"\(([A-Z]{2})\)", str(store_name))
            street = " ".join(
                str(x) for x in (g.get("streetName"), g.get("streetNumber")) if x
            ) or None
            city = g.get("city") or None
            return Importer._lidl_normalize({
                "chain_slug": "lidl",
                "external_id": str(obj_num),
                "name": f"Lidl {city or store_name}".strip(),
                "address": street,
                "city": city,
                "province": prov.group(1) if prov else None,
                "postal_code": g.get("zip") or None,
                "lat": float(lat),
                "lng": float(lng),
            })
        return None

    @staticmethod
    def _lidl_normalize(s: dict) -> dict:
        """Il campo city di Lidl a volte include la provincia: "Adria (RO)".

        Idempotente: applicata sia al parse sia ai dati già in checkpoint.
        """
        m = re.match(r"^(.*?)\s*\(([A-Z]{2})\)$", s.get("city") or "")
        if m:
            s = {**s, "city": m.group(1),
                 "province": s.get("province") or m.group(2),
                 "name": f"Lidl {m.group(1)}"}
        return s

    async def fetch_lidl(self) -> list[dict]:
        r = await self._request("GET", LIDL_SITEMAP_URL,
                                headers=HEADERS, timeout=30)
        if r is None or r.status_code != 200:
            log.error("lidl: sitemap non raggiungibile (HTTP %s)",
                      r.status_code if r else "n/d")
            return []
        urls = re.findall(r"<loc>([^<]+)</loc>", r.text)
        # Le pagine negozio sono /s/it-IT/ricerca-negozio/<città>/<via>/
        # (7 slash senza trailing); le pagine regione/città ne hanno meno.
        store_urls = sorted(
            u.rstrip("/") for u in urls
            if "/ricerca-negozio/" in u and u.rstrip("/").count("/") == 7
        )
        log.info("lidl: %d pagine negozio in sitemap (~%d min a 1 req/s)",
                 len(store_urls), len(store_urls) // 60 + 1)

        state = self._load_state(LIDL_STATE_PATH)
        if state:
            log.info("lidl: resume da checkpoint (%d pagine già scandite)",
                     len(state))
        new_since_flush = 0
        for url in store_urls:
            if url in state:
                continue
            store: dict | None = None
            r = await self._request("GET", f"{url}/_payload.json",
                                    headers=HEADERS, timeout=30,
                                    follow_redirects=True)
            if r is None:
                continue  # errore rete persistente: riproverò al prossimo run
            if r.status_code == 200:
                try:
                    store = self._parse_lidl_payload(r.json())
                except json.JSONDecodeError:
                    store = None
                if store is None:
                    log.warning("lidl: payload non parsabile per %s", url)
            state[url] = store
            new_since_flush += 1
            if new_since_flush >= 25:
                self._save_state(LIDL_STATE_PATH, state)
                new_since_flush = 0
                log.info("lidl: checkpoint (%d/%d pagine, %d negozi)",
                         len(state), len(store_urls),
                         sum(1 for v in state.values() if v))
        if new_since_flush:
            self._save_state(LIDL_STATE_PATH, state)

        stores = [self._lidl_normalize(state[u])
                  for u in store_urls if state.get(u)]
        log.info("lidl: %d negozi da %d pagine", len(stores), len(store_urls))
        return stores

    # ── Aldi: API uberall con chiave pubblica del widget ─────────────────────

    async def fetch_aldi(self) -> list[dict]:
        key = ALDI_UBERALL_KEY_FALLBACK
        r = await self._request("GET", ALDI_LOCATOR_URL, headers=HEADERS,
                                timeout=30, follow_redirects=True)
        if r is not None and r.status_code == 200:
            m = re.search(r'WEB_UBERALL_WIDGET_KEY:"([^"]+)"', r.text)
            if m:
                key = m.group(1)
            else:
                log.warning("aldi: WEB_UBERALL_WIDGET_KEY non trovata nella "
                            "pagina, uso il fallback")
        r = await self._request(
            "GET", UBERALL_LOCATIONS_URL.format(key=key),
            params={
                "v": "20230110",
                "language": "it",
                "fieldMask": ["id", "identifier", "name", "streetAndNumber",
                              "zip", "city", "province", "lat", "lng"],
            },
            headers={**HEADERS, "Accept": "application/json"}, timeout=60,
        )
        if r is None or r.status_code != 200:
            log.error("aldi: uberall locations HTTP %s",
                      r.status_code if r else "n/d")
            return []
        payload = r.json() or {}
        if payload.get("status") != "SUCCESS":
            log.error("aldi: uberall status=%s", payload.get("status"))
            return []
        stores: list[dict] = []
        for loc in (payload.get("response") or {}).get("locations") or []:
            lat, lng = loc.get("lat"), loc.get("lng")
            ext = loc.get("identifier") or loc.get("id")
            if not ext or lat is None or lng is None:
                continue
            city = loc.get("city")
            stores.append({
                "chain_slug": "aldi",
                "external_id": str(ext),
                "name": loc.get("name") or f"ALDI {city or ''}".strip(),
                "address": loc.get("streetAndNumber"),
                "city": city,
                "province": loc.get("province"),
                "postal_code": loc.get("zip"),
                "lat": float(lat),
                "lng": float(lng),
            })
        log.info("aldi: %d negozi dall'API uberall", len(stores))
        return stores

    # ── Penny: endpoint /api/stores della SPA ────────────────────────────────

    async def fetch_penny(self) -> list[dict]:
        r = await self._request(
            "GET", PENNY_STORES_URL,
            headers={**HEADERS, "Accept": "application/json"}, timeout=60,
        )
        if r is None or r.status_code != 200:
            log.error("penny: /api/stores HTTP %s",
                      r.status_code if r else "n/d")
            return []
        try:
            data = r.json()
        except json.JSONDecodeError:
            log.error("penny: /api/stores non è JSON")
            return []
        stores: list[dict] = []
        for s in data or []:
            pos = s.get("position") or {}
            lat, lng = pos.get("lat"), pos.get("lng")
            ext = s.get("storeId")
            if not ext or lat is None or lng is None:
                continue
            city = s.get("city") or None
            stores.append({
                "chain_slug": "penny",
                "external_id": str(ext),
                "name": f"Penny {city or ''}".strip(),
                "address": s.get("street") or None,
                "city": city,
                # NB: il campo "province" dell'API è la regione → lo ignoriamo
                "province": None,
                "postal_code": s.get("zip") or None,
                "lat": float(lat),
                "lng": float(lng),
            })
        log.info("penny: %d negozi da /api/stores", len(stores))
        return stores


# ── Upsert DB (solo con --apply) ─────────────────────────────────────────────

def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        local = REPO_ROOT / ".db_url.local"
        if local.exists():
            url = local.read_text(encoding="utf-8").strip()
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def apply_stores(stores: list[dict]) -> None:
    import asyncpg  # import locale: serve solo con --apply

    url = _db_url()
    if not url:
        sys.exit("Nessuna DATABASE_URL e nessun .db_url.local")
    conn = await asyncpg.connect(url)
    try:
        upserted = 0
        for s in stores:
            chain_id = await conn.fetchval(
                "SELECT id FROM chains WHERE slug = $1", s["chain_slug"]
            )
            if not chain_id:
                log.error("chain '%s' non trovata nel DB — skip", s["chain_slug"])
                continue
            existing = await conn.fetchval(
                "SELECT id FROM stores WHERE chain_id = $1 AND external_id = $2",
                chain_id, s["external_id"],
            )
            if existing:
                await conn.execute(
                    """
                    UPDATE stores
                    SET name = $2, address = $3, city = $4, province = $5,
                        postal_code = $6,
                        coordinates = ST_SetSRID(ST_MakePoint($7, $8), 4326),
                        is_active = TRUE
                    WHERE id = $1
                    """,
                    existing, s["name"], s["address"], s["city"],
                    s["province"], s["postal_code"], s["lng"], s["lat"],
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO stores
                        (chain_id, external_id, name, address, city, province,
                         postal_code, coordinates, is_active)
                    VALUES ($1, $2, $3, $4, $5, $6, $7,
                            ST_SetSRID(ST_MakePoint($8, $9), 4326), TRUE)
                    """,
                    chain_id, s["external_id"], s["name"], s["address"],
                    s["city"], s["province"], s["postal_code"],
                    s["lng"], s["lat"],
                )
            upserted += 1
        log.info("=== Upsert completato: %d negozi ===", upserted)
    finally:
        await conn.close()


CHAINS = ("md", "eurospin", "lidl", "aldi", "penny")


def validate_and_dedup(stores: list[dict]) -> tuple[list[dict], dict]:
    """Scarta coordinate fuori Italia e duplicati per (chain, external_id)."""
    ok: list[dict] = []
    seen: set[tuple[str, str]] = set()
    out_of_bbox = 0
    duplicates = 0
    for s in stores:
        if not (LAT_MIN <= s["lat"] <= LAT_MAX and LNG_MIN <= s["lng"] <= LNG_MAX):
            out_of_bbox += 1
            log.warning("scartato (fuori bbox Italia): %s %s lat=%s lng=%s",
                        s["chain_slug"], s["external_id"], s["lat"], s["lng"])
            continue
        key = (s["chain_slug"], s["external_id"])
        if key in seen:
            duplicates += 1
            log.warning("scartato (duplicato): %s %s", *key)
            continue
        seen.add(key)
        ok.append(s)
    return ok, {"out_of_bbox": out_of_bbox, "duplicates": duplicates}


async def main(args: argparse.Namespace) -> None:
    chains = CHAINS if args.chain == "all" else (args.chain,)
    stores: list[dict] = []
    async with httpx.AsyncClient() as client:
        imp = Importer(client)
        for chain in chains:
            log.info("=== store locator %s ===", chain)
            if chain == "md":
                stores.extend(await imp.fetch_md(args.md_max_id))
            elif chain == "eurospin":
                stores.extend(await imp.fetch_eurospin())
            else:
                stores.extend(await getattr(imp, f"fetch_{chain}")())

    stores, rejected = validate_and_dedup(stores)
    if rejected["out_of_bbox"] or rejected["duplicates"]:
        log.info("validazione: %d fuori bbox, %d duplicati scartati",
                 rejected["out_of_bbox"], rejected["duplicates"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "chains": list(chains),
            "count": len(stores),
            "rejected": rejected,
            "stores": stores,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Dry-run JSON: %d negozi → %s", len(stores), OUT_PATH)

    if args.apply:
        log.info("--apply richiesto: upsert in stores…")
        await apply_stores(stores)
    else:
        log.info("=== DRY-RUN — nessuna scrittura nel DB. Per applicare: --apply ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Importa i punti vendita discount da store locator pubblici"
    )
    parser.add_argument("--chain", choices=[*CHAINS, "all"], default="all")
    parser.add_argument("--md-max-id", type=int, default=30,
                        help="Ultimo id pv MD da scandire (default 30; per il "
                             "run completo usare 1000 — ~17 min a 1 req/s, "
                             "con resume automatico da md_scan_state.json)")
    parser.add_argument("--apply", action="store_true",
                        help="Upsert in stores (default: solo JSON dry-run)")
    asyncio.run(main(parser.parse_args()))
