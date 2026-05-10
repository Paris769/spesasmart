"use client";
import { useAppStore } from "@/lib/store";

const RADII = [1, 3, 5, 10, 20];

export default function LocationBar() {
  const { location, radiusKm, setLocation, setRadius } = useAppStore();

  const detectLocation = () => {
    if (!navigator.geolocation) return alert("Geolocalizzazione non supportata");
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        setLocation({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          label: "Posizione attuale",
        }),
      () => alert("Impossibile rilevare la posizione")
    );
  };

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
