"use client";
import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchProducts, getProductPrices, Product, PriceResult } from "@/lib/api";
import { RETAIL_SERVICE_CONFIG } from "@/lib/retailServices";
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
  AlertTriangle,
} from "lucide-react";

function formatEur(value?: number | null) {
  return typeof value === "number" ? `EUR ${value.toFixed(2)}` : "prezzo non disponibile";
}

const QUICK_SEARCHES = ["pasta barilla", "latte parmalat", "tonno rio mare", "caffe", "uova", "acqua"];
const SEARCH_FALLBACK_LOCATION = { lat: 45.4642, lng: 9.19, label: "Milano" };

function serviceSummary() {
  const delivery = RETAIL_SERVICE_CONFIG.filter((c) => c.services.includes("delivery")).length;
  const pickup = RETAIL_SERVICE_CONFIG.filter((c) => c.services.includes("pickup")).length;
  const delegated = RETAIL_SERVICE_CONFIG.filter((c) => c.pickupDelegate.enabled).length;
  return { delivery, pickup, delegated };
}

function hasProductPrice(p: Product) {
  return p.min_price != null && (p.price_store_count ?? 0) > 0;
}

function freshnessLabel(value?: string | null) {
  if (!value) return null;
  const hours = Math.floor((Date.now() - new Date(value).getTime()) / 3_600_000);
  if (hours < 1) return "aggiornato ora";
  if (hours < 24) return `${hours}h fa`;
  return `${Math.floor(hours / 24)}g fa`;
}

