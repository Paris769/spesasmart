import re
import unicodedata
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db

router = APIRouter(prefix="/products", tags=["products"])

MIN_VALID_PRICE = 0.10


def _strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )

def _search_tokens(q: str) -> list[str]:
    normalized = _strip_accents(q.lower())
    return [tok for tok in re.findall(r"[a-z0-9]+", normalized) if len(tok) >= 2]


def _word_regex(q: str) -> str:
    tokens = _search_tokens(q)
    if not tokens:
        return r"$^"
    parts: list[str] = []
    for tok in tokens:
        if tok == "caffe":
            parts.append(r"caff.")
        else:
            parts.append(re.escape(tok))
    return r"(^|[^[:alnum:]_])" + r"[[:space:][:punct:]]+".join(parts) + r"([^[:alnum:]_]|$)"


def _irrelevant_regex(q: str) -> str:
    tokens = _search_tokens(q)
    if len(tokens) != 1:
        return r"$^"
    exclusions = {
        "caffe": [
            r"caffeina", r"yogurt", r"kefir", r"gelat[[:alnum:]_]*", r"cono", r"coppa", r"coppe", r"coppette",
            r"crema fredda", r"macchina", r"macchine", r"decalcificante", r"disincrostante",
            r"tazzin[[:alnum:]_]*", r"bicchier[[:alnum:]_]*", r"latte", r"ginseng", r"variegato", r"dessert", r"budino",
            r"affogato", r"fruyo", r"grisb.*", r"zero grassi", r"vasetto", r"mousse", r"cookies",
            r"cioccolato", r"cremosi", r"nocciola", r"vaniglia", r"stracciatella",
            r"yomo", r"muller", r"müller", r"fage", r"sorbissimo", r"panna", r"gelateria",
            r"senza peccato", r"crema di", r"zuppalatte", r"colussi", r"cereali", r"orzo",
            r"biscott[[:alnum:]_]*", r"liquore", r"estratto", r"cacao", r"amaro",
        ],
        "latte": [
            r"detergente", r"corpo", r"crema", r"bagnoschiuma", r"pan", r"biscott",
            r"gelat", r"yogurt", r"kefir", r"cioccolat", r"macchiato", r"fiocco", r"fiocchi",
        ],
        "acqua": [r"micellare", r"profumo", r"detergente", r"colonia", r"ossigenata", r"patch", r"hydrogel", r"contorno occhi", r"peonia", r"mask", r"demineralizzat[[:alnum:]_]*", r"bagnodoccia", r"doccia", r"shampoo", r"cetriolo"],
        "pasta": [r"dentifric[[:alnum:]_]*", r"placca", r"carie", r"antitartaro", r"collutor[[:alnum:]_]*", r"capitano"],
        "olio": [r"motor[[:alnum:]_]*", r"motore", r"benzina", r"diesel", r"15w", r"10w", r"5w", r"lubrificant[[:alnum:]_]*", r"shell", r"helix", r"detergente", r"doccia", r"eucerin"],
        "riso": [r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pate", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "pollo": [r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pate", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "petto": [r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pate", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "pomodori": [r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pate", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "mele": [r"aceto", r"succo", r"nettare", r"omogeneizzat[[:alnum:]_]*", r"confettura", r"composta", r"biscott[[:alnum:]_]*", r"grancereale", r"mirtilli", r"nocciol[[:alnum:]_]*"],
    }
    parts = exclusions.get(tokens[0], [])
    if not parts:
        return r"$^"
    return r"(^|[^[:alnum:]_])(" + "|".join(parts) + r")([^[:alnum:]_]|$)"


def _has_irrelevant_terms(q: str) -> bool:
    return _irrelevant_regex(q) != r"$^"


def _required_regex(q: str) -> str:
    tokens = _search_tokens(q)
    if len(tokens) != 1:
        return r"$^"
    required = {
        "caffe": [
            r"arabica", r"grani", r"macinat[[:alnum:]_]*", r"solubil[[:alnum:]_]*",
            r"espresso", r"ciald[[:alnum:]_]*", r"capsul[[:alnum:]_]*", r"classico",
            r"classic", r"filtro", r"tradition", r"deka", r"decaffeinat[[:alnum:]_]*",
            r"americano", r"coffee",
        ],
    }
    parts = required.get(tokens[0], [])
    if not parts:
        return r"$^"
    return r"(^|[^[:alnum:]_])(" + "|".join(parts) + r")([^[:alnum:]_]|$)"


def _has_required_terms(q: str) -> bool:
    return _required_regex(q) != r"$^"


def _preference_regex(q: str) -> str:
    tokens = _search_tokens(q)
    if len(tokens) != 1:
        return r"$^"
    preferences = {
        "pasta": [
            r"spaghetti", r"penne", r"fusilli", r"rigatoni", r"farfalle", r"linguine",
            r"sedani", r"mezze penne", r"pasta di semola", r"grano duro", r"semola di grano",
        ],
    }
    parts = preferences.get(tokens[0], [])
    if not parts:
        return r"$^"
    return r"(^|[^[:alnum:]_])(" + "|".join(parts) + r")([^[:alnum:]_]|$)"


def _deprioritize_regex(q: str) -> str:
    tokens = _search_tokens(q)
    if len(tokens) != 1:
        return r"$^"
    deprioritize = {
        "pasta": [
            r"raviol[[:alnum:]_]*", r"tortell[[:alnum:]_]*", r"cappellett[[:alnum:]_]*",
            r"gnocch[[:alnum:]_]*", r"brise[[:alnum:]_]*", r"sfoglia", r"ripien[[:alnum:]_]*",
            r"pappa", r"pastina", r"lasagn[[:alnum:]_]*", r"cannellon[[:alnum:]_]*",
        ],
    }
    parts = deprioritize.get(tokens[0], [])
    if not parts:
        return r"$^"
    return r"(^|[^[:alnum:]_])(" + "|".join(parts) + r")([^[:alnum:]_]|$)"


def _has_preference_terms(q: str) -> bool:
    return _preference_regex(q) != r"$^"


def _has_deprioritize_terms(q: str) -> bool:
    return _deprioritize_regex(q) != r"$^"

def _parse_area_wkt(area: Optional[str]) -> Optional[str]:
    """
    Converte un'area "lat,lng;lat,lng;…" (poligono disegnato sulla mappa)
    in un POLYGON WKT, oppure None se l'input non è valido.

    I valori sono validati come float in range geografico: il WKT risultante
    viene passato come parametro bound a ST_GeomFromText (nessuna injection).
    """
    if not area:
        return None
    pts: list[tuple[float, float]] = []
    for chunk in area.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(",")
        if len(parts) != 2:
            return None
        try:
            lat = float(parts[0])
            lng = float(parts[1])
        except ValueError:
            return None
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
            return None
        pts.append((lat, lng))
    if len(pts) < 3:
        return None
    # Chiude l'anello del poligono (primo punto == ultimo)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    # WKT usa l'ordine x y = lng lat
    coords = ", ".join(f"{lng} {lat}" for lat, lng in pts)
    return f"POLYGON(({coords}))"


@router.get("/search")
async def search_products(
    q: Optional[str] = Query(None, min_length=2),
    barcode: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    lat: Optional[float] = Query(None),
    lng: Optional[float] = Query(None),
    radius_km: float = Query(5.0, ge=0.5, le=50),
    area: Optional[str] = Query(None, description="Poligono 'lat,lng;lat,lng;…'"),
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
    q_tokens = _search_tokens(q)
    strict_match = len(q_tokens) <= 1 and len(q_tokens[0]) <= 5 if q_tokens else True
    filters = ["TRUE"]
    params: dict = {
        "q": q,
        "q_like": f"%{q}%",
        "q_lower": q_lower,
        "q_lower_start": q_lower + " %",
        "q_lower_mid": "% " + q_lower + " %",
        "q_lower_end": "% " + q_lower,
        "q_tsquery": " ".join(q_tokens) or q_lower,
        "q_word_re": _word_regex(q),
        "irrelevant_re": _irrelevant_regex(q),
        "has_irrelevant": _has_irrelevant_terms(q),
        "required_re": _required_regex(q),
        "has_required": _has_required_terms(q),
        "preference_re": _preference_regex(q),
        "has_preference": _has_preference_terms(q),
        "deprioritize_re": _deprioritize_regex(q),
        "has_deprioritize": _has_deprioritize_terms(q),
        "allow_fuzzy": (not strict_match) and len(q_tokens) == 1,
        "limit": limit,
        "candidate_limit": max(500, min(2000, limit * 50)),
        "offset": offset,
        "min_valid_price": MIN_VALID_PRICE,
    }

    if category_id:
        filters.append("p.category_id = :category_id")
        params["category_id"] = category_id

    # Filtro geografico opzionale per il prezzo mostrato nei risultati.
    # I negozi virtuali della spesa online (external_id '*-online') sono
    # nazionali: restano sempre visibili. Il filtro (area disegnata, oppure
    # raggio) si applica solo ai punti vendita fisici (click & collect).
    price_geo = ""
    area_wkt = _parse_area_wkt(area)
    if area_wkt:
        price_geo = """
              AND (
                    s.external_id LIKE '%-online'
                    OR ST_Contains(
                         ST_MakeValid(ST_GeomFromText(:area_wkt, 4326)),
                         s.coordinates
                       )
                  )"""
        params["area_wkt"] = area_wkt
    elif lat is not None and lng is not None:
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

    # Ricerca fuzzy solo quando la query e abbastanza lunga. Per query brevi
    # o merceologiche (es. caffe) privilegiamo parole intere per evitare falsi
    # positivi come 'caffeina'. Prima selezioniamo candidati testuali, poi
    # calcoliamo prezzi solo su quel set: query comuni come "latte" restano rapide.
    await db.execute(text("SET LOCAL pg_trgm.word_similarity_threshold = 0.45"))

    result = await db.execute(
        text(f"""
            WITH candidates AS MATERIALIZED (
                SELECT p.*,
                       CASE
                           WHEN lower(p.name) = :q_lower THEN 10
                           WHEN lower(p.name) LIKE :q_lower_start THEN 9
                           WHEN lower(p.name) ~ :q_word_re THEN 8
                           WHEN lower(COALESCE(p.brand, '')) ~ :q_word_re THEN 7
                           WHEN to_tsvector('simple', lower(p.name || ' ' || COALESCE(p.brand, '') || ' ' || COALESCE(p.description, '')))
                                @@ plainto_tsquery('simple', :q_tsquery) THEN 6
                           ELSE 1
                       END AS word_rank,
                       CASE
                           WHEN :has_preference AND lower(p.name || ' ' || COALESCE(p.brand, '') || ' ' || COALESCE(p.description, '')) ~ :preference_re THEN 2
                           WHEN :has_deprioritize AND lower(p.name || ' ' || COALESCE(p.brand, '') || ' ' || COALESCE(p.description, '')) ~ :deprioritize_re THEN -2
                           ELSE 0
                       END AS product_fit_rank,
                       word_similarity(:q, p.name) AS fuzzy_score,
                       ts_rank(
                           to_tsvector('simple', lower(p.name || ' ' || COALESCE(p.brand, '') || ' ' || COALESCE(p.description, ''))),
                           plainto_tsquery('simple', :q_tsquery)
                       ) AS ts_score
                FROM products p
                WHERE {where}
                  AND NOT (:has_irrelevant AND lower(p.name || ' ' || COALESCE(p.brand, '') || ' ' || COALESCE(p.description, '')) ~ :irrelevant_re)
                  AND NOT (:has_required AND lower(p.name || ' ' || COALESCE(p.brand, '') || ' ' || COALESCE(p.description, '')) !~ :required_re)
                  AND (
                    lower(p.name) ~ :q_word_re
                    OR lower(COALESCE(p.brand, '')) ~ :q_word_re
                    OR to_tsvector('simple', lower(p.name || ' ' || COALESCE(p.brand, '') || ' ' || COALESCE(p.description, '')))
                        @@ plainto_tsquery('simple', :q_tsquery)
                    OR (:allow_fuzzy AND :q <% p.name)
                  )
                ORDER BY
                    word_rank DESC,
                    product_fit_rank DESC,
                    fuzzy_score DESC,
                    ts_score DESC,
                    similarity(p.name, :q) DESC,
                    p.updated_at DESC NULLS LAST
                LIMIT :candidate_limit
            )
            SELECT c.*,
                   pr.min_price,
                   pr.store_count AS price_store_count,
                   pr.available_store_count,
                   pr.best_price_chain_name,
                   pr.best_price_chain_slug,
                   pr.best_price_store_name,
                   pr.best_price_in_stock,
                   pr.best_price_scraped_at,
                   pr.best_price_per_unit
            FROM candidates c
            JOIN LATERAL (
                SELECT stats.min_price,
                       stats.store_count,
                       stats.available_store_count,
                       best.chain_name AS best_price_chain_name,
                       best.chain_slug AS best_price_chain_slug,
                       best.store_name AS best_price_store_name,
                       best.in_stock AS best_price_in_stock,
                       best.scraped_at AS best_price_scraped_at,
                       best.price_per_unit AS best_price_per_unit
                FROM (
                    SELECT COALESCE(
                               MIN(x.price) FILTER (WHERE x.in_stock IS TRUE),
                               MIN(x.price)
                           ) AS min_price,
                           COUNT(DISTINCT x.store_id) AS store_count,
                           COUNT(DISTINCT x.store_id) FILTER (WHERE x.in_stock IS TRUE) AS available_store_count
                    FROM prices x
                    JOIN stores s ON x.store_id = s.id
                    WHERE x.product_id = c.id
                      AND x.is_current = TRUE
                      AND x.price >= :min_valid_price
                      AND s.is_active = TRUE
                      {price_geo}
                ) stats
                LEFT JOIN LATERAL (
                    SELECT x.price,
                           x.in_stock,
                           x.scraped_at,
                           x.price_per_unit,
                           s.name AS store_name,
                           ch.name AS chain_name,
                           ch.slug AS chain_slug
                    FROM prices x
                    JOIN stores s ON x.store_id = s.id
                    JOIN chains ch ON s.chain_id = ch.id
                    WHERE x.product_id = c.id
                      AND x.is_current = TRUE
                      AND x.price >= :min_valid_price
                      AND s.is_active = TRUE
                      {price_geo}
                    ORDER BY x.in_stock DESC, x.price ASC
                    LIMIT 1
                ) best ON TRUE
            ) pr ON TRUE
            WHERE COALESCE(pr.store_count, 0) > 0
            ORDER BY
                c.word_rank DESC,
                c.product_fit_rank DESC,
                c.fuzzy_score DESC,
                c.ts_score DESC,
                pr.store_count DESC,
                similarity(c.name, :q) DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    rows = [dict(r) for r in result.mappings().all()]

    # Telemetria (fire-and-forget): alimenta gli agenti Product/Growth. Le
    # ricerche a 0 risultati sono i gap più preziosi. Non deve mai rompere la
    # risposta all'utente.
    try:
        await db.execute(
            text(
                "INSERT INTO search_log (query, n_results, lat, lng, radius_km) "
                "VALUES (:q, :n, :lat, :lng, :r)"
            ),
            {"q": q[:200], "n": len(rows), "lat": lat, "lng": lng,
             "r": radius_km},
        )
        await db.commit()
    except Exception:
        pass

    return rows

@router.get("/{product_id}/prices")
async def get_product_prices(
    product_id: str,
    response: Response,
    lat: float = Query(...),
    lng: float = Query(...),
    radius_km: float = Query(5.0, ge=0.5, le=50),
    area: Optional[str] = Query(None, description="Poligono 'lat,lng;lat,lng;…'"),
    db: AsyncSession = Depends(get_db),
):
    # I prezzi sono aggiornati dallo scraper poche volte al giorno: cache 5 min.
    response.headers["Cache-Control"] = "public, max-age=300"
    """Prezzi del prodotto nei negozi vicini, ordinati per prezzo crescente.

    Il filtro geografico sui punti vendita fisici usa l'area disegnata
    (se fornita) oppure il raggio. I negozi della spesa online sono
    sempre inclusi (consegna nazionale).
    """
    params: dict = {"product_id": product_id, "lat": lat, "lng": lng, "min_valid_price": MIN_VALID_PRICE}
    area_wkt = _parse_area_wkt(area)
    if area_wkt:
        geo_filter = """s.external_id LIKE '%-online'
                    OR ST_Contains(
                         ST_MakeValid(ST_GeomFromText(:area_wkt, 4326)),
                         s.coordinates
                       )"""
        params["area_wkt"] = area_wkt
    else:
        geo_filter = """s.external_id LIKE '%-online'
                    OR ST_DWithin(
                         s.coordinates::geography,
                         ST_Point(:lng, :lat)::geography,
                         :radius_m
                       )"""
        params["radius_m"] = radius_km * 1000

    result = await db.execute(
        text(f"""
            SELECT
                p.price, p.original_price, p.promo_label,
                p.price_per_unit, p.in_stock, p.scraped_at,
                s.id AS store_id, s.name AS store_name,
                s.address, s.city, s.has_delivery, s.has_click_collect,
                c.name  AS chain_name,
                c.slug  AS chain_slug,
                COALESCE(p.product_url, c.shop_url) AS shop_url,
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
              AND p.price >= :min_valid_price
              AND s.is_active   = TRUE
              AND ({geo_filter})
            ORDER BY p.in_stock DESC, p.price ASC
            LIMIT 30
        """),
        params,
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


@router.get("/seo/sitemap")
async def seo_sitemap(
    response: Response,
    limit: int = Query(5000, le=20000),
    db: AsyncSession = Depends(get_db),
):
    """Elenco prodotti indicizzabili (con almeno un prezzo) per la sitemap SEO."""
    response.headers["Cache-Control"] = "public, max-age=21600"  # 6h
    rows = await db.execute(
        text(
            """
            SELECT p.id::text AS id, p.name, p.updated_at,
                   count(DISTINCT pr.store_id) AS store_count
            FROM products p
            JOIN prices pr ON pr.product_id = p.id AND pr.is_current = TRUE AND pr.price >= :min_valid_price
            GROUP BY p.id, p.name, p.updated_at
            ORDER BY store_count DESC, p.updated_at DESC NULLS LAST
            LIMIT :limit
            """
        ),
        {"limit": limit, "min_valid_price": MIN_VALID_PRICE},
    )
    return [dict(r) for r in rows.mappings().all()]


@router.get("/{product_id}")
async def get_product(
    product_id: str, response: Response, db: AsyncSession = Depends(get_db)
):
    """Prodotto + offerte per catena (alimenta la pagina SEO server-rendered)."""
    response.headers["Cache-Control"] = "public, max-age=600"
    prod = await db.execute(
        text(
            "SELECT id::text, barcode, name, brand, image_url, description, "
            "unit, unit_quantity FROM products WHERE id = :id"
        ),
        {"id": product_id, "min_valid_price": MIN_VALID_PRICE},
    )
    row = prod.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Prodotto non trovato")

    offers = await db.execute(
        text(
            """
            SELECT DISTINCT ON (c.id)
                   c.name AS chain_name, c.slug AS chain_slug,
                   pr.price, pr.in_stock, c.shop_url, pr.product_url
            FROM prices pr
            JOIN stores s ON pr.store_id = s.id
            JOIN chains c ON s.chain_id = c.id
            WHERE pr.product_id = :id AND pr.is_current = TRUE AND pr.price >= :min_valid_price AND s.is_active = TRUE
            ORDER BY c.id, pr.in_stock DESC, pr.price ASC
            """
        ),
        {"id": product_id, "min_valid_price": MIN_VALID_PRICE},
    )
    offer_list = sorted(
        [dict(o) for o in offers.mappings().all()],
        key=lambda x: (not bool(x.get("in_stock", True)), float(x["price"])),
    )
    available_prices = [float(o["price"]) for o in offer_list if o.get("in_stock") is not False]
    prices = available_prices or [float(o["price"]) for o in offer_list]
    return {
        **dict(row),
        "min_price": min(prices) if prices else None,
        "max_price": max(prices) if prices else None,
        "store_count": len(offer_list),
        "offers": offer_list,
    }
