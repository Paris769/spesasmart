"""
Promo score (Fase 2): la "promo" e' vera o gonfiata?

Per ogni negozio dove sul prezzo corrente del prodotto si puo' AFFERMARE uno
sconto (la definizione unica di core/price_origin: non dimostrabilmente
scaduta e con una prova di sconto — vedi sconto_affermabile_sql e
_CURRENT_PROMOS_SQL), confrontiamo il prezzo corrente con la MEDIANA dei
prezzi dello stesso prodotto nello stesso negozio negli ultimi 60 giorni
(tutte le rilevazioni, anche is_current=FALSE, escluse quelle in quarantena).

Verdetti:
  - "insufficient_history"  < 3 rilevazioni negli ultimi 60gg
  - "fake_promo"            prezzo corrente >= mediana (la promo non e' sotto
                            il prezzo abituale)
  - "true_promo"            prezzo corrente <= mediana * 0.85
  - "weak_promo"            prezzo corrente <= mediana * 0.95 (e la fascia
                            residua sotto mediana: sconto marginale)

Non esiste piu' un verdetto "promo_non_viva": una riga su cui lo sconto non e'
affermabile non produce nessun check, perche' non c'e' niente da certificare
(vedi il presidio in compute_promo_check). Ogni check porta
`sconto_affermabile: True`, che e' quello che il frontend legge per mostrare
barrato e percentuale.

NOTA: questo modulo dipende solo da sqlalchemy (e da app.core.freshness, che
non ha dipendenze) — e' importato anche da scripts/weekly_digest.py fuori dal
contesto FastAPI.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.freshness import fresh_price_sql
# price_origin importa solo datetime e typing: resta dentro il vincolo di
# dipendenze dichiarato sopra (questo modulo gira anche fuori da FastAPI).
from app.core.price_origin import (
    descrizione_di,
    sconto_affermabile_sql,
    origine_di,
    sconto_affermabile_di,
)

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
# Il confine di "promozione" lo definisce price_origin, non questa query: vedi
# sconto_affermabile_sql. Aggiunge i suoi bind param allo stesso dict.
_SCONTO_AFFERMABILE_SQL = sconto_affermabile_sql(_FRESH_PARAMS, price_alias="pr")

_CURRENT_PROMOS_SQL = text(f"""
    SELECT pr.store_id::text AS store_id,
           s.name            AS store_name,
           c.name            AS chain_name,
           pr.price          AS current_price,
           -- `price` con il suo nome serve a origine_di/descrizione_di, che
           -- leggono la riga: senza, la prova del barrato resta quella larga
           -- (original_price > 0) invece di quella stretta (> price).
           pr.price          AS price,
           pr.original_price AS original_price,
           pr.promo_label    AS promo_label,
           pr.source         AS source,
           pr.promo_expires  AS promo_expires
    FROM prices pr
    JOIN stores s ON s.id = pr.store_id
    JOIN chains c ON s.chain_id = c.id
    WHERE pr.product_id = :pid
      AND pr.is_current = TRUE
      AND pr.quarantined = FALSE
      AND pr.price >= :min_valid_price
      AND s.is_active = TRUE
      AND {_FRESH_SQL}
      -- "In promozione" = la definizione del modulo
      -- (sconto_affermabile_sql), la stessa di offers.py e
      -- offers_rebuild.py. Quello che prima mancava QUI e c'era in tutte le
      -- altre query di serving e' il FILTRO DI SCADENZA: una riga col barrato
      -- e la scadenza passata entrava fra le promo correnti, origine_di() la
      -- classificava "scaduta" e PromoCheck stampava il riquadro verde
      -- "sconto vero -X%" con accanto il badge rosso "offerta scaduta il
      -- GG/MM". Misurate oggi: 0 righe fresche (difetto latente) ma 93.439
      -- righe correnti col barrato piu' alto del prezzo e la scadenza passata
      -- (eurospin_web 58.500, flyer 34.922, md 17), che entravano appena
      -- tornate fresche. Il conteggio e' per sua natura mobile: dipende da
      -- quali sorgenti sono state riscrappate di recente, quindi va riletto,
      -- non dato per buono (il giro precedente ne contava 112.795).
      -- Il giro precedente aveva aggiunto un SECONDO confine, l'esclusione
      -- delle righe di listino col barrato e nessuna scadenza: e' stato
      -- rimosso, perche' escludeva lo sconto dichiarato dalla catena insieme
      -- all'offerta scaduta. Lo sconto senza termine si certifica, e
      -- l'etichetta dice che il termine non e' indicato.
      -- promo_label non e' mai stato una prova (vedi sconto_affermabile_sql).
      AND {_SCONTO_AFFERMABILE_SQL}
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

