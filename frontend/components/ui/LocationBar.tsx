"use client";
import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useAppStore } from "@/lib/store";

// Leaflet va importato dinamicamente (richiede window, no SSR)
const MapView = dynamic(() => import("@/components/ui/MapView"), { ssr: false });

const RADII = [1, 3, 5, 10, 20];

export default function LocationBar() {
  const { location, radiusKm, setLocation, setRadius } = useAppStore();
  const [showMap, setShowMap] = useState(false);
  const [locating, setLocating] = useState(false);

  const detectLocation = (silent = false) => {
    if (!navigator.geolocation) {
      if (!silent) alert("Geolocalizzazione non supportata");
      return;
    }
    if (!silent) setLocating(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          label: "Posizione attuale",
        });
        setLocating(false);
      },
      (err) => {
        console.warn("Geolocalizzazione fallita:", err.message);
        setLocating(false);
        if (!silent) alert("Impossibile rilevare la posizione");
      },
      { timeout: 10000, maximumAge: 60000 }
    );
  };

  // Auto-detect al mount quando: location nulla (prima visita) OPPURE
  // la posizione salvata era stata auto-rilevata (label "Posizione attuale"),
  // così le coordinate GPS stale vengono aggiornate automaticamente.
  useEffect(() => {
    if (!location || location.label === "Posizione attuale") {
      detectLocation(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 bg-white border rounded-xl p-3 shadow-sm">
        <button
          onClick={() => {
            detectLocation();
            setShowMap(true);
          }}
          className="flex items-center gap-1 text-sm bg-primary text-white px-3 py-1.5 rounded-lg hover:bg-green-700 transition disabled:opacity-60"
          disabled={locating}
        >
          📍 {locating
            ? "Rilevamento…"
            : location
            ? location.label
            : "Usa la mia posizione"}
        </button>

        {location && (
          <>
            <span className="text-xs text-gray-500">
              {location.lat.toFixed(4)}, {location.lng.toFixed(4)}
            </span>
            <button
              onClick={() => setShowMap((s) => !s)}
              className="text-xs px-2 py-1 rounded-lg border border-gray-300 hover:border-primary transition"
            >
              {showMap ? "Nascondi mappa" : "🗺 Mostra mappa"}
            </button>
          </>
        )}

        <div className="flex items-center gap-1 ml-auto">
          <span className="text-xs text-gray-600">Raggio:</span>
          {RADII.map((r) => (
            <button
              key={r}
              onClick={() => setRadius(r)}
              className={`text-xs px-2 py-1 rounded-full border transition ${
                radiusKm === r
                  ? "bg-primary text-white border-primary"
                  : "border-gray-300 hover:border-primary"
              }`}
            >
              {r} km
            </button>
          ))}
        </div>
      </div>

      {/* Mappa con la posizione attuale — compare al click del pulsante */}
      {showMap && location && (
        <div className="rounded-xl overflow-hidden border shadow h-[280px]">
          <MapView
            // key sulle coordinate: rimonta la mappa quando la posizione
            // cambia, così si ri-centra sul nuovo punto
            key={`${location.lat.toFixed(5)},${location.lng.toFixed(5)}`}
            center={[location.lat, location.lng]}
            stores={[]}
            radiusKm={radiusKm}
          />
        </div>
      )}
    </div>
  );
}
