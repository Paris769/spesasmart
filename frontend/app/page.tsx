"use client";
import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchProducts, getProductPrices, Product } from "@/lib/api";
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
} from "lucide-react";

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

  const canGoBack = trailPos >= 0;
  const canGoForward = trailPos < trail.length - 1;

  return (
    <div className="flex flex-col gap-4">
      <LocationBar />

      {/* Barra di ricerca */}
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
            placeholder="Cerca un prodotto…"
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

      {/* Skeleton durante la ricerca */}
      {!selectedProduct && searching && debouncedQuery.length >= 2 && (
        <PriceCardSkeletonList n={4} />
      )}

      {/* Suggerimenti prodotti */}
      {!selectedProduct && !searching && products && products.length > 0 && (
        <div>
          <p className="text-xs text-stone-400 mb-1.5 px-1">
            {products.length} prodotti — tocca per vedere i prezzi vicino a te
          </p>
          <ul className="bg-white border border-stone-200 rounded-card shadow-card divide-y divide-stone-100 overflow-y-auto max-h-[62vh]">
            {products.map((p) => (
              <li key={p.id}>
                <button
                  onClick={() => openProduct(p)}
                  className="w-full text-left px-4 py-3 hover:bg-surface active:bg-stone-100 flex items-center gap-3 transition"
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
                  {p.min_price != null && (
                    <div className="text-right shrink-0">
                      <p className="text-sm font-bold text-primary tnum">
                        €{Number(p.min_price).toFixed(2)}
                      </p>
                      {p.price_store_count ? (
                        <p className="text-[10px] text-stone-400">
                          {p.price_store_count} negoz{p.price_store_count > 1 ? "i" : "io"}
                        </p>
                      ) : null}
                    </div>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Nessun risultato di ricerca */}
      {!selectedProduct && !searching && debouncedQuery.length >= 2 && products && products.length === 0 && (
        <EmptyState
          Icon={PackageOpen}
          title="Nessun prodotto trovato"
          subtitle={`Nessun risultato per "${debouncedQuery}". Prova con un termine più generico.`}
        />
      )}

      {/* Risultati prezzi */}
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
                {/* Hero "miglior affare" */}
                {prices.length > 1 && maxSave > 0.01 && (
                  <div className="relative overflow-hidden rounded-2xl bg-hero-grad text-white p-4 shadow-float">
                    <div className="absolute inset-0 bg-mesh" aria-hidden />
                    <div className="relative">
                      <p className="text-[12px] font-medium text-white/80">
                        Miglior prezzo da {best.chain_name}
                      </p>
                      <p className="text-price-xl tnum mt-0.5">€{best.price.toFixed(2)}</p>
                      <p className="text-[13px] text-white/90 mt-0.5">
                        fino a <strong className="tnum">€{maxSave.toFixed(2)}</strong> in meno
                        rispetto al più caro
                      </p>
                    </div>
                  </div>
                )}

                <p className="text-sm text-stone-500">
                  <strong className="text-deep">{prices.length}</strong> prezzi — spesa online e
                  negozi entro {radiusKm} km
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

      {/* Stato iniziale */}
      {!query && !selectedProduct && (
        <EmptyState
          Icon={ShoppingCart}
          title="Trova il prezzo migliore"
          subtitle="Cerca qualsiasi prodotto e confronta i prezzi nei supermercati vicino a te."
        />
      )}
    </div>
  );
}