# La mediana e' il DENOMINATORE di discount_pct: se nello stesso negozio si
# alternano prezzo di scaffale e settimane di volantino, e' la media di due
# grandezze diverse e va dichiarato insieme al verdetto.
#
# Prima qui c'era array_agg(DISTINCT source) dentro _HISTORY_SQL: classificava
# per SOLA SORGENTE, mentre l'origine e' una decisione per RIGA. Su una storia
# tutta aldi (o tutta eurospin_web) il set collassava sempre a un elemento e il
# flag era strutturalmente False, cioe' falso proprio nel caso per cui era
# stato scritto: righe con prezzo barrato ("volantino") e righe senza
# ("negozio") hanno la stessa `source`.
#
# Qui le combinazioni DISTINTE dei campi che decidono l'origine: original_price
# e' portato solo quando E' una prova (> price), cosi' le combinazioni restano
# poche anche su storie lunghe. Le date tornano come `date` tipizzate (non
# jsonb), perche' price_origin confronta oggetti data, non stringhe.
#
# `scraped_at::date` c'e' perche' l'origine di una riga dello STORICO va
# valutata col calendario del GIORNO IN CUI E' STATA RILEVATA, non con quello
# di oggi: una storia fatta di sole righe da volantino, alcune con scadenza
# ormai passata, produceva {volantino, scaduta}, cioe' due categorie su una
# storia che per natura e' omogenea, e mediana_origini_miste diventava True.
# Il verso dell'errore e' un falso positivo: toglie una certificazione
# corretta. E' lo stesso rimedio di products.py (price-history), dove l'origine
# si calcola passando `quando = p['scraped_at'].date()`.
# Il DISTINCT ora include la data, quindi le combinazioni crescono: restano
# comunque poche, perche' una rilevazione al giorno per negozio su 60 giorni e'
# il caso peggiore.
_HISTORY_ORIGINI_SQL = text("""
    SELECT DISTINCT
           store_id::text   AS store_id,
           source,
           promo_expires,
           scraped_at::date AS giorno,
           CASE WHEN original_price > price THEN original_price END AS original_price
    FROM prices
    WHERE product_id = :pid
      AND quarantined = FALSE
      AND scraped_at > NOW() - make_interval(days => :days)
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
        discount_pct, verdict, origine, origine_label,
        mediana_origini_miste}]
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

    # Origini delle RIGHE che compongono la mediana, negozio per negozio.
    # origine_di() legge la riga: le chiavi che mancano (promo_label, che qui
    # non serve perche' non e' una prova) tornano None senza rompere nulla.
    combo_rows = (await db.execute(
        _HISTORY_ORIGINI_SQL, {"pid": product_id, "days": HISTORY_DAYS}
    )).mappings().all()
    origini_by_store: dict[str, set[str]] = {}
    for c in combo_rows:
        # `giorno` e' la data della rilevazione: l'origine di una riga di due
        # mesi fa si giudica col calendario di allora (vedi la nota sulla
        # query), altrimenti una promo finita nel frattempo risulta "scaduta"
        # e una storia omogenea sembra mista.
        origini_by_store.setdefault(c["store_id"], set()).add(
            origine_di(c, c.get("giorno"))
        )

    checks: list[dict] = []
    for row in promos:
        price = float(row["current_price"])
        hist = hist_by_store.get(row["store_id"])
        median = float(hist["median_60d"]) if hist and hist["median_60d"] is not None else None
        n_obs = int(hist["n_obs"]) if hist else 0

        discount_pct = None
        if median and median > 0:
            discount_pct = round((1 - price / median) * 100, 1)

        # "sconosciuta" non e' una categoria diversa: e' assenza di categoria,
        # e contarla come seconda natura accenderebbe l'avviso su una
        # sorgente non mappata invece che su un confronto davvero misto.
        origini_storico = {
            o for o in origini_by_store.get(row["store_id"], set())
            if o != "sconosciuta"
        }

        # ── IL PRESIDIO, IN UNA FORMA SOLA ─────────────────────────────────
        # Il verdetto certifica uno SCONTO: dove lo sconto non e' affermabile
        # non c'e' niente da certificare, e "sconto vero -25%" su un'offerta
        # finita e' la frase che manda l'utente in negozio a pagare il prezzo
        # pieno.
        #
        # Prima qui il verdetto veniva DECLASSATO a "promo_non_viva", un
        # valore in piu' che ogni consumatore doveva conoscere (l'email del
        # digest, il frontend, il contratto di rebuild.py) e che nessuno
        # produceva mai: il predicato SQL qui sopra e questa funzione nascono
        # dallo stesso punto di price_origin, quindi il ramo era irraggiungibile
        # e il presidio non proteggeva nulla — mentre il riquadro in fondo al
        # ramo restava. Adesso la riga che non supera il controllo non produce
        # nessun check: senza riga non c'e' riquadro da smentire, e il campo
        # `sconto_affermabile` di ogni check e' True per costruzione.
        #
        # E il controllo E' raggiungibile, benche' raramente: il predicato SQL
        # confronta la scadenza con (now() AT TIME ZONE 'Europe/Rome')::date
        # dentro Postgres, questa funzione con la data civile italiana calcolata
        # in Python qualche millisecondo dopo. A cavallo della mezzanotte
        # italiana le due date differiscono: una riga con scadenza ieri letta a
        # 23:59:59,999 passa il filtro SQL (ieri >= ieri) e viene fermata qui,
        # dove la data e' gia' oggi. VERIFICATO eseguendo le due date sullo
        # stesso istante: SQL ammette, sconto_affermabile_di() dice False.
        #
        # NON vale invece il secondo motivo che qui era scritto prima, cioe' il
        # rimedio senza tzdata di price_origin: quel rimedio usa UTC+1, che
        # d'estate (Italia UTC+2) e' INDIETRO di un'ora rispetto a Postgres,
        # quindi la sua data e' uguale o PRECEDENTE. Una data precedente rende
        # promo_in_corso piu' facile da soddisfare, non piu' difficile: il
        # rimedio e' sempre piu' permissivo del filtro SQL e non puo' far
        # scartare qui una riga che il filtro ha ammesso. Misurato: all'istante
        # 2026-08-23 22:30 UTC Postgres legge 2026-08-24 e il rimedio
        # 2026-08-23, e su una scadenza 2026-08-23 il filtro SQL esclude
        # mentre Python affermerebbe — la direzione opposta a quella che
        # servirebbe al presidio.
        if not sconto_affermabile_di(row):
            continue

        checks.append({
            "store_id": row["store_id"],
            "store_name": row["store_name"],
            "chain_name": row["chain_name"],
            "current_price": price,
            "median_60d": round(median, 2) if median is not None else None,
            "discount_pct": discount_pct,
            "verdict": _verdict(price, median, n_obs),
            # Permesso di affermare un risparmio: vedi core/price_origin. Qui
            # e' sempre True — le righe che non lo sono non arrivano a questa
            # riga di codice (vedi il presidio sopra) — e il campo c'e'
            # comunque perche' il frontend legge QUESTO, non la categoria, per
            # decidere se mostrare barrato e percentuale: dedurlo dall'origine
            # sarebbe riscrivere lato client una regola del backend.
            "sconto_affermabile": True,
            # L'origine si decide per RIGA: origine_di/descrizione_di leggono
            # la riga del DB cosi' com'e' (source, promo_expires,
            # original_price, promo_label), senza che il chiamante debba
            # ricordarsi quali campi servono.
            "origine": origine_di(row),
            "origine_label": descrizione_di(row),
            # Il verdetto confronta il prezzo con la mediana dello stesso
            # negozio: se la mediana nasce da origini diverse, "true_promo" non
            # poggia su un termine di confronto omogeneo e chi legge deve
            # saperlo.
            "mediana_origini_miste": len(origini_storico) > 1,
        })
    return checks
