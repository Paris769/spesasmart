"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  Calculator,
  Info,
  PackageSearch,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  WandSparkles,
} from "lucide-react";
import LocationBar from "@/components/ui/LocationBar";
import PurchasePlan from "@/components/ui/PurchasePlan";
import { optimizeQuick, Product, QuickOptimizeResult, searchProducts } from "@/lib/api";
import { useAppStore } from "@/lib/store";

type AgentItem = {
  query: string;
  quantity: number;
  product_id?: string;
  label?: string;
  image_url?: string | null;
  brand?: string | null;
};

const WEEKLY_BASICS = [
  "latte",
  "pasta",
  "riso",
  "pane",
  "uova",
  "petto di pollo",
  "pomodori",
  "insalata",
  "mele",
  "banane",
  "yogurt",
  "biscotti",
  "acqua",
  "caffe",
  "olio",
  "carta igienica",
];

const DINNER_BASICS = ["pasta", "passata", "parmigiano", "insalata", "pane"];
const BREAKFAST_BASICS = ["latte", "caffe", "biscotti", "yogurt", "cereali"];
const CLEANING_BASICS = ["detersivo", "carta igienica", "ammorbidente", "spugne"];
const DEFAULT_LOCATION = { lat: 45.4642, lng: 9.19, label: "Milano" };

const KEYWORD_ITEMS: Record<string, string[]> = {
  colazione: BREAKFAST_BASICS,
  cena: DINNER_BASICS,
  pranzo: ["pasta", "tonno", "pomodori", "insalata"],
  settimana: WEEKLY_BASICS,
  casa: CLEANING_BASICS,
  pulizia: CLEANING_BASICS,
  bambini: ["latte", "yogurt", "biscotti", "pasta", "frutta"],
  palestra: ["petto di pollo", "riso", "uova", "yogurt greco", "banane"],
};

function itemKey(item: AgentItem) {
  return item.product_id || item.query.toLowerCase();
}

