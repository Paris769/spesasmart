"""
Ricostruzione della spesa con le offerte: POST /api/v1/rebuild/from-items.

"Fammi una spesa con i prodotti della mia ultima spesa, ma scegliendo quelli in
offerta e mantenendo il piu' possibile le marche che compro": questo endpoint
e' l'ingresso HTTP del motore in app/services/offers_rebuild.py. Lo chiamano
l'assistente AI e lo storico spese, che gli passano le voci gia' estratte
(nome, marca, product_id quando l'hanno).

CONTRATTO
---------
Richiesta:
    {
      "items": [{"name": str, "brand": str|null,
                 "product_id": uuid|null, "quantity": float}],   # max 60
      "lat": float, "lng": float,
      "radius_km": float = 10,          # 0.5 - 50
      "chain_slug": str|null,           # limita la spesa a una sola catena
      "keep_brand": bool = true         # false = accetta il cambio marca se
                                        #         il risparmio e' sensibile
    }

Risposta:
    {
      "items": [{
        "query_name": str,
        "original": {"product_id", "name", "brand", "price"},
        "chosen":   {"product_id", "name", "brand", "image_url", "price",
                     "original_price", "discount_pct", "price_per_unit",
                     "promo_label", "promo_expires", "chain_slug",
                     "chain_name", "store_id", "store_name",
                     "distance_km"} | null,
        "match_kind": "same_product_on_offer" | "same_brand_on_offer"
                    | "similar_on_offer" | "no_offer_same_product"
                    | "not_found",
        "saving_vs_original": float|null,
        "brand_kept": bool,
        "promo_verdict": "true_promo"|"weak_promo"|"fake_promo"
                       |"insufficient_history"|null,
        "note": str            # frase breve in italiano, pronta da mostrare
      }],
      "summary": {"items_total", "on_offer_count", "brand_kept_count",
                  "brand_changed_count", "not_found_count",
                  "total_estimated", "total_without_offers",
                  "estimated_saving"}
    }

`distance_km` e' null per gli store online nazionali e per i volantini: non
hanno una distanza (vedi core/geo_coverage).

Cache-Control: no-store. La risposta dipende dalla spesa passata dell'utente e
dalla sua posizione: non va messa in nessuna cache condivisa.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.offers_rebuild import MAX_ITEMS, rebuild_with_offers

router = APIRouter(prefix="/rebuild", tags=["rebuild"])


class RebuildItem(BaseModel):
    name: str
    brand: Optional[str] = None
    product_id: Optional[str] = None
    quantity: float = 1.0


class RebuildRequest(BaseModel):
    items: list[RebuildItem]
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(10.0, ge=0.5, le=50)
    chain_slug: Optional[str] = None
    keep_brand: bool = True


@router.post("/from-items")
async def rebuild_from_items(
    body: RebuildRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Sostituisce ogni voce della spesa con la migliore offerta compatibile."""
    # Dipende dai dati della spesa dell'utente: mai in cache.
    response.headers["Cache-Control"] = "no-store"

    items = [it for it in body.items if (it.name or "").strip()]
    if not items:
        raise HTTPException(status_code=400, detail="Fornire almeno una voce valida")
    if len(items) > MAX_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"Massimo {MAX_ITEMS} voci per richiesta (ricevute {len(items)})",
        )

    payload: list[dict] = []
    for it in items:
        pid: Optional[str] = None
        if it.product_id:
            try:
                pid = str(UUID(str(it.product_id)))
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=422,
                    detail=f"product_id non valido per la voce '{it.name}'",
                )
        payload.append({
            "name": it.name.strip(),
            "brand": (it.brand or "").strip() or None,
            "product_id": pid,
            # Quantita' negative o nulle non hanno senso in una spesa: si
            # normalizzano a 1 invece di rifiutare l'intera richiesta.
            "quantity": it.quantity if it.quantity and it.quantity > 0 else 1.0,
        })

    chain = (body.chain_slug or "").strip().lower() or None

    return await rebuild_with_offers(
        db,
        payload,
        lat=body.lat,
        lng=body.lng,
        radius_km=body.radius_km,
        chain_slug=chain,
        keep_brand=body.keep_brand,
    )
