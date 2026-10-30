"""
Provenienza dei prezzi: da dove viene ciascun numero che mostriamo.

L'app confronta grandezze diverse senza dirlo: il prezzo della spesa online di
Esselunga accanto all'offerta da volantino di Eurospin, come se fossero la
stessa cosa. Non lo sono — l'e-commerce ha costi di picking e consegna che
spesso finiscono nel prezzo, e un'offerta da volantino vale solo in negozio e
solo per quella settimana.

Un utente che va nel negozio che gli abbiamo indicato e paga di piu' non torna:
per questo la provenienza va dichiarata accanto a ogni prezzo, non nascosta.

Le etichette sono pensate per essere lette da chi fa la spesa, non da chi
conosce il database.

La provenienza si decide PER RIGA, non per sola sorgente
--------------------------------------------------------
Dire "la sorgente aldi e' un volantino" e' comodo ma falso: sulle righe fresche
misurate in produzione, 840 righe Aldi su 892 non hanno nessun dato
promozionale (niente original_price, niente promo_expires, niente promo_label).
Quelle righe sono il prezzo di scaffale che la catena pubblica, non un'offerta a
tempo: chiamarle "offerta volantino" promette uno sconto che non esiste e, non
avendo una scadenza, non si puo' nemmeno qualificare con una data.
Per questo origine() guarda anche la riga: original_price e promo_expires sono
una prova di promozione, promo_label NO (su MD contiene categorie di reparto e
condizioni di carta fedelta', per esempio "BUONA SPESA CARD", non promozioni).

Una promozione scaduta va detta, non nascosta
---------------------------------------------
Su una sorgente di volantino una promo_expires passata e' la prova che l'offerta
e' finita: la categoria diventa "scaduta" e l'etichetta porta la data. Su una
sorgente di LISTINO (eurospin_web) la stessa promo_expires passata e' invece un
residuo di metadati che lo spider si trascina dietro: la riga resta il listino
del negozio. E' la correzione che evita di etichettare "offerta volantino, fino
al 20/09" oltre centomila prezzi di listino Eurospin.

Il prezzo segnalato da un utente non e' un prezzo pubblicato dalla catena: ha una
categoria sua ("segnalato"), perche' l'unica cosa peggiore di un prezzo senza
provenienza e' un prezzo di cui non si sa nemmeno chi l'ha scritto.

Le condizioni si dicono in qualunque categoria, l'accumulo di punti NO
----------------------------------------------------------------------
Quando promo_label dichiara una condizione di carta fedelta' ("BUONA SPESA
CARD" su MD, "PROMO CARD -27%" e "PROMO BLUCARD -30%" su CosiComodo) la nota
viene aggiunta all'etichetta in qualunque categoria, non solo sulle offerte: la
riga MD senza prezzo barrato e' "prezzo in negozio", ed e' esattamente la riga
su cui l'utente senza carta arriverebbe in cassa e pagherebbe il prezzo pieno.
Ma "+ 3 PUNTI CARD" nomina la stessa carta e NON condiziona il prezzo: con la
carta si prendono punti, il prezzo e' lo stesso per tutti. Dire "con carta
fedelta'" la' fa scartare un prezzo valido, che e' il danno peggiore dei due.
La regola e' scritta sui valori veri di promo_label, misurati per sorgente:
vedi la tabella accanto a _PAROLE_PUNTI.

Provenienza e sconto sono DUE DOMANDE, e non vanno fuse
-------------------------------------------------------
  - la PROVENIENZA risponde a "che tipo di prezzo e' questo" (online,
    negozio, volantino, scaduta, segnalato): e' origine(), ed e' decisa dalla
    mappa delle sorgenti piu' i dati promozionali della riga;
  - lo SCONTO AFFERMABILE risponde a "possiamo dire che costa meno di prima,
    e fino a quando": e' stato_sconto() / sconto_affermabile().
Una riga del catalogo online di una catena puo' portare uno sconto dichiarato
dalla catena: sono due fatti veri insieme. Il giro precedente li aveva fusi in
una regola sola (`promozione_viva` valeva `origine(...) == "volantino"`) e,
applicata al predicato SQL, quella fusione ha svuotato la pagina Offerte fuori
dal Nord: misurato sulle righe servite, 52.163 righe fresche candidate scese a
2.163, cosicomodo da 45.653 a ZERO, e zero card a Roma, Napoli, Palermo, Bari
e Firenze. Il difetto vero era un'affermazione FALSA ("Risparmi EUR 10,00" su
una promozione finita il 23 agosto), e quella si chiude escludendo le
promozioni SCADUTE, non tutti gli sconti che non vengono da un volantino.

Rimisurato con la regola separata di questo giro, sulle stesse clausole di
serving (correnti, non in quarantena, prezzo >= 0,10, negozi attivi, fresche
per la soglia della catena): il predicato fuso seleziona 2.163 righe, quello
separato 47.881, con cosicomodo da 0 a 41.245. I totali assoluti NON sono
confrontabili fra un giro e l'altro — "fresche" e' una finestra mobile e
dipende da quali sorgenti sono state riscrappate di recente, per cui le 52.163
di allora sono 47.881 oggi — mentre il rapporto fra le due regole e il fatto
che cosicomodo passi da zero a decine di migliaia di righe sono stabili.

Tre stati, tre frasi (vedi stato_sconto)
----------------------------------------
  1. scadenza NEL FUTURO    -> sconto affermabile, e si dice fino a quando:
                               "offerta volantino, fino al GG/MM";
  2. scadenza NEL PASSATO    -> NON affermabile: niente barrato, niente
                               percentuale, niente "Risparmi", e la riga non
                               entra fra le offerte. Dove la provenienza lo
                               dimostra l'etichetta e' "offerta scaduta il
                               GG/MM" (vedi sotto perche' non su ogni
                               sorgente);
  3. NESSUNA scadenza, con una prova di sconto -> affermabile ma senza termine
                               noto: si mostrano barrato e percentuale, la riga
                               entra fra le offerte, e l'etichetta aggiunge
                               _NOTA_SENZA_TERMINE, che NON nomina nessuna
                               data. Non si inventa un "fino a esaurimento"
                               che la catena non dichiara.
Le superfici che mostrano il prezzo barrato, il badge "Risparmi", il pill del
promo_label o la percentuale di sconto leggono `sconto_affermabile`, e il
predicato SQL che decide cosa ENTRA fra le offerte nasce dallo stesso punto
(vedi sconto_affermabile_sql): una regola, due linguaggi, un solo posto dove
cambiarla.

L'avviso descrive il confronto che l'utente ha davanti
-------------------------------------------------------
Il confronto e' misto quando fra le origini mostrate c'e' piu' di una categoria,
non solo quando c'e' "online": un prezzo di scaffale accanto a un'offerta da
volantino e' il confronto fra le due grandezze piu' diverse che l'app produce.
Siccome le frasi non sono interscambiabili, il testo lo sceglie avviso() e i
chiamanti non scelgono nulla (vedi la guida accanto alle costanti).

Questo modulo e' l'unica fonte di verita' della categoria: il frontend rende
`origine` e `origine_label` che arrivano da qui e non li ricalcola, perche' una
logica duplicata in due linguaggi prima o poi divergera' — ed era gia'
divergente.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional

# La data da confrontare col calendario e' quella CIVILE ITALIANA: il backend
# gira su Render in UTC, l'Italia e' UTC+1/+2, quindi per una o due ore a notte
# date.today() sarebbe ancora il giorno prima e una promo scaduta ieri
# risulterebbe valida (o viceversa). prices.promo_expires e' un `date` scritto
# dagli spider sul calendario italiano: va confrontato con lo stesso calendario.
try:  # su un'immagine senza tzdata ZoneInfo non trova il fuso: sotto il rimedio
    from zoneinfo import ZoneInfo

    _ROMA: Optional[Any] = ZoneInfo("Europe/Rome")
except Exception:  # pragma: no cover - dipende dall'immagine di deploy
    _ROMA = None

# ── Come nasce ciascuna sorgente ────────────────────────────────────────────
# "online"    → listino dell'e-commerce della catena
# "negozio"   → prezzo di scaffale pubblicato dalla catena per punto vendita
# "volantino" → promozione a tempo, valida nei negozi
# "segnalato" → prezzo visto in corsia e segnalato da un utente dallo scanner
_ORIGINE_PER_SORGENTE: dict[str, str] = {
    # Spesa online (il prezzo che paghi ordinando sul sito)
    "esselunga_web": "online",
    "carrefour_web": "online",
    "conad_web": "online",
    "cosicomodo": "online",   # Famila, Italmark, Il Gigante
    "coop": "online",
    "pam": "online",
    # API Pepesto: legge spesaonline.esselunga.it e spesaonline.conad.it
    # (scraping/spiders/pepesto_spider.py), quindi e' spesa online come le
    # altre. Misurato oggi: ZERO righe in prices, ma lo spider e' lanciabile
    # (AVVIO.md) e scrive source='pepesto' alla riga 118: senza questa voce
    # origine() direbbe "sconosciuta", che in tutta l'app e' SILENZIOSA — il
    # prezzo si vedrebbe e la provenienza no.
    "pepesto": "online",
    # Listino di negozio pubblicato dalla catena
    "eurospin_web": "negozio",
    # Volantini
    "flyer": "volantino",     # lo scrape E' un volantino: sempre volantino
    "iper": "volantino",      # Digital Flyer API di Iper (cataloghi.iper.it)
    # Pagine offerte dei discount: volantino solo se la riga lo prova
    "md": "volantino",
    "lidl": "volantino",
    "aldi": "volantino",
    "penny": "volantino",
    # Segnalazione di un utente dallo scanner
    "user_scan": "segnalato",
}

# Sorgenti che per loro natura SONO un volantino, anche senza dati promozionali
# sulla riga: lo scrape stesso legge un volantino (flyer) o un catalogo
# promozionale a tempo (iper, Digital Flyer: 21.252 righe correnti su 21.252 con
# promo_label e ZERO con promo_expires, quindi la natura e' l'unica cosa su cui
# si possa decidere).
#
# Questo e' l'UNICO insieme che decide, e la regola e' scritta in modo che la
# dimenticanza costi il meno possibile: ogni altra sorgente mappata a
# "volantino" qui sopra (i discount che pubblicano in un'unica pagina sia
# offerte sia prezzi di scaffale) deve MERITARE la categoria con una prova sulla
# riga, altrimenti resta il prezzo di scaffale della catena. Nella forma
# precedente c'era un secondo insieme che elencava i discount e, sotto, un
# `return "volantino"` finale che lo scavalcava: una catena aggiunta domani alla
# mappa e dimenticata negli insiemi avrebbe detto "offerta volantino" su tutto
# il suo catalogo senza nessuno sconto — esattamente il difetto che questo
# modulo esiste per impedire. Adesso la dimenticanza produce "negozio", che e'
# prudente: afferma un prezzo, non uno sconto.
_VOLANTINO_PER_NATURA: frozenset[str] = frozenset({"flyer", "iper"})

_ETICHETTA = {
    "online": "prezzo spesa online",
    "negozio": "prezzo in negozio",
    "volantino": "offerta volantino",
    "scaduta": "offerta scaduta",
    "segnalato": "prezzo segnalato da un utente",
    "sconosciuta": "provenienza non dichiarata",
}

# ── L'avviso di confronto misto ─────────────────────────────────────────────
# Le categorie che l'utente paga in modo DIVERSO: metterne due accanto nello
# stesso confronto significa confrontare grandezze diverse, e l'avviso serve.
# Legare l'avviso alla sola presenza di "online" era troppo stretto: un prezzo
# di scaffale confrontato con un'offerta da volantino e' il confronto fra le due
# grandezze piu' diverse che l'app produce (uno vale sempre, l'altro solo in
# negozio e solo per quella settimana) e non faceva scattare nulla — ed e' il
# caso PIU' frequente sui discount, dove lo stesso scaffale produce ora
# "negozio" per le righe senza prova e "volantino" per quelle con prova.
#
# "sconosciuta" NON e' qui: e' assenza di informazione, non una natura, e
# contarla come categoria distinta farebbe comparire l'avviso su un confronto
# per il resto omogeneo solo perche' una riga non si e' potuta classificare.
_CATEGORIE_CONFRONTO: frozenset[str] = frozenset(
    {"online", "negozio", "volantino", "scaduta", "segnalato"}
)

# Categorie di prezzo che valgono solo finche' l'offerta e' in corso — o che non
# valgono piu'. Sono quelle che reggono il testo AVVISO_OFFERTA_A_TEMPO.
_A_TEMPO: frozenset[str] = frozenset({"volantino", "scaduta"})

# I tre testi dell'avviso. Un avviso che non descrive il confronto che l'utente
# ha davanti e' peggio di nessun avviso, quindi la frase cambia col caso.
#
# QUALE SCEGLIERE (i chiamanti stanno in altri file: questa e' la guida)
#   - meglio di tutto: NON scegliere, chiamare `avviso(origini)`, che sceglie
#     da solo sulle origini davvero mostrate e restituisce None se sono
#     omogenee;
#   - AVVISO_ONLINE_VS_NEGOZIO: quando fra le origini mostrate c'e' "online"
#     accanto a una categoria di negozio e NESSUNA offerta a tempo;
#   - AVVISO_OFFERTA_A_TEMPO: quando NON c'e' "online" e fra le origini c'e'
#     "volantino" o "scaduta" accanto a un prezzo che vale sempre;
#   - AVVISO_CONFRONTO_MISTO: tutti gli altri confronti misti (per esempio
#     online + volantino, dove valgono entrambe le avvertenze) ed e' il
#     fallback giusto per chi non sa quali categorie ha in mano: e' l'unica
#     frase vera in OGNI combinazione, perche' non nomina nessuna categoria.
# ATTENZIONE: prima di questo giro AVVISO_CONFRONTO_MISTO conteneva il testo
# che ora sta in AVVISO_ONLINE_VS_NEGOZIO. Chi importa il nome e lo stampa
# senza passare da avviso() sta stampando una frase piu' generica di prima, non
# una frase sbagliata.
AVVISO_ONLINE_VS_NEGOZIO = (
    "I prezzi della spesa online possono differire da quelli in negozio."
)
AVVISO_OFFERTA_A_TEMPO = (
    "Alcuni di questi prezzi sono offerte a tempo, valide solo in negozio e "
    "solo fino alla data sull'etichetta."
)
AVVISO_CONFRONTO_MISTO = (
    "I prezzi messi a confronto non sono tutti della stessa natura: controlla "
    "l'etichetta accanto a ciascuno."
)

# Condizioni nascoste dentro promo_label: non sono una prova di promozione, ma
# dicono a che condizione il prezzo vale in cassa — qualunque sia la categoria
# della riga, offerta o prezzo di scaffale. Senza questa nota l'utente senza
# carta fedelta' arriva in cassa e paga il prezzo pieno.
_NOTA_CARTA = "con carta fedelta'"

# ── La carta fedelta' dentro promo_label: due casi OPPOSTI ──────────────────
# Misurato oggi in produzione (righe correnti, non in quarantena, prezzo >=
# 0,10, negozi attivi, fresche per la soglia della catena), tutti i valori di
# promo_label che nominano una carta, per sorgente:
#
#   PREZZO CONDIZIONATO alla carta — la nota serve
#     cosicomodo  "PROMO CARD -4%" … "-68%"          10.048 righe
#     cosicomodo  "PROMO BLUCARD -3%" … "-55%"       12.819 righe
#     md          "BUONA SPESA CARD" (+ FRESCO,
#                 FREDDO, DROGHERIA ALIMENTARE, …)       16 righe
#     penny       "PENNYCard"                 (nessuna riga fresca; 16 correnti)
#
#   ACCUMULO DI PUNTI — la nota e' FALSA
#     cosicomodo  "+ 1 / 2 / 3 / 4 / 5 / 8 / 10 / 15 / 20 / 30 / 50 PUNTI
#                 CARD" e le stesse quantita' con "PUNTI BLUCARD"
#                                                   13.734 righe
#
#   PUNTI SENZA NESSUNA CARTA — fuori da entrambi i casi
#     esselunga_web "N - PUNTI FRAGOLA" / "N - PUNTI FRAGOLA SET"
#                                                    1.093 righe
#     Non nominano una carta, quindi non avrebbero mai preso la nota; stanno
#     qui perche' la regola dei punti le intercetta per prima e il conto delle
#     etichette che parlano di punti sia completo.
#
# Le etichette di punti sono TUTTE della forma "+ N PUNTI (BLU)CARD": zero
# hanno un prezzo barrato, zero hanno una scadenza, nessuna porta una
# percentuale. Con la carta l'utente prende punti in piu', il prezzo in cassa
# e' lo stesso per tutti — e la nota lo spingeva a SCARTARE un prezzo valido
# credendo di non averne diritto, che e' il verso di danno peggiore.
# Per questo l'accumulo di punti vince sul nome della carta: se l'etichetta
# parla di punti, non sta dichiarando una condizione di prezzo.
_PAROLE_PUNTI: frozenset[str] = frozenset({"punti", "punto"})


def _oggi_italia() -> date:
    """La data civile italiana, non quella del processo (vedi sopra)."""
    if _ROMA is not None:
        return datetime.now(_ROMA).date()
    # Rimedio senza tzdata: UTC+1 e' sempre piu' vicino all'Italia di UTC.
    return (datetime.now(timezone.utc) + timedelta(hours=1)).date()


def _come_data(valore: Any) -> Optional[date]:
    """Normalizza a `date` quello che arriva dal DB (date, datetime o niente)."""
    if valore is None:
        return None
    if isinstance(valore, datetime):
        return valore.date()
    if isinstance(valore, date):
        return valore
    return None


def promo_in_corso(promo_expires: Any = None, oggi: Optional[date] = None) -> bool:
    """
    True solo se la promozione e' ancora valida alla data civile italiana.

    Il confronto col calendario non e' un dettaglio: in produzione quasi tutte
    le righe con promo_expires hanno una data GIA' passata, perche' il campo
    resta attaccato al prezzo anche quando lo spider ricarica il listino. Far
    vincere la scadenza senza guardarla etichettava come "offerta volantino,
    fino al 20/09" oltre centomila prezzi di listino Eurospin: categoria
    sbagliata e data vecchia di settimane stampata accanto al prezzo, cioe'
    proprio il danno di fiducia che questo modulo esiste per evitare.

    `oggi` serve ai test per fissare il calendario: in esercizio resta None.
    """
    scadenza = _come_data(promo_expires)
    if scadenza is None:
        return False
    return scadenza >= (_come_data(oggi) or _oggi_italia())


def promo_scaduta(promo_expires: Any = None, oggi: Optional[date] = None) -> bool:
    """True se c'e' una scadenza ed e' passata (il contrario di in corso)."""
    return _come_data(promo_expires) is not None and not promo_in_corso(promo_expires, oggi)