function uniqueItems(items: AgentItem[]): AgentItem[] {
  const seen = new Set<string>();
  return items
    .map((item) => ({ ...item, query: item.query.trim(), label: item.label?.trim() || item.query.trim() }))
    .filter((item) => item.query.length >= 2)
    .filter((item) => {
      const key = itemKey(item);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 40);
}

function textItems(items: string[]): AgentItem[] {
  return uniqueItems(items.map((query) => ({ query, label: query, quantity: 1 })));
}

function normalizePrompt(text: string) {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9\s,;\n-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function parseRequest(text: string): AgentItem[] {
  const clean = normalizePrompt(text);
  if (!clean) return [];

  const explicit = text
    .split(/\n|,|;/)
    .map((x) => x.replace(/^[-*]\s*/, "").trim())
    .filter((x) => x.length >= 2);

  if (explicit.length >= 2) return textItems(explicit);

  if (
    clean.includes("settiman") ||
    clean.includes("settimam") ||
    clean.includes("spesa per la sett") ||
    clean.includes("spesa della sett") ||
    clean === "fammi la spesa" ||
    clean === "fai la spesa"
  ) {
    return textItems(WEEKLY_BASICS);
  }

  const matched: string[] = [];
  for (const [keyword, keywordItems] of Object.entries(KEYWORD_ITEMS)) {
    if (clean.includes(keyword)) matched.push(...keywordItems);
  }

  if (matched.length) return textItems(matched);

  const fallback = text
    .split(/\s+e\s+|\s+con\s+|,/)
    .map((x) => x.trim())
    .filter((x) => x.length >= 2 && !/^fammi\s+la\s+spesa/i.test(x));
  return textItems(fallback.length ? fallback : [text]);
}

function hasProductPrice(p: Product) {
  return p.min_price != null && (p.price_store_count ?? 0) > 0;
}

export default function AgentePage() {
  const { location, radiusKm, setLocation } = useAppStore();
  const [prompt, setPrompt] = useState("Fammi la spesa per la settimana");
  const [items, setItems] = useState<AgentItem[]>([]);
  const [manualItem, setManualItem] = useState("");
  const [listMessage, setListMessage] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<Product[]>([]);
  const [searching, setSearching] = useState(false);
  const [result, setResult] = useState<QuickOptimizeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const term = manualItem.trim();
    if (term.length < 2) {
      setSuggestions([]);
      setSearching(false);
      return;
    }

    setSearching(true);
    const handle = setTimeout(async () => {
      try {
        const found = await searchProducts(term, location?.lat, location?.lng, radiusKm);
        setSuggestions(found.slice(0, 8));
      } catch {
        setSuggestions([]);
      } finally {
        setSearching(false);
      }
    }, 250);

    return () => clearTimeout(handle);
  }, [manualItem, location, radiusKm]);

  const estimatedMode = useMemo(() => {
    if (!result?.best_single) return null;
    const multiSavings = result.multi_store?.savings_vs_single || 0;
    return multiSavings > 0
      ? `Dividendo su piu negozi risparmi EUR ${multiSavings.toFixed(2)}`
      : `Conviene fare tutto da ${result.best_single.chain_name}`;
  }, [result]);

  const generateItems = () => {
    const generated = parseRequest(prompt);
    setItems(generated);
    setResult(null);
    setError(null);
    setListMessage(
      generated.length > 0
        ? `Lista generata: ${generated.length} prodotti. Ora puoi scegliere riferimenti reali o preparare il piano.`
        : "Non ho capito la richiesta: scrivi almeno un prodotto o una richiesta tipo spesa per la settimana."
    );
  };

  const addItem = (item: AgentItem) => {
    setItems((prev) => uniqueItems([...prev, item]));
    setManualItem("");
    setSuggestions([]);
    setResult(null);
    setListMessage(null);
  };

  const addManualItem = () => {
    const query = manualItem.trim();
    if (query.length < 2) return;
    addItem({ query, label: query, quantity: 1 });
  };

  const addProduct = (product: Product) => {
    if (!hasProductPrice(product)) return;
    addItem({
      query: product.name,
      label: product.name,
      product_id: product.id,
      image_url: product.image_url,
      brand: product.brand,
      quantity: 1,
    });
  };

  const removeItem = (key: string) => {
    setItems((prev) => prev.filter((x) => itemKey(x) !== key));
    setResult(null);
  };

  const requestCurrentLocation = () =>
    new Promise<{ lat: number; lng: number; label: string }>((resolve, reject) => {
      if (!navigator.geolocation) {
        reject(new Error("Geolocalizzazione non supportata"));
        return;
      }

      navigator.geolocation.getCurrentPosition(
        (pos) =>
          resolve({
            lat: pos.coords.latitude,
            lng: pos.coords.longitude,
            label: "Posizione attuale",
          }),
        () => reject(new Error("Posizione non disponibile")),
        { timeout: 10000, maximumAge: 60000 }
      );
    });

  const runAgent = async () => {
    if (items.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      let activeLocation = location;
      if (!activeLocation) {
        try {
          activeLocation = await requestCurrentLocation();
        } catch {
          activeLocation = DEFAULT_LOCATION;
        }
        setLocation(activeLocation);
      }

      const plan = await optimizeQuick(
        items.map((item) => ({
          query: item.query,
          quantity: item.quantity,
          product_id: item.product_id,
        })),
        activeLocation.lat,
        activeLocation.lng,
        radiusKm
      );
      setResult(plan);
    } catch {
      setError("Non sono riuscito a preparare il piano. Riprova tra poco o scegli una citta.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <LocationBar />

      <header className="flex items-start gap-3">
        <div className="w-11 h-11 rounded-xl bg-primary text-white grid place-items-center shrink-0">
          <Bot size={22} />
        </div>
        <div>
          <h1 className="text-xl font-bold text-deep leading-tight">Agente spesa</h1>
          <p className="text-sm text-stone-500">
            Trasforma una richiesta in lista, piano negozio e acquisto guidato.
          </p>
        </div>
      </header>

      <div className="rounded-card border border-amber-200 bg-amber-50 px-3 py-2 flex gap-2 text-xs text-amber-900">
        <Info size={16} className="mt-0.5 shrink-0" />
        <p>
          SpesaSmart confronta le catene con dati disponibili: Carrefour, Conad,
          Esselunga, Famila, Il Gigante, Iper, Coop/Ipercoop, Lidl, Eurospin,
          Aldi, MD, Penny e Pam. Alcuni prezzi, negozi o offerte possono non
          comparire per zona, prodotto, disponibilita online o aggiornamento dati.
        </p>
      </div>

      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card flex flex-col gap-3">
        <label className="text-sm font-semibold text-deep" htmlFor="agent-prompt">
          Cosa devo preparare?
        </label>
        <textarea
          id="agent-prompt"
          value={prompt}
          onChange={(e) => {
            setPrompt(e.target.value);
            setListMessage(null);
          }}
          rows={3}
          className="w-full border-2 border-stone-200 focus:border-primary rounded-xl px-3 py-2 outline-none transition text-sm"
          placeholder="Es. Fammi la spesa per la settimana, oppure latte, pasta, uova..."
        />
        <button
          onClick={generateItems}
          className="inline-flex items-center justify-center gap-2 bg-primary text-white px-4 py-2.5 rounded-xl text-sm font-bold active:scale-[0.99] transition"
        >
          <WandSparkles size={17} /> {items.length ? "Rigenera lista" : "Genera lista"}
        </button>
        {listMessage && (
          <p className={`text-sm rounded-xl px-3 py-2 border ${
            items.length
              ? "text-primary-700 bg-primary-50 border-primary/20"
              : "text-amber-700 bg-amber-50 border-amber-200"
          }`}>
            {listMessage}
          </p>
        )}
      </section>

      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-sm font-bold text-deep">Lista proposta</p>
            <p className="text-xs text-stone-400">
              Scrivi un prodotto e scegli il riferimento reale quando compare.
            </p>
          </div>
          <span className="text-xs text-stone-500">{items.length} voci</span>
        </div>

        {items.length === 0 && (
          <div className="rounded-xl border border-dashed border-stone-200 bg-surface px-3 py-4 text-sm text-stone-500">
            Premi Genera lista: l'agente trasformera la richiesta in prodotti modificabili.
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          {items.map((it) => (
            <span
              key={itemKey(it)}
              className="inline-flex items-center gap-1.5 rounded-full border border-stone-200 bg-surface pl-1.5 pr-2 py-1 text-sm max-w-full"
            >
              {it.image_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={it.image_url}
                  alt=""
                  className="w-6 h-6 rounded-full object-contain bg-white border border-stone-100 shrink-0"
                />
              ) : (
                <span className="w-6 h-6 rounded-full bg-stone-100 grid place-items-center text-stone-400 shrink-0">
                  <PackageSearch size={13} />
                </span>
              )}
              <span className="truncate max-w-[190px]">{it.label || it.query}</span>
              {it.product_id && <span className="text-[10px] text-primary font-bold">scelto</span>}
              <button
                onClick={() => removeItem(itemKey(it))}
                aria-label={`Rimuovi ${it.label || it.query}`}
                className="text-stone-400 hover:text-red-600 shrink-0"
              >
                <Trash2 size={13} />
              </button>
            </span>
          ))}
        </div>

        <div className="relative flex flex-col gap-2">
          <div className="flex gap-2">
            <input
              value={manualItem}
              onChange={(e) => setManualItem(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addManualItem()}
              className="flex-1 border-2 border-stone-200 focus:border-primary rounded-xl px-3 py-2 text-sm outline-none"
              placeholder="Aggiungi prodotto, es. latte"
            />
            <button
              onClick={addManualItem}
              className="w-11 rounded-xl bg-stone-900 text-white grid place-items-center"
              aria-label="Aggiungi prodotto"
            >
              <Plus size={18} />
            </button>
          </div>

          {manualItem.trim().length >= 2 && (searching || suggestions.length > 0) && (
            <div className="bg-white border border-stone-200 rounded-xl shadow-float overflow-hidden max-h-[56vh] overflow-y-auto">
              {searching && suggestions.length === 0 && (
                <p className="px-4 py-3 text-sm text-stone-400">Cerco prodotti...</p>
              )}
              {suggestions.map((product) => {
                const hasPrice = hasProductPrice(product);
                return (
                  <button
                    key={product.id}
                    onClick={() => hasPrice && addProduct(product)}
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
                      <p className="text-sm text-stone-800 leading-snug line-clamp-2">
                        {product.name}
                      </p>
                      {product.brand && <p className="text-[11px] text-stone-400">{product.brand}</p>}
                    </div>
                    {hasPrice ? (
                      <div className="text-right shrink-0">
                        <p className="text-sm font-semibold text-deep tnum">
                          da EUR {Number(product.min_price).toFixed(2)}
                        </p>
                        <p className="text-[10px] text-stone-400">
                          {product.price_store_count} negoz{product.price_store_count! > 1 ? "i" : "io"}
                        </p>
                      </div>
                    ) : (
                      <span className="text-[11px] font-medium text-stone-400 shrink-0">
                        nessun prezzo
                      </span>
                    )}
                  </button>
                );
              })}
              <button
                onClick={addManualItem}
                className="w-full px-3 py-2 text-left text-[12px] text-stone-500 hover:bg-surface"
              >
                + Usa "{manualItem.trim()}" come ricerca generica
              </button>
            </div>
          )}
        </div>
      </section>

      <button
        onClick={runAgent}
        disabled={items.length === 0 || loading}
        className="inline-flex items-center justify-center gap-2 bg-secondary text-white px-4 py-3 rounded-xl text-sm font-bold disabled:opacity-50 active:scale-[0.99] transition"
      >
        {loading ? (
          <>Calcolo in corso...</>
        ) : (
          <>
            <Calculator size={18} /> {location ? "Prepara piano automatico" : "Usa posizione e prepara piano"}
          </>
        )}
      </button>

      {!location && (
        <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-xl p-3">
          Premi il pulsante: usero la tua posizione se disponibile, altrimenti Milano come riferimento.
        </p>
      )}

      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl p-3">{error}</p>}

      {estimatedMode && (
        <div className="rounded-card border border-primary/25 bg-primary-50/60 p-3 flex items-center gap-2 text-sm text-deep">
          <Sparkles size={17} className="text-primary shrink-0" />
          <span className="font-semibold">{estimatedMode}</span>
        </div>
      )}

      {result && <PurchasePlan result={result} />}

      <section className="rounded-card border border-stone-200 bg-white p-4 flex gap-2 text-[12px] text-stone-500">
        <ShieldCheck size={16} className="text-primary shrink-0 mt-0.5" />
        <p>
          L'agente prepara lista, confronto prezzi e link ufficiali. Non invia ordini,
          non paga e non usa credenziali senza una conferma esplicita dell'utente.
        </p>
      </section>
    </div>
  );
}
