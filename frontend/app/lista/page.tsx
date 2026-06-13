"use client";
import { useState } from "react";
import { optimizeQuick, QuickOptimizeResult } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import LocationBar from "@/components/ui/LocationBar";
import PurchasePlan from "@/components/ui/PurchasePlan";

export default function ListaPage() {
  const { location, radiusKm } = useAppStore();
  const [text, setText] = useState("");
  const [items, setItems] = useState<string[]>([]);
  const [result, setResult] = useState<QuickOptimizeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addFromText = () => {
    // accetta "latte, pasta, caffè" oppure una voce per riga
    const parts = text
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter((s) => s.length >= 2);
    if (parts.length) {
      setItems((prev) => Array.from(new Set([...prev, ...parts])));
      setText("");
      setResult(null);
    }
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
        items.map((q) => ({ query: q, quantity: 1 })),
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
          Scrivi cosa ti serve: ti dico <b>dove costa meno</b> tra i negozi vicini.
        </p>
      </div>

      {/* Input voci */}
      <div className="flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addFromText()}
          placeholder="Es. latte, pasta, caffè, olio…"
          className="flex-1 border-2 border-gray-200 focus:border-primary rounded-xl px-4 py-2 outline-none transition"
        />
        <button
          onClick={addFromText}
          className="bg-primary text-white px-4 rounded-xl font-medium"
        >
          Aggiungi
        </button>
      </div>

      {/* Chip voci */}
      {items.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {items.map((it, i) => (
            <span
              key={i}
              className="bg-white border rounded-full pl-3 pr-2 py-1 text-sm flex items-center gap-1"
            >
              {it}
              <button
                onClick={() => removeItem(i)}
                className="text-gray-400 hover:text-red-500 font-bold px-1"
                aria-label={`Rimuovi ${it}`}
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
