"use client";
import { useState, useRef, useEffect } from "react";
import {
  scanBarcode, parseReceipt, getNearbyStores, submitPrice,
  ReceiptResult, ReceiptItem, Store, PriceSubmitResult,
} from "@/lib/api";
import { useAppStore } from "@/lib/store";
import PriceCard from "@/components/ui/PriceCard";

type Tab = "barcode" | "scontrino";

export default function ScannerPage() {
  const [tab, setTab] = useState<Tab>("barcode");
  const { location, radiusKm } = useAppStore();

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-bold text-gray-800">Scanner</h1>

      {/* Tab switcher */}
      <div className="flex bg-gray-100 rounded-xl p-1 gap-1">
        {(["barcode", "scontrino"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 py-2 rounded-lg text-sm font-medium transition ${
              tab === t ? "bg-white shadow text-primary" : "text-gray-500"
            }`}
          >
            {t === "barcode" ? "📷 Barcode" : "🧾 Scontrino"}
          </button>
        ))}
      </div>

      {tab === "barcode" ? (
        <BarcodeTab location={location} radiusKm={radiusKm} />
      ) : (
        <ScontrinoTab />
      )}
    </div>
  );
}

// ── Tab Barcode ──────────────────────────────────────────────────────────────

function BarcodeTab({ location, radiusKm }: { location: any; radiusKm: number }) {
  const [barcode, setBarcode] = useState("");
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const doScan = async (bc: string) => {
    if (!location) return alert("Attiva prima la posizione");
    setLoading(true);
    try {
      const data = await scanBarcode(bc, location.lat, location.lng, radiusKm);
      setResult(data);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="bg-white border-2 border-dashed border-gray-300 rounded-xl p-8 text-center">
        <p className="text-4xl mb-2">📷</p>
        <p className="text-sm text-gray-500 mb-1">
          Inquadra il barcode con la fotocamera oppure inseriscilo manualmente
        </p>
        <p className="text-xs text-gray-400">(Fotocamera nativa disponibile nell'app mobile)</p>
      </div>

      <div className="flex gap-2">
        <input
          value={barcode}
          onChange={(e) => setBarcode(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && barcode && doScan(barcode)}
          placeholder="Inserisci barcode manualmente…"
          className="flex-1 border-2 border-gray-200 focus:border-primary rounded-xl px-4 py-2 outline-none transition"
        />
        <button
          onClick={() => doScan(barcode)}
          disabled={!barcode || loading}
          className="bg-primary text-white px-4 rounded-xl font-medium disabled:opacity-50"
        >
          Cerca
        </button>
      </div>

      {loading && <p className="text-center text-gray-500">Ricerca in corso…</p>}

      {result && (
        <div className="flex flex-col gap-3">
          {result.product && (
            <div className="flex items-center gap-3 bg-white border rounded-xl p-4">
              {result.product.image_url && (
                <img src={result.product.image_url} alt={result.product.name}
                  className="w-16 h-16 object-contain rounded" />
              )}
              <div>
                <p className="font-bold text-gray-900">{result.product.name}</p>
                {result.product.brand && <p className="text-sm text-gray-500">{result.product.brand}</p>}
                {result.message && <p className="text-xs text-amber-600 mt-1">{result.message}</p>}
              </div>
            </div>
          )}
          {result.prices?.length > 0 && (
            <>
              <p className="text-sm text-gray-600">
                <strong>{result.prices.length}</strong> prezzi trovati entro {radiusKm} km
              </p>
              {result.prices.map((p: any, i: number) => (
                <PriceCard key={`${p.store_id}-${i}`} result={p} rank={i} />
              ))}
            </>
          )}
          {result.prices?.length === 0 && (
            <p className="text-center text-gray-500 py-4">
              Nessun prezzo trovato nel raggio di {radiusKm} km
            </p>
          )}

          {result.product && (
            <PriceSubmitForm
              barcode={barcode}
              location={location}
              radiusKm={radiusKm}
            />
          )}
        </div>
      )}
    </>
  );
}

// ── Form contribuzione prezzo ────────────────────────────────────────────────

function PriceSubmitForm({
  barcode,
  location,
  radiusKm,
}: {
  barcode: string;
  location: any;
  radiusKm: number;
}) {
  const [open, setOpen] = useState(false);
  const [stores, setStores] = useState<Store[]>([]);
  const [storeId, setStoreId] = useState("");
  const [price, setPrice] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState<PriceSubmitResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open && location && stores.length === 0) {
      getNearbyStores(location.lat, location.lng, radiusKm).then(setStores);
    }
  }, [open, location, radiusKm, stores.length]);

  const handleSubmit = async () => {
    if (!storeId || !price) return;
    const parsed = parseFloat(price.replace(",", "."));
    if (isNaN(parsed) || parsed <= 0) return setError("Inserisci un prezzo valido");
    setError(null);
    setLoading(true);
    try {
      const res = await submitPrice(barcode, storeId, parsed);
      setSubmitted(res);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Errore durante l'invio");
    } finally {
      setLoading(false);
    }
  };

  if (submitted) {
    const { comparison: c } = submitted;
    const isBelow = c.delta_pct < -1;
    const isAbove = c.delta_pct > 1;
    return (
      <div className="bg-green-50 border border-green-200 rounded-xl p-4 flex flex-col gap-2">
        <p className="font-semibold text-green-800">✅ Prezzo inviato — grazie!</p>
        <p className="text-sm text-gray-700">
          Hai segnalato <strong>€{submitted.submitted_price.toFixed(2)}</strong> —{" "}
          <span className={isBelow ? "text-green-700 font-medium" : isAbove ? "text-red-600 font-medium" : "text-gray-600"}>
            {c.vs_avg}
          </span>
        </p>
        <div className="grid grid-cols-3 gap-2 text-center text-xs mt-1">
          <div className="bg-white rounded-lg p-2 border">
            <p className="text-gray-400">Min</p>
            <p className="font-bold text-green-700">€{c.price_min.toFixed(2)}</p>
          </div>
          <div className="bg-white rounded-lg p-2 border">
            <p className="text-gray-400">Media</p>
            <p className="font-bold text-gray-700">€{c.price_avg.toFixed(2)}</p>
          </div>
          <div className="bg-white rounded-lg p-2 border">
            <p className="text-gray-400">Max</p>
            <p className="font-bold text-red-600">€{c.price_max.toFixed(2)}</p>
          </div>
        </div>
        <p className="text-xs text-gray-400 text-center">
          Basato su {c.store_count} {c.store_count === 1 ? "negozio" : "negozi"}
        </p>
      </div>
    );
  }

  return (
    <div className="border-2 border-dashed border-gray-200 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm text-gray-600 hover:bg-gray-50 transition"
      >
        <span>📍 Hai visto questo prodotto in negozio? Segnala il prezzo</span>
        <span className="text-gray-400">{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 flex flex-col gap-3 border-t border-gray-100">
          {stores.length === 0 ? (
            <p className="text-sm text-gray-400 py-2">Caricamento negozi vicini…</p>
          ) : (
            <select
              value={storeId}
              onChange={(e) => setStoreId(e.target.value)}
              className="border-2 border-gray-200 focus:border-primary rounded-xl px-3 py-2 text-sm outline-none transition"
            >
              <option value="">Seleziona negozio…</option>
              {stores.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.chain_name} — {s.name} ({s.distance_km} km)
                </option>
              ))}
            </select>
          )}

          <div className="flex gap-2">
            <div className="relative flex-1">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm">€</span>
              <input
                type="text"
                inputMode="decimal"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder="0,00"
                className="w-full border-2 border-gray-200 focus:border-primary rounded-xl pl-7 pr-3 py-2 text-sm outline-none transition"
              />
            </div>
            <button
              onClick={handleSubmit}
              disabled={!storeId || !price || loading}
              className="bg-primary text-white px-4 rounded-xl text-sm font-medium disabled:opacity-50 transition"
            >
              {loading ? "…" : "Invia"}
            </button>
          </div>

          {error && <p className="text-xs text-red-600">{error}</p>}
        </div>
      )}
    </div>
  );
}

// ── Tab Scontrino ────────────────────────────────────────────────────────────

function ScontrinoTab() {
  const [result, setResult] = useState<ReceiptResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = async (file: File) => {
    setError(null);
    setResult(null);
    if (file.type.startsWith("image/")) {
      setPreview(URL.createObjectURL(file));
    } else {
      setPreview(null);
    }
    setLoading(true);
    try {
      const data = await parseReceipt(file);
      setResult(data);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Errore durante l'analisi dello scontrino");
    } finally {
      setLoading(false);
    }
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  return (
    <>
      {/* Drop zone */}
      <div
        onDrop={onDrop}
        onDragOver={(e) => e.preventDefault()}
        onClick={() => inputRef.current?.click()}
        className="bg-white border-2 border-dashed border-green-300 rounded-xl p-8 text-center cursor-pointer hover:border-primary transition"
      >
        {preview ? (
          <img src={preview} alt="scontrino" className="max-h-48 mx-auto rounded-lg object-contain" />
        ) : (
          <>
            <p className="text-4xl mb-2">🧾</p>
            <p className="text-sm text-gray-600 font-medium mb-1">
              Carica una foto dello scontrino
            </p>
            <p className="text-xs text-gray-400">
              JPEG, PNG, WEBP o PDF · max 10 MB · trascina o clicca
            </p>
          </>
        )}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,application/pdf"
        className="hidden"
        onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
      />

      {loading && (
        <div className="flex flex-col items-center gap-2 py-6">
          <div className="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-gray-500">Analisi con AI in corso…</p>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
          {error}
        </div>
      )}

      {result && <ReceiptView result={result} />}
    </>
  );
}

// ── Visualizzazione risultato scontrino ───────────────────────────────────────

function ReceiptView({ result }: { result: ReceiptResult }) {
  const formatPrice = (n: number | null) =>
    n != null ? `€${n.toFixed(2)}` : "—";

  return (
    <div className="flex flex-col gap-4">
      {/* Header store info */}
      <div className="bg-green-50 border border-green-200 rounded-xl p-4">
        <div className="flex items-start gap-3">
          <span className="text-2xl">🏪</span>
          <div>
            <p className="font-bold text-gray-900">
              {result.store_name || "Negozio non identificato"}
            </p>
            {result.store_chain && (
              <p className="text-sm text-green-700 font-medium">{result.store_chain}</p>
            )}
            {result.store_address && (
              <p className="text-xs text-gray-500">{result.store_address}</p>
            )}
            <div className="flex gap-4 mt-1">
              {result.purchase_date && (
                <p className="text-xs text-gray-500">
                  📅 {new Date(result.purchase_date).toLocaleDateString("it-IT")}
                </p>
              )}
              {result.total_amount != null && (
                <p className="text-xs font-semibold text-gray-700">
                  Totale: {formatPrice(result.total_amount)}
                </p>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Items */}
      <p className="text-sm text-gray-600">
        <strong>{result.items_count}</strong> articoli rilevati
      </p>

      <div className="flex flex-col gap-2">
        {result.items.map((item, i) => (
          <ReceiptItemRow key={i} item={item} />
        ))}
      </div>
    </div>
  );
}

function ReceiptItemRow({ item }: { item: ReceiptItem }) {
  const formatPrice = (n: number | null) =>
    n != null ? `€${n.toFixed(2)}` : "—";

  return (
    <div className="bg-white border rounded-xl p-3 flex items-center gap-3">
      {/* Immagine prodotto se abbinato */}
      {item.matched_product?.image_url ? (
        <img
          src={item.matched_product.image_url}
          alt={item.name}
          className="w-12 h-12 object-contain rounded flex-shrink-0"
        />
      ) : (
        <div className="w-12 h-12 bg-gray-100 rounded flex items-center justify-center flex-shrink-0">
          <span className="text-xl">🛒</span>
        </div>
      )}

      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900 truncate">{item.name}</p>
        {item.matched_product && (
          <p className="text-xs text-green-600">✓ {item.matched_product.name}</p>
        )}
        {item.quantity > 1 && (
          <p className="text-xs text-gray-400">{item.quantity}x {formatPrice(item.unit_price)}</p>
        )}
      </div>

      <p className="text-sm font-bold text-gray-900 flex-shrink-0">
        {formatPrice(item.total_price ?? item.unit_price)}
      </p>
    </div>
  );
}
