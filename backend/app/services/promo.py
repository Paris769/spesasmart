"""
Promo score (Fase 2): la "promo" e' vera o gonfiata?

Per ogni negozio dove il prezzo corrente del prodotto e' in promozione
(original_price > price OPPURE promo_label valorizzata), confrontiamo il
prezzo corrente con la MEDIANA dei prezzi dello stesso prodotto nello stesso
negozio negli ultimi 60 giorni (tutte le rilevazioni, anche is_current=FALSE,
escluse quelle in quarantena).

Verdetti:
  - "insufficient_history"  < 3 rilevazioni negli ultimi 60gg
  - "fake_promo"            prezzo corrente >= mediana (la promo non e' sotto
                            il prezzo abituale)
  - "true_promo"            prezzo corrente <= mediana * 0.85
  - "weak_promo"            prezzo corrente <= mediana * 0.95 (e la fascia
                            residua sotto mediana: sconto marginale)

NOTA: questo modulo dipende solo da sqlalchemy (e da app.core.freshness, che
non ha dipendenze) — e' importato anche da scripts/weekly_digest.py fuori dal
contesto FastAPI.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.freshness import fresh_price_sql

HISTORY_DAYS = 60
MIN_OBSERVATIONS = 3
TRUE_PROMO_RATIO = 0.85
WEAK_PROMO_RATIO = 0.95
# Soglia sotto cui un prezzo e' un placeholder (es. 0.01), non un'offerta.
# Definita QUI (stesso valore degli endpoint) per non importare dagli endpoint
# FastAPI e creare import circolari.
MIN_VALID_PRICE = 0.10

# Regola di serving (vedi core/freshness): nei verdetti promo entrano solo
# prezzi correnti di negozi attivi, sopra la soglia minima e abbastanza
# freschi per la loro catena. Frammento e bind param deterministici,
# costruiti una volta a livello di modulo.
_FRESH_PARAMS: dict = {}
_FRESH_SQL = fresh_price_sql(_FRESH_PARAMS, price_alias="pr", chain_alias="c")

_CURRENT_PROMOS_SQL = text(f"""
    SELECT pr.store_id::text AS store_id,
           s.name            AS store_name,
           c.name            AS chain_name,
           pr.price          AS current_price,
           pr.original_price AS original_price,
           pr.promo_label    AS promo_label
    FROM prices pr
    JOIN stores s ON s.id = pr.store_id
    JOIN chains c ON s.chain_id = c.id
    WHERE pr.product_id = :pid
      AND pr.is_current = TRUE
      AND pr.quarantined = FALSE
      AND pr.price >= :min_valid_price
      AND s.is_active = TRUE
      AND {_FRESH_SQL}
      AND (
            (pr.original_price IS NOT NULL AND pr.original_price > pr.price)
            OR pr.promo_label IS NOT NULL
          )
""")

_HISTORY_SQL = text("""
    SELECT store_id::text AS store_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY price) AS median_60d,
           count(*) AS n_obs
    FROM prices
    WHERE product_id = :pid
      AND quarantined = FALSE
      AND scraped_at > NOW() - make_interval(days => :days)
    GROUP BY store_id
""")


def _verdict(current_price: float, median: float | None, n_obs: int) -> str:
    if median is None or n_obs < MIN_OBSERVATIONS:
        return "insufficient_history"
    if current_price >= median:
        return "fake_promo"
    if current_price <= median * TRUE_PROMO_RATIO:
        return "true_promo"
    return "weak_promo"


async def compute_promo_check(db: AsyncSession, product_id: str) -> list[dict]:
    """
    Ritorna un check per ogni store con prezzo corrente in promo:
      [{store_id, store_name, chain_name, current_price, median_60d,
        discount_pct, verdict}]
    Lista vuota se il prodotto non e' in promo da nessuna parte.
    """
    promos = (await db.execute(
        _CURRENT_PROMOS_SQL,
        {"pid": product_id, "min_valid_price": MIN_VALID_PRICE, **_FRESH_PARAMS},
    )).mappings().all()
    if not promos:
        return []

    history = (await db.execute(
        _HISTORY_SQL, {"pid": product_id, "days": HISTORY_DAYS}
    )).mappings().all()
    hist_by_store = {h["store_id"]: h for h in history}

    checks: list[dict] = []
    for row in promos:
        price = float(row["current_price"])
        hist = hist_by_store.get(row["store_id"])
        median = float(hist["median_60d"]) if hist and hist["median_60d"] is not None else None
        n_obs = int(hist["n_obs"]) if hist else 0

        discount_pct = None
        if median and median > 0:
            discount_pct = round((1 - price / median) * 100, 1)

        checks.append({
            "store_id": row["store_id"],
            "store_name": row["store_name"],
            "chain_name": row["chain_name"],
            "current_price": price,
            "median_60d": round(median, 2) if median is not None else None,
            "discount_pct": discount_pct,
            "verdict": _verdict(price, median, n_obs),
        })
    return checks
