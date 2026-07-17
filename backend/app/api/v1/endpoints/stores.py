from typing import Optional
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.freshness import fresh_price_sql
from app.db.session import get_db

router = APIRouter(prefix="/stores", tags=["stores"])

MIN_VALID_PRICE = 0.10  # come in products.py/lists.py: sotto e' placeholder/errore

# Soglie tier copertura catalogo per catena
_TIER_FULL_MIN_PRODUCTS = 5000
_TIER_PROMO_MIN_PRODUCTS = 100


def _coverage_tier(products_with_current_price: int) -> str:
    if products_with_current_price >= _TIER_FULL_MIN_PRODUCTS:
        return "full"
    if products_with_current_price >= _TIER_PROMO_MIN_PRODUCTS:
        return "promo"
    return "none"


@router.get("/nearby")
async def get_nearby_stores(
    response: Response,
    lat: float = Query(..., description="Latitudine utente"),
    lng: float = Query(..., description="Longitudine utente"),
    radius_km: float = Query(5.0, ge=0.5, le=50, description="Raggio in km"),
    chain_id: Optional[int] = Query(None),
    has_delivery: Optional[bool] = Query(None),
    has_click_collect: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    # I negozi cambiano molto raramente: cache 10 min (browser + React Query).
    response.headers["Cache-Control"] = "public, max-age=600"

    filters = ["s.is_active = TRUE"]
    params: dict = {"lat": lat, "lng": lng, "radius_m": radius_km * 1000}

    if chain_id:
        filters.append("s.chain_id = :chain_id")
        params["chain_id"] = chain_id
    if has_delivery is not None:
        filters.append("s.has_delivery = :has_delivery")
        params["has_delivery"] = has_delivery
    if has_click_collect is not None:
        filters.append("s.has_click_collect = :has_cc")
        params["has_cc"] = has_click_collect

    where = " AND ".join(filters)

    query = text(f"""
        SELECT
            s.id, s.name, s.address, s.city, s.province,
            s.has_delivery, s.has_click_collect,
            c.name  AS chain_name,
            c.slug  AS chain_slug,
            c.has_online_shop,
            c.shop_url,
            ROUND(ST_Distance(
                s.coordinates::geography,
                ST_Point(:lng, :lat)::geography
            )::numeric / 1000, 2) AS distance_km
        FROM stores s
        JOIN chains c ON s.chain_id = c.id
        WHERE {where}
          AND ST_DWithin(
                s.coordinates::geography,
                ST_Point(:lng, :lat)::geography,
                :radius_m
              )
        ORDER BY distance_km
        LIMIT 50
    """)

    result = await db.execute(query, params)
    rows = result.mappings().all()
    return [dict(r) for r in rows]


# NB: dichiarato PRIMA di /{store_id}, altrimenti "coverage" verrebbe
# interpretato come store_id.
@router.get("/coverage")
async def get_chain_coverage(response: Response, db: AsyncSession = Depends(get_db)):
    """
    Copertura dati per catena: quanti prodotti hanno un prezzo corrente
    servibile (non in quarantena, non stantio), quanti punti vendita fisici
    esistono, quando e' avvenuto l'ultimo scrape e il tier di copertura:
    "full" (catalogo), "promo" (solo volantino/promozioni), "none".
    """
    response.headers["Cache-Control"] = "public, max-age=3600"

    params: dict = {"min_valid_price": MIN_VALID_PRICE}
    fresh = fresh_price_sql(params, price_alias="p", chain_alias="c2")

    # Singola query aggregata (niente N+1). I due LEFT JOIN pre-aggregati
    # (negozi fisici, prezzi correnti) evitano il COUNT DISTINCT sul prodotto
    # cartesiano chains x stores x prices (misurato: ~2.5x piu' veloce).
    # last_scraped_at e' il MAX su tutti i prezzi correnti della catena
    # (anche stantii/quarantinati: indica l'ultima attivita' di scrape);
    # products_with_current_price conta solo i prezzi servibili.
    result = await db.execute(
        text(f"""
            SELECT
                c.slug,
                c.name,
                c.has_online_shop,
                COALESCE(st.physical_stores, 0) AS physical_stores,
                COALESCE(pa.products_with_current_price, 0) AS products_with_current_price,
                pa.last_scraped_at
            FROM chains c
            LEFT JOIN (
                SELECT chain_id, COUNT(*) AS physical_stores
                FROM stores
                WHERE is_active AND external_id NOT LIKE '%-online'
                GROUP BY chain_id
            ) st ON st.chain_id = c.id
            LEFT JOIN (
                SELECT s.chain_id,
                       COUNT(DISTINCT p.product_id) FILTER (
                           WHERE NOT p.quarantined
                             AND p.price >= :min_valid_price
                             AND {fresh}
                       ) AS products_with_current_price,
                       MAX(p.scraped_at) AS last_scraped_at
                FROM prices p
                JOIN stores s ON p.store_id = s.id
                JOIN chains c2 ON s.chain_id = c2.id
                WHERE p.is_current AND s.is_active
                GROUP BY s.chain_id
            ) pa ON pa.chain_id = c.id
            ORDER BY products_with_current_price DESC, c.name
        """),
        params,
    )

    chains = []
    for r in result.mappings().all():
        n_products = int(r["products_with_current_price"] or 0)
        chains.append({
            "slug": r["slug"],
            "name": r["name"],
            "products_with_current_price": n_products,
            "physical_stores": int(r["physical_stores"] or 0),
            "has_online_shop": bool(r["has_online_shop"]),
            "last_scraped_at": r["last_scraped_at"].isoformat() if r["last_scraped_at"] else None,
            "tier": _coverage_tier(n_products),
        })
    return {"chains": chains}


@router.get("/{store_id}")
async def get_store(store_id: str, db: AsyncSession = Depends(get_db)):
    query = text("""
        SELECT s.*, c.name AS chain_name, c.slug AS chain_slug,
               c.has_online_shop, c.shop_url
        FROM stores s JOIN chains c ON s.chain_id = c.id
        WHERE s.id = :store_id
    """)
    result = await db.execute(query, {"store_id": store_id})
    row = result.mappings().first()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Negozio non trovato")
    return dict(row)
