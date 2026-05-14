from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db

router = APIRouter(prefix="/lists", tags=["lists"])


# ── Schemi ──────────────────────────────────────────────────────────────────

class ListCreate(BaseModel):
    name: str = "Lista spesa"


class ItemAdd(BaseModel):
    product_id: Optional[str] = None
    product_name: Optional[str] = None
    quantity: float = 1
    unit: Optional[str] = None


class OptimizeRequest(BaseModel):
    lat: float
    lng: float
    radius_km: float = 5.0
    max_stores: int = 2          # quanti negozi al massimo nella soluzione


# ── Endpoint ─────────────────────────────────────────────────────────────────

@router.get("/")
async def get_lists(user_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT * FROM shopping_lists WHERE user_id = :uid ORDER BY created_at DESC"),
        {"uid": user_id},
    )
    return [dict(r) for r in result.mappings().all()]


@router.post("/")
async def create_list(body: ListCreate, user_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("INSERT INTO shopping_lists (user_id, name) VALUES (:uid, :name) RETURNING *"),
        {"uid": user_id, "name": body.name},
    )
    await db.commit()
    return dict(result.mappings().first())


@router.get("/{list_id}")
async def get_list(list_id: str, db: AsyncSession = Depends(get_db)):
    lst = await db.execute(
        text("SELECT * FROM shopping_lists WHERE id = :id"),
        {"id": list_id},
    )
    row = lst.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Lista non trovata")

    items = await db.execute(
        text("""
            SELECT li.*, p.name AS product_name_db, p.image_url, p.unit AS product_unit
            FROM list_items li
            LEFT JOIN products p ON li.product_id = p.id
            WHERE li.list_id = :lid
            ORDER BY li.sort_order
        """),
        {"lid": list_id},
    )
    return {**dict(row), "items": [dict(i) for i in items.mappings().all()]}


@router.post("/{list_id}/items")
async def add_item(list_id: str, body: ItemAdd, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("""
            INSERT INTO list_items (list_id, product_id, product_name, quantity, unit)
            VALUES (:lid, :pid, :pname, :qty, :unit)
            RETURNING *
        """),
        {
            "lid": list_id,
            "pid": body.product_id,
            "pname": body.product_name,
            "qty": body.quantity,
            "unit": body.unit,
        },
    )
    await db.commit()
    return dict(result.mappings().first())


