"""
Storico spese (Fase 3): la base dati dell'assistente AI.

Caso d'uso guida: "fammi una spesa con i prodotti della mia ultima spesa, ma
scegliendo quelli in offerta e mantenendo il piu' possibile le marche".
GET /purchases/last e' l'endpoint che serve quel caso: restituisce la spesa
piu' recente con gli articoli gia' ancorati al catalogo (product_id), cosi' il
motore delle offerte puo' cercare alternative senza rifare il match.

Nessuna auth: come watches/recurring l'email e' la chiave (users e' vuota).

REGOLA DI SICUREZZA: ogni query filtra SEMPRE per email - mai restituire o
cancellare spese di altri. Le email sono normalizzate in minuscolo in
scrittura, cosi' il confronto per uguaglianza usa l'indice
(email, purchase_date DESC NULLS LAST, created_at DESC).

ATTENZIONE SQL: nei text() di SQLAlchemy il cast va scritto
CAST(:param AS tipo) e mai :param::tipo (il parser dei bind non riconosce
:param seguito da '::' e lo manda letterale a Postgres).
"""
import json
import re
import time
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

router = APIRouter(prefix="/purchases", tags=["purchases"])

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
VALID_SOURCES = ("receipt", "plan", "manual")

MAX_ITEMS_PER_PURCHASE = 200
MAX_PURCHASES_PER_EMAIL = 50

# Match testuale: stessa soglia della ricerca prodotti (products.py), tarata
# per non far esplodere i candidati restando tollerante sulle abbreviazioni
# degli scontrini ("LATTE P.SCREM 1L").
WORD_SIMILARITY_THRESHOLD = 0.45
# Candidati trigram valutati per articolo prima di applicare i bonus: tiene
# limitato il numero di EXISTS su prices (14M di righe).
MATCH_CANDIDATES = 30
# Un articolo per volta non basta (200 articoli = 200 round trip): i needle
# vengono deduplicati e passati a blocchi come array.
MATCH_BATCH = 50
# Bonus di ranking. Il brand pesa piu' della distanza testuale: l'assistente
# deve "mantenere il piu' possibile le marche". Il prezzo corrente rompe la
# parita' verso prodotti effettivamente confrontabili.
BRAND_BONUS = 0.30
CURRENT_PRICE_BONUS = 0.10

# EAN-8 / UPC-12 / EAN-13 / GTIN-14 dentro al testo dell'articolo.
BARCODE_RE = re.compile(r"(?<!\d)(\d{8}|\d{12,14})(?!\d)")

# Rate limit in-memory (finestra scorrevole di 1h) sulle POST, come watches.py.
# Il limite "duro" (50 spese per email) resta verificato sul DB.
_RATE_WINDOW_S = 3600
_RATE_MAX_REQUESTS = 60
_rate_buckets: dict[str, list[float]] = {}


# ---------------------------------------------------------------- validazione

def _validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Email non valida")
    return email


def _check_rate_limit(email: str) -> None:
    now = time.monotonic()
    bucket = [t for t in _rate_buckets.get(email, []) if now - t < _RATE_WINDOW_S]
    if len(bucket) >= _RATE_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="Troppe richieste, riprova piu' tardi")
    bucket.append(now)
    _rate_buckets[email] = bucket
    if len(_rate_buckets) > 5000:
        for k in [k for k, v in _rate_buckets.items() if not v or now - v[-1] > _RATE_WINDOW_S]:
            _rate_buckets.pop(k, None)


# Nomi pubblici per gli altri flussi che scrivono nello storico
# (receipts.py dopo l'OCR, l'assistente quando conferma un piano).
validate_purchase_email = _validate_email


def _valid_uuid(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_numeric(value: Any, places: int = 2) -> Optional[Decimal]:
    """
    Le colonne di importo sono `numeric` senza precisione: un float arriverebbe
    a Postgres con tutta la coda binaria (1.29 -> 1.28999...). Convertiamo in
    Decimal arrotondato, che asyncpg scrive esatto.
    """
    number = _as_float(value)
    if number is None:
        return None
    try:
        return Decimal(str(round(number, places)))
    except (InvalidOperation, ValueError):
        return None


def _field(item: Any, key: str) -> Any:
    """Legge una chiave sia da dict sia da modello pydantic/oggetto."""
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


# ------------------------------------------------------- normalizzazione testo

def _strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )


