"use client";
import { PriceResult, outbound } from "@/lib/api";
import { useCountUp } from "@/lib/useCountUp";
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

  const savings =
    result.original_price && result.original_price > result.price
      ? (result.original_price - result.price).toFixed(2)
      : null;

  const deltaPct =
    avgPrice && avgPrice > result.price
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
          ) : result.is_online ? (
            <p className="flex items-center gap-1">
              <Globe size={13} /> Spesa online - tutta Italia
            </p>
          ) : hasMultipleLocations ? (
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
          )}
        </div>

        <div className="flex items-end gap-2 mt-0.5 flex-wrap">
          {/* prezzo eroe: euro grande, centesimi piccoli */}
          <span className="text-deep font-extrabold tnum leading-none flex items-start">
            <span className="text-[15px] mt-0.5 mr-0.5">â‚¬</span>
            <span className="text-price">{eur}</span>
            <span className="text-[16px] mt-0.5">,{cent}</span>
          </span>
          {result.original_price && (
            <span className="text-sm line-through text-stone-400 tnum mb-0.5">
              â‚¬{result.original_price.toFixed(2)}
            </span>
          )}
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
          {result.promo_label && (
            <span className="inline-flex items-center gap-1 text-[11px] bg-accent-50 text-accent font-semibold px-2 py-0.5 rounded-pill">
              <Tag size={11} /> {result.promo_label}
            </span>
          )}
          {savings && (
            <span className="text-[11px] bg-accent text-white font-semibold px-2 py-0.5 rounded-pill">
              Risparmi â‚¬{savings}
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