@router.delete("/{list_id}/items/{item_id}", status_code=204)
async def remove_item(list_id: str, item_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(
        text("DELETE FROM list_items WHERE id = :iid AND list_id = :lid"),
        {"iid": item_id, "lid": list_id},
    )
    await db.commit()


@router.post("/{list_id}/optimize")
async def optimize_list(list_id: str, body: OptimizeRequest, db: AsyncSession = Depends(get_db)):
    """
    Algoritmo di ottimizzazione:
    1. Recupera tutti gli item della lista con product_id
    2. Per ogni prodotto trova i prezzi nei negozi nel raggio
    3. Calcola la soluzione a singolo negozio (totale più basso)
    4. Calcola la soluzione multi-negozio (max_stores) se conviene
    5. Restituisce entrambe le opzioni con i totali
    """
    items_res = await db.execute(
        text("SELECT * FROM list_items WHERE list_id = :lid AND product_id IS NOT NULL"),
        {"lid": list_id},
    )
    items = items_res.mappings().all()

    if not items:
        raise HTTPException(status_code=400, detail="Nessun prodotto con ID nella lista")

    product_ids = [str(i["product_id"]) for i in items]
    quantities = {str(i["product_id"]): float(i["quantity"]) for i in items}

    # Prezzi per ogni prodotto nei negozi vicini
    prices_res = await db.execute(
        text("""
            SELECT
                p.product_id::text, p.price, p.price_per_unit,
                s.id::text AS store_id, s.name AS store_name,
                c.name AS chain_name, c.slug AS chain_slug,
                c.shop_url, s.has_delivery, s.has_click_collect,
                ROUND(ST_Distance(
                    s.coordinates::geography,
                    ST_Point(:lng, :lat)::geography
                )::numeric / 1000, 2) AS distance_km
            FROM prices p
            JOIN stores s ON p.store_id = s.id
            JOIN chains c ON s.chain_id = c.id
            WHERE p.product_id = ANY(:pids::uuid[])
              AND p.is_current      = TRUE
              AND s.is_active       = TRUE
              AND c.is_active       = TRUE
              AND c.has_online_shop = TRUE   -- scope: solo catene con spesa online
              AND ST_DWithin(
                    s.coordinates::geography,
                    ST_Point(:lng, :lat)::geography,
                    :radius_m
                  )
            ORDER BY p.price
        """),
        {
            "pids": product_ids,
            "lat": body.lat,
            "lng": body.lng,
            "radius_m": body.radius_km * 1000,
        },
    )
    all_prices = prices_res.mappings().all()

    # Indicizza: product_id → lista prezzi (già ordinata per prezzo asc)
    by_product: dict[str, list] = {}
    for row in all_prices:
        pid = row["product_id"]
        by_product.setdefault(pid, []).append(dict(row))

    # ── Opzione 1: singolo negozio ───────────────────────────────────────────
    # Per ogni negozio calcola il totale sommando il miglior prezzo disponibile
    store_totals: dict[str, dict] = {}
    for pid, price_rows in by_product.items():
        qty = quantities.get(pid, 1)
        for row in price_rows:
            sid = row["store_id"]
            store_totals.setdefault(sid, {
                "store_id": sid,
                "store_name": row["store_name"],
                "chain_name": row["chain_name"],
                "chain_slug": row["chain_slug"],
                "shop_url": row["shop_url"],
                "has_delivery": row["has_delivery"],
                "has_click_collect": row["has_click_collect"],
                "distance_km": row["distance_km"],
                "total": 0.0,
                "covered_products": 0,
                "items": [],
            })
            entry = store_totals[sid]
            # aggiunge solo se non ancora assegnato per questo prodotto
            already = next((x for x in entry["items"] if x["product_id"] == pid), None)
            if not already:
                entry["total"] += float(row["price"]) * qty
                entry["covered_products"] += 1
                entry["items"].append({
                    "product_id": pid,
                    "price": float(row["price"]),
                    "quantity": qty,
                    "subtotal": float(row["price"]) * qty,
                })

    n_products = len(product_ids)
    # solo negozi che coprono tutti i prodotti
    complete_stores = [
        s for s in store_totals.values() if s["covered_products"] == n_products
    ]
    best_single = min(complete_stores, key=lambda x: x["total"]) if complete_stores else None

    # ── Opzione 2: multi-negozio greedy ─────────────────────────────────────
    # Assegna ogni prodotto al negozio più economico (indipendentemente)
    best_per_product = {}
    for pid, price_rows in by_product.items():
        if price_rows:
            best_per_product[pid] = price_rows[0]  # già sorted ASC

    multi_store_assignment: dict[str, dict] = {}
    multi_total = 0.0
    for pid, row in best_per_product.items():
        qty = quantities.get(pid, 1)
        sid = row["store_id"]
        multi_store_assignment.setdefault(sid, {
            "store_id": sid,
            "store_name": row["store_name"],
            "chain_name": row["chain_name"],
            "shop_url": row["shop_url"],
            "has_delivery": row["has_delivery"],
            "distance_km": row["distance_km"],
            "subtotal": 0.0,
            "items": [],
        })
        subtotal = float(row["price"]) * qty
        multi_total += subtotal
        multi_store_assignment[sid]["subtotal"] += subtotal
        multi_store_assignment[sid]["items"].append({
            "product_id": pid,
            "price": float(row["price"]),
            "quantity": qty,
            "subtotal": subtotal,
        })

    result = {
        "single_store": best_single,
        "multi_store": {
            "total": round(multi_total, 2),
            "stores": list(multi_store_assignment.values()),
            "savings_vs_single": round(
                (best_single["total"] - multi_total) if best_single else 0, 2
            ),
        },
        "products_without_prices": [
            pid for pid in product_ids if pid not in by_product
        ],
    }

    # Salva risultato nella lista
    await db.execute(
        text("UPDATE shopping_lists SET optimization_result = :res WHERE id = :lid"),
        {"res": str(result), "lid": list_id},
    )
    await db.commit()
    return result
