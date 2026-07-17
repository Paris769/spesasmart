"""
Endpoint promo score (Fase 2): GET /api/v1/promo/{product_id}.

Espone il calcolo di app/services/promo.py: per ogni negozio dove il prodotto
risulta in promozione, dice se la promo e' vera (sotto il prezzo abituale) o
gonfiata. Cache 1h: lo storico prezzi cambia al massimo una volta al giorno.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.promo import compute_promo_check

router = APIRouter(prefix="/promo", tags=["promo"])


@router.get("/{product_id}")
async def get_promo_check(product_id: str, response: Response, db: AsyncSession = Depends(get_db)):
    try:
        pid = str(UUID(product_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="product_id non valido")

    product = await db.execute(text("SELECT id FROM products WHERE id = :pid"), {"pid": pid})
    if not product.first():
        raise HTTPException(status_code=404, detail="Prodotto non trovato")

    checks = await compute_promo_check(db, pid)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return {"product_id": pid, "checks": checks}