def _normalize_needle(name: str, brand: Optional[str] = None) -> str:
    """
    Testo da confrontare col catalogo. Gli scontrini arrivano in maiuscolo, con
    accenti persi, punteggiatura e codici interni: si tengono solo i token
    alfanumerici di almeno 2 caratteri, senza i codici lunghi (sono barcode,
    gestiti a parte). Il brand viene appeso quando non e' gia' nel nome: i nomi
    del catalogo iniziano spesso con la marca.
    """
    raw = _strip_accents((name or "").lower())
    raw = BARCODE_RE.sub(" ", raw)
    tokens = [tok for tok in re.findall(r"[a-z0-9]+", raw) if len(tok) >= 2]
    needle = " ".join(tokens[:12])
    brand_norm = _normalize_brand(brand)
    if brand_norm and brand_norm not in needle:
        needle = f"{brand_norm} {needle}".strip()
    return needle[:200]


def _normalize_brand(brand: Optional[str]) -> str:
    raw = _strip_accents((brand or "").lower())
    tokens = re.findall(r"[a-z0-9]+", raw)
    return " ".join(tokens)[:60]


def _extract_barcode(*values: Optional[str]) -> Optional[str]:
    for value in values:
        if not value:
            continue
        found = BARCODE_RE.search(str(value))
        if found:
            return found.group(1)
    return None


# -------------------------------------------------------------------- matching

# Candidati trigram (indice GIN idx_products_name_trgm) nel sottoquery piu'
# interno, bonus applicati solo sui MATCH_CANDIDATES sopravvissuti: l'EXISTS su
# prices va calcolato dopo il LIMIT, altrimenti Postgres lo valuta su tutti i
# candidati del bitmap scan.
_MATCH_SQL = f"""
    SELECT q.idx,
           m.product_id,
           m.product_name,
           m.product_brand,
           m.image_url
    FROM unnest(
             CAST(:idxs AS int[]),
             CAST(:needles AS text[]),
             CAST(:brands AS text[])
         ) AS q(idx, needle, brand)
    LEFT JOIN LATERAL (
        SELECT p2.id::text AS product_id,
               p2.name     AS product_name,
               p2.brand    AS product_brand,
               p2.image_url,
               cand.ws
                 + CASE WHEN q.brand <> '' AND (
                             lower(COALESCE(p2.brand, '')) = q.brand
                          OR position(q.brand IN lower(p2.name)) > 0
                        ) THEN {BRAND_BONUS} ELSE 0 END
                 + CASE WHEN EXISTS (
                          SELECT 1 FROM prices pr
                          WHERE pr.product_id = p2.id
                            AND pr.is_current = TRUE
                            AND pr.quarantined = FALSE
                        ) THEN {CURRENT_PRICE_BONUS} ELSE 0 END AS score
        FROM (
            SELECT p.id AS pid, word_similarity(q.needle, p.name) AS ws
            FROM products p
            WHERE q.needle <% p.name
            ORDER BY word_similarity(q.needle, p.name) DESC
            LIMIT {MATCH_CANDIDATES}
        ) cand
        JOIN products p2 ON p2.id = cand.pid
        ORDER BY score DESC, cand.ws DESC, p2.name
        LIMIT 1
    ) m ON TRUE
"""

_NO_MATCH = {
    "product_id": None,
    "product_name": None,
    "product_brand": None,
    "image_url": None,
    "match_confidence": "none",
}


