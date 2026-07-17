"""
Spesa abituale (Fase 2): liste ricorrenti ancorate a un'email.

Nessuna auth: l'email e' la chiave (users e' vuota). Le liste vivono in
shopping_lists con is_recurring=TRUE e digest_email valorizzata; le voci in
list_items (product_id se la voce e' ancorata a un prodotto reale,
product_name = testo libero della query altrimenti).

REGOLA DI SICUREZZA: ogni query filtra SEMPRE per digest_email — mai
restituire o modificare liste di altri.
"""
import re
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

router = APIRouter(prefix="/recurring", tags=["recurring"])

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
MAX_ITEMS = 60
MAX_LISTS_PER_EMAIL = 10


def _validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Email non valida")
    return email


def _valid_uuid(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return str(UUID(value))
    except (ValueError, TypeError):
        return None


class RecurringItem(BaseModel):
    query: str
    quantity: float = 1
    product_id: Optional[str] = None


class RecurringCreate(BaseModel):
    email: str
    name: str
    items: list[RecurringItem]


class RecurringUpdate(BaseModel):
    email: str
    name: Optional[str] = None
    items: Optional[list[RecurringItem]] = None


def _clean_items(items: list[RecurringItem]) -> list[RecurringItem]:
    cleaned = [it for it in items if it.query and len(it.query.strip()) >= 2]
    if not cleaned:
        raise HTTPException(status_code=422, detail="Fornire almeno una voce valida")
    return cleaned[:MAX_ITEMS]


async def _insert_items(db: AsyncSession, list_id: str, items: list[RecurringItem]) -> int:
    # Ancoriamo solo prodotti realmente esistenti; altrimenti la voce resta
    # testuale (il digest la segnalera' come "da ancorare"). Una sola SELECT
    # per validare tutti gli id + un INSERT multi-riga: niente N+1.
    candidate_ids = sorted({pid for pid in (_valid_uuid(it.product_id) for it in items) if pid})
    existing_ids: set[str] = set()
    if candidate_ids:
        result = await db.execute(
            text("SELECT id::text FROM products WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": candidate_ids},
        )
        existing_ids = {r[0] for r in result.all()}

    rows = []
    for idx, it in enumerate(items):
        pid = _valid_uuid(it.product_id)
        if pid not in existing_ids:
            pid = None
        rows.append({
            "lid": list_id,
            "pid": pid,
            "pname": it.query.strip()[:500],
            "qty": max(float(it.quantity or 1), 0.01),
            "ord": idx,
        })
    await db.execute(
        text("""
            INSERT INTO list_items (list_id, product_id, product_name, quantity, sort_order)
            VALUES (:lid, :pid, :pname, :qty, :ord)
        """),
        rows,
    )
    return len(rows)


async def _get_owned_list(db: AsyncSession, list_id: str, email: str) -> dict:
    lid = _valid_uuid(list_id)
    if not lid:
        raise HTTPException(status_code=422, detail="id non valido")
    result = await db.execute(
        text("""
            SELECT id, name FROM shopping_lists
            WHERE id = :lid AND is_recurring = TRUE AND lower(digest_email) = :email
        """),
        {"lid": lid, "email": email},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Lista non trovata")
    return dict(row)


async def _read_list(db: AsyncSession, list_id: str) -> dict:
    """Rilegge una lista nella stessa shape di GET /recurring (il frontend
    ricostruisce l'editor dalla risposta di POST/PUT)."""
    lst = await db.execute(
        text("""
            SELECT id, name, created_at, last_digest_at
            FROM shopping_lists WHERE id = :lid
        """),
        {"lid": list_id},
    )
    row = dict(lst.mappings().first())
    items = await db.execute(
        text("""
            SELECT li.id, li.product_name, li.quantity, li.product_id,
                   p.name AS product_name_resolved, p.image_url
            FROM list_items li
            LEFT JOIN products p ON p.id = li.product_id
            WHERE li.list_id = :lid
            ORDER BY li.sort_order
        """),
        {"lid": list_id},
    )
    row["items"] = [
        {
            "id": i["id"],
            "query": i["product_name"],
            "quantity": float(i["quantity"]) if i["quantity"] is not None else 1,
            "product_id": i["product_id"],
            "product_name_resolved": i["product_name_resolved"],
            "image_url": i["image_url"],
        }
        for i in items.mappings().all()
    ]
    return row


@router.post("/", status_code=201)
@router.post("", status_code=201, include_in_schema=False)
async def create_recurring(body: RecurringCreate, db: AsyncSession = Depends(get_db)):
    email = _validate_email(body.email)
    name = (body.name or "").strip() or "Spesa abituale"
    items = _clean_items(body.items)

    count = await db.execute(
        text("""
            SELECT count(*) AS n FROM shopping_lists
            WHERE is_recurring = TRUE AND lower(digest_email) = :email
        """),
        {"email": email},
    )
    if (count.mappings().first() or {}).get("n", 0) >= MAX_LISTS_PER_EMAIL:
        raise HTTPException(status_code=429, detail=f"Massimo {MAX_LISTS_PER_EMAIL} liste ricorrenti per email")

    result = await db.execute(
        text("""
            INSERT INTO shopping_lists (user_id, name, is_recurring, digest_email)
            VALUES (NULL, :name, TRUE, :email)
            RETURNING id, name
        """),
        {"name": name[:200], "email": email},
    )
    lst = dict(result.mappings().first())
    await _insert_items(db, str(lst["id"]), items)
    await db.commit()
    return await _read_list(db, str(lst["id"]))


@router.get("/")
@router.get("", include_in_schema=False)
async def list_recurring(email: str = Query(...), db: AsyncSession = Depends(get_db)):
    email = _validate_email(email)
    lists = await db.execute(
        text("""
            SELECT id, name, created_at, last_digest_at
            FROM shopping_lists
            WHERE is_recurring = TRUE AND lower(digest_email) = :email
            ORDER BY created_at DESC
        """),
        {"email": email},
    )
    rows = [dict(r) for r in lists.mappings().all()]
    if not rows:
        return {"lists": []}

    items = await db.execute(
        text("""
            SELECT li.list_id, li.id, li.product_name, li.quantity, li.product_id,
                   p.name AS product_name_resolved, p.image_url
            FROM list_items li
            JOIN shopping_lists sl ON sl.id = li.list_id
            LEFT JOIN products p ON p.id = li.product_id
            WHERE sl.is_recurring = TRUE AND lower(sl.digest_email) = :email
            ORDER BY li.sort_order
        """),
        {"email": email},
    )
    by_list: dict[str, list[dict]] = {}
    for i in items.mappings().all():
        by_list.setdefault(str(i["list_id"]), []).append({
            "id": i["id"],
            "query": i["product_name"],
            "quantity": float(i["quantity"]) if i["quantity"] is not None else 1,
            "product_id": i["product_id"],
            "product_name_resolved": i["product_name_resolved"],
            "image_url": i["image_url"],
        })
    for r in rows:
        r["items"] = by_list.get(str(r["id"]), [])
    return {"lists": rows}


@router.put("/{list_id}")
async def update_recurring(list_id: str, body: RecurringUpdate, db: AsyncSession = Depends(get_db)):
    email = _validate_email(body.email)
    lst = await _get_owned_list(db, list_id, email)
    lid = str(lst["id"])

    if body.name is not None and body.name.strip():
        await db.execute(
            text("UPDATE shopping_lists SET name = :name, updated_at = NOW() WHERE id = :lid"),
            {"name": body.name.strip()[:200], "lid": lid},
        )

    if body.items is not None:
        items = _clean_items(body.items)
        # Sostituzione integrale delle voci (contratto PUT)
        await db.execute(text("DELETE FROM list_items WHERE list_id = :lid"), {"lid": lid})
        await _insert_items(db, lid, items)

    await db.commit()
    return await _read_list(db, lid)


@router.delete("/{list_id}", status_code=204)
async def delete_recurring(list_id: str, email: str = Query(...), db: AsyncSession = Depends(get_db)):
    email = _validate_email(email)
    lst = await _get_owned_list(db, list_id, email)
    # list_items ha FK ON DELETE CASCADE su shopping_lists
    await db.execute(text("DELETE FROM shopping_lists WHERE id = :lid"), {"lid": str(lst["id"])})
    await db.commit()
