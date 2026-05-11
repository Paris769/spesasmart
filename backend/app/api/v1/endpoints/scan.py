from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

router = APIRouter(prefix="/scan", tags=["scan"])


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

    prices_res = await db.execute(
        text("""
            SELECT
                p.price, p.original_price, p.promo_label, p.in_stock,
                p.source, p.scraped_at,
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
              AND s.is_active   = TRUE
              AND ST_DWithin(
                    s.coordinates::geography,
                    ST_Point(:lng, :lat)::geography,
                    :radius_m
                  )
            ORDER BY p.price
            LIMIT 20
        """),
        {"pid": str(product["id"]), "lat": lat, "lng": lng, "radius_m": radius_km * 1000},
    )

    return {
        "product": product,
        "prices": [dict(r) for r in prices_res.mappings().all()],
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

    # 5. Statistiche di confronto (tutti i prezzi attuali per questo prodotto)
    stats_res = await db.execute(
        text("""
            SELECT
                COUNT(*)                        AS store_count,
                MIN(price)                      AS price_min,
                MAX(price)                      AS price_max,
                ROUND(AVG(price)::numeric, 2)   AS price_avg
            FROM prices
            WHERE product_id = :pid AND is_current = TRUE
        """),
        {"pid": prod_id},
    )
    stats = dict(stats_res.mappings().first())

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
        },
    }
