#!/usr/bin/env python
"""
Deduplica lo storico prezzi Eurospin.

Le offerte Eurospin sono nazionali: lo stesso prezzo veniva riscritto ogni
giorno su centinaia di negozi, generando milioni di righe identiche (~99,6% di
duplicati) senza alcuna informazione in piu'. Qui teniamo UNA riga per ogni
combinazione (prodotto, giorno, prezzo) — cosi' la serie storica di ogni
prodotto resta intatta per il controllo "promo vera o finta" — e togliamo il resto.

Tocca SOLO le righe storiche (is_current = FALSE) con source='eurospin_web'.
I prezzi correnti non vengono mai toccati.

Procede un PRODOTTO alla volta: le cancellazioni massive in un colpo solo
facevano cadere la connessione del pooler.

Uso:
    python scripts/dedup_eurospin_history.py --dry-run
    python scripts/dedup_eurospin_history.py
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

SOURCE = "eurospin_web"


def db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        local = Path(__file__).resolve().parent.parent / ".db_url.local"
        if local.exists():
            url = local.read_text(encoding="utf-8-sig").strip()
    if not url:
        sys.exit("DATABASE_URL non impostata (ne' .db_url.local presente)")
    return url.replace("postgresql+asyncpg://", "postgresql://")


DELETE_UN_PRODOTTO = """
    DELETE FROM prices p
    WHERE p.id IN (
        SELECT id FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY date_trunc('day', scraped_at), price
                       ORDER BY id
                   ) AS rn
            FROM prices
            WHERE product_id = $1
              AND is_current = FALSE
              AND source = $2
        ) x
        WHERE rn > 1
    )
"""


async def connetti(url: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(url, timeout=60, command_timeout=180)
    await conn.execute("SET statement_timeout = '180s'")
    return conn


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    url = db_url()
    conn = await connetti(url)
    try:
        prima = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE is_current) AS correnti,
                      count(*) FILTER (WHERE NOT is_current) AS storici
               FROM prices WHERE source = $1""",
            SOURCE,
        )
        print(f"Eurospin prima: {prima['correnti']:,} correnti | {prima['storici']:,} storici")

        prodotti = [
            r["product_id"]
            for r in await conn.fetch(
                """SELECT DISTINCT product_id FROM prices
                   WHERE source = $1 AND is_current = FALSE""",
                SOURCE,
            )
        ]
        print(f"Prodotti con storico da ripulire: {len(prodotti):,}\n")

        if args.dry_run:
            print("[DRY] nessuna modifica eseguita")
            return

        totale = 0
        for i, pid in enumerate(prodotti, 1):
            for tentativo in range(3):
                try:
                    status = await conn.execute(DELETE_UN_PRODOTTO, pid, SOURCE)
                    totale += int(status.split()[-1]) if status.startswith("DELETE") else 0
                    break
                except (asyncpg.PostgresError, OSError, asyncpg.exceptions.ConnectionDoesNotExistError):
                    # il pooler puo' chiudere la connessione: riapri e riprova
                    try:
                        await conn.close()
                    except Exception:
                        pass
                    await asyncio.sleep(2)
                    conn = await connetti(url)
            if i % 100 == 0 or i == len(prodotti):
                print(f"  {i:>5}/{len(prodotti)} prodotti — righe eliminate: {totale:,}", flush=True)

        dopo = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE is_current) AS correnti,
                      count(*) FILTER (WHERE NOT is_current) AS storici
               FROM prices WHERE source = $1""",
            SOURCE,
        )
        print(f"\nEurospin dopo: {dopo['correnti']:,} correnti | {dopo['storici']:,} storici")
        print(f"Righe eliminate: {totale:,}")
        print("\nRecupero spazio (VACUUM ANALYZE)…", flush=True)
        await conn.execute("VACUUM (ANALYZE) prices")
        print("Fatto.")
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
