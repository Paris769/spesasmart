"use client";
import { useMemo, useState } from "react";
import { QuickOptimizeResult, outbound } from "@/lib/api";
import {
  ShoppingBag,
  ExternalLink,
  Check,
  Store as StoreIcon,
  Split,
  ArrowRight,
  ShieldCheck,
} from "lucide-react";

/**
 * Assistente acquisto (modello "handoff"):
 * dato il risultato dell'ottimizzatore, costruisce un PIANO D'ACQUISTO guidato.
 * L'utente sceglie "tutto da un negozio" oppure lo split multi-negozio, poi
 * apre i prodotti sui siti UFFICIALI (dove è già loggato), spunta cosa ha
 * aggiunto e va al checkout. L'app non gestisce mai credenziali né pagamenti.
 */

type PlanStore = {
  key: string;
  chain_name: string;
  chain_slug?: string | null;
  shop_url: string | null;
  total: number;
  items: {
    key: string;
    label: string; // testo voce (query)
    product_name: string;
    price: number;
    quantity: number;
    product_url: string | null;
  }[];
};

function buildPlans(result: QuickOptimizeResult) {
  const single: PlanStore[] = result.best_single
    ? [
        {
          key: "single-" + result.best_single.store_id,
          chain_name: result.best_single.chain_name,
          chain_slug: result.best_single.chain_slug,
          shop_url: result.best_single.shop_url,
          total: result.best_single.total,
          items: result.best_single.items.map((it, i) => ({
            key: `s-${i}`,
            label: it.query,
            product_name: it.product_name,
            price: it.price,
            quantity: it.quantity,
            product_url: it.product_url,
          })),
        },
      ]
    : [];

  const multi: PlanStore[] = (result.multi_store?.stores || []).map((s, si) => ({
    key: "multi-" + s.store_id,
    chain_name: s.chain_name,
    shop_url: s.shop_url,
    total: s.subtotal,
    items: s.items.map((it, i) => ({
      key: `m-${si}-${i}`,
      label: it.query,
      product_name: it.product_name,
      price: it.price,
      quantity: it.quantity,
      product_url: it.product_url,
    })),
  }));

  return { single, multi };
}

