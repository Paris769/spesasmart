"use client";
import { useMemo, useState } from "react";
import { QuickOptimizeResult, outbound } from "@/lib/api";
import {
  ArrowRight,
  Check,
  ExternalLink,
  LogIn,
  PackageCheck,
  ShieldCheck,
  ShoppingBag,
  Split,
  Store as StoreIcon,
  Truck,
} from "lucide-react";

type FulfillmentMode = "delivery" | "pickup";
type StrategyMode = "single" | "multi";

type PlanStore = {
  key: string;
  chain_name: string;
  chain_slug?: string | null;
  shop_url: string | null;
  has_delivery?: boolean;
  has_click_collect?: boolean;
  total: number;
  items: {
    key: string;
    label: string;
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
          has_delivery: result.best_single.has_delivery,
          has_click_collect: result.best_single.has_click_collect,
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
    chain_slug: s.chain_slug,
    shop_url: s.shop_url,
    has_delivery: s.has_delivery,
    has_click_collect: s.has_click_collect,
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

function serviceLabel(service: FulfillmentMode) {
  return service === "delivery" ? "consegna a casa" : "ritiro in negozio";
}

function supportsService(store: PlanStore, service: FulfillmentMode) {
  return service === "delivery" ? store.has_delivery !== false : store.has_click_collect !== false;
}

export default function PurchasePlan({ result }: { result: QuickOptimizeResult }) {
  const { single, multi } = useMemo(() => buildPlans(result), [result]);
  const hasMulti = (result.multi_store?.savings_vs_single || 0) > 0 && multi.length > 0;

  const [fulfillment, setFulfillment] = useState<FulfillmentMode | null>(null);
  const [mode, setMode] = useState<StrategyMode | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [done, setDone] = useState<Set<string>>(new Set());

  if (!single.length) return null;

  const stores = mode === "single" ? single : mode === "multi" ? multi : [];
  const allItems = stores.flatMap((s) => s.items);
  const doneCount = allItems.filter((it) => done.has(it.key)).length;
  const progress = allItems.length ? Math.round((doneCount / allItems.length) * 100) : 0;
  const unsupportedStores = fulfillment
    ? stores.filter((store) => !supportsService(store, fulfillment))
    : [];

  const toggle = (k: string) =>
    setDone((prev) => {
      const n = new Set(prev);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });

  const resetPlan = () => {
    setMode(null);
    setConfirmed(false);
    setDone(new Set());
  };

  return (
    <div className="rounded-card border-2 border-primary/40 bg-white shadow-best ring-1 ring-primary/10 overflow-hidden flex flex-col">
      <div className="bg-hero-grad text-white px-4 py-3 relative overflow-hidden">
        <div className="absolute inset-0 bg-mesh opacity-60" aria-hidden />
        <div className="relative flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-full bg-white/20 grid place-items-center shrink-0">
            <ShoppingBag size={18} className="text-white" />
          </div>
          <div>
            <p className="font-bold leading-tight flex items-center gap-2">
              Fai la spesa ora
              <span className="text-[10px] font-bold uppercase tracking-wide bg-white/25 px-1.5 py-0.5 rounded-pill">
                guidato
              </span>
            </p>
            <p className="text-[12px] text-white/85">
              Scegli consegna o ritiro, conferma i prodotti e completa sul sito ufficiale
            </p>
          </div>
        </div>
      </div>

      <div className="p-4 flex flex-col gap-3">
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={() => {
              setFulfillment("delivery");
              setConfirmed(false);
            }}
            className={`text-left rounded-btn border p-3 flex items-center gap-2 transition active:scale-[0.99] ${
              fulfillment === "delivery"
                ? "border-primary bg-primary-50 text-deep"
                : "border-stone-200 bg-white text-stone-600"
            }`}
          >
            <Truck size={18} className="text-primary shrink-0" />
            <span className="text-sm font-semibold">Consegna a casa</span>
          </button>
          <button
            onClick={() => {
              setFulfillment("pickup");
              setConfirmed(false);
            }}
            className={`text-left rounded-btn border p-3 flex items-center gap-2 transition active:scale-[0.99] ${
              fulfillment === "pickup"
                ? "border-primary bg-primary-50 text-deep"
                : "border-stone-200 bg-white text-stone-600"
            }`}
          >
            <PackageCheck size={18} className="text-primary shrink-0" />
            <span className="text-sm font-semibold">Ritiro in negozio</span>
          </button>
        </div>

        {fulfillment && mode === null && (
          <div className="flex flex-col gap-2">
            <button
              onClick={() => {
                setMode("single");
                setConfirmed(false);
              }}
              className="text-left rounded-btn border border-primary/30 bg-primary-50 p-3 flex items-center gap-3 active:scale-[0.99] transition"
            >
              <StoreIcon size={20} className="text-primary shrink-0" />
              <div className="flex-1">
                <p className="font-semibold text-deep text-sm">Tutto da {single[0].chain_name}</p>
                <p className="text-[12px] text-stone-500">Un solo negozio, piu comodo</p>
              </div>
              <span className="font-bold text-deep tnum">EUR {single[0].total.toFixed(2)}</span>
            </button>

            {hasMulti && (
              <button
                onClick={() => {
                  setMode("multi");
                  setConfirmed(false);
                }}
                className="text-left rounded-btn border border-blue-300 bg-blue-50 p-3 flex items-center gap-3 active:scale-[0.99] transition"
              >
                <Split size={20} className="text-blue-600 shrink-0" />
                <div className="flex-1">
                  <p className="font-semibold text-deep text-sm">Dividi su {multi.length} negozi</p>
                  <p className="text-[12px] text-accent font-medium">
                    Risparmi EUR {result.multi_store.savings_vs_single.toFixed(2)}
                  </p>
                </div>
                <span className="font-bold text-deep tnum">EUR {result.multi_store.total.toFixed(2)}</span>
              </button>
            )}
          </div>
        )}

        {!fulfillment && (
          <p className="text-[12px] text-stone-500 bg-surface border border-stone-200 rounded-xl p-3">
            Scegli prima se vuoi ricevere la spesa a casa o ritirarla in negozio.
          </p>
        )}

        {fulfillment && mode !== null && (
          <div className="flex flex-col gap-3">
            <div>
              <div className="flex items-center justify-between text-[12px] text-stone-500 mb-1">
                <span>
                  {doneCount}/{allItems.length} aggiunti al carrello
                </span>
                <button onClick={resetPlan} className="text-primary font-medium">
                  Cambia
                </button>
              </div>
              <div className="h-2 rounded-pill bg-stone-200 overflow-hidden">
                <div className="h-full bg-primary transition-all" style={{ width: `${progress}%` }} />
              </div>
            </div>

            <div className="rounded-xl border border-stone-200 bg-surface p-3 flex flex-col gap-2">
              <div className="flex items-center gap-2 text-sm font-semibold text-deep">
                <LogIn size={16} className="text-primary" />
                Conferma e accedi al sito del supermercato
              </div>
              <p className="text-[12px] text-stone-500">
                Dopo la conferma, fai login sul sito ufficiale del supermercato. Poi l'agente potra aprire i prodotti uno alla volta e inserirli nel carrello guidato; SpesaSmart non legge password, non salva credenziali e non paga.
              </p>
              {!confirmed ? (
                <button
                  onClick={() => setConfirmed(true)}
                  className="inline-flex items-center justify-center gap-2 bg-secondary text-white px-4 py-2.5 rounded-xl text-sm font-bold active:scale-[0.99] transition"
                >
                  <Check size={17} /> Confermo prodotti e preferisco {serviceLabel(fulfillment)}
                </button>
              ) : (
                <div className="text-[12px] text-green-700 bg-green-50 border border-green-200 rounded-xl p-2">
                  Prodotti confermati. Ora accedi al sito del supermercato; dopo il login l'agente puo procedere con l'inserimento guidato nel carrello.
                </div>
              )}
            </div>

            {unsupportedStores.length > 0 && (
              <p className="text-[12px] text-amber-700 bg-amber-50 border border-amber-200 rounded-xl p-3">
                Alcuni negozi potrebbero non supportare {serviceLabel(fulfillment)}. Verifica il servizio sul sito ufficiale prima di confermare l'ordine.
              </p>
            )}

            {stores.map((s) => (
              <div key={s.key} className="rounded-btn border border-stone-200 overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2 bg-surface gap-2">
                  <div>
                    <span className="text-sm font-semibold text-deep">{s.chain_name}</span>
                    <p className="text-[11px] text-stone-500">
                      {supportsService(s, fulfillment)
                        ? `${serviceLabel(fulfillment)} disponibile o da confermare sul sito`
                        : `${serviceLabel(fulfillment)} da verificare sul sito`}
                    </p>
                  </div>
                  <span className="text-sm font-bold tnum">EUR {s.total.toFixed(2)}</span>
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
                            isDone ? "bg-primary border-primary text-white" : "border-stone-300 text-transparent"
                          }`}
                        >
                          <Check size={15} strokeWidth={3} />
                        </button>
                        <div className="flex-1 min-w-0">
                          <p className={`text-sm leading-snug ${isDone ? "line-through text-stone-400" : "text-stone-800"}`}>
                            {it.product_name}
                          </p>
                          <p className="text-[11px] text-stone-400">{it.label}</p>
                        </div>
                        <span className="text-sm font-semibold tnum shrink-0">EUR {it.price.toFixed(2)}</span>
                        {it.product_url ? (
                          <a
                            href={outbound(it.product_url, s.chain_slug)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className={`shrink-0 inline-flex items-center gap-1 text-[12px] px-2.5 py-1 rounded-btn font-medium active:scale-95 ${
                              confirmed ? "bg-primary text-white" : "bg-stone-200 text-stone-500 pointer-events-none"
                            }`}
                            aria-disabled={!confirmed}
                          >
                            Inserisci <ExternalLink size={12} />
                          </a>
                        ) : (
                          s.shop_url && (
                            <a
                              href={outbound(s.shop_url, s.chain_slug)}
                              target="_blank"
                              rel="noopener noreferrer"
                              className={`shrink-0 text-[12px] underline ${confirmed ? "text-primary" : "text-stone-400 pointer-events-none"}`}
                              aria-disabled={!confirmed}
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
                    className={`flex items-center justify-center gap-1.5 text-sm font-semibold py-2.5 transition active:scale-[0.99] ${
                      confirmed ? "bg-primary text-white hover:bg-primary-700" : "bg-stone-200 text-stone-500 pointer-events-none"
                    }`}
                    aria-disabled={!confirmed}
                  >
                    Accedi e avvia carrello {s.chain_name} <ArrowRight size={15} />
                  </a>
                )}
              </div>
            ))}

            <p className="flex items-start gap-1.5 text-[11px] text-stone-400">
              <ShieldCheck size={13} className="shrink-0 mt-0.5 text-primary" />
              L'agente puo preparare e guidare l'inserimento nel carrello sul sito ufficiale. L'ultimo invio ordine e il pagamento restano sempre confermati da te sul sito del supermercato.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