async def match_purchase_items(db: AsyncSession, items: list[Any]) -> list[dict]:
    """
    Ancora articoli testuali al catalogo prodotti.

    `items`: lista di dict (o oggetti) con `name` e, opzionali, `brand`,
    `product_id`, `barcode`.

    Ritorna una lista allineata per indice agli input:
        {"product_id": str|None, "product_name": str|None,
         "product_brand": str|None, "image_url": str|None,
         "match_confidence": "exact"|"fuzzy"|"none"}

    Strategia (in ordine):
      a) product_id fornito ed esistente, oppure barcode/EAN nel testo che
         risolve su products.barcode -> 'exact';
      b) match trigram sul nome (indice GIN), con bonus al prodotto della
         marca giusta e a quello che ha prezzi correnti -> 'fuzzy';
      c) nessun candidato sopra soglia -> product_id NULL, 'none'.
    """
    results: list[dict] = [dict(_NO_MATCH) for _ in items]
    if not items:
        return results

    # (a1) product_id espliciti: una sola SELECT per validarli tutti.
    explicit: dict[int, str] = {}
    for idx, item in enumerate(items):
        pid = _valid_uuid(_field(item, "product_id"))
        if pid:
            explicit[idx] = pid
    if explicit:
        rows = await db.execute(
            text("""
                SELECT id::text AS id, name, brand, image_url
                FROM products
                WHERE id = ANY(CAST(:ids AS uuid[]))
            """),
            {"ids": sorted(set(explicit.values()))},
        )
        found = {r["id"]: r for r in rows.mappings().all()}
        for idx, pid in explicit.items():
            row = found.get(pid)
            if row:
                results[idx] = {
                    "product_id": row["id"],
                    "product_name": row["name"],
                    "product_brand": row["brand"],
                    "image_url": row["image_url"],
                    "match_confidence": "exact",
                }

    # (a2) barcode nel testo: gli articoli con product_id valido sono gia' fatti.
    pending = [i for i in range(len(items)) if results[i]["product_id"] is None]
    codes: dict[int, str] = {}
    for idx in pending:
        code = _extract_barcode(
            _field(items[idx], "barcode"), _field(items[idx], "name")
        )
        if code:
            codes[idx] = code
    if codes:
        rows = await db.execute(
            text("""
                SELECT barcode, id::text AS id, name, brand, image_url
                FROM products
                WHERE barcode = ANY(CAST(:codes AS text[]))
            """),
            {"codes": sorted(set(codes.values()))},
        )
        by_code = {r["barcode"]: r for r in rows.mappings().all()}
        for idx, code in codes.items():
            row = by_code.get(code)
            if row:
                results[idx] = {
                    "product_id": row["id"],
                    "product_name": row["name"],
                    "product_brand": row["brand"],
                    "image_url": row["image_url"],
                    "match_confidence": "exact",
                }

    # (b) match testuale: needle deduplicati, cosi' le righe ripetute dello
    # stesso scontrino costano una sola valutazione.
    pending = [i for i in range(len(items)) if results[i]["product_id"] is None]
    keys: dict[tuple[str, str], list[int]] = {}
    for idx in pending:
        brand = _normalize_brand(_field(items[idx], "brand"))
        needle = _normalize_needle(_field(items[idx], "name") or "", brand)
        if len(needle) < 3:
            continue
        keys.setdefault((needle, brand), []).append(idx)
    if not keys:
        return results

    unique = list(keys.keys())
    # La soglia di word_similarity e' un GUC: SET LOCAL vale per la
    # transazione corrente (la sessione ne apre una implicita alla prima
    # execute), quindi non sporca le altre richieste.
    await db.execute(
        text(f"SET LOCAL pg_trgm.word_similarity_threshold = {WORD_SIMILARITY_THRESHOLD}")
    )
    for start in range(0, len(unique), MATCH_BATCH):
        chunk = unique[start:start + MATCH_BATCH]
        rows = await db.execute(
            text(_MATCH_SQL),
            {
                "idxs": list(range(len(chunk))),
                "needles": [needle for needle, _ in chunk],
                "brands": [brand for _, brand in chunk],
            },
        )
        for row in rows.mappings().all():
            if not row["product_id"]:
                continue
            for idx in keys[chunk[row["idx"]]]:
                results[idx] = {
                    "product_id": row["product_id"],
                    "product_name": row["product_name"],
                    "product_brand": row["product_brand"],
                    "image_url": row["image_url"],
                    "match_confidence": "fuzzy",
                }
    return results


# ------------------------------------------------------------------ scrittura

class PurchaseItemIn(BaseModel):
    name: str
    brand: Optional[str] = None
    quantity: Optional[float] = 1
    unit_price: Optional[float] = None
    line_total: Optional[float] = None
    product_id: Optional[str] = None


class PurchaseCreate(BaseModel):
    email: str
    chain_slug: Optional[str] = None
    store_id: Optional[str] = None
    purchase_date: Optional[date] = None
    total: Optional[float] = None
    source: Optional[str] = "manual"
    items: list[PurchaseItemIn]


def _clean_items(items: list[Any]) -> list[Any]:
    cleaned = [it for it in items if (_field(it, "name") or "").strip()]
    if not cleaned:
        raise HTTPException(status_code=422, detail="Fornire almeno un articolo con un nome")
    if len(cleaned) > MAX_ITEMS_PER_PURCHASE:
        raise HTTPException(
            status_code=422,
            detail=f"Massimo {MAX_ITEMS_PER_PURCHASE} articoli per spesa",
        )
    return cleaned


async def _check_quota(db: AsyncSession, email: str) -> None:
    result = await db.execute(
        text("SELECT count(*) AS n FROM purchases WHERE email = :email"),
        {"email": email},
    )
    if (result.mappings().first() or {}).get("n", 0) >= MAX_PURCHASES_PER_EMAIL:
        raise HTTPException(
            status_code=429,
            detail=f"Massimo {MAX_PURCHASES_PER_EMAIL} spese per email: cancellane qualcuna",
        )


check_purchase_quota = _check_quota


