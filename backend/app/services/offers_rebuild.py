"""
Motore di sostituzione "rifai la spesa con le offerte".

Caso d'uso: l'utente ha una spesa passata (storico scontrini / liste) e vuole
rifarla scegliendo i prodotti IN OFFERTA, mantenendo il piu' possibile le
MARCHE che compra di solito.

Per ogni voce in ingresso il motore cerca la migliore sostituzione in offerta
secondo una scala di priorita' esplicita (restituita, non nascosta):

  1. same_product_on_offer   lo STESSO prodotto e' in offerta -> sostituzione
                             perfetta, nessun cambio.
  2. same_brand_on_offer     prodotto in offerta della STESSA marca e
                             merceologicamente compatibile.
  3. similar_on_offer        prodotto in offerta compatibile ma di ALTRA marca:
                             e' un cambio di marca e va detto chiaramente.
  4. no_offer_same_product   nessuna offerta accettabile -> si tiene il
                             prodotto originale al miglior prezzo corrente.
  5. not_found               la voce non e' nel catalogo (o non ha prezzi in
                             zona).

PERCHE' NON USA LA RICERCA TESTUALE DELL'APP
--------------------------------------------
Oggi la ricerca sbaglia in modo insidioso: "olio di oliva" propone "tonno
all'olio di oliva", "olio extravergine" propone "tigelle con olio
extravergine". In un carrello ricostruito un errore cosi' non e' un risultato
mediocre, e' un prodotto sbagliato messo nella spesa dell'utente. Qui la
compatibilita' merceologica si decide con tre regole in cascata (vedi
`_match`), non con un punteggio fuzzy:

  (a) REGOLA DELLA TESTA + PREPOSIZIONE. In italiano il nome commerciale ha la
      classe merceologica in testa, e gli INGREDIENTI sono introdotti da una
      preposizione: "Tonno all'olio di oliva" (testa: tonno), "Tigelle con
      olio extravergine" (testa: tigelle), "Olio di oliva Monini" (testa:
      olio). Quindi sono "ancore" valide solo i primi HEAD_WINDOW token
      informativi NON preceduti da una preposizione. Una sostituzione e'
      ammessa solo se originale e candidato condividono almeno un'ancora.
      Questo da' solo la regola, non un elenco di eccezioni per prodotto:
      "Barilla Al Bronzo Pasta Spaghetti" resta compatibile con "Barilla Pasta
      Spaghetti N.5" perche' "pasta" e' ancora in entrambi.
  (b) SOVRAPPOSIZIONE DEI TOKEN INFORMATIVI (vedi soglie sotto).
  (c) COERENZA DI PEZZATURA (vedi SIZE_RATIO_MIN).

Quando la sovrapposizione e' debole si preferisce `no_offer_same_product`:
meglio nessuna offerta che un prodotto diverso.

REGOLE DI SERVING DEI PREZZI
----------------------------
Identiche al resto dell'app: `is_current`, `NOT quarantined`,
`price >= MIN_VALID_PRICE`, `stores.is_active` e la soglia di freschezza per
catena (app/core/freshness.fresh_price_sql). In zona entrano i negozi fisici
nel raggio, gli store online nazionali solo se la catena serve davvero la
regione (app/core/geo_coverage) e gli store "-offerte" delle catene da
volantino solo se quella catena ha punti vendita nel raggio.

PERFORMANCE: NESSUN N+1
-----------------------
Il numero di query NON dipende dal numero di voci: sempre 4 (negozi in zona,
candidati per nome, prezzi, storico per il verdetto promo). Vedi la nota su
`MAX_STORES_PER_CHAIN` e `MAX_CANDIDATES_PER_ITEM` per i limiti che tengono
bassa la cardinalita' del lookup prezzi.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.freshness import fresh_price_sql
from app.core.geo_coverage import unavailable_online_chains
from app.services import promo as promo_service

# ── Costanti di serving ─────────────────────────────────────────────────────
# Stessa soglia di products.py/offers.py/stores.py: sotto e' placeholder/errore.
MIN_VALID_PRICE = 0.10

# ── Soglie dell'algoritmo di match (tutte motivate) ─────────────────────────

# Lunghezza minima di un token perche' sia "informativo". 3 e non 2 perche':
# (a) sotto i 3 caratteri l'indice trigram su products.name non e' utilizzabile
#     (pg_trgm non estrae trigrammi da un pattern di 2 lettere), quindi il
#     prefiltro SQL degenererebbe in un seq scan su 80k prodotti;
# (b) in italiano i token di 1-2 lettere sono quasi tutti funzionali
#     (di, da, al, in, lo, pz, ml...).
# Eccezione: i marcatori di formato/variante generati da `_profile`
# ("n5", "pct0p5", "00") restano sempre informativi.
MIN_TOKEN_LEN = 3

# Quanti token informativi iniziali possono fare da "ancora" (classe
# merceologica). 2 e non 1 perche' moltissimi nomi hanno una sotto-linea prima
# della classe ("Al Bronzo Pasta Spaghetti", "Fresco Latte Intero"); 2 e non 3
# perche' a 3 rientrerebbero gli ingredienti in terza posizione e tornerebbe il
# falso match "tonno all'olio di oliva" -> "olio di oliva".
HEAD_WINDOW = 2

# Coerenza di pezzatura: rapporto ammesso fra le quantita' totali (normalizzate
# a g / ml / pezzi). 0.60 (cioe' 0.60x - 1.67x) e' scelto per far passare i
# formati adiacenti dello stesso prodotto (400 g vs 500 g = 0.80;
# 1 L vs 1,5 L = 0.67; 75 cl vs 1 L = 0.75) e bloccare i salti di formato veri
# (500 g vs 1 kg = 0.50; 150 g vs 500 g = 0.30), che non sono lo stesso
# acquisto. Se una delle due pezzature non e' deducibile, o se le dimensioni
# sono diverse (g vs ml), non si rifiuta: non si inventa un vincolo su un dato
# che non c'e'.
SIZE_RATIO_MIN = 0.60
SIZE_RATIO_MAX = 1.0 / SIZE_RATIO_MIN

# Cambio marca con `keep_brand=False`: la priorita' 3 supera la 2 solo se il
# risparmio e' SENSIBILMENTE maggiore. Doppia soglia (assoluta + relativa)
# perche' su un prodotto da 0,89 euro un -15% sono 13 centesimi, cioe' niente
# che giustifichi cambiare marca; su uno da 6,00 euro 0,30 euro sono poco ma
# il 15% (0,90) e' significativo. Serve quindi che valgano entrambe.
BRAND_SWITCH_MIN_ABS = 0.30
BRAND_SWITCH_MIN_REL = 0.15

# ── Limiti anti-esplosione (vedi nota PERFORMANCE nel docstring) ────────────

# Voci massime per chiamata (allineato alla validazione dell'endpoint).
MAX_ITEMS = 60

# Negozi fisici per catena tenuti in zona, i piu' vicini. Nel raggio di 10 km
# di una grande citta' ci sono ~120 negozi attivi (misurato su Milano) e il
# lookup prezzi costa O(prodotti x negozi) di sonde sull'indice
# (product_id, store_id): con 120 negozi la query passa da ~0,3 s a ~2 s.
# Dentro la stessa catena e la stessa citta' i prezzi sono pressoche'
# identici, quindi i 3 punti vendita piu' vicini sono una scelta a perdita di
# informazione quasi nulla. Gli store nazionali (online / "-offerte") non
# contano nel limite: sono uno per catena.
MAX_STORES_PER_CHAIN = 3

# Candidati per voce passati al lookup prezzi, ordinati per qualita' del match
# (i candidati della stessa marca entrano per primi, vedi `_rank_key`): tiene
# il numero di product_id interrogati sotto controllo anche con 60 voci.
MAX_CANDIDATES_PER_ITEM = 40

# Tetto di sicurezza sui prodotti restituiti dal prefiltro per nome.
CANDIDATE_HARD_LIMIT = 30000

# Verdetto promo (promo.py) calcolato al massimo per questo numero di voci:
# oltre, `promo_verdict` resta None. Lo storico 60 giorni e' la parte piu'
# costosa della pipeline e oltre le ~30 voci non cambia la decisione.
MAX_PROMO_VERDICTS = 30

# ── Vocabolario ─────────────────────────────────────────────────────────────

# Parole funzionali italiane + rumore di confezionamento. NON includono i
# qualificatori merceologici (intero, scremato, integrale, bio, glutine...):
# quelli sono esattamente l'informazione che distingue due prodotti.
_STOPWORDS: frozenset[str] = frozenset("""
al all alla allo agli alle ai col con cui dal dalla dallo dai dalle del della
dello dei delle degli gli ill lll nel nella nello nei nelle non per piu sul
sulla sullo sui sulle tra fra una uno che chi come dove quando sono essere
conf confezione confezioni pezzi pezzo pack multipack astuccio busta bustina
bustine buste vasetto vasetti bottiglia bottiglie barattolo lattina lattine
scatola sacchetto sacchetti tubetto flacone vaschetta brik brick tetra
cartone cartoni formato risparmio convenienza offerta offerte promo promozione
nuovo nuova prodotto articolo circa
gr grammi kilo chilo litro litri cent centilitri millilitri
""".split())

# Preposizioni e connettori: un token informativo subito dopo uno di questi
# NON e' la classe merceologica, e' un ingrediente o un complemento.
# E' la regola che blocca "Tonno ALL'olio di oliva" e "Tigelle CON olio".
_PREPOSITIONS: frozenset[str] = frozenset("""
di del della dello dei delle degli da dal dalla dallo dai dalle
al all alla allo ai agli alle con senza per in nel nella nello nei nelle
su sul sulla sullo sui sulle tra fra base gusto gusti aroma aromi tipo stile
ripieno ripiena farcito farcita condito condita
""".split())

# Token che, se presenti SOLO nel candidato, segnalano una classe merceologica
# diversa da quella dell'ancora. Lista corta e volutamente incompleta: serve
# per le trappole note del catalogo italiano (la ricerca dell'app usa lo stesso
# approccio, vedi _irrelevant_regex in endpoints/products.py). Si applica solo
# ai token che NON sono nella voce dell'utente: se l'utente cerca "latte di
# mandorla", "mandorla" non e' un conflitto.
_CLASS_CONFLICT: dict[str, frozenset[str]] = {
    "latte": frozenset({
        "detergente", "struccante", "micellare", "corpo", "viso", "mani",
        "doccia", "bagnoschiuma", "solare", "mandorla", "mandorle", "cocco",
        "soia", "avena", "riso", "nocciola", "condensato", "gelato", "gelati",
        "yogurt", "kefir", "biscotto", "biscotti", "cioccolato", "stelvio",
        "dop", "formaggio", "caffe", "macchiato",
    }),
    "olio": frozenset({
        "motore", "lubrificante", "corpo", "viso", "capelli", "massaggio",
        "solare", "doccia", "tonno", "sardine", "acciughe", "patatine",
        "tigelle", "focaccia", "bruschette",
    }),
    "acqua": frozenset({
        "micellare", "ossigenata", "profumo", "colonia", "toletta",
        "demineralizzata", "distillata", "shampoo",
    }),
    "pasta": frozenset({
        "dentifricia", "denti", "sfoglia", "brisee", "frolla", "pizza",
        "acciughe", "modellabile", "nocciola", "pistacchio",
    }),
    "riso": frozenset({"gatto", "gatti", "cane", "cani", "shampoo", "latte"}),
    "yogurt": frozenset({"gelato", "frozen", "dessert", "cereali", "biscotti"}),
    "caffe": frozenset({
        "caffeina", "macchina", "macchine", "decalcificante", "tazzina",
        "tazzine", "bicchieri", "yogurt", "gelato", "liquore", "biscotti",
    }),
    "pollo": frozenset({"gatto", "gatti", "cane", "cani", "croccantini"}),
    "tonno": frozenset({"gatto", "gatti", "cane", "cani", "surimi", "granchio"}),
    # Linee di prodotto che condividono il nome ma sono un'altra merce:
    # "Nutella B-ready" sono snack, non crema spalmabile.
    "nutella": frozenset({"ready", "biscuits", "biscotti", "snack", "wafer", "gelato"}),
    "cioccolato": frozenset({"gelato", "yogurt", "budino", "cereali"}),
    "the": frozenset({"gelato", "yogurt", "profumo", "candela"}),
}

# Forme accentate che l'utente (o lo storico) puo' avere scritto senza accento.
# Serve SOLO al prefiltro SQL: Postgres non ha l'estensione unaccent (verificato
# su pg_extension) e ILIKE e' sensibile agli accenti, quindi '%caffe%' non
# troverebbe mai "Caffe'" scritto con l'accento.
_ACCENT_VARIANTS: dict[str, tuple[str, ...]] = {
    "caffe": ("caffè", "caffé"),
    "te": ("tè",),
    "pure": ("purè",),
    "ragu": ("ragù",),
    "piu": ("più",),
    "bonta": ("bontà",),
    "novita": ("novità",),
    "qualita": ("qualità",),
    "varieta": ("varietà",),
    "meta": ("metà",),
}

# Suffissi societari da togliere dal nome marca prima del confronto.
_BRAND_NOISE_RE = re.compile(
    r"\b(s\s*\.?\s*p\s*\.?\s*a|s\s*\.?\s*r\s*\.?\s*l|spa|srl|group|holding)\b"
)

# ── Pezzatura ───────────────────────────────────────────────────────────────
# Fattore di conversione verso l'unita' base della dimensione:
#   "w" = peso (g), "v" = volume (ml), "p" = pezzi.
_UNITS: dict[str, tuple[str, float]] = {
    "mg": ("w", 0.001),
    "g": ("w", 1.0),
    "gr": ("w", 1.0),
    "grammi": ("w", 1.0),
    "kg": ("w", 1000.0),
    "ml": ("v", 1.0),
    "cl": ("v", 10.0),
    "dl": ("v", 100.0),
    "l": ("v", 1000.0),
    "lt": ("v", 1000.0),
    "litri": ("v", 1000.0),
    "litro": ("v", 1000.0),
    "pz": ("p", 1.0),
    "pezzi": ("p", 1.0),
    "pezzo": ("p", 1.0),
    "capsule": ("p", 1.0),
    "cialde": ("p", 1.0),
    "rotoli": ("p", 1.0),
    "fogli": ("p", 1.0),
    "lavaggi": ("p", 1.0),
    "bustine": ("p", 1.0),
}
# Alternativa ordinata per lunghezza decrescente: "grammi" deve vincere su "g",
# altrimenti "500 grammi" verrebbe letto come "500 g" + token "rammi".
_UNITS_ALT = "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True))
_NUM = r"\d+(?:[.,]\d+)?"

# "6 x 100 g", "6x100g", "2 X 1,5 L"
_SIZE_MULTI_RE = re.compile(
    rf"(?<![a-z0-9])(\d+) *[x×] *({_NUM}) *({_UNITS_ALT})(?![a-z0-9])"
)
# "500 g", "1,5 L", "250ml"
_SIZE_SINGLE_RE = re.compile(rf"(?<![a-z0-9])({_NUM}) *({_UNITS_ALT})(?![a-z0-9])")
# "n.5", "n 7", "n° 3": numero di formato della pasta, NON una pezzatura.
_FORMAT_NUM_RE = re.compile(r"(?<![a-z0-9])n *[.°]? *(\d{1,3})(?![0-9])")
# "0%", "0,5 %": percentuale di grassi/alcol, distingue due prodotti.
_PCT_RE = re.compile(rf"({_NUM}) *%")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SPECIAL_TOKEN_RE = re.compile(r"^(?:n\d+|pct\d)")


def _deaccent(value: str) -> str:
    """Minuscolo senza accenti; il carattere di sostituzione U+FFFD (54 nomi
    nel catalogo, dati scrapati male) viene semplicemente eliminato."""
    folded = unicodedata.normalize("NFKD", value.lower()).replace("�", "")
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _norm_brand(brand: Optional[str]) -> str:
    """Marca normalizzata per il confronto: senza accenti, suffissi societari,
    spazi e punteggiatura. "Barilla S.p.A." e "barilla" coincidono."""
    if not brand:
        return ""
    cleaned = _BRAND_NOISE_RE.sub(" ", _deaccent(brand))
    return re.sub(r"[^a-z0-9]+", "", cleaned)


def _brand_match(a: str, b: str) -> bool:
    """
    True se le due marche normalizzate sono la stessa marca.

    Oltre all'uguaglianza accetta il prefisso (da 4 caratteri) perche' nel
    catalogo la stessa marca compare sia nuda sia con la linea attaccata:
    "Barilla" / "Barilla Al Bronzo", "Esselunga" / "Esselunga Bio". La soglia
    di 4 evita che sigle corte collidano ("Pam" non diventa "Pampers").
    """
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a))


@dataclass(frozen=True)
class _Size:
    dim: str          # "w" | "v" | "p"
    total: float      # quantita' totale nell'unita' base (g / ml / pezzi)
    pack: int = 1     # numero di confezioni ("6 x 1 L" -> 6)


def _parse_size(name: str, unit: Optional[str], unit_quantity: Optional[Any]) -> tuple[Optional[_Size], str]:
    """
    Estrae la pezzatura dal nome e restituisce (size, nome_senza_pezzatura).

    Il nome ripulito serve alla tokenizzazione: "500 g" non deve diventare il
    token "500". Se dal nome non si deduce niente si prova con le colonne
    products.unit / products.unit_quantity.
    """
    size: Optional[_Size] = None
    text_out = name

    match = _SIZE_MULTI_RE.search(text_out)
    if match:
        dim, factor = _UNITS[match.group(3)]
        pack = int(match.group(1))
        qty = float(match.group(2).replace(",", "."))
        size = _Size(dim, qty * factor * pack, pack)
        text_out = text_out[: match.start()] + " " + text_out[match.end():]

    if size is None:
        match = _SIZE_SINGLE_RE.search(text_out)
        if match:
            dim, factor = _UNITS[match.group(2)]
            qty = float(match.group(1).replace(",", "."))
            size = _Size(dim, qty * factor, 1)
            text_out = text_out[: match.start()] + " " + text_out[match.end():]

    # Le altre pezzature nel nome ("... 500 g x 2 confezioni") vanno comunque
    # togliete dai token, ma non cambiano la pezzatura principale.
    text_out = _SIZE_MULTI_RE.sub(" ", text_out)
    text_out = _SIZE_SINGLE_RE.sub(" ", text_out)

    if size is None and unit and unit_quantity is not None:
        key = _deaccent(str(unit)).strip()
        if key in _UNITS:
            dim, factor = _UNITS[key]
            try:
                size = _Size(dim, float(unit_quantity) * factor, 1)
            except (TypeError, ValueError):
                size = None

    return size, text_out


def _size_compatible(a: Optional[_Size], b: Optional[_Size]) -> bool:
    """Pezzature coerenti. Se una manca o le dimensioni sono diverse non si
    rifiuta: il vincolo si applica solo quando il dato c'e' davvero."""
    if a is None or b is None or a.dim != b.dim:
        return True
    if a.total <= 0 or b.total <= 0:
        return True
    ratio = a.total / b.total
    return SIZE_RATIO_MIN <= ratio <= SIZE_RATIO_MAX


def _same_size(a: Optional[_Size], b: Optional[_Size]) -> bool:
    if a is None or b is None or a.dim != b.dim:
        return False
    return abs(a.total - b.total) < 1e-6


@dataclass
class _Profile:
    """Rappresentazione normalizzata di un nome prodotto."""
    raw_name: str
    brand_norm: str
    size: Optional[_Size]
    tokens: list[str] = field(default_factory=list)       # token informativi in ordine
    token_set: frozenset[str] = frozenset()
    anchors: tuple[str, ...] = ()                         # possibili classi merceologiche


def _is_informative(token: str) -> bool:
    if _SPECIAL_TOKEN_RE.match(token):
        return True
    if token in _STOPWORDS:
        return False
    if token.isdigit():
        # Un numero nudo (es. "00" della farina) informa; una cifra sola no.
        return len(token) >= 2
    return len(token) >= MIN_TOKEN_LEN


def _profile(name: str, brand: Optional[str], unit: Optional[str] = None,
             unit_quantity: Optional[Any] = None,
             extra_brand: Optional[str] = None) -> _Profile:
    """
    Costruisce il profilo di un nome prodotto: pezzatura, token informativi e
    ancore (classi merceologiche ammesse). La marca viene RIMOSSA dai token:
    altrimenti "Barilla" peserebbe come un qualificatore merceologico e due
    prodotti della stessa marca sembrerebbero simili solo per questo.
    """
    brand_norm = _norm_brand(brand) or _norm_brand(extra_brand)
    folded = _deaccent(name)

    # I marcatori di formato/variante diventano token espliciti PRIMA della
    # pezzatura, cosi' "N.5 500g" non si confonde con "5 500 g".
    folded = _FORMAT_NUM_RE.sub(lambda m: f" n{m.group(1)} ", folded)
    folded = _PCT_RE.sub(lambda m: f" pct{m.group(1).replace(',', 'p').replace('.', 'p')} ", folded)

    size, stripped = _parse_size(folded, unit, unit_quantity)

    raw_tokens = _TOKEN_RE.findall(stripped)

    # Rimozione della marca dal flusso di token (anche multi-parola:
    # "Barilla Al Bronzo" -> barilla, al, bronzo).
    brand_tokens = set()
    for source in (brand, extra_brand):
        if source:
            brand_tokens.update(t for t in _TOKEN_RE.findall(_deaccent(source)) if t)
    if brand_tokens:
        kept = [t for t in raw_tokens if t not in brand_tokens]
        # Non svuotare il nome: se la marca coincide col nome intero
        # (es. prodotto chiamato solo "Barilla") si tengono i token originali.
        if kept:
            raw_tokens = kept

    tokens = [t for t in raw_tokens if _is_informative(t)]

    # Ancore: primi HEAD_WINDOW token informativi NON preceduti da preposizione.
    anchors: list[str] = []
    seen = 0
    for idx, tok in enumerate(raw_tokens):
        if not _is_informative(tok):
            continue
        if seen >= HEAD_WINDOW:
            break
        prev = raw_tokens[idx - 1] if idx > 0 else None
        if prev is None or prev not in _PREPOSITIONS:
            if tok not in anchors:
                anchors.append(tok)
        seen += 1

    return _Profile(
        raw_name=name,
        brand_norm=brand_norm,
        size=size,
        tokens=tokens,
        token_set=frozenset(tokens),
        anchors=tuple(anchors),
    )


@dataclass(frozen=True)
class _Match:
    anchor: str
    level: str      # "strong" | "weak"
    score: float


def _match(query: _Profile, cand: _Profile) -> Optional[_Match]:
    """
    Compatibilita' merceologica fra la voce dell'utente e un candidato.

    Restituisce None se incompatibile, altrimenti il livello:

      "strong" i due nomi condividono almeno un token informativo OLTRE
               all'ancora, oppure uno dei due non ha altri qualificatori
               (es. voce "Latte" contro "Latte intero 1 L": l'utente non ha
               espresso nessun vincolo che il candidato possa contraddire).
               Soglia = 1 token distintivo in comune, non 2: con 2 cadrebbero
               quasi tutte le sostituzioni reali, perche' i nomi del catalogo
               sono corti ("Spaghetti n.5" sono 2 token in tutto).
      "weak"   ancora in comune ma qualificatori distintivi TUTTI diversi
               (es. "Latte Piacere Leggero" contro "Latte Parzialmente
               Scremato"). Ammesso solo a marca identica: il cambio di marca
               con match debole e' esattamente l'errore che non vogliamo.

    Il punteggio serve a ordinare i candidati, non a deciderne l'ammissibilita'.
    """
    shared_anchor = next((a for a in query.anchors if a in cand.anchors), None)
    if shared_anchor is None:
        return None

    # Conflitto di classe: token del candidato che indicano un'altra merce e
    # che l'utente non ha chiesto.
    conflicts = _CLASS_CONFLICT.get(shared_anchor)
    if conflicts and (cand.token_set & conflicts) - query.token_set:
        return None

    if not _size_compatible(query.size, cand.size):
        return None

    q_rest = query.token_set - {shared_anchor}
    c_rest = cand.token_set - {shared_anchor}
    common_rest = q_rest & c_rest

    # "strong" se i qualificatori concordano, oppure se l'utente non ne ha
    # espressi (voce "Latte" contro "Latte intero 1 L": nessun vincolo da
    # contraddire). NON basta che sia il CANDIDATO a non averne: se la query
    # chiede "caffe in GRANI" e il candidato dichiara solo "caffe" (perche' il
    # resto del nome era la marca, es. "Yomo Il Caffe", che e' uno yogurt),
    # il qualificatore della query resta insoddisfatto -> match debole, e un
    # match debole non autorizza il cambio di marca.
    if common_rest or not q_rest:
        level = "strong"
    else:
        level = "weak"

    union = query.token_set | cand.token_set
    jaccard = len(query.token_set & cand.token_set) / len(union) if union else 0.0
    score = jaccard
    if _same_size(query.size, cand.size):
        score += 0.25
    if level == "strong":
        score += 0.15

    return _Match(anchor=shared_anchor, level=level, score=score)


# ── SQL ─────────────────────────────────────────────────────────────────────

# Negozi in zona. Stessa semantica di offers.py/products.py:
#  - negozi fisici entro il raggio;
#  - store online nazionali ("-online") solo se la catena serve la regione;
#  - store "-offerte" (volantini MD/Lidl/Penny/Aldi, coordinate nazionali) solo
#    se quella catena ha punti vendita reali nel raggio.
_SCOPE_SQL = text("""
    WITH phys AS MATERIALIZED (
        SELECT s.id,
               s.chain_id,
               ROUND((ST_Distance(
                   s.coordinates::geography,
                   ST_Point(:lng, :lat)::geography
               ))::numeric / 1000, 2) AS distance_km
        FROM stores s
        WHERE s.is_active = TRUE
          AND s.external_id NOT LIKE '%-online'
          AND s.external_id NOT LIKE '%-offerte'
          AND ST_DWithin(
                s.coordinates::geography,
                ST_Point(:lng, :lat)::geography,
                :radius_m
              )
    )
    SELECT s.id::text   AS store_id,
           s.name       AS store_name,
           c.slug       AS chain_slug,
           c.name       AS chain_name,
           ph.distance_km
    FROM phys ph
    JOIN stores s ON s.id = ph.id
    JOIN chains c ON c.id = ph.chain_id
    WHERE (CAST(:chain_slug AS text) IS NULL OR c.slug = CAST(:chain_slug AS text))
  UNION ALL
    SELECT s.id::text, s.name, c.slug, c.name, NULL::numeric
    FROM stores s
    JOIN chains c ON c.id = s.chain_id
    WHERE s.is_active = TRUE
      AND (CAST(:chain_slug AS text) IS NULL OR c.slug = CAST(:chain_slug AS text))
      AND (
            (s.external_id LIKE '%-online'
             AND NOT (c.slug = ANY(string_to_array(:no_online, ','))))
         OR (s.external_id LIKE '%-offerte'
             AND s.chain_id IN (SELECT chain_id FROM phys))
          )
""")

# Prefiltro dei candidati per nome. `name ILIKE ANY(...)` usa l'indice
# idx_products_name_trgm (gin_trgm_ops) con un Bitmap Index Scan: ~0,13 s su
# 80k prodotti contro ~1,7 s del seq scan con regex. E' volutamente LARGO: la
# selezione vera (testa + preposizione + sovrapposizione) e' in `_match`.
# UNION e non OR: con l'OR il planner perde il percorso su indice per uno dei
# due rami e ricade sul seq scan.
_CANDIDATES_SQL = text("""
    (
        SELECT id::text AS product_id, name, brand, image_url, unit, unit_quantity
        FROM products
        WHERE name ILIKE ANY(CAST(:patterns AS text[]))
        LIMIT :cand_limit
    )
    UNION
    (
        SELECT id::text AS product_id, name, brand, image_url, unit, unit_quantity
        FROM products
        WHERE id = ANY(CAST(:pids AS uuid[]))
    )
""")


def _prices_sql(params: dict) -> Any:
    """
    Prezzi correnti dei candidati nei negozi in zona.

    `store_id = ANY(:sids)` e' ripetuto anche fuori dalla join con `scope`
    perche' solo in quella forma diventa Index Cond su
    idx_prices_current_product_store (product_id, store_id) WHERE is_current:
    la join serve solo ad attaccare lo slug della catena, che e' cio' di cui ha
    bisogno fresh_price_sql per la soglia di freschezza.

    Il predicato di offerta e' scritto VERBATIM come quello dell'indice
    parziale idx_prices_current_promo_covering
    (is_current AND (original_price > price OR promo_label IS NOT NULL
    OR source = 'flyer')), cosi' resta utilizzabile quando il planner preferisce
    partire dai negozi; la variante piu' stretta con NULLIF(TRIM(...)) scarta le
    etichette vuote (carrefour_web ne ha migliaia) ed e' valutata a parte.
    """
    fresh = fresh_price_sql(params, price_alias="p", chain_alias="st")
    return text(f"""
        WITH scope AS (
            SELECT * FROM unnest(
                CAST(:sids AS uuid[]),
                CAST(:slugs AS text[])
            ) AS t(store_id, slug)
        )
        SELECT p.product_id::text AS product_id,
               p.store_id::text   AS store_id,
               p.price,
               p.original_price,
               NULLIF(TRIM(p.promo_label), '') AS promo_label,
               p.promo_expires,
               p.price_per_unit,
               p.source,
               p.in_stock,
               (
                     (p.original_price > p.price
                      OR p.promo_label IS NOT NULL
                      OR p.source = 'flyer')
                 AND (p.original_price > p.price
                      OR NULLIF(TRIM(p.promo_label), '') IS NOT NULL
                      OR p.source = 'flyer')
                 AND (p.promo_expires IS NULL OR p.promo_expires >= CURRENT_DATE)
               ) AS is_offer
        FROM prices p
        JOIN scope st ON st.store_id = p.store_id
        WHERE p.product_id = ANY(CAST(:pids AS uuid[]))
          AND p.store_id = ANY(CAST(:sids AS uuid[]))
          AND p.is_current = TRUE
          AND NOT p.quarantined
          AND p.price >= :min_valid_price
          AND {fresh}
    """)


# Storico per il verdetto promo, in UNA query per tutte le voci scelte.
# Versione batch della coppia di query di services/promo.py: la funzione
# compute_promo_check lavora su un solo product_id e qui servirebbe una query
# per voce (N+1). Le soglie e la classificazione restano quelle di promo.py
# (vedi `_promo_verdicts`), quindi il verdetto e' lo stesso dell'endpoint
# GET /api/v1/promo/{product_id}.
_PROMO_HISTORY_SQL = text("""
    SELECT product_id::text AS product_id,
           store_id::text   AS store_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY price) AS median_60d,
           count(*) AS n_obs
    FROM prices
    WHERE product_id = ANY(CAST(:pids AS uuid[]))
      AND quarantined = FALSE
      AND scraped_at > NOW() - make_interval(days => :days)
    GROUP BY product_id, store_id
