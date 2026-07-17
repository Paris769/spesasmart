"use client";
import { useEffect, useState } from "react";
import { AlertTriangle, BadgePercent, TrendingDown } from "lucide-react";
import { getPromoCheck, PromoCheckRow } from "@/lib/api";

// Isola client per la pagina prodotto: verifica se un'offerta e reale
// confrontando il prezzo corrente con la mediana degli ultimi 60 giorni.
// Se l'endpoint /promo non esiste o non ha storia sufficiente, non mostra nulla.

interface Props {
  productId: string;
}

function fmtEur(value: number | null | undefined) {
  return typeof value === "number" ? `EUR ${value.toFixed(2)}` : null;
}

function verdictView(row: PromoCheckRow) {
  const pct =
    typeof row.discount_pct === "number" ? Math.round(Math.abs(row.discount_pct)) : null;
  if (row.verdict === "true_promo") {
    return {
      wrap: "border-green-200 bg-green-50",
      badge: "text-green-700 bg-green-100",
      Icon: TrendingDown,
      text: `sconto vero${pct != null ? ` -${pct}%` : ""} vs prezzo abituale 60gg`,
    };
  }
  if (row.verdict === "weak_promo") {
    return {
      wrap: "border-amber-200 bg-amber-50",
      badge: "text-amber-800 bg-amber-100",
      Icon: BadgePercent,
      text: `sconto debole${pct != null ? ` -${pct}%` : ""} vs prezzo abituale 60gg`,
    };
  }
  return {
    wrap: "border-red-200 bg-red-50",
    badge: "text-red-700 bg-red-100",
    Icon: AlertTriangle,
    text: "attenzione: prezzo NON sotto la media abituale",
  };
}

export default function PromoCheck({ productId }: Props) {
  const [checks, setChecks] = useState<PromoCheckRow[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    getPromoCheck(productId)
      .then((res) => {
        if (cancelled) return;
        setChecks((res.checks || []).filter((c) => c.verdict !== "insufficient_history"));
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
      </p>
    </section>
  );
}