export default function PurchasePlan({ result }: { result: QuickOptimizeResult }) {
  const { single, multi } = useMemo(() => buildPlans(result), [result]);
  const hasMulti = (result.multi_store?.savings_vs_single || 0) > 0 && multi.length > 0;

  const [mode, setMode] = useState<"single" | "multi" | null>(null);
  const [done, setDone] = useState<Set<string>>(new Set());

  if (!single.length) return null;

  const stores = mode === "single" ? single : mode === "multi" ? multi : [];
  const allItems = stores.flatMap((s) => s.items);
  const doneCount = allItems.filter((it) => done.has(it.key)).length;
  const progress = allItems.length ? Math.round((doneCount / allItems.length) * 100) : 0;

  const toggle = (k: string) =>
    setDone((prev) => {
      const n = new Set(prev);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });

  return (
    <div className="rounded-card border border-stone-200 bg-white shadow-card p-4 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-full bg-primary-50 grid place-items-center">
          <ShoppingBag size={17} className="text-primary" />
        </div>
        <div>
          <p className="font-bold text-deep leading-tight">Assistente acquisto</p>
          <p className="text-[12px] text-stone-500">
            Apri i prodotti, aggiungili e paga sul sito ufficiale
          </p>
        </div>
      </div>

      {/* Scelta strategia */}
      {mode === null && (
        <div className="flex flex-col gap-2">
          <button
            onClick={() => setMode("single")}
            className="text-left rounded-btn border border-primary/30 bg-primary-50 p-3 flex items-center gap-3 active:scale-[0.99] transition"
          >
            <StoreIcon size={20} className="text-primary shrink-0" />
            <div className="flex-1">
              <p className="font-semibold text-deep text-sm">
                Tutto da {single[0].chain_name}
              </p>
              <p className="text-[12px] text-stone-500">Un solo negozio, più comodo</p>
            </div>
            <span className="font-bold text-deep tnum">€{single[0].total.toFixed(2)}</span>
          </button>

          {hasMulti && (
            <button
              onClick={() => setMode("multi")}
              className="text-left rounded-btn border border-blue-300 bg-blue-50 p-3 flex items-center gap-3 active:scale-[0.99] transition"
            >
              <Split size={20} className="text-blue-600 shrink-0" />
              <div className="flex-1">
                <p className="font-semibold text-deep text-sm">
                  Dividi su {multi.length} negozi
                </p>
                <p className="text-[12px] text-accent font-medium">
                  Risparmi €{result.multi_store.savings_vs_single.toFixed(2)}
                </p>
              </div>
              <span className="font-bold text-deep tnum">
                €{result.multi_store.total.toFixed(2)}
              </span>
            </button>
          )}
        </div>
      )}

      {/* Piano guidato */}
      {mode !== null && (
        <div className="flex flex-col gap-3">
          {/* progress */}
          <div>
            <div className="flex items-center justify-between text-[12px] text-stone-500 mb-1">
              <span>
                {doneCount}/{allItems.length} aggiunti al carrello
              </span>
              <button
                onClick={() => {
                  setMode(null);
                  setDone(new Set());
                }}
                className="text-primary font-medium"
              >
                Cambia
              </button>
            </div>
            <div className="h-2 rounded-pill bg-stone-200 overflow-hidden">
              <div
                className="h-full bg-primary transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>

          {stores.map((s) => (
            <div key={s.key} className="rounded-btn border border-stone-200 overflow-hidden">
              <div className="flex items-center justify-between px-3 py-2 bg-surface">
                <span className="text-sm font-semibold text-deep">{s.chain_name}</span>
                <span className="text-sm font-bold tnum">€{s.total.toFixed(2)}</span>
              </div>
              <ul className="divide-y divide-stone-100">
                {s.items.map((it) => {
                  const isDone = done.has(it.key);
                  return (
                    <li key={it.key} className="flex items-center gap-2 px-3 py-2">
                      <button
                        onClick={() => toggle(it.key)}
                        aria-label="Segna aggiunto"
                        className={`w-6 h-6 rounded-md border-2 grid place-items-center shrink-0 transition ${
                          isDone
                            ? "bg-primary border-primary text-white"
                            : "border-stone-300 text-transparent"
                        }`}
                      >
                        <Check size={15} strokeWidth={3} />
                      </button>
                      <div className="flex-1 min-w-0">
                        <p
                          className={`text-sm leading-snug ${
                            isDone ? "line-through text-stone-400" : "text-stone-800"
                          }`}
                        >
                          {it.product_name}
                        </p>
                        <p className="text-[11px] text-stone-400">{it.label}</p>
                      </div>
                      <span className="text-sm font-semibold tnum shrink-0">
                        €{it.price.toFixed(2)}
                      </span>
                      {it.product_url ? (
                        <a
                          href={outbound(it.product_url, s.chain_slug)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="shrink-0 inline-flex items-center gap-1 text-[12px] bg-primary text-white px-2.5 py-1 rounded-btn font-medium active:scale-95"
                        >
                          Apri <ExternalLink size={12} />
                        </a>
                      ) : (
                        s.shop_url && (
                          <a
                            href={outbound(s.shop_url, s.chain_slug)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="shrink-0 text-[12px] text-stone-500 underline"
                          >
                            Cerca
                          </a>
                        )
                      )}
                    </li>
                  );
                })}
              </ul>
              {s.shop_url && (
                <a
                  href={outbound(s.shop_url, s.chain_slug)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-center gap-1.5 bg-primary text-white text-sm font-semibold py-2.5 hover:bg-primary-700 transition active:scale-[0.99]"
                >
                  Vai al carrello di {s.chain_name} <ArrowRight size={15} />
                </a>
              )}
            </div>
          ))}

          <p className="flex items-start gap-1.5 text-[11px] text-stone-400">
            <ShieldCheck size={13} className="shrink-0 mt-0.5 text-primary" />
            Paghi sul sito ufficiale del supermercato. SpesaSmart non vede né conserva
            le tue credenziali o i dati di pagamento.
          </p>
        </div>
      )}
    </div>
  );
}
