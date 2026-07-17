"use client";
import { useEffect, useState } from "react";
import { Plus, Search } from "lucide-react";
import { Product, searchProducts } from "@/lib/api";
import { useAppStore } from "@/lib/store";

// Ricerca + ancoraggio prodotto riusabile (pattern estratto da agente/page.tsx):
// input con autocomplete di prodotti REALI dal catalogo. Chi lo usa riceve o un
// prodotto ancorato (onPickProduct) o un testo generico (onPickGeneric).

interface Props {
  placeholder?: string;
  onPickProduct: (product: Product) => void;
  onPickGeneric: (query: string) => void;
}

function hasProductPrice(p: Product) {
  return p.min_price != null && (p.price_store_count ?? 0) > 0;
}

export default function ProductPicker({ placeholder, onPickProduct, onPickGeneric }: Props) {
  const { location, radiusKm } = useAppStore();
  const [text, setText] = useState("");
  const [suggestions, setSuggestions] = useState<Product[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    const term = text.trim();
    if (term.length < 2) {
      setSuggestions([]);
      setSearching(false);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const handle = setTimeout(async () => {
      try {
        const found = await searchProducts(term, location?.lat, location?.lng, radiusKm);
        if (!cancelled) setSuggestions(found.slice(0, 8));
      } catch {
        if (!cancelled) setSuggestions([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [text, location, radiusKm]);

  const pickGeneric = () => {
    const query = text.trim();
    if (query.length < 2) return;
    onPickGeneric(query);
    setText("");
    setSuggestions([]);
  };

  const pickProduct = (product: Product) => {
    if (!hasProductPrice(product)) return;
    onPickProduct(product);
    setText("");
    setSuggestions([]);
  };

  return (
    <div className="relative flex flex-col gap-2">
      <div className="flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && pickGeneric()}
          className="flex-1 border-2 border-stone-200 focus:border-primary rounded-xl px-3 py-2 text-sm outline-none"
          placeholder={placeholder || "Aggiungi prodotto, es. latte"}
        />
        <button
          onClick={pickGeneric}
          className="w-11 rounded-xl bg-stone-900 text-white grid place-items-center"
          aria-label="Aggiungi come ricerca generica"
        >
          <Plus size={18} />
        </button>
      </div>

      {text.trim().length >= 2 && (searching || suggestions.length > 0) && (
        <div className="absolute z-20 top-[calc(100%+4px)] left-0 right-0 bg-white border border-stone-200 rounded-xl shadow-float overflow-hidden max-h-[56vh] overflow-y-auto">
          <p className="px-4 py-2 text-[12px] font-medium text-primary bg-primary-50 border-b border-primary/10">
            Riferimenti reali: scegli un prodotto preciso per un confronto sicuro.
          </p>
          {searching && suggestions.length === 0 && (
            <p className="px-4 py-3 text-sm text-stone-400">Cerco prodotti...</p>
          )}
          {suggestions.map((product) => {
            const hasPrice = hasProductPrice(product);
            return (
              <button
                key={product.id}
                onClick={() => pickProduct(product)}
                disabled={!hasPrice}
                className={`w-full flex items-center gap-3 px-3 py-2 text-left border-b border-stone-100 last:border-0 ${
                  hasPrice ? "hover:bg-surface" : "cursor-not-allowed opacity-55"
                }`}
              >
                {product.image_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={product.image_url}
                    alt=""
                    className="w-10 h-10 object-contain rounded bg-white border border-stone-100 shrink-0"
                  />
                ) : (
                  <div className="w-10 h-10 rounded bg-stone-100 grid place-items-center shrink-0 text-stone-300">
                    <Search size={16} />
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-stone-800 leading-snug line-clamp-2">{product.name}</p>
                  {product.brand && <p className="text-[11px] text-stone-400">{product.brand}</p>}
                </div>
                {hasPrice ? (
                  <div className="text-right shrink-0">
                    <p className="text-sm font-semibold text-deep tnum">
                      da EUR {Number(product.min_price).toFixed(2)}
                    </p>
                    <p className="text-[10px] text-stone-400">
                      {product.price_store_count} negoz{(product.price_store_count ?? 0) > 1 ? "i" : "io"}
                    </p>
                  </div>
                ) : (
                  <span className="text-[11px] font-medium text-stone-400 shrink-0">nessun prezzo</span>
                )}
              </button>
            );
          })}
          <button
            onClick={pickGeneric}
            className="w-full px-3 py-2 text-left text-[12px] text-stone-500 hover:bg-surface"
          >
            + Usa "{text.trim()}" come ricerca generica
          </button>
        </div>
      )}
    </div>
  );
}
