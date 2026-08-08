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


async def preserve_flyer_promos(
    conn: asyncpg.Connection,
    store_ids: list,
    product_ids: list,
) -> int:
    """
    Arricchisce le righe prezzo correnti appena scritte con i metadati promo
    dei volantini che il flip is_current=FALSE dello spider ha appena spento.

    Problema risolto (misurato in produzione): flyers/load.py scrive righe
    source='flyer' con promo_expires e original_price; lo scrape quotidiano
    della stessa coppia (store, product) le spegneva e la promo spariva dal
    feed /offers/nearby entro 24h, perdendo scadenza e prezzo pieno.

    Strategia (ereditarietà): la NUOVA riga corrente dello spider eredita
    promo_expires — e original_price/promo_label se non ne ha già — dalla
    riga spenta più recente con promo ancora valida (promo_expires >= oggi).
      * promo scadute: mai ereditate (le spegne lo sweep di flyers/load.py);
      * righe quarantined: mai usate come sorgente;
      * sanity: se la riga flyer ha un original_price NON superiore al prezzo
        corrente dello spider la promo non si applica a quel prezzo e non
        viene ereditato nulla (evita "offerte" senza sconto);
      * nessuna riga cambia is_current: l'invariante "una sola riga corrente
        per (store, product)" resta intatto;
      * auto-sostenuta: la riga arricchita di oggi (promo_expires valorizzato)
        farà da sorgente domani anche se prune elimina la riga flyer originale.

    Va chiamata DOPO l'insert delle nuove righe correnti, nella stessa
    transazione quando c'è. Ritorna il numero di righe arricchite.
    """
    if not store_ids or not product_ids:
        return 0
    tag = await conn.execute(
        """
        UPDATE prices AS n SET
            promo_expires  = f.promo_expires,
            original_price = COALESCE(n.original_price, f.original_price),
            promo_label    = COALESCE(n.promo_label, f.promo_label)
        FROM (
            SELECT DISTINCT ON (store_id, product_id)
                   store_id, product_id, original_price, promo_label,
                   promo_expires
            FROM prices
            WHERE store_id = ANY($1::uuid[])
              AND product_id = ANY($2::uuid[])
              AND NOT is_current
              AND NOT quarantined
              AND promo_expires IS NOT NULL
              AND promo_expires >= CURRENT_DATE
            ORDER BY store_id, product_id, scraped_at DESC
        ) AS f
        WHERE n.store_id = f.store_id
          AND n.product_id = f.product_id
          AND n.is_current
          AND n.source <> 'flyer'
          AND n.promo_expires IS NULL
          AND (f.original_price IS NULL OR f.original_price > n.price)
        """,
        store_ids,
        product_ids,
    )
    try:
        return int(tag.split()[-1])
    except (AttributeError, IndexError, ValueError):
        return 0
