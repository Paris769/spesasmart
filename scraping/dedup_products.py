"""
Deduplica i prodotti: unisce le righe `products` che rappresentano lo stesso
prodotto fisico ma sono state inserite separatamente da scraper diversi.

Causa: ogni catena salva un barcode diverso (Carrefour l'EAN reale, Conad
`conad-{cod}`, Esselunga `esselunga-{cod}`), così lo stesso latte diventa
righe distinte e l'app mostra ogni prodotto in "1 negozio".

Strategia:
  Pass A — match esatto su EAN canonico. Sicuro al 100%: stesso GTIN-13 =
           stesso prodotto.
  Pass B — match fuzzy entro blocchi (stesso brand + stessa quantità):
           similarità di Jaccard dei token del nome >= soglia. Il blocking
           per brand+quantità impedisce di fondere brand o formati diversi.

Per ogni gruppo: sceglie un superstite, ri-punta prices (e ogni FK verso
products), sistema i flag is_current, elimina i duplicati.

Uso:
    python -m scraping.dedup_products            # DRY-RUN: stampa, non scrive
    python -m scraping.dedup_products --apply     # applica le modifiche
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import asyncpg

from .ean import canonical_ean, name_token_jaccard, norm_brand, normalize_quantity

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("dedup")

DB_URL = (
    os.getenv("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql://")
)

# Soglia di similarità per il match fuzzy (Pass B). Il blocking per
# brand+quantità rende già impossibile fondere brand/formati diversi;
# questa soglia separa prodotti simili dello stesso brand e formato.
JACCARD_THRESHOLD = 0.6


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict = {}

    def add(self, x) -> None:
        self.parent.setdefault(x, x)

    def find(self, x):
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


async def _fk_columns(conn: asyncpg.Connection) -> list[tuple[str, str]]:
    """Tabelle/colonne con foreign key verso products.id."""
    rows = await conn.fetch(
        """
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON tc.constraint_name = ccu.constraint_name
         AND tc.table_schema = ccu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND ccu.table_name = 'products'
          AND ccu.column_name = 'id'
        """
    )
    return [(r["table_name"], r["column_name"]) for r in rows]


def _pick_survivor(group: list[dict]) -> dict:
    """
    Sceglie la riga superstite del gruppo:
    EAN reale > is_verified > più prezzi > created_at più vecchio.
    """
    def key(p: dict) -> tuple:
        return (
            1 if p["_ean"] else 0,
            1 if p.get("is_verified") else 0,
            p["_price_count"],
            -(p["created_at"].timestamp() if p.get("created_at") else 0),
        )

    return max(group, key=key)


async def dedup(conn: asyncpg.Connection, apply: bool = False) -> int:
    products = [
        dict(r)
        for r in await conn.fetch(
            """SELECT id, barcode, name, brand, image_url, source,
                      is_verified, created_at
               FROM products"""
        )
    ]
    log.info("Prodotti totali nel DB: %d", len(products))
    if not products:
        return 0

    counts = {
        r["product_id"]: r["c"]
        for r in await conn.fetch(
            "SELECT product_id, COUNT(*) AS c FROM prices GROUP BY product_id"
        )
    }
    for p in products:
        p["_ean"] = canonical_ean(p["barcode"])
        p["_price_count"] = counts.get(p["id"], 0)

    uf = _UnionFind()
    for p in products:
        uf.add(p["id"])

    # ── Pass A: match esatto su EAN canonico ─────────────────────────────────
    by_ean: dict[str, list[dict]] = {}
    for p in products:
        if p["_ean"]:
            by_ean.setdefault(p["_ean"], []).append(p)
    pass_a_groups = 0
    for grp in by_ean.values():
        if len(grp) > 1:
            pass_a_groups += 1
            for p in grp[1:]:
                uf.union(grp[0]["id"], p["id"])
    log.info("Pass A (EAN esatto): %d gruppi di duplicati", pass_a_groups)

    # ── Pass B: match fuzzy entro blocchi brand+quantità ─────────────────────
    blocks: dict[tuple, list[dict]] = {}
    for p in products:
        brand = norm_brand(p["brand"])
        qty = normalize_quantity(p["name"])
        if not brand or not qty:
            continue  # senza brand o quantità certi → niente fuzzy (sicurezza)
        blocks.setdefault((brand, qty), []).append(p)

    pass_b_pairs = 0
    for grp in blocks.values():
        if len(grp) < 2:
            continue
        for i in range(len(grp)):
            for j in range(i + 1, len(grp)):
                a, b = grp[i], grp[j]
                if uf.find(a["id"]) == uf.find(b["id"]):
                    continue
                if name_token_jaccard(a["name"], b["name"]) >= JACCARD_THRESHOLD:
                    uf.union(a["id"], b["id"])
                    pass_b_pairs += 1
    log.info("Pass B (fuzzy brand+quantità): %d coppie unite", pass_b_pairs)

    # ── Costruzione dei gruppi di merge ──────────────────────────────────────
    by_id = {p["id"]: p for p in products}
    groups: dict = {}
    for p in products:
        groups.setdefault(uf.find(p["id"]), []).append(p)
    merges = [g for g in groups.values() if len(g) > 1]
    log.info(
        "Gruppi da unire: %d — righe prodotto coinvolte: %d",
        len(merges), sum(len(g) for g in merges),
    )
    if not merges:
        log.info("Nessun duplicato da unire.")
        return 0

    fk_cols = await _fk_columns(conn)
    log.info("Foreign key verso products: %s", fk_cols)

    if not apply:
        log.info("=== DRY-RUN — nessuna modifica scritta. Anteprima merge: ===")
        for g in merges[:60]:
            survivor = _pick_survivor(g)
            log.info(
                "MERGE → superstite [%s] %s (%s)",
                survivor["source"], (survivor["name"] or "")[:55],
                survivor["barcode"],
            )
            for p in g:
                if p["id"] != survivor["id"]:
                    log.info(
                        "        ⤷ unisce [%s] %s (%s)",
                        p["source"], (p["name"] or "")[:55], p["barcode"],
                    )
        if len(merges) > 60:
            log.info("        … e altri %d gruppi", len(merges) - 60)
        log.info("=== Per applicare: --apply ===")
        return len(merges)

    # ── Applicazione (transazione) ───────────────────────────────────────────
    survivor_ids: list = []
    merged = 0
    async with conn.transaction():
        for g in merges:
            survivor = _pick_survivor(g)
            dupes = [p["id"] for p in g if p["id"] != survivor["id"]]
            survivor_ids.append(survivor["id"])

            # ri-punta ogni FK verso products dai duplicati al superstite
            for table, col in fk_cols:
                await conn.execute(
                    f"UPDATE {table} SET {col} = $1 "
                    f"WHERE {col} = ANY($2::uuid[])",
                    survivor["id"], dupes,
                )

            # il superstite eredita un EAN reale, se ne esiste uno nel gruppo
            if not survivor["_ean"]:
                real = next((p["_ean"] for p in g if p["_ean"]), None)
                if real:
                    await conn.execute(
                        "UPDATE products SET barcode = $1 WHERE id = $2",
                        real, survivor["id"],
                    )
            # il superstite eredita un'immagine, se gli manca
            if not survivor["image_url"]:
                img = next(
                    (p["image_url"] for p in g if p["image_url"]), None
                )
                if img:
                    await conn.execute(
                        "UPDATE products SET image_url = $1 WHERE id = $2",
                        img, survivor["id"],
                    )

            await conn.execute(
                "DELETE FROM products WHERE id = ANY($1::uuid[])", dupes
            )
            merged += len(dupes)

        # dopo il re-pointing un superstite può avere più prezzi is_current
        # per lo stesso negozio: tiene corrente solo il più recente.
        await conn.execute(
            """
            UPDATE prices SET is_current = FALSE
            WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY product_id, store_id
                        ORDER BY scraped_at DESC NULLS LAST
                    ) AS rn
                    FROM prices
                    WHERE is_current = TRUE
                      AND product_id = ANY($1::uuid[])
                ) t WHERE rn > 1
            )
            """,
            survivor_ids,
        )

    log.info(
        "=== Dedup completato: %d gruppi uniti, %d righe duplicate eliminate ===",
        len(merges), merged,
    )
    return len(merges)


async def main(args: argparse.Namespace) -> None:
    if not DB_URL:
        sys.exit("Errore: DATABASE_URL non impostata")
    conn = await asyncpg.connect(DB_URL)
    try:
        await dedup(conn, apply=args.apply)
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dedup prodotti SpesaSmart")
    parser.add_argument(
        "--apply", action="store_true",
        help="Applica le modifiche (default: dry-run, stampa soltanto)",
    )
    asyncio.run(main(parser.parse_args()))
