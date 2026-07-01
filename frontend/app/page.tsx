"use client";
import Link from "next/link";
import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchProducts, getProductPrices, Product } from "@/lib/api";
import { RETAIL_SERVICE_CONFIG, minSpendLabel, serviceLabel } from "@/lib/retailServices";
import { useAppStore } from "@/lib/store";
import LocationBar from "@/components/ui/LocationBar";
import PriceCard from "@/components/ui/PriceCard";
import { PriceCardSkeletonList } from "@/components/ui/PriceCardSkeleton";
import EmptyState from "@/components/ui/EmptyState";
import {
  Search,
  X,
  ChevronLeft,
  ChevronRight,
  ShoppingCart,
  PackageOpen,
  MapPin,
  Bot,
  Barcode,
  ListChecks,
  Truck,
  Store,
  Handshake,
  Sparkles,
  ArrowRight,
  ShieldCheck,
  Clock,
  CheckCircle2,
} from "lucide-react";

const QUICK_SEARCHES = ["latte", "tonno rio", "caffe", "pasta", "uova", "acqua"];

function serviceSummary() {
  const delivery = RETAIL_SERVICE_CONFIG.filter((c) => c.services.includes("delivery")).length;
  const pickup = RETAIL_SERVICE_CONFIG.filter((c) => c.services.includes("pickup")).length;
  const delegated = RETAIL_SERVICE_CONFIG.filter((c) => c.pickupDelegate.enabled).length;
  return { delivery, pickup, delegated };
}

