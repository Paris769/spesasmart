"use client";

import {
  AlertTriangle,
  ArrowRight,
  BadgePercent,
  CheckCircle2,
  CreditCard,
  Info,
  MapPin,
  PackageOpen,
  ShoppingBasket,
  Tag,
} from "lucide-react";
import { RebuildItem, RebuildResultData, RebuildSummary } from "@/lib/api";
import {
  NOTA_SENZA_TERMINE_BREVE,
  condizioneDichiarata,
  etichettaOrigine,
  parseOrigine,
  scontoAffermabile,
  separaNotaSenzaTermine,
} from "@/lib/priceOrigin";

/**
 * Rendering delle sostituzioni di "rifai la spesa con le offerte".
 * Usato sia dal box assistente (quando la risposta contiene `rebuild`) sia
 * dalla pagina /spese (chiamata diretta a /rebuild/from-items).
 *
 * DIFENSIVO per contratto: il backend si deploya separatamente, quindi ogni
 * campo puo' mancare o arrivare null. Nessun accesso non protetto, e il
 * riepilogo viene ricalcolato dagli item quando il backend non lo manda.
 */

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function eur(value: unknown): string | null {
  const n = num(value);
  return n === null ? null : `EUR ${n.toFixed(2)}`;
}

/** "2026-10-15" -> "15/10/2026"; lascia il valore originale se non e' una data ISO. */
function formatDay(value?: string | null): string {
  const parts = (value || "").slice(0, 10).split("-");
  return parts.length === 3 && parts[0].length === 4
    ? `${parts[2]}/${parts[1]}/${parts[0]}`
    : (value || "");
}

/** Accordo singolare/plurale: "1 marca cambiata" invece di "1 marche cambiate". */
function plural(count: number | null | undefined, one: string, many: string): string {
  return count === 1 ? one : many;
}

/** Sconto affermabile sulla voce scelta: la stessa condizione di PriceCard e
 *  OfferCard, dichiarata dal backend e non ricalcolata qui, perche' su questa
 *  superficie il risparmio si SOMMA e finisce nell'intestazione.
 *  Vedi scontoAffermabile() in lib/priceOrigin. */
function scontoItem(item: RebuildItem): boolean {
  return scontoAffermabile(item.chosen);
}

function isOnOffer(item: RebuildItem): boolean {
  const kind = item.match_kind || "";
  if (typeof kind === "string" && kind.endsWith("_on_offer")) return true;
  // discount_pct nasce dal prezzo barrato, che da solo non prova niente: un
  // barrato con una scadenza GIA' PASSATA non e' uno sconto ottenibile. Come
  // ripiego (quando il backend non dichiara match_kind) conta solo se il
  // backend dichiara lo sconto affermabile su quella riga.
  const discount = num(item.chosen?.discount_pct);
  return discount !== null && discount > 0 && scontoItem(item);
}

function hasChoice(item: RebuildItem): boolean {
  const chosen = item.chosen;
  if (!chosen) return false;
  return Boolean(chosen.product_id || chosen.name);
}

const MATCH_LABEL: Record<string, string> = {
  same_product_on_offer: "stesso prodotto in offerta",
  same_brand_on_offer: "stessa marca in offerta",
  similar_on_offer: "prodotto simile in offerta",
  no_offer_same_product: "nessuna offerta: stesso prodotto",
  not_found: "non trovato",
};

function matchLabel(kind?: string | null): string {
  if (!kind) return "sostituzione";
  return MATCH_LABEL[kind] || kind.replace(/_/g, " ");
}