""")

_NIL_UUID = "00000000-0000-0000-0000-000000000000"


# ── Helper di formattazione ─────────────────────────────────────────────────

def _eur(value: float) -> str:
    """Importo in formato italiano: 0,40 invece di 0.40."""
    return f"{value:.2f}".replace(".", ",")


def _fnum(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _discount_pct(price: Optional[float], original: Optional[float]) -> Optional[int]:
    if price is None or original is None or original <= price or original <= 0:
        return None
    return int(round((original - price) / original * 100))


# ── Pipeline ────────────────────────────────────────────────────────────────

def _limit_stores(rows: list[dict]) -> list[dict]:
    """
    Tiene gli store nazionali (distance_km NULL) e i MAX_STORES_PER_CHAIN
    punti vendita fisici piu' vicini per catena. Vedi la motivazione sulla
    costante.
    """
    by_chain: dict[str, list[dict]] = {}
    out: list[dict] = []
    for row in rows:
        if row["distance_km"] is None:
            out.append(row)
        else:
            by_chain.setdefault(row["chain_slug"], []).append(row)
    for stores in by_chain.values():
        stores.sort(key=lambda r: float(r["distance_km"]))
        out.extend(stores[:MAX_STORES_PER_CHAIN])
    return out


def _like_patterns(anchor_tokens: set[str]) -> list[str]:
    """
    Pattern ILIKE per il prefiltro trigram, uno per ancora (piu' le varianti
    accentate note). Le ancore sotto MIN_TOKEN_LEN sono escluse: pg_trgm non
    puo' usarle e farebbero degenerare la query in un seq scan.
    """
    patterns: list[str] = []
    for token in sorted(anchor_tokens):
        if len(token) < MIN_TOKEN_LEN or _SPECIAL_TOKEN_RE.match(token):
            continue
        patterns.append(f"%{token}%")
        for variant in _ACCENT_VARIANTS.get(token, ()):
            patterns.append(f"%{variant}%")
    return patterns


def _rank_key(entry: tuple[_Match, dict]) -> tuple:
    """Ordinamento dei candidati per qualita' del match (non per prezzo):
    prima la stessa marca, poi i match forti, poi il punteggio."""
    match, cand = entry
    return (
        0 if cand["_brand_same"] else 1,
        0 if match.level == "strong" else 1,
        -match.score,
    )


def _offer_sort_key(row: dict) -> tuple:
    """
    Scelta dell'offerta migliore dentro un bucket di priorita':
    match forte prima del debole, poi prezzo piu' basso, poi disponibilita',
    poi sconto piu' alto, poi negozio piu' vicino.

    L'ordinamento primario e' sul PREZZO e non sul prezzo al litro/kg: la
    coerenza di pezzatura e' gia' garantita da SIZE_RATIO_MIN, quindi a parita'
    di formato il prezzo e' l'unico numero che l'utente paga davvero.
    """
    return (
        0 if row["_level"] == "strong" else 1,
        row["price"],
        0 if row["in_stock"] is not False else 1,
        -(row["discount_pct"] or 0),
        row["distance_km"] if row["distance_km"] is not None else 9999.0,
        -row["_score"],
    )


async def _promo_verdicts(db: AsyncSession, keys: list[tuple[str, str, float]]) -> dict[tuple[str, str], str]:
    """
    Verdetto promo (true_promo / weak_promo / fake_promo / insufficient_history)
    per le coppie (product_id, store_id) scelte, in UNA query.

    Riusa services/promo.py: stessa finestra storica, stesso numero minimo di
    osservazioni, stesse soglie e stessa funzione di classificazione, quindi il
    verdetto coincide con quello di compute_promo_check / GET /api/v1/promo.
    """
    if not keys:
        return {}
    pids = sorted({pid for pid, _, _ in keys})
    rows = (await db.execute(
        _PROMO_HISTORY_SQL,
        {"pids": pids, "days": promo_service.HISTORY_DAYS},
    )).mappings().all()

    history = {(r["product_id"], r["store_id"]): r for r in rows}
    out: dict[tuple[str, str], str] = {}
    for pid, sid, price in keys:
        hist = history.get((pid, sid))
        median = _fnum(hist["median_60d"]) if hist else None
        n_obs = int(hist["n_obs"]) if hist else 0
        out[(pid, sid)] = promo_service._verdict(price, median, n_obs)
    return out


def _note(match_kind: str, original: dict, chosen: dict,
          saving: Optional[float], discount_pct: Optional[int]) -> str:
    """Frase breve in italiano da mostrare accanto alla voce."""
    if match_kind == "not_found":
        return "prodotto non trovato nel catalogo"
    if match_kind == "no_offer_same_product":
        return "nessuna offerta in zona: resta il prodotto che compri di solito"

    bits: list[str] = []
    if match_kind == "same_product_on_offer":
        bits.append("stesso prodotto in offerta")
    elif match_kind == "same_brand_on_offer":
        bits.append("stessa marca in offerta")
    else:
        da = (original.get("brand") or "").strip() or "senza marca"
        a = (chosen.get("brand") or "").strip() or "senza marca"
        bits.append(f"cambio marca: {da} -> {a}")

    if discount_pct:
        bits.append(f"-{discount_pct}%")
    if saving is not None and saving > 0.004:
        bits.append(f"risparmi {_eur(saving)}")
    return ", ".join(bits)


async def rebuild_with_offers(
    db: AsyncSession,
    items: list[dict],
    lat: float,
    lng: float,
    radius_km: float = 10.0,
    chain_slug: Optional[str] = None,
    keep_brand: bool = True,
) -> dict:
    """
    Ricostruisce una spesa scegliendo i prodotti in offerta e mantenendo, dove
    possibile, le marche della spesa originale.

    `items`: [{name, brand?, product_id?, quantity?}] (dallo storico spese).

    Ritorna {"items": [...], "summary": {...}}; il contratto completo di ogni
    voce e' nel README dell'endpoint (api/v1/endpoints/rebuild.py).
    """
    items = [it for it in (items or []) if (it.get("name") or "").strip()][:MAX_ITEMS]
    empty_summary = {
        "items_total": 0, "on_offer_count": 0, "brand_kept_count": 0,
        "brand_changed_count": 0, "not_found_count": 0,
        "total_estimated": 0.0, "total_without_offers": 0.0, "estimated_saving": 0.0,
    }
    if not items:
        return {"items": [], "summary": empty_summary}

    # ── 1) negozi in zona ───────────────────────────────────────────────────
    scope_rows = (await db.execute(_SCOPE_SQL, {
        "lat": lat,
        "lng": lng,
        "radius_m": radius_km * 1000,
        "chain_slug": chain_slug,
        "no_online": unavailable_online_chains(lat, lng),
    })).mappings().all()

    stores = _limit_stores([dict(r) for r in scope_rows])
    if not stores:
        return {
            "items": [
                {
                    "query_name": it.get("name"),
                    "original": {"product_id": it.get("product_id"), "name": it.get("name"),
                                 "brand": it.get("brand"), "price": None},
                    "chosen": None,
                    "match_kind": "not_found",
                    "saving_vs_original": None,
                    "brand_kept": False,
                    "promo_verdict": None,
                    "note": "nessun negozio nel raggio indicato",
                }
                for it in items
            ],
            "summary": {**empty_summary, "items_total": len(items), "not_found_count": len(items)},
        }
    store_by_id = {s["store_id"]: s for s in stores}

    # ── 2) profili delle voci + candidati per nome (una query) ──────────────
    queries: list[_Profile] = [
        _profile(it["name"], it.get("brand")) for it in items
    ]
    anchor_tokens: set[str] = set()
    for prof in queries:
        anchor_tokens.update(prof.anchors)

    item_pids = [str(it["product_id"]) for it in items if it.get("product_id")]
    patterns = _like_patterns(anchor_tokens)

    cand_rows = (await db.execute(_CANDIDATES_SQL, {
        "patterns": patterns or ["%\u0000%"],
        "pids": item_pids or [_NIL_UUID],
        "cand_limit": CANDIDATE_HARD_LIMIT,
    })).mappings().all()

    candidates: list[dict] = []
    cand_profiles: list[_Profile] = []
    for row in cand_rows:
        candidates.append(dict(row))
        cand_profiles.append(_profile(row["name"], row["brand"], row["unit"], row["unit_quantity"]))
    cand_by_id = {c["product_id"]: i for i, c in enumerate(candidates)}

    # Se la voce porta un product_id ma non la marca, la marca autorevole e'
    # quella del catalogo: si ricostruisce il profilo togliendola dai token,
    # altrimenti "Barilla" peserebbe come qualificatore merceologico.
    for pos, item in enumerate(items):
        if item.get("brand") or not item.get("product_id"):
            continue
        idx = cand_by_id.get(str(item["product_id"]))
        if idx is None:
            continue
        catalog_brand = candidates[idx]["brand"]
        if catalog_brand:
            queries[pos] = _profile(item["name"], None, extra_brand=catalog_brand)

    # ── 3) match per voce, in memoria (nessuna query per voce) ──────────────
    # Per ogni voce: il prodotto ORIGINALE (quello che l'utente comprava) e i
    # candidati compatibili, limitati a MAX_CANDIDATES_PER_ITEM.
    per_item: list[dict] = []
    wanted_pids: set[str] = set()

    for item, qprof in zip(items, queries):
        declared_pid = str(item["product_id"]) if item.get("product_id") else None
        brand_hint = _norm_brand(item.get("brand"))

        scored: list[tuple[_Match, dict]] = []
        for cand, cprof in zip(candidates, cand_profiles):
            match = _match(qprof, cprof)
            if match is None:
                continue
            brand_same = _brand_match(qprof.brand_norm or brand_hint, cprof.brand_norm)
            enriched = dict(cand)
            enriched["_brand_same"] = brand_same
            enriched["_profile"] = cprof
            scored.append((match, enriched))

        # Prodotto originale: l'id dichiarato vince sempre; altrimenti il
        # candidato piu' simile (la stessa marca pesa, vedi _rank_key).
        original_idx = cand_by_id.get(declared_pid) if declared_pid else None
        if original_idx is not None:
            original_row = candidates[original_idx]
            original_profile = cand_profiles[original_idx]
        elif scored:
            best = min(scored, key=_rank_key)[1]
            original_row = {k: v for k, v in best.items() if not k.startswith("_")}
            original_profile = best["_profile"]
        else:
            original_row = None
            original_profile = None

        # Se l'originale e' noto, la marca da mantenere e' la SUA (dato di
        # catalogo), non quella eventualmente approssimata nella voce.
        if original_profile is not None and original_profile.brand_norm:
            target_brand = original_profile.brand_norm
            for match, cand in scored:
                cand["_brand_same"] = _brand_match(target_brand, cand["_profile"].brand_norm)

        scored.sort(key=_rank_key)
        kept = scored[:MAX_CANDIDATES_PER_ITEM]

        if original_row is not None:
            wanted_pids.add(original_row["product_id"])
        for _, cand in kept:
            wanted_pids.add(cand["product_id"])

        per_item.append({
            "item": item,
            "query_profile": qprof,
            "original": original_row,
            "original_profile": original_profile,
            "candidates": kept,
        })

    # ── 4) prezzi dei candidati nei negozi in zona (una query) ──────────────
    prices_by_product: dict[str, list[dict]] = {}
    if wanted_pids:
        price_params: dict = {
            "pids": sorted(wanted_pids),
            "sids": [s["store_id"] for s in stores],
            "slugs": [s["chain_slug"] for s in stores],
            "min_valid_price": MIN_VALID_PRICE,
        }
        sql = _prices_sql(price_params)
        for row in (await db.execute(sql, price_params)).mappings().all():
            prices_by_product.setdefault(row["product_id"], []).append(dict(row))

    def _best_current(pid: Optional[str]) -> Optional[dict]:
        """Miglior prezzo corrente (offerta o no) del prodotto in zona."""
        rows = prices_by_product.get(pid or "")
        if not rows:
            return None
        return min(rows, key=lambda r: (
            float(r["price"]),
            0 if r["in_stock"] is not False else 1,
        ))

    def _offers_for(pid: str, level: str, score: float) -> list[dict]:
        out = []
        for row in prices_by_product.get(pid, []):
            if not row["is_offer"]:
                continue
            store = store_by_id.get(row["store_id"])
            if store is None:
                continue
            price = float(row["price"])
            original_price = _fnum(row["original_price"])
            out.append({
                **row,
                "price": price,
                "original_price": original_price,
                "discount_pct": _discount_pct(price, original_price),
                "distance_km": _fnum(store["distance_km"]),
                "chain_slug": store["chain_slug"],
                "chain_name": store["chain_name"],
                "store_name": store["store_name"],
                "_level": level,
                "_score": score,
            })
        return out

    # ── 5) scelta per voce ──────────────────────────────────────────────────
    results: list[dict] = []
    verdict_keys: list[tuple[str, str, float]] = []

    for entry in per_item:
        item = entry["item"]
        original_row = entry["original"]
        query_name = item["name"]

        if original_row is None:
            results.append({
                "query_name": query_name,
                "original": {
                    "product_id": item.get("product_id"),
                    "name": query_name,
                    "brand": item.get("brand"),
                    "price": None,
                },
                "chosen": None,
                "match_kind": "not_found",
                "saving_vs_original": None,
                "brand_kept": False,
                "promo_verdict": None,
                "note": _note("not_found", {}, {}, None, None),
                "_quantity": max(float(item.get("quantity") or 1), 0.0),
            })
            continue

        original_pid = original_row["product_id"]
        original_best = _best_current(original_pid)
        original_price = float(original_best["price"]) if original_best else None
        target_brand = (entry["original_profile"].brand_norm
                        if entry["original_profile"] else _norm_brand(item.get("brand")))

        same_product: list[dict] = []
        same_brand: list[dict] = []
        similar: list[dict] = []

        for match, cand in entry["candidates"]:
            offers = _offers_for(cand["product_id"], match.level, match.score)
            if not offers:
                continue
            if cand["product_id"] == original_pid:
                same_product.extend(offers)
            elif cand["_brand_same"]:
                same_brand.extend(offers)
            elif match.level == "strong":
                # Cambio marca: ammesso solo con match forte. Un match debole
                # su un'altra marca e' il falso positivo che non vogliamo.
                similar.extend(offers)

        # Se l'originale non e' fra i candidati (match con se stesso rifiutato
        # per via della pezzatura, o id dichiarato fuori dal prefiltro) la
        # priorita' 1 va comunque verificata.
        if not same_product:
            same_product = _offers_for(original_pid, "strong", 1.0)

        best_same_product = min(same_product, key=_offer_sort_key) if same_product else None
        best_same_brand = min(same_brand, key=_offer_sort_key) if same_brand else None
        best_similar = min(similar, key=_offer_sort_key) if similar else None

        chosen_row: Optional[dict] = None
        match_kind = "no_offer_same_product"

        if best_same_product is not None:
            chosen_row, match_kind = best_same_product, "same_product_on_offer"
        else:
            prefer_similar = False
            if not keep_brand and best_similar is not None:
                if best_same_brand is None:
                    prefer_similar = True
                else:
                    gain = best_same_brand["price"] - best_similar["price"]
                    prefer_similar = (
                        gain >= BRAND_SWITCH_MIN_ABS
                        and gain >= BRAND_SWITCH_MIN_REL * best_same_brand["price"]
                    )
            if prefer_similar:
                chosen_row, match_kind = best_similar, "similar_on_offer"
            elif best_same_brand is not None:
                chosen_row, match_kind = best_same_brand, "same_brand_on_offer"
            elif best_similar is not None:
                chosen_row, match_kind = best_similar, "similar_on_offer"

        if chosen_row is None:
            # Priorita' 4: nessuna offerta, si tiene l'originale al miglior
            # prezzo corrente. La voce non resta mai vuota.
            if original_best is None:
                results.append({
                    "query_name": query_name,
                    "original": {
                        "product_id": original_pid,
                        "name": original_row["name"],
                        "brand": original_row["brand"],
                        "price": None,
                    },
                    "chosen": None,
                    "match_kind": "not_found",
                    "saving_vs_original": None,
                    "brand_kept": False,
                    "promo_verdict": None,
                    "note": "nessun prezzo valido in zona per questo prodotto",
                    "_quantity": max(float(item.get("quantity") or 1), 0.0),
                })
                continue
            store = store_by_id[original_best["store_id"]]
            price = float(original_best["price"])
            orig_p = _fnum(original_best["original_price"])
            chosen_row = {
                **original_best,
                "price": price,
                "original_price": orig_p,
                "discount_pct": _discount_pct(price, orig_p),
                "distance_km": _fnum(store["distance_km"]),
                "chain_slug": store["chain_slug"],
                "chain_name": store["chain_name"],
                "store_name": store["store_name"],
            }
            chosen_product = original_row
            match_kind = "no_offer_same_product"
        else:
            chosen_product = candidates[cand_by_id[chosen_row["product_id"]]]

        brand_kept = (
            match_kind in ("same_product_on_offer", "no_offer_same_product")
            or _brand_match(target_brand, _norm_brand(chosen_product.get("brand")))
        )
        saving = (round(original_price - chosen_row["price"], 2)
                  if original_price is not None else None)

        chosen_out = {
            "product_id": chosen_row["product_id"],
            "name": chosen_product["name"],
            "brand": chosen_product["brand"],
            "image_url": chosen_product["image_url"],
            "price": round(chosen_row["price"], 2),
            "original_price": (round(chosen_row["original_price"], 2)
                               if chosen_row["original_price"] is not None else None),
            "discount_pct": chosen_row["discount_pct"],
            "price_per_unit": _fnum(chosen_row.get("price_per_unit")),
            "promo_label": chosen_row.get("promo_label"),
            "promo_expires": chosen_row.get("promo_expires"),
            "chain_slug": chosen_row["chain_slug"],
            "chain_name": chosen_row["chain_name"],
            "store_id": chosen_row["store_id"],
            "store_name": chosen_row["store_name"],
            "distance_km": chosen_row["distance_km"],
        }

        if match_kind != "no_offer_same_product":
            verdict_keys.append((chosen_row["product_id"], chosen_row["store_id"],
                                 chosen_row["price"]))

        results.append({
            "query_name": query_name,
            "original": {
                "product_id": original_pid,
                "name": original_row["name"],
                "brand": original_row["brand"],
                "price": round(original_price, 2) if original_price is not None else None,
            },
            "chosen": chosen_out,
            "match_kind": match_kind,
            "saving_vs_original": saving,
            "brand_kept": bool(brand_kept),
            "promo_verdict": None,
            "note": _note(match_kind, original_row, chosen_product, saving,
                          chosen_row["discount_pct"]),
            "_quantity": max(float(item.get("quantity") or 1), 0.0),
            "_original_price": original_price,
        })

    # ── 6) verdetto promo (una query) ──────────────────────────────────────
    verdicts = await _promo_verdicts(db, verdict_keys[:MAX_PROMO_VERDICTS])
    for res in results:
        chosen = res.get("chosen")
        if chosen and res["match_kind"] != "no_offer_same_product":
            res["promo_verdict"] = verdicts.get((chosen["product_id"], chosen["store_id"]))

    # ── 7) riepilogo ───────────────────────────────────────────────────────
    on_offer = {"same_product_on_offer", "same_brand_on_offer", "similar_on_offer"}
    total_estimated = 0.0
    total_without = 0.0
    on_offer_count = brand_kept_count = brand_changed_count = not_found_count = 0

    for res in results:
        qty = res.pop("_quantity", 1.0)
        base = res.pop("_original_price", None)
        chosen = res.get("chosen")
        if res["match_kind"] == "not_found":
            not_found_count += 1
            continue
        if res["match_kind"] in on_offer:
            on_offer_count += 1
        if chosen:
            total_estimated += chosen["price"] * qty
            total_without += (base if base is not None else chosen["price"]) * qty
            if res["brand_kept"]:
                brand_kept_count += 1
            else:
                brand_changed_count += 1

    summary = {
        "items_total": len(results),
        "on_offer_count": on_offer_count,
        "brand_kept_count": brand_kept_count,
        "brand_changed_count": brand_changed_count,
        "not_found_count": not_found_count,
        "total_estimated": round(total_estimated, 2),
        "total_without_offers": round(total_without, 2),
        "estimated_saving": round(total_without - total_estimated, 2),
    }
    return {"items": results, "summary": summary}
