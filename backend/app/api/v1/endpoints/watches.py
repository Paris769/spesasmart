"""
Price watch anonimi via email (Fase 2).

La tabella users e' vuota e non c'e' login: l'email e' la chiave. I watch sono
righe di price_alerts con user_id NULL + email valorizzata. Il digest
settimanale (scripts/weekly_digest.py) valuta i watch attivi e notifica quando
il miglior prezzo corrente scende sotto la soglia.
"""
import re
import time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.freshness import fresh_price_sql
from app.db.session import get_db

router = APIRouter(prefix="/watches", tags=["watches"])

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
MAX_WATCHES_PER_EMAIL = 20
MIN_VALID_PRICE = 0.10

# Regola di serving (vedi core/freshness): nel miglior prezzo corrente entrano
# solo prezzi non in quarantena, sopra la soglia minima, di negozi attivi e
# abbastanza freschi per la loro catena. Frammento e bind param deterministici,
# costruiti una volta a livello di modulo.
_FRESH_PARAMS: dict = {}
_FRESH_SQL = fresh_price_sql(_FRESH_PARAMS, price_alias="pr", chain_alias="c")

# Rate limit semplice in-memory: finestra scorrevole di 1h sulle POST per email.
# Protegge da abusi senza infrastruttura extra; il limite "duro" (20 watch per
# email) e' comunque verificato sul DB, cosi' sopravvive ai riavvii.
_RATE_WINDOW_S = 3600
_RATE_MAX_REQUESTS = 30
_rate_buckets: dict[str, list[float]] = {}


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _validate_email(email: str) -> str:
    email = _normalize_email(email)
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Email non valida")
    return email


def _check_rate_limit(email: str) -> None:
    now = time.monotonic()
    bucket = [t for t in _rate_buckets.get(email, []) if now - t < _RATE_WINDOW_S]
    if len(bucket) >= _RATE_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="Troppe richieste, riprova piu' tardi")
    bucket.append(now)
    _rate_buckets[email] = bucket
    # pulizia opportunistica per non far crescere il dict all'infinito
    if len(_rate_buckets) > 5000:
        for k in [k for k, v in _rate_buckets.items() if not v or now - v[-1] > _RATE_WINDOW_S]:
            _rate_buckets.pop(k, None)


class WatchCreate(BaseModel):
    product_id: str
    email: str
    threshold_price: Optional[float] = None


@router.post("/", status_code=201)
@router.post("", status_code=201, include_in_schema=False)
async def create_watch(body: WatchCreate, db: AsyncSession = Depends(get_db)):
    email = _validate_email(body.email)
    _check_rate_limit(email)

    try:
        pid = str(UUID(body.product_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="product_id non valido")

    if body.threshold_price is not None and body.threshold_price <= 0:
        raise HTTPException(status_code=422, detail="threshold_price deve essere positivo")

    product = await db.execute(
        text("SELECT id FROM products WHERE id = :pid"), {"pid": pid}
    )
    if not product.first():
        raise HTTPException(status_code=404, detail="Prodotto non trovato")

    # Dedup: stesso email+prodotto -> aggiorna la soglia invece di duplicare.
    existing = await db.execute(
        text("""
            SELECT id FROM price_alerts
            WHERE lower(email) = :email AND product_id = :pid
            LIMIT 1
        """),
        {"email": email, "pid": pid},
    )
    row = existing.mappings().first()
    if row:
        result = await db.execute(
            text("""
                UPDATE price_alerts
                SET threshold_price = :thr, is_active = TRUE
                WHERE id = :id
                RETURNING id, product_id, email, threshold_price
            """),
            {"id": str(row["id"]), "thr": body.threshold_price},
        )
        await db.commit()
        return dict(result.mappings().first())

    # Quota per email (limite persistente, non solo in-memory)
    count = await db.execute(
        text("SELECT count(*) AS n FROM price_alerts WHERE lower(email) = :email AND is_active = TRUE"),
        {"email": email},
    )
    if (count.mappings().first() or {}).get("n", 0) >= MAX_WATCHES_PER_EMAIL:
        raise HTTPException(status_code=429, detail=f"Massimo {MAX_WATCHES_PER_EMAIL} watch per email")

    result = await db.execute(
        text("""
            INSERT INTO price_alerts (user_id, product_id, email, threshold_price, is_active)
            VALUES (NULL, :pid, :email, :thr, TRUE)
            RETURNING id, product_id, email, threshold_price
        """),
        {"pid": pid, "email": email, "thr": body.threshold_price},
    )
    await db.commit()
    return dict(result.mappings().first())


@router.get("/")
@router.get("", include_in_schema=False)
async def list_watches(email: str = Query(...), db: AsyncSession = Depends(get_db)):
    email = _validate_email(email)
    result = await db.execute(
        text(f"""
            SELECT a.id, a.product_id, p.name AS product_name,
                   a.threshold_price, a.is_active, a.created_at,
                   (
                       SELECT MIN(pr.price)
                       FROM prices pr
                       JOIN stores s ON s.id = pr.store_id
                       JOIN chains c ON s.chain_id = c.id
                       WHERE pr.product_id = a.product_id
                         AND pr.is_current = TRUE
                         AND pr.quarantined = FALSE
                         AND pr.price >= :min_valid_price
                         AND s.is_active = TRUE
                         AND {_FRESH_SQL}
                   ) AS current_min_price
            FROM price_alerts a
            JOIN products p ON p.id = a.product_id
            WHERE lower(a.email) = :email
            ORDER BY a.created_at DESC
        """),
        {"email": email, "min_valid_price": MIN_VALID_PRICE, **_FRESH_PARAMS},
    )
    return {"watches": [dict(r) for r in result.mappings().all()]}


@router.delete("/{watch_id}", status_code=204)
async def delete_watch(watch_id: str, email: str = Query(...), db: AsyncSession = Depends(get_db)):
    email = _validate_email(email)
    try:
        wid = str(UUID(watch_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="id non valido")

    result = await db.execute(
        text("DELETE FROM price_alerts WHERE id = :id AND lower(email) = :email RETURNING id"),
        {"id": wid, "email": email},
    )
    if not result.first():
        # Non riveliamo se il watch esiste per un'altra email
        raise HTTPException(status_code=404, detail="Watch non trovato")
    await db.commit()