/** Il riepilogo del backend ha la priorita'; i campi mancanti li ricalcolo. */
function buildSummary(items: RebuildItem[], given?: RebuildSummary | null): RebuildSummary {
  const chosenPrices = items.map((it) => num(it.chosen?.price)).filter((p): p is number => p !== null);
  const totalEstimated = chosenPrices.reduce((acc, p) => acc + p, 0);
  // "senza offerte" e' il totale a prezzo pieno, e il prezzo barrato puo'
  // entrarci solo dove il backend dichiara lo sconto affermabile: altrimenti
  // questo ripiego gonfia il "risparmi EUR X" dell'intestazione con barrati di
  // promozioni finite. Senza sconto affermabile il prezzo pieno E' il prezzo
  // scelto, e il risparmio sommato resta zero.
  const totalWithoutOffers = items.reduce((acc, it) => {
    const barrato = scontoItem(it) ? num(it.chosen?.original_price) : null;
    const full = barrato ?? num(it.chosen?.price) ?? num(it.original?.price);
    return acc + (full ?? 0);
  }, 0);

  return {
    items_total: num(given?.items_total) ?? items.length,
    on_offer_count: num(given?.on_offer_count) ?? items.filter(isOnOffer).length,
    brand_kept_count:
      num(given?.brand_kept_count) ?? items.filter((it) => it.brand_kept === true).length,
    brand_changed_count:
      num(given?.brand_changed_count) ??
      items.filter((it) => it.brand_kept === false && hasChoice(it)).length,
    not_found_count:
      num(given?.not_found_count) ??
      items.filter((it) => it.match_kind === "not_found" || !hasChoice(it)).length,
    total_estimated: num(given?.total_estimated) ?? (chosenPrices.length ? totalEstimated : null),
    total_without_offers:
      num(given?.total_without_offers) ?? (totalWithoutOffers > 0 ? totalWithoutOffers : null),
    estimated_saving:
      num(given?.estimated_saving) ??
      (totalWithoutOffers > totalEstimated && chosenPrices.length
        ? totalWithoutOffers - totalEstimated
        : null),
    // L'avviso NON si ricalcola: lo decide il backend sulle voci scelte. Va
    // ricopiato esplicitamente, altrimenti si perde proprio qui, nel punto
    // che rifa' i totali lato client.
    avviso: given?.avviso ?? null,
  };
}

