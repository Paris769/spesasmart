"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  Calculator,
  CheckCircle2,
  Clock,
  Info,
  PackageSearch,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Store,
  Trash2,
  Truck,
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

const EXAMPLE_PROMPTS = [
  "Fammi la spesa per la settimana",
  "Colazione per 4 persone",
  "Cena veloce: pasta, tonno, pomodori e insalata",
  "Spesa palestra con pollo, riso, uova e yogurt greco",
];

const AGENT_STEPS = [
  { icon: WandSparkles, title: "1. Creo la lista", text: "Trasformo la richiesta in prodotti modificabili." },
  { icon: Search, title: "2. Fisso i riferimenti", text: "Puoi scegliere prodotti reali per evitare match sbagliati." },
  { icon: ShoppingGuideIcon, title: "3. Preparo il carrello", text: "Scegli consegna o ritiro e apri i link ufficiali." },
];

function ShoppingGuideIcon({ size, className }: { size?: number; className?: string }) {
  return <Store size={size} className={className} />;
}

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

function cleanItemQuery(value: string) {
  return value
    .replace(/^[-*]\s*/, "")
    .replace(/\bkefyr\b/gi, "kefir")
    .replace(/\s+/g, " ")
    .trim();
}

function firstInputSegment(value: string) {
  return cleanItemQuery(value.split(/\n|,|;/)[0] || "");
}

function buildResolveSearchTerm(value: string, currentItem?: AgentItem) {
  const typed = firstInputSegment(value);
  const current = cleanItemQuery(currentItem?.query || "");
  if (!current) return typed;
  if (!typed) return current;
  const currentFirstWord = current.split(/\s+/)[0]?.toLowerCase();
  return currentFirstWord && typed.toLowerCase().includes(currentFirstWord)
    ? typed
    : `${current} ${typed}`.trim();
}

function fallbackSearchTerms(term: string, currentItem?: AgentItem) {
  const current = cleanItemQuery(currentItem?.query || "");
  const firstWord = term.split(/\s+/)[0] || "";
  return Array.from(new Set([term, current, firstWord].map(cleanItemQuery).filter((x) => x.length >= 2)));
}

function itemKey(item: AgentItem) {
  return item.product_id || cleanItemQuery(item.query).toLowerCase();
}

function uniqueItems(items: AgentItem[]): AgentItem[] {
  const byKey = new Map<string, AgentItem>();
  for (const rawItem of items) {
    const item = {
      ...rawItem,
      query: cleanItemQuery(rawItem.query),
      label: cleanItemQuery(rawItem.label || rawItem.query),
      quantity: rawItem.quantity || 1,
    };
    if (item.query.length < 2) continue;
    const key = itemKey(item);
    const existing = byKey.get(key);
    if (existing) {
      byKey.set(key, { ...existing, quantity: (existing.quantity || 1) + (item.quantity || 1) });
      continue;
    }
    byKey.set(key, item);
  }
  return Array.from(byKey.values()).slice(0, 40);
}