def _numero(valore: Any) -> Optional[float]:
    """Il valore come float quando e' un numero, altrimenti None."""
    if valore is None or isinstance(valore, bool):
        return None
    try:
        return float(valore)
    except (TypeError, ValueError):
        return None


def _prova_promozionale(original_price: Any, price: Any = None) -> bool:
    """
    Un prezzo barrato PIU' BASSO del prezzo non prova nulla; promo_label NO.

    original_price dice "prima costava X": e' la sola cosa, oltre a una scadenza,
    che distingue un'offerta da un prezzo di scaffale. Uno zero o un valore non
    numerico e' un residuo dello spider, non un prezzo precedente.

    Quando il chiamante passa anche `price` la prova e' quella STRETTA del resto
    del progetto, `original_price > price` (offers.py, offers_rebuild.py,
    _discount_pct): un original_price uguale al prezzo — capita quando lo spider
    legge lo stesso numero da due campi della pagina — non e' uno sconto, e
    promuoverlo a "offerta volantino" fa affermare un'offerta che in cassa non
    c'e'. Senza `price` resta la prova larga `> 0`, perche' non si puo'
    confrontare con un numero che non si ha.

    Quanto pesa la differenza fra le due prove, MISURATO su produzione (righe
    correnti, non in quarantena): le righe con original_price > 0 e <= price
    sono 9.584, TUTTE di sorgente eurospin_web e ZERO fresche. La differenza
    fra le due prove e' quindi inerte sulle righe servite di oggi: `price`
    serve a non far dipendere la risposta da quale chiamante la chiede, non a
    correggere righe esistenti.

    ATTENZIONE: da questo giro la prova viene interrogata su DUE assi diversi.
    Per origine() continua a valere solo sulle sorgenti mappate "volantino"
    (su eurospin_web, mappata "negozio", la categoria non cambia mai per un
    barrato). Per stato_sconto() vale su TUTTE le sorgenti di catena, perche'
    un barrato su un listino e' uno sconto dichiarato dalla catena anche se il
    prezzo resta un prezzo di listino: e' la separazione dei due assi. Quelle
    9.584 righe sono esattamente le righe che la prova larga promuoverebbe a
    sconto senza che ci sia uno sconto, ed e' per loro che `price` va passato.
    """
    prima = _numero(original_price)
    if prima is None or prima <= 0:
        return False
    adesso = _numero(price)
    if adesso is None:
        return True
    return prima > adesso


