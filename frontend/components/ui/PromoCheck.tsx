"use client";
import { useEffect, useState } from "react";
import { AlertTriangle, BadgePercent, TrendingDown } from "lucide-react";
import { getPromoCheck, PromoCheckRow } from "@/lib/api";
import { parseOrigine, scontoAffermabile } from "@/lib/priceOrigin";
import OrigineBadge from "@/components/ui/OrigineBadge";

// Isola client per la pagina prodotto: verifica se un'offerta e reale
// confrontando il prezzo corrente con la mediana degli ultimi 60 giorni.
// Se l'endpoint /promo non esiste o non ha storia sufficiente, non mostra nulla.

interface Props {
  productId: string;
}

function fmtEur(value: number | null | undefined) {
  return typeof value === "number" ? `EUR ${value.toFixed(2)}` : null;
}

/** I soli verdetti di cui questo componente ha un testo VERO da scrivere.
 *
 *  E' il presidio di questa superficie, ed e' UNO SOLO: una riga si rende se e
 *  solo se passa da qui (vedi `renderizzabile`). Prima erano due meta-presidi
 *  che si annullavano. Il verdetto "promo_non_viva" il backend NON lo produce
 *  piu': services/promo.py scarta la riga su cui lo sconto non e' affermabile
 *  invece di declassarne il verdetto, e quel nome non esiste piu' in nessun
 *  punto di codice eseguibile. Anche quando esisteva non arrivava mai, perche'
 *  il predicato SQL di /promo serve solo righe su cui lo sconto e'
 *  affermabile; e questa funzione non aveva un ramo per quel verdetto, quindi
 *  se fosse arrivato sarebbe caduto nel `return` finale, che dice
 *  "attenzione: prezzo NON sotto la media abituale" — una frase che su una
 *  riga di cui la mediana non e' stata calcolata e' semplicemente falsa.
 *  Un verdetto che non riconosciamo adesso non si rende: non si indovina una
 *  frase, e la sezione non si inventa un giudizio.
 *
 *  Che il presidio sia RAGGIUNGIBILE non dipende dal backend di oggi: scatta
 *  nella finestra fra il deploy di Vercel e quello di Render (dove
 *  `sconto_affermabile` non arriva), e su qualunque verdetto che un backend
 *  piu' nuovo aggiungesse. */
const VERDETTI_RESI = new Set<string>(["true_promo", "weak_promo", "fake_promo"]);

/** Una riga si mostra solo se il backend dichiara lo sconto affermabile E il
 *  verdetto e' uno di quelli che sappiamo scrivere.
 *
 *  Perche' serve anche lo sconto affermabile: questa sezione si intitola "E
 *  una vera offerta?" ed e' per intero un'affermazione di sconto. Senza il
 *  campo (backend non ancora aggiornato) la sezione sparisce invece di
 *  giudicare: e' la regola di compatibilita' del resto del progetto — senza il
 *  dato le affermazioni non si tirano a indovinare. */
function renderizzabile(row: PromoCheckRow): boolean {
  return scontoAffermabile(row) && VERDETTI_RESI.has(String(row.verdict));
}

