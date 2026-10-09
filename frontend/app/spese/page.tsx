"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BadgePercent,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Loader2,
  Mail,
  Plus,
  Receipt,
  Save,
  ShoppingBasket,
  Trash2,
  Upload,
} from "lucide-react";
import LocationBar from "@/components/ui/LocationBar";
import ProductPicker from "@/components/ui/ProductPicker";
import RebuildResult from "@/components/ui/RebuildResult";
import { useAppStore } from "@/lib/store";
import { DEFAULT_LOCATION } from "@/lib/location";
import { isValidEmail, readStoredEmail, storeEmail } from "@/lib/email";
import {
  apiError,
  ChainCoverage,
  createPurchase,
  getChainsCoverage,
  getLastPurchase,
  getPurchase,
  getPurchases,
  isMissingEndpoint,
  parseReceiptFor,
  Product,
  PurchaseDetail,
  PurchaseItemInput,
  PurchaseSummary,
  purchaseItemsToRebuildItems,
  rebuildFromItems,
  RebuildResultData,
  ReceiptResult,
} from "@/lib/api";

/**
 * Le mie spese: storico, caricamento scontrino e inserimento manuale.
 *
 * Senza uno storico l'assistente non puo' "rifare l'ultima spesa": questa
 * pagina e' il modo per riempirlo. Tutte le chiamate sono difensive: il
 * backend si deploya a parte, quindi /purchases, /receipts/parse e
 * /rebuild/from-items possono ancora non esistere (404/405) e in quel caso
 * si spiega la situazione invece di mostrare un errore tecnico.
 */

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

type ManualItem = {
  uid: string;
  name: string;
  brand?: string;
  quantity: number;
  product_id?: string;
  image_url?: string | null;
};

let uidSeq = 0;
function newUid() {
  uidSeq += 1;
  return `m${uidSeq}`;
}

function formatDay(value?: string | null) {
  if (!value) return "data non indicata";
  const parts = value.slice(0, 10).split("-");
  return parts.length === 3 ? `${parts[2]}/${parts[1]}/${parts[0]}` : value;
}

function eur(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value)
    ? `EUR ${value.toFixed(2)}`
    : null;
}

