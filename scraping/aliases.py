"""
Risoluzione barcode → product_id con supporto agli alias.

Quando il dedup unisce due prodotti elimina il duplicato, ma ne registra il
barcode nella tabella `product_aliases` puntando al superstite. Gli spider
devono perciò cercare un barcode PRIMA tra i prodotti veri e POI tra gli
alias: così un prodotto già unito viene ritrovato e aggiornato sul posto,
senza ricreare un doppione (la "churn" che lasciava prezzi stantii).
"""
from __future__ import annotations

import asyncpg


async def resolve_existing(
    conn: asyncpg.Connection, barcodes: list[str]
) -> tuple[dict[str, object], set[str]]:
    """
    Per una lista di barcode ritorna:
      - id_by_bc: {barcode: product_id} per i barcode che già esistono,
        risolti sia dai prodotti veri sia dagli alias del dedup;
      - direct_bcs: i barcode che corrispondono direttamente a una riga
        `products` (quelli su cui ha senso fare UPDATE dei dati prodotto;
        per gli alias si aggiorna solo il prezzo, non si tocca il superstite).
    """
    if not barcodes:
        return {}, set()

    rows = await conn.fetch(
        "SELECT id, barcode FROM products WHERE barcode = ANY($1::text[])",
        barcodes,
    )
    id_by_bc: dict[str, object] = {r["barcode"]: r["id"] for r in rows}
    direct_bcs: set[str] = set(id_by_bc)

    missing = [bc for bc in barcodes if bc not in id_by_bc]
    if missing:
        alias_rows = await conn.fetch(
            "SELECT alias_barcode, product_id FROM product_aliases "
            "WHERE alias_barcode = ANY($1::text[])",
            missing,
        )
        for r in alias_rows:
            id_by_bc[r["alias_barcode"]] = r["product_id"]

    return id_by_bc, direct_bcs
