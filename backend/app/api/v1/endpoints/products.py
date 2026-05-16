from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db

router = APIRouter(prefix="/products", tags=["products"])


@router.get("/search")
async def search_products(
    q: Optional[str] = Query(None, min_length=2),
    barcode: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    lat: Optional[float] = Query(None),
    lng: Optional[float] = Query(None),
    radius_km: float = Query(5.0, ge=0.5, le=50),
    limit: int = Query(20, le=100),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
):
    if barcode:
        result = await db.execute(
            text("SELECT * FROM products WHERE barcode = :barcode LIMIT 1"),
            {"barcode": barcode},
        )
        row = result.mappings().first()
        return [dict(row)] if row else []

    if not q:
        raise HTTPException(status_code=400, detail="Fornire q o barcode")

    q_lower = q.lower()
    filters = ["TRUE"]
    params: dict = {
        "q": q,
        "q_like": f"%{q}%",
        "q_lower": q_lower,
        "q_lower_start": q_lower + " %",
        "q_lower_mid": "% " + q_lower + " %",
        "q_lower_end": "% " + q_lower,
        "q_tsquery": q_lower,
        "limit": limit,
        "offset": offset,
    }

    if category_id:
        filters.append("p.category_id = :category_id")
        params["category_id"] = category_id

    # Filtro geografico opzionale per il prezzo mostrato nei risultati.
    # I negozi virtuali della spesa online (external_id '*-online') sono
    # nazionali: restano sempre visibili a prescindere dal raggio. Il raggio
    # filtra solo i punti vendita fisici (click & collect).
    price_geo = ""
    if lat is not None and lng is not None:
        price_geo = """
              AND (
                    s.external_id LIKE '%-online'
                    OR ST_DWithin(
                         s.coordinates::geography,
                         ST_Point(:lng, :lat)::geography,
                         :radius_m
                       )
                  )"""
        params["lat"] = lat
        params["lng"] = lng
        params["radius_m"] = radius_km * 1000

    where = " AND ".join(filters)
    result = await db.execute(
        text(f"""
            SELECT p.*,
                   CASE
                       WHEN lower(p.name) = :q_lower                THEN 4
                       WHEN lower(p.name) LIKE :q_lower_start       THEN 3
                       WHEN lower(p.name) LIKE :q_lower_mid
                         OR lower(p.name) LIKE :q_lower_end         THEN 2
                       ELSE 1
                   END AS word_rank,
                   ts_rank(
                       to_tsvector('simple', lower(p.name)),
                       plainto_tsquery('simple', :q_tsquery)
                   ) AS ts_score,
                   pr.min_price,
                   pr.store_count AS price_store_count
            FROM products p
            LEFT JOIN LATERAL (
                SELECT MIN(x.price)            AS min_price,
                       COUNT(DISTINCT x.store_id) AS store_count
                FROM prices x
                JOIN stores s ON x.store_id = s.id
                WHERE x.product_id = p.id
                  AND x.is_current = TRUE
                  AND s.is_active  = TRUE
                  {price_geo}
            ) pr ON TRUE
            WHERE {where} AND (
                to_tsvector('simple', lower(p.name || ' ' || COALESCE(p.brand, '')))
                    @@ plainto_tsquery('simple', :q_tsquery)
                OR p.name ILIKE :q_like
                OR p.brand ILIKE :q_like
            )
            ORDER BY
                word_rank DESC,
                ts_score   DESC,
                similarity(p.name, :q) DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    return [dict(r) for r in result.mappings().all()]


@router.get("/{product_id}/prices")
async def get_product_prices(
    product_id: str,
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(5.0, ge=0.5, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Prezzi del prodotto nei negozi vicini, ordinati per prezzo crescente."""
    result = await db.execute(
        text("""
            SELECT
                p.price, p.original_price, p.promo_label,
                p.price_per_unit, p.in_stock, p.scraped_at,
                s.id AS store_id, s.name AS store_name,
                s.address, s.city, s.has_delivery, s.has_click_collect,
                c.name  AS chain_name,
                c.slug  AS chain_slug,
                c.shop_url,
                (s.external_id LIKE '%-online') AS is_online,
                CASE
                    WHEN s.external_id LIKE '%-online' THEN NULL
                    ELSE ROUND(ST_Distance(
                        s.coordinates::geography,
                        ST_Point(:lng, :lat)::geography
                    )::numeric / 1000, 2)
                END AS distance_km
            FROM prices p
            JOIN stores s  ON p.store_id  = s.id
            JOIN chains c  ON s.chain_id  = c.id
            WHERE p.product_id = :product_id
              AND p.is_current  = TRUE
              AND s.is_active   = TRUE
              AND (
                    s.external_id LIKE '%-online'
                    OR ST_DWithin(
                         s.coordinates::geography,
                         ST_Point(:lng, :lat)::geography,
                         :radius_m
                       )
                  )
            ORDER BY p.price ASC
            LIMIT 30
        """),
        {"product_id": product_id, "lat": lat, "lng": lng, "radius_m": radius_km * 1000},
    )
    return [dict(r) for r in result.mappings().all()]


@router.get("/{product_id}/price-history")
async def get_price_history(
    product_id: str,
    store_id: Optional[str] = Query(None),
    days: int = Query(90, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
):
    filters = ["product_id = :product_id", "scraped_at > NOW() - make_interval(days => :days)"]
    params: dict = {"product_id": product_id, "days": days}

    if store_id:
        filters.append("store_id = :store_id")
        params["store_id"] = store_id

    result = await db.execute(
        text(f"""
            SELECT store_id, price, scraped_at
            FROM prices
            WHERE {" AND ".join(filters)}
            ORDER BY scraped_at
        """),
        params,
    )
    return [dict(r) for r in result.mappings().all()]


@router.get("/{product_id}")
async def get_product(product_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT * FROM products WHERE id = :id"),
        {"id": product_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Prodotto non trovato")
    return dict(row)