def origine(
    source: Optional[str],
    promo_expires: Any = None,
    original_price: Any = None,
    promo_label: Optional[str] = None,
    oggi: Optional[date] = None,
    price: Any = None,
) -> str:
    """
    Categoria di provenienza della RIGA: "online", "negozio", "volantino",
    "scaduta", "segnalato" o "sconosciuta".

    I parametri oltre a `source` sono opzionali perche' i chiamanti esistenti
    passano ancora solo sorgente e scadenza; piu' dati si passano, piu' la
    categoria e' precisa. In particolare, senza `original_price` una riga
    discount senza scadenza viene classificata "negozio", che e' la lettura
    prudente: afferma un prezzo, non uno sconto.

    `price` e' l'ultimo parametro (e va passato per nome) per non spostare
    quelli che i chiamanti passano posizionalmente. Serve solo come termine di
    confronto del prezzo barrato: passandolo, la prova di promozione diventa
    quella stretta `original_price > price` usata nel resto del progetto. Dove
    la riga c'e' tutta conviene usare origine_di(riga), che lo passa da se'.

    `promo_label` e' accettato ma NON decide: su MD contiene reparti e
    condizioni di carta fedelta', su Conad il nome del suo programma di prezzi
    PERMANENTI, su Carrefour la stringa vuota. Serve solo a qualificare la riga
    in descrizione().
    """
    if not source:
        return "sconosciuta"

    base = _ORIGINE_PER_SORGENTE.get(source)
    if base is None:
        return "sconosciuta"

    # Una segnalazione resta una segnalazione: chi l'ha scritta e' l'unica cosa
    # che l'utente deve sapere di quel numero, e nessun metadato la cambia.
    if base == "segnalato":
        return "segnalato"

    if base == "volantino":
        if promo_in_corso(promo_expires, oggi):
            return "volantino"
        if promo_scaduta(promo_expires, oggi):
            # L'offerta e' finita e la data lo dimostra: dirlo e' l'unico modo
            # di non affermare uno sconto che in cassa non c'e' piu'.
            return "scaduta"
        if source in _VOLANTINO_PER_NATURA:
            # Lo scrape stesso E' un volantino: qui la natura non ha bisogno di
            # prove sulla riga (e su iper non ce ne sarebbero, vedi sopra).
            return "volantino"
        if _prova_promozionale(original_price, price):
            return "volantino"
        # Nessuna prova: la riga afferma un prezzo, non uno sconto.
        return "negozio"

    # Sorgenti di listino (online / negozio). Una promo ANCORA IN CORSO vince
    # sulla sorgente: una riga nata dallo scrape del sito ma ereditata da un
    # volantino valido E' un'offerta a tempo (vedi preserve_flyer_promos negli
    # spider). Una promo scaduta, invece, qui non dice piu' nulla sulla natura
    # del prezzo: e' un residuo di metadati e la riga resta il suo listino.
    if promo_in_corso(promo_expires, oggi):
        return "volantino"
    return base


