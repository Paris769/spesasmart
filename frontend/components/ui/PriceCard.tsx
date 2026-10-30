"use client";
import { PriceResult, outbound } from "@/lib/api";
import { useCountUp } from "@/lib/useCountUp";
import {
  NOTA_SENZA_TERMINE,
  condizioneDichiarata,
  parseOrigine,
  scontoAffermabile,
  separaNotaSenzaTermine,
} from "@/lib/priceOrigin";
import OrigineBadge from "@/components/ui/OrigineBadge";
import {
  Truck,
  Store,
  Tag,
  Globe,
  Clock,
  Crown,
  ArrowUpRight,
  TrendingDown,
  ShoppingCart,
  AlertTriangle,
  Info,
  Newspaper,
  CalendarX,
  UserRound,
  CreditCard,
} from "lucide-react";

interface Props {
  result: PriceResult;
  rank: number;
  avgPrice?: number; // media dei prezzi del set â†’ delta "-X% vs media"
  imageUrl?: string | null;
  /** Unita del prodotto (kg, l, pzâ€¦) per etichettare il prezzo per unita. */
  unit?: string | null;
}

// Etichetta per il prezzo per unita: normalizza l'unita del prodotto
// ("ml" â†’ "L", "g" â†’ "kg"). Default "kg" come comportamento storico.
function perUnitSuffix(unit?: string | null) {
  const u = (unit || "").trim().toLowerCase();
  if (!u) return "kg";
  if (["l", "lt", "ml", "cl", "litri", "litro"].includes(u)) return "L";
  if (["kg", "g", "gr", "grammi"].includes(u)) return "kg";
  if (["pz", "pezzi", "pc", "pcs"].includes(u)) return "pz";
  return u;
}

// Pallino col colore di brand della catena (l'arancio resta riservato ai deal).
const CHAIN_DOT: Record<string, string> = {
  esselunga: "#E2001A",
  conad: "#E30613",
  carrefour: "#004E9F",
  coop: "#E2001A",
  lidl: "#0050AA",
  eurospin: "#FFDD00",
  md: "#F58220",
  aldi: "#004B93",
  penny: "#CD1719",
  pam: "#C8102E",
  famila: "#E2001A",
  ilgigante: "#F39200",
  italmark: "#0093D0",
};

