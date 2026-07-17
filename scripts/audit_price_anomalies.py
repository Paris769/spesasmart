#!/usr/bin/env python
"""
Audit anomalie prezzi cross-catena (SpesaSmart).

Per ogni prodotto con prezzi correnti in almeno --min-chains catene diverse
calcola la mediana cross-catena (mediana delle mediane per catena, cosi' una
catena con molti negozi non domina) e marca come SOSPETTI i prezzi:
  - sotto  --low-pct  % della mediana (default 40%): tipicamente prezzo per
    singolo pezzo di un multipack, o errore di scrape;
  - sopra --high-pct % della mediana (default 300%).

Euristica multipack: se il nome prodotto contiene pattern tipo "x6", "6x",
"cluster", "fardello" il sospetto viene segnalato come possibile multipack
(prezzo a pezzo vs prezzo a confezione).

Default DRY-RUN: stampa il report (tabella + JSON in scripts/out/) e NON
scrive nulla sul DB.

  --apply    UPDATE prices SET quarantined = true sui prezzi sospetti
             (mai DELETE: rollback sempre possibile).
  --release  UPDATE prices SET quarantined = false su TUTTI i prezzi
             (rollback totale della quarantena).

Connessione: --db-url, oppure env DATABASE_URL, oppure il file .db_url.local
nella root del repo.

Esempi (PowerShell):
  python scripts/audit_price_anomalies.py
  python scripts/audit_price_anomalies.py --low-pct 35 --high-pct 400
  python scripts/audit_price_anomalies.py --apply
  python scripts/audit_price_anomalies.py --release
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

# Come in backend/app/api/v1/endpoints/products.py e lists.py
MIN_VALID_PRICE = 0.10

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_DEFAULT_OUT = _SCRIPT_DIR / "out" / "price_anomalies.json"

# Nomi che suggeriscono una confezione multipla: "x6", "6x", "6 x 1,5", "×4",
# cluster, fardello, multipack, "confezione da 6" ecc.
MULTIPACK_RE = re.compile(
    r"(?ix)"
    r"(?:\b\d+\s*[x×]\s*\d)"      # 6x1,5  12 x 33
    r"|(?:\bx\s*\d+\b)"                # x6  x 12
    r"|(?:\b\d+\s*x\b)"                # 6x  12 x
    r"|(?:\bcluster\b)"
    r"|(?:\bfardello\b)"
    r"|(?:\bmulti\s*pack\b)"
    r"|(?:\bconfezione\s+da\s+\d+)"
    r"|(?:\b\d+\s*(?:pezzi|pz|bott(?:iglie)?|lattine|brik)\b)"
)

# Mediana per (prodotto, catena) sui prezzi correnti validi e non gia'
# quarantinati, poi mediana cross-catena; sospetti i prezzi fuori range.
# Il pre-filtro multi_chain riduce subito il set (solo ~27% dei prodotti ha
# prezzi in 2+ catene): senza, l'aggregato sfora lo statement_timeout.
ANOMALY_SQL = """
WITH multi_chain AS (
    SELECT p.product_id
    FROM prices p
    JOIN stores s ON p.store_id = s.id
    WHERE p.is_current
      AND NOT p.quarantined
      AND p.price >= $1
      AND s.is_active
    GROUP BY p.product_id
    HAVING COUNT(DISTINCT s.chain_id) >= $2
),
chain_median AS (
    SELECT p.product_id,
           s.chain_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY p.price::float8) AS chain_median
    FROM prices p
    JOIN stores s ON p.store_id = s.id
    JOIN multi_chain mc ON mc.product_id = p.product_id
    WHERE p.is_current
      AND NOT p.quarantined
      AND p.price >= $1
      AND s.is_active
    GROUP BY p.product_id, s.chain_id
),
product_median AS (
    SELECT product_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY chain_median) AS median_price,
           COUNT(*) AS chain_count
    FROM chain_median
    GROUP BY product_id
)
SELECT pr.id            AS price_id,
       pr.product_id,
       pr.price::float8 AS price,
       pr.quarantined   AS already_quarantined,
       prod.name        AS product_name,
       prod.brand       AS brand,
       c.slug           AS chain_slug,
       c.name           AS chain_name,
       s.name           AS store_name,
       m.median_price,
       m.chain_count,
       pr.price::float8 / m.median_price AS ratio,
       pr.scraped_at