# `etichetta()` e' stata RIMOSSA in questo giro: era pubblica, non aveva
# nessun chiamante in tutto il repo (backend/, scripts/, scraping/) e
# restituiva _ETICHETTA[cat] nudo, cioe' senza la scadenza ("fino al 08/11")
# e senza la nota della carta fedelta'. Chi domani l'avesse usata al posto di
# descrizione() — il nome e' piu' ovvio — avrebbe perso in silenzio proprio le
# due informazioni per cui questo modulo esiste, e pyflakes non segnala una
# funzione pubblica. Il testo da mostrare accanto al prezzo e' descrizione()
# (o descrizione_di() dove la riga c'e' tutta): una sola via di rendering.


def _giorno_mese(giorno: date, oggi: date) -> str:
    """
    "18/10", e con l'anno quando non e' quello corrente: "offerta scaduta il
    18/10" su una promo dell'anno scorso sembrerebbe finita la settimana scorsa.
    """
    if giorno.year != oggi.year:
        return giorno.strftime("%d/%m/%Y")
    return giorno.strftime("%d/%m")


def _nomina_una_carta(parole: set[str]) -> bool:
    """
    True se fra le parole c'e' il nome di una carta fedelta'.

    Non basta la parola "card": misurati in produzione, i token che nominano
    una carta sono "card" (cosicomodo 38.186 righe, md 133), "blucard"
    (cosicomodo 15.951, la carta di Famila) e "pennycard" (penny 16). Con la
    sola parola intera "card" le 12.819 righe fresche "PROMO BLUCARD -x%"
    restavano senza nota, cioe' un prezzo condizionato alla carta affermato
    senza dire la condizione.
    L'unico altro token con "cart" dentro e' "cartoleria-libri-giornali" (un
    reparto MD, 9 righe): non finisce per "card" e non entra.
    """
    return any(p == "card" or (len(p) > 4 and p.endswith("card")) for p in parole)


