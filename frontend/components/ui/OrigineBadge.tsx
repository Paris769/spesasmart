import { CalendarX, Globe, Newspaper, Store, UserRound } from "lucide-react";
import { Origine, etichettaOrigine } from "@/lib/priceOrigin";

// Etichetta di provenienza accanto al prezzo: informazione di servizio, non
// un richiamo. Piu' piccola e piu' tenue del prezzo, nello stesso pill gia'
// usato in PriceCard e OfferCard (text-[10px] + icona lucide size 11).
//
// Il colore da solo non basta: l'icona e il testo dicono la stessa cosa,
// cosi' l'etichetta si legge anche senza distinguere ambra da grigio.
// L'arancio (accent) resta riservato a sconti e risparmio: una provenienza
// non e' un'offerta.
//
// Il pill e' `max-w-full` + `truncate`: su schermo di telefono l'etichetta si
// accorcia invece di spingere il prezzo fuori dalla sua riga. L'app si usa in
// negozio, con una mano: il numero deve restare dov'e'.
const TONO: Record<Origine, string> = {
  volantino: "text-amber-800 bg-amber-100",
  negozio: "text-stone-600 bg-stone-200",
  online: "text-stone-600 bg-stone-100",
  // Offerta finita: non e' cautela, e' un prezzo che non puoi piu' avere.
  scaduta: "text-red-700 bg-red-100",
  // Segnalato da un altro utente: non e' un dato scrapato dalla catena, non
  // va presentato con la stessa autorita'.
  segnalato: "text-amber-800 bg-amber-50",
  sconosciuta: "",
};

const ICONA = {
  volantino: Newspaper,
  negozio: Store,
  online: Globe,
  scaduta: CalendarX,
  segnalato: UserRound,
} as const;

interface Props {
  /** Origine dichiarata dal backend. null (campo assente) = nessun badge. */
  origine: Origine | null;
  /** Testo pronto dal backend (`origine_label`, es. "offerta volantino,
   *  fino al 15/11"). Senza, ripiega sull'etichetta breve. */
  label?: string | null;
}

export default function OrigineBadge({ origine, label }: Props) {
  if (!origine || origine === "sconosciuta") return null;
  const testo = label || etichettaOrigine(origine);
  if (!testo) return null;
  const Icona = ICONA[origine];

  return (
    <span
      className={`inline-flex max-w-full items-center gap-0.5 text-[10px] font-bold px-1.5 py-0.5 rounded-pill ${TONO[origine]}`}
    >
      <Icona size={11} className="shrink-0" />{" "}
      <span className="truncate">{testo}</span>
    </span>
  );
}