function textItems(items: string[]): AgentItem[] {
  return uniqueItems(items.map((query) => ({ query: cleanItemQuery(query), label: cleanItemQuery(query), quantity: 1 })));
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
    .map((x) => cleanItemQuery(x))
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

function firstGenericItem(items: AgentItem[]) {
  return items.find((item) => !item.product_id);
}

export default function AgentePage() {
  const { location, radiusKm, setLocation } = useAppStore();
  const [prompt, setPrompt] = useState("Fammi la spesa per la settimana");
  const [items, setItems] = useState<AgentItem[]>([]);
  const [manualItem, setManualItem] = useState("");
  const [resolveTarget, setResolveTarget] = useState<string | null>(null);
  const [listMessage, setListMessage] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<Product[]>([]);
  const [searching, setSearching] = useState(false);
  const [result, setResult] = useState<QuickOptimizeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingStage, setLoadingStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resolvingItem = useMemo(
    () => items.find((item) => resolveTarget && itemKey(item) === resolveTarget),
    [items, resolveTarget]
  );
  const suggestionTerm = useMemo(
    () => (resolveTarget ? buildResolveSearchTerm(manualItem, resolvingItem) : cleanItemQuery(manualItem)),
    [manualItem, resolveTarget, resolvingItem]
  );

  useEffect(() => {
    const term = suggestionTerm.trim();
    if (term.length < 2) {
      setSuggestions([]);
      setSearching(false);
      return;
    }

    let cancelled = false;
    setSearching(true);
    const handle = setTimeout(async () => {
      try {
        let found: Product[] = [];
        for (const candidate of fallbackSearchTerms(term, resolvingItem)) {
          found = await searchProducts(candidate, location?.lat, location?.lng, radiusKm);
          if (found.length > 0) break;
        }
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
  }, [suggestionTerm, resolvingItem, location, radiusKm]);

  const estimatedMode = useMemo(() => {
    if (!result?.best_single) return null;
    const multiSavings = result.multi_store?.savings_vs_single || 0;
    return multiSavings > 0
      ? `Dividendo su piu negozi risparmi EUR ${multiSavings.toFixed(2)}`
      : `Conviene fare tutto da ${result.best_single.chain_name}`;
  }, [result]);

  const focusNextGeneric = (nextItems: AgentItem[], message: string) => {
    const nextGeneric = firstGenericItem(nextItems);
    setSuggestions([]);
    setResult(null);
    if (!nextGeneric) {
      setResolveTarget(null);
      setManualItem("");
      setListMessage(message);
      return;
    }
    setResolveTarget(itemKey(nextGeneric));
    setManualItem(nextGeneric.query);
    setListMessage(`${message} Ora scegli marca e formato per "${nextGeneric.query}".`);
  };

  const generateItems = () => {
    const generated = parseRequest(prompt);
    const firstGeneric = firstGenericItem(generated);
    setItems(generated);
    setResolveTarget(firstGeneric ? itemKey(firstGeneric) : null);
    setManualItem(firstGeneric?.query || "");
    setSuggestions([]);
    setResult(null);
    setError(null);
    setLoadingStage(null);
    setListMessage(
      generated.length > 0
        ? `Lista generata: ${generated.length} prodotti. Scegli marca e formato per i prodotti ambigui.`
        : "Non ho capito la richiesta: scrivi almeno un prodotto o una richiesta tipo spesa per la settimana."
    );
  };

  const addItem = (item: AgentItem) => {
    setItems((prev) => uniqueItems([...prev, item]));
    setManualItem("");
    setResolveTarget(null);
    setSuggestions([]);
    setResult(null);
    setListMessage(null);
  };

  const useManualAsGeneric = () => {
    const query = resolveTarget ? suggestionTerm : cleanItemQuery(manualItem);
    if (query.length < 2) return;
    if (resolveTarget) {
      const nextItems = uniqueItems(
        items.map((item) =>
          itemKey(item) === resolveTarget ? { query, label: query, quantity: 1 } : item
        )
      );
      setItems(nextItems);
      focusNextGeneric(nextItems, `Usero "${query}" come ricerca generica: il match sara meno preciso.`);
      return;
    }
    addItem({ query, label: query, quantity: 1 });
  };

  const addProduct = (product: Product) => {
    if (!hasProductPrice(product)) return;
    const selected: AgentItem = {
      query: product.name,
      label: product.name,
      product_id: product.id,
      image_url: product.image_url,
      brand: product.brand,
      quantity: 1,
    };
    if (resolveTarget) {
      const nextItems = uniqueItems(
        items.map((item) => (itemKey(item) === resolveTarget ? selected : item))
      );
      setItems(nextItems);
      focusNextGeneric(nextItems, `Prodotto scelto: ${product.name}.`);
      return;
    }
    addItem(selected);
  };

  const startResolveItem = (item: AgentItem) => {
    setResolveTarget(itemKey(item));
    setManualItem(item.query);
    setSuggestions([]);
    setListMessage(`Stai scegliendo marca e formato per "${item.query}".`);
  };

  const removeItem = (key: string) => {
    setItems((prev) => prev.filter((x) => itemKey(x) !== key));
    if (resolveTarget === key) {
      setResolveTarget(null);
      setManualItem("");
      setSuggestions([]);
    }
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
    setLoadingStage("Controllo posizione e zona di consegna...");
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

      setLoadingStage("Cerco prodotti, prezzi e disponibilita nelle catene configurate...");
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
      setLoadingStage("Costruisco il piano migliore per consegna o ritiro...");
      setResult(plan);
    } catch {
      setError("Non sono riuscito a preparare il piano: il servizio dati potrebbe non essere raggiungibile o alcuni prodotti sono troppo generici. Riprova tra poco, scegli Milano o seleziona riferimenti reali dalla lista.");
    } finally {
      setLoading(false);
      setLoadingStage(null);
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

      <section className="grid gap-2 md:grid-cols-3">
        {AGENT_STEPS.map((step) => {
          const Icon = step.icon;
          return (
            <div key={step.title} className="rounded-card border border-stone-200 bg-white p-3 shadow-card flex gap-2">
              <Icon size={17} className="text-primary shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-bold text-deep">{step.title}</p>
                <p className="text-[12px] text-stone-500">{step.text}</p>
              </div>
            </div>
          );
        })}
      </section>

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
        <div className="flex items-center justify-between gap-2">
          <label className="text-sm font-semibold text-deep" htmlFor="agent-prompt">
            Cosa devo preparare?
          </label>
          <span className="text-[11px] text-stone-400">puoi scrivere come parleresti a una persona</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {EXAMPLE_PROMPTS.map((example) => (
            <button
              key={example}
              onClick={() => {
                setPrompt(example);
                setListMessage(null);
                setResult(null);
              }}
              className="rounded-pill border border-stone-200 bg-surface px-3 py-1.5 text-[12px] font-medium text-stone-700 hover:border-primary hover:text-primary active:scale-[0.98] transition"
            >
              {example}
            </button>
          ))}
        </div>
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
            Premi Genera lista: l'agente crea una proposta. Poi scegli i riferimenti reali per i prodotti piu ambigui.
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          {items.map((it) => {
            const key = itemKey(it);
            const isResolving = resolveTarget === key;
            return (
              <span
                key={key}
                className={`inline-flex items-center gap-1.5 rounded-full border pl-1.5 pr-2 py-1 text-sm max-w-full ${
                  isResolving ? "border-primary bg-primary-50" : "border-stone-200 bg-surface"
                }`}
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
                {it.product_id ? (
                  <span className="text-[10px] text-primary font-bold">scelto</span>
                ) : (
                  <button
                    onClick={() => startResolveItem(it)}
                    aria-label={`Scegli marca e formato per ${it.label || it.query}`}
                    className="text-[10px] text-primary font-bold hover:underline"
                  >
                    {isResolving ? "in scelta" : "scegli"}
                  </button>
                )}
                <button
                  onClick={() => removeItem(key)}
                  aria-label={`Rimuovi ${it.label || it.query}`}
                  className="text-stone-400 hover:text-red-600 shrink-0"
                >
                  <Trash2 size={13} />
                </button>
              </span>
            );
          })}
        </div>

        <div className="relative flex flex-col gap-2">
          <div className="flex gap-2">
            <input
              value={manualItem}
              onChange={(e) => setManualItem(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && useManualAsGeneric()}
              className="flex-1 border-2 border-stone-200 focus:border-primary rounded-xl px-3 py-2 text-sm outline-none"
              placeholder={resolveTarget ? `Scegli marca e formato per ${manualItem || "prodotto"}` : "Aggiungi prodotto, es. latte"}
            />
            <button
              onClick={useManualAsGeneric}
              className="w-11 rounded-xl bg-stone-900 text-white grid place-items-center"
              aria-label={resolveTarget ? "Usa testo come ricerca generica per la voce selezionata" : "Aggiungi prodotto"}
            >
              <Plus size={18} />
            </button>
          </div>

          {suggestionTerm.length >= 2 && (searching || suggestions.length > 0) && (
            <div className="bg-white border border-stone-200 rounded-xl shadow-float overflow-hidden max-h-[56vh] overflow-y-auto">
              <p className="px-4 py-2 text-[12px] font-medium text-primary bg-primary-50 border-b border-primary/10">
                {resolveTarget ? `Scegli quale ${suggestionTerm} vuoi: marca, formato e prezzo reale.` : "Riferimenti reali: scegli un prodotto preciso se vuoi evitare match sbagliati nel piano."}
              </p>
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
                      <p className="text-[10px] text-primary font-medium">match prodotto reale</p>
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
                onClick={useManualAsGeneric}
                className="w-full px-3 py-2 text-left text-[12px] text-stone-500 hover:bg-surface"
              >
                + Usa "{suggestionTerm}" come ricerca generica
              </button>
            </div>
          )}
          {suggestionTerm.length >= 2 && !searching && suggestions.length === 0 && (
            <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-800">
              Non vedo ancora un riferimento preciso: puoi usare il termine come ricerca generica, ma il match sara meno sicuro.
            </p>
          )}
        </div>
      </section>

      {items.length > 0 && (
        <div className="rounded-card border border-stone-200 bg-white p-3 shadow-card flex items-start gap-2 text-sm text-stone-600">
          <CheckCircle2 size={17} className="text-primary shrink-0 mt-0.5" />
          <p>
            Prima preparo il piano, poi ti faccio scegliere consegna a casa, ritiro in negozio o ritiro tramite incaricato. I prodotti non trovati verranno segnalati prima del carrello.
          </p>
        </div>
      )}

      <button
        onClick={runAgent}
        disabled={items.length === 0 || loading}
        className="inline-flex items-center justify-center gap-2 bg-secondary text-white px-4 py-3 rounded-xl text-sm font-bold disabled:opacity-50 active:scale-[0.99] transition"
      >
        {loading ? (
          <>{loadingStage || "Calcolo in corso..."}</>
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

      {loadingStage && (
        <div className="rounded-xl border border-primary/20 bg-primary-50 px-3 py-2 text-sm text-primary-700 flex items-center gap-2">
          <Clock size={16} className="shrink-0" /> {loadingStage}
        </div>
      )}

      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl p-3">{error}</p>}

      {estimatedMode && (
        <div className="rounded-card border border-primary/25 bg-primary-50/60 p-3 flex items-center gap-2 text-sm text-deep">
          <Sparkles size={17} className="text-primary shrink-0" />
          <span className="font-semibold">{estimatedMode}</span>
        </div>
      )}

      {result && (
        <div className="rounded-card border border-stone-200 bg-white p-3 shadow-card flex items-start gap-2 text-sm text-stone-600">
          <Truck size={17} className="text-primary shrink-0 mt-0.5" />
          <p>
            Ho trovato {result.n_findable} prodotti su {result.n_items}. Ora scegli consegna o ritiro: mostro minimi, servizi e link ufficiali prima del carrello.
          </p>
        </div>
      )}

      {result?.not_found?.length ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <p className="font-semibold">Da rivedere prima dell'acquisto</p>
          <p className="mt-1">Non ho trovato: {result.not_found.join(", ")}. Prova con nomi piu generici o scegli un riferimento reale dalla lista.</p>
        </div>
      ) : null}

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
