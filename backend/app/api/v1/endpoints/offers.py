"""
Offerte vicino a te: GET /api/v1/offers/nearby.

Aggrega le migliori promozioni correnti nella zona dell'utente. "Offerta" ha
UNA definizione in tutta l'app ed e' quella di core/price_origin: una riga su
cui si puo' AFFERMARE uno sconto, cioe' non dimostrabilmente scaduta e con una
prova di sconto (barrato piu' alto del prezzo, oppure una sorgente che per
natura E' un volantino). Il predicato SQL non la riscrive, lo genera il modulo
(sconto_affermabile_sql, usato nella CTE `best`). promo_label non e' mai stato
una prova. Una promozione con la scadenza GIA' PASSATA non entra: e' il solo
confine che il giro precedente aveva ragione di mettere.
NON e' richiesto che la riga sia di categoria "volantino": provenienza e
sconto sono due domande diverse, e pretendere la categoria qui aveva svuotato
la pagina fuori dal Nord (vedi la misura accanto al predicato). Entrano i
negozi fisici entro il raggio piu' gli store online nazionali (solo se la
catena serve la zona, vedi core/geo_coverage). Una sola offerta - la migliore
- per coppia (prodotto, catena).

Ordinamento ROUND-ROBIN fra le catene (vedi CTE `ranked`): l'ordinamento
globale per sconto percentuale premiava sistematicamente le insegne che
espongono il prezzo barrato con percentuali alte (carrefour, esselunga,
famila) ed escludeva del tutto i discount, che hanno prezzi bassi in assoluto
ma sconti percentuali piu' contenuti - e con loro tutte le promo da volantino.
Ora ogni catena presente in zona entra con la sua offerta migliore prima che
una qualsiasi catena piazzi la seconda.

Regole di serving identiche al resto dell'app: is_current, NOT quarantined,
prezzo minimo valido, store attivi, freshness per catena (core/freshness).

PROVENIENZA: ogni riga porta `origine` (che TIPO di prezzo e'),
`origine_label` (il testo pronto, con la scadenza quando c'e' o la nota
"termine non indicato" quando non c'e') e `sconto_affermabile` (se si puo'
affermare un risparmio) — tutti e tre da core/price_origin, che e' l'unica
fonte di verita'. Il campo si chiamava `promozione_viva` fino al giro scorso,
quando fondeva le due domande; il nome nuovo dice cosa decide.
Porta anche `avviso`, valorizzato con lo stesso testo su tutte le righe SOLO
quando l'insieme restituito mescola nature di prezzo diverse.
Con `chain` o `source=flyer` le origini sono spesso omogenee e l'avviso e'
null: un avviso sempre presente e' un avviso che l'utente impara a saltare.
`source=flyer` significa "solo righe che l'app chiama offerta da volantino", e
la selezione la fa price_origin.origine() riga per riga, non un predicato SQL
sulla sorgente (quello escludeva le righe md/lidl/aldi/penny/iper che la card
accanto etichetta "offerta volantino"). Da questo giro quel filtro toglie
anche gli sconti di catalogo (origine "online"/"negozio"), che senza filtro
entrano fra le offerte.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.freshness import fresh_price_sql
from app.core.geo_coverage import unavailable_online_chains
from app.core.price_origin import (
    avviso as avviso_origini,
    descrizione_di,
    sconto_affermabile_sql,
    origine_di,
    sconto_affermabile_di,
)
from app.db.session import get_db

router = APIRouter(prefix="/offers", tags=["offers"])

MIN_VALID_PRICE = 0.10  # come in products.py/stores.py: sotto e' placeholder/errore


@router.get("/nearby")
async def get_nearby_offers(
    response: Response,
    lat: float = Query(..., description="Latitudine utente"),
    lng: float = Query(..., description="Longitudine utente"),
    radius_km: float = Query(5.0, ge=0.5, le=50, description="Raggio in km"),
    limit: int = Query(60, ge=1, le=120),
    chain: Optional[str] = Query(None, description="Filtra per slug catena"),
    source: str = Query(
        "all",
        pattern="^(flyer|all)$",
        description="'flyer' = solo offerte da volantino; 'all' = tutte",
    ),
    db: AsyncSession = Depends(get_db),
):
    # Le promo cambiano poche volte al giorno: cache 15 minuti.
    response.headers["Cache-Control"] = "public, max-age=900"

    # "Solo volantino" non e' piu' `AND p.source = 'flyer'`: l'etichetta che
    # la pagina mostra accanto al prezzo viene da price_origin, che decide per
    # RIGA (una riga aldi senza prezzo barrato ne' scadenza e' il prezzo di
    # scaffale, non un volantino). Un predicato SQL che rifacesse quella
    # decisione sarebbe una seconda copia della stessa logica, destinata a
    # divergere: e' esattamente la contraddizione che il filtro aveva
    # (etichetta "offerta volantino" sulla card, card che spariva attivando
    # "Solo volantino"). Quindi la query resta larga e la selezione finale si
    # fa in Python su origine(). Si pesca piu' largo del richiesto perche'
    # dopo il filtro le righe restino abbastanza a riempire `limit`.
    row_limit = min(limit * 6, 600) if source == "flyer" else limit

    params: dict = {
        "lat": lat,
        "lng": lng,
        "radius_m": radius_km * 1000,
        "row_limit": row_limit,
        "chain": chain,
        "min_valid_price": MIN_VALID_PRICE,
        # catene con spesa online che NON servono la zona: escluse
        "no_online": unavailable_online_chains(lat, lng),
    }
    # La CTE nearby_stores espone lo slug della catena come colonna "slug",
    # cosi' fresh_price_sql puo' usarla come alias catena (ns.slug).
    fresh = fresh_price_sql(params, price_alias="p", chain_alias="ns")
    # La definizione di offerta viene dalla mappa di price_origin, non da un
    # predicato scritto a mano qui: vedi sconto_affermabile_sql.
    affermabile = sconto_affermabile_sql(params, price_alias="p")

    result = await db.execute(
        text(f"""
            WITH nearby_stores AS MATERIALIZED (
                SELECT s.id,
                       s.name AS store_name,
                       c.id   AS chain_id,
                       c.slug,
                       c.name AS chain_name,
                       CASE
                           WHEN s.external_id LIKE '%-online' THEN NULL
                           ELSE ROUND(ST_Distance(
                               s.coordinates::geography,
                               ST_Point(:lng, :lat)::geography
                           )::numeric / 1000, 2)
                       END AS distance_km
                FROM stores s
                JOIN chains c ON s.chain_id = c.id
                WHERE s.is_active = TRUE
                  AND (CAST(:chain AS text) IS NULL OR c.slug = :chain)
                  AND (
                        (s.external_id LIKE '%-online'
                         AND NOT (c.slug = ANY(string_to_array(:no_online, ','))))
                        OR ST_DWithin(
                             s.coordinates::geography,
                             ST_Point(:lng, :lat)::geography,
                             :radius_m
                           )
                      )
            ),
            best AS (
                -- migliore offerta per coppia (prodotto, catena): sconto piu'
                -- alto, a parita' di sconto il prezzo piu' basso
                SELECT DISTINCT ON (p.product_id, ns.chain_id)
                       p.product_id,
                       ns.slug AS chain_slug,
                       ns.chain_name,
                       ns.store_name,
                       ns.distance_km,
                       p.price,
                       p.original_price,
                       CASE
                           WHEN p.original_price > p.price
                           THEN (ROUND(
                               (p.original_price - p.price)
                               / p.original_price * 100
                           ))::int
                       END AS discount_pct,
                       NULLIF(TRIM(p.promo_label), '') AS promo_label,
                       p.promo_expires,
                       p.source,
                       p.price_per_unit
                FROM prices p
                JOIN nearby_stores ns ON p.store_id = ns.id
                WHERE p.is_current = TRUE
                  AND NOT p.quarantined
                  AND p.price >= :min_valid_price
                  AND {fresh}
                  -- Cosa E' un'offerta lo decide price_origin, non questa
                  -- query: sconto_affermabile_sql seleziona le righe su cui
                  -- sconto_affermabile() dice True, e gli elenchi di sorgenti
                  -- li genera dalla mappa del modulo.
                  -- NON seleziona le righe di categoria "volantino": e' la
                  -- correzione di questo giro. Il predicato del giro scorso
                  -- pretendeva la categoria, cioe' fondeva la PROVENIENZA
                  -- (che tipo di prezzo e') con la VALIDITA' DELLO SCONTO
                  -- (se possiamo dire che costa meno di prima), e svuotava la
                  -- pagina: una riga cosicomodo e' un prezzo del catalogo
                  -- online E uno sconto dichiarato dalla catena, due fatti
                  -- veri insieme.
                  --
                  -- MISURATO (TABLESAMPLE SYSTEM (5) delle pagine di
                  -- `prices`: righe is_current, non in quarantena, prezzo >=
                  -- 0,10, scraped_at nelle ultime 240 ore, cioe' la
                  -- tolleranza di freschezza piu' larga del serving). Il
                  -- conteggio esatto sull'intero insieme non e' ottenibile:
                  -- sfora i 280 s di statement_timeout su questa istanza, e
                  -- anche la sola COUNT appoggiata agli indici parziali.
                  -- Sul campione, predicato del giro scorso -> questo:
                  --   cosicomodo     42.963 righe campionate     0 -> 2.090
                  --   esselunga_web      892                      0 ->   110
                  --   carrefour_web      877                      0 ->   108
                  --   iper               108                    108 ->   108
                  --   conad_web           54                      0 ->     0
                  --   aldi                54                      1 ->     1
                  --   pam                 36                      0 ->    17
                  --   lidl                18                      2 ->     2
                  --   coop                17                      0 ->     1
                  --   md                   9                      9 ->     9
                  --   flyer                7                      0 ->     0
                  --   TOTALE          45.035                    120 -> 2.446
                  -- Riportato in scala (x20): da ~2.400 righe candidate a
                  -- ~48.900, con cosicomodo da ZERO a ~41.800. E' l'ordine di
                  -- grandezza atteso, e il "prima" coincide col 2.163
                  -- misurato in modo esatto nel giro scorso.
                  -- Le 7 righe flyer DEL CAMPIONE hanno la scadenza passata
                  -- (4 anche col barrato) e restano fuori da entrambi i
                  -- predicati: e' il confine che va tenuto, ed e' il caso
                  -- IDROPULITRICE. Vale per quelle 7 righe e non per la
                  -- sorgente: contate in modo esatto il 30/10/2026, le righe
                  -- flyer fresche sono 39 e portano TUTTE promo_expires
                  -- 2026-11-04, cioe' una scadenza NEL FUTURO.
                  -- Il primo ramo del predicato non e' quindi codice morto:
                  -- e' l'UNICO che seleziona quelle 39 righe, perche' i rami 2
                  -- e 3 chiedono entrambi promo_expires IS NULL (31 delle 39
                  -- hanno anche il barrato, ma non e' da la' che entrano).
                  -- Sono l'intero contributo volantino della pagina, e in
                  -- tutto il DB sono le SOLE righe fresche con una scadenza
                  -- futura: contate il 30/10/2026 su ogni sorgente.
                  -- Il predicato SQL e sconto_affermabile_di() sono stati
                  -- confrontati su 20.000 righe vere: zero disaccordi.
                  -- La scadenza e' confrontata col calendario CIVILE
                  -- ITALIANO (dentro sconto_affermabile_sql), lo stesso che usa
                  -- price_origin: CURRENT_DATE e' la data del fuso della
                  -- sessione Postgres (UTC su Supabase), quindi fra
                  -- mezzanotte e le 01:00/02:00 italiane il filtro e
                  -- l'etichetta parlavano di due giorni diversi.
                  AND {affermabile}
                ORDER BY p.product_id, ns.chain_id,
                         (CASE WHEN p.original_price > p.price
                               THEN (p.original_price - p.price) / p.original_price
                          END) DESC NULLS LAST,
                         p.price ASC
            ),
            ranked AS (
                -- posizione dell'offerta DENTRO la sua catena (1 = la migliore)
                SELECT b.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY b.chain_slug
                           ORDER BY b.discount_pct DESC NULLS LAST, b.price ASC
                       ) AS chain_rank
                FROM best b
            ),
            top AS (
                -- round-robin: prima la #1 di ogni catena (fra loro ordinate
                -- per sconto), poi la #2 di ogni catena, e cosi' via. Con
                -- l'ordinamento globale per sconto le catene discount non
                -- entravano mai; cosi' ognuna ottiene una quota del limite.
                -- NB: mai nominare un bind param dentro un commento SQL. Il
                -- parser di text() non salta i commenti e pretende un valore
                -- anche per un parametro che compare solo qui.
                SELECT * FROM ranked
                -- nessuna catena puo' occupare piu' slot del limite: taglia
                -- l'input del sort finale senza cambiare il risultato
                WHERE chain_rank <= :row_limit
                ORDER BY chain_rank, discount_pct DESC NULLS LAST, price ASC
                LIMIT :row_limit
            )
            -- il join con products avviene SOLO sulle righe finali (<= row_limit):
            -- evita migliaia di lookup inutili sulla tabella prodotti
            SELECT t.product_id::text AS product_id,
                   pr.name AS product_name,
                   pr.brand,
                   pr.image_url,
                   t.chain_slug,
                   t.chain_name,
                   t.store_name,
                   t.distance_km,
                   t.price,
                   t.original_price,
                   t.discount_pct,
                   t.promo_label,
                   t.promo_expires,
                   t.source,
                   t.price_per_unit,
                   t.chain_rank
            FROM top t
            JOIN products pr ON pr.id = t.product_id
            ORDER BY t.chain_rank, t.discount_pct DESC NULLS LAST, t.price ASC
        """),
        params,
    )
    offerte = [dict(r) for r in result.mappings().all()]

    # La provenienza si decide per riga, e la riga qui c'e' tutta (la CTE
    # `best` seleziona anche p.price): origine_di/descrizione_di la leggono da
    # se', quindi la prova del barrato e' quella STRETTA del resto del
    # progetto (original_price > price). Elencare i campi a mano, come si
    # faceva qui, lasciava fuori `price` e teneva la prova larga
    # (original_price > 0): 9.584 righe correnti hanno un barrato <= prezzo.
    # `sconto_affermabile` e' il permesso di affermare un risparmio: il prezzo
    # barrato, il badge "Risparmi EUR X", il pill del promo_label e la
    # percentuale di sconto si mostrano solo dove vale True.
    for o in offerte:
        o["origine"] = origine_di(o)
        o["origine_label"] = descrizione_di(o)
        o["sconto_affermabile"] = sconto_affermabile_di(o)

    # Questo filtro SCARTA DAVVERO, e da questo giro piu' di prima: il
    # predicato SQL seleziona le righe con uno sconto AFFERMABILE, che non e'
    # la stessa cosa della categoria "volantino". Una riga del catalogo online
    # col barrato entra fra le offerte (origine "online") e "Solo volantino"
    # la toglie, com'e' giusto: l'utente che spunta quel filtro vuole le
    # offerte da fare in negozio. Per questo `row_limit` pesca piu' largo di
    # `limit` quando source=flyer (vedi sopra).
    if source == "flyer":
        offerte = [o for o in offerte if o["origine"] == "volantino"]
    offerte = offerte[:limit]

    # L'avviso e' condizionale e calcolato sulle righe che l'utente vede
    # davvero in questa risposta. Il round-robin fra catene mescola le origini
    # nel caso generale, ma non quando il chiamante passa chain=<slug> o
    # source=flyer: in quei casi le origini sono omogenee e dichiarare un
    # confronto online/negozio che non esiste e' una frase che l'utente
    # impara a saltare, cosi' quando servira' davvero non la leggera'.
    avviso_lista = avviso_origini([o["origine"] for o in offerte])
    for o in offerte:
        o["avviso"] = avviso_lista
    return offerte