function todayIso() {
  const now = new Date();
  const m = `${now.getMonth() + 1}`.padStart(2, "0");
  const d = `${now.getDate()}`.padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

function receiptToItems(receipt: ReceiptResult): PurchaseItemInput[] {
  return (receipt.items || [])
    .filter((it) => !it.is_discount && (it.name || "").trim().length >= 2)
    .map((it) => {
      const row: PurchaseItemInput = { name: it.name.trim() };
      const brand = it.matched_product?.brand;
      if (brand) row.brand = brand;
      if (it.quantity && it.quantity > 0) row.quantity = it.quantity;
      if (it.unit_price && it.unit_price > 0) row.unit_price = it.unit_price;
      if (it.matched_product?.id) row.product_id = it.matched_product.id;
      return row;
    });
}

function manualToItems(items: ManualItem[]): PurchaseItemInput[] {
  return items
    .filter((it) => it.name.trim().length >= 2)
    .map((it) => {
      const row: PurchaseItemInput = { name: it.name.trim() };
      if (it.brand) row.brand = it.brand;
      if (it.quantity > 0) row.quantity = it.quantity;
      if (it.product_id) row.product_id = it.product_id;
      return row;
    });
}

/** Messaggio utente per gli errori di rete/backend, senza dettagli tecnici. */
function friendlyError(err: unknown, missingLabel: string) {
  const info = apiError(err);
  if (isMissingEndpoint(info)) return missingLabel;
  if (info.timeout || info.network)
    return "Il servizio non ha risposto in tempo: era in pausa e si sta riavviando. Riprova tra qualche secondo.";
  if (info.status === 429) return "Troppe richieste di seguito: riprova tra un minuto.";
  return "Operazione non riuscita. Riprova tra poco.";
}

const MISSING_PURCHASES =
  "Lo storico spese non e' ancora attivo sul server: il backend viene aggiornato a parte, riprova piu' tardi.";
const MISSING_REBUILD =
  "La funzione \"rifai la spesa con le offerte\" non e' ancora attiva sul server: riprova piu' tardi.";
const MISSING_RECEIPT =
  "La lettura degli scontrini non e' ancora attiva sul server: riprova piu' tardi.";

export default function SpesePage() {
  const { location, radiusKm } = useAppStore();

  const [email, setEmail] = useState("");
  const [emailSaved, setEmailSaved] = useState(false);

  const [purchases, setPurchases] = useState<PurchaseSummary[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [chains, setChains] = useState<ChainCoverage[]>([]);

  const [details, setDetails] = useState<Record<string, PurchaseDetail>>({});
  const [openId, setOpenId] = useState<string | null>(null);
  const [workingId, setWorkingId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);
  const [rebuilds, setRebuilds] = useState<Record<string, RebuildResultData>>({});

  const [receipt, setReceipt] = useState<ReceiptResult | null>(null);
  const [receiptSaved, setReceiptSaved] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [manualOpen, setManualOpen] = useState(false);
  const [manualChain, setManualChain] = useState("");
  const [manualDate, setManualDate] = useState(todayIso());
  const [manualItems, setManualItems] = useState<ManualItem[]>([]);
  const [savingManual, setSavingManual] = useState(false);
  const [manualMessage, setManualMessage] = useState<string | null>(null);

  const emailOk = isValidEmail(email);

  useEffect(() => {
    const stored = readStoredEmail();
    if (stored) {
      setEmail(stored);
      setEmailSaved(true);
    }
  }, []);

  // Catene note: popolano la tendina del supermercato nell'inserimento manuale.
  useEffect(() => {
    let cancelled = false;
    getChainsCoverage()
      .then((rows) => {
        if (!cancelled) setChains(rows.filter((c) => c.slug && c.name));
      })
      .catch(() => {
        if (!cancelled) setChains([]); // tendina assente: il campo e' opzionale
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadPurchases = async (mail: string) => {
    if (!isValidEmail(mail)) return;
    setLoadingList(true);
    setListError(null);
    try {
      const rows = await getPurchases(mail.trim(), 30);
      setPurchases(Array.isArray(rows) ? rows : []);
    } catch (err) {
      setPurchases([]);
      setListError(friendlyError(err, MISSING_PURCHASES));
    } finally {
      setLoadingList(false);
    }
  };

  // Email gia' salvata su questo dispositivo: carico subito lo storico.
  useEffect(() => {
    if (emailOk) void loadPurchases(email);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [emailSaved]);

  const confirmEmail = () => {
    if (!emailOk) return;
    storeEmail(email);
    setEmailSaved(true);
    void loadPurchases(email);
  };

  /** Gli articoli di una spesa. GET /purchases/{id} non e' garantito dal
   *  contratto: se manca, per la spesa piu' recente ripiego su /purchases/last. */
  const resolveDetail = async (purchase: PurchaseSummary): Promise<PurchaseDetail> => {
    const cached = details[purchase.id];
    if (cached?.items && cached.items.length) return cached;
    try {
      const detail = await getPurchase(purchase.id, email.trim());
      setDetails((prev) => ({ ...prev, [purchase.id]: detail }));
      return detail;
    } catch (err) {
      const info = apiError(err);
      const isNewest = purchases.length > 0 && purchases[0].id === purchase.id;
      if (isMissingEndpoint(info) && isNewest) {
        const last = await getLastPurchase(email.trim());
        setDetails((prev) => ({ ...prev, [purchase.id]: last }));
        return last;
      }
      throw err;
    }
  };

  const togglePurchase = async (purchase: PurchaseSummary) => {
    setRowError(null);
    if (openId === purchase.id) {
      setOpenId(null);
      return;
    }
    setOpenId(purchase.id);
    if (details[purchase.id]?.items?.length) return;
    setWorkingId(purchase.id);
    try {
      await resolveDetail(purchase);
    } catch (err) {
      setRowError({
        id: purchase.id,
        message: friendlyError(
          err,
          "Il dettaglio di questa spesa non e' ancora disponibile dal server."
        ),
      });
    } finally {
      setWorkingId(null);
    }
  };

  const rebuildPurchase = async (purchase: PurchaseSummary) => {
    setRowError(null);
    setWorkingId(purchase.id);
    try {
      const detail = await resolveDetail(purchase);
      const items = purchaseItemsToRebuildItems(detail?.items);
      if (!items.length) {
        setRowError({
          id: purchase.id,
          message: "Questa spesa non ha prodotti utilizzabili per cercare le offerte.",
        });
        return;
      }
      const at = location ?? DEFAULT_LOCATION;
      const data = await rebuildFromItems(items, at.lat, at.lng, radiusKm, {
        chain_slug: purchase.chain_slug || undefined,
        keep_brand: true,
      });
      setRebuilds((prev) => ({ ...prev, [purchase.id]: data }));
      setOpenId(purchase.id);
    } catch (err) {
      setRowError({ id: purchase.id, message: friendlyError(err, MISSING_REBUILD) });
    } finally {
      setWorkingId(null);
    }
  };

  const onPickFile = async (file?: File | null) => {
    if (!file) return;
    setUploadError(null);
    setReceipt(null);
    setReceiptSaved(false);
    if (!emailOk) {
      setUploadError("Inserisci prima la tua email: e' la chiave del tuo storico.");
      return;
    }
    const okType =
      file.type.startsWith("image/") ||
      file.type === "application/pdf" ||
      /\.(jpg|jpeg|png|webp|heic|pdf)$/i.test(file.name);
    if (!okType) {
      setUploadError("Formato non supportato: carica una foto dello scontrino o un PDF.");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setUploadError("File troppo grande: il limite e' 10 MB.");
      return;
    }
    setUploading(true);
    try {
      const parsed = await parseReceiptFor(file, email.trim());
      setReceipt(parsed);
      if (parsed?.purchase_id) {
        setReceiptSaved(true);
        void loadPurchases(email);
      }
    } catch (err) {
      setUploadError(friendlyError(err, MISSING_RECEIPT));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const saveReceiptAsPurchase = async () => {
    if (!receipt || !emailOk) return;
    const items = receiptToItems(receipt);
    if (!items.length) {
      setUploadError("Nessun articolo leggibile nello scontrino: prova con una foto piu' nitida.");
      return;
    }
    setUploading(true);
    setUploadError(null);
    try {
      await createPurchase({
        email: email.trim(),
        chain_slug: receipt.store_chain || undefined,
        purchase_date: receipt.purchase_date || undefined,
        total: receipt.total_amount ?? undefined,
        source: "receipt",
        items,
      });
      setReceiptSaved(true);
      void loadPurchases(email);
    } catch (err) {
      setUploadError(friendlyError(err, MISSING_PURCHASES));
    } finally {
      setUploading(false);
    }
  };

  const addManualProduct = (product: Product) => {
    setManualMessage(null);
    setManualItems((prev) =>
      prev.some((it) => it.product_id === product.id)
        ? prev
        : [
            ...prev,
            {
              uid: newUid(),
              name: product.name,
              brand: product.brand || undefined,
              quantity: 1,
              product_id: product.id,
              image_url: product.image_url,
            },
          ]
    );
  };

  const addManualGeneric = (query: string) => {
    setManualMessage(null);
    setManualItems((prev) => [
      ...prev,
      { uid: newUid(), name: query, quantity: 1 },
    ]);
  };

  const saveManualPurchase = async () => {
    if (!emailOk) {
      setManualMessage("Inserisci prima la tua email.");
      return;
    }
    const items = manualToItems(manualItems);
    if (!items.length) {
      setManualMessage("Aggiungi almeno un prodotto.");
      return;
    }
    setSavingManual(true);
    setManualMessage(null);
    try {
      await createPurchase({
        email: email.trim(),
        chain_slug: manualChain || undefined,
        purchase_date: manualDate || undefined,
        source: "manual",
        items,
      });
      setManualItems([]);
      setManualMessage("Spesa salvata: ora posso rifarla scegliendo le offerte.");
      void loadPurchases(email);
    } catch (err) {
      setManualMessage(friendlyError(err, MISSING_PURCHASES));
    } finally {
      setSavingManual(false);
    }
  };

  const chainLabel = useMemo(() => {
    const bySlug = new Map(chains.map((c) => [c.slug, c.name]));
    return (slug?: string | null, name?: string | null) =>
      name || (slug ? bySlug.get(slug) || slug : "supermercato non indicato");
  }, [chains]);

  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card">
        <div className="flex items-start gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary-50 text-primary">
            <Receipt size={20} />
          </span>
          <div>
            <h1 className="text-lg font-extrabold leading-tight text-deep">Le mie spese</h1>
            <p className="mt-1 text-[13px] text-stone-500">
              Salva le spese che fai: l&apos;assistente le riprende e te le rifa&apos;
              scegliendo i prodotti in offerta, mantenendo il piu&apos; possibile le tue
              marche.
            </p>
          </div>
        </div>
      </section>

      {/* Email: chiave dello storico, come in /lista e negli avvisi prezzo */}
      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card">
        <label htmlFor="spese-email" className="text-sm font-bold text-deep">
          La tua email
        </label>
        <p className="mt-0.5 text-[12px] text-stone-500">
          Serve per ritrovare le tue spese su qualsiasi dispositivo. Niente password.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            confirmEmail();
          }}
          className="mt-2 flex flex-col gap-2 sm:flex-row"
        >
          <div className="relative flex-1">
            <Mail size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-stone-400" />
            <input
              id="spese-email"
              type="email"
              inputMode="email"
              autoComplete="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                setEmailSaved(false);
              }}
              placeholder="nome@email.it"
              className="h-11 w-full rounded-xl border border-stone-200 pl-9 pr-3 text-[14px] outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
            />
          </div>
          <button
            type="submit"
            disabled={!emailOk || loadingList}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-[13px] font-bold text-white transition active:scale-[0.99] disabled:opacity-50"
          >
            {loadingList ? <Loader2 size={15} className="animate-spin" /> : null}
            Carica le mie spese
          </button>
        </form>
      </section>

      <LocationBar />

      {/* Scontrino */}
      <section className="rounded-card border border-stone-200 bg-white p-4 shadow-card">
        <div className="flex items-start gap-3">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent-50 text-accent-600">
            <Upload size={19} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-extrabold text-deep">Carica uno scontrino</p>
            <p className="mt-0.5 text-[12px] text-stone-500">
              Foto o PDF, fino a 10 MB. Leggo gli articoli e li salvo come spesa.
            </p>
          </div>
        </div>

        <label
          htmlFor="spese-file"
          className="mt-3 flex h-11 cursor-pointer items-center justify-center gap-2 rounded-xl border-2 border-dashed border-stone-300 text-[13px] font-bold text-stone-600 transition hover:border-primary hover:text-primary"
        >
          {uploading ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
          {uploading ? "Sto leggendo lo scontrino..." : "Scegli il file dello scontrino"}
        </label>
        <input
          id="spese-file"
          ref={fileRef}
          type="file"
          accept="image/*,application/pdf"
          className="sr-only"
          disabled={uploading}
          onChange={(e) => void onPickFile(e.target.files?.[0])}
        />

        {uploadError && (
          <p className="mt-2 flex items-start gap-1.5 rounded-xl border border-red-200 bg-red-50 p-2.5 text-[12px] text-red-700">
            <AlertTriangle size={13} className="mt-[2px] shrink-0" />
            {uploadError}
          </p>
        )}

        {receipt && (
          <div className="mt-3 rounded-xl border border-stone-200 bg-surface p-3">
            <p className="text-[13px] font-bold text-deep">
              {receipt.store_name || "Scontrino letto"}
              {receipt.purchase_date ? ` - ${formatDay(receipt.purchase_date)}` : ""}
            </p>
            <p className="text-[12px] text-stone-500">
              {receipt.items_count ?? receipt.items?.length ?? 0} articoli
              {eur(receipt.total_amount) ? ` - totale ${eur(receipt.total_amount)}` : ""}
            </p>
            <ul className="mt-2 max-h-56 divide-y divide-stone-200 overflow-y-auto rounded-lg bg-white">
              {(receipt.items || []).map((it, i) => (
                <li
                  key={`${it.name}-${i}`}
                  className="flex items-center justify-between gap-3 px-2.5 py-1.5"
                >
                  <span className="min-w-0 flex-1 truncate text-[12px] text-stone-700">
                    {it.name}
                    {it.quantity && it.quantity !== 1 ? ` x${it.quantity}` : ""}
                  </span>
                  <span className="tnum shrink-0 text-[12px] font-semibold text-deep">
                    {eur(it.total_price ?? it.unit_price) || "-"}
                  </span>
                </li>
              ))}
            </ul>
            {receiptSaved ? (
              <p className="mt-2 flex items-center gap-1.5 text-[12px] font-semibold text-primary">
                <CheckCircle2 size={14} /> Spesa salvata nello storico.
              </p>
            ) : (
              <button
                type="button"
                onClick={() => void saveReceiptAsPurchase()}
                disabled={uploading || !emailOk}
                className="mt-2 inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary px-3 text-[13px] font-bold text-white transition active:scale-[0.99] disabled:opacity-50"
              >
                <Save size={14} /> Salva come mia spesa
              </button>
            )}
          </div>
        )}
      </section>

      {/* Inserimento manuale */}
      <section className="rounded-card border border-stone-200 bg-white shadow-card">
        <button
          type="button"
          onClick={() => setManualOpen((v) => !v)}
          aria-expanded={manualOpen}
          className="flex w-full items-center gap-3 p-4 text-left"
        >
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary-50 text-primary">
            <Plus size={19} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-extrabold text-deep">
              Inserisci una spesa a mano
            </span>
            <span className="block text-[12px] text-stone-500">
              Scegli il supermercato e aggiungi i prodotti dal catalogo.
            </span>
          </span>
          {manualOpen ? (
            <ChevronUp size={18} className="shrink-0 text-stone-400" />
          ) : (
            <ChevronDown size={18} className="shrink-0 text-stone-400" />
          )}
        </button>

        {manualOpen && (
          <div className="flex flex-col gap-3 border-t border-stone-100 p-4">
            <div className="flex flex-col gap-3 sm:flex-row">
              <div className="flex-1">
                <label htmlFor="spese-chain" className="text-[12px] font-bold text-deep">
                  Supermercato
                </label>
                {chains.length > 0 ? (
                  <select
                    id="spese-chain"
                    value={manualChain}
                    onChange={(e) => setManualChain(e.target.value)}
                    className="mt-1 h-11 w-full rounded-xl border border-stone-200 bg-white px-2 text-[13px] outline-none focus:border-primary"
                  >
                    <option value="">Non indicato</option>
                    {chains.map((c) => (
                      <option key={c.slug} value={c.slug}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id="spese-chain"
                    value={manualChain}
                    onChange={(e) => setManualChain(e.target.value.trim().toLowerCase())}
                    placeholder="es. conad"
                    className="mt-1 h-11 w-full rounded-xl border border-stone-200 px-3 text-[13px] outline-none focus:border-primary"
                  />
                )}
              </div>
              <div className="flex-1">
                <label htmlFor="spese-date" className="text-[12px] font-bold text-deep">
                  Data della spesa
                </label>
                <input
                  id="spese-date"
                  type="date"
                  value={manualDate}
                  onChange={(e) => setManualDate(e.target.value)}
                  className="mt-1 h-11 w-full rounded-xl border border-stone-200 px-3 text-[13px] outline-none focus:border-primary"
                />
              </div>
            </div>

            <div>
              <p className="text-[12px] font-bold text-deep">Prodotti acquistati</p>
              <div className="mt-1">
                <ProductPicker
                  placeholder="Aggiungi prodotto, es. pasta barilla"
                  onPickProduct={addManualProduct}
                  onPickGeneric={addManualGeneric}
                />
              </div>
            </div>

            {manualItems.length > 0 && (
              <ul className="divide-y divide-stone-100 rounded-xl border border-stone-200">
                {manualItems.map((it) => (
                  <li key={it.uid} className="flex items-center gap-2 px-2.5 py-2">
                    {it.image_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={it.image_url}
                        alt=""
                        className="h-9 w-9 shrink-0 rounded bg-white object-contain"
                      />
                    ) : (
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded bg-stone-100 text-stone-400">
                        <ShoppingBasket size={15} />
                      </span>
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] font-semibold text-stone-800">
                        {it.name}
                      </p>
                      {it.brand && <p className="text-[11px] text-stone-400">{it.brand}</p>}
                    </div>
                    <label className="sr-only" htmlFor={`qty-${it.uid}`}>
                      Quantita&apos; di {it.name}
                    </label>
                    <input
                      id={`qty-${it.uid}`}
                      type="number"
                      min={1}
                      max={99}
                      value={it.quantity}
                      onChange={(e) =>
                        setManualItems((prev) =>
                          prev.map((row) =>
                            row.uid === it.uid
                              ? { ...row, quantity: Math.max(1, Number(e.target.value) || 1) }
                              : row
                          )
                        )
                      }
                      className="h-9 w-14 shrink-0 rounded-lg border border-stone-200 px-2 text-center text-[13px] outline-none focus:border-primary"
                    />
                    <button
                      type="button"
                      onClick={() =>
                        setManualItems((prev) => prev.filter((row) => row.uid !== it.uid))
                      }
                      aria-label={`Rimuovi ${it.name}`}
                      className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-stone-400 transition hover:bg-red-50 hover:text-red-600"
                    >
                      <Trash2 size={15} />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {manualMessage && (
              <p className="rounded-xl border border-stone-200 bg-surface p-2.5 text-[12px] text-stone-600">
                {manualMessage}
              </p>
            )}

            <button
              type="button"
              onClick={() => void saveManualPurchase()}
              disabled={savingManual || manualItems.length === 0}
              className="inline-flex h-11 items-center justify-center gap-2 self-start rounded-xl bg-stone-900 px-4 text-[13px] font-bold text-white transition active:scale-[0.99] disabled:opacity-50"
            >
              {savingManual ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}
              Salva questa spesa
            </button>
          </div>
        )}
      </section>

      {/* Storico */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2 px-1">
          <h2 className="text-sm font-extrabold text-deep">Spese salvate</h2>
          {emailOk && (
            <button
              type="button"
              onClick={() => void loadPurchases(email)}
              disabled={loadingList}
              className="text-[12px] font-bold text-primary disabled:opacity-50"
            >
              Aggiorna
            </button>
          )}
        </div>

        {!emailOk && (
          <p className="rounded-card border border-stone-200 bg-white p-4 text-[13px] text-stone-500 shadow-card">
            Inserisci la tua email qui sopra per vedere lo storico delle spese.
          </p>
        )}

        {emailOk && loadingList && (
          <p className="rounded-card border border-stone-200 bg-white p-4 text-[13px] text-stone-500 shadow-card">
            Carico le tue spese...
          </p>
        )}

        {emailOk && !loadingList && listError && (
          <p className="flex items-start gap-1.5 rounded-card border border-amber-200 bg-amber-50 p-4 text-[13px] text-amber-900">
            <AlertTriangle size={14} className="mt-[2px] shrink-0" />
            {listError}
          </p>
        )}

        {emailOk && !loadingList && !listError && purchases.length === 0 && (
          <div className="rounded-card border border-stone-200 bg-white p-4 text-[13px] text-stone-600 shadow-card">
            <p className="font-bold text-deep">Nessuna spesa salvata</p>
            <p className="mt-1">
              Carica uno scontrino o inserisci una spesa a mano: appena c&apos;e&apos; una
              spesa, l&apos;assistente puo&apos; rifarla con le offerte.
            </p>
            <Link
              href="/offerte"
              className="mt-2 inline-flex h-10 items-center justify-center rounded-xl border border-stone-200 px-3 text-[13px] font-bold text-deep"
            >
              Guarda le offerte di questa settimana
            </Link>
          </div>
        )}

        {purchases.map((purchase) => {
          const detail = details[purchase.id];
          const rebuild = rebuilds[purchase.id];
          const isOpen = openId === purchase.id;
          const working = workingId === purchase.id;
          const error = rowError?.id === purchase.id ? rowError.message : null;
          return (
            <article
              key={purchase.id}
              className="overflow-hidden rounded-card border border-stone-200 bg-white shadow-card"
            >
              <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-extrabold text-deep">
                    {chainLabel(purchase.chain_slug, purchase.chain_name)}
                  </p>
                  <p className="text-[12px] text-stone-500">
                    {formatDay(purchase.purchase_date || purchase.created_at)}
                    {purchase.items_count != null ? ` - ${purchase.items_count} articoli` : ""}
                    {eur(purchase.total) ? ` - ${eur(purchase.total)}` : ""}
                    {purchase.source ? ` - ${purchase.source}` : ""}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void togglePurchase(purchase)}
                    disabled={working}
                    aria-expanded={isOpen}
                    className="inline-flex h-10 items-center gap-1.5 rounded-xl border border-stone-200 px-3 text-[12px] font-bold text-deep transition active:scale-[0.99] disabled:opacity-50"
                  >
                    {isOpen ? "Chiudi" : "Apri"}
                    {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>
                  <button
                    type="button"
                    onClick={() => void rebuildPurchase(purchase)}
                    disabled={working}
                    className="inline-flex h-10 items-center gap-1.5 rounded-xl bg-primary px-3 text-[12px] font-bold text-white transition active:scale-[0.99] disabled:opacity-50"
                  >
                    {working ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <BadgePercent size={14} />
                    )}
                    Rifai con le offerte
                  </button>
                </div>
              </div>

              {error && (
                <p className="mx-4 mb-4 flex items-start gap-1.5 rounded-xl border border-amber-200 bg-amber-50 p-2.5 text-[12px] text-amber-900">
                  <AlertTriangle size={13} className="mt-[2px] shrink-0" />
                  {error}
                </p>
              )}

              {isOpen && (
                <div className="flex flex-col gap-3 border-t border-stone-100 p-4">
                  {working && !detail && (
                    <p className="text-[12px] text-stone-500">Carico gli articoli...</p>
                  )}
                  {detail?.items && detail.items.length > 0 && (
                    <ul className="divide-y divide-stone-100 rounded-xl border border-stone-200">
                      {detail.items.map((it, i) => (
                        <li
                          key={`${it.id || it.name}-${i}`}
                          className="flex items-center justify-between gap-3 px-2.5 py-2"
                        >
                          <span className="min-w-0 flex-1 text-[13px] text-stone-700">
                            {it.name}
                            {it.quantity && it.quantity !== 1 ? ` x${it.quantity}` : ""}
                            {it.brand ? (
                              <span className="text-stone-400"> - {it.brand}</span>
                            ) : null}
                          </span>
                          <span className="tnum shrink-0 text-[12px] font-semibold text-deep">
                            {eur(it.unit_price) || "-"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {detail && !detail.items?.length && !working && (
                    <p className="text-[12px] text-stone-500">
                      Nessun articolo registrato per questa spesa.
                    </p>
                  )}
                  {rebuild && (
                    <RebuildResult
                      data={rebuild}
                      title="Sostituzioni in offerta per questa spesa"
                      originalLabel="In questa spesa"
                    />
                  )}
                </div>
              )}
            </article>
          );
        })}
      </section>
    </div>
  );
}
