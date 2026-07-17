"""
Importer punti vendita discount (Lidl / MD / Aldi / Penny / Eurospin).

Fonti store locator pubbliche (verificate il 2026-07-04):
  md        POST https://www.mdspa.it/punti_vendita_admin/get_pv.php (data pv=<id>)
            → JSON {pv: {id, indirizzo, citta, cap, latitudine, longitudine…}}.
            Non esiste un endpoint "lista completa": si scandiscono gli id
            numerici (default 1..--md-max-id). VALIDATO (pv=1 e pv=5 reali).
  eurospin  GET https://digitalflyer.eurospin.it/api/eurospin/eurospin-italia/stores
            (Bearer token via oauth client_credentials del viewer pubblico)
            → 1337 negozi con indirizzo, città, provincia, CAP e
            gpsCoordinates. VALIDATO.
  lidl      BLOCCATO senza browser: lo store finder (storesearch-frontend)
            chiama "/api/stores/" da un chunk JS con parametri firmati non
            ricostruibili staticamente; il vecchio dataset Bing
            spatial.virtualearth.net/…/Filialdaten-IT risponde 401
            (chiave ruotata). Serve ispezione rete da browser reale.
  aldi      BLOCCATO: nessun endpoint JSON individuato staticamente sulle
            pagine aldi.it (store finder caricato client-side).
  penny     BLOCCATO: penny.it è una SPA Nuxt; l'API dello store finder non è
            visibile nell'HTML servito. Serve ispezione rete da browser reale.

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
RATE = 1.0

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

    # ── MD: scan degli id get_pv.php ─────────────────────────────────────────

    async def fetch_md(self, max_id: int) -> list[dict]:
        stores: list[dict] = []
        misses = 0
        for pv_id in range(1, max_id + 1):
            await self._throttle()
            try:
                r = await self.client.post(
                    MD_PV_URL, data={"pv": str(pv_id)},
                    headers=HEADERS, timeout=30, follow_redirects=True,
                )
            except httpx.RequestError as exc:
                log.warning("md pv=%d errore: %s", pv_id, exc)
                continue
            if r.status_code != 200 or not r.text.strip().startswith("{"):
                misses += 1
                continue
            info = (r.json() or {}).get("pv") or {}
            lat, lng = info.get("latitudine"), info.get("longitudine")
            if not info.get("id") or not lat or not lng:
                misses += 1
                continue
            stores.append({
                "chain_slug": "md",
                "external_id": f"md-pv-{info['id']}",
                "name": f"MD {(info.get('citta') or '').title()}".strip(),
                "address": (info.get("indirizzo") or "").title() or None,
                "city": (info.get("citta") or "").title() or None,
                "province": info.get("provincia") or None,
                "postal_code": info.get("cap") or None,
                "lat": float(lat),
                "lng": float(lng),
            })
        log.info("md: %d negozi trovati su %d id scanditi (%d vuoti)",
                 len(stores), max_id, misses)
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

    # ── Catene bloccate senza browser ────────────────────────────────────────

    async def fetch_lidl(self) -> list[dict]:
        log.warning(
            "lidl: store locator BLOCCATO senza browser — lo storesearch "
            "frontend chiama /api/stores/ con parametri generati dal JS; il "
            "vecchio dataset Bing Filialdaten-IT risponde 401 (chiave "
            "ruotata). TODO: catturare la chiamata reale con Chrome DevTools."
        )
        return []

    async def fetch_aldi(self) -> list[dict]:
        log.warning("aldi: nessun endpoint JSON store locator individuato "
                    "staticamente — serve ispezione rete da browser.")
        return []

    async def fetch_penny(self) -> list[dict]:
        log.warning("penny: SPA Nuxt, API store finder non visibile nell'HTML "
                    "— serve ispezione rete da browser.")
        return []


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

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "chains": list(chains),
            "count": len(stores),
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
                        help="Ultimo id pv MD da scandire (default 30; il "
                             "run completo richiede ~900 id a 1 req/s)")
    parser.add_argument("--apply", action="store_true",
                        help="Upsert in stores (default: solo JSON dry-run)")
    asyncio.run(main(parser.parse_args()))