def _condizione(promo_label: Optional[str]) -> Optional[str]:
    """
    La condizione dichiarata dentro promo_label, quando c'e'.

    Solo la carta fedelta', perche' e' l'unica che i dati di produzione
    mostrano e l'unica che cambia il prezzo che l'utente paga davvero in cassa.

    Un'etichetta di PUNTI non e' una condizione di prezzo: vedi la misura
    accanto a _PAROLE_PUNTI. Si guarda prima quella, perche' il nome della
    carta compare in entrambi i casi ("+ 3 PUNTI CARD" e "PROMO CARD -27%")
    e solo i punti distinguono l'accumulo dal prezzo condizionato.
    """
    if not promo_label:
        return None
    testo = str(promo_label).lower().replace("'", "")
    # Parole intere: la punteggiatura ai bordi si butta, quella interna no
    # ("cartoleria-libri-giornali" resta un token solo).
    parole = {p.strip(" -_.,;:()") for p in testo.split()}
    if parole & _PAROLE_PUNTI:
        # Accumulo di punti: il prezzo e' lo stesso per tutti.
        return None
    if _nomina_una_carta(parole):
        return _NOTA_CARTA
    if "carta" in parole and "fedelta" in testo:
        # "carta" da sola NON basta: in produzione e' carta igienica/riciclata
        # ("LINEA CARTA RICICLATA BUONA SPESA CARD", dove la condizione la
        # dichiara "CARD").
        return _NOTA_CARTA
    return None