function SmartHome({ onQuickSearch }: { onQuickSearch: (q: string) => void }) {
  const [draft, setDraft] = useState("pasta barilla");
  const summary = serviceSummary();

  const submit = (event?: { preventDefault: () => void }) => {
    event?.preventDefault();
    const q = draft.trim() || "pasta barilla";
    onQuickSearch(q);
  };

  return (
    <section className="rounded-card border border-primary/20 bg-white shadow-card overflow-hidden">
      <div className="p-4 sm:p-5 bg-hero-grad text-white relative overflow-hidden">
        <div className="absolute inset-0 bg-mesh opacity-55" aria-hidden />
        <div className="relative flex flex-col gap-4">
          <div>
            <p className="text-[12px] font-bold uppercase tracking-wide text-white/75">SpesaSmart confronta i supermercati</p>
            <h1 className="mt-1 text-2xl sm:text-3xl font-extrabold leading-tight">Fai la spesa risparmiando</h1>
            <p className="mt-2 text-sm sm:text-base text-white/86 max-w-2xl">
              Scrivi un prodotto e scopri subito il prezzo migliore, il supermercato dove conviene e gli altri prezzi disponibili.
            </p>
          </div>

          <form onSubmit={submit} className="rounded-2xl bg-white p-2 shadow-float flex flex-col sm:flex-row gap-2">
            <div className="relative flex-1">
              <Search size={19} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-stone-400" />
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                className="w-full h-12 rounded-xl border border-stone-200 pl-10 pr-3 text-base text-deep outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
                placeholder="Cerca un prodotto, es. pasta barilla"
              />
            </div>
            <button type="submit" className="h-12 rounded-xl bg-primary px-4 text-sm font-extrabold text-white active:scale-[0.99] transition">
              Cerca prezzo migliore
            </button>
          </form>

          <div className="flex flex-wrap gap-2 text-sm">
            <span className="text-white/75">Prova:</span>
            {QUICK_SEARCHES.slice(0, 3).map((q) => (
              <button
                key={q}
                onClick={() => onQuickSearch(q)}
                className="rounded-pill bg-white/14 border border-white/20 px-3 py-1 font-semibold text-white active:scale-[0.98] transition"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="grid gap-0 border-t border-stone-100 md:grid-cols-3">
        <div className="p-4 border-b md:border-b-0 md:border-r border-stone-100">
          <p className="text-sm font-bold text-deep">Prezzo migliore subito</p>
          <p className="text-[12px] text-stone-500 mt-1">Nei risultati vedi prezzo e supermercato senza aprire la scheda.</p>
        </div>
        <div className="p-4 border-b md:border-b-0 md:border-r border-stone-100">
          <p className="text-sm font-bold text-deep">Tutti gli shop al passaggio</p>
          <p className="text-[12px] text-stone-500 mt-1">Passa sul risultato per vedere prezzi, disponibilita e aggiornamento.</p>
        </div>
        <div className="p-4">
          <p className="text-sm font-bold text-deep">Consegna e ritiro dopo</p>
          <p className="text-[12px] text-stone-500 mt-1">{summary.delivery} catene con consegna, {summary.pickup} con ritiro, {summary.delegated} predisposte per incaricato.</p>
        </div>
      </div>
    </section>
  );
}

function ProductPricePopover({ prices, loading, fallbackArea }: { prices?: PriceResult[]; loading: boolean; fallbackArea: boolean }) {
  return (
    <div className="absolute right-2 top-[calc(100%-6px)] z-30 w-[min(360px,calc(100vw-2rem))] rounded-xl border border-stone-200 bg-white shadow-float p-3 text-left">
      <div className="flex items-start justify-between gap-2 border-b border-stone-100 pb-2">
        <div>
          <p className="text-sm font-bold text-deep">Prezzi nei supermercati</p>
          <p className="text-[11px] text-stone-400">{fallbackArea ? "Area: Milano se la posizione non e attiva" : "Area: posizione/raggio selezionati"}</p>
        </div>
      </div>
      {loading && <p className="py-3 text-sm text-stone-500">Carico prezzi e disponibilita...</p>}
      {!loading && prices && prices.length === 0 && (
        <p className="py-3 text-sm text-stone-500">Nessun prezzo dettagliato disponibile ora.</p>
      )}
      {!loading && prices && prices.length > 0 && (
        <div className="mt-2 flex flex-col gap-1.5 max-h-64 overflow-y-auto">
          {prices.slice(0, 10).map((price, index) => (
            <div key={`${price.store_id}-${index}`} className="flex items-center justify-between gap-3 rounded-lg bg-surface px-2.5 py-2">
              <div className="min-w-0">
                <p className="text-[12px] font-semibold text-deep truncate">{price.chain_name}</p>
                <p className={`text-[10px] ${price.in_stock === false ? "text-red-600" : "text-stone-400"}`}>
                  {price.in_stock === false ? "non disponibile" : "disponibile"}
                  {freshnessLabel(price.scraped_at) ? ` - ${freshnessLabel(price.scraped_at)}` : ""}
                </p>
              </div>
              <p className={`text-sm font-extrabold tnum shrink-0 ${price.in_stock === false ? "text-stone-400" : "text-primary"}`}>
                {formatEur(price.price)}
              </p>
            </div>
          ))}
        </div>
      )}
      <p className="mt-2 text-[10px] text-stone-400">Non disponibile = presente nel catalogo ma non acquistabile ora.</p>
    </div>
  );
}

function ProductResultRow({
  product,
  location,
  radiusKm,
  searchArea,
  onOpen,
}: {
  product: Product;
  location?: { lat: number; lng: number } | null;
  radiusKm: number;
  searchArea?: [number, number][] | null;
  onOpen: (product: Product) => void;
}) {
  const [inspect, setInspect] = useState(false);
  const hasPrice = hasProductPrice(product);
  const effectiveLocation = location ?? SEARCH_FALLBACK_LOCATION;
  const bestChain = product.best_price_chain_name || product.best_price_store_name || "supermercato disponibile";
  const unavailableBest = product.best_price_in_stock === false;
  const updated = freshnessLabel(product.best_price_scraped_at);

  const { data: previewPrices, isFetching } = useQuery({
    queryKey: ["price-preview", product.id, effectiveLocation.lat, effectiveLocation.lng, radiusKm, searchArea],
    queryFn: () => getProductPrices(product.id, effectiveLocation.lat, effectiveLocation.lng, radiusKm, searchArea),
    enabled: inspect && hasPrice,
    staleTime: 5 * 60_000,
  });

  return (
    <li
      className="relative"
      onMouseEnter={() => setInspect(true)}
      onMouseLeave={() => setInspect(false)}
      onFocus={() => setInspect(true)}
      onBlur={() => setInspect(false)}
    >
      <div
        role="button"
        tabIndex={hasPrice ? 0 : -1}
        onClick={() => hasPrice && onOpen(product)}
        onKeyDown={(event) => {
          if (hasPrice && (event.key === "Enter" || event.key === " ")) onOpen(product);
        }}
        className={`w-full text-left px-4 py-3 flex items-center gap-3 transition ${
          hasPrice ? "hover:bg-surface active:bg-stone-100 cursor-pointer" : "cursor-not-allowed opacity-55"
        }`}
      >
        {product.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={product.image_url} alt={product.name} className="w-12 h-12 object-contain rounded-lg shrink-0 bg-white border border-stone-100" />
        ) : (
          <div className="w-12 h-12 rounded-lg shrink-0 bg-stone-100 grid place-items-center text-stone-300">
            <ShoppingCart size={18} />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-stone-900 text-sm leading-snug line-clamp-2">{product.name}</p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
            {product.brand && <span className="text-stone-400">{product.brand}</span>}
            {unavailableBest && (
              <span className="inline-flex items-center gap-1 rounded-pill bg-red-50 px-2 py-0.5 font-semibold text-red-700">
                <AlertTriangle size={10} /> miglior prezzo non disponibile
              </span>
            )}
          </div>
        </div>
        <div className="text-right shrink-0 min-w-[112px]">
          {hasPrice ? (
            <>
              <p className="text-[10px] font-bold uppercase tracking-wide text-stone-400">Prezzo migliore</p>
              <p className="text-lg font-extrabold text-primary tnum leading-tight">{formatEur(Number(product.min_price))}</p>
              <p className="text-[11px] font-semibold text-deep truncate max-w-[130px]">{bestChain}</p>
              {product.best_price_per_unit != null && (
                <p className="text-[10px] text-stone-500 tnum">{formatEur(Number(product.best_price_per_unit))}/unita</p>
              )}
              <p className="text-[10px] text-stone-400">{product.price_store_count} shop{updated ? ` - ${updated}` : ""}</p>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  setInspect((value) => !value);
                }}
                className="mt-1 rounded-pill border border-primary/20 bg-primary-50 px-2 py-0.5 text-[10px] font-bold text-primary md:hidden"
              >
                Mostra shop
              </button>
            </>
          ) : (
            <p className="text-[11px] font-medium text-stone-400">nessun prezzo</p>
          )}
        </div>
      </div>
      {inspect && hasPrice && (
        <ProductPricePopover prices={previewPrices} loading={isFetching} fallbackArea={!location} />
      )}
    </li>
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

  const { data: products, isFetching: searching, isError: searchError, refetch: retrySearch } = useQuery({
    queryKey: ["search", debouncedQuery, location, radiusKm, searchArea],
    queryFn: () =>
      searchProducts(
        debouncedQuery,
        (location ?? SEARCH_FALLBACK_LOCATION).lat,
        (location ?? SEARCH_FALLBACK_LOCATION).lng,
        radiusKm,
        searchArea
      ),
    enabled: debouncedQuery.length >= 2 && !selectedProduct,
    staleTime: 30_000,
    retry: 1,
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

  const canGoBack = trailPos >= 0;
  const canGoForward = trailPos < trail.length - 1;
  const showSmartHome = !query && !selectedProduct;

  return (
    <div className="flex flex-col gap-4">
      {showSmartHome && <SmartHome onQuickSearch={startSearch} />}

      <LocationBar />

      {!showSmartHome && (
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
      )}

      {!selectedProduct && searching && debouncedQuery.length >= 2 && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-stone-500 bg-white border border-stone-200 rounded-xl px-3 py-2 shadow-card">
            Sto cercando "{debouncedQuery}" e controllo prezzi, disponibilita e catene compatibili.
          </p>
          <PriceCardSkeletonList n={4} />
        </div>
      )}

      {!selectedProduct && !searching && searchError && debouncedQuery.length >= 2 && (
        <div className="rounded-card border border-red-200 bg-red-50 p-4 text-sm text-red-700 flex flex-col gap-2">
          <p className="font-bold">Non riesco a recuperare i prezzi ora</p>
          <p>C'e stato un problema temporaneo nel confronto dei supermercati. Riprova o cerca un nome piu semplice.</p>
          <button
            onClick={() => retrySearch()}
            className="self-start rounded-xl bg-red-600 px-3 py-2 text-xs font-bold text-white active:scale-[0.99] transition"
          >
            Riprova
          </button>
        </div>
      )}

      {!selectedProduct && !searching && products && products.length > 0 && (
        <div>
          <p className="text-xs text-stone-400 mb-1.5 px-1">
            {products.length} prodotti - ordinati dando priorita a prezzo disponibile e pertinenza
          </p>
          <ul className="bg-white border border-stone-200 rounded-card shadow-card divide-y divide-stone-100 overflow-y-auto max-h-[62vh]">
            {products.map((p) => (
              <ProductResultRow
                key={p.id}
                product={p}
                location={location}
                radiusKm={radiusKm}
                searchArea={searchArea}
                onOpen={openProduct}
              />
            ))}
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
