"use client";
import { useQuery } from "@tanstack/react-query";
import { getNearbyStores } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import LocationBar from "@/components/ui/LocationBar";
import dynamic from "next/dynamic";

// Leaflet deve essere importato dinamicamente (no SSR)
const MapView = dynamic(() => import("@/components/ui/MapView"), { ssr: false });

export default function MapPage() {
  const { location, radiusKm } = useAppStore();

  const { data: stores, isFetching } = useQuery({
    queryKey: ["stores", location, radiusKm],
    queryFn: () => getNearbyStores(location!.lat, location!.lng, radiusKm),
    enabled: !!location,
  });

  return (
    <div className="flex flex-col gap-4">
      <LocationBar />
      <h1 className="text-xl font-bold text-gray-800">
        Supermercati vicino a te
      </h1>

      {!location && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800">
          📍 Attiva la posizione per vedere i negozi sulla mappa
        </div>
      )}

      {location && (
        <div className="rounded-xl overflow-hidden border shadow h-[480px]">
          <MapView
            center={[location.lat, location.lng]}
            stores={stores ?? []}
            radiusKm={radiusKm}
          />
        </div>
      )}

      {stores && stores.length > 0 && (
        <ul className="flex flex-col gap-2">
          {stores.map((s) => (
            <li
              key={s.id}
              className="bg-white border rounded-xl px-4 py-3 flex items-start justify-between gap-2"
            >
              <div>
                <p className="font-semibold text-gray-900">{s.chain_name} — {s.name}</p>
                <p className="text-xs text-gray-500">{s.address}, {s.city}</p>
                <div className="flex gap-1 mt-1">
                  {s.has_delivery && (
                    <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded">🚚 Consegna</span>
                  )}
                  {s.has_click_collect && (
                    <span className="text-xs bg-purple-50 text-purple-700 px-2 py-0.5 rounded">🏪 Click&Collect</span>
                  )}
                </div>
              </div>
              <span className="text-sm font-bold text-primary whitespace-nowrap">
                {s.distance_km} km
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
