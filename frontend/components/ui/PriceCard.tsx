import { PriceResult } from "@/lib/api";
import {
  Truck,
  Store,
  Tag,
  Globe,
  Clock,
  CheckCircle2,
  ArrowUpRight,
} from "lucide-react";

interface Props {
  result: PriceResult;
  rank: number;
}

// Colore del chip-catena (brand) — testo neutro, sfondo tenue.
const CHAIN_COLORS: Record<string, string> = {
  esselunga: "bg-blue-100 text-blue-800",
  conad: "bg-red-100 text-red-800",
  carrefour: "bg-orange-100 text-orange-800",
  coop: "bg-teal-100 text-teal-800",
  lidl: "bg-yellow-100 text-yellow-800",
  pam: "bg-purple-100 text-purple-800",
  famila: "bg-rose-100 text-rose-800",
  ilgigante: "bg-amber-100 text-amber-800",
  italmark: "bg-cyan-100 text-cyan-800",
};

export default function PriceCard({ result, rank }: Props) {
  const isBest = rank === 0;
  const chainColor =
    CHAIN_COLORS[result.chain_slug] ?? "bg-stone-100 text-stone-700";
  const savings =
    result.original_price && result.original_price > result.price
      ? (result.original_price - result.price).toFixed(2)
      : null;

  const freshness = (() => {
    if (!result.scraped_at) return null;
    const h = Math.floor((Date.now() - new Date(result.scraped_at).getTime()) / 3_600_000);
    if (h < 1) return { label: "Aggiornato ora", tone: "text-success" };
    if (h < 24) return { label: `${h}h fa`, tone: "text-stone-400" };
    return { label: `${Math.floor(h / 24)}g fa`, tone: "text-warning" };
  })();

  return (
    <div
      className={`relative rounded-card border p-4 flex flex-col gap-2 shadow-card transition active:scale-[0.99] ${
        isBest
          ? "border-primary/30 ring-1 ring-primary/30 bg-primary-50"
          : "border-stone-200 bg-white"
      }`}
    >
      {/* barra convenienza (rank) a sinistra */}
      <span
        className={`absolute left-0 top-3 bottom-3 w-1 rounded-full ${
          rank === 0
            ? "bg-success"
            : rank === 1
            ? "bg-lime-400"
            : rank === 2
            ? "bg-warning"
            : "bg-stone-200"
        }`}
        aria-hidden
      />

      <div className="flex items-start justify-between gap-3 pl-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-pill ${chainColor}`}>
              {result.chain_name}
            </span>
            {isBest && (
              <span className="inline-flex items-center gap-0.5 text-[11px] font-bold text-primary">
                <CheckCircle2 size={13} /> Miglior prezzo
              </span>
            )}
          </div>
          <p className="text-sm font-medium text-stone-900 mt-1.5">{result.store_name}</p>
          <p className="text-[13px] text-stone-500 flex items-center gap-1">
            {result.is_online ? (
              <>
                <Globe size={13} /> Spesa online · consegna in tutta Italia
              </>
            ) : (
              <>
                <Store size={13} /> {result.address} · {result.distance_km} km
              </>
            )}
          </p>
        </div>

        <div className="text-right shrink-0">
          <p className="text-[28px] leading-8 font-bold text-deep tnum">
            €{result.price.toFixed(2)}
          </p>
          {result.original_price && (
            <p className="text-sm line-through text-stone-400 tnum">
              €{result.original_price.toFixed(2)}
            </p>
          )}
          {result.price_per_unit && (
            <p className="text-[11px] text-stone-500 tnum">
              €{result.price_per_unit.toFixed(2)}/kg
            </p>
          )}
        </div>
      </div>

      {(result.promo_label || savings) && (
        <div className="flex flex-wrap gap-1.5 pl-2">
          {result.promo_label && (
            <span className="inline-flex items-center gap-1 text-[11px] bg-accent-50 text-accent font-semibold px-2 py-0.5 rounded-pill">
              <Tag size={12} /> {result.promo_label}
            </span>
          )}
          {savings && (
            <span className="text-[11px] bg-accent text-white font-semibold px-2 py-0.5 rounded-pill">
              Risparmi €{savings}
            </span>
          )}
        </div>
      )}

      <div className="flex items-center gap-2 mt-1 flex-wrap pl-2">
        {result.has_delivery && (
          <span className="inline-flex items-center gap-1 text-[11px] bg-blue-50 text-blue-700 px-2 py-0.5 rounded-pill">
            <Truck size={12} /> Consegna
          </span>
        )}
        {result.has_click_collect && (
          <span className="inline-flex items-center gap-1 text-[11px] bg-purple-50 text-purple-700 px-2 py-0.5 rounded-pill">
            <Store size={12} /> Click &amp; Collect
          </span>
        )}
        {result.shop_url && (
          <a
            href={result.shop_url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto inline-flex items-center gap-1 text-[13px] bg-primary text-white px-3 py-1.5 rounded-btn font-semibold hover:bg-primary-700 transition active:scale-95"
          >
            Acquista <ArrowUpRight size={15} />
          </a>
        )}
        {freshness && (
          <span className={`inline-flex items-center gap-1 text-[11px] ${freshness.tone} ${result.shop_url ? "" : "ml-auto"}`}>
            <Clock size={11} /> {freshness.label}
          </span>
        )}
      </div>
    </div>
  );
}
