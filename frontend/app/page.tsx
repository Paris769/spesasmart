"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { searchProducts, getProductPrices, Product } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import LocationBar from "@/components/ui/LocationBar";
import PriceCard from "@/components/ui/PriceCard";

export default function HomePage() {
  const [query, setQuery] = useState("");
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const { location, radiusKm } = useAppStore();

  const { data: products, isFetching: searching } = useQuery({
    queryKey: ["search", query],
    queryFn: () => searchProducts(query),
    enabled: query.length >= 2 && !selectedProduct,
    staleTime: 30_000,
  });

  const { data: prices, isFetching: loadingPrices } = useQuery({
    queryKey: ["prices", selectedProduct?.id, location, radiusKm],
    queryFn: () =>
      getProductPrices(selectedProduct!.id, location!.lat, location!.lng, radiusKm),
    enabled: !!selectedProduct && !!location,
  });

  return (
    <div className="flex flex-col gap-4">
      <LocationBar />

      {/* Search bar */}
      <div className="relative">
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSelectedProduct(null);
          }}
          placeholder="Cerca un prodotto… es. latte intero, pasta barilla"
          className="w-full border-2 border-gray-200 focus:border-primary rounded-xl px-4 py-3 text-lg outline-none transition"
        />
        {query && (
          <button
            onClick={() => { setQuery(""); setSelectedProduct(null); }}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-xl"
          >
            ×
          </button>
        )}
      </div>

      {/* Suggerimenti prodotti */}
      {!selectedProduct && products && products.length > 0 && (
        <ul className="bg-white border rounded-xl shadow-sm divide-y overflow-hidden">
          {products.slice(0, 8).map((p) => (
            <li key={p.id}>
              <button
                onClick={() => { setSelectedProduct(p); setQuery(p.name); }}
                className="w-full text-left px-4 py-3 hover:bg-gray-50 flex items-center gap-3"
              >
                {p.image_url && (
                  <img src={p.image_url} alt={p.name} className="w-10 h-10 object-contain rounded" />
                )}
                <div>
                  <p className="font-medium text-gray-900 text-sm">{p.name}</p>
                  {p.brand && <p className="text-xs text-gray-500">{p.brand}</p>}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Risultati prezzi */}
      {selectedProduct && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            {selectedProduct.image_url && (
              <img
                src={selectedProduct.image_url}
                alt={selectedProduct.name}
                className="w-16 h-16 object-contain rounded-lg border"
              />
            )}
            <div>
              <h2 className="text-lg font-bold text-gray-900">{selectedProduct.name}</h2>
              {selectedProduct.brand && (
                <p className="text-sm text-gray-500">{selectedProduct.brand}</p>
              )}
            </div>
          </div>

          {!location && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800">
              📍 Attiva la posizione per vedere i prezzi vicino a te
            </div>
          )}

          {loadingPrices && (
            <div className="text-center py-8 text-gray-500">Ricerca prezzi in corso…</div>
          )}

          {prices && prices.length === 0 && (
            <div className="text-center py-8 text-gray-500">
              Nessun prezzo trovato nel raggio di {radiusKm} km
            </div>
          )}

          {prices && prices.length > 0 && (
            <>
              <p className="text-sm text-gray-600">
                Trovati <strong>{prices.length}</strong> prezzi entro {radiusKm} km
              </p>
              <div className="flex flex-col gap-3">
                {prices.map((p, i) => (
                  <PriceCard key={`${p.store_id}-${i}`} result={p} rank={i} />
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Stato vuoto */}
      {!query && !selectedProduct && (
        <div className="text-center py-16 text-gray-400">
          <p className="text-4xl mb-3">🛒</p>
          <p className="text-lg font-medium">Trova il prezzo migliore</p>
          <p className="text-sm mt-1">
            Cerca qualsiasi prodotto e confronta i prezzi nei supermercati vicino a te
          </p>
        </div>
      )}
    </div>
  );
}
