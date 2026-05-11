from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db

router = APIRouter(prefix="/scan", tags=["scan"])


@router.get("/{barcode}")
async def scan_barcode(
    barcode: str,
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(5.0, ge=0.5, le=50),
    db: AsyncSession = Depends(get_db),
):
    """
    Scansione barcode: restituisce il prodotto e i prezzi nei negozi vicini.
    Se il prodotto non è nel db locale, cerca su Open Food Facts.
    """
    product_res = await db.execute(
        text("SELECT * FROM products WHERE barcode = :bc LIMIT 1"),
        {"bc": barcode},
    )
    product = product_res.mappings().first()

    if not product:
        # Fallback Open Food Facts
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            off_res = await client.get(
                f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
            )
        if off_res.status_code == 200:
            data = off_res.json()
            if data.get("status") == 1:
                p = data["product"]
                return {
                    "product": {
                        "barcode": barcode,
                        "name": p.get("product_name_it") or p.get("product_name", "Prodotto sconosciuto"),
                        "brand": p.get("brands", ""),
                        "image_url": p.get("image_url", ""),
                        "source": "open_food_facts",
                    },
                    "prices": [],
                    "message": "Prodotto trovato su Open Food Facts ma non ancora nei prezzi locali",
                }
        return {"product": None, "prices": [], "message": "Prodotto non trovato"}

    prices_res = await db.execute(
        text("""
            SELECT
                p.price, p.original_price, p.promo_label, p.in_stock, p.scraped_at,
                s.id AS store_id, s.name AS store_name, s.address,
                s.has_delivery, s.has_click_collect,
                c.name AS chain_name, c.slug AS chain_slug, c.shop_url,
                ROUND(ST_Distance(
                    s.coordinates::geography,
                    ST_Point(:lng, :lat)::geography
                )::numeric / 1000, 2) AS distance_km
            FROM prices p
            JOIN stores s ON p.store_id = s.id
            JOIN chains c ON s.chain_id = c.id
            WHERE p.product_id = :pid
              AND p.is_current = TRUE
              AND s.is_active  = TRUE
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
        "product": dict(product),
        "prices": [dict(r) for r in prices_res.mappings().all()],
    }
