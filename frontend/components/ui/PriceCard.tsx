import { PriceResult } from "@/lib/api";

interface Props {
  result: PriceResult;
  rank: number;
}

const CHAIN_COLORS: Record<string, string> = {
  esselunga: "bg-blue-100 text-blue-800",
  conad:     "bg-red-100 text-red-800",
  carrefour: "bg-orange-100 text-orange-800",
  coop:      "bg-teal-100 text-teal-800",
  lidl:      "bg-yellow-100 text-yellow-800",
  pam:       "bg-purple-100 text-purple-800",
};

export default function PriceCard({ result, rank }: Props) {
  const isBest = rank === 0;
  const chainColor = CHAIN_COLORS[result.chain_slug] ?? "bg-gray-100 text-gray-800";
  const savings =
    result.original_price && result.original_price > result.price
      ? (result.original_price - result.price).toFixed(2)
      : null;

  const freshness = (() => {
    if (!result.scraped_at) return null;
    const diff = Date.now() - new Date(result.scraped_at).getTime();
    const h = Math.floor(diff / 3600000);
    return h < 1 ? "Aggiornato ora" : `${h}h fa`;
  })();

  return (
    <div
      className={`rounded-xl border p-4 flex flex-col gap-2 transition shadow-sm ${
        isBest ? "border-primary ring-2 ring-primary/20 bg-green-50" : "bg-white"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${chainColor}`}>
            {result.chain_name}
          </span>
          {isBest && (
            <span className="ml-1 text-xs font-bold text-primary">✓ Miglior prezzo</span>
          )}
        </div>
        <div className="text-right">
          <p className="text-2xl font-bold text-gray-900">
            €{result.price.toFixed(2)}
          </p>
          {result.original_price && (
            <p className="text-sm line-through text-gray-400">
              €{result.original_price.toFixed(2)}
            </p>
          )}
          {result.price_per_unit && (
            <p className="text-xs text-gray-500">
              €{result.price_per_unit.toFixed(2)}/kg
            </p>
          )}
        </div>
      </div>

      <p className="text-sm font-medium text-gray-700">{result.store_name}</p>
      <p className="text-xs text-gray-500">{result.address} · {result.distance_km} km</p>

      {result.promo_label && (
        <span className="text-xs bg-secondary/10 text-secondary font-semibold px-2 py-0.5 rounded">
          🏷 {result.promo_label}
        </span>
      )}

      {savings && (
        <span className="text-xs text-green-700 font-medium">
          Risparmi €{savings}
        </span>
      )}

      <div className="flex items-center gap-2 mt-1 flex-wrap">
        {result.has_delivery && (
          <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded">
            🚚 Consegna a casa
          </span>
        )}
        {result.has_click_collect && (
          <span className="text-xs bg-purple-50 text-purple-700 px-2 py-0.5 rounded">
            🏪 Click & Collect
          </span>
        )}
        {result.shop_url && (
          <a
            href={result.shop_url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto text-xs bg-primary text-white px-3 py-1 rounded-lg hover:bg-green-700 transition"
          >
            Acquista →
          </a>
        )}
        {freshness && <span className="text-xs text-gray-400 ml-auto">{freshness}</span>}
      </div>
    </div>
  );
}
