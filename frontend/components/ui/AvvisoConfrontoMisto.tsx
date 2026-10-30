import { Info } from "lucide-react";
import { AVVISO_CONFRONTO_MISTO } from "@/lib/priceOrigin";

// Avviso da mostrare quando il confronto mette insieme prezzi della spesa
// online e prezzi di negozio/volantino. Stesso blocco ambra di
// ChainCoverageBanner: e' gia' il linguaggio dell'app per "attenzione, leggi".
// Visibile ma non allarmante: nessun rosso, nessun punto esclamativo.
//
// Va messo in UN solo posto per pagina (vedi app/page.tsx e /offerte),
// altrimenti la stessa frase compare tre volte nella stessa schermata.
//
// Va mostrato SOLO quando il confronto e' davvero misto, e sull'insieme che
// l'utente vede in quel punto: un avviso sempre presente diventa invisibile e
// non protegge nessuno. Chi chiama decide la condizione (confrontoMisto sulle
// righe mostrate, oppure il campo `avviso` del backend quando lo calcola
// sull'insieme reso).
export default function AvvisoConfrontoMisto({ testo }: { testo?: string | null }) {
  return (
    <div className="rounded-card border border-amber-200 bg-amber-50 px-3 py-2 flex gap-2 text-xs text-amber-900">
      <Info size={16} className="mt-0.5 shrink-0" />
      <p>{testo || AVVISO_CONFRONTO_MISTO}</p>
    </div>
  );
}
