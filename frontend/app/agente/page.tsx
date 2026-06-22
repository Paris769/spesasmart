"use client";

import { useMemo, useState } from "react";
import {
  Bot,
  Calculator,
  Plus,
  ShieldCheck,
  Sparkles,
  Trash2,
  WandSparkles,
} from "lucide-react";
import LocationBar from "@/components/ui/LocationBar";
import PurchasePlan from "@/components/ui/PurchasePlan";
import { optimizeQuick, QuickOptimizeResult } from "@/lib/api";
import { useAppStore } from "@/lib/store";

type AgentItem = {
  query: string;
  quantity: number;
};

const WEEKLY_BASICS = [
  "latte",
  "pasta",
  "riso",
  "pane",
  "uova",
  "pollo",
  "pomodori",
  "insalata",
  "mele",
  "yogurt",
  "acqua",
  "caffe",
];

const DINNER_BASICS = ["pasta", "passata", "parmigiano", "insalata", "pane"];
const BREAKFAST_BASICS = ["latte", "caffe", "biscotti", "yogurt", "cereali"];
const CLEANING_BASICS = ["detersivo", "carta igienica", "ammorbidente", "spugne"];

const KEYWORD_ITEMS: Record<string, string[]> = {
  colazione: BREAKFAST_BASICS,
  cena: DINNER_BASICS,
  pranzo: ["pasta", "tonno", "pomodori", "insalata"],
  settimana: WEEKLY_BASICS,
  casa: CLEANING_BASICS,
  pulizia: CLEANING_BASICS,
  bambini: ["latte", "yogurt", "biscotti", "pasta", "frutta"],
  palestra: ["pollo", "riso", "uova", "yogurt greco", "banane"],
};

function uniqueItems(items: string[]): AgentItem[] {
  const seen = new Set<string>();
  return items
    .map((x) => x.trim())
    .filter((x) => x.length >= 2)
    .filter((x) => {
      const key = x.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 40)
    .map((query) => ({ query, quantity: 1 }));
}

function parseRequest(text: string): AgentItem[] {
  const clean = text.toLowerCase();
  const explicit = text
    .split(/[\n,;]+/)
    .map((x) => x.replace(/^[-*]\s*/, "").trim())
    .filter((x) => x.length >= 2);

  if (explicit.length >= 2) return uniqueItems(explicit);

  const matched: string[] = [];
  for (const [keyword, items] of Object.entries(KEYWORD_ITEMS)) {
    if (clean.includes(keyword)) matched.push(...items);
  }

  if (matched.length) return uniqueItems(matched);
  return uniqueItems(text.split(/\s+e\s+|\s+con\s+|,/));
}

export default function AgentePage() {
  const { location, radiusKm, setLocation } = useAppStore();
  const [prompt, setPrompt] = useState("Fammi la spesa per la settimana");
  const [items, setItems] = useState<AgentItem[]>(() => parseRequest("spesa per la settimana"));
  const [manualItem, setManualItem] = useState("");
  const [result, setResult] = useState<QuickOptimizeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const estimatedMode = useMemo(() => {
    if (!result?.best_single) return null;
    const multiSavings = result.multi_store?.savings_vs_single || 0;
    return multiSavings > 0
      ? `Dividendo su piu negozi risparmi EUR ${multiSavings.toFixed(2)}`
      : `Conviene fare tutto da ${result.best_single.chain_name}`;
  }, [result]);

  const generateItems = () => {
    setItems(parseRequest(prompt));
    setResult(null);
    setError(null);
  };

  const addManualItem = () => {
    const query = manualItem.trim();
    if (query.length < 2) return;
    setItems((prev) => uniqueItems([...prev.map((x) => x.query), query]));
    setManualItem("");
    setResult(null);
  };

  const removeItem = (query: string) => {
    setItems((prev) => prev.filter((x) => x.query !== query));
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
        activeLocation = await requestCurrentLocation();
        setLocation(activeLocation);
      }

      const plan = await optimizeQuick(items, activeLocation.lat, activeLocation.lng, radiusKm);
      setResult(plan);
    } catch {
      setError(
        "Non sono riuscito a preparare il piano. Attiva la posizione e riprova."
      );
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

      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card flex flex-col gap-3">
        <label className="text-sm font-semibold text-deep" htmlFor="agent-prompt">
          Cosa devo preparare?
        </label>
        <textarea
          id="agent-prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          className="w-full border-2 border-stone-200 focus:border-primary rounded-xl px-3 py-2 outline-none transition text-sm"
          placeholder="Es. Fammi la spesa per la settimana, oppure latte, pasta, uova..."
        />
        <button
          onClick={generateItems}
          className="inline-flex items-center justify-center gap-2 bg-primary text-white px-4 py-2.5 rounded-xl text-sm font-bold active:scale-[0.99] transition"
        >
          <WandSparkles size={17} /> Genera lista
        </button>
      </section>

      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-sm font-bold text-deep">Lista proposta</p>
            <p className="text-xs text-stone-400">Puoi modificarla prima di far lavorare l'agente.</p>
          </div>
          <span className="text-xs text-stone-500">{items.length} voci</span>
        </div>

        <div className="flex flex-wrap gap-2">
          {items.map((it) => (
            <span
              key={it.query}
              className="inline-flex items-center gap-1.5 rounded-full border border-stone-200 bg-surface px-2 py-1 text-sm"
            >
              {it.query}
              <button
                onClick={() => removeItem(it.query)}
                aria-label={`Rimuovi ${it.query}`}
                className="text-stone-400 hover:text-red-600"
              >
                <Trash2 size={13} />
              </button>
            </span>
          ))}
        </div>

        <div className="flex gap-2">
          <input
            value={manualItem}
            onChange={(e) => setManualItem(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addManualItem()}
            className="flex-1 border-2 border-stone-200 focus:border-primary rounded-xl px-3 py-2 text-sm outline-none"
            placeholder="Aggiungi prodotto"
          />
          <button
            onClick={addManualItem}
            className="w-11 rounded-xl bg-stone-900 text-white grid place-items-center"
            aria-label="Aggiungi prodotto"
          >
            <Plus size={18} />
          </button>
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
          Premi il pulsante: ti chiedero la posizione e poi confrontero i negozi vicini.
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
