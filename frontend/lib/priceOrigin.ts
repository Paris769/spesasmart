// Provenienza dei prezzi, lato PRESENTAZIONE.
//
// Questo modulo NON classifica piu' i prezzi: la provenienza la decide il
// backend (app/core/price_origin.py) e arriva nei campi `origine` e
// `origine_label`. Qui restano soltanto le cose che servono a RENDERE:
// leggere l'origine dichiarata, un'etichetta breve per le righe strette,
// la frase dell'avviso e la condizione che decide se mostrarlo.
//
// Perche' la classificazione e' stata rimossa: fino a ieri questo file
// conteneva una copia della mappa sorgente->origine del backend, con dentro
// esattamente il bug che il backend aveva appena corretto (una promo_expires
// nel passato veniva letta come offerta corrente, su oltre centomila prezzi
// di listino). Due implementazioni della stessa regola in due linguaggi
// divergono sempre: la seconda si accorge dei cambiamenti in ritardo, e nel
// frattempo mente. Se un punto della UI non ha i campi, il rimedio e' farli
// arrivare dal backend, non ricalcolarli qui.
//
// Regola di compatibilita': frontend (Vercel) e backend (Render) si
// deployano separatamente. Quando l'origine non arriva, le etichette
// SPARISCONO. Nessun default "online": sarebbe esattamente la bugia che
// stiamo eliminando.

/** Le sei categorie dichiarate dal backend.
 *  - online    -> listino dell'e-commerce della catena
 *  - negozio   -> prezzo di scaffale pubblicato dalla catena
 *  - volantino -> promozione a tempo, ancora valida, da fare in negozio
 *  - scaduta   -> promozione finita: il prezzo non e' piu' ottenibile
 *  - segnalato -> prezzo riferito da un altro utente, non verificato
 *  - sconosciuta -> provenienza non dichiarabile: in UI non si mostra nulla */
export type Origine =
  | "online"
  | "negozio"
  | "volantino"
  | "scaduta"
  | "segnalato"
  | "sconosciuta";

/** Etichetta breve, per le righe dense e le colonne strette dove la label
 *  completa del backend (che porta anche la data) non entra. Per
 *  "sconosciuta" non c'e' etichetta: scrivere "provenienza non dichiarata"
 *  accanto a un prezzo semina dubbio senza aiutare chi sta facendo la spesa. */
const ETICHETTA: Record<Origine, string | null> = {
  online: "prezzo spesa online",
  negozio: "prezzo in negozio",
  volantino: "offerta volantino",
  scaduta: "offerta scaduta",
  segnalato: "prezzo segnalato da un utente",
  sconosciuta: null,
};

/** Mostrato quando il confronto mette insieme prezzi di natura diversa, SOLO
 *  dove il testo del backend non arriva (il campo `avviso` non c'e' su tutti
 *  gli endpoint: vedi PriceResult in lib/api.ts).
 *
 *  E' copia esatta di AVVISO_CONFRONTO_MISTO del backend
 *  (app/core/price_origin.py), che fra i suoi tre testi e' il FALLBACK: non
 *  nomina nessuna categoria, quindi e' l'unica frase vera in ogni
 *  combinazione. Gli altri due (AVVISO_ONLINE_VS_NEGOZIO,
 *  AVVISO_OFFERTA_A_TEMPO) nominano le categorie del confronto e li sceglie
 *  `avviso()` nel backend: qui NON si duplicano, e nemmeno la scelta, perche'
 *  la stessa regola scritta due volte in due linguaggi divergera' (e' il
 *  motivo per cui da questo file e' stata rimossa la mappa delle origini).
 *
 *  Il testo precedente ("spesa online, prezzi di scaffale e offerte a tempo
 *  si ottengono a condizioni diverse") elencava tre nature e non coincideva
 *  con nessuno dei tre del backend: da quando confrontoMisto scatta su
 *  qualunque coppia di categorie, su un confronto {negozio, volantino}
 *  nominava la spesa online, che in quel confronto non c'era. */
export const AVVISO_CONFRONTO_MISTO =
  "I prezzi messi a confronto non sono tutti della stessa natura: controlla " +
  "l'etichetta accanto a ciascuno.";

/** Legge l'origine dichiarata dal backend. Ritorna null quando il campo non
 *  c'e' (backend non ancora aggiornato), non e' riconosciuto o vale
 *  "sconosciuta": in tutti e tre i casi la UI non deve mostrare nulla. */
export function parseOrigine(value?: string | null): Origine | null {
  if (!value) return null;
  if (
    value === "online" ||
    value === "negozio" ||
    value === "volantino" ||
    value === "scaduta" ||
    value === "segnalato"
  ) {
    return value;
  }
  return null;
}

/** Testo breve ("offerta volantino"), senza la scadenza. */
export function etichettaOrigine(origine?: Origine | null): string | null {
  if (!origine) return null;
  return ETICHETTA[origine];
}