def descrizione(
    source: Optional[str],
    promo_expires: Any = None,
    original_price: Any = None,
    promo_label: Optional[str] = None,
    oggi: Optional[date] = None,
    price: Any = None,
) -> str:
    """
    Etichetta completa, con la scadenza quando c'e': l'utente deve sapere entro
    quando l'offerta e' valida — o da quando non lo e' piu' — altrimenti
    l'informazione non gli serve.

    La condizione dichiarata dentro promo_label (oggi solo la carta fedelta')
    viene aggiunta in QUALUNQUE categoria, non solo sulle offerte: su MD la
    condizione sta nel label e la riga senza prezzo barrato e' "negozio", cioe'
    proprio il caso in cui prima la nota veniva scartata. Un "prezzo in negozio"
    che in cassa vale solo con la carta e' un prezzo che l'utente senza carta
    non paghera': affermarlo e non nominare la carta e' peggio che tacere.
    L'unica categoria esclusa e' "sconosciuta": di quella riga non sappiamo
    nemmeno la sorgente, e non ci aggiungiamo affermazioni.

    La coda dell'etichetta e' la frase dello STATO DELLO SCONTO, che e' l'altro
    asse (vedi stato_sconto):
      - in corso      -> ", fino al GG/MM";
      - senza termine -> ", sconto dichiarato dalla catena, termine non
                         indicato". Serve proprio dove la categoria non parla
                         di sconti ("prezzo spesa online" con un barrato
                         accanto): senza questa coda la card mostra una
                         percentuale e l'etichetta non la spiega. Non nomina
                         nessuna data, perche' non c'e';
      - finito        -> ", offerta scaduta il GG/MM", ma SOLO dove la
                         provenienza lo dimostra, cioe' dove origine() dice
                         "scaduta". Su una sorgente di LISTINO una
                         promo_expires passata e' un residuo di metadati (vedi
                         la nota in testa al modulo): scrivere "offerta scaduta
                         il 20/09" accanto a oltre centomila prezzi di listino
                         Eurospin sarebbe la stessa etichetta falsa al
                         contrario. Su quelle righe lo sconto non si afferma
                         comunque — ci pensa stato_sconto, che restituisce
                         "finito" — e l'etichetta resta il listino che e'.
    """
    cat = origine(source, promo_expires, original_price, promo_label, oggi, price)
    stato = stato_sconto(source, promo_expires, original_price, promo_label, oggi, price)
    base = _ETICHETTA[cat]
    giorno = _come_data(promo_expires)
    riferimento = _come_data(oggi) or _oggi_italia()

    if stato == SCONTO_IN_CORSO and giorno is not None:
        base = f"{base}, fino al {_giorno_mese(giorno, riferimento)}"
    elif cat == "scaduta" and giorno is not None:
        base = f"{base} il {_giorno_mese(giorno, riferimento)}"
    elif stato == SCONTO_SENZA_TERMINE:
        base = f"{base}, {_NOTA_SENZA_TERMINE}"

    if cat != "sconosciuta":
        condizione = _condizione(promo_label)
        if condizione:
            base = f"{base}, {condizione}"
    return base


def origine_di(riga: Optional[Mapping[str, Any]], oggi: Optional[date] = None) -> str:
    """
    origine() leggendo direttamente una riga del DB.

    Esiste perche' i chiamanti non debbano ricordarsi quali campi servono: dove
    i campi vengono elencati a mano, prima o poi un endpoint ne dimentica uno e
    la categoria peggiora in silenzio.
    """
    r = riga or {}
    return origine(
        r.get("source"),
        r.get("promo_expires"),
        r.get("original_price"),
        r.get("promo_label"),
        oggi,
        r.get("price"),
    )


def descrizione_di(riga: Optional[Mapping[str, Any]], oggi: Optional[date] = None) -> str:
    """descrizione() leggendo direttamente una riga del DB."""
    r = riga or {}
    return descrizione(
        r.get("source"),
        r.get("promo_expires"),
        r.get("original_price"),
        r.get("promo_label"),
        oggi,
        r.get("price"),
    )


# ── "Su questa riga possiamo affermare uno sconto?" ────────────────────────
# La seconda domanda, indipendente dalla provenienza. Risponde per tutte le
# superfici che affermano un risparmio (prezzo barrato, badge "Risparmi
# EUR X", pill del promo_label, percentuale di sconto) e per il predicato SQL
# che decide cosa entra fra le offerte.
#
# IL NOME PRECEDENTE ERA SBAGLIATO. Si chiamava promozione_viva() e valeva
# `origine(...) == "volantino"`, cioe' fondeva le due domande in una: una riga
# del catalogo online con uno sconto dichiarato dalla catena non e' una
# "promozione viva" — e infatti quella regola, portata nel predicato SQL, ha
# svuotato la pagina Offerte (allora 52.163 righe fresche candidate -> 2.163,
# cosicomodo 45.653 -> 0; rimisurato oggi 47.881 -> 2.163, cosicomodo
# 41.245 -> 0: vedi la nota in testa al modulo sulla finestra mobile delle
# righe fresche). Il nome di oggi dice cosa decide: se lo SCONTO si
# puo' AFFERMARE. "Senza scadenza" non vuol dire "non in corso": vuol dire che
# il termine non lo sappiamo, e lo si dice invece di buttare via lo sconto.
#
# Cosa resta fuori, ed e' il difetto che questo giro chiude davvero: le righe
# con una scadenza GIA' PASSATA. Il caso vero e' "IDROPULITRICE AD ALTA
# PRESSIONE", Eurospin Trento, 59,99 con 69,99 barrato e promo_expires
# 2026-08-23: la card diceva "Risparmi EUR 10,00" in arancione e l'utente
# andava a pagare 69,99.
SCONTO_IN_CORSO = "in_corso"            # scadenza nel futuro: si dice fino a quando
SCONTO_FINITO = "finito"                # scadenza passata: niente da affermare
SCONTO_SENZA_TERMINE = "senza_termine"  # prova di sconto, nessun termine noto
SCONTO_ASSENTE = "nessuno"              # nessuna prova: e' solo un prezzo

# I due stati su cui si puo' affermare un risparmio. "finito" e "nessuno" no:
# il primo perche' l'offerta e' dimostrabilmente chiusa, il secondo perche' non
# c'e' niente da confrontare.
_STATI_AFFERMABILI: frozenset[str] = frozenset(
    {SCONTO_IN_CORSO, SCONTO_SENZA_TERMINE}
)

# La frase dello stato 3, aggiunta da descrizione(). Due cose insieme: che lo
# sconto lo dichiara la CATENA (non l'app, non un utente) e che il termine non
# e' indicato. Niente date inventate, nessun "fino a esaurimento" che la catena
# non dice. Lunghezza verificata insieme all'etichetta piu' lunga e alla nota
# della carta: vedi il commento su MAX_TEXT_LEN in services/assistant_tools.
_NOTA_SENZA_TERMINE = "sconto dichiarato dalla catena, termine non indicato"


