"use client";
import Link from "next/link";
import { NearbyOffer } from "@/lib/api";
import {
  NOTA_SENZA_TERMINE,
  condizioneDichiarata,
  parseOrigine,
  scontoAffermabile,
  separaNotaSenzaTermine,
} from "@/lib/priceOrigin";
import OrigineBadge from "@/components/ui/OrigineBadge";
import {
  CalendarDays,
  CreditCard,
  Globe,
  Info,
  MapPin,
  ShoppingCart,
  Tag,
} from "lucide-react";

// Pallino col colore di brand della catena (come in PriceCard: l'arancio
// resta riservato ai deal).
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

/** "2026-08-15" -> "15 agosto" (null se la data non e' valida). */
function fmtExpiry(iso: string): string | null {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return d.toLocaleDateString("it-IT", { day: "numeric", month: "long" });
}

export default function OfferCard({ offer }: { offer: NearbyOffer }) {
  const dot = CHAIN_DOT[offer.chain_slug] ?? "#6B7280";
  const [eur, cent] = Number(offer.price).toFixed(2).split(".");
  // Provenienza: la dichiara il backend, qui non si classifica niente. Non si
  // guarda `source` (e' il nome tecnico dello spider: e' cosi' che i volantini
  // di MD, Lidl, Aldi e Penny restavano senza etichetta) e non si rifa' il
  // calcolo lato client. Campo assente = nessuna etichetta.
  const origine = parseOrigine(offer.origine);
  // La riga "Spesa online" col mappamondo compare SOLO quando non c'e' una
  // distanza, cioe' per i soli store virtuali del DB. Misurato il 29/10/2026:
  // gli store con external_id '%-online' sono TRE - carrefour-online (19.984
  // righe correnti), esselunga-online (19.675) e conad-online (885). Il
  // commento diceva "i due soli" e nominava Esselunga e Carrefour: e'
  // leggendo questo commento che una lente del collaudo ha concluso il falso.
  // Per tutte le altre catene e-commerce (CosiComodo: Famila/Italmark/Il
  // Gigante, Iper, EasyCoop, Pam a Casa) la card mostra km e indirizzo di un
  // negozio fisico: se qui sopprimessimo il badge, la sorgente piu' grande
  // dell'app resterebbe senza nessuna dichiarazione accanto a un indirizzo.
  // Il badge si omette solo dove la riga sotto dice davvero la stessa cosa.
  const naturaGiaDetta = origine === "online" && offer.distance_km == null;
  // Catalogo online di un negozio FISICO: prezzo "online" con una distanza e
  // un indirizzo accanto. E' il 96,4% delle card di /offerte (misurato sulle
  // righe servite: 55.176 online su 57.213, di cui 50.703 cosicomodo), e il
  // solo badge grigio da 10px non dice la conseguenza - che quel numero e'
  // ordinabile dal catalogo di quel punto vendita e non e' il prezzo di cassa.
  // E' la stessa biforcazione che PriceCard ha nella riga della natura.
  const catalogoDiNegozio = origine === "online" && offer.distance_km != null;
  const scaduta = origine === "scaduta";
  // Una sola definizione di sconto, la stessa di PriceCard, e NON la decide
  // questa card: la dichiara il backend riga per riga (scontoAffermabile() in
  // lib/priceOrigin). Gate di barrato, "-X%" e pill del promo_label.
  //
  // Prima la condizione era `origine === "volantino"`, cioe' la provenienza
  // usata come prova di sconto. Sono due domande diverse: una riga del
  // catalogo online con un barrato dichiarato dalla catena porta uno sconto
  // vero, e sulle righe fresche e' il caso normale (campione 30/10/2026: su
  // cosicomodo 38 righe fresche su 760 hanno barrato senza scadenza, mentre le
  // righe con scadenza FUTURA sono ZERO su tutte le sorgenti). Resta fuori la
  // promozione SCADUTA, che e' il caso da cui nasceva l'affermazione falsa.
  const affermabile = scontoAffermabile(offer);
  const hasStrike =
    affermabile && offer.original_price != null && offer.original_price > offer.price;
  // "fino al GG" si dice solo dove un termine c'e' DAVVERO e vale ancora:
  // sconto affermabile e categoria "volantino". Due casi lo escludevano prima
  // e uno no: su un'offerta finita la data la porta l'etichetta di origine
  // ("offerta scaduta il GG/MM") e il badge sparisce; ma una riga di listino
  // con una promo_expires PASSATA e' di categoria "negozio", non "scaduta"
  // (il backend la legge come residuo di metadati), e la' il badge diceva
  // "fino al 23 agosto" su una data passata. Nel campione del 30/10/2026
  // eurospin_web ha 16 righe cosi' su 1.046.
  const termineDichiarato = affermabile && origine === "volantino";
  const expiry =
    offer.promo_expires && termineDichiarato
      ? fmtExpiry(offer.promo_expires)
      : null;
  // Caso 3: lo sconto c'e', il termine no. Si mostra lo sconto e si dice che
  // la catena non indica fino a quando vale. Lo dichiara il backend nella coda
  // di origine_label e qui si legge da la': ricavarlo dalla categoria lo
  // perdeva proprio sulle sorgenti che SONO un volantino per natura, dove su
  // questa pagina "offerta volantino" fa pensare all'offerta della settimana
  // mentre la data manca perche' non l'abbiamo, non perche' sia passata (iper:
  // 1.890 righe fresche affermabili, 1.764 col barrato e ZERO con una
  // scadenza, misurate il 30/10/2026).
  const { label: etichetta, senzaTermine } = separaNotaSenzaTermine(
    offer.origine_label
  );
  // La condizione dichiarata dal backend nella coda di origine_label (oggi
  // solo la carta fedelta'): qui il pill porta l'etichetta breve, quindi
  // senza questa riga la condizione non arriverebbe affatto.
  const condizione = condizioneDichiarata(offer.origine_label);

  return (
    <Link
      href={`/p/${offer.product_id}`}
      className="relative overflow-hidden rounded-2xl p-3.5 flex gap-3 bg-white border border-stone-200 shadow-card animate-pop-in transition hover:shadow-cardHover active:scale-[0.99]"
    >
      {/* immagine prodotto */}
      <div className="shrink-0 w-[60px] h-[60px] rounded-xl bg-white border border-stone-200 grid place-items-center overflow-hidden">
        {offer.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={offer.image_url}
            alt=""
            className="w-full h-full object-contain"
          />
        ) : (
          <ShoppingCart size={22} className="text-stone-300" />
        )}
      </div>

      {/* corpo */}
      <div className="flex-1 min-w-0 flex flex-col gap-1">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-deep">
            <span className="w-2 h-2 rounded-full" style={{ background: dot }} />
            {offer.chain_name}
          </span>
          {affermabile && offer.discount_pct != null && offer.discount_pct >= 1 && (
            <span className="text-[11px] font-bold text-white bg-accent px-1.5 py-0.5 rounded-pill">
              -{offer.discount_pct}%
            </span>
          )}
          {/* Etichetta breve quando la scadenza ha gia' il suo badge piu'
              sotto; per un'offerta scaduta si mostra invece la label completa
              del backend, che porta la data in cui e' finita. */}
          {!naturaGiaDetta && (
            <OrigineBadge
              origine={origine}
              label={scaduta ? etichetta : null}
            />
          )}
        </div>

        <p className="text-[13px] font-medium text-stone-900 leading-tight line-clamp-2">
          {offer.product_name}
          {offer.brand && (
            <span className="text-stone-500 font-normal"> - {offer.brand}</span>
          )}
        </p>

        <div className="flex items-end gap-2 flex-wrap">
          <span className="text-deep font-extrabold tnum leading-none flex items-start">
            <span className="text-[13px] mt-0.5 mr-0.5">&euro;</span>
            <span className="text-[24px] tracking-tight">{eur}</span>
            <span className="text-[14px] mt-0.5">,{cent}</span>
          </span>
          {hasStrike && (
            <span className="text-sm line-through text-stone-400 tnum mb-0.5">
              &euro;{Number(offer.original_price).toFixed(2)}
            </span>
          )}
          {offer.price_per_unit != null && (
            <span className="text-[11px] text-stone-500 tnum mb-0.5">
              &euro;{Number(offer.price_per_unit).toFixed(2).replace(".", ",")}/u
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5 flex-wrap text-[11px] text-stone-500">
          {offer.distance_km != null ? (
            <span className="inline-flex items-center gap-0.5">
              <MapPin size={11} /> {offer.distance_km} km - {offer.store_name}
            </span>
          ) : (
            <span className="inline-flex items-center gap-0.5">
              <Globe size={11} /> Spesa online
            </span>
          )}
          {expiry && (
            <span className="inline-flex items-center gap-0.5 text-amber-800 bg-amber-50 px-1.5 py-0.5 rounded-pill font-medium">
              <CalendarDays size={11} /> fino al {expiry}
            </span>
          )}
          {/* Come in PriceCard: il TESTO di promo_label afferma uno sconto
              anche in grigio ("PROMO CARD -27%", "Offerta -14%"), quindi lo
              porta solo una promozione viva. Non e' una prova di sconto in
              nessun caso: Carrefour scrive "", Conad "Bassi e Fissi" che e' il
              suo programma di prezzi PERMANENTI, MD il nome della pagina del
              volantino. */}
          {affermabile && offer.promo_label && (
            <span className="inline-flex max-w-[180px] items-center gap-1 rounded-pill bg-accent-50 px-1.5 py-0.5 font-semibold text-accent">
              <Tag size={11} className="shrink-0" />{" "}
              <span className="truncate">{offer.promo_label}</span>
            </span>
          )}
        </div>

        {/* La pagina si intitola "Offerte vicino a te" e chiude con "le
            migliori promozioni entro X km": accanto a "3,1 km - Famila
            Albaredo d'Adige" l'utente ci va col volantino in testa. Se quel
            prezzo e' il catalogo online del punto vendita, lo deve leggere
            qui, in una riga di testo, non dedurlo da un badge grigio da
            10px. */}
        {catalogoDiNegozio && (
          <p className="text-[11px] leading-snug text-stone-500">
            Prezzo del catalogo online di questo punto vendita: in negozio il
            prezzo alla cassa puo&apos; essere diverso.
          </p>
        )}
        {/* Caso 3: in fondo alla card, nello stesso corpo 11px della riga del
            catalogo online. Niente conto alla rovescia e niente data
            inventata: l'unica cosa che manca e' il termine, e lo diciamo. */}
        {senzaTermine && (
          <p className="flex items-start gap-1 text-[11px] leading-snug text-stone-500">
            <Info size={11} className="mt-0.5 shrink-0" />
            <span>{NOTA_SENZA_TERMINE}</span>
          </p>
        )}
        {condizione && (
          <p className="flex items-start gap-1 text-[11px] font-medium leading-snug text-stone-700">
            <CreditCard size={11} className="mt-0.5 shrink-0" />
            <span>Questo prezzo vale solo {condizione}</span>
          </p>
        )}
      </div>
    </Link>
  );
}
