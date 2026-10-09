"use client";

import {
  AlertTriangle,
  ArrowRight,
  BadgePercent,
  CheckCircle2,
  MapPin,
  PackageOpen,
  ShoppingBasket,
  Tag,
} from "lucide-react";
import { RebuildItem, RebuildResultData, RebuildSummary } from "@/lib/api";

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

function isOnOffer(item: RebuildItem): boolean {
  const kind = item.match_kind || "";
  if (typeof kind === "string" && kind.endsWith("_on_offer")) return true;
  const discount = num(item.chosen?.discount_pct);
  return discount !== null && discount > 0;
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
  const totalWithoutOffers = items.reduce((acc, it) => {
    const full = num(it.chosen?.original_price) ?? num(it.chosen?.price) ?? num(it.original?.price);
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
                  {eur(chosen.original_price) && (
                    <p className="tnum text-[10px] text-stone-400 line-through">
                      {eur(chosen.original_price)}
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
          {discount !== null && discount > 0 && (
            <Badge tone="accent">
              <BadgePercent size={10} /> -{Math.round(discount)}%
            </Badge>
          )}
          {saving !== null && saving > 0.009 && (
            <Badge tone="green">risparmi {eur(saving)}</Badge>
          )}
          {saving !== null && saving < -0.009 && (
            <Badge tone="neutral">costa {eur(Math.abs(saving))} in piu&apos;</Badge>
          )}
          {chosen?.promo_label && <Badge tone="neutral">{chosen.promo_label}</Badge>}
          {chosen?.promo_expires && (
            <Badge tone="neutral">fino al {formatDay(chosen.promo_expires)}</Badge>
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
