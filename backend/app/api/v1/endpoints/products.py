import re
import unicodedata
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.freshness import fresh_price_sql, stale_price_sql
from app.db.session import get_db
from app.core.price_origin import (
    descrizione as origine_descrizione,
    descrizione_di,
    origine,
    origine_di,
    sconto_affermabile_di,
    avviso as avviso_origini,
)
from app.core.geo_coverage import (
    unavailable_online_chains,
    offer_chains_param,
    CHAINS_NEARBY_SQL,
)

router = APIRouter(prefix="/products", tags=["products"])

MIN_VALID_PRICE = 0.10

# Accompagna nearest_store_name / nearest_store_km in GET /{id}/prices: quei
# due campi riguardano il punto vendita piu' vicino della catena, mentre il
# prezzo della riga e' il prezzo della spesa online. Senza questa frase, chi
# li accende domani stampa un indirizzo accanto a un prezzo che in quel
# negozio non vale.
NEAREST_STORE_NOTA = (
    "Punto vendita piu' vicino di questa catena: il prezzo qui sopra e' quello "
    "della spesa online, non il prezzo di questo negozio."
)


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
            r"yomo", r"muller", r"mÃƒÂ¼ller", r"fage", r"sorbissimo", r"panna", r"gelateria",
            r"senza peccato", r"crema di", r"zuppalatte", r"colussi", r"cereali", r"orzo",
            r"biscott[[:alnum:]_]*", r"liquore", r"estratto", r"cacao", r"amaro",
        ],
        "latte": [
            r"detergente", r"corpo", r"crema", r"bagnoschiuma", r"pan", r"biscott",
            r"gelat", r"yogurt", r"kefir", r"cioccolat", r"macchiato", r"fiocco", r"fiocchi",
            # Formaggi il cui nome commerciale inizia con "Latte ..." (es.
            # "Latte Montagna Alto Adige Stelvio DOP"): non sono latte.
            r"stelvio", r"dop", r"formagg[[:alnum:]_]*", r"stagionat[[:alnum:]_]*",
            r"asiago", r"fontina", r"caciotta", r"toma",
        ],
        "acqua": [r"micellare", r"profumo", r"detergente", r"colonia", r"ossigenata", r"patch", r"hydrogel", r"contorno occhi", r"peonia", r"mask", r"demineralizzat[[:alnum:]_]*", r"bagnodoccia", r"doccia", r"shampoo", r"cetriolo"],
        "pasta": [r"bris.e", r"sfoglia", r"frolla", r"pizza", r"lievitat[[:alnum:]_]*", r"raviol[[:alnum:]_]*", r"tortell[[:alnum:]_]*", r"cappellett[[:alnum:]_]*", r"gnocch[[:alnum:]_]*", r"ripien[[:alnum:]_]*", r"pappa", r"pastina", r"lasagn[[:alnum:]_]*", r"cannellon[[:alnum:]_]*", r"dentifric[[:alnum:]_]*", r"placca", r"carie", r"antitartaro", r"collutor[[:alnum:]_]*", r"capitano"],
        "olio": [r"motor[[:alnum:]_]*", r"motore", r"benzina", r"diesel", r"15w", r"10w", r"5w", r"lubrificant[[:alnum:]_]*", r"shell", r"helix", r"detergente", r"doccia", r"eucerin"],
        "riso": [r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "pollo": [r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "petto": [r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "tonno": [r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*", r"petfriends", r"surimi", r"granchio"],
        "insalata": [r"russa", r"capricciosa", r"maionese", r"di riso", r"di pasta", r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
        "pomodori": [r"gatto", r"gatti", r"cane", r"cani", r"purina", r"gourmet", r"mao", r"pat[eé]", r"bao", r"filettini", r"senior", r"almo", r"nature", r"hydration", r"hfc", r"noodles", r"fusian", r"maggi", r"croccant[[:alnum:]_]*", r"pet[[:alnum:]_]*"],
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
        "tonno": [
            r"tonno", r"al naturale", r"olio", r"filett[[:alnum:]_]*", r"tranc[[:alnum:]_]*",
            r"rio mare", r"nostromo", r"mareblu", r"as do mar",
        ],
        "insalata": [
            r"lattuga", r"gentile", r"iceberg", r"misticanza", r"rucola", r"valeriana",
            r"songino", r"belga", r"cuori", r"insalata mista",
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
        "tonno": [
            r"gatto", r"gatti", r"cane", r"cani", r"pet[[:alnum:]_]*", r"surimi", r"granchio",
        ],
        "insalata": [
            r"russa", r"capricciosa", r"maionese", r"di riso", r"di pasta", r"pollo", r"tonno",
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
    Converte un'area "lat,lng;lat,lng;Ã¢â‚¬Â¦" (poligono disegnato sulla mappa)
    in un POLYGON WKT, oppure None se l'input non ÃƒÂ¨ valido.

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
    area: Optional[str] = Query(None, description="Poligono 'lat,lng;lat,lng;Ã¢â‚¬Â¦'"),
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
        # catene con spesa online che NON servono la zona: escluse dai risultati
        "no_online": unavailable_online_chains(lat, lng),
    }

    if category_id:
        filters.append("p.category_id = :category_id")
        params["category_id"] = category_id

    # Filtro geografico opzionale per il prezzo mostrato nei risultati.
    # I negozi della spesa online sono nazionali, ma entrano solo se la loro
    # catena serve davvero questa zona (vedi core/geo_coverage): senza questo
    # controllo un utente in Sardegna vedeva prezzi Esselunga a 500 km.
    # NB: il frammento è inserito in due sottoquery dove le catene hanno alias
    # DIVERSI (cx e ch), quindi lo generiamo per alias.
    price_geo_tpl = ""
    area_wkt = _parse_area_wkt(area)
    if area_wkt:
        price_geo_tpl = """
              AND (
                    (s.external_id LIKE '%-online'
                     AND NOT ({CH}.slug = ANY(string_to_array(:no_online, ','))))
                    OR ST_Contains(
                         ST_MakeValid(ST_GeomFromText(:area_wkt, 4326)),
                         s.coordinates
                       )
                  )"""
        params["area_wkt"] = area_wkt
    elif lat is not None and lng is not None:
        price_geo_tpl = """
              AND (
                    (s.external_id LIKE '%-online'
                     AND NOT ({CH}.slug = ANY(string_to_array(:no_online, ','))))
                    OR (s.external_id LIKE '%-offerte'
                        AND {CH}.slug = ANY(string_to_array(:offer_chains, ',')))
                    OR ST_DWithin(
                         s.coordinates::geography,
                         ST_Point(:lng, :lat)::geography,
                         :radius_m
                       )
                  )"""
        params["lat"] = lat
        params["lng"] = lng
        params["radius_m"] = radius_km * 1000

    # Catene "da volantino" (MD, Lidl, Penny, Aldi): le loro offerte stanno su un
    # unico punto nazionale, quindi sparivano per chi non era vicino a quelle
    # coordinate. Le mostriamo dove la catena ha punti vendita reali. La query e'
    # leggera (poche centinaia di ms) e usa l'indice spaziale.
    params["offer_chains"] = ""
    if lat is not None and lng is not None:
        vicine = await db.execute(
            text(CHAINS_NEARBY_SQL),
            {"lat": lat, "lng": lng, "radius_m": radius_km * 1000},
        )
        params["offer_chains"] = offer_chains_param([r[0] for r in vicine.fetchall()])

    price_geo_cx = price_geo_tpl.format(CH="cx")
    price_geo_ch = price_geo_tpl.format(CH="ch")
    price_geo_cm = price_geo_tpl.format(CH="cm")

    where = " AND ".join(filters)

    # Igiene dati: nei minimi e nei conteggi entrano solo prezzi non in
    # quarantena e abbastanza freschi per la loro catena (vedi core/freshness).
    fresh_stats = fresh_price_sql(params, price_alias="x", chain_alias="cx")
    fresh_best = fresh_price_sql(params, price_alias="x", chain_alias="ch")
    fresh_min = fresh_price_sql(params, price_alias="x", chain_alias="cm")

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
                   pr.min_price_per_unit,
                   pr.store_count AS price_store_count,
                   pr.available_store_count,
                   pr.min_price_source,
                   pr.min_price_promo_expires,
                   pr.min_price_original_price,
                   pr.min_price_promo_label,
                   -- Il prezzo della RIGA (non min_price, che e' l'aggregato):
                   -- e' il termine di confronto del barrato. Le due colonne
                   -- esistevano nella LATERAL `pr` ma non erano proiettate
                   -- qui, quindi r.get("min_price_row_price") tornava None e
                   -- la prova del barrato restava quella LARGA
                   -- (original_price > 0), al contrario di cio' che il
                   -- commento accanto a `prove` dichiarava.
                   pr.min_price_row_price,
                   pr.best_price_chain_name,
                   pr.best_price_chain_slug,
                   pr.best_price_store_name,
                   pr.best_price_in_stock,
                   pr.best_price_scraped_at,
                   pr.best_price_per_unit,
                   pr.best_price_source,
                   pr.best_price_promo_expires,
                   pr.best_price_original_price,
                   pr.best_price_promo_label,
                   pr.best_price_row_price
            FROM candidates c
            JOIN LATERAL (
                SELECT stats.min_price,
                       stats.min_price_per_unit,
                       stats.store_count,
                       stats.available_store_count,
                       minrow.source AS min_price_source,
                       minrow.promo_expires AS min_price_promo_expires,
                       minrow.original_price AS min_price_original_price,
                       minrow.promo_label AS min_price_promo_label,
                       -- Il prezzo della riga serve come TERMINE DI CONFRONTO
                       -- del barrato: senza, price_origin applica la prova
                       -- larga (original_price > 0) invece di quella stretta
                       -- (> price) che usa il resto del progetto, e una riga
                       -- con barrato uguale o inferiore al prezzo passerebbe
                       -- per uno sconto che non c'e' (9.584 righe correnti
                       -- hanno un barrato <= prezzo).
                       minrow.price AS min_price_row_price,
                       best.chain_name AS best_price_chain_name,
                       best.chain_slug AS best_price_chain_slug,
                       best.store_name AS best_price_store_name,
                       best.in_stock AS best_price_in_stock,
                       best.scraped_at AS best_price_scraped_at,
                       best.price_per_unit AS best_price_per_unit,
                       best.source AS best_price_source,
                       best.promo_expires AS best_price_promo_expires,
                       best.original_price AS best_price_original_price,
                       best.promo_label AS best_price_promo_label,
                       best.price AS best_price_row_price
                FROM (
                    -- min_price e la riga da cui nasce: il numero piu' visto
                    -- dell'app non puo' portare l'etichetta di un'ALTRA riga.
                    -- `best` ordina per in_stock DESC (in Postgres NULLS
                    -- FIRST) mentre min_price filtra in_stock IS TRUE: su un
                    -- prodotto con righe in_stock NULL e righe TRUE i due
                    -- numeri nascono da record distinti, e la griglia stampava
                    -- min_price con sotto la provenienza di best_price.
                    SELECT COALESCE(
                               MIN(x.price) FILTER (WHERE x.in_stock IS TRUE),
                               MIN(x.price)
                           ) AS min_price,
                           COALESCE(
                               MIN(x.price_per_unit) FILTER (WHERE x.in_stock IS TRUE),
                               MIN(x.price_per_unit)
                           ) AS min_price_per_unit,
                           COUNT(DISTINCT x.store_id) AS store_count,
                           COUNT(DISTINCT x.store_id) FILTER (WHERE x.in_stock IS TRUE) AS available_store_count
                    FROM prices x
                    JOIN stores s ON x.store_id = s.id
                    JOIN chains cx ON s.chain_id = cx.id
                    WHERE x.product_id = c.id
                      AND x.is_current = TRUE
                      AND NOT x.quarantined
                      AND x.price >= :min_valid_price
                      AND {fresh_stats}
                      AND s.is_active = TRUE
                      {price_geo_cx}
                ) stats
                LEFT JOIN LATERAL (
                    -- La RIGA di min_price. L'ORDER BY riproduce esattamente
                    -- il COALESCE(MIN FILTER in_stock IS TRUE, MIN) di
                    -- `stats`: (x.in_stock IS TRUE) e' un booleano mai NULL,
                    -- quindi DESC mette davanti le righe disponibili e fra
                    -- quelle vince la piu' economica; se nessuna lo e', vince
                    -- la piu' economica in assoluto. Stesse clausole di
                    -- serving di `stats`, altrimenti sarebbe un'altra riga.
                    SELECT x.source,
                           x.promo_expires,
                           x.original_price,
                           x.price,
                           NULLIF(TRIM(x.promo_label), '') AS promo_label
                    FROM prices x
                    JOIN stores s ON x.store_id = s.id
                    JOIN chains cm ON s.chain_id = cm.id
                    WHERE x.product_id = c.id
                      AND x.is_current = TRUE
                      AND NOT x.quarantined
                      AND x.price >= :min_valid_price
                      AND {fresh_min}
                      AND s.is_active = TRUE
                      {price_geo_cm}
                    ORDER BY (x.in_stock IS TRUE) DESC, x.price ASC
                    LIMIT 1
                ) minrow ON TRUE
                LEFT JOIN LATERAL (
                    SELECT x.price,
                           x.in_stock,
                           x.scraped_at,
                           x.price_per_unit,
                           x.source,
                           x.promo_expires,
                           x.original_price,
                           NULLIF(TRIM(x.promo_label), '') AS promo_label,
                           s.name AS store_name,
                           ch.name AS chain_name,
                           ch.slug AS chain_slug
                    FROM prices x
                    JOIN stores s ON x.store_id = s.id
                    JOIN chains ch ON s.chain_id = ch.id
                    WHERE x.product_id = c.id
                      AND x.is_current = TRUE
                      AND NOT x.quarantined
                      AND x.price >= :min_valid_price
                      AND {fresh_best}
                      AND s.is_active = TRUE
                      {price_geo_ch}
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

    # La griglia dei risultati e' il prezzo piu' visto dell'app: senza etichetta
    # mette in colonna il listino di negozio Eurospin e il prezzo e-commerce
    # Esselunga come se fossero la stessa grandezza.
    # La sorgente del PREZZO e' best_price_source: non r["source"], che arriva
    # qui con SELECT p.* ed e' la provenienza della SCHEDA prodotto
    # (open_food_facts). Passarla a origine() darebbe "sconosciuta" su ogni
    # riga, con l'aria di funzionare.
    # L'origine si decide per RIGA: original_price/promo_label della riga del
    # miglior prezzo sono la prova che quella riga e' davvero un'offerta.
    for r in rows:
        src = r.get("best_price_source")
        exp = r.get("best_price_promo_expires")
        prove = {
            "original_price": r.get("best_price_original_price"),
            "promo_label": r.get("best_price_promo_label"),
            # Il prezzo della riga rende la prova del barrato quella STRETTA
            # (original_price > price), la stessa del predicato SQL e di
            # origine_di(): senza, questa superficie e /lists/optimize-quick
            # potevano dare due nature diverse allo stesso numero.
            "price": r.get("best_price_row_price"),
        }
        r["best_price_origine"] = origine(src, exp, **prove)
        r["best_price_origine_label"] = origine_descrizione(src, exp, **prove)

        # min_price porta la provenienza della PROPRIA riga (LATERAL minrow):
        # e' il numero che la griglia di ricerca stampa come "Prezzo migliore",
        # e prima prendeva in prestito l'etichetta di best_price, cioe' di
        # un'altra riga. Un'etichetta che non appartiene al prezzo stampato e'
        # peggio di un'etichetta assente.
        min_src = r.get("min_price_source")
        min_prove = {
            "original_price": r.get("min_price_original_price"),
            "promo_label": r.get("min_price_promo_label"),
            "price": r.get("min_price_row_price"),
        }
        min_exp = r.get("min_price_promo_expires")
        r["min_price_origine"] = origine(min_src, min_exp, **min_prove) if min_src else None
        r["min_price_origine_label"] = (
            origine_descrizione(min_src, min_exp, **min_prove) if min_src else None
        )

    # Telemetria (fire-and-forget): alimenta gli agenti Product/Growth. Le
    # ricerche a 0 risultati sono i gap piÃƒÂ¹ preziosi. Non deve mai rompere la
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
    area: Optional[str] = Query(None, description="Poligono 'lat,lng;lat,lng;Ã¢â‚¬Â¦'"),
    db: AsyncSession = Depends(get_db),
):
    # I prezzi sono aggiornati dallo scraper poche volte al giorno: cache 5 min.
    response.headers["Cache-Control"] = "public, max-age=300"
    """Prezzi del prodotto nei negozi vicini, ordinati per prezzo crescente.

    Il filtro geografico sui punti vendita fisici usa l'area disegnata
    (se fornita) oppure il raggio. I negozi della spesa online sono
    sempre inclusi (consegna nazionale).

    Ogni riga porta `origine` e `origine_label` (vedi core/price_origin) e
    `avviso`: lo stesso testo su tutte le righe, valorizzato SOLO quando
    l'insieme restituito mette a confronto prezzi online e prezzi di
    negozio/volantino, null altrimenti.
    """
    params: dict = {"product_id": product_id, "lat": lat, "lng": lng, "min_valid_price": MIN_VALID_PRICE,
                    "no_online": unavailable_online_chains(lat, lng)}
    area_wkt = _parse_area_wkt(area)
    if area_wkt:
        geo_filter = """(s.external_id LIKE '%-online' AND NOT (c.slug = ANY(string_to_array(:no_online, ','))))
                    OR ST_Contains(
                         ST_MakeValid(ST_GeomFromText(:area_wkt, 4326)),
                         s.coordinates
                       )"""
        params["area_wkt"] = area_wkt
    else:
        geo_filter = """(s.external_id LIKE '%-online' AND NOT (c.slug = ANY(string_to_array(:no_online, ','))))
                    OR ST_DWithin(
                         s.coordinates::geography,
                         ST_Point(:lng, :lat)::geography,
                         :radius_m
                       )"""
        params["radius_m"] = radius_km * 1000

    # I prezzi stantii restano visibili ma marcati stale=true (il frontend
    # decide come mostrarli); i prezzi in quarantena non escono mai.
    stale_expr = stale_price_sql(params, price_alias="p", chain_alias="c")

    result = await db.execute(
        text(f"""
            SELECT
                p.price, p.original_price, p.promo_label,
                p.price_per_unit, p.in_stock, p.scraped_at,
                p.source, p.promo_expires,
                ({stale_expr}) AS stale,
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
              AND NOT p.quarantined
              AND p.price >= :min_valid_price
              AND s.is_active   = TRUE
              AND ({geo_filter})
            ORDER BY p.in_stock DESC, p.price ASC
            LIMIT 30
        """),
        params,
    )
    offerte = [dict(r) for r in result.mappings().all()]

    # Provenienza dichiarata: senza, l'app mette accanto il prezzo dell'e-commerce
    # e l'offerta da volantino come se fossero la stessa cosa. Chi va in negozio
    # e paga di piu' non torna. original_price e promo_label sono la prova
    # promozionale della singola riga: senza di loro una riga di catena
    # discount passerebbe per offerta solo perche' viene da quella catena.
    # La riga qui c'e' tutta (la SELECT porta anche p.price), quindi la legge
    # origine_di/descrizione_di: elencare i campi a mano lasciava fuori
    # `price` e teneva la prova del barrato larga (original_price > 0) invece
    # di quella stretta (> price) usata nel resto del progetto.
    #
    # `sconto_affermabile` e' il permesso di affermare un risparmio, ed e'
    # questa la superficie dove il danno era vivo: GET /products/{id}/prices
    # non filtra la freschezza, e le 58.500 righe eurospin_web col barrato e
    # la scadenza GIA' PASSATA arrivano davvero alla card, che su quelle
    # mostrava il prezzo barrato e il badge arancione "Risparmi EUR X". Caso
    # vero: "IDROPULITRICE AD ALTA PRESSIONE", Eurospin Trento, 59,99 con
    # 69,99 barrato, promo_expires 2026-08-23: adesso il campo e' False e la
    # card non afferma niente.
    # Le altre 1.289.606 righe eurospin_web col barrato e NESSUNA scadenza
    # restano affermabili, ed e' giusto: un barrato senza scadenza e' uno
    # sconto che la catena dichiara, solo senza termine noto — che e' cio' che
    # dice `origine_label`. Pretendere la categoria "volantino", come faceva
    # il giro scorso, le buttava via tutte insieme a quelle scadute.
    for o in offerte:
        o["origine"] = origine_di(o)
        o["origine_label"] = descrizione_di(o)
        o["sconto_affermabile"] = sconto_affermabile_di(o)

    # E' la lista dove l'offerta volantino Lidl sta sopra al prezzo e-commerce
    # Esselunga, quindi l'avviso riguarda l'INSIEME, non la singola riga.
    # L'endpoint restituisce pero' una lista nuda, consumata dal frontend come
    # array: incartarla in un oggetto romperebbe la pagina prodotto. L'avviso
    # va quindi su ogni riga, ed e' None quando le origini sono omogenee: un
    # avviso sempre presente non protegge nessuno. `confronto_misto` e' stato
    # rimosso: era la copia booleana di `avviso is not None`, nessuno la
    # leggeva, e due campi che dicono la stessa cosa prima o poi divergono.
    avviso_lista = avviso_origini([o["origine"] for o in offerte])
    for o in offerte:
        o["avviso"] = avviso_lista

    # Per le catene con prezzo nazionale (un unico punto "online"/"offerte") la
    # distanza sarebbe vuota, e l'utente non saprebbe di avere quell'insegna a
    # due passi. Recuperiamo il punto vendita piu' vicino della stessa catena
    # con UNA query a parte: dentro la query principale lo stesso controllo
    # costava decine di secondi.
    virtuali = {o["chain_slug"] for o in offerte if o.get("is_online") or o.get("distance_km") is None}
    if virtuali and lat is not None and lng is not None:
        vicini = await db.execute(
            text("""
                SELECT DISTINCT ON (c.slug)
                       c.slug AS chain_slug,
                       s.name AS nearest_store_name,
                       ROUND((ST_Distance(
                           s.coordinates::geography,
                           ST_SetSRID(ST_Point(:lng, :lat), 4326)::geography
                       ) / 1000)::numeric, 2) AS nearest_store_km
                FROM stores s
                JOIN chains c ON s.chain_id = c.id
                WHERE s.is_active
                  AND c.slug = ANY(string_to_array(:slugs, ','))
                  AND s.external_id NOT LIKE '%-online'
                  AND s.external_id NOT LIKE '%-offerte'
                  AND ST_DWithin(
                        s.coordinates::geography,
                        ST_SetSRID(ST_Point(:lng, :lat), 4326)::geography,
                        :radius_m
                      )
                ORDER BY c.slug, s.coordinates <-> ST_SetSRID(ST_Point(:lng, :lat), 4326)
            """),
            {"lat": lat, "lng": lng, "slugs": ",".join(sorted(virtuali)),
             "radius_m": (radius_km or 10) * 1000},
        )
        per_catena = {r["chain_slug"]: r for r in vicini.mappings().all()}
        for o in offerte:
            v = per_catena.get(o["chain_slug"])
            if v:
                o["nearest_store_name"] = v["nearest_store_name"]
                o["nearest_store_km"] = float(v["nearest_store_km"])
                # Questi due campi dicono dove la catena ha un punto vendita,
                # NON dove vale il prezzo qui sopra: il prezzo e' quello dello
                # store nazionale della spesa online. Chi li stampa accanto al
                # prezzo deve stampare anche questa frase, altrimenti mette un
                # indirizzo accanto a un numero che in quel negozio non vale.
                o["nearest_store_nota"] = NEAREST_STORE_NOTA

    return offerte


@router.get("/{product_id}/price-history")
async def get_price_history(
    product_id: str,
    store_id: Optional[str] = Query(None),
    days: int = Query(90, ge=7, le=365),
    db: AsyncSession = Depends(get_db),
):
    filters = [
        "product_id = :product_id",
        "scraped_at > NOW() - make_interval(days => :days)",
        "NOT quarantined",
    ]
    params: dict = {"product_id": product_id, "days": days}

    if store_id:
        filters.append("store_id = :store_id")
        params["store_id"] = store_id

    result = await db.execute(
        text(f"""
            SELECT store_id, price, scraped_at, source, promo_expires,
                   original_price,
                   NULLIF(TRIM(promo_label), '') AS promo_label
            FROM prices
            WHERE {" AND ".join(filters)}
            ORDER BY scraped_at
        """),
        params,
    )
    punti = [dict(r) for r in result.mappings().all()]

    # Nel grafico l'omissione fa il danno peggiore: una settimana di volantino
    # dentro una serie di listino disegna un crollo e una risalita che il
    # prodotto non ha mai avuto, e l'utente la legge come andamento reale.
    # Questa query non filtra is_current ne' la catena, quindi mescola
    # rilevazioni di sorgenti diverse anche sullo stesso negozio.
    # Ogni punto va letto col calendario della SUA data, non con quello di
    # oggi: un punto del 12/08 con promo_expires 18/08 in quella settimana era
    # una promo in corso ("offerta volantino, fino al 18/08"), non "offerta
    # scaduta il 18/08" - una frase che parla di adesso e non di allora, e che
    # altrimenti esce su quasi tutta la parte vecchia della serie. scraped_at
    # e' gia' selezionato dalla query, quindi il dato per farlo bene e' nella
    # stessa riga.
    for p in punti:
        quando = p.get("scraped_at")
        quando = quando.date() if isinstance(quando, datetime) else quando
        p["origine"] = origine_di(p, quando)
        p["origine_label"] = descrizione_di(p, quando)
    return punti


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
            JOIN prices pr ON pr.product_id = p.id AND pr.is_current = TRUE
                          AND NOT pr.quarantined AND pr.price >= :min_valid_price
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

    # Igiene dati come in /search: min/max, store_count e offerte (rich
    # snippet AggregateOffer) considerano solo prezzi freschi per la catena.
    offer_params: dict = {"id": product_id, "min_valid_price": MIN_VALID_PRICE}
    fresh_offers = fresh_price_sql(offer_params, price_alias="pr", chain_alias="c")
    offers = await db.execute(
        text(
            f"""
            SELECT DISTINCT ON (c.id)
                   c.name AS chain_name, c.slug AS chain_slug,
                   pr.price, pr.in_stock, c.shop_url, pr.product_url,
                   pr.source, pr.promo_expires,
                   pr.original_price,
                   NULLIF(TRIM(pr.promo_label), '') AS promo_label
            FROM prices pr
            JOIN stores s ON pr.store_id = s.id
            JOIN chains c ON s.chain_id = c.id
            WHERE pr.product_id = :id AND pr.is_current = TRUE AND NOT pr.quarantined
              AND pr.price >= :min_valid_price AND s.is_active = TRUE
              AND {fresh_offers}
            ORDER BY c.id, pr.in_stock DESC, pr.price ASC
            """
        ),
        offer_params,
    )
    offer_list = sorted(
        [dict(o) for o in offers.mappings().all()],
        key=lambda x: (not bool(x.get("in_stock", True)), float(x["price"])),
    )
    # Qui l'omissione esce dall'app: min_price/max_price finiscono nello
    # structured data pubblico (AggregateOffer) verso Google, dove un prezzo da
    # volantino presentato come prezzo d'offerta e' una dichiarazione
    # commerciale, non solo un dato UI. E il DISTINCT ON ... ORDER BY pr.price
    # ASC tiene per catena la riga piu' economica, cioe' preferisce
    # sistematicamente il volantino al listino.
    # origine_di()/descrizione_di() e non i campi elencati a mano: elencarli
    # lasciava fuori `price` (che la riga ha: tre righe sotto si legge con
    # float(o["price"])), e senza `price` la prova del barrato ricade su quella
    # LARGA, `original_price > 0`, invece di quella STRETTA,
    # `original_price > price`, che usano il predicato SQL e tutte le altre
    # superfici.
    # Cosa cambiava davvero, ESEGUITO sulle due forme della chiamata invece di
    # dedurlo: su una riga eurospin_web con barrato 1,00 e prezzo 1,50 la
    # CATEGORIA non cambia ("negozio" in entrambi i casi, perche' per origine()
    # la prova conta solo sulle sorgenti mappate volantino), ma l'ETICHETTA
    # prima diventava "prezzo in negozio, sconto dichiarato dalla catena,
    # termine non indicato", cioe' affermava uno sconto su una riga il cui
    # barrato e' PIU' BASSO del prezzo. Quell'etichetta finisce nello
    # structured data pubblico verso Google. Sono 9.584 righe correnti,
    # misurate il 30/10/2026, tutte eurospin_web e nessuna fresca: il difetto
    # era latente e si armava al rientro di quella sorgente in finestra.
    # Sulle sorgenti mappate volantino (md, lidl, aldi, penny) cambiava anche
    # la categoria, da "volantino" a "negozio"; oggi righe cosi' non ce ne
    # sono, perche' quelle 9.584 sono tutte di una sorgente di listino.
    for o in offer_list:
        o["origine"] = origine_di(o)
        o["origine_label"] = descrizione_di(o)

    available_prices = [float(o["price"]) for o in offer_list if o.get("in_stock") is not False]
    prices = available_prices or [float(o["price"]) for o in offer_list]
    return {
        **dict(row),
        "min_price": min(prices) if prices else None,
        "max_price": max(prices) if prices else None,
        "store_count": len(offer_list),
        "offers": offer_list,
        "avviso": avviso_origini([o["origine"] for o in offer_list]),
    }

