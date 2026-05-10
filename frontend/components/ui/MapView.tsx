"use client";
import { MapContainer, TileLayer, Marker, Popup, Circle } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import L from "leaflet";
import { Store } from "@/lib/api";

// Fix icone Leaflet in Next.js
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
});

interface Props {
  center: [number, number];
  stores: Store[];
  radiusKm: number;
}

export default function MapView({ center, stores, radiusKm }: Props) {
  return (
    <MapContainer center={center} zoom={13} className="h-full w-full">
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='© <a href="https://www.openstreetmap.org/">OpenStreetMap</a>'
      />

      {/* Cerchio raggio ricerca */}
      <Circle
        center={center}
        radius={radiusKm * 1000}
        pathOptions={{ color: "#16a34a", fillColor: "#16a34a", fillOpacity: 0.05 }}
      />

      {/* Marker utente */}
      <Marker position={center}>
        <Popup>La tua posizione</Popup>
      </Marker>

      {/* Marker negozi */}
      {stores.map((s) => (
        <Marker key={s.id} position={[0, 0]}>
          <Popup>
            <strong>{s.chain_name}</strong><br />
            {s.name}<br />
            {s.address}<br />
            {s.distance_km} km
            {s.shop_url && (
              <><br /><a href={s.shop_url} target="_blank" rel="noopener noreferrer">Acquista online →</a></>
            )}
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  );
}