/** I due campi con cui il backend dichiara se su QUELLA riga si puo'
 *  affermare uno sconto. Si legge con scontoAffermabile(), non a mano.
 *
 *  `sconto_affermabile` e' il nome nuovo; `promozione_viva` e' quello che il
 *  backend mandava fino al giro precedente, quando la regola era piu' stretta
 *  (solo origine "volantino"). Frontend (Vercel) e backend (Render) si
 *  deployano separatamente, quindi nella finestra fra i due deploy arriva solo
 *  il nome vecchio: leggerlo e' PRUDENTE, perche' l'insieme vecchio e' un
 *  sottoinsieme proprio di quello nuovo (il nuovo aggiunge gli sconti senza
 *  scadenza e non aggiunge nessuna riga scaduta). Nella finestra si mostra
 *  quindi meno informazione, mai un'affermazione falsa. */
export interface RigaConSconto {
  sconto_affermabile?: boolean | null;
  /** Nome precedente dello stesso permesso, regola piu' stretta. */
  promozione_viva?: boolean | null;
}

/** True quando il backend dichiara che su questa riga si puo' affermare uno
 *  SCONTO. E' l'unico permesso per il prezzo barrato, il badge "Risparmi
 *  EUR X", il pill del promo_label e qualunque percentuale di sconto.
 *
 *  SI LEGGE, NON SI RICALCOLA. Prima questa funzione derivava il permesso
 *  dall'origine (`origine === "volantino"`), cioe' riscriveva lato client una
 *  regola del backend: e' la stessa duplicazione per cui da questo file e'
 *  stata rimossa la mappa delle sorgenti. Oggi non sarebbe nemmeno piu'
 *  possibile, perche' provenienza e sconto sono due domande DIVERSE:
 *    - la provenienza dice che TIPO di prezzo e' (online, negozio, volantino);
 *    - lo sconto affermabile dice se possiamo dire "costa meno di prima".
 *  Una riga del catalogo online di una catena puo' portare uno sconto
 *  dichiarato dalla catena: sono due fatti veri insieme, e la regola vecchia
 *  li fondeva in uno.
 *
 *  La regola che il backend applica (core/price_origin) ha due condizioni:
 *  la riga non e' dimostrabilmente scaduta (nessuna scadenza, oppure scadenza
 *  da oggi in avanti) E ha una prova di sconto (prezzo barrato piu' alto del
 *  prezzo, oppure una sorgente che per natura E' un volantino).
 *
 *  CAMPO ASSENTE (backend non ancora aggiornato): false. E' la regola di
 *  compatibilita' del resto del file - senza il dato le affermazioni
 *  SPARISCONO, non si tirano a indovinare. Costo accettato: nella finestra fra
 *  il deploy di Vercel e quello di Render si mostra meno informazione. Nessuna
 *  bugia, e per il tempo di un deploy.
 *
 *  MISURATO il 30/10/2026 su produzione, campione TABLESAMPLE SYSTEM (0,05%
 *  delle pagine di `prices`; righe is_current, non in quarantena, prezzo >=
 *  0,10). La colonna "barrato senza scadenza" e' esattamente cio' che la
 *  regola vecchia buttava via e la nuova tiene:
 *    sorgente        righe   barrato senza scadenza   scad. passata   futura
 *    eurospin_web    1.046   593                      16              0
 *    cosicomodo        671    43                       0              0
 *    iper               30    19                       0              0
 *    flyer              39     0                      39              0
 *    carrefour_web      13     2                       0              0
 *    esselunga_web       5     1                       0              0
 *    pam                 5     1                       0              0
 *  Su un secondo campione (0,1% delle pagine, limitato alle righe con
 *  scraped_at negli ultimi 10 giorni, cioe' la tolleranza di freschezza piu'
 *  larga del serving) le righe con scadenza FUTURA sono ZERO su ogni sorgente:
 *  oggi il barrato "fino al GG/MM" e' il caso raro, e lo sconto senza termine
 *  e' il caso normale. Con la regola vecchia su quelle righe fresche non
 *  compariva quasi nessun barrato. */
export function scontoAffermabile(riga?: RigaConSconto | null): boolean {
  if (!riga) return false;
  if (typeof riga.sconto_affermabile === "boolean") return riga.sconto_affermabile;
  return riga.promozione_viva === true;
}

/** Testo per il caso 3: sconto affermabile di cui NON sappiamo la scadenza.
 *  Breve per i pill, lungo per la riga leggibile della card. Non promette
 *  nessuna data e non inventa un "fino a esaurimento" che la catena non dice. */
export const NOTA_SENZA_TERMINE_BREVE = "termine non indicato";
export const NOTA_SENZA_TERMINE =
  "Sconto dichiarato dalla catena: non e' indicato fino a quando vale.";

