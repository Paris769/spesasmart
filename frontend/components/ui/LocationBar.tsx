"use client";
import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useAppStore, AreaPoint } from "@/lib/store";

const MapView = dynamic(() => import("@/components/ui/MapView"), { ssr: false });

const RADII = [1, 3, 5, 10, 20, 30, 50];
const CITY_PRESETS = [
  { label: "Milano", lat: 45.4642, lng: 9.19 },
  { label: "Roma", lat: 41.9028, lng: 12.4964 },
  { label: "Torino", lat: 45.0703, lng: 7.6869 },
  { label: "Napoli", lat: 40.8518, lng: 14.2681 },
  { label: "Bologna", lat: 44.4949, lng: 11.3426 },
];

export default function LocationBar() {
  const { location, radiusKm, searchArea, setLocation, setRadius, setSearchArea } =
    useAppStore();
  const [showMap, setShowMap] = useState(false);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [drawMode, setDrawMode] = useState(false);
  const [draftArea, setDraftArea] = useState<AreaPoint[]>([]);

  const chooseCity = (city: (typeof CITY_PRESETS)[number]) => {
    setLocation({ lat: city.lat, lng: city.lng, label: city.label });
    setLocationError(null);
    setShowMap(false);
  };

  const detectLocation = (silent = false) => {
    if (!navigator.geolocation) {
      setLocationError("Il browser non supporta la posizione. Scegli una citta.");
      return;
    }

    if (!silent) {
      setLocating(true);
      setLocationError(null);
    }

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          label: "Posizione attuale",
        });
        setLocating(false);
        setLocationError(null);
      },
      (err) => {
        console.warn("Geolocalizzazione fallita:", err.message);
        setLocating(false);
        if (!silent) {
          setLocationError("Posizione non disponibile. Scegli una citta qui sotto.");
        }
      },
      { timeout: 10000, maximumAge: 60000 }
    );
  };

  useEffect(() => {
    if (!location || location.label === "Posizione attuale") {
      detectLocation(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startDrawing = () => {
    setDraftArea([]);
    setDrawMode(true);
    setShowMap(true);
  };

  const confirmArea = () => {
    setSearchArea(draftArea);
    setDrawMode(false);
    setDraftArea([]);
  };

  const cancelDrawing = () => {
    setDrawMode(false);
    setDraftArea([]);
  };

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
          {locating ? "Rilevamento..." : location ? location.label : "Usa la mia posizione"}
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
              {showMap ? "Nascondi mappa" : "Mostra mappa"}
            </button>
          </>
        )}

        <div className="flex items-center gap-1 ml-auto flex-wrap">
          <span className="text-xs text-gray-600">Raggio:</span>
          {RADII.map((r) => (
            <button
              key={r}
              onClick={() => setRadius(r)}
              className={`text-xs px-2 py-1 rounded-full border transition ${
                radiusKm === r && !searchArea
                  ? "bg-primary text-white border-primary"
                  : "border-gray-300 hover:border-primary"
              } ${searchArea ? "opacity-50" : ""}`}
            >
              {r} km
            </button>
          ))}
        </div>
      </div>

      {(!location || locationError) && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-3 py-2 text-sm text-amber-800">
          <p className="font-medium">{locationError || "Se il browser non trova la posizione, scegli una citta."}</p>
          <div className="flex flex-wrap gap-2 mt-2">
            {CITY_PRESETS.map((city) => (
              <button
                key={city.label}
                onClick={() => chooseCity(city)}
                className="px-3 py-1.5 rounded-lg bg-white border border-amber-200 text-amber-900 hover:border-primary transition"
              >
                {city.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {showMap && location && (
        <div className="flex flex-wrap items-center gap-2 bg-white border rounded-xl px-3 py-2 shadow-sm">
          {!drawMode && !searchArea && (
            <button
              onClick={startDrawing}
              className="text-xs px-3 py-1.5 rounded-lg border border-primary text-primary hover:bg-green-50 transition"
            >
              Disegna area di interesse
            </button>
          )}

          {!drawMode && searchArea && (
            <>
              <span className="text-xs bg-green-50 text-green-800 px-2 py-1 rounded-lg">
                Area personalizzata - {searchArea.length} punti
              </span>
              <button
                onClick={startDrawing}
                className="text-xs px-2 py-1 rounded-lg border border-gray-300 hover:border-primary transition"
              >
                Ridisegna
              </button>
              <button
                onClick={() => setSearchArea(null)}
                className="text-xs px-2 py-1 rounded-lg border border-red-300 text-red-600 hover:bg-red-50 transition"
              >
                Rimuovi area
              </button>
            </>
          )}

          {drawMode && (
            <>
              <span className="text-xs text-gray-600">
                Tieni premuto e trascina sulla mappa per disegnare l'area
                {draftArea.length >= 3 && " - area tracciata"}
              </span>
              <button
                onClick={confirmArea}
                disabled={draftArea.length < 3}
                className="text-xs px-3 py-1 rounded-lg bg-primary text-white hover:bg-green-700 transition disabled:opacity-40"
              >
                Conferma area
              </button>
              <button
                onClick={cancelDrawing}
                className="text-xs px-2 py-1 rounded-lg border border-gray-300 hover:border-primary transition"
              >
                Esci
              </button>
            </>
          )}
        </div>
      )}

      {showMap && location && (
        <div
          className={`rounded-xl overflow-hidden border shadow ${
            drawMode ? "h-[360px]" : "h-[280px]"
          }`}
        >
          <MapView
            key={`${location.lat.toFixed(5)},${location.lng.toFixed(5)}`}
            center={[location.lat, location.lng]}
            stores={[]}
            radiusKm={radiusKm}
            drawMode={drawMode}
            draftArea={draftArea}
            savedArea={searchArea}
            onDraftChange={(pts) => setDraftArea(pts)}
          />
        </div>
      )}
    </div>
  );
}
