"""
SpesaSmart Guardian — agente autonomo di sorveglianza e auto-riparazione.

Incorpora, automatizzandole, le diagnosi e i rimedi che finora richiedevano
intervento manuale. Gira a intervalli (workflow guardian.yml) e:

  CONTROLLA
    • liveness endpoint  — sonda le API dei cataloghi (Esselunga route,
      CosìComodo search-by-category, Carrefour grid). Intercetta in anticipo
      i cambi-API che zittiscono uno spider (es. Esselunga facet→HTTP 204).
    • salute DB          — raggiungibilità, stato sola-lettura (disco pieno),
      dimensione vicina al limite del piano.
    • freschezza dati    — ultimo scraped_at per catena; segnala i cataloghi fermi.
    • copertura          — % prodotti con ≥2 negozi (la metrica "1 negozio"),
      conteggi prodotti/prezzi.

  AUTO-RIPARA  (con --heal)
    • DB vicino al limite o in sola-lettura → prune dello storico prezzi.
    • catena ferma e endpoint vivo          → ri-scrape mirato (con --heal all).
    • dopo qualsiasi scrape                  → dedup.

  ALLERTA
    • annotazioni GitHub Actions (::error:: / ::warning::) + job summary.
    • exit code 1 SOLO per problemi che richiedono l'uomo (API cambiata =
      serve fix al codice; DB non scrivibile non risolvibile col prune).

Uso:
    python -m scraping.guardian                 # check + heal (prune, dedup)
    python -m scraping.guardian --heal all      # include ri-scrape catene ferme
    python -m scraping.guardian --check-only     # solo diagnosi, nessuna azione
    python -m scraping.guardian --probe-only     # solo liveness endpoint (no DB)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import asyncpg
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("guardian")

DB_URL = (
    os.getenv("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql://")
)

# Soglie configurabili via env (limiti del piano DB e freschezza attesa).
DB_LIMIT_MB = int(os.getenv("GUARDIAN_DB_LIMIT_MB", "500"))      # free tier ~500MB
DB_WARN_PCT = int(os.getenv("GUARDIAN_DB_WARN_PCT", "80"))        # warn oltre l'80%
COVERAGE_MIN_PCT = int(os.getenv("GUARDIAN_COVERAGE_MIN_PCT", "40"))

# Freschezza massima attesa per catena (ore). I cataloghi online si rinfrescano
# ogni notte; CosìComodo ruota i 101 negozi su più giorni → soglia più ampia.
FRESHNESS_HOURS = {
    "esselunga": 36, "carrefour": 36, "conad": 72,
    "eurospin": 72, "iper": 72,
    "famila": 24 * 8, "ilgigante": 24 * 8, "italmark": 24 * 8,
}
DEFAULT_FRESHNESS_H = 72

HDRS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9",
}


# ──────────────────────────────────────────────────────────────────────────
# Liveness endpoint — il cuore del rilevamento precoce dei guasti API
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class Probe:
    name: str
    method: str
    url: str
    headers: dict
    validator: Callable[[object], bool]
    params: Optional[dict] = None
    is_json: bool = True
    note: str = ""


def _esselunga_ok(j) -> bool:
    if not isinstance(j, dict):
        return False
    items = j.get("leftMenuItems") or []
    sets = sum(len(it.get("menuItemProductSets") or []) for it in items)
    return len(items) > 10 and sets > 50


def _cosicomodo_ok(j) -> bool:
    if not isinstance(j, dict):
        return False
    if j.get("products"):
        return True
    pag = j.get("pagination") or {}
    return bool(pag.get("totalResults") or pag.get("totalPages"))


PROBES: list[Probe] = [
    Probe(
        name="esselunga",
        method="GET",
        url="https://spesaonline.esselunga.it/commerce/resources/route/v1/supermercato",
        headers={**HDRS, "Accept": "application/json", "X-PAGE-PATH": "supermercato"},
        validator=_esselunga_ok,
        note="route/v1 → leftMenuItems (productSetId per il catalogo)",
    ),
    Probe(
        name="cosicomodo",
        method="GET",
        url=("https://api.cosicomodo.it/occ/v2/ilgigante/stores/bresso"
             "/users/anonymous/products/search-by-category"),
        headers={**HDRS, "Accept": "application/json",
                 "Origin": "https://www.cosicomodo.it",
                 "Referer": "https://www.cosicomodo.it/"},
        params={"facet": ":relevance", "currentPage": 0, "pageSize": 5,
                "fields": "FULL", "categoryCode": "10001"},
        validator=_cosicomodo_ok,
        note="OCC search-by-category anonima",
    ),
    Probe(
        name="carrefour",
        method="GET",
        url="https://www.carrefour.it/spesa-online/frutta-e-verdura/",
        headers={**HDRS, "Accept": "text/html,application/xhtml+xml"},
        validator=lambda html: isinstance(html, str) and "data-option-cgid" in html,
        is_json=False,
        note="griglia categoria → data-option-cgid",
    ),
]


@dataclass
class ProbeResult:
    name: str
    alive: bool
    status: Optional[int]
    detail: str


async def run_probes(client: httpx.AsyncClient) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    for p in PROBES:
        status: Optional[int] = None
        alive = False
        detail = ""
        for attempt in range(3):
            try:
                r = await client.request(
                    p.method, p.url, headers=p.headers, params=p.params,
                    timeout=30, follow_redirects=True,
                )
                status = r.status_code
                if r.status_code == 200:
                    payload = r.json() if p.is_json else r.text
                    alive = bool(p.validator(payload))
                    detail = "ok" if alive else "200 ma struttura inattesa (possibile cambio API)"
                else:
                    detail = f"HTTP {r.status_code}"
                break
            except Exception as exc:  # noqa: BLE001
                detail = f"errore: {type(exc).__name__}: {str(exc)[:80]}"
            await asyncio.sleep(2 ** attempt)
        lvl = log.info if alive else log.error
        lvl("PROBE %-11s %s (%s)", p.name, "VIVO" if alive else "GIÙ", detail)
        results.append(ProbeResult(p.name, alive, status, detail))
    return results


# ──────────────────────────────────────────────────────────────────────────
# Salute DB / freschezza / copertura
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class DbHealth:
    reachable: bool = False
    writable: bool = False
    size_mb: float = 0.0
    size_pct: float = 0.0
    products: int = 0
    prices: int = 0
    prices_current: int = 0
    coverage_pct: float = 0.0
    stale_chains: list[str] = field(default_factory=list)
    chain_age_h: dict = field(default_factory=dict)


async def check_db(conn: asyncpg.Connection) -> DbHealth:
    h = DbHealth(reachable=True)

    # sola-lettura? (disco pieno / default_transaction_read_only)
    try:
        async with conn.transaction():
            await conn.execute("CREATE TEMP TABLE _guardian_probe(x int) ON COMMIT DROP")
            await conn.execute("INSERT INTO _guardian_probe VALUES (1)")
        h.writable = True
    except asyncpg.exceptions.ReadOnlySQLTransactionError:
        h.writable = False
        log.error("DB in SOLA LETTURA (disco pieno o read-only) — scritture bloccate")
    except Exception as exc:  # noqa: BLE001
        h.writable = False
        log.error("DB scrittura non verificabile: %s", exc)

    size = await conn.fetchval("SELECT pg_database_size(current_database())")
    h.size_mb = round(size / 1_048_576, 1)
    h.size_pct = round(h.size_mb / DB_LIMIT_MB * 100, 1) if DB_LIMIT_MB else 0.0

    h.products = await conn.fetchval("SELECT count(*) FROM products") or 0
    h.prices = await conn.fetchval("SELECT count(*) FROM prices") or 0
    h.prices_current = await conn.fetchval(
        "SELECT count(*) FROM prices WHERE is_current = TRUE"
    ) or 0

    # Copertura: tra i prodotti con almeno un prezzo corrente, quota con ≥2 negozi.
    cov = await conn.fetchrow(
        """
        WITH per_prod AS (
            SELECT product_id, COUNT(DISTINCT store_id) AS n
            FROM prices WHERE is_current = TRUE
            GROUP BY product_id
        )
        SELECT COUNT(*) AS tot,
               COUNT(*) FILTER (WHERE n >= 2) AS multi
        FROM per_prod
        """
    )
    if cov and cov["tot"]:
        h.coverage_pct = round(cov["multi"] / cov["tot"] * 100, 1)

    # Freschezza per catena (sui prezzi correnti).
    rows = await conn.fetch(
        """
        SELECT c.slug,
               EXTRACT(EPOCH FROM (NOW() - MAX(p.scraped_at))) / 3600 AS age_h
        FROM prices p
        JOIN stores s ON p.store_id = s.id
        JOIN chains c ON s.chain_id = c.id
        WHERE p.is_current = TRUE
        GROUP BY c.slug
        """
    )
    for r in rows:
        slug = r["slug"]
        age = round(float(r["age_h"]), 1) if r["age_h"] is not None else None
        h.chain_age_h[slug] = age
        limit = FRESHNESS_HOURS.get(slug, DEFAULT_FRESHNESS_H)
        if age is not None and age > limit:
            h.stale_chains.append(slug)

    log.info(
        "DB: %.1f MB (%.0f%% di %d MB) | prodotti=%d prezzi=%d (correnti=%d) | "
        "copertura 2+ negozi=%.1f%%",
        h.size_mb, h.size_pct, DB_LIMIT_MB, h.products, h.prices,
        h.prices_current, h.coverage_pct,
    )
    if h.stale_chains:
        log.warning("Catene ferme (oltre soglia freschezza): %s",
                    ", ".join(h.stale_chains))
    return h


# ──────────────────────────────────────────────────────────────────────────
# Auto-riparazione
# ──────────────────────────────────────────────────────────────────────────

async def heal(
    conn: asyncpg.Connection,
    health: DbHealth,
    probes: list[ProbeResult],
    rescrape: bool,
) -> list[str]:
    """Esegue i rimedi automatici. Ritorna l'elenco delle azioni svolte."""
    from .prune import prune_prices
    from .dedup_products import dedup

    actions: list[str] = []
    alive = {p.name for p in probes if p.alive}
    scraped_any = False

    # 1. Spazio DB: prune se vicino al limite (e DB scrivibile).
    if health.writable and health.size_pct >= DB_WARN_PCT:
        log.info("Spazio DB %.0f%% ≥ %d%% → prune storico", health.size_pct, DB_WARN_PCT)
        deleted = await prune_prices(conn)
        actions.append(f"prune: {deleted} righe storiche eliminate")

    # 2. Ri-scrape catene ferme (solo con --heal all, endpoint vivo, DB scrivibile).
    if rescrape and health.writable and health.stale_chains:
        # mappa catena→endpoint sondato (più catene condividono CosìComodo)
        cosic = {"famila", "ilgigante", "italmark"}
        for slug in health.stale_chains:
            probe_name = "cosicomodo" if slug in cosic else slug
            if probe_name not in alive:
                log.warning("Catena %s ferma ma endpoint '%s' non vivo → salto "
                            "(serve fix codice)", slug, probe_name)
                continue
            try:
                n = await _rescrape_chain(conn, slug)
                actions.append(f"rescrape {slug}: {n} prezzi")
                scraped_any = scraped_any or n > 0
            except Exception as exc:  # noqa: BLE001
                log.error("Rescrape %s fallito: %s", slug, exc)

    # 3. Dedup dopo qualsiasi scrape (o se ci sono catene appena rinfrescate).
    if scraped_any:
        log.info("Scrape eseguiti → dedup")
        merged = await dedup(conn, apply=True)
        actions.append(f"dedup: {merged} gruppi uniti")

    return actions


