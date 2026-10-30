from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.freshness import fresh_price_sql
from app.core.price_origin import (
    avviso as avviso_origini,
    descrizione_di,
    origine_di,
    sconto_affermabile_di,
)

router = APIRouter(prefix="/scan", tags=["scan"])

# Come in products.py e offers.py: sotto questa soglia il prezzo e' un
# placeholder o un errore di scraping, non un prezzo.
MIN_VALID_PRICE = 0.10


class PriceSubmit(BaseModel):
    store_id: str
    price: float = Field(..., gt=0, le=9999.99)
    user_id: Optional[str] = None


async def _fetch_off_product(barcode: str) -> dict | None:
    """Cerca un prodotto su Open Food Facts. Ritorna un dict normalizzato o None."""
    async with httpx.AsyncClient(timeout=5) as client:
        res = await client.get(
            f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
        )
    if res.status_code != 200:
        return None
    data = res.json()
    if data.get("status") != 1:
        return None
    p = data["product"]
    return {
        "name": (p.get("product_name_it") or p.get("product_name") or "").strip() or None,
        "brand": (p.get("brands") or "").strip() or None,
        "image_url": p.get("image_url") or p.get("image_front_url") or None,
    }


async def _get_or_create_product(barcode: str, db: AsyncSession) -> dict | None:
    """
    Trova il prodotto nel DB locale, oppure lo crea via Open Food Facts.
    Ritorna il record prodotto o None se barcode sconosciuto ovunque.
    """
    res = await db.execute(
        text("SELECT * FROM products WHERE barcode = :bc LIMIT 1"),
        {"bc": barcode},
    )
    product = res.mappings().first()
    if product:
        return dict(product)

    off = await _fetch_off_product(barcode)
    if not off or not off["name"]:
        return None

    new_id = await db.execute(
        text("""
            INSERT INTO products (barcode, name, brand, image_url, source)
            VALUES (:bc, :name, :brand, :image_url, 'open_food_facts')
            RETURNING *
        """),
        {
            "bc": barcode,
            "name": off["name"],
            "brand": off["brand"],
            "image_url": off["image_url"],
        },
    )
    await db.commit()
    return dict(new_id.mappings().first())


