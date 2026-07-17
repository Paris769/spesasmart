"""
POC volantini discount — scrive le promo matchate in prices.

Default: DRY-RUN — stampa cosa scriverebbe, non tocca il DB.
Con --apply esegue l'upsert con lo stesso pattern degli spider esistenti
(UPDATE is_current=FALSE sui prezzi correnti + INSERT del nuovo prezzo).

Campi scritti: price, original_price, promo_label, promo_expires (promo_to),
price_per_unit (dichiarato dal volantino), in_stock=TRUE, is_current=TRUE,
source='flyer'.

store_id: il punto vendita "nazionale" virtuale della catena (external_id
'<slug>-online' / '<slug>-offerte' / 'md-pv-1'). Se la catena non ha uno
store virtuale (es. Eurospin, che ha solo punti vendita fisici) il prezzo
nazionale viene replicato su un campione di store fisici attivi
(--store-limit, default 25 — stesso approccio di eurospin_spider).
I volantini zonali per-store sono fuori scope per il POC.

⚠ In questa sessione POC il flag --apply NON va eseguito contro il DB di
produzione: è implementato per completezza, usare solo dry-run.

Uso:
    python -m scraping.flyers.load scraping/flyers/out/mock/<data>/matched.json
    python -m scraping.flyers.load <matched.json> --apply   # (non in questa sessione)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

from .match import db_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("flyers.load")

SOURCE = "flyer"
MIN_MATCH_SCORE = 0.55       # sotto questa soglia non scriviamo il prezzo
MIN_CONFIDENCE = 0.8         # confidenza minima dell'estrazione vision


async def national_store_ids(
    conn: asyncpg.Connection, chain: str, store_limit: int
) -> list[str]:
    """Store 'nazionale' virtuale della catena (online/offerte/md-pv-1);
    in mancanza, un campione di store fisici attivi (volantino nazionale)."""
    row = await conn.fetchrow(
        """
        SELECT s.id, s.external_id
        FROM stores s
        JOIN chains c ON c.id = s.chain_id
        WHERE c.slug = $1
          AND s.is_active = TRUE
          AND (s.external_id LIKE '%-online'
               OR s.external_id LIKE '%-offerte'
               OR s.external_id = 'md-pv-1')
        ORDER BY (s.external_id LIKE '%-online') DESC, s.external_id
        LIMIT 1
        """,
        chain,
    )
    if row:
        log.info("Store nazionale %s: %s (%s)", chain, row["external_id"], row["id"])
        return [str(row["id"])]

    rows = await conn.fetch(
        """
        SELECT s.id
        FROM stores s
        JOIN chains c ON c.id = s.chain_id
        WHERE c.slug = $1 AND s.is_active = TRUE
        ORDER BY
          CASE WHEN s.province = ANY($2::text[]) THEN 0 ELSE 1 END,
          s.province NULLS LAST, s.city NULLS LAST, s.external_id
        LIMIT $3
        """,
        chain,
        ["MI", "RM", "TO", "NA", "BO", "FI", "PA", "GE", "VR", "PD"],
        store_limit,
    )
    if rows:
        log.info("Nessuno store virtuale per %s: uso %d store fisici attivi "
                 "(prezzo nazionale)", chain, len(rows))
    return [str(r["id"]) for r in rows]


def promo_label(item: dict, chain: str) -> str:
    price, orig = item.get("price"), item.get("original_price")
    if orig and price and orig > price:
        pct = round((orig - price) / orig * 100)
        label = f"Volantino {chain.capitalize()} -{pct}%"
    else:
        label = f"Volantino {chain.capitalize()}"
    if item.get("requires_card"):
        label += " (con carta)"
    return label


def parse_date(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d")
    except ValueError:
        return None


async def run(args: argparse.Namespace) -> None:
    matched_path = Path(args.matched)
    if not matched_path.exists():
        sys.exit(f"File non trovato: {matched_path}")
    data = json.loads(matched_path.read_text(encoding="utf-8"))
    chain = args.chain or data.get("chain")
    if chain == "mock":
        chain = "lidl"  # la fixture mock simula un volantino Lidl
    matches = data.get("matches", [])

    rows: list[dict] = []
    skipped = {"unmatched": 0, "low_score": 0, "low_confidence": 0, "no_price": 0}
    for m in matches:
        item = m["flyer_item"]
        if not m.get("product_id"):
            skipped["unmatched"] += 1
            continue
        if (m.get("match_score") or 0) < MIN_MATCH_SCORE:
            skipped["low_score"] += 1
            continue
        if (item.get("confidence") or 0) < MIN_CONFIDENCE:
            skipped["low_confidence"] += 1
            continue
        if not item.get("price"):
            skipped["no_price"] += 1
            continue
        rows.append({
            "product_id": m["product_id"],
            "price": float(item["price"]),
            "original_price": (float(item["original_price"])
                               if item.get("original_price") else None),
            "promo_label": promo_label(item, chain),
            "promo_expires": parse_date(item.get("promo_to")),
            "price_per_unit": (float(item["price_per_unit_claimed"])
                               if item.get("price_per_unit_claimed") else None),
            "name": item.get("name"),
        })

    log.info("Prezzi da scrivere: %d — scartati: %s", len(rows), skipped)
    if not rows:
        return

    url = db_url()
    if not url:
        sys.exit("Nessuna DATABASE_URL e nessun .db_url.local")
    conn = await asyncpg.connect(url)
    try:
        store_ids = await national_store_ids(conn, chain, args.store_limit)
        if not store_ids:
            sys.exit(f"Nessuno store per la catena '{chain}' nel DB")

        if not args.apply:
            log.info("=== DRY-RUN — nessuna scrittura. Anteprima (%d prezzi × "
                     "%d store): ===", len(rows), len(store_ids))
            for r in rows[:30]:
                log.info(
                    "[DRY] %-50s €%.2f%s%s → product %s",
                    (r["name"] or "")[:50],
                    r["price"],
                    f" (orig €{r['original_price']:.2f})" if r["original_price"] else "",
                    f" fino al {r['promo_expires']:%Y-%m-%d}" if r["promo_expires"] else "",
                    r["product_id"],
                )
            if len(rows) > 30:
                log.info("[DRY] … e altri %d prezzi", len(rows) - 30)
            log.info("=== Per applicare: --apply (NON in questa sessione POC) ===")
            return

        now = datetime.now(timezone.utc)
        product_ids = [r["product_id"] for r in rows]
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE prices SET is_current = FALSE
                WHERE store_id = ANY($1::uuid[])
                  AND product_id = ANY($2::uuid[])
                  AND is_current = TRUE
                """,
                store_ids, product_ids,
            )
            await conn.executemany(
                """
                INSERT INTO prices
                    (product_id, store_id, price, original_price, promo_label,
                     promo_expires, price_per_unit, in_stock, is_current,
                     source, scraped_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, TRUE, $8, $9)
                """,
                [
                    (r["product_id"], sid, r["price"], r["original_price"],
                     r["promo_label"], r["promo_expires"], r["price_per_unit"],
                     SOURCE, now)
                    for r in rows
                    for sid in store_ids
                ],
            )
        log.info("=== Applicato: %d prezzi flyer × %d store ===",
                 len(rows), len(store_ids))
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Carica le promo volantino in prices")
    parser.add_argument("matched", help="Percorso di matched.json")
    parser.add_argument("--chain", default=None,
                        help="Slug catena (default: dal file matched.json)")
    parser.add_argument("--store-limit", type=int, default=25,
                        help="Max store fisici se manca lo store virtuale "
                             "nazionale (default: 25)")
    parser.add_argument("--apply", action="store_true",
                        help="Scrive davvero nel DB (default: dry-run)")
    asyncio.run(run(parser.parse_args()))