async def _rescrape_chain(conn: asyncpg.Connection, slug: str) -> int:
    """Ri-scrape mirato di una catena ferma riusando gli spider del runner."""
    from .spiders.esselunga_spider import EsselungaSpider
    from .spiders.carrefour_spider import CarrefourSpider
    from .spiders.cosicomodo_spider import CosiComodoSpider

    async with httpx.AsyncClient() as client:
        if slug == "esselunga":
            return await EsselungaSpider(client, conn).run()
        if slug == "carrefour":
            return await CarrefourSpider(client, conn).run()
        if slug in ("famila", "ilgigante", "italmark"):
            return await CosiComodoSpider(client, conn).scrape_prices()
    log.info("Rescrape non gestito per catena %s — salto", slug)
    return 0


# ──────────────────────────────────────────────────────────────────────────
# Report + alert
# ──────────────────────────────────────────────────────────────────────────

def emit_report(
    probes: list[ProbeResult],
    health: Optional[DbHealth],
    actions: list[str],
) -> int:
    """Stampa annotazioni GitHub + job summary. Ritorna l'exit code."""
    dead = [p for p in probes if not p.alive]
    critical: list[str] = []
    warnings: list[str] = []

    for p in dead:
        critical.append(f"API '{p.name}' GIÙ ({p.detail}) — probabile cambio "
                        f"endpoint: serve fix allo spider {p.name}")
    if health:
        if not health.writable:
            critical.append("DB in sola-lettura (disco pieno?) — scritture bloccate")
        if health.size_pct >= 95:
            warnings.append(f"DB al {health.size_pct:.0f}% del limite")
        if health.coverage_pct and health.coverage_pct < COVERAGE_MIN_PCT:
            warnings.append(f"copertura 2+ negozi bassa: {health.coverage_pct:.1f}%")
        if health.stale_chains:
            warnings.append("catene ferme: " + ", ".join(health.stale_chains))

    for c in critical:
        print(f"::error::[guardian] {c}")
    for w in warnings:
        print(f"::warning::[guardian] {w}")

    # Job summary (se in GitHub Actions)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        lines = ["# 🛡️ SpesaSmart Guardian", ""]
        lines.append("## Endpoint")
        for p in probes:
            lines.append(f"- {'✅' if p.alive else '❌'} **{p.name}** — {p.detail}")
        if health:
            lines += [
                "", "## Database",
                f"- Dimensione: **{health.size_mb} MB** ({health.size_pct:.0f}% di {DB_LIMIT_MB} MB)",
                f"- Scrivibile: {'✅' if health.writable else '❌'}",
                f"- Prodotti: {health.products:,} · Prezzi correnti: {health.prices_current:,}",
                f"- Copertura 2+ negozi: **{health.coverage_pct:.1f}%**",
            ]
            if health.chain_age_h:
                lines.append("- Freschezza per catena (ore):")
                for slug, age in sorted(health.chain_age_h.items()):
                    flag = " ⚠️" if slug in health.stale_chains else ""
                    lines.append(f"  - {slug}: {age}h{flag}")
        if actions:
            lines += ["", "## Azioni di auto-riparazione"]
            lines += [f"- {a}" for a in actions]
        lines += ["", "## Esito"]
        lines.append("🔴 Richiede intervento" if critical else
                     ("🟡 Avvisi" if warnings else "🟢 Tutto ok"))
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError:
            pass

    return 1 if critical else 0