function SmartHome({ onQuickSearch }: { onQuickSearch: (q: string) => void }) {
  const summary = serviceSummary();
  const highlightedChains = RETAIL_SERVICE_CONFIG.slice(0, 6);
  const primaryTasks = [
    { icon: Bot, label: "Spesa settimanale", detail: "Lista completa e piano pronto", href: "/agente" },
    { icon: Search, label: "Confronta un prezzo", detail: "Risultati con fonte e disponibilita", action: () => onQuickSearch("tonno rio") },
    { icon: Truck, label: "Consegna o ritiro", detail: "Minimi e servizi prima del checkout", href: "/agente" },
  ];

  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-card border border-primary/20 bg-white shadow-card overflow-hidden">
        <div className="bg-hero-grad text-white p-4 relative overflow-hidden">
          <div className="absolute inset-0 bg-mesh opacity-60" aria-hidden />
          <div className="relative flex items-start gap-3">
            <div className="w-11 h-11 rounded-xl bg-white/20 grid place-items-center shrink-0">
              <Sparkles size={22} />
            </div>
            <div className="min-w-0">
              <h1 className="text-xl font-extrabold leading-tight">La spesa migliore, prima di aprire il carrello</h1>
              <p className="text-sm text-white/85 mt-1">
                Genera la lista, confronta prezzi reali e scegli consegna, ritiro o ritiro tramite incaricato.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Link
                  href="/agente"
                  className="inline-flex items-center gap-2 rounded-xl bg-white text-primary px-3 py-2 text-sm font-bold active:scale-[0.99] transition"
                >
                  <Bot size={16} /> Fai la spesa con l'agente
                </Link>
                <button
                  onClick={() => onQuickSearch("latte")}
                  className="inline-flex items-center gap-2 rounded-xl bg-white/15 border border-white/25 text-white px-3 py-2 text-sm font-bold active:scale-[0.99] transition"
                >
                  <Search size={16} /> Cerca un prodotto
                </button>
              </div>
            </div>
          </div>
        </div>

        <div className="p-4 grid gap-2 md:grid-cols-3 border-b border-stone-100">
          {primaryTasks.map((task) => {
            const Icon = task.icon;
            const content = (
              <>
                <Icon size={19} className="text-primary shrink-0" />
                <span className="min-w-0">
                  <span className="block text-sm font-bold text-deep">{task.label}</span>
                  <span className="block text-[12px] text-stone-500">{task.detail}</span>
                </span>
              </>
            );
            return task.href ? (
              <Link
                key={task.label}
                href={task.href}
                className="rounded-btn border border-primary/25 bg-primary-50 p-3 flex items-center gap-3 active:scale-[0.99] transition"
              >
                {content}
              </Link>
            ) : (
              <button
                key={task.label}
                onClick={task.action}
                className="rounded-btn border border-stone-200 bg-white p-3 flex items-center gap-3 text-left active:scale-[0.99] transition"
              >
                {content}
              </button>
            );
          })}
        </div>

        <div className="p-4 grid gap-2 sm:grid-cols-3">
          <Link
            href="/agente"
            className="rounded-btn border border-primary/30 bg-primary-50 p-3 flex items-center gap-3 active:scale-[0.99] transition"
          >
            <Bot size={20} className="text-primary shrink-0" />
            <span className="min-w-0">
              <span className="block text-sm font-bold text-deep">Agente spesa</span>
              <span className="block text-[12px] text-stone-500">Lista, piano e carrello guidato</span>
            </span>
          </Link>
          <button
            onClick={() => onQuickSearch("latte")}
            className="rounded-btn border border-stone-200 bg-white p-3 flex items-center gap-3 text-left active:scale-[0.99] transition"
          >
            <Search size={20} className="text-primary shrink-0" />
            <span className="min-w-0">
              <span className="block text-sm font-bold text-deep">Confronta prezzo</span>
              <span className="block text-[12px] text-stone-500">Trova il prodotto piu conveniente</span>
            </span>
          </button>
          <Link
            href="/scanner"
            className="rounded-btn border border-stone-200 bg-white p-3 flex items-center gap-3 active:scale-[0.99] transition"
          >
            <Barcode size={20} className="text-primary shrink-0" />
            <span className="min-w-0">
              <span className="block text-sm font-bold text-deep">Scanner</span>
              <span className="block text-[12px] text-stone-500">Barcode, scontrini e verifica prezzi</span>
            </span>
          </Link>
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-3">
        <div className="rounded-card border border-stone-200 bg-white p-4 shadow-card">
          <Truck size={19} className="text-blue-600" />
          <p className="mt-2 text-sm font-bold text-deep">Consegna a casa</p>
          <p className="text-[12px] text-stone-500">{summary.delivery} catene configurate, minimi mostrati prima del checkout.</p>
        </div>
        <div className="rounded-card border border-stone-200 bg-white p-4 shadow-card">
          <Store size={19} className="text-primary" />
          <p className="mt-2 text-sm font-bold text-deep">Ritiro in negozio</p>
          <p className="text-[12px] text-stone-500">{summary.pickup} catene con ritiro o verifica punto vendita.</p>
        </div>
        <div className="rounded-card border border-stone-200 bg-white p-4 shadow-card">
          <Handshake size={19} className="text-accent" />
          <p className="mt-2 text-sm font-bold text-deep">Ritiro con incaricato</p>
          <p className="text-[12px] text-stone-500">{summary.delegated} catene predisposte per delega/servizio esterno dove consentito.</p>
        </div>
      </section>

      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card">
        <div className="grid gap-3 md:grid-cols-3">
          <div className="flex items-start gap-2">
            <CheckCircle2 size={17} className="text-primary shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-bold text-deep">Risultati piu affidabili</p>
              <p className="text-[12px] text-stone-500">Disponibilita e prezzi anomali vengono evidenziati o esclusi.</p>
            </div>
          </div>
          <div className="flex items-start gap-2">
            <Clock size={17} className="text-primary shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-bold text-deep">Dati da verificare</p>
              <p className="text-[12px] text-stone-500">Ogni prezzo rimanda al sito ufficiale per conferma finale.</p>
            </div>
          </div>
          <div className="flex items-start gap-2">
            <ShieldCheck size={17} className="text-primary shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-bold text-deep">Ordine sotto controllo</p>
              <p className="text-[12px] text-stone-500">Login, pagamento e invio ordine restano sempre confermati da te.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-sm font-bold text-deep">Prova subito</p>
            <p className="text-xs text-stone-400">Ricerche utili per controllare match e prezzi reali.</p>
          </div>
          <ListChecks size={18} className="text-primary" />
        </div>
        <div className="flex flex-wrap gap-2">
          {QUICK_SEARCHES.map((q) => (
            <button
              key={q}
              onClick={() => onQuickSearch(q)}
              className="rounded-pill border border-stone-200 bg-surface px-3 py-1.5 text-sm font-medium text-stone-700 hover:border-primary hover:text-primary active:scale-[0.98] transition"
            >
              {q}
            </button>
          ))}
        </div>
      </section>

      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card flex flex-col gap-3">
        <div className="flex items-start gap-2">
          <ShieldCheck size={18} className="text-primary shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-bold text-deep">Minimi e servizi per catena</p>
            <p className="text-xs text-stone-500">
              I minimi possono cambiare per CAP, negozio, slot e promo: SpesaSmart li mostra come guida operativa e li fa confermare sul sito ufficiale.
            </p>
          </div>
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          {highlightedChains.map((chain) => (
            <div key={chain.chainSlug} className="rounded-btn border border-stone-200 bg-surface p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-bold text-deep">{chain.chainName}</p>
                <span className="text-[11px] text-stone-500">{chain.services.map(serviceLabel).join(" / ")}</span>
              </div>
              <p className="mt-1 text-[12px] text-stone-600">
                Consegna {minSpendLabel(chain.deliveryMin)} - Ritiro {minSpendLabel(chain.pickupMin)}
              </p>
              <p className="mt-1 text-[11px] text-stone-400">{chain.pickupDelegate.label}</p>
            </div>
          ))}
        </div>
        <Link href="/agente" className="inline-flex items-center justify-center gap-2 rounded-xl bg-stone-900 text-white px-4 py-2.5 text-sm font-bold active:scale-[0.99] transition">
          Prepara lista con servizi e minimi <ArrowRight size={16} />
        </Link>
      </section>
    </div>
  );
}