@router.get("/{barcode}")
async def scan_barcode(
    barcode: str,
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(5.0, ge=0.5, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Scansione barcode: restituisce il prodotto e i prezzi nei negozi vicini."""
    product = await _get_or_create_product(barcode, db)

    if not product:
        return {"product": None, "prices": [], "message": "Prodotto non trovato"}

    # Regole di serving allineate agli altri percorsi (products.py /search,
    # offers.py /nearby): NOT quarantined, prezzo minimo valido, freschezza
    # per catena. Era l'unico endpoint che non filtrava nulla, e con
    # l'etichetta di provenienza accanto una riga messa in quarantena o
    # vecchia di mesi diventava il prezzo piu' basso dell'elenco, dichiarato e
    # tracciato, davanti a un utente che ha la confezione in mano.
    params: dict = {
        "pid": str(product["id"]),
        "lat": lat,
        "lng": lng,
        "radius_m": radius_km * 1000,
        "min_valid_price": MIN_VALID_PRICE,
    }
    fresh = fresh_price_sql(params, price_alias="p", chain_alias="c")

    prices_res = await db.execute(
        text(f"""
            SELECT
                p.price, p.original_price, p.promo_label, p.in_stock,
                p.source, p.promo_expires, p.scraped_at,
                s.id   AS store_id,   s.name  AS store_name, s.address,
                s.has_delivery, s.has_click_collect,
                c.name AS chain_name, c.slug  AS chain_slug, c.shop_url,
                ROUND(ST_Distance(
                    s.coordinates::geography,
                    ST_Point(:lng, :lat)::geography
                )::numeric / 1000, 2) AS distance_km
            FROM prices p
            JOIN stores s ON p.store_id  = s.id
            JOIN chains c ON s.chain_id  = c.id
            WHERE p.product_id = :pid
              AND p.is_current  = TRUE
              AND NOT p.quarantined
              AND p.price >= :min_valid_price
              AND {fresh}
              AND s.is_active   = TRUE
              AND ST_DWithin(
                    s.coordinates::geography,
                    ST_Point(:lng, :lat)::geography,
                    :radius_m
                  )
            ORDER BY p.price
            LIMIT 20
        """),
        params,
    )

    prezzi = [dict(r) for r in prices_res.mappings().all()]

    # Senza promo_expires nel SELECT una riga esselunga_web ereditata da
    # volantino risulterebbe "prezzo spesa online": e' esattamente l'errore che
    # price_origin esiste per evitare. Qui pesa doppio, perche' l'utente e'
    # fisicamente in negozio col telefono in mano e scambiare un prezzo
    # volantino per un prezzo di scaffale gli costa subito.
    for p in prezzi:
        p["origine"] = origine_di(p)
        p["origine_label"] = descrizione_di(p)
        # La riga porta original_price (vedi il SELECT), quindi la card puo'
        # disegnare un prezzo barrato: il permesso di disegnarlo e'
        # `sconto_affermabile`, ed e' PriceCard a leggerlo. Senza il campo
        # quella card non mostra nessun barrato — prudente ma muto proprio
        # dove l'utente e' in corsia e lo sconto, quando c'e', e' la cosa che
        # sta cercando.
        p["sconto_affermabile"] = sconto_affermabile_di(p)

    return {
        "product": product,
        "prices": prezzi,
        "avviso": avviso_origini([p["origine"] for p in prezzi]),
    }


@router.post("/{barcode}/price", status_code=201)
async def submit_price(
    barcode: str,
    body: PriceSubmit,
    db: AsyncSession = Depends(get_db),
):
    """
    Crowdsourcing prezzi: salva il prezzo che l'utente ha visto in negozio.
    Crea automaticamente il prodotto nel DB (via Open Food Facts) se non esiste ancora.
    """
    # 1. Prodotto — crea se mancante
    product = await _get_or_create_product(barcode, db)
    if not product:
        raise HTTPException(
            status_code=404,
            detail="Barcode non riconosciuto — aggiungi prima il prodotto su Open Food Facts",
        )

    # 2. Negozio esistente
    store_res = await db.execute(
        text("SELECT id FROM stores WHERE id = :sid AND is_active = TRUE"),
        {"sid": body.store_id},
    )
    if not store_res.mappings().first():
        raise HTTPException(status_code=404, detail="Negozio non trovato")

    prod_id = str(product["id"])

    # 3. Disattiva prezzo corrente per questo prodotto+negozio
    await db.execute(
        text("""
            UPDATE prices
            SET is_current = FALSE
            WHERE product_id = :pid AND store_id = :sid AND is_current = TRUE
        """),
        {"pid": prod_id, "sid": body.store_id},
    )

    # 4. Inserisci nuovo prezzo
    await db.execute(
        text("""
            INSERT INTO prices
                (product_id, store_id, price, in_stock, is_current, source, scraped_at)
            VALUES
                (:pid, :sid, :price, TRUE, TRUE, 'user_scan', :now)
        """),
        {
            "pid": prod_id,
            "sid": body.store_id,
            "price": body.price,
            "now": datetime.now(timezone.utc),
        },
    )
    await db.commit()

    # 5. Statistiche di confronto.
    # Min/media/max sono il giudizio che l'utente legge in corsia col prodotto
    # in mano: le regole di serving sono quelle della GET qui sopra e del resto
    # dell'app (NOT quarantined, prezzo minimo valido, negozi attivi,
    # freschezza per catena). Senza quelle clausole il denominatore conteneva
    # righe di negozi disattivati e cataloghi fermi da settimane (misurato nel
    # collaudo: 1.827.246 righe eurospin_web correnti e ZERO fresche), quindi
    # "sotto la media del 12,3%" non descriveva nessun mercato reale.
    stats_params: dict = {"pid": prod_id, "min_valid_price": MIN_VALID_PRICE}
    fresh_stats = fresh_price_sql(stats_params, price_alias="pr", chain_alias="c")
    _WHERE_STATS = f"""
            FROM prices pr
            JOIN stores s ON s.id = pr.store_id
            JOIN chains c ON c.id = s.chain_id
            WHERE pr.product_id = :pid
              AND pr.is_current = TRUE
              AND NOT pr.quarantined
              AND pr.price >= :min_valid_price
              AND s.is_active = TRUE
              AND {fresh_stats}
    """
    stats_res = await db.execute(
        text(f"""
            SELECT
                COUNT(*)                           AS store_count,
                MIN(pr.price)                      AS price_min,
                MAX(pr.price)                      AS price_max,
                ROUND(AVG(pr.price)::numeric, 2)   AS price_avg
            {_WHERE_STATS}
        """),
        stats_params,
    )
    stats = dict(stats_res.mappings().first())

    # Le origini si decidono per RIGA, non per sola sorgente: era
    # array_agg(DISTINCT source), e su una sorgente che contiene sia prezzi di
    # scaffale sia offerte con prezzo barrato il set collassava a un elemento.
    # Qui le combinazioni DISTINTE dei campi che decidono l'origine, sulle
    # STESSE righe che compongono min/media/max: original_price e' portato solo
    # quando e' una prova (> price), cosi' le combinazioni restano poche.
    combo_res = await db.execute(
        text(f"""
            SELECT DISTINCT
                   pr.source,
                   pr.promo_expires,
                   CASE WHEN pr.original_price > pr.price
                        THEN pr.original_price END AS original_price,
                   NULLIF(TRIM(pr.promo_label), '') AS promo_label
            {_WHERE_STATS}
        """),
        stats_params,
    )
    origini_stats = [origine_di(r) for r in combo_res.mappings().all()]

    price_avg = float(stats["price_avg"] or body.price)
    delta_pct = round((body.price - price_avg) / price_avg * 100, 1) if price_avg else 0

    return {
        "saved": True,
        "product": {
            "id": prod_id,
            "name": product["name"],
            "barcode": barcode,
        },
        "submitted_price": body.price,
        "comparison": {
            "store_count": stats["store_count"],
            "price_min": float(stats["price_min"] or body.price),
            "price_max": float(stats["price_max"] or body.price),
            "price_avg": price_avg,
            "delta_pct": delta_pct,
            "vs_avg": (
                "uguale alla media"
                if abs(delta_pct) < 1
                else f"{'sopra' if delta_pct > 0 else 'sotto'} la media del {abs(delta_pct):.1f}%"
            ),
            # price_avg e' la media fra listini online e offerte da volantino:
            # non e' una grandezza che significhi qualcosa, e vs_avg e' testo
            # finito che l'utente legge come un giudizio. L'avviso va accanto
            # alla frase, non solo dentro i dati — e calcolato sulle righe che
            # stanno DAVVERO dentro quei numeri, non su un insieme piu' ampio.
            "avviso": avviso_origini(origini_stats),
        },
    }