async def insert_purchase(
    db: AsyncSession,
    *,
    email: str,
    items: list[Any],
    chain_slug: Optional[str] = None,
    store_id: Optional[str] = None,
    purchase_date: Optional[date] = None,
    total: Optional[float] = None,
    source: str = "manual",
    raw: Optional[Any] = None,
) -> dict:
    """
    Scrive una spesa e i suoi articoli, facendo il match sul catalogo per gli
    articoli senza product_id. Riusabile dagli altri flussi che producono una
    spesa (es. receipts.py dopo l'OCR). Fa il commit; l'email va passata gia'
    validata con _validate_email.
    """
    items = _clean_items(items)
    await _check_quota(db, email)

    source = (source or "manual").strip().lower()
    if source not in VALID_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"source non valido: usa uno di {', '.join(VALID_SOURCES)}",
        )

    store_uuid = _valid_uuid(store_id)
    if store_uuid:
        exists = await db.execute(
            text("SELECT 1 FROM stores WHERE id = CAST(:sid AS uuid)"), {"sid": store_uuid}
        )
        if not exists.first():
            raise HTTPException(status_code=422, detail="store_id inesistente")

    matches = await match_purchase_items(db, items)

    created = await db.execute(
        text("""
            INSERT INTO purchases (email, chain_slug, store_id, purchase_date, total, source, raw)
            VALUES (
                :email,
                :chain_slug,
                CAST(:store_id AS uuid),
                CAST(:purchase_date AS date),
                CAST(:total AS numeric),
                :source,
                CAST(:raw AS jsonb)
            )
            RETURNING id::text AS id
        """),
        {
            "email": email,
            "chain_slug": (chain_slug or "").strip().lower()[:50] or None,
            "store_id": store_uuid,
            "purchase_date": purchase_date,
            "total": _as_numeric(total),
            "source": source,
            "raw": _dump_raw(raw),
        },
    )
    purchase_id = created.mappings().first()["id"]

    rows = []
    for item, match in zip(items, matches):
        quantity = _as_numeric(_field(item, "quantity"), places=3)
        if not quantity or quantity <= 0:
            quantity = Decimal("1")
        unit_price = _as_numeric(_field(item, "unit_price"))
        line_total = _as_numeric(_field(item, "line_total"))
        if line_total is None and unit_price is not None:
            line_total = _as_numeric(unit_price * quantity)
        rows.append({
            "purchase_id": purchase_id,
            "product_id": match["product_id"],
            "name": (_field(item, "name") or "").strip()[:500],
            # Se l'articolo non porta la marca (tipico degli scontrini) si
            # eredita quella del prodotto agganciato: e' il dato su cui
            # l'assistente poi "mantiene il piu' possibile le marche".
            "brand": ((_field(item, "brand") or "").strip() or match.get("product_brand") or "")[:200] or None,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_total": line_total,
            "match_confidence": match["match_confidence"],
        })
    await db.execute(
        text("""
            INSERT INTO purchase_items
                (purchase_id, product_id, name, brand, quantity, unit_price, line_total, match_confidence)
            VALUES (
                CAST(:purchase_id AS uuid),
                CAST(:product_id AS uuid),
                :name,
                :brand,
                CAST(:quantity AS numeric),
                CAST(:unit_price AS numeric),
                CAST(:line_total AS numeric),
                :match_confidence
            )
        """),
        rows,
    )
    await db.commit()
    return {"id": purchase_id, "items_count": len(rows)}