function Badge({
  tone,
  children,
}: {
  tone: "green" | "amber" | "red" | "neutral" | "accent";
  children: React.ReactNode;
}) {
  const tones: Record<string, string> = {
    green: "bg-primary-50 text-primary-700 border-primary/25",
    amber: "bg-amber-50 text-amber-800 border-amber-200",
    red: "bg-red-50 text-red-700 border-red-200",
    neutral: "bg-stone-100 text-stone-600 border-stone-200",
    accent: "bg-accent-50 text-accent-600 border-accent/30",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-pill border px-2 py-0.5 text-[10px] font-bold ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

/** Tono del Badge locale per ciascuna provenienza. Ambra = cautela (offerta a
 *  tempo, prezzo riferito da un utente), rosso = offerta finita, neutro = il
 *  prezzo normale della catena. Il colore non porta mai l'informazione da
 *  solo: il testo dell'etichetta c'e' sempre. */
const TONO_ORIGINE: Record<string, "amber" | "red" | "neutral"> = {
  volantino: "amber",
  segnalato: "amber",
  scaduta: "red",
  negozio: "neutral",
  online: "neutral",
};

function SummaryHeader({ summary }: { summary: RebuildSummary }) {
  const saving = num(summary.estimated_saving);
  const total = num(summary.total_estimated);
  const before = num(summary.total_without_offers);

  return (
    <div className="relative overflow-hidden rounded-2xl bg-hero-grad p-4 text-white shadow-float">
      <div className="absolute inset-0 bg-mesh" aria-hidden />
      <div className="relative flex flex-col gap-2">
        <p className="text-[11px] font-bold uppercase tracking-wide text-white/75">
          Spesa rifatta con le offerte
        </p>
        <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
          {total !== null && (
            <p className="text-price tnum">{eur(total)}</p>
          )}
          {saving !== null && saving > 0.009 && (
            <p className="text-[13px] text-white/90">
              risparmi <strong className="tnum">{eur(saving)}</strong>
              {before !== null ? <span className="text-white/70"> (senza offerte {eur(before)})</span> : null}
            </p>
          )}
        </div>
        <ul className="flex flex-wrap gap-1.5 text-[11px] font-semibold">
          <li className="rounded-pill border border-white/25 bg-white/14 px-2 py-0.5">
            {summary.on_offer_count ?? 0} di {summary.items_total ?? 0}{" "}
            {plural(summary.items_total, "voce", "voci")} in offerta
          </li>
          <li className="rounded-pill border border-white/25 bg-white/14 px-2 py-0.5">
            {summary.brand_kept_count ?? 0}{" "}
            {plural(summary.brand_kept_count, "marca mantenuta", "marche mantenute")}
          </li>
          {(summary.brand_changed_count ?? 0) > 0 && (
            <li className="rounded-pill border border-white/25 bg-white/14 px-2 py-0.5">
              {summary.brand_changed_count}{" "}
              {plural(summary.brand_changed_count, "marca cambiata", "marche cambiate")}
            </li>
          )}
          {(summary.not_found_count ?? 0) > 0 && (
            <li className="rounded-pill border border-white/25 bg-white/14 px-2 py-0.5">
              {summary.not_found_count}{" "}
              {plural(summary.not_found_count, "voce non trovata", "voci non trovate")}
            </li>
          )}
        </ul>
        {/* Il totale e il risparmio qui sopra sono una somma di voci che
            possono avere natura diversa: quei soldi si risparmiano solo
            andando in quei negozi entro la scadenza. Lo dichiara il backend
            sulle voci scelte, qui si rende. */}
        {summary.avviso && (
          <p className="flex gap-1.5 rounded-xl border border-white/30 bg-white/14 px-2.5 py-1.5 text-[11px] text-white/90">
            <Info size={13} className="mt-0.5 shrink-0" />
            <span>{summary.avviso}</span>
          </p>
        )}
      </div>
    </div>
  );
}

function ItemRow({ item, originalLabel }: { item: RebuildItem; originalLabel: string }) {
  const original = item.original || {};
  const chosen = item.chosen || null;
  const picked = hasChoice(item);
  const title = (item.query_name || original.name || chosen?.name || "prodotto").trim();
  const discount = num(chosen?.discount_pct);
  const saving = num(item.saving_vs_original);
  const fakePromo = item.promo_verdict === "fake_promo";
  const brandFrom = (original.brand || "").trim();
  const brandTo = (chosen?.brand || "").trim();

  // Provenienza delle due colonne: `saving_vs_original` e' la differenza fra
  // questi due prezzi, e se le nature non coincidono non e' un risparmio
  // confrontabile. Il backend le dichiara, qui si rendono.
  const origineChosen = parseOrigine(chosen?.origine);
  const origineOriginal = parseOrigine(original.origine);
  // Caso 3: la nota ("termine non indicato") arriva nella coda di
  // origine_label e qui si separa, cosi' il Badge porta la provenienza e il
  // badge breve porta la nota, una volta sola. Prima il Badge stampava
  // labelChosen per intero, nota compresa, col badge breve accanto: la stessa
  // informazione due volte sulla stessa riga, e qui senza nemmeno il
  // troncamento del pill che altrove la giustificava.
  const { label: labelSeparata, senzaTermine } = separaNotaSenzaTermine(
    chosen?.origine_label
  );
  const labelChosen = labelSeparata || etichettaOrigine(origineChosen);
  // Lo sconto lo dichiara il backend, riga per riga: barrato, "-X%" e pill del
  // promo_label. Stessa regola di PriceCard e OfferCard (scontoAffermabile()
  // in lib/priceOrigin), perche' "rifai la spesa con le offerte" e' la
  // superficie che PROPONE di cambiare prodotto per risparmiare: qui il
  // barrato di una promozione finita fa cambiare un acquisto per niente.
  // Prima la condizione era `origine === "volantino"`, cioe' la provenienza
  // scambiata per prova di sconto: sopprimeva anche gli sconti di catalogo
  // senza scadenza, che sulle righe fresche sono il caso normale.
  const affermabile = scontoAffermabile(chosen);
  const barrato = affermabile ? num(chosen?.original_price) : null;
  const prezzoScelto = num(chosen?.price);
  const mostraBarrato =
    barrato !== null && (prezzoScelto === null || barrato > prezzoScelto);
  // Il prezzo di un'offerta finita non si ottiene piu': il risparmio rispetto
  // alla voce precedente sarebbe un conto su un numero che non si puo' pagare.
  const risparmioAffermabile = origineChosen !== "scaduta";
  const condizione = condizioneDichiarata(chosen?.origine_label);
  // Quando l'etichetta di origine porta gia' la data (volantino in corso,
  // offerta scaduta) il badge "fino al ..." sparisce: la stessa data due
  // volte sulla stessa riga si legge come un errore.
  const dataNellaLabel =
    Boolean(chosen?.origine_label) &&
    (origineChosen === "volantino" || origineChosen === "scaduta");
  // "fino al GG/MM/AAAA" e' un termine ANCORA VALIDO: si dice solo dove lo
  // sconto e' affermabile e la categoria e' "volantino". Senza questa
  // condizione il badge stampava `chosen.promo_expires` cosi' com'era: su una
  // riga di listino con promo_expires passata la categoria e' "negozio" (non
  // "scaduta"), quindi dataNellaLabel era false e la riga diceva "fino al
  // 23/08/2026" su una data passata. Nel campione del 30/10/2026 eurospin_web
  // ha 16 righe con scadenza passata su 1.046 campionate.
  const termineDichiarato = affermabile && origineChosen === "volantino";

  return (
    <li className="px-3 py-3 sm:px-4">
      <div className="flex flex-col gap-2">
        <div className="flex items-start justify-between gap-2">
          <p className="min-w-0 flex-1 text-sm font-bold leading-snug text-deep">{title}</p>
          <span className="shrink-0 text-[10px] font-semibold text-stone-400">
            {matchLabel(typeof item.match_kind === "string" ? item.match_kind : null)}
          </span>
        </div>

        {/* riga originale -> scelto */}
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1 rounded-xl bg-surface px-2.5 py-2">
            <p className="text-[10px] font-bold uppercase tracking-wide text-stone-400">
              {originalLabel}
            </p>
            <p className="truncate text-[12px] font-semibold text-stone-700">
              {original.name || title}
            </p>
            <p className="text-[11px] text-stone-500">
              {brandFrom || "marca non indicata"}
              {eur(original.price) ? ` - ${eur(original.price)}` : ""}
            </p>
            {origineOriginal && eur(original.price) && (
              <p className="text-[10px] text-stone-400">
                {etichettaOrigine(origineOriginal)}
              </p>
            )}
          </div>

          <ArrowRight
            size={16}
            className="hidden shrink-0 text-stone-300 sm:block"
            aria-hidden
          />
          <span className="text-[10px] font-bold text-stone-400 sm:hidden" aria-hidden>
            {picked ? "sostituito con" : "nessuna sostituzione"}
          </span>

          <div
            className={`min-w-0 flex-1 rounded-xl border px-2.5 py-2 ${
              picked ? "border-primary/25 bg-primary-50" : "border-amber-200 bg-amber-50"
            }`}
          >
            {picked && chosen ? (
              <div className="flex items-start gap-2">
                {chosen.image_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={chosen.image_url}
                    alt=""
                    className="h-10 w-10 shrink-0 rounded bg-white object-contain"
                  />
                ) : (
                  <span className="grid h-10 w-10 shrink-0 place-items-center rounded bg-white text-primary">
                    <ShoppingBasket size={16} />
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] font-semibold leading-snug text-deep line-clamp-2">
                    {chosen.name || "prodotto in offerta"}
                  </p>
                  <p className="truncate text-[11px] text-stone-600">
                    {[brandTo, chosen.chain_name, chosen.store_name].filter(Boolean).join(" - ") ||
                      "catena non indicata"}
                  </p>
                  {num(chosen.distance_km) !== null && (
                    <p className="flex items-center gap-1 text-[10px] text-stone-500">
                      <MapPin size={10} /> {num(chosen.distance_km)!.toFixed(1)} km
                    </p>
                  )}
                </div>
                <div className="shrink-0 text-right">
                  <p className="tnum text-sm font-extrabold text-primary">{eur(chosen.price)}</p>
                  {mostraBarrato && (
                    <p className="tnum text-[10px] text-stone-400 line-through">
                      {eur(barrato)}
                    </p>
                  )}
                  {num(chosen.price_per_unit) !== null && (
                    <p className="tnum text-[10px] text-stone-500">
                      {eur(chosen.price_per_unit)}/unita
                    </p>
                  )}
                </div>
              </div>
            ) : (
              <div className="flex items-start gap-2">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded bg-white text-amber-600">
                  <PackageOpen size={16} />
                </span>
                <p className="text-[12px] font-semibold leading-snug text-amber-900">
                  Nessun prodotto in offerta trovato: resta da comprare come sempre.
                </p>
              </div>
            )}
          </div>
        </div>

        {/* badge */}
        <div className="flex flex-wrap items-center gap-1.5">
          {picked && item.brand_kept === true && (
            <Badge tone="green">
              <CheckCircle2 size={10} /> stessa marca
            </Badge>
          )}
          {picked && item.brand_kept === false && (
            <Badge tone="amber">
              <Tag size={10} /> marca cambiata: {brandFrom || "n.d."} -&gt; {brandTo || "n.d."}
            </Badge>
          )}
          {affermabile && discount !== null && discount > 0 && (
            <Badge tone="accent">
              <BadgePercent size={10} /> -{Math.round(discount)}%
            </Badge>
          )}
          {risparmioAffermabile && saving !== null && saving > 0.009 && (
            <Badge tone="green">risparmi {eur(saving)}</Badge>
          )}
          {saving !== null && saving < -0.009 && (
            <Badge tone="neutral">costa {eur(Math.abs(saving))} in piu&apos;</Badge>
          )}
          {origineChosen && labelChosen && (
            <Badge tone={TONO_ORIGINE[origineChosen]}>
              {origineChosen === "scaduta" && <AlertTriangle size={10} />}
              {labelChosen}
            </Badge>
          )}
          {affermabile && chosen?.promo_label && (
            <Badge tone="neutral">{chosen.promo_label}</Badge>
          )}
          {condizione && (
            <Badge tone="amber">
              <CreditCard size={10} /> vale solo {condizione}
            </Badge>
          )}
          {chosen?.promo_expires && termineDichiarato && !dataNellaLabel && (
            <Badge tone="neutral">fino al {formatDay(chosen.promo_expires)}</Badge>
          )}
          {/* Caso 3: lo sconto si mostra, il termine non c'e'. Badge neutro,
              non accent: non e' un richiamo, e' la cosa che non sappiamo. */}
          {senzaTermine && (
            <Badge tone="neutral">{NOTA_SENZA_TERMINE_BREVE}</Badge>
          )}
          {fakePromo && (
            <Badge tone="red">
              <AlertTriangle size={10} /> non e&apos; sotto il prezzo abituale
            </Badge>
          )}
        </div>

        {item.note && <p className="text-[11px] text-stone-500">{item.note}</p>}
      </div>
    </li>
  );
}

export default function RebuildResult({
  data,
  title = "Sostituzioni proposte",
  originalLabel = "Nell'ultima spesa",
  footnote,
}: {
  data: RebuildResultData;
  title?: string;
  /** Intestazione della colonna di sinistra: cambia se la spesa non e' l'ultima. */
  originalLabel?: string;
  footnote?: string;
}) {
  const items = Array.isArray(data?.items) ? data.items.filter(Boolean) : [];
  const summary = buildSummary(items, data?.summary);

  if (items.length === 0) {
    return (
      <div className="rounded-card border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-900">
        Non ho ottenuto sostituzioni per questa spesa. Puoi riprovare allargando il
        raggio di ricerca oppure guardare le offerte della settimana.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <SummaryHeader summary={summary} />
      <div className="overflow-hidden rounded-card border border-stone-200 bg-white shadow-card">
        <p className="border-b border-stone-100 bg-surface px-3 py-2 text-[11px] font-bold uppercase tracking-wide text-stone-500 sm:px-4">
          {title}
        </p>
        <ul className="divide-y divide-stone-100">
          {items.map((item, i) => (
            <ItemRow
              key={`${item.chosen?.product_id || item.original?.product_id || item.query_name || "row"}-${i}`}
              item={item}
              originalLabel={originalLabel}
            />
          ))}
        </ul>
      </div>
      <p className="px-1 text-[11px] text-stone-400">
        {footnote ||
          "Prezzi e promozioni come rilevati dalle catene: verifica sempre in negozio o sul sito prima di acquistare."}
      </p>
    </div>
  );
}