export default function HomePage() {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [trail, setTrail] = useState<Product[]>([]);
  const [trailPos, setTrailPos] = useState(-1);
  const [baseQuery, setBaseQuery] = useState("");
  const { location, radiusKm, searchArea } = useAppStore();

  useEffect(() => {
    if (selectedProduct) return;
    const t = setTimeout(() => setDebouncedQuery(query), 350);
    return () => clearTimeout(t);
  }, [query, selectedProduct]);

  const { data: products, isFetching: searching } = useQuery({
    queryKey: ["search", debouncedQuery, location, radiusKm, searchArea],
    queryFn: () =>
      searchProducts(debouncedQuery, location?.lat, location?.lng, radiusKm, searchArea),
    enabled: debouncedQuery.length >= 2 && !selectedProduct,
    staleTime: 30_000,
  });

  const { data: prices, isFetching: loadingPrices } = useQuery({
    queryKey: ["prices", selectedProduct?.id, location, radiusKm, searchArea],
    queryFn: () =>
      getProductPrices(selectedProduct!.id, location!.lat, location!.lng, radiusKm, searchArea),
    enabled: !!selectedProduct && !!location,
  });

  const openProduct = (p: Product) => {
    setTrail((t) => [...t.slice(0, trailPos + 1), p]);
    setTrailPos((i) => i + 1);
    setSelectedProduct(p);
    setQuery(p.name);
    setDebouncedQuery(p.name);
  };

  const applyPos = (pos: number) => {
    setTrailPos(pos);
    if (pos < 0) {
      setSelectedProduct(null);
      setQuery(baseQuery);
      setDebouncedQuery(baseQuery);
    } else {
      const p = trail[pos];
      setSelectedProduct(p);
      setQuery(p.name);
      setDebouncedQuery(p.name);
    }
  };

  const resetTrail = (q: string) => {
    setBaseQuery(q);
    setTrail([]);
    setTrailPos(-1);
  };

  const startSearch = (q: string) => {
    setQuery(q);
    setDebouncedQuery(q);
    setSelectedProduct(null);
    resetTrail(q);
  };

  const hasProductPrice = (p: Product) =>
    p.min_price != null && (p.price_store_count ?? 0) > 0;

  const canGoBack = trailPos >= 0;
  const canGoForward = trailPos < trail.length - 1;
  const showSmartHome = !query && !selectedProduct;

  return (
    <div className="flex flex-col gap-4">
      <LocationBar />

      <div className="flex items-stretch gap-2">
        <button
          onClick={() => applyPos(trailPos - 1)}
          disabled={!canGoBack}
          aria-label="Indietro"
          className="shrink-0 w-11 grid place-items-center rounded-btn border border-stone-200 bg-white text-stone-600 hover:border-primary hover:text-primary disabled:opacity-30 transition"
        >
          <ChevronLeft size={20} />
        </button>
        <button
          onClick={() => applyPos(trailPos + 1)}
          disabled={!canGoForward}
          aria-label="Avanti"
          className="shrink-0 w-11 grid place-items-center rounded-btn border border-stone-200 bg-white text-stone-600 hover:border-primary hover:text-primary disabled:opacity-30 transition"
        >
          <ChevronRight size={20} />
        </button>
        <div className="relative flex-1">
          <Search
            size={18}
            className="absolute left-3.5 top-1/2 -translate-y-1/2 text-stone-400"
          />
          <input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedProduct(null);
              resetTrail(e.target.value);
            }}
            placeholder="Cerca un prodotto, es. latte, tonno rio, caffe..."
            className="w-full bg-white border border-stone-200 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-pill pl-10 pr-10 py-3 text-base outline-none transition shadow-card"
          />
          {query && (
            <button
              onClick={() => {
                setQuery("");
                setSelectedProduct(null);
                resetTrail("");
              }}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-stone-400 hover:text-stone-700"
              aria-label="Pulisci"
            >
              <X size={18} />
            </button>
          )}
        </div>
      </div>

      {showSmartHome && <SmartHome onQuickSearch={startSearch} />}

      {!selectedProduct && searching && debouncedQuery.length >= 2 && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-stone-500 bg-white border border-stone-200 rounded-xl px-3 py-2 shadow-card">
            Sto cercando "{debouncedQuery}" e controllo prezzi, disponibilita e catene compatibili.
          </p>
          <PriceCardSkeletonList n={4} />
        </div>
      )}

      {!selectedProduct && !searching && products && products.length > 0 && (
        <div>
          <p className="text-xs text-stone-400 mb-1.5 px-1">
            {products.length} prodotti - ordinati dando priorita a prezzo disponibile e pertinenza
          </p>
          <ul className="bg-white border border-stone-200 rounded-card shadow-card divide-y divide-stone-100 overflow-y-auto max-h-[62vh]">
            {products.map((p) => {
              const hasPrice = hasProductPrice(p);
              return (
                <li key={p.id}>
                  <button
                    onClick={() => hasPrice && openProduct(p)}
                    disabled={!hasPrice}
                    className={`w-full text-left px-4 py-3 flex items-center gap-3 transition ${
                      hasPrice
                        ? "hover:bg-surface active:bg-stone-100"
                        : "cursor-not-allowed opacity-55"
                    }`}
                  >
                    {p.image_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={p.image_url}
                        alt={p.name}
                        className="w-11 h-11 object-contain rounded-lg shrink-0 bg-white"
                      />
                    ) : (
                      <div className="w-11 h-11 rounded-lg shrink-0 bg-stone-100 grid place-items-center text-stone-300">
                        <ShoppingCart size={18} />
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-stone-900 text-sm leading-snug">{p.name}</p>
                      {p.brand && <p className="text-xs text-stone-400">{p.brand}</p>}
                    </div>
                    <div className="text-right shrink-0">
                      {hasPrice ? (
                        <>
                          <p className="text-sm font-bold text-primary tnum">
                            EUR {Number(p.min_price).toFixed(2)}
                          </p>
                          <p className="text-[10px] text-stone-400">
                            {p.price_store_count} negoz{p.price_store_count! > 1 ? "i" : "io"}
                          </p>
                        </>
                      ) : (
                        <p className="text-[11px] font-medium text-stone-400">
                          nessun prezzo
                        </p>
                      )}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {!selectedProduct && !searching && debouncedQuery.length >= 2 && products && products.length === 0 && (
        <EmptyState
          Icon={PackageOpen}
          title="Nessun prodotto trovato"
          subtitle={`Nessun risultato per "${debouncedQuery}". Prova con un termine piu generico.`}
        />
      )}

      {selectedProduct && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            {selectedProduct.image_url && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={selectedProduct.image_url}
                alt={selectedProduct.name}
                className="w-16 h-16 object-contain rounded-card border border-stone-200 bg-white"
              />
            )}
            <div>
              <h2 className="text-lg font-bold text-deep leading-tight">{selectedProduct.name}</h2>
              {selectedProduct.brand && (
                <p className="text-sm text-stone-500">{selectedProduct.brand}</p>
              )}
            </div>
          </div>

          {!location && (
            <div className="bg-accent-50 border border-accent/30 rounded-card p-4 text-sm text-accent-600 flex items-center gap-2">
              <MapPin size={16} /> Attiva la posizione per vedere i prezzi vicino a te
            </div>
          )}

          {loadingPrices && <PriceCardSkeletonList n={4} />}

          {!loadingPrices && prices && prices.length === 0 && (
            <EmptyState
              Icon={MapPin}
              title="Nessun prezzo nei dintorni"
              subtitle="Prova ad allargare il raggio di ricerca o l'area sulla mappa."
            />
          )}

          {!loadingPrices && prices && prices.length > 0 && (() => {
            const avg = prices.reduce((s, p) => s + p.price, 0) / prices.length;
            const best = prices[0];
            const worst = prices[prices.length - 1];
            const maxSave = worst.price - best.price;
            return (
              <>
                {prices.length > 1 && maxSave > 0.01 && (
                  <div className="relative overflow-hidden rounded-2xl bg-hero-grad text-white p-4 shadow-float">
                    <div className="absolute inset-0 bg-mesh" aria-hidden />
                    <div className="relative">
                      <p className="text-[12px] font-medium text-white/80">
                        Miglior prezzo da {best.chain_name}
                      </p>
                      <p className="text-price-xl tnum mt-0.5">EUR {best.price.toFixed(2)}</p>
                      <p className="text-[13px] text-white/90 mt-0.5">
                        fino a <strong className="tnum">EUR {maxSave.toFixed(2)}</strong> in meno
                        rispetto al piu caro
                      </p>
                    </div>
                  </div>
                )}

                <p className="text-sm text-stone-500">
                  <strong className="text-deep">{prices.length}</strong> prezzi - spesa online e
                  negozi entro {radiusKm} km
                </p>
                <p className="text-[11px] text-stone-400 -mt-1">
                  Disponibili mostrati prima, poi prezzo. Alcuni link Acquista
                  possono essere affiliati (ADV).
                </p>
                <div className="flex flex-col gap-3">
                  {prices.map((p, i) => (
                    <PriceCard
                      key={`${p.store_id}-${i}`}
                      result={p}
                      rank={i}
                      avgPrice={avg}
                      imageUrl={selectedProduct.image_url}
                    />
                  ))}
                </div>
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}
