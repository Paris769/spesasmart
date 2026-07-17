"""
Soglie di freschezza dei prezzi per il SERVING (ricerca, confronto, ottimizzazione).

NON riguarda lo scraping (per quello vedi scraping/guardian.py FRESHNESS_HOURS):
qui si decide quando un prezzo e' troppo vecchio per essere considerato
affidabile nei confronti tra catene, nei minimi mostrati e nei piani di
ottimizzazione della spesa.

Default: 7 giorni. Le catene servite da CosiComodo (famila, ilgigante,
italmark - slug verificati sulla tabella chains) vengono scrapate a rotazione
sui punti vendita: un giro completo richiede piu' giorni, quindi hanno una
tolleranza di 10 giorni.
"""

DEFAULT_MAX_AGE_HOURS = 7 * 24  # 7 giorni

CHAIN_MAX_AGE_HOURS: dict[str, int] = {
    "famila": 10 * 24,
    "ilgigante": 10 * 24,
    "italmark": 10 * 24,
}


def max_age_hours(chain_slug: str) -> int:
    """Ore massime di eta' oltre cui un prezzo della catena non e' affidabile."""
    return CHAIN_MAX_AGE_HOURS.get(chain_slug, DEFAULT_MAX_AGE_HOURS)


def fresh_price_sql(params: dict, price_alias: str = "p", chain_alias: str = "c") -> str:
    """
    Espressione SQL booleana (riutilizzabile in WHERE o in SELECT): TRUE se il
    prezzo e' abbastanza recente per la sua catena.

    Aggiunge a `params` i bind param necessari (nomi deterministici: la
    chiamata e' idempotente, si puo' invocare piu' volte sullo stesso dict).
    Richiede che nella query siano in scope un alias della tabella prices
    (colonna scraped_at) e uno della tabella chains (colonna slug).
    """
    buckets: dict[int, list[str]] = {}
    for slug, hours in CHAIN_MAX_AGE_HOURS.items():
        buckets.setdefault(hours, []).append(slug)

    whens: list[str] = []
    for i, hours in enumerate(sorted(buckets)):
        params[f"fresh_slugs_{i}"] = sorted(buckets[hours])
        params[f"fresh_hours_{i}"] = hours
        # CAST(... AS text[]) e non :param::text[]: il parser dei bind di
        # SQLAlchemy text() NON riconosce :param seguito subito da '::'
        # (verificato: arriva letterale a Postgres -> syntax error).
        # Cast ::int esplicito sui rami del CASE: sono bind param non tipati e
        # Postgres li risolverebbe a text, rompendo make_interval(hours => ...).
        whens.append(
            f"WHEN {chain_alias}.slug = ANY(CAST(:fresh_slugs_{i} AS text[])) "
            f"THEN (:fresh_hours_{i})::int"
        )
    params["fresh_hours_default"] = DEFAULT_MAX_AGE_HOURS

    case_sql = " ".join(whens)
    return (
        f"{price_alias}.scraped_at >= NOW() - make_interval(hours => "
        f"CASE {case_sql} ELSE (:fresh_hours_default)::int END)"
    )


def stale_price_sql(params: dict, price_alias: str = "p", chain_alias: str = "c") -> str:
    """Negazione di fresh_price_sql: TRUE se il prezzo e' stantio."""
    return f"NOT ({fresh_price_sql(params, price_alias, chain_alias)})"