/** La coda ESATTA che il backend aggiunge a `origine_label` nel caso 3
 *  (price_origin._NOTA_SENZA_TERMINE, preceduta da ", "). Verificata
 *  eseguendo price_origin su una riga vera: cosicomodo 1,79 col barrato 1,95
 *  e nessuna scadenza -> "prezzo spesa online, sconto dichiarato dalla
 *  catena, termine non indicato". */
const CODA_SENZA_TERMINE =
  ", sconto dichiarato dalla catena, termine non indicato";

/** Separa la nota del caso 3 dall'etichetta del backend: la provenienza resta
 *  per il pill, la nota va nella riga leggibile, e la frase si scrive UNA
 *  volta sola.
 *
 *  Perche' il backend la mette nell'etichetta: cosi' arriva anche alle
 *  superfici che non passano da qui (assistente, digest settimanale), che di
 *  quella riga leggono solo `origine_label`. Perche' qui si separa: nella card
 *  l'etichetta sta in un pill text-[10px] + truncate, che su telefono taglia
 *  proprio la coda; e rendere pill e riga insieme fa leggere all'utente la
 *  stessa frase due volte, sul caso piu' numeroso dell'app (cosicomodo col
 *  barrato e senza scadenza: 41.245 righe fresche misurate il 30/10/2026).
 *
 *  Si legge quello che il backend ha DECISO invece di ricavarlo dalla
 *  categoria. Ricavarlo era sbagliato in due modi: buttava via le sorgenti
 *  che SONO un volantino per natura (iper, 1.890 righe fresche affermabili,
 *  1.764 col barrato e ZERO con una scadenza) e la motivazione scritta
 *  nominava due sorgenti su sei - anche md, lidl, aldi e penny danno
 *  origine "volantino" con uno sconto senza termine. */
export function separaNotaSenzaTermine(label?: string | null): {
  label: string | null;
  senzaTermine: boolean;
} {
  if (!label) return { label: label ?? null, senzaTermine: false };
  const i = label.indexOf(CODA_SENZA_TERMINE);
  if (i < 0) return { label, senzaTermine: false };
  return {
    label: label.slice(0, i) + label.slice(i + CODA_SENZA_TERMINE.length),
    senzaTermine: true,
  };
}

/** La condizione che il backend ha scritto nella coda di `origine_label`
 *  (oggi solo "con carta fedelta'"), da rendere nella riga leggibile della
 *  card e non solo dentro il pill da 10px, che su telefono si troncava
 *  proprio sulla coda.
 *
 *  NON si classifica niente: non si guarda promo_label (che e' testo scrapato
 *  e sul quale il backend ha una regola sua), si legge soltanto l'etichetta
 *  che il backend ha gia' deciso. Se non c'e', non si mostra nulla.
 *
 *  Sarebbe piu' solido un campo dedicato nella risposta (`condizione`):
 *  chiesto al backend, vedi richieste_ad_altri. Finche' non c'e', questa
 *  funzione cerca la frase esatta del backend, non una parola generica. */
export function condizioneDichiarata(label?: string | null): string | null {
  if (!label) return null;
  const testo = label.toLowerCase().replace(/['’]/g, "");
  return testo.includes("con carta fedelta") ? "con carta fedelta'" : null;
}

/** True se l'insieme mette a confronto prezzi di natura diversa, cioe' se fra
 *  le origini mostrate c'e' PIU DI UNA categoria.
 *
 *  Prima la funzione pretendeva la presenza di "online":
 *  `presenti.has("online") && ...`. Era troppo stretta, e il caso che
 *  escludeva e' il piu' frequente: un prezzo di scaffale confrontato con
 *  un'offerta da volantino ({negozio, volantino}) non faceva scattare nessun
 *  avviso, benche' l'utente li paghi in modo diverso - uno vale sempre,
 *  l'altro solo in negozio e solo questa settimana. Sui discount lo stesso
 *  scaffale produce ora entrambe le categorie (con prova di sconto
 *  "volantino", senza prova "negozio"), quindi era proprio il confronto
 *  tipico a restare senza presidio.
 *
 *  "sconosciuta" non conta come categoria distinta: non e' una natura, e'
 *  l'assenza di dichiarazione, e parseOrigine la riduce gia' a null.
 *
 *  Va chiamata sull'insieme che l'utente VEDE in quel punto, non su tutte le
 *  righe che il backend ha considerato: un avviso sempre presente diventa
 *  invisibile e non protegge nessuno.
 *
 *  Le origini non dichiarate vanno scartate da chi chiama (parseOrigine),
 *  cosi' un backend vecchio non fa comparire l'avviso a caso. */
export function confrontoMisto(origini: (Origine | null)[]): boolean {
  const presenti = new Set(
    (origini.filter(Boolean) as Origine[]).filter((o) => o !== "sconosciuta")
  );
  return presenti.size > 1;
}