function verdictView(row: PromoCheckRow) {
  const pct =
    typeof row.discount_pct === "number" ? Math.round(Math.abs(row.discount_pct)) : null;
  const pctTxt = pct != null ? ` -${pct}%` : "";
  // La mediana 60gg nata da prezzi di natura diversa non e' un denominatore
  // omogeneo. Si AGGIUNGE in coda al verdetto, non lo sostituisce: prima
  // stava davanti al dispatch e intercettava anche fake_promo, cioe' le righe
  // in cui il prezzo NON e' sotto l'abituale. La' discount_pct e' <= 0 per
  // definizione (_verdict in promo.py: current_price >= median) e Math.abs
  // toglie il segno, quindi l'utente leggeva "sconto -0% non verificato" dove
  // prima leggeva l'avvertenza rossa: una frase che comincia con "sconto" al
  // posto di quella che lo mette in guardia. Misurate il 30/10/2026: delle 39
  // righe flyer fresche 38 danno fake_promo e 8 hanno mediana_origini_miste.
  // La forma giusta e' quella di scripts/weekly_digest.py, che accoda.
  const miste = row.mediana_origini_miste === true;
  const coda = miste
    ? " - confronto non verificato: il prezzo abituale mette insieme prezzi di natura diversa"
    : "";
  // Qui NON si guarda l'origine, e non e' una dimenticanza.
  //
  // Prima c'erano due rami agganciati alla provenienza, e nessuno dei due
  // reggeva:
  //  - `origine === "scaduta"`: impossibile per costruzione, non solo raro. Il
  //    predicato SQL di /promo (sconto_affermabile_sql in core/price_origin,
  //    invocato da services/promo.py) serve solo righe senza scadenza o con
  //    scadenza da oggi in avanti, e "scaduta" e' esattamente la categoria
  //    delle righe con scadenza PASSATA. Il ramo era morto e dava
  //    l'impressione che il caso fosse presidiato.
  //  - la vecchia prova di sconto ricavata dalla provenienza
  //    (`origine === "volantino"`), che declassava il testo da "sconto" a
  //    "prezzo sotto il suo abituale" dove la provenienza non era "volantino":
  //    con i due assi separati quella non e' piu' la domanda giusta. Una riga
  //    del catalogo online con un barrato dichiarato dalla catena e non
  //    scaduto PORTA uno sconto, e il testo qui e' comunque relativo e
  //    qualificato ("vs prezzo abituale 60gg"): non promette nessuna data.
  //
  // Il termine della promozione, dove non e' indicato, lo dice l'etichetta di
  // origine del backend, che la riga rende sotto il testo del verdetto.
  if (row.verdict === "true_promo") {
    // "sconto vero" e' una certificazione: su un confronto non omogeneo non si
    // da'. Niente spunta verde, tono di cautela, e il motivo scritto accanto.
    if (miste)
      return {
        wrap: "border-amber-200 bg-amber-50",
        badge: "text-amber-800 bg-amber-100",
        Icon: BadgePercent,
        text: `sconto${pctTxt} non verificato: il prezzo abituale 60gg mette insieme prezzi di natura diversa`,
      };
    return {
      wrap: "border-green-200 bg-green-50",
      badge: "text-green-700 bg-green-100",
      Icon: TrendingDown,
      text: `sconto vero${pctTxt} vs prezzo abituale 60gg`,
    };
  }
  if (row.verdict === "weak_promo") {
    return {
      wrap: "border-amber-200 bg-amber-50",
      badge: "text-amber-800 bg-amber-100",
      Icon: BadgePercent,
      text: `sconto debole${pctTxt} vs prezzo abituale 60gg${coda}`,
    };
  }
  // Resta solo "fake_promo": VERDETTI_RESI non ne lascia passare altri, quindi
  // questo non e' un ripiego per verdetti ignoti (era quello il riquadro che
  // diceva il falso) ma l'ultimo dei tre casi.
  return {
    wrap: "border-red-200 bg-red-50",
    badge: "text-red-700 bg-red-100",
    Icon: AlertTriangle,
    text: `attenzione: prezzo NON sotto la media abituale${coda}`,
  };
}

export default function PromoCheck({ productId }: Props) {
  const [checks, setChecks] = useState<PromoCheckRow[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    getPromoCheck(productId)
      .then((res) => {
        if (cancelled) return;
        // Un solo filtro, quello di renderizzabile(): "insufficient_history"
        // non e' in VERDETTI_RESI, quindi cade da se'.
        setChecks((res.checks || []).filter(renderizzabile));
      })
      .catch(() => {
        if (!cancelled) setChecks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [productId]);

  if (!checks || checks.length === 0) return null;

  return (
    <section>
      <h2 className="text-lg font-semibold text-deep mb-2">E una vera offerta?</h2>
      <ul className="flex flex-col gap-2">
        {checks.map((row, i) => {
          const view = verdictView(row);
          const Icon = view.Icon;
          const median = fmtEur(row.median_60d);
          return (
            <li
              key={`${row.store_id}-${i}`}
              className={`rounded-card border p-3 flex items-center gap-3 ${view.wrap}`}
            >
              <span className={`w-8 h-8 rounded-full grid place-items-center shrink-0 ${view.badge}`}>
                <Icon size={16} />
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-bold text-deep">{row.chain_name}</p>
                <p className="text-[12px] text-stone-600">{view.text}</p>
                {median && (
                  <p className="text-[11px] text-stone-400 tnum">
                    ora {fmtEur(row.current_price)} - prezzo abituale {median}
                  </p>
                )}
                {parseOrigine(row.origine) && (
                  <p className="mt-0.5">
                    <OrigineBadge
                      origine={parseOrigine(row.origine)}
                      label={row.origine_label}
                    />
                  </p>
                )}
              </div>
              <span className="text-sm font-extrabold text-deep tnum shrink-0">
                {fmtEur(row.current_price)}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="text-[11px] text-stone-400 mt-1.5">
        Confronto con il prezzo mediano degli ultimi 60 giorni per catena, sui dati raccolti da SpesaSmart.
        {checks.some((c) => c.mediana_origini_miste === true) &&
          " Dove il prezzo abituale nasce da rilevazioni di natura diversa (spesa online, listino di negozio, volantino) il confronto non e' verificato e lo diciamo sulla riga."}
      </p>
    </section>
  );
}
