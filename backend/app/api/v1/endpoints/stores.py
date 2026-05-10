from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db

router = APIRouter(prefix="/stores", tags=["stores"])


@router.get("/nearby")
async def get_nearby_stores(
    lat: float = Query(..., description="Latitudine utente"),
    lng: float = Query(..., description="Longitudine utente"),
    radius_km: float = Query(5.0, ge=0.5, le=50, description="Raggio in km"),
    chain_id: Optional[int] = Query(None),
    has_delivery: Optional[bool] = Query(None),
    has_click_collect: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    filters = ["s.is_active = TRUE"]
    params: dict = {"lat": lat, "lng": lng, "radius_m": radius_km * 1000}

    if chain_id:
        filters.append("s.chain_id = :chain_id")
        params["chain_id"] = chain_id
    if has_delivery is not None:
        filters.append(f"s.has_delivery = {'TRUE' if has_delivery else 'FALSE'}")
    if has_click_collect is not None:
        filters.append(f"s.has_click_collect = {'TRUE' if has_click_collect else 'FALSE'}")

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
