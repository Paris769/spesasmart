"use client";
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  Circle,
  Polygon,
  CircleMarker,
  useMapEvents,
} from "react-leaflet";
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

type LatLng = [number, number];

interface Props {
  center: LatLng;
  stores: Store[];
  radiusKm: number;
  /** Modalità disegno: i click sulla mappa aggiungono vertici. */
  drawMode?: boolean;
  /** Vertici del poligono in fase di disegno. */
  draftArea?: LatLng[];
  /** Area personalizzata già confermata. */
  savedArea?: LatLng[] | null;
  /** Callback su click in modalità disegno. */
  onMapClick?: (latlng: LatLng) => void;
}

/** Cattura i click sulla mappa quando si sta disegnando l'area. */
function ClickHandler({ onClick }: { onClick: (p: LatLng) => void }) {
  useMapEvents({
    click: (e) => onClick([e.latlng.lat, e.latlng.lng]),
  });
  return null;
}

const AREA_STYLE = {
  color: "#16a34a",
  fillColor: "#16a34a",
  fillOpacity: 0.12,
  weight: 2,
};

export default function MapView({
  center,
  stores,
  radiusKm,
  drawMode = false,
  draftArea = [],
  savedArea = null,
  onMapClick,
}: Props) {
  const polygon = drawMode ? draftArea : savedArea ?? [];
  // Il cerchio del raggio si mostra solo se non c'è un'area personalizzata
  const showCircle = !drawMode && (!savedArea || savedArea.length < 3);

  return (
    <MapContainer center={center} zoom={12} className="h-full w-full">
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='© <a href="https://www.openstreetmap.org/">OpenStreetMap</a>'
      />

      {drawMode && onMapClick && <ClickHandler onClick={onMapClick} />}

      {/* Cerchio raggio ricerca */}
      {showCircle && (
        <Circle
          center={center}
          radius={radiusKm * 1000}
          pathOptions={{ color: "#16a34a", fillColor: "#16a34a", fillOpacity: 0.05 }}
        />
      )}

      {/* Poligono area personalizzata (confermata o in disegno) */}
      {polygon.length >= 3 && (
        <Polygon positions={polygon} pathOptions={AREA_STYLE} />
      )}

      {/* Vertici durante il disegno */}
      {drawMode &&
        draftArea.map((p, i) => (
          <CircleMarker
            key={i}
            center={p}
            radius={5}
            pathOptions={{
              color: "#16a34a",
              fillColor: "#ffffff",
              fillOpacity: 1,
              weight: 2,
            }}
          />
        ))}

      {/* Marker utente */}
      <Marker position={center}>
        <Popup>La tua posizione</Popup>
      </Marker>

      {/* Marker negozi */}
      {stores.map((s) => (
        <Marker key={s.id} position={[0, 0]}>
          <Popup>
            <strong>{s.chain_name}</strong>
            <br />
            {s.name}
            <br />
            {s.address}
            <br />
            {s.distance_km} km
            {s.shop_url && (
              <>
                <br />
                <a href={s.shop_url} target="_blank" rel="noopener noreferrer">
                  Acquista online →
                </a>
              </>
            )}
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  );
}