def stato_sconto(
    source: Optional[str],
    promo_expires: Any = None,
    original_price: Any = None,
    promo_label: Optional[str] = None,
    oggi: Optional[date] = None,
    price: Any = None,
) -> str:
    """
    Quale dei quattro stati di sconto porta la riga (vedi le costanti).

    L'ordine delle decisioni e' quello che conta:
      1. una sorgente non mappata, o una segnalazione di un utente, non
         dichiara nessuno sconto: un prezzo riferito da un altro utente non e'
         un'offerta della catena, e di una sorgente sconosciuta non sappiamo
         nemmeno cosa sia il numero;
      2. una scadenza ANCORA VALIDA e' lo sconto nella sua forma migliore: la
         catena dichiara un termine ed e' nel futuro -> si afferma, e si dice
         fino a quando. Basta DA SOLA, senza prezzo barrato, ed e' una scelta
         fra due letture possibili: una data di fine e' una dichiarazione
         della catena, non un indizio. Chiedere ANCHE il barrato butterebbe
         via offerte con un termine vero: contate il 30/10/2026, le uniche
         righe fresche con una scadenza futura in tutto il DB sono 39, tutte
         flyer e tutte al 04/11, e 8 di quelle 39 non hanno il barrato —
         sparirebbero da /offerte pur avendo un termine dichiarato dalla
         catena e ancora valido. Cosa costa: una riga di LISTINO che ha ereditato una
         scadenza futura da un volantino (preserve_flyer_promos negli spider)
         si afferma senza nessuna prova di sconto; sulle righe fresche di
         listino quelle scadenze future sono ZERO (cosicomodo, esselunga_web,
         carrefour_web, coop, pam, misurate il 30/10/2026);
      3. una scadenza PASSATA chiude il discorso: qualunque barrato ci sia
         accanto, quell'offerta e' finita;
      4. senza scadenza serve una PROVA: la sorgente che per natura E' un
         volantino (lo scrape stesso legge un volantino), oppure il prezzo
         barrato piu' alto del prezzo. Allora lo sconto si afferma, ma il
         termine non si conosce.

    `promo_label` e' accettato per avere la stessa firma di origine() ma NON
    decide: e' testo scrapato (reparti MD, condizioni di carta fedelta', il
    nome del programma di prezzi PERMANENTI di Conad) e non e' mai stato una
    prova di sconto.
    """
    if not source:
        return SCONTO_ASSENTE
    base = _ORIGINE_PER_SORGENTE.get(source)
    if base is None or base == "segnalato":
        return SCONTO_ASSENTE
    if promo_in_corso(promo_expires, oggi):
        return SCONTO_IN_CORSO
    if promo_scaduta(promo_expires, oggi):
        return SCONTO_FINITO
    if source in _VOLANTINO_PER_NATURA:
        return SCONTO_SENZA_TERMINE
    if _prova_promozionale(original_price, price):
        return SCONTO_SENZA_TERMINE
    return SCONTO_ASSENTE


def sconto_affermabile(
    source: Optional[str],
    promo_expires: Any = None,
    original_price: Any = None,
    promo_label: Optional[str] = None,
    oggi: Optional[date] = None,
    price: Any = None,
) -> bool:
    """
    True quando su questa riga si puo' affermare un risparmio.

    E' il permesso che leggono il prezzo barrato, il badge "Risparmi EUR X", il
    pill del promo_label e la percentuale di sconto. Non dice QUANDO scade:
    quello sta in stato_sconto() e, in forma di testo, in descrizione().
    """
    return stato_sconto(
        source, promo_expires, original_price, promo_label, oggi, price
    ) in _STATI_AFFERMABILI


def stato_sconto_di(
    riga: Optional[Mapping[str, Any]], oggi: Optional[date] = None
) -> str:
    """stato_sconto() leggendo direttamente una riga del DB."""
    r = riga or {}
    return stato_sconto(
        r.get("source"),
        r.get("promo_expires"),
        r.get("original_price"),
        r.get("promo_label"),
        oggi,
        r.get("price"),
    )


def sconto_affermabile_di(
    riga: Optional[Mapping[str, Any]], oggi: Optional[date] = None
) -> bool:
    """sconto_affermabile() leggendo direttamente una riga del DB."""
    return stato_sconto_di(riga, oggi) in _STATI_AFFERMABILI


# ── La stessa regola, sul lato SQL ──────────────────────────────────────────
# Il predicato che decide cosa ENTRA fra le offerte deve selezionare le stesse
# righe su cui sconto_affermabile() dice True, altrimenti l'app chiama offerta
# in un punto cio' che in un altro mostra senza barrato.
#
# Gli elenchi di sorgenti NON sono riscritti qui: nascono dalle due strutture
# sopra (_ORIGINE_PER_SORGENTE e _VOLANTINO_PER_NATURA), cosi' una catena
# aggiunta alla mappa entra nel predicato da sola. Resta duplicata la FORMA
# della decisione, non i suoi dati.
_SORGENTI_DI_CATENA: tuple[str, ...] = tuple(sorted(
    s for s, o in _ORIGINE_PER_SORGENTE.items() if o != "segnalato"
))
_SORGENTI_VOLANTINO_PER_NATURA: tuple[str, ...] = tuple(sorted(_VOLANTINO_PER_NATURA))


