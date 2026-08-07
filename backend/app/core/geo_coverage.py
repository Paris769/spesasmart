"""
Copertura geografica delle catene con spesa online.

Alcune catene (Esselunga, Carrefour, Conad) sono nel database con UN SOLO
"negozio online" nazionale: nelle query venivano quindi inclusi sempre, a
qualunque distanza. Risultato: un utente in Sardegna vedeva prezzi Esselunga —
catena che in Sardegna non ha punti vendita né consegna — a 507 km di distanza.

Qui teniamo, come dato esplicito e correggibile, in quali regioni ciascuna
catena online è effettivamente disponibile. Le regioni sono riconosciute da
lat/lng con riquadri (bounding box): approssimati sui confini interni, ma più
che sufficienti per il caso che conta davvero — isole e macro-aree.

Se un domani avremo le aree di consegna reali (per CAP), questo modulo è il
punto unico da sostituire.
"""
from typing import Optional

# ── Regioni italiane ────────────────────────────────────────────────────────
# (nome, lat_min, lat_max, lng_min, lng_max, lat_centro, lng_centro)
# I riquadri sono generosi e si sovrappongono (es. Milano cade sia nel riquadro
# lombardo sia in quello piemontese): fra i candidati vince quindi la regione
# con il CENTRO più vicino, non la prima dell'elenco.
_REGIONI: list[tuple[str, float, float, float, float, float, float]] = [
    ("sardegna", 38.80, 41.35, 8.10, 9.85, 40.10, 9.00),
    ("sicilia", 36.60, 38.35, 12.35, 15.70, 37.60, 14.00),
    ("calabria", 37.90, 40.15, 15.60, 17.25, 39.00, 16.40),
    ("puglia", 39.75, 42.25, 14.90, 18.55, 41.00, 16.60),
    ("basilicata", 39.85, 41.15, 15.30, 16.90, 40.50, 16.10),
    ("campania", 39.95, 41.55, 13.75, 15.80, 40.80, 14.80),
    ("molise", 41.35, 42.10, 14.10, 15.20, 41.70, 14.60),
    ("abruzzo", 41.65, 42.90, 13.00, 14.80, 42.20, 13.80),
    ("marche", 42.65, 43.98, 12.20, 13.95, 43.40, 13.10),
    ("umbria", 42.35, 43.65, 11.85, 13.30, 42.90, 12.50),
    ("lazio", 40.75, 42.85, 11.40, 14.05, 41.90, 12.60),
    ("toscana", 42.20, 44.50, 9.65, 12.40, 43.40, 11.20),
    ("emilia-romagna", 43.70, 45.15, 9.15, 12.80, 44.50, 11.20),
    ("liguria", 43.75, 44.70, 7.45, 10.10, 44.30, 8.80),
    ("piemonte", 44.05, 46.50, 6.60, 9.25, 45.00, 7.90),
    ("valle-aosta", 45.45, 46.00, 6.75, 7.95, 45.72, 7.40),
    ("lombardia", 44.65, 46.65, 8.45, 11.45, 45.60, 9.90),
    ("trentino", 45.65, 47.10, 10.35, 12.50, 46.40, 11.30),
    ("veneto", 44.75, 46.70, 10.60, 13.15, 45.60, 11.90),
    ("friuli", 45.55, 46.65, 12.30, 13.95, 46.10, 13.10),
]


def region_for(lat: Optional[float], lng: Optional[float]) -> Optional[str]:
    """Regione italiana per una coordinata (None se fuori dai riquadri noti)."""
    if lat is None or lng is None:
        return None
    candidate = [
        (nome, lat_c, lng_c)
        for nome, la_min, la_max, ln_min, ln_max, lat_c, lng_c in _REGIONI
        if la_min <= lat <= la_max and ln_min <= lng <= ln_max
    ]
    if not candidate:
        return None
    # Distanza approssimata (equirettangolare): alle latitudini italiane un
    # grado di longitudine vale circa 0,7 gradi di latitudine.
    def _dist(c: tuple[str, float, float]) -> float:
        return (lat - c[1]) ** 2 + ((lng - c[2]) * 0.7) ** 2

    return min(candidate, key=_dist)[0]


# ── Dove ciascuna catena "solo online" è realmente disponibile ───────────────
# Fonte: presenza dei punti vendita/aree di consegna dichiarate dalle insegne.
# Vale SOLO per le catene senza negozi geolocalizzati nel database: le altre
# (Famila, Eurospin, Iper, Il Gigante, Italmark…) sono già filtrate per distanza.
ONLINE_COVERAGE: dict[str, set[str]] = {
    # Esselunga non ha punti vendita al Sud, nelle isole né nel Nord-Est estremo.
    "esselunga": {
        "lombardia", "piemonte", "liguria", "veneto", "emilia-romagna",
        "toscana", "lazio", "marche",
    },
    # Carrefour: consegna a domicilio concentrata su Nord e Centro.
    "carrefour": {
        "lombardia", "piemonte", "liguria", "veneto", "emilia-romagna",
        "toscana", "lazio", "friuli", "trentino", "valle-aosta", "umbria",
        "marche", "abruzzo",
    },
    # Conad è presente in tutta Italia, isole comprese.
    "conad": {r[0] for r in _REGIONI},
    # Coop/EasyCoop: consegna nelle regioni storiche di presenza.
    "coop": {
        "toscana", "emilia-romagna", "lombardia", "piemonte", "liguria",
        "veneto", "lazio", "umbria", "marche",
    },
    # Pam a Casa: aree urbane del Centro-Nord.
    "pam": {
        "lombardia", "veneto", "emilia-romagna", "toscana", "lazio", "piemonte",
        "friuli", "liguria",
    },
}


def online_store_sql(chain_alias: str = "c") -> str:
    """
    Frammento SQL da usare al posto del nudo `s.external_id LIKE '%-online'`.

    Un negozio online entra nei risultati solo se la sua catena serve davvero la
    zona dell'utente. Richiede il bind param `no_online` (lista di slug).
    """
    return (
        f"(s.external_id LIKE '%-online' "
        f"AND NOT ({chain_alias}.slug = ANY(CAST(:no_online AS text[]))))"
    )


def unavailable_online_chains(lat: Optional[float], lng: Optional[float]) -> list[str]:
    """
    Catene con spesa online da NON mostrare per quella posizione.

    Se la regione non è riconosciuta (coordinate fuori Italia o in mare) non
    filtriamo nulla: meglio mostrare qualcosa in più che nascondere per errore.
    """
    regione = region_for(lat, lng)
    if regione is None:
        return []
    return [slug for slug, regioni in ONLINE_COVERAGE.items() if regione not in regioni]
