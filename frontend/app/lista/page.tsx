"use client";
import { useEffect, useMemo, useState } from "react";
import {
  createRecurringList,
  deleteRecurringList,
  getRecurringLists,
  optimizeQuick,
  updateRecurringList,
  PlanStrategy,
  Product,
  QuickOptimizeResult,
  RecurringItemInput,
  RecurringList,
} from "@/lib/api";
import { useAppStore } from "@/lib/store";
import LocationBar from "@/components/ui/LocationBar";
import PurchasePlan from "@/components/ui/PurchasePlan";
import ProductPicker from "@/components/ui/ProductPicker";
import {
  CalendarClock,
  Calculator,
  CloudOff,
  Coins,
  ListChecks,
  Mail,
  Minus,
  PackageCheck,
  PackageSearch,
  Plus,
  RefreshCw,
  Save,
  Sparkles,
  Store,
  Trash2,
} from "lucide-react";

// Filtri del piano carrello (Fase 1 auto-carrello). L'ordine è quello dei chip.
const PLAN_STRATEGIES: { key: PlanStrategy; label: string; Icon: typeof Coins; hint: string }[] = [
  { key: "cheapest", label: "Prezzo più basso", Icon: Coins, hint: "Spendi il meno possibile, anche dividendo su più negozi." },
  { key: "availability", label: "Disponibilità", Icon: PackageCheck, hint: "Preferisci i prodotti disponibili in negozio." },
  { key: "fewest_stores", label: "Meno negozi", Icon: Store, hint: "Fai tutto in un solo negozio, più comodo." },
];

// Spesa abituale: lista ricorrente salvata via API /recurring (chiave: email).
// Ogni settimana il backend ricalcola il piano piu conveniente e lo invia via
// email. Fallback: se le API non rispondono la lista resta in localStorage.

const EMAIL_KEY = "spesasmart_email";
const LOCAL_LISTS_KEY = "spesasmart_recurring_local";
const DEFAULT_LOCATION = { lat: 45.4642, lng: 9.19, label: "Milano" };

type EditableItem = {
  uid: string;
  query: string;
  quantity: number;
  product_id?: string;
  label: string;
  image_url?: string | null;
};

type Draft = {
  id: string | null; // null = nuova; "local-..." = solo dispositivo
  name: string;
  items: EditableItem[];
};

const EMPTY_DRAFT: Draft = { id: null, name: "", items: [] };

let uidCounter = 0;
function newUid() {
  uidCounter += 1;
  return `it-${Date.now().toString(36)}-${uidCounter}`;
}

function isValidEmail(value: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value.trim());
}

function isLocalId(id: string | null) {
  return id != null && id.startsWith("local-");
}

