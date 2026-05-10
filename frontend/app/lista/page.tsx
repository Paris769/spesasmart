"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api, { optimizeList } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import LocationBar from "@/components/ui/LocationBar";

const USER_ID = "demo-user"; // sostituire con auth reale

export default function ListaPage() {
  const [newItem, setNewItem] = useState("");
  const { location, radiusKm } = useAppStore();
  const qc = useQueryClient();

  const { data: lists } = useQuery({
    queryKey: ["lists"],
    queryFn: () => api.get(`/lists/?user_id=${USER_ID}`).then((r) => r.data),
  });

  const activeList = lists?.[0];

  const addItem = useMutation({
    mutationFn: (name: string) =>
      api.post(`/lists/${activeList.id}/items`, { product_name: name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["lists"] });
      setNewItem("");
    },
  });

  const createList = useMutation({
    mutationFn: () =>
      api.post(`/lists/?user_id=${USER_ID}`, { name: "Lista spesa" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lists"] }),
  });

  const [optimResult, setOptimResult] = useState<any>(null);

  const optimize = async () => {
    if (!activeList || !location) return;
    const res = await optimizeList(activeList.id, location.lat, location.lng, radiusKm);
    setOptimResult(res);
  };

  return (
    <div className="flex flex-col gap-4">
      <LocationBar />
      <h1 className="text-xl font-bold text-gray-800">Lista della spesa</h1>

      {!activeList && (
        <button
          onClick={() => createList.mutate()}
          className="bg-primary text-white px-4 py-2 rounded-xl text-sm font-medium"
        >
          + Crea lista
        </button>
      )}

      {activeList && (
        <>
          <div className="flex gap-2">
            <input
              value={newItem}
              onChange={(e) => setNewItem(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && newItem && addItem.mutate(newItem)}
              placeholder="Aggiungi prodotto…"
              className="flex-1 border-2 border-gray-200 focus:border-primary rounded-xl px-4 py-2 outline-none transition"
            />
            <button
              onClick={() => newItem && addItem.mutate(newItem)}
              className="bg-primary text-white px-4 rounded-xl font-medium"
            >
              +
            </button>
          </div>

          <ul className="flex flex-col gap-1">
            {activeList.items?.map((item: any) => (
              <li key={item.id} className="bg-white border rounded-xl px-4 py-2 flex items-center gap-3">
                <span className="text-sm flex-1">{item.product_name_db || item.product_name}</span>
                <span className="text-xs text-gray-400">×{item.quantity}</span>
              </li>
            ))}
          </ul>

          {activeList.items?.length > 0 && (
            <button
              onClick={optimize}
              disabled={!location}
              className="bg-secondary text-white px-4 py-2 rounded-xl text-sm font-bold disabled:opacity-50"
            >
              🧮 Ottimizza lista (trova dove spendo meno)
            </button>
          )}

          {optimResult && (
            <div className="flex flex-col gap-3">
              {optimResult.single_store && (
                <div className="bg-green-50 border border-primary rounded-xl p-4">
                  <p className="font-bold text-primary mb-1">✓ Miglior singolo negozio</p>
                  <p className="font-semibold">{optimResult.single_store.chain_name} — {optimResult.single_store.store_name}</p>
                  <p className="text-2xl font-bold mt-1">€{optimResult.single_store.total.toFixed(2)}</p>
                  {optimResult.single_store.shop_url && (
                    <a
                      href={optimResult.single_store.shop_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-block mt-2 bg-primary text-white text-sm px-3 py-1.5 rounded-lg"
                    >
                      Acquista online →
                    </a>
                  )}
                </div>
              )}

              {optimResult.multi_store && optimResult.multi_store.savings_vs_single > 0 && (
                <div className="bg-blue-50 border border-blue-300 rounded-xl p-4">
                  <p className="font-bold text-blue-700 mb-1">
                    💡 Risparmio distribuito su più negozi
                  </p>
                  <p className="text-2xl font-bold">€{optimResult.multi_store.total.toFixed(2)}</p>
                  <p className="text-sm text-green-700 font-medium">
                    Risparmi €{optimResult.multi_store.savings_vs_single.toFixed(2)} rispetto al singolo negozio
                  </p>
                  {optimResult.multi_store.stores.map((s: any) => (
                    <div key={s.store_id} className="mt-2 border-t pt-2">
                      <p className="text-sm font-semibold">{s.chain_name} — €{s.subtotal.toFixed(2)}</p>
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
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