def _dump_raw(raw: Optional[Any]) -> Optional[str]:
    if raw is None:
        return None
    try:
        return json.dumps(raw, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ endpoints

@router.post("/", status_code=201)
@router.post("", status_code=201, include_in_schema=False)
async def create_purchase(body: PurchaseCreate, db: AsyncSession = Depends(get_db)):
    email = _validate_email(body.email)
    _check_rate_limit(email)
    return await insert_purchase(
        db,
        email=email,
        items=body.items,
        chain_slug=body.chain_slug,
        store_id=body.store_id,
        purchase_date=body.purchase_date,
        total=body.total,
        source=body.source or "manual",
    )


@router.get("/")
@router.get("", include_in_schema=False)
async def list_purchases(
    email: str = Query(...),
    limit: int = Query(10, ge=1, le=MAX_PURCHASES_PER_EMAIL),
    db: AsyncSession = Depends(get_db),
):
    email = _validate_email(email)
    result = await db.execute(
        text("""
            SELECT p.id::text AS id,
                   p.chain_slug,
                   p.purchase_date,
                   p.total,
                   p.source,
                   p.created_at,
                   (
                       SELECT count(*) FROM purchase_items i
                       WHERE i.purchase_id = p.id
                   ) AS items_count
            FROM purchases p
            WHERE p.email = :email
            ORDER BY p.purchase_date DESC NULLS LAST, p.created_at DESC
            LIMIT :limit
        """),
        {"email": email, "limit": limit},
    )
    purchases = []
    for row in result.mappings().all():
        purchases.append({
            "id":            row["id"],
            "chain_slug":    row["chain_slug"],
            "purchase_date": row["purchase_date"],
            "total":         _as_float(row["total"]),
            "source":        row["source"],
            "created_at":    row["created_at"],
            "items_count":   int(row["items_count"] or 0),
        })
    return {"purchases": purchases}


async def _read_purchase(db: AsyncSession, email: str, purchase_id: Optional[str]) -> dict:
    """
    Spesa + articoli completi, filtrando SEMPRE per email. `purchase_id` None
    significa "la piu' recente". 404 'no_purchases' quando l'email non ha
    spese: e' il contratto su cui si appoggia l'assistente.
    """
    head_sql = """
        SELECT id::text AS id, chain_slug, purchase_date, total, source
        FROM purchases
        WHERE email = :email
    """
    params: dict = {"email": email}
    if purchase_id is None:
        head_sql += " ORDER BY purchase_date DESC NULLS LAST, created_at DESC LIMIT 1"
    else:
        head_sql += " AND id = CAST(:pid AS uuid) LIMIT 1"
        params["pid"] = purchase_id

    head = await db.execute(text(head_sql), params)
    row = head.mappings().first()
    if not row:
        # Su /last il contratto e' 'no_purchases' (l'assistente lo usa per
        # capire che lo storico e' vuoto). Su /{id} non distinguiamo tra "non
        # esiste" e "e' di un'altra email".
        raise HTTPException(
            status_code=404,
            detail="no_purchases" if purchase_id is None else "Spesa non trovata",
        )

    # Nessuna colonna di ordinamento nello schema migrato: ctid conserva
    # l'ordine di inserimento (le righe non vengono mai aggiornate), cosi'
    # l'assistente ripropone gli articoli nell'ordine dello scontrino.
    items_rows = await db.execute(
        text("""
            SELECT i.name,
                   i.brand,
                   i.quantity,
                   i.unit_price,
                   i.product_id::text AS product_id,
                   p.name      AS product_name,
                   p.image_url AS image_url,
                   i.match_confidence
            FROM purchase_items i
            LEFT JOIN products p ON p.id = i.product_id
            WHERE i.purchase_id = CAST(:pid AS uuid)
            ORDER BY i.ctid
        """),
        {"pid": row["id"]},
    )
    items = []
    for item in items_rows.mappings().all():
        items.append({
            "name":             item["name"],
            "brand":            item["brand"],
            "quantity":         _as_float(item["quantity"]),
            "unit_price":       _as_float(item["unit_price"]),
            "product_id":       item["product_id"],
            "product_name":     item["product_name"],
            "image_url":        item["image_url"],
            "match_confidence": item["match_confidence"],
        })
    return {
        "id":            row["id"],
        "chain_slug":    row["chain_slug"],
        "purchase_date": row["purchase_date"],
        "total":         _as_float(row["total"]),
        "source":        row["source"],
        "items":         items,
    }


# /last prima di /{purchase_id}: FastAPI prova le rotte in ordine di
# dichiarazione e "last" non e' un uuid.
@router.get("/last")
async def last_purchase(email: str = Query(...), db: AsyncSession = Depends(get_db)):
    return await _read_purchase(db, _validate_email(email), None)


@router.get("/{purchase_id}")
async def get_purchase(
    purchase_id: str,
    email: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    email = _validate_email(email)
    pid = _valid_uuid(purchase_id)
    if not pid:
        raise HTTPException(status_code=422, detail="id non valido")
    return await _read_purchase(db, email, pid)


@router.delete("/{purchase_id}", status_code=204)
async def delete_purchase(
    purchase_id: str,
    email: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    email = _validate_email(email)
    pid = _valid_uuid(purchase_id)
    if not pid:
        raise HTTPException(status_code=422, detail="id non valido")
    # Gli articoli cadono per ON DELETE CASCADE.
    result = await db.execute(
        text("""
            DELETE FROM purchases
            WHERE id = CAST(:pid AS uuid) AND email = :email
            RETURNING id
        """),
        {"pid": pid, "email": email},
    )
    if not result.first():
        raise HTTPException(status_code=404, detail="Spesa non trovata")
    await db.commit()
    return Response(status_code=204)