def sconto_affermabile_sql(params: dict, price_alias: str = "p") -> str:
    """
    Espressione SQL booleana: TRUE sulle righe su cui sconto_affermabile()
    dice True, cioe' le sole su cui si puo' affermare un risparmio.

    I tre rami ricalcano stato_sconto() nell'ordine in cui decide:
      1. una scadenza ANCORA VALIDA (stato "in corso") vale su qualunque
         sorgente di catena, listini compresi: e' il caso di
         preserve_flyer_promos negli spider;
      2. flyer e iper SONO un volantino per natura, senza bisogno di prove
         sulla riga (su iper non ce ne sarebbero: zero righe con scadenza);
      3. senza scadenza, il barrato piu' alto del prezzo e' la prova (stato
         "senza termine"). Questo ramo vale per TUTTE le sorgenti di catena, ed
         e' l'unica differenza rispetto al giro precedente, dove era limitato
         ai discount md/lidl/aldi/penny. Quella restrizione nasceva dal fondere
         provenienza e sconto, e il suo effetto misurato e' nel report del
         giro: la pagina Offerte restava vuota fuori dal Nord perche'
         cosicomodo — 45.653 righe fresche col barrato e nessuna scadenza —
         non poteva entrare in nessun ramo.
    Una scadenza PASSATA non entra in nessun ramo: il ramo 1 la esclude e i
    rami 2 e 3 chiedono promo_expires IS NULL. E' il filtro che mancava a
    promo.py ed e' il difetto vero del caso IDROPULITRICE.
    Le segnalazioni degli utenti e le sorgenti non mappate restano fuori da
    tutti e tre: non sono sconti dichiarati da una catena.

    Il ramo 3 non ripete `original_price > 0` come fa _prova_promozionale:
    tutti e tre i chiamanti (endpoints/offers.py, services/offers_rebuild.py,
    services/promo.py) filtrano `price >= :min_valid_price` con
    MIN_VALID_PRICE = 0.10, quindi `original_price > price` implica gia' il
    maggiore di zero. Se un quarto chiamante dovesse usare il predicato senza
    quel filtro, va aggiunto qui.

    La data e' quella CIVILE ITALIANA, come in price_origin: CURRENT_DATE
    sarebbe la data del fuso della sessione Postgres (UTC su Supabase), quindi
    per una o due ore a notte il filtro e l'etichetta parlerebbero di due
    giorni diversi.

    Aggiunge a `params` i bind param che usa (nomi deterministici: la chiamata
    e' idempotente). CAST(:p AS text[]) e non :p::text[]: il parser dei bind di
    SQLAlchemy text() non riconosce :param seguito subito da '::'.
    """
    params["sconto_catena"] = list(_SORGENTI_DI_CATENA)
    params["sconto_natura"] = list(_SORGENTI_VOLANTINO_PER_NATURA)
    p = price_alias
    return f"""(
        {p}.source = ANY(CAST(:sconto_catena AS text[]))
        AND (
            ({p}.promo_expires IS NOT NULL
             AND {p}.promo_expires >= (now() AT TIME ZONE 'Europe/Rome')::date)
         OR ({p}.promo_expires IS NULL
             AND ({p}.source = ANY(CAST(:sconto_natura AS text[]))
                  OR ({p}.original_price IS NOT NULL
                      AND {p}.original_price > {p}.price)))
        )
    )"""


def _categorie_confronto(origini: Any) -> set[str]:
    """
    Le sole categorie di confronto presenti nell'insieme.

    Scarta tutto il resto — None (i chiamanti passano `r.get("origine")`, che
    sulle risposte di un backend vecchio e' assente), stringhe che non sono
    categorie e "sconosciuta", che e' assenza di informazione e non una natura
    di prezzo. Se contassero, una riga non classificata farebbe comparire
    l'avviso su un confronto per il resto omogeneo.
    """
    return {
        o
        for o in (origini or [])
        if isinstance(o, str) and o in _CATEGORIE_CONFRONTO
    }


def confronto_misto(origini: list[str]) -> bool:
    """
    True quando fra le origini c'e' PIU' DI UNA categoria di confronto, cioe'
    quando l'utente sta guardando prezzi che pagherebbe in modi diversi: e' il
    caso in cui l'avviso serve davvero.

    Non e' piu' legato alla presenza di "online". Un prezzo di scaffale accanto
    a un'offerta da volantino ({negozio, volantino}) e' il confronto fra le due
    grandezze piu' diverse che l'app produce, e prima non avvisava nessuno;
    dopo la decisione sui discount per riga e' anche il caso piu' frequente.
    "sconosciuta" non conta (vedi _categorie_confronto), quindi
    {negozio, sconosciuta} resta omogeneo e non avvisa.

    Va calcolato sulle righe che l'utente vede DAVVERO in quel punto: un avviso
    attaccato a tutto, o calcolato su un insieme piu' grande di quello mostrato,
    e' un avviso che l'utente impara a saltare.
    """
    return len(_categorie_confronto(origini)) > 1


def avviso(origini: list[str]) -> Optional[str]:
    """
    Il testo giusto PER QUESTO confronto, None quando il confronto e' omogeneo.

    Esiste perche' i chiamanti possano scrivere `"avviso": avviso(origini)`
    senza ripetere la condizione in ogni endpoint — dove la condizione viene
    ripetuta a mano, prima o poi un endpoint la dimentica — e perche' adesso
    non c'e' piu' una sola frase: allargato il confronto misto a
    {negozio, volantino}, la vecchia frase sull'e-commerce non descriveva piu'
    il caso che l'utente aveva davanti, e un avviso che parla di un'altra cosa
    e' peggio di nessun avviso. La scelta sta qui, una volta per tutta l'app:
      - c'e' "online" e nessuna offerta a tempo -> AVVISO_ONLINE_VS_NEGOZIO;
      - offerte a tempo e nessun "online"      -> AVVISO_OFFERTA_A_TEMPO;
      - entrambe le cose, o un misto che non e' nessuna delle due (per esempio
        {negozio, segnalato})                  -> AVVISO_CONFRONTO_MISTO, la
        frase generica, l'unica vera in ogni combinazione.
    """
    categorie = _categorie_confronto(origini)
    if len(categorie) < 2:
        return None
    online = "online" in categorie
    a_tempo = bool(categorie & _A_TEMPO)
    if online and not a_tempo:
        return AVVISO_ONLINE_VS_NEGOZIO
    if a_tempo and not online:
        return AVVISO_OFFERTA_A_TEMPO
    return AVVISO_CONFRONTO_MISTO
