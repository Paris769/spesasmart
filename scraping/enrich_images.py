"""
Arricchimento immagini prodotti da Open Food Facts.

Trova i prodotti senza immagine che hanno un barcode EAN numerico
(8-14 cifre), interroga l'API OFF per barcode e riempie products.image_url.

Pensato soprattutto per Carrefour, i cui prodotti hanno il barcode EAN ma
nessuna immagine (l'endpoint AJAX non la espone). Funziona per qualunque
prodotto con barcode numerico e immagine mancante.

Uso:
    python -m scraping.enrich_images
    python -m scraping.enrich_images --limit 2000 --dry-run

Note:
  - L'API prodotto di OFF limita a ~100 richieste/minuto: il modulo
    impone un ritmo di ~80/min per non farsi bloccare.
  - I prodotti a marchio privato spesso NON sono su OFF: è normale che
    una buona parte delle ricerche non trovi nulla.
  - Idempotente: ad ogni esecuzione restano da fare solo i prodotti
    ancora senza immagine, quindi più esecuzioni completano il catalogo.
"""
import argparse
import asyncio
import logging
import os
import sys
import time

import asyncpg
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("enrich_images")

DB_URL = (
    os.getenv("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql://")
)

OFF_API = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
USER_AGENT = "SpesaSmart/1.0 (https://spesasmart.it; +info@optimait.it)"

# OFF impone 15 richieste/min/IP sull'endpoint prodotto: oltre → ban dell'IP
# (era la causa dei 429 in CI). 4.3s ≈ 14/min, sotto il limite.
RATE = 4.3
TIMEOUT = 12         # timeout per richiesta
# In 90 min di CI: ~1250 prodotti/run. Job schedulato frequente per coprire il
# backlog nel tempo; per riempire in fretta usare il dump bulk OFF (vedi README).
DEFAULT_LIMIT = 1200
DB_BATCH = 100       # ogni quanti aggiornamenti scrivere sul DB


async def _fetch_off_image(client: httpx.AsyncClient, barcode: str) -> str | None:
    """Ritorna l'URL immagine da Open Food Facts per il barcode, o None."""
    try:
        r = await client.get(
            OFF_API.format(barcode=barcode),
            params={"fields": "image_front_url,image_url"},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
    except httpx.RequestError as exc:
        log.warning("Errore rete barcode %s: %s", barcode, exc)
        return None

    if r.status_code == 429:
        log.warning("429 da OFF — attesa 30s")
        await asyncio.sleep(30)
        return None
    if r.status_code != 200:
        return None

    try:
        data = r.json()
    except ValueError:
        return None
    if data.get("status") != 1:
        return None  # prodotto non presente su OFF

    p = data.get("product") or {}
    url = p.get("image_front_url") or p.get("image_url")
    return url or None


async def _flush(conn: asyncpg.Connection, updates: list[tuple]) -> int:
    """Scrive in batch (id, image_url) su products. Ritorna il numero scritto."""
    if not updates:
        return 0
    await conn.execute(
        """
        UPDATE products AS p
           SET image_url = v.url,
               updated_at = NOW()
        FROM unnest($1::uuid[], $2::text[]) AS v(id, url)
        WHERE p.id = v.id
        """,
        [u[0] for u in updates],
        [u[1] for u in updates],
    )
    return len(updates)


async def enrich_images(
    conn: asyncpg.Connection,
    limit: int = DEFAULT_LIMIT,
    dry_run: bool = False,
    shards: int = 1,
    shard: int = 0,
) -> int:
    """
    Arricchisce le immagini mancanti. Ritorna il numero di immagini scritte.

    Sharding (shards>1): ogni shard processa un sottoinsieme DISGIUNTO di
    prodotti (hash stabile del barcode % shards == shard). Permette di eseguire
    N runner in parallelo — ognuno con un IP diverso e quindi il proprio budget
    di rate-limit OFF (15 req/min) — moltiplicando la velocità senza superare il
    limite per-IP e mantenendo il match 100% corretto (sempre per barcode).
    """
    rows = await conn.fetch(
        """
        SELECT id, barcode
          FROM products
         WHERE (image_url IS NULL OR image_url = '')
           AND barcode ~ '^[0-9]{8,14}$'
           AND ($2 <= 1 OR (abs(hashtext(barcode)) % $2) = $3)
         ORDER BY updated_at DESC NULLS LAST
         LIMIT $1
        """,
        limit, shards, shard,
    )
    if shards > 1:
        log.info("Shard %d/%d", shard, shards)
    log.info("Prodotti da arricchire (barcode EAN, senza immagine): %d", len(rows))
    if not rows:
        return 0

    found = 0
    written = 0
    pending: list[tuple] = []
    t_last = 0.0

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for i, row in enumerate(rows, start=1):
            # Throttle: ~80 richieste/minuto
            elapsed = time.monotonic() - t_last
            if elapsed < RATE:
                await asyncio.sleep(RATE - elapsed)
            t_last = time.monotonic()

            url = await _fetch_off_image(client, row["barcode"])
            if url:
                found += 1
                if dry_run:
                    log.info("[DRY] %s → %s", row["barcode"], url)
                else:
                    pending.append((row["id"], url))

            if len(pending) >= DB_BATCH:
                written += await _flush(conn, pending)
                pending = []

            if i % 200 == 0:
                log.info(
                    "  %d/%d esaminati — %d immagini trovate", i, len(rows), found
                )

        written += await _flush(conn, pending)

    log.info(
        "=== Fine. Esaminati: %d — immagini trovate: %d — scritte: %d ===",
        len(rows), found, written if not dry_run else found,
    )
    return found if dry_run else written


async def main(args: argparse.Namespace) -> None:
    if not DB_URL:
        sys.exit("Errore: DATABASE_URL non impostata")
    conn = await asyncpg.connect(DB_URL)
    try:
        await enrich_images(
            conn, limit=args.limit, dry_run=args.dry_run,
            shards=args.shards, shard=args.shard,
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arricchimento immagini da OFF")
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help=f"Max prodotti per esecuzione (default {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Stampa le immagini trovate senza scrivere nel DB",
    )
    parser.add_argument(
        "--shards", type=int, default=1,
        help="Numero totale di shard paralleli (default 1 = nessuno sharding)",
    )
    parser.add_argument(
        "--shard", type=int, default=0,
        help="Indice di questo shard (0..shards-1)",
    )
    asyncio.run(main(parser.parse_args()))