# ──────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> int:
    probes: list[ProbeResult] = []
    health: Optional[DbHealth] = None
    actions: list[str] = []

    async with httpx.AsyncClient() as client:
        probes = await run_probes(client)

    if args.probe_only:
        return emit_report(probes, None, actions)

    if not DB_URL:
        log.error("DATABASE_URL non impostata — impossibile controllare il DB")
        # gli endpoint sono comunque stati sondati
        code = emit_report(probes, None, actions)
        return code or 1

    conn = await asyncpg.connect(DB_URL)
    try:
        health = await check_db(conn)
        if not args.check_only:
            actions = await heal(conn, health, probes, rescrape=(args.heal == "all"))
            if actions:
                log.info("Azioni svolte: %s", "; ".join(actions))
    finally:
        await conn.close()

    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "probes": [asdict(p) for p in probes],
        "health": asdict(health) if health else None,
        "actions": actions,
    }
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        log.info("Report scritto in %s", args.json_out)

    return emit_report(probes, health, actions)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SpesaSmart Guardian")
    parser.add_argument("--check-only", action="store_true",
                        help="Solo diagnosi, nessuna auto-riparazione")
    parser.add_argument("--probe-only", action="store_true",
                        help="Solo liveness endpoint (non richiede DB)")
    parser.add_argument("--heal", choices=["safe", "all"], default="safe",
                        help="safe=prune+dedup (default); all=anche ri-scrape catene ferme")
    parser.add_argument("--json-out", default=None,
                        help="Scrive il report JSON nel file indicato")
    sys.exit(asyncio.run(main(parser.parse_args())))
