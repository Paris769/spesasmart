"""
Offerte vicino a te: GET /api/v1/offers/nearby.

Aggrega le migliori promozioni correnti nella zona dell'utente: prezzi con
sconto dichiarato (original_price > price), con etichetta promo, oppure righe
caricate dai volantini (source='flyer'). Entrano i negozi fisici entro il
raggio piu' gli store online nazionali (solo se la catena serve la zona, vedi
core/geo_coverage). Una sola offerta - la migliore - per coppia
(prodotto, catena).

Regole di serving identiche al resto dell'app: is_current, NOT quarantined,
prezzo minimo valido, store attivi, freshness per catena (core/freshness).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.freshness import fresh_price_sql
from app.core.geo_coverage import unavailable_online_chains
from app.db.session import get_db

router = APIRouter(prefix="/offers", tags=["offers"])

MIN_VALID_PRICE = 0.10  # come in products.py/stores.py: sotto e' placeholder/errore


@router.get("/nearby")
async def get_nearby_offers(
    response: Response,
    lat: float = Query(..., description="Latitudine utente"),
    lng: float = Query(..., description="Longitudine utente"),
    radius_km: float = Query(5.0, ge=0.5, le=50, description="Raggio in km"),
    limit: int = Query(60, ge=1, le=120),
    chain: Optional[str] = Query(None, description="Filtra per slug catena"),
    db: AsyncSession = Depends(get_db),
):
    # Le promo cambiano poche volte al giorno: cache 15 minuti.
    response.headers["Cache-Control"] = "public, max-age=900"

    params: dict = {
        "lat": lat,
        "lng": lng,
        "radius_m": radius_km * 1000,
        "limit": limit,
        "chain": chain,
        "min_valid_price": MIN_VALID_PRICE,
        # catene con spesa online che NON servono la zona: escluse
        "no_online": unavailable_online_chains(lat, lng),
    }
    # La CTE nearby_stores espone lo slug della catena come colonna "slug",
    # cosi' fresh_price_sql puo' usarla come alias catena (ns.slug).
    fresh = fresh_price_sql(params, price_alias="p", chain_alias="ns")

    result = await db.execute(
        text(f"""
            WITH nearby_stores AS MATERIALIZED (
                SELECT s.id,
                       s.name AS store_name,
                       c.id   AS chain_id,
                       c.slug,
                       c.name AS chain_name,
                       CASE
                           WHEN s.external_id LIKE '%-online' THEN NULL
                           ELSE ROUND(ST_Distance(
                               s.coordinates::geography,
                               ST_Point(:lng, :lat)::geography
                           )::numeric / 1000, 2)
                       END AS distance_km
                FROM stores s
                JOIN chains c ON s.chain_id = c.id
                WHERE s.is_active = TRUE
                  AND (CAST(:chain AS text) IS NULL OR c.slug = :chain)
                  AND (
                        (s.external_id LIKE '%-online'
                         AND NOT (c.slug = ANY(string_to_array(:no_online, ','))))
                        OR ST_DWithin(
                             s.coordinates::geography,
                             ST_Point(:lng, :lat)::geography,
                             :radius_m
                           )
                      )
            ),
            best AS (
                -- migliore offerta per coppia (prodotto, catena): sconto piu'
                -- alto, a parita' di sconto il prezzo piu' basso
                SELECT DISTINCT ON (p.product_id, ns.chain_id)
                       p.product_id,
                       ns.slug AS chain_slug,
                       ns.chain_name,
                       ns.store_name,
                       ns.distance_km,
                       p.price,
                       p.original_price,
                       CASE
                           WHEN p.original_price > p.price
                           THEN (ROUND(
                               (p.original_price - p.price)
                               / p.original_price * 100
                           ))::int
                       END AS discount_pct,
                       NULLIF(TRIM(p.promo_label), '') AS promo_label,
                       p.promo_expires,
                       p.source,
                       p.price_per_unit
                FROM prices p
                JOIN nearby_stores ns ON p.store_id = ns.id
                WHERE p.is_current = TRUE
                  AND NOT p.quarantined
                  AND p.price >= :min_valid_price
                  AND {fresh}
                  AND (p.promo_expires IS NULL OR p.promo_expires >= CURRENT_DATE)
                  -- Prima clausola: identica al predicato del futuro indice
                  -- parziale promo (il planner non sa dimostrare implicazioni
                  -- attraverso NULLIF/TRIM, quindi va scritta verbatim).
                  AND (
                        p.original_price > p.price
                        OR p.promo_label IS NOT NULL
                        OR p.source = 'flyer'
                      )
                  -- Seconda clausola (piu' stretta): etichette vuote ('') non
                  -- contano come promo (es. carrefour_web ne ha migliaia).
                  AND (
                        p.original_price > p.price
                        OR NULLIF(TRIM(p.promo_label), '') IS NOT NULL
                        OR p.source = 'flyer'
                      )
                ORDER BY p.product_id, ns.chain_id,
                         (CASE WHEN p.original_price > p.price
                               THEN (p.original_price - p.price) / p.original_price
                          END) DESC NULLS LAST,
                         p.price ASC
            ),
            top AS (
                SELECT * FROM best
                ORDER BY discount_pct DESC NULLS LAST, price ASC
                LIMIT :limit
            )
            -- il join con products avviene SOLO sulle righe finali (<= limit):
            -- evita migliaia di lookup inutili sulla tabella prodotti
            SELECT t.product_id::text AS product_id,
                   pr.name AS product_name,
                   pr.brand,
                   pr.image_url,
                   t.chain_slug,
                   t.chain_name,
                   t.store_name,
                   t.distance_km,
                   t.price,
                   t.original_price,
                   t.discount_pct,
                   t.promo_label,
                   t.promo_expires,
                   t.source,
                   t.price_per_unit
            FROM top t
            JOIN products pr ON pr.id = t.product_id
            ORDER BY t.discount_pct DESC NULLS LAST, t.price ASC
        """),
        params,
    )
    return [dict(r) for r in result.mappings().all()]