function readLocalLists(): RecurringList[] {
  try {
    const raw = localStorage.getItem(LOCAL_LISTS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeLocalLists(lists: RecurringList[]) {
  try {
    localStorage.setItem(LOCAL_LISTS_KEY, JSON.stringify(lists));
  } catch {
    // storage pieno o disabilitato: ignoro
  }
}

function draftToRecurring(draft: Draft, id: string): RecurringList {
  return {
    id,
    name: draft.name.trim(),
    items: draft.items.map((it, i) => ({
      id: `${id}-${i}`,
      query: it.query,
      quantity: it.quantity,
      product_id: it.product_id ?? null,
      product_name_resolved: it.label !== it.query ? it.label : null,
      image_url: it.image_url ?? null,
    })),
  };
}

function recurringToDraft(list: RecurringList): Draft {
  return {
    id: list.id,
    name: list.name,
    items: (list.items || []).map((it) => ({
      uid: newUid(),
      query: it.query,
      quantity: it.quantity || 1,
      product_id: it.product_id ?? undefined,
      label: it.product_name_resolved || it.query,
      image_url: it.image_url ?? null,
    })),
  };
}

function draftItemsPayload(draft: Draft): RecurringItemInput[] {
  return draft.items.map((it) => ({
    query: it.query,
    quantity: it.quantity,
    ...(it.product_id ? { product_id: it.product_id } : {}),
  }));
}

export default function ListaPage() {
  const { location, radiusKm, setLocation } = useAppStore();
  const [email, setEmail] = useState("");
  const [cloudLists, setCloudLists] = useState<RecurringList[]>([]);
  const [localLists, setLocalLists] = useState<RecurringList[]>([]);
  const [cloudOffline, setCloudOffline] = useState(false);
  const [loadingLists, setLoadingLists] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [result, setResult] = useState<QuickOptimizeResult | null>(null);
  const [optimizing, setOptimizing] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [strategy, setStrategy] = useState<PlanStrategy>("cheapest");

  const emailOk = isValidEmail(email);

  // Email persistita: e la chiave delle liste e del digest settimanale.
  useEffect(() => {
    try {
      const stored = localStorage.getItem(EMAIL_KEY);
      if (stored) setEmail(stored);
    } catch {
      // storage non disponibile
    }
    setLocalLists(readLocalLists());
  }, []);

  useEffect(() => {
    if (!emailOk) return;
    try {
      localStorage.setItem(EMAIL_KEY, email.trim());
    } catch {
      // storage non disponibile
    }
  }, [email, emailOk]);

  const loadLists = async (targetEmail: string) => {
    if (!isValidEmail(targetEmail)) return;
    setLoadingLists(true);
    setWarning(null);
    try {
      const lists = await getRecurringLists(targetEmail.trim());
      setCloudLists(lists);
      setCloudOffline(false);
    } catch {
      setCloudLists([]);
      setCloudOffline(true);
      setWarning(
        "Salvataggio cloud non disponibile: mostro le liste salvate su questo dispositivo."
      );
    } finally {
      setLoadingLists(false);
    }
  };

  // Al primo accesso con email gia salvata, carico subito le liste.
  useEffect(() => {
    if (emailOk && cloudLists.length === 0 && !loadingLists) loadLists(email);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [emailOk]);

  const startNewList = () => {
    setDraft({ ...EMPTY_DRAFT, items: [] });
    setResult(null);
    setMessage(null);
  };

  const editList = (list: RecurringList) => {
    setDraft(recurringToDraft(list));
    setResult(null);
    setMessage(null);
  };

  const addProduct = (product: Product) => {
    setDraft((prev) => {
      if (prev.items.some((it) => it.product_id === product.id)) return prev;
      return {
        ...prev,
        items: [
          ...prev.items,
          {
            uid: newUid(),
            query: product.name,
            quantity: 1,
            product_id: product.id,
            label: product.name,
            image_url: product.image_url,
          },
        ],
      };
    });
    setResult(null);
  };

  const addGeneric = (query: string) => {
    setDraft((prev) => {
      const key = query.toLowerCase();
      if (prev.items.some((it) => !it.product_id && it.query.toLowerCase() === key)) return prev;
      return {
        ...prev,
        items: [...prev.items, { uid: newUid(), query, quantity: 1, label: query }],
      };
    });
    setResult(null);
  };

  const changeQuantity = (uid: string, delta: number) => {
    setDraft((prev) => ({
      ...prev,
      items: prev.items.map((it) =>
        it.uid === uid
          ? { ...it, quantity: Math.min(99, Math.max(1, it.quantity + delta)) }
          : it
      ),
    }));
    setResult(null);
  };

  const removeDraftItem = (uid: string) => {
    setDraft((prev) => ({ ...prev, items: prev.items.filter((it) => it.uid !== uid) }));
    setResult(null);
  };

  const saveLocally = (toSave: Draft) => {
    // Conservo l'id cloud se presente: al retry il salvataggio fara un update
    // della lista esistente invece di crearne una duplicata.
    const id = toSave.id ?? `local-${Date.now().toString(36)}`;
    const record = draftToRecurring(toSave, id);
    const next = [...readLocalLists().filter((l) => l.id !== id), record];
    writeLocalLists(next);
    setLocalLists(next);
    setDraft((prev) => ({ ...prev, id }));
    return record;
  };

  const saveList = async () => {
    const name = draft.name.trim();
    if (!emailOk) {
      setWarning("Inserisci un'email valida: e la chiave della tua spesa abituale.");
      return;
    }
    if (name.length < 2) {
      setWarning("Dai un nome alla lista, es. Spesa settimanale.");
      return;
    }
    if (draft.items.length === 0) {
      setWarning("Aggiungi almeno un prodotto prima di salvare.");
      return;
    }
    setSaving(true);
    setWarning(null);
    setMessage(null);
    try {
      const payload = draftItemsPayload(draft);
      let saved: RecurringList;
      if (draft.id && !isLocalId(draft.id)) {
        saved = await updateRecurringList(draft.id, email.trim(), name, payload);
      } else {
        saved = await createRecurringList(email.trim(), name, payload);
      }
      // Se era una copia locale (nuova o non sincronizzata), la rimuovo: ora vive nel cloud.
      if (draft.id) {
        const next = readLocalLists().filter((l) => l.id !== draft.id);
        writeLocalLists(next);
        setLocalLists(next);
      }
      setCloudOffline(false);
      // Backend vecchio: POST/PUT possono rispondere senza items. In quel caso
      // conservo le voci correnti e aggiorno solo id e nome dalla risposta.
      if (Array.isArray(saved.items) && saved.items.length > 0) {
        setDraft(recurringToDraft(saved));
      } else {
        setDraft((prev) => ({ ...prev, id: saved.id, name: saved.name || prev.name }));
      }
      setMessage(`Lista "${saved.name}" salvata: ogni settimana ricevi il piano piu conveniente via email.`);
      await loadLists(email);
    } catch {
      saveLocally(draft);
      setCloudOffline(true);
      setWarning(
        "Salvataggio cloud non disponibile: la lista e salvata solo su questo dispositivo. Riprova piu tardi per attivare il piano settimanale via email."
      );
    } finally {
      setSaving(false);
    }
  };

  const removeList = async (list: RecurringList) => {
    setWarning(null);
    if (isLocalId(list.id)) {
      const next = readLocalLists().filter((l) => l.id !== list.id);
      writeLocalLists(next);
      setLocalLists(next);
    } else {
      try {
        await deleteRecurringList(list.id, email.trim());
        setCloudLists((prev) => prev.filter((l) => l.id !== list.id));
        // Rimuovo anche l'eventuale copia locale non sincronizzata.
        const next = readLocalLists().filter((l) => l.id !== list.id);
        writeLocalLists(next);
        setLocalLists(next);
      } catch {
        setWarning("Non riesco a eliminare la lista ora: riprova piu tardi.");
        return;
      }
    }
    if (draft.id === list.id) startNewList();
  };

  const calculatePlan = async (strat: PlanStrategy = strategy) => {
    if (draft.items.length === 0) return;
    setOptimizing(true);
    setPlanError(null);
    try {
      let activeLocation = location;
      if (!activeLocation) {
        activeLocation = DEFAULT_LOCATION;
        setLocation(activeLocation);
      }
      const plan = await optimizeQuick(
        draft.items.map((it) => ({
          query: it.query,
          quantity: it.quantity,
          product_id: it.product_id,
        })),
        activeLocation.lat,
        activeLocation.lng,
        radiusKm,
        strat
      );
      setResult(plan);
    } catch {
      setPlanError(
        "Non sono riuscito a calcolare il piano: il servizio dati potrebbe non essere raggiungibile. Riprova tra poco."
      );
    } finally {
      setOptimizing(false);
    }
  };

  // Cambia filtro: se un piano è già calcolato, lo ricalcolo subito con la
  // nuova strategia; altrimenti aggiorno solo la scelta.
  const chooseStrategy = (strat: PlanStrategy) => {
    setStrategy(strat);
    if (result) calculatePlan(strat);
  };

  const allLists = useMemo(
    () => [
      ...cloudLists,
      ...localLists.filter((local) => !cloudLists.some((c) => c.id === local.id)),
    ],
    [cloudLists, localLists]
  );

  const planSummary = useMemo(() => {
    if (!result?.best_single) return null;
    if (result.strategy === "availability") {
      const inStock = result.in_stock_count ?? result.n_findable;
      return `${inStock}/${result.n_findable} prodotti disponibili nel piano`;
    }
    if (result.strategy === "fewest_stores") {
      return `Conviene fare tutto da ${result.best_single.chain_name}`;
    }
    const multiSavings = result.multi_store?.savings_vs_single || 0;
    return multiSavings > 0
      ? `Dividendo su piu negozi risparmi EUR ${multiSavings.toFixed(2)}`
      : `Conviene fare tutto da ${result.best_single.chain_name}`;
  }, [result]);

  return (
    <div className="flex flex-col gap-4">
      <LocationBar />

      <header className="flex items-start gap-3">
        <div className="w-11 h-11 rounded-xl bg-primary text-white grid place-items-center shrink-0">
          <RefreshCw size={22} />
        </div>
        <div>
          <h1 className="text-xl font-bold text-deep leading-tight">Spesa abituale</h1>
          <p className="text-sm text-stone-500">
            Salva la lista che compri sempre: la teniamo d'occhio noi.
          </p>
        </div>
      </header>

      {/* Box spiegazione: il valore della lista ricorrente */}
      <div className="rounded-card border border-primary/20 bg-primary-50/60 p-3 flex gap-2 text-sm text-deep">
        <CalendarClock size={18} className="text-primary shrink-0 mt-0.5" />
        <p>
          <strong>Ogni settimana ricalcoliamo il piano piu conveniente e te lo
          mandiamo via email</strong>: stessa lista, supermercato migliore del
          momento. Tu decidi se e quando comprare.
        </p>
      </div>

      {/* Email: chiave delle liste e destinatario del digest */}
      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card flex flex-col gap-2">
        <label className="text-sm font-semibold text-deep flex items-center gap-1.5" htmlFor="recurring-email">
          <Mail size={15} className="text-primary" /> La tua email
        </label>
        <div className="flex gap-2">
          <input
            id="recurring-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="nome@esempio.it"
            className="flex-1 border-2 border-stone-200 focus:border-primary rounded-xl px-3 py-2 text-sm outline-none"
          />
          <button
            onClick={() => loadLists(email)}
            disabled={!emailOk || loadingLists}
            className="rounded-xl bg-stone-900 text-white px-3 text-sm font-semibold disabled:opacity-50"
          >
            {loadingLists ? "Carico..." : "Le mie liste"}
          </button>
        </div>
        <p className="text-[11px] text-stone-400">
          Usiamo l'email solo per ritrovare le tue liste e inviarti il piano settimanale. Niente spam.
        </p>
      </section>

      {cloudOffline && (
        <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-xl p-3 flex gap-2">
          <CloudOff size={16} className="shrink-0 mt-0.5" />
          <span>Salvataggio cloud non disponibile: le liste restano su questo dispositivo finche il servizio non torna raggiungibile.</span>
        </p>
      )}

      {/* Liste salvate */}
      {allLists.length > 0 && (
        <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card flex flex-col gap-2">
          <p className="text-sm font-bold text-deep flex items-center gap-1.5">
            <ListChecks size={16} className="text-primary" /> Le tue liste
          </p>
          <div className="flex flex-col gap-1.5">
            {allLists.map((list) => (
              <div
                key={list.id}
                className={`flex items-center gap-2 rounded-xl border px-3 py-2 ${
                  draft.id === list.id ? "border-primary bg-primary-50" : "border-stone-200 bg-surface"
                }`}
              >
                <button onClick={() => editList(list)} className="flex-1 min-w-0 text-left">
                  <p className="text-sm font-semibold text-deep truncate">
                    {list.name}
                    {isLocalId(list.id) && (
                      <span className="ml-2 text-[10px] font-bold text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded-pill">
                        solo dispositivo
                      </span>
                    )}
                    {!isLocalId(list.id) &&
                      !cloudLists.some((c) => c.id === list.id) &&
                      localLists.some((l) => l.id === list.id) && (
                        <span className="ml-2 text-[10px] font-bold text-amber-700 bg-amber-50 border border-amber-200 px-1.5 py-0.5 rounded-pill">
                          non sincronizzata
                        </span>
                      )}
                  </p>
                  <p className="text-[11px] text-stone-400">
                    {list.items.length} prodott{list.items.length === 1 ? "o" : "i"}
                    {list.last_digest_at
                      ? ` - ultimo piano inviato: ${new Date(list.last_digest_at).toLocaleDateString("it-IT")}`
                      : ""}
                  </p>
                </button>
                <button
                  onClick={() => removeList(list)}
                  aria-label={`Elimina ${list.name}`}
                  className="text-stone-400 hover:text-red-600 shrink-0"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
          <button onClick={startNewList} className="self-start text-[12px] font-semibold text-primary hover:underline">
            + Crea una nuova lista
          </button>
        </section>
      )}

      {/* Editor lista */}
      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-bold text-deep">
            {draft.id ? "Modifica lista" : "Nuova lista"}
          </p>
          <span className="text-xs text-stone-500">{draft.items.length} voci</span>
        </div>

        <input
          value={draft.name}
          onChange={(e) => setDraft((prev) => ({ ...prev, name: e.target.value }))}
          placeholder="Nome lista, es. Spesa settimanale"
          className="border-2 border-stone-200 focus:border-primary rounded-xl px-3 py-2 text-sm outline-none"
        />

        <ProductPicker
          placeholder="Aggiungi prodotto, es. latte"
          onPickProduct={addProduct}
          onPickGeneric={addGeneric}
        />

        {draft.items.length === 0 && (
          <div className="rounded-xl border border-dashed border-stone-200 bg-surface px-3 py-4 text-sm text-stone-500">
            Cerca i prodotti che compri sempre: scegli il riferimento reale per un confronto piu preciso.
          </div>
        )}

        {draft.items.length > 0 && (
          <ul className="flex flex-col divide-y divide-stone-100 rounded-xl border border-stone-100">
            {draft.items.map((it) => (
              <li key={it.uid} className="flex items-center gap-2 px-3 py-2">
                {it.image_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={it.image_url}
                    alt=""
                    className="w-8 h-8 rounded object-contain bg-white border border-stone-100 shrink-0"
                  />
                ) : (
                  <span className="w-8 h-8 rounded bg-stone-100 grid place-items-center text-stone-400 shrink-0">
                    <PackageSearch size={14} />
                  </span>
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-stone-800 truncate">{it.label}</p>
                  <p className="text-[10px] font-bold">
                    {it.product_id ? (
                      <span className="text-primary">prodotto scelto</span>
                    ) : (
                      <span className="text-amber-700">ricerca generica</span>
                    )}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => changeQuantity(it.uid, -1)}
                    aria-label={`Riduci quantita di ${it.label}`}
                    className="w-7 h-7 rounded-lg border border-stone-200 grid place-items-center text-stone-600 active:scale-95"
                  >
                    <Minus size={13} />
                  </button>
                  <span className="w-7 text-center text-sm font-semibold tnum">{it.quantity}</span>
                  <button
                    onClick={() => changeQuantity(it.uid, 1)}
                    aria-label={`Aumenta quantita di ${it.label}`}
                    className="w-7 h-7 rounded-lg border border-stone-200 grid place-items-center text-stone-600 active:scale-95"
                  >
                    <Plus size={13} />
                  </button>
                </div>
                <button
                  onClick={() => removeDraftItem(it.uid)}
                  aria-label={`Rimuovi ${it.label}`}
                  className="text-stone-400 hover:text-red-600 shrink-0"
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* Filtro del piano: come deve ottimizzare l'app il carrello */}
        {draft.items.length > 0 && (
          <div className="rounded-xl border border-stone-200 bg-surface p-2.5">
            <p className="text-[11px] font-semibold text-stone-500 mb-1.5 px-0.5">
              Come vuoi ottimizzare?
            </p>
            <div className="grid grid-cols-3 gap-1.5">
              {PLAN_STRATEGIES.map(({ key, label, Icon }) => {
                const active = strategy === key;
                return (
                  <button
                    key={key}
                    onClick={() => chooseStrategy(key)}
                    aria-pressed={active}
                    className={`flex flex-col items-center gap-1 rounded-lg border px-2 py-2 text-center transition active:scale-[0.98] ${
                      active
                        ? "border-primary bg-primary-50 text-deep"
                        : "border-stone-200 bg-white text-stone-500"
                    }`}
                  >
                    <Icon size={17} className={active ? "text-primary" : "text-stone-400"} />
                    <span className="text-[11px] font-semibold leading-tight">{label}</span>
                  </button>
                );
              })}
            </div>
            <p className="text-[11px] text-stone-400 mt-1.5 px-0.5">
              {PLAN_STRATEGIES.find((s) => s.key === strategy)?.hint}
            </p>
          </div>
        )}

        <div className="grid gap-2 sm:grid-cols-2">
          <button
            onClick={saveList}
            disabled={saving || draft.items.length === 0}
            className="inline-flex items-center justify-center gap-2 bg-primary text-white px-4 py-2.5 rounded-xl text-sm font-bold disabled:opacity-50 active:scale-[0.99] transition"
          >
            <Save size={16} /> {saving ? "Salvo..." : "Salva spesa abituale"}
          </button>
          <button
            onClick={() => calculatePlan()}
            disabled={optimizing || draft.items.length === 0}
            className="inline-flex items-center justify-center gap-2 bg-secondary text-white px-4 py-2.5 rounded-xl text-sm font-bold disabled:opacity-50 active:scale-[0.99] transition"
          >
            <Calculator size={16} /> {optimizing ? "Calcolo..." : "Calcola piano ora"}
          </button>
        </div>
        {!location && (
          <p className="text-[11px] text-stone-400">
            Senza posizione attiva uso Milano come riferimento per il calcolo.
          </p>
        )}
      </section>

      {message && (
        <p className="text-sm text-primary-700 bg-primary-50 border border-primary/20 rounded-xl p-3">{message}</p>
      )}
      {warning && (
        <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-xl p-3">{warning}</p>
      )}
      {planError && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl p-3">{planError}</p>
      )}

      {planSummary && (
        <div className="rounded-card border border-primary/25 bg-primary-50/60 p-3 flex items-center gap-2 text-sm text-deep">
          <Sparkles size={17} className="text-primary shrink-0" />
          <span className="font-semibold">{planSummary}</span>
        </div>
      )}

      {result?.not_found?.length ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <p className="font-semibold">Da rivedere prima dell'acquisto</p>
          <p className="mt-1">
            Non ho trovato: {result.not_found.join(", ")}. Prova con nomi piu generici o scegli un riferimento reale.
          </p>
        </div>
      ) : null}

      {result && <PurchasePlan result={result} />}
    </div>
  );
}
