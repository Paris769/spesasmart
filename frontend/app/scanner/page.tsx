"use client";
import { useState, useRef } from "react";
import { scanBarcode } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import PriceCard from "@/components/ui/PriceCard";

export default function ScannerPage() {
  const [manualBarcode, setManualBarcode] = useState("");
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const { location, radiusKm } = useAppStore();

  const doScan = async (barcode: string) => {
    if (!location) return alert("Attiva prima la posizione");
    setLoading(true);
    try {
      const data = await scanBarcode(barcode, location.lat, location.lng, radiusKm);
      setResult(data);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-bold text-gray-800">Scanner barcode</h1>

      <div className="bg-white border-2 border-dashed border-gray-300 rounded-xl p-8 text-center">
        <p className="text-4xl mb-2">📷</p>
        <p className="text-sm text-gray-500 mb-4">
          Inquadra il barcode con la fotocamera oppure inseriscilo manualmente
        </p>
        <p className="text-xs text-gray-400">
          (La fotocamera nativa è disponibile nell'app mobile)
        </p>
      </div>

      <div className="flex gap-2">
        <input
          value={manualBarcode}
          onChange={(e) => setManualBarcode(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && manualBarcode && doScan(manualBarcode)}
          placeholder="Inserisci barcode manualmente…"
          className="flex-1 border-2 border-gray-200 focus:border-primary rounded-xl px-4 py-2 outline-none transition"
        />
        <button
          onClick={() => doScan(manualBarcode)}
          disabled={!manualBarcode || loading}
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
                <img
                  src={result.product.image_url}
                  alt={result.product.name}
                  className="w-16 h-16 object-contain rounded"
                />
              )}
              <div>
                <p className="font-bold text-gray-900">{result.product.name}</p>
                {result.product.brand && (
                  <p className="text-sm text-gray-500">{result.product.brand}</p>
                )}
                {result.message && (
                  <p className="text-xs text-amber-600 mt-1">{result.message}</p>
                )}
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
        </div>
      )}
    </div>
  );
}
