"use client";
import { useEffect } from "react";
import { useAppStore } from "@/lib/store";

const RADII = [1, 3, 5, 10, 20];

export default function LocationBar() {
  const { location, radiusKm, setLocation, setRadius } = useAppStore();

  const detectLocation = (silent = false) => {
    if (!navigator.geolocation) {
      if (!silent) alert("Geolocalizzazione non supportata");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        setLocation({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          label: "Posizione attuale",
        }),
      (err) => {
        console.warn("Geolocalizzazione fallita:", err.message);
        if (!silent) alert("Impossibile rilevare la posizione");
      },
      { timeout: 10000, maximumAge: 60000 }
    );
  };

  // Auto-detect on mount when: location is null (first visit) OR
  // the stored location was previously auto-detected (label === "Posizione attuale")
  // so stale GPS coordinates get refreshed automatically.
  useEffect(() => {
    if (!location || location.label === "Posizione attuale") {
      detectLocation(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-wrap items-center gap-2 bg-white border rounded-xl p-3 shadow-sm">
      <button
        onClick={detectLocation}
        className="flex items-center gap-1 text-sm bg-primary text-white px-3 py-1.5 rounded-lg hover:bg-green-700 transition"
      >
        📍 {location ? location.label : "Usa la mia posizione"}
      </button>

      {location && (
        <span className="text-xs text-gray-500">
          {location.lat.toFixed(4)}, {location.lng.toFixed(4)}
        </span>
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
  );
}