FROM prices pr
JOIN product_median m ON m.product_id = pr.product_id
JOIN products prod    ON prod.id = pr.product_id
JOIN stores s         ON pr.store_id = s.id
JOIN chains c         ON s.chain_id = c.id
WHERE pr.is_current
  AND pr.price >= $1
  AND s.is_active
  AND m.median_price > 0
  AND (pr.price::float8 < m.median_price * $3
       OR pr.price::float8 > m.median_price * $4)
ORDER BY pr.price::float8 / m.median_price ASC
"""


def resolve_db_url(cli_url: str | None) -> str | None:
    url = cli_url or os.getenv("DATABASE_URL")
    if not url:
        local = _REPO_ROOT / ".db_url.local"
        if local.is_file():
            url = local.read_text(encoding="utf-8").strip()
    if not url:
        return None
    return url.replace("postgresql+asyncpg://", "postgresql://")


def build_report(rows, args) -> dict:
    anomalies = []
    for r in rows:
        name = r["product_name"] or ""
        anomalies.append({
            "price_id": str(r["price_id"]),
            "product_id": str(r["product_id"]),
            "product_name": name,
            "brand": r["brand"],
            "chain_slug": r["chain_slug"],
            "chain_name": r["chain_name"],
            "store_name": r["store_name"],
            "price": round(float(r["price"]), 2),
            "median_price": round(float(r["median_price"]), 2),
            "ratio": round(float(r["ratio"]), 3),
            "direction": "low" if float(r["ratio"]) < 1 else "high",
            "chain_count": int(r["chain_count"]),
            "possible_multipack": bool(MULTIPACK_RE.search(name)),
            "already_quarantined": bool(r["already_quarantined"]),
            "scraped_at": r["scraped_at"].isoformat() if r["scraped_at"] else None,
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "low_pct": args.low_pct,
            "high_pct": args.high_pct,
            "min_chains": args.min_chains,
            "min_valid_price": MIN_VALID_PRICE,
        },
        "n_anomalies": len(anomalies),
        "n_low": sum(1 for a in anomalies if a["direction"] == "low"),
        "n_high": sum(1 for a in anomalies if a["direction"] == "high"),
        "n_possible_multipack": sum(1 for a in anomalies if a["possible_multipack"]),
        "applied": False,  # aggiornato dopo l'eventuale --apply
        "anomalies": anomalies,
    }


def print_table(anomalies: list[dict], limit: int) -> None:
    if not anomalies:
        print("Nessuna anomalia trovata con le soglie correnti.")
        return
    header = f"{'PRODOTTO':<44} {'CATENA':<11} {'PREZZO':>8} {'MEDIANA':>8} {'RATIO':>6}  FLAG"
    print(header)
    print("-" * len(header))
    for a in anomalies[:limit]:
        flags = []
        if a["possible_multipack"]:
            flags.append("multipack?")
        if a["already_quarantined"]:
            flags.append("gia' quarantinato")
        name = a["product_name"][:43]
        print(
            f"{name:<44} {a['chain_slug']:<11} {a['price']:>8.2f} "
            f"{a['median_price']:>8.2f} {a['ratio']:>6.2f}  {' '.join(flags)}"
        )
    if len(anomalies) > limit:
        print(f"... e altre {len(anomalies) - limit} righe (vedi JSON completo)")


async def run(args) -> int:
    db_url = resolve_db_url(args.db_url)
    if not db_url:
        print("ERRORE: nessuna connection string. Usa --db-url, DATABASE_URL "
              "o il file .db_url.local nella root del repo.", file=sys.stderr)
        return 2

    try:
        conn = await asyncpg.connect(db_url, timeout=20)
    except Exception as exc:
        print(f"ERRORE connessione DB: {exc}", file=sys.stderr)
        return 2

    # L'aggregato sulle mediane e' pesante: il default del pooler Supabase
    # (2 min) non basta. Alza il limite solo per questa sessione.
    # enable_nestloop=off: il planner sottostima i CTE (bind param) e sceglie
    # nested loop -> centinaia di migliaia di letture random (~4 min); con gli
    # hash join la stessa query scansiona prices poche volte (~45 s misurati).
    try:
        await conn.execute(f"SET statement_timeout = '{int(args.timeout_s)}s'")
        await conn.execute("SET enable_nestloop = off")
    except Exception:
        pass  # se il ruolo non lo consente si prova comunque

    try:
        if args.release:
            # Rollback totale: rimette in circolo tutti i prezzi quarantinati.
            tag = await conn.execute("UPDATE prices SET quarantined = FALSE WHERE quarantined")
            n = int(tag.split()[-1])
            print(f"--release: rimessi in circolo {n} prezzi (quarantined = false).")
            return 0

        rows = await conn.fetch(
            ANOMALY_SQL,
            MIN_VALID_PRICE,
            args.min_chains,
            args.low_pct / 100.0,
            args.high_pct / 100.0,
        )
        report = build_report(rows, args)

        print(
            f"Prodotti confrontabili in >= {args.min_chains} catene: "
            f"anomalie {report['n_anomalies']} "
            f"(low {report['n_low']}, high {report['n_high']}, "
            f"possibili multipack {report['n_possible_multipack']})\n"
        )
        print_table(report["anomalies"], args.table_limit)

        if args.apply:
            ids = [a["price_id"] for a in report["anomalies"] if not a["already_quarantined"]]
            if ids:
                tag = await conn.execute(
                    "UPDATE prices SET quarantined = TRUE WHERE id = ANY($1::uuid[])",
                    ids,
                )
                n = int(tag.split()[-1])
                print(f"\n--apply: quarantinati {n} prezzi (quarantined = true, nessun DELETE).")
            else:
                print("\n--apply: niente da quarantinare.")
            report["applied"] = True
        else:
            print("\nDRY-RUN: nessuna scrittura sul DB. Usa --apply per quarantinare, "
                  "--release per il rollback totale.")

        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Report JSON salvato in: {out_path}")
        return 0
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit anomalie prezzi cross-catena (dry-run di default).",
    )
    parser.add_argument("--db-url", default=None,
                        help="Connection string Postgres (default: env DATABASE_URL, poi .db_url.local)")
    parser.add_argument("--low-pct", type=float, default=40.0,
                        help="Sospetto se prezzo < LOW%% della mediana (default 40)")
    parser.add_argument("--high-pct", type=float, default=300.0,
                        help="Sospetto se prezzo > HIGH%% della mediana (default 300)")
    parser.add_argument("--min-chains", type=int, default=3,
                        help="Minimo di catene diverse con prezzo corrente (default 3)")
    parser.add_argument("--table-limit", type=int, default=40,
                        help="Righe massime della tabella a video (default 40)")
    parser.add_argument("--timeout-s", type=int, default=300,
                        help="statement_timeout di sessione in secondi (default 300)")
    parser.add_argument("--out", default=str(_DEFAULT_OUT),
                        help=f"Percorso del report JSON (default {_DEFAULT_OUT})")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true",
                        help="Quarantina i prezzi sospetti (UPDATE, mai DELETE)")
    action.add_argument("--release", action="store_true",
                        help="Rollback totale: quarantined = false su tutti i prezzi")
    args = parser.parse_args()

    if args.low_pct <= 0 or args.high_pct <= args.low_pct:
        parser.error("--low-pct deve essere > 0 e --high-pct > --low-pct")
    if args.min_chains < 2:
        parser.error("--min-chains deve essere >= 2")

    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
