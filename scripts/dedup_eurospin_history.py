#!/usr/bin/env python
"""
Deduplica lo storico prezzi Eurospin.

Le offerte Eurospin sono nazionali: lo stesso prezzo veniva riscritto ogni
giorno su centinaia di negozi, generando milioni di righe identiche (~99,6% di
duplicati) senza alcuna informazione in piu'. Qui teniamo UNA riga per ogni
combinazione (prodotto, giorno, prezzo) — così la serie storica di ogni prodotto
resta intatta per il controllo "promo vera o finta" — ed eliminiamo il resto.

Tocca SOLO le righe storiche (is_current = FALSE) della catena eurospin.
I prezzi correnti non vengono mai toccati.

Uso:
    python scripts/dedup_eurospin_history.py --dry-run   # solo conteggio
    python scripts/dedup_eurospin_history.py             # esegue
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

BATCH = 50_000
CHAIN = "eurospin"


def db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        local = Path(__file__).resolve().parent.parent / ".db_url.local"
        if local.exists():
            url = local.read_text(encoding="utf-8-sig").strip()
    if not url:
        sys.exit("DATABASE_URL non impostata (ne' .db_url.local presente)")
    return url.replace("postgresql+asyncpg://", "postgresql://")


SELECT_DUPLICATI = """
    SELECT pr.id
    FROM prices pr
    JOIN stores s ON pr.store_id = s.id
    JOIN chains c ON s.chain_id = c.id
    WHERE c.slug = $1
      AND pr.is_current = FALSE
      AND EXISTS (
          -- esiste già un'altra riga con stesso prodotto, giorno e prezzo:
          -- questa è una copia, la teniamo solo una volta (id più basso)
          SELECT 1
          FROM prices altra
          JOIN stores s2 ON altra.store_id = s2.id
          JOIN chains c2 ON s2.chain_id = c2.id
          WHERE c2.slug = $1
            AND altra.is_current = FALSE
            AND altra.product_id = pr.product_id
            AND altra.price = pr.price
            AND date_trunc('day', altra.scraped_at) = date_trunc('day', pr.scraped_at)
            AND altra.id < pr.id
      )
    LIMIT $2
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="conta soltanto")
    args = ap.parse_args()

    conn = await asyncpg.connect(db_url())
    try:
        await conn.execute("SET statement_timeout = '600s'")

        prima = await conn.fetchrow(
            """
            SELECT count(*) FILTER (WHERE pr.is_current) AS correnti,
                   count(*) FILTER (WHERE NOT pr.is_current) AS storici
            FROM prices pr JOIN stores s ON pr.store_id=s.id
            JOIN chains c ON s.chain_id=c.id WHERE c.slug=$1
            """,
            CHAIN,
        )
        print(f"Eurospin prima: {prima['correnti']:,} correnti | {prima['storici']:,} storici")

        if args.dry_run:
            n = await conn.fetchval(
                f"SELECT count(*) FROM ({SELECT_DUPLICATI.replace('LIMIT $2', 'LIMIT 5000000')}) x",
                CHAIN,
            )
            print(f"[DRY] duplicati eliminabili: {n:,}")
            return

        # execute() restituisce la stringa di stato "DELETE n": è da lì che
        # leggiamo quante righe sono state eliminate a ogni lotto.
        totale = 0
        giro = 0
        while True:
            giro += 1
            status = await conn.execute(
                f"""
                WITH da_eliminare AS ({SELECT_DUPLICATI})
                DELETE FROM prices p USING da_eliminare d WHERE p.id = d.id
                """,
                CHAIN,
                BATCH,
            )
            n = int(status.split()[-1]) if status.startswith("DELETE") else 0
            totale += n
            print(f"  lotto {giro:>3}: eliminate {n:,} (totale {totale:,})", flush=True)
            if n == 0:
                break

        dopo = await conn.fetchrow(
            """
            SELECT count(*) FILTER (WHERE pr.is_current) AS correnti,
                   count(*) FILTER (WHERE NOT pr.is_current) AS storici
            FROM prices pr JOIN stores s ON pr.store_id=s.id
            JOIN chains c ON s.chain_id=c.id WHERE c.slug=$1
            """,
            CHAIN,
        )
        print(f"\nEurospin dopo: {dopo['correnti']:,} correnti | {dopo['storici']:,} storici")
        print(f"Righe eliminate: {totale:,}")
        print("\nRecupero spazio su disco in corso (VACUUM)…")
        await conn.execute("VACUUM (ANALYZE) prices")
        print("Fatto.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
