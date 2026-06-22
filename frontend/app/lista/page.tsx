"use client";
import { useEffect, useState } from "react";
import {
  optimizeQuick,
  searchProducts,
  QuickOptimizeResult,
  Product,
} from "@/lib/api";
import { useAppStore } from "@/lib/store";
import LocationBar from "@/components/ui/LocationBar";
import PurchasePlan from "@/components/ui/PurchasePlan";
import { PencilLine, Calculator, ShoppingBag, Search } from "lucide-react";

// Una voce della lista: o un PRODOTTO REALE scelto dall'autocomplete (con
// product_id → confronto esatto tra negozi), oppure testo libero (match fuzzy).
type ListItem = {
  query: string;
  product_id?: string;
  label: string;
  image_url?: string | null;
};
const itemKey = (it: ListItem) => it.product_id ?? it.query.toLowerCase();

export default function ListaPage() {
  const { location, radiusKm } = useAppStore();
  const [text, setText] = useState("");
  const [items, setItems] = useState<ListItem[]>([]);
  const [sug, setSug] = useState<Product[]>([]);
  const [searching, setSearching] = useState(false);
  const [result, setResult] = useState<QuickOptimizeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Autocomplete: mentre scrivi, propone prodotti REALI dal catalogo (debounce).
  // Funziona anche senza posizione; se attiva, mostra il prezzo "da €…" vicino.
  useEffect(() => {
    const t = text.trim();
    if (t.length < 2) {
      setSug([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const h = setTimeout(async () => {
      try {
        const res = await searchProducts(t, location?.lat, location?.lng, radiusKm);
        setSug(res.slice(0, 8));
      } catch {
        setSug([]);
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => clearTimeout(h);
  }, [text, location, radiusKm]);

  const addItem = (it: ListItem) => {
    setItems((prev) =>
      prev.some((x) => itemKey(x) === itemKey(it)) ? prev : [...prev, it]
    );
    setText("");
    setSug([]);
    setResult(null);
  };

  const hasProductPrice = (p: Product) =>
    p.min_price != null && (p.price_store_count ?? 0) > 0;

  const addProduct = (p: Product) => {
    if (!hasProductPrice(p)) return;
    addItem({ query: p.name, product_id: p.id, label: p.name, image_url: p.image_url });
  };

  const addFreeText = () => {
    const t = text.trim();
    if (t.length >= 2) addItem({ query: t, label: t });
  };

  const removeItem = (i: number) => {
    setItems((prev) => prev.filter((_, idx) => idx !== i));
    setResult(null);
  };

  const optimize = async () => {
    if (!location || items.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const res = await optimizeQuick(
        items.map((it) => ({ query: it.query, quantity: 1, product_id: it.product_id })),
        location.lat,
        location.lng,
        radiusKm
      );
      setResult(res);
    } catch {
      setError("Errore nell'ottimizzazione. Riprova.");
    } finally {
      setLoading(false);
    }
  };

  const best = result?.best_single;
  const multi = result?.multi_store;

  return (
    <div className="flex flex-col gap-4">
      <LocationBar />
      <div>
        <h1 className="text-xl font-bold text-gray-800">La tua lista della spesa</h1>
        <p className="text-sm text-gray-500">
          Scrivi cosa ti serve e <b>scegli il prodotto reale</b> dall&apos;elenco: ti
          dico dove costa meno.
        </p>
      </div>

      {/* Spiegazione sempre visibile: fa scoprire l'assistente d'acquisto
          ancora prima di ottimizzare (compariva solo a risultati pronti). */}
      <div className="rounded-card border border-primary/20 bg-primary-50/60 p-3">
        <div className="flex items-center gap-2 mb-2.5">
          <div className="w-7 h-7 rounded-full bg-primary grid place-items-center">
            <ShoppingBag size={15} className="text-white" />
          </div>
          <p className="text-sm font-bold text-deep">
            Assistente acquisto — ti accompagna fino al carrello
          </p>
        </div>
        <ol className="grid grid-cols-3 gap-2 text-center">
          {[
            { Icon: PencilLine, t: "Scegli i prodotti" },
            { Icon: Calculator, t: "Trova dove costa meno" },
            { Icon: ShoppingBag, t: "Apri e compra guidato" },
          ].map(({ Icon, t }, i) => (
            <li key={i} className="flex flex-col items-center gap-1">
              <div className="relative">
                <Icon size={20} className="text-primary" />
                <span className="absolute -top-1.5 -right-2 w-4 h-4 rounded-full bg-primary text-white text-[10px] font-bold grid place-items-center">
                  {i + 1}
                </span>
              </div>
              <span className="text-[11px] leading-tight text-stone-600">{t}</span>
            </li>
          ))}
        </ol>
        <p className="text-[11px] text-stone-400 mt-2 text-center">
          Serve la posizione attiva. Ogni prodotto si apre già pronto sul sito del
          supermercato dove sei loggato.
        </p>
      </div>

      {/* Input + autocomplete prodotti reali */}
      <div className="relative">
        <div className="flex gap-2">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addFreeText()}
            placeholder="Cerca un prodotto… es. latte, Gillette Mach3"
            className="flex-1 border-2 border-gray-200 focus:border-primary rounded-xl px-4 py-2 outline-none transition"
          />
          <button
            onClick={addFreeText}
            className="bg-primary text-white px-4 rounded-xl font-medium"
          >
            Aggiungi
          </button>
        </div>

        {text.trim().length >= 2 && (sug.length > 0 || searching) && (
          <div className="absolute z-20 left-0 right-0 mt-1 bg-white border border-stone-200 rounded-xl shadow-float overflow-hidden max-h-[60vh] overflow-y-auto">
            {searching && sug.length === 0 && (
              <p className="px-4 py-3 text-sm text-stone-400">Cerco prodotti…</p>
            )}
            {sug.map((p) => {
              const hasPrice = hasProductPrice(p);
              return (
              <button
                key={p.id}
                onClick={() => hasPrice && addProduct(p)}
                disabled={!hasPrice}
                className={`w-full flex items-center gap-3 px-3 py-2 text-left border-b border-stone-100 last:border-0 ${
                  hasPrice ? "hover:bg-surface" : "cursor-not-allowed opacity-55"
                }`}
              >
                {p.image_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={p.image_url}
                    alt=""
                    className="w-9 h-9 object-contain rounded bg-white border border-stone-100 shrink-0"
                  />
                ) : (
                  <div className="w-9 h-9 rounded bg-stone-100 grid place-items-center shrink-0 text-stone-300">
                    <Search size={16} />
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-stone-800 leading-snug truncate">{p.name}</p>
                  {p.brand && <p className="text-[11px] text-stone-400">{p.brand}</p>}
                </div>
                {hasPrice ? (
                  <span className="text-sm font-semibold text-deep tnum shrink-0">
                    da €{Number(p.min_price).toFixed(2)}
                  </span>
                ) : (
                  <span className="text-[11px] font-medium text-stone-400 shrink-0">
                    nessun prezzo
                  </span>
                )}
              </button>
              );
            })}
            <button
              onClick={addFreeText}
              className="w-full px-3 py-2 text-left text-[12px] text-stone-500 hover:bg-surface"
            >
              + Aggiungi «{text.trim()}» come ricerca generica
            </button>
          </div>
        )}
      </div>

      {/* Chip voci */}
      {items.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {items.map((it, i) => (
            <span
              key={i}
              className="bg-white border rounded-full pl-1.5 pr-2 py-1 text-sm flex items-center gap-1.5"
            >
              {it.image_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={it.image_url}
                  alt=""
                  className="w-6 h-6 object-contain rounded-full bg-white border border-stone-100"
                />
              ) : (
                <span className="w-6 h-6 rounded-full bg-stone-100 grid place-items-center text-stone-400 text-[10px]">
                  ●
                </span>
              )}
              <span className="max-w-[150px] truncate">{it.label}</span>
              {it.product_id && (
                <span
                  className="text-[10px] text-primary font-bold"
                  title="Prodotto reale selezionato"
                >
                  ✓
                </span>
              )}
              <button
                onClick={() => removeItem(i)}
                className="text-gray-400 hover:text-red-500 font-bold px-1"
                aria-label={`Rimuovi ${it.label}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {items.length > 0 && (
        <button
          onClick={optimize}
          disabled={!location || loading}
          className="bg-secondary text-white px-4 py-3 rounded-xl text-sm font-bold disabled:opacity-50"
        >
          {loading
            ? "Calcolo in corso…"
            : !location
            ? "📍 Attiva la posizione per ottimizzare"
            : "🧮 Trova dove spendo meno"}
        </button>
      )}

      {error && <p className="text-sm text-red-600">{error}</p>}

      {/* Risultati */}
      {result && (
        <div className="flex flex-col gap-4">
          {/* Miglior negozio singolo */}
          {best && (
            <div className="bg-green-50 border border-primary rounded-xl p-4">
              <p className="font-bold text-primary mb-1">
                ✓ Conviene fare tutto da {best.chain_name}
              </p>
              <p className="text-sm text-gray-600">
                {best.store_name}
                {best.is_online
                  ? " · spesa online"
                  : best.distance_km != null
                  ? ` · ${best.distance_km} km`
                  : ""}
              </p>
              <p className="text-3xl font-bold mt-1 text-gray-900">
                €{best.total.toFixed(2)}
              </p>
              <p className="text-xs text-gray-500">
                {best.covered}/{result.n_findable} prodotti trovati qui
              </p>

              <div className="mt-3 flex flex-col divide-y">
                {best.items.map((it, i) => (
                  <div key={i} className="flex items-center justify-between py-1.5 text-sm">
                    <span className="text-gray-500 w-20 shrink-0">{it.query}</span>
                    <span className="flex-1 text-gray-800 truncate px-2">
                      {it.product_name}
                    </span>
                    <span className="font-semibold whitespace-nowrap">
                      €{it.price.toFixed(2)}
                    </span>
                    {it.product_url && (
                      <a
                        href={it.product_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="ml-2 text-xs bg-primary text-white px-2 py-0.5 rounded"
                      >
                        Apri
                      </a>
                    )}
                  </div>
                ))}
              </div>

              {best.shop_url && (
                <a
                  href={best.shop_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block mt-3 bg-primary text-white text-sm px-4 py-2 rounded-lg font-medium"
                >
                  Vai al negozio →
                </a>
              )}
            </div>
          )}

          {/* Assistente acquisto (handoff): piano guidato + deep-link */}
          {best && <PurchasePlan result={result} />}

          {/* Split multi-negozio se conviene */}
          {multi && multi.savings_vs_single > 0 && (
            <div className="bg-blue-50 border border-blue-300 rounded-xl p-4">
              <p className="font-bold text-blue-700 mb-1">
                💡 Dividendo su più negozi risparmi €
                {multi.savings_vs_single.toFixed(2)}
              </p>
              <p className="text-2xl font-bold text-gray-900">
                €{multi.total.toFixed(2)}
              </p>
              <div className="mt-2 flex flex-col gap-2">
                {multi.stores.map((s) => (
                  <div key={s.store_id} className="border-t border-blue-200 pt-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold">{s.chain_name}</span>
                      <span className="text-sm font-bold">€{s.subtotal.toFixed(2)}</span>
                    </div>
                    <p className="text-xs text-gray-500">
                      {s.items.map((it) => it.query).join(", ")}
                    </p>
                    {s.shop_url && (
                      <a
                        href={s.shop_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-blue-600 underline"
                      >
                        Acquista qui →
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Confronto altri negozi */}
          {result.single_ranking.length > 1 && (
            <div className="bg-white border rounded-xl p-4">
              <p className="text-sm font-semibold text-gray-700 mb-2">
                Confronto negozi (stessa lista)
              </p>
              <div className="flex flex-col gap-1">
                {result.single_ranking.map((s) => (
                  <div
                    key={s.store_id}
                    className="flex items-center justify-between text-sm"
                  >
                    <span className="text-gray-700">
                      {s.chain_name}
                      <span className="text-gray-400 text-xs">
                        {" "}
                        ({s.covered}/{result.n_findable})
                      </span>
                    </span>
                    <span className="font-semibold">€{s.total.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Non trovati */}
          {result.not_found.length > 0 && (
            <p className="text-xs text-gray-500">
              Non trovati: {result.not_found.join(", ")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