export default function PriceCard({ result, rank, avgPrice, imageUrl, unit }: Props) {
  const unavailable = result.in_stock === false;
  const isBest = rank === 0 && !unavailable;
  const dot = CHAIN_DOT[result.chain_slug] ?? "#6B7280";
  const price = useCountUp(result.price, 480);
  const [eur, cent] = price.toFixed(2).split(".");

  // Staleness: oltre 3 giorni segnalo l'eta del dato; stale === true dal
  // backend prevale con un avviso piu esplicito.
  const ageDays = result.scraped_at
    ? Math.floor((Date.now() - new Date(result.scraped_at).getTime()) / 86_400_000)
    : null;
  const isStale = result.stale === true;
  const isAging = !isStale && ageDays != null && ageDays > 3;

  // Provenienza del prezzo. null = il backend non la manda ancora: in quel
  // caso nessuna etichetta e si torna al comportamento precedente.
  const origine = parseOrigine(result.origine);

  // Offerta finita: quel prezzo non si ottiene piu'. Nessuna affermazione al
  // presente su un prezzo che l'app stessa dichiara passato - nemmeno il pill
  // verde "-X% vs media", che e' il colore del risparmio su un numero che non
  // si puo' pagare.
  const offertaFinita = origine === "scaduta";

  // UNA sola definizione di "sconto" in questa card, e NON la decide questa
  // card: la dichiara il backend riga per riga. Vale per il barrato, per il
  // badge arancione "Risparmi", per il pill del promo_label e per qualunque
  // percentuale di sconto. Vedi scontoAffermabile() in lib/priceOrigin.
  //
  // Prima la condizione era `origine === "volantino"`, cioe' la provenienza
  // usata come prova di sconto: due domande diverse fuse in una. Su una riga
  // del catalogo online con un prezzo barrato dichiarato dalla catena la
  // fusione sopprimeva un'informazione VERA, e il campione del 30/10/2026 dice
  // che e' il caso piu' frequente (su cosicomodo, la sorgente piu' grande e
  // piu' fresca, 43 righe campionate su 671 hanno il barrato e nessuna
  // scadenza, mentre le righe con scadenza sono zero).
  // Resta fuori, e questo e' il punto, la promozione SCADUTA: e' da la' che
  // veniva "Risparmi EUR 10,00" su un'offerta finita il 23/08.
  const affermabile = scontoAffermabile(result);
  // Caso 3: lo sconto c'e' ma non sappiamo entro quando. Si mostra, e si dice
  // che il termine non e' indicato. Nessuna data, nessun conto alla rovescia.
  // La nota arriva nella coda dell'etichetta del backend e qui si separa:
  // provenienza nel pill, nota nella riga leggibile qui sotto. Prima erano
  // entrambe, cioe' il pill "prezzo spesa online, sconto dichiarato dalla
  // catena, termine non indicato" e subito sotto la stessa frase per esteso.
  const { label: etichetta, senzaTermine } = separaNotaSenzaTermine(
    result.origine_label
  );

  // Il barrato si confronta col prezzo: 9.584 righe servite (tutte
  // eurospin_web) hanno original_price > 0 e <= price, perche' lo spider
  // legge due campi diversi della stessa pagina. Un barrato PIU' BASSO del
  // prezzo mostrato, nel linguaggio dell'app, dice "prima costava meno": un
  // numero incomprensibile accanto a quello che conta.
  const sconto =
    affermabile && result.original_price && result.original_price > result.price
      ? { barrato: result.original_price, risparmio: (result.original_price - result.price).toFixed(2) }
      : null;

  const deltaPct =
    !offertaFinita && avgPrice && avgPrice > result.price
      ? Math.round(((avgPrice - result.price) / avgPrice) * 100)
      : null;

  const freshness = (() => {
    if (!result.scraped_at) return null;
    const h = Math.floor((Date.now() - new Date(result.scraped_at).getTime()) / 3_600_000);
    if (h < 1) return { label: "Aggiornato ora", tone: "text-success" };
    if (h < 24) return { label: `${h}h fa`, tone: "text-stone-400" };
    return { label: `${Math.floor(h / 24)}g fa`, tone: "text-warning" };
  })();

  const locations = result.chain_locations ?? [];
  const hasMultipleLocations = locations.length > 1;
  const availableLocations = locations.filter((location) => location.in_stock !== false);
  const visibleLocations = (availableLocations.length ? availableLocations : locations).slice(0, 4);

  // Natura del prezzo. SE mostrarla la decide l'ORIGINE, non is_online:
  // pilotare la frase con is_online la sopprimeva proprio sulle catene
  // e-commerce con negozi fisici (CosiComodo, Iper, EasyCoop, Pam a Casa),
  // dove la riga grande parlava di un negozio a 3 km e la sola cosa che
  // diceva "online" era il pill da 10px accanto al nome catena.
  //
  // COSA dice la frase, sul ramo online, lo decide invece is_online, perche'
  // "tutta Italia" e' vero solo per i negozi VIRTUALI nazionali. Misurato in
  // produzione (29/10/2026): gli store con external_id '%-online' sono TRE -
  // esselunga-online, carrefour-online, conad-online - e is_online in backend
  // e' esattamente `external_id LIKE '%-online'` (products.py:557).
  // Tutte le altre catene che la mappa classifica "online" hanno ZERO store
  // virtuali: famila 324 negozi, italmark 15, ilgigante 12 (le tre catene di
  // CosiComodo, la sorgente piu' grande e piu' fresca dell'app), iper 22,
  // coop 1, pam 1. Per loro quel prezzo e' il catalogo online DI QUEL punto
  // vendita, ordinabile solo nella sua area di consegna, e NON e' il prezzo
  // di scaffale di quel negozio: "tutta Italia" sarebbe falso due volte.
  const mostraNatura = origine !== null || result.is_online;
  const natura = (() => {
    const tipo = origine ?? (result.is_online ? "online" : null);
    if (tipo === "online")
      return result.is_online ? (
        <p className="flex items-center gap-1">
          <Globe size={13} /> Spesa online - tutta Italia
        </p>
      ) : (
        // Negozio fisico con catalogo online. Subito sotto, `dove` stampa km
        // e nome del punto vendita (o l'elenco delle sedi della catena),
        // quindi "del punto vendita" ha un referente sullo schermo e le due
        // righe non si smentiscono piu'. Si dice "punto vendita" e non
        // "questo negozio" perche' la card aggrega le sedi della stessa
        // catena: il prezzo e' di uno di quei cataloghi, non di tutti.
        <p className="flex items-start gap-1">
          <Globe size={13} className="mt-0.5 shrink-0" />
          <span>
            Prezzo del catalogo online del punto vendita - in negozio il prezzo
            alla cassa puo' essere diverso
          </span>
        </p>
      );
    if (tipo === "negozio")
      return (
        <p className="flex items-center gap-1">
          <Store size={13} /> Prezzo di listino in negozio
        </p>
      );
    if (tipo === "volantino")
      return (
        <p className="flex items-center gap-1 text-amber-800">
          <Newspaper size={13} /> Offerta valida in negozio
        </p>
      );
    if (tipo === "scaduta")
      return (
        <p className="flex items-center gap-1 text-red-700">
          <CalendarX size={13} /> Offerta finita: oggi in negozio paghi il
          listino
        </p>
      );
    if (tipo === "segnalato")
      return (
        <p className="flex items-center gap-1 text-amber-800">
          <UserRound size={13} /> Prezzo visto da un utente, non verificato da
          noi
        </p>
      );
    return null;
  })();

  // La condizione a cui quel prezzo vale in cassa (oggi solo la carta
  // fedelta'), in corpo 13px dentro il blocco della natura.
  //
  // Prima arrivava SOLO dentro OrigineBadge, che e' text-[10px] + truncate:
  // su telefono "prezzo in negozio, con carta fedelta'" (37 caratteri) si
  // tagliava proprio sulla coda, cioe' sulla condizione, e la riga grande
  // diceva "Prezzo di listino in negozio" senza nominare la carta. L'utente
  // legge la frase, non il pill: entra senza carta e paga il prezzo pieno.
  //
  // Il badge resta (porta anche la scadenza): qui si aggiunge la riga
  // leggibile, che non spinge il prezzo fuori dalla sua riga perche' sta nel
  // blocco di testo sopra al prezzo.
  const condizione = condizioneDichiarata(result.origine_label);

  // Dove si paga quel prezzo: sedi della catena o negozio singolo. Per gli
  // store online non c'e' distanza significativa.
  const dove = hasMultipleLocations ? (
    <div className="flex flex-col gap-1">
      <p className="flex items-center gap-1 font-medium text-stone-600">
        <Store size={13} /> {availableLocations.length || locations.length} sedi disponibili
      </p>
      <div className="flex flex-wrap gap-1">
        {visibleLocations.map((location) => (
          <span
            key={location.store_id}
            className="rounded-pill bg-stone-100 px-2 py-0.5 text-[11px] text-stone-600"
          >
            {location.distance_km != null ? `${location.distance_km} km - ` : ""}{location.store_name}
          </span>
        ))}
        {locations.length > visibleLocations.length && (
          <span className="rounded-pill bg-stone-100 px-2 py-0.5 text-[11px] text-stone-500">
            +{locations.length - visibleLocations.length} altre
          </span>
        )}
      </div>
    </div>
  ) : (
    <p className="flex items-center gap-1">
      <Store size={13} /> {result.distance_km} km - {result.store_name}
    </p>
  );

  return (
    <div
      className={`relative overflow-hidden rounded-2xl p-3.5 flex gap-3 animate-pop-in transition active:scale-[0.99] ${
        unavailable
          ? "bg-red-50/40 border border-red-200 shadow-card"
          : isBest
          ? "bg-primary-50 ring-1 ring-primary/40 shadow-best"
          : "bg-white border border-stone-200 shadow-card"
      }`}
    >
      {/* sheen che attraversa la card migliore */}
      {isBest && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-0 -left-1/3 w-1/3 bg-white/40 blur-md animate-sheen"
        />
      )}

      {/* immagine prodotto */}
      <div className="shrink-0 w-[60px] h-[60px] rounded-xl bg-white border border-stone-200 grid place-items-center overflow-hidden">
        {imageUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={imageUrl} alt="" className="w-full h-full object-contain" />
        ) : (
          <ShoppingCart size={22} className="text-stone-300" />
        )}
      </div>

      {/* corpo */}
      <div className="flex-1 min-w-0 flex flex-col gap-1.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span
            className="inline-flex items-center gap-1 text-[11px] font-semibold text-deep"
          >
            <span className="w-2 h-2 rounded-full" style={{ background: dot }} />
            {result.chain_name}
          </span>
          <OrigineBadge origine={origine} label={etichetta} />
          {isBest && (
            <span className="inline-flex items-center gap-0.5 text-[10px] font-bold text-white bg-save-grad px-1.5 py-0.5 rounded-pill">
              <Crown size={11} /> MIGLIORE
            </span>
          )}
          {unavailable && (
            <span className="inline-flex items-center gap-0.5 text-[10px] font-bold text-red-700 bg-red-100 px-1.5 py-0.5 rounded-pill">
              <AlertTriangle size={11} /> NON DISPONIBILE
            </span>
          )}
          {isStale && (
            <span className="inline-flex items-center gap-0.5 text-[10px] font-bold text-stone-600 bg-stone-200 px-1.5 py-0.5 rounded-pill">
              <Clock size={11} /> dato datato - verifica sul sito
            </span>
          )}
          {isAging && (
            <span className="inline-flex items-center gap-0.5 text-[10px] font-bold text-amber-800 bg-amber-100 px-1.5 py-0.5 rounded-pill">
              <Clock size={11} /> aggiornato {ageDays}g fa
            </span>
          )}
        </div>

        <div className="text-[13px] text-stone-500 leading-tight">
          {unavailable ? (
            <p className="flex items-center gap-1">
              <AlertTriangle size={13} className="text-red-600" /> Non disponibile sul sito
            </p>
          ) : (
            <div className="flex flex-col gap-1">
              {mostraNatura && natura}
              {/* Caso 3: lo sconto e' affermabile ma la catena non dice entro
                  quando. Il barrato e la percentuale si mostrano (sono veri),
                  e questa riga dice l'unica cosa che non sappiamo. Sta QUI, in
                  corpo 13px nel blocco di testo sopra al prezzo, e non dentro
                  OrigineBadge: il pill e' text-[10px] + truncate e su telefono
                  taglierebbe proprio la coda, cioe' la parte che informa.
                  Niente conto alla rovescia e niente "ultimi giorni": una data
                  che non abbiamo non si inventa. */}
              {senzaTermine && (
                <p className="flex items-start gap-1">
                  <Info size={13} className="mt-0.5 shrink-0" />
                  <span>{NOTA_SENZA_TERMINE}</span>
                </p>
              )}
              {condizione && (
                <p className="flex items-start gap-1 font-medium text-stone-700">
                  <CreditCard size={13} className="mt-0.5 shrink-0" />
                  <span>Questo prezzo vale solo {condizione}</span>
                </p>
              )}
              {!result.is_online && dove}
            </div>
          )}
        </div>

        <div className="flex items-end gap-2 mt-0.5 flex-wrap">
          {/* prezzo eroe: euro grande, centesimi piccoli */}
          <span className="text-deep font-extrabold tnum leading-none flex items-start">
            <span className="text-[15px] mt-0.5 mr-0.5">â‚¬</span>
            <span className="text-price">{eur}</span>
            <span className="text-[16px] mt-0.5">,{cent}</span>
          </span>
          {sconto && (
            <span className="text-sm line-through text-stone-400 tnum mb-0.5">
              â‚¬{sconto.barrato.toFixed(2)}
            </span>
          )}
          {/* "-X% vs media" non e' uno sconto della catena: e' il confronto
              col set di prezzi che l'utente ha davanti, e su un prezzo
              OTTENIBILE resta un'informazione vera (per questo non e' legato
              a scontoAffermabile). Su un'offerta finita no: nella stessa card che
              dice in rosso "Offerta finita: oggi in negozio paghi il listino"
              il pill verde presentava quel prezzo come conveniente, e il verde
              e' il colore del risparmio. Riguarda 34.922 righe di volantino
              con barrato e scadenza passata, piu' 19.356 senza barrato. */}
          {deltaPct && deltaPct >= 1 && (
            <span className="mb-0.5 inline-flex items-center gap-0.5 text-[11px] font-bold text-success bg-primary-50 px-1.5 py-0.5 rounded-pill">
              <TrendingDown size={11} /> -{deltaPct}% vs media
            </span>
          )}
        </div>

        {/* riga badge + CTA */}
        <div className="flex items-center gap-1.5 mt-1 flex-wrap">
          {result.price_per_unit != null && (
            <span className="text-[11px] text-stone-500 tnum">
              â‚¬{result.price_per_unit.toFixed(2).replace(".", ",")}/{perUnitSuffix(unit)}
            </span>
          )}
          {/* promo_label e' testo scrapato, e il suo TESTO afferma uno sconto
              anche quando il pill e' grigio: misurato sulle righe servite,
              1.348.106 label di categoria "negozio" e 112.291 di categoria
              "online" contengono una percentuale ("Offerta -14%", "PROMO CARD
              -27%") su righe che non hanno nessuna promozione viva - e 1.368
              di quelle "PROMO CARD -27%" non hanno nemmeno il barrato. Un pill
              grigio che dice "-27%" accanto a "prezzo spesa online" e'
              un'affermazione di sconto in tono dimesso, non un'informazione
              neutra: si mostra solo con una promozione viva, e allora e'
              arancione come il resto del risparmio.
              La condizione della carta fedelta', che prima passava solo da
              qui, ora ha la sua riga leggibile sopra al prezzo. */}
          {affermabile && result.promo_label && (
            <span className="inline-flex max-w-full items-center gap-1 rounded-pill bg-accent-50 px-2 py-0.5 text-[11px] font-semibold text-accent">
              <Tag size={11} className="shrink-0" />{" "}
              <span className="truncate">{result.promo_label}</span>
            </span>
          )}
          {sconto && (
            <span className="text-[11px] bg-accent text-white font-semibold px-2 py-0.5 rounded-pill">
              Risparmi â‚¬{sconto.risparmio}
            </span>
          )}
          {result.has_delivery && (
            <Truck size={13} className="text-blue-600" aria-label="Consegna" />
          )}

          {result.shop_url && (
            <a
              href={outbound(result.shop_url, result.chain_slug)}
              target="_blank"
              rel="noopener noreferrer"
              className={`ml-auto inline-flex items-center gap-1 text-[13px] px-3 py-1.5 rounded-btn font-semibold transition active:scale-95 ${
                unavailable
                  ? "bg-stone-200 text-stone-700 hover:bg-stone-300"
                  : isBest
                  ? "bg-primary text-white hover:bg-primary-700"
                  : "bg-stone-900 text-white hover:bg-stone-700"
              }`}
            >
              {unavailable ? "Verifica" : "Acquista"} <ArrowUpRight size={15} />
            </a>
          )}
          {freshness && !result.shop_url && (
            <span className={`ml-auto inline-flex items-center gap-1 text-[11px] ${freshness.tone}`}>
              <Clock size={11} /> {freshness.label}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
