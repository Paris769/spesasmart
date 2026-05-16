"use client";
import { useEffect, useRef } from "react";
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  Circle,
  Polygon,
  useMap,
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
  /** Modalità disegno: tieni premuto e trascina per tracciare l'area. */
  drawMode?: boolean;
  /** Tracciato dell'area in fase di disegno. */
  draftArea?: LatLng[];
  /** Area personalizzata già confermata. */
  savedArea?: LatLng[] | null;
  /** Callback continuo col tracciato mentre si disegna. */
  onDraftChange?: (pts: LatLng[]) => void;
}

const AREA_STYLE = {
  color: "#16a34a",
  fillColor: "#16a34a",
  fillOpacity: 0.15,
  weight: 2,
};

const MIN_PX_GAP = 8; // distanza minima in pixel tra punti campionati
const MAX_POINTS = 300; // tetto al numero di punti del tracciato

/**
 * Disegno a mano libera: mousedown inizia il tracciato, mousemove lo estende
 * (campionato ogni ~8px), mouseup lo chiude. Durante il disegno il pan della
 * mappa è disattivato così il trascinamento traccia l'area invece di spostare
 * la vista.
 */
function FreehandHandler({
  enabled,
  onDraft,
}: {
  enabled: boolean;
  onDraft: (pts: LatLng[]) => void;
}) {
  const map = useMap();
  const drawing = useRef(false);
  const pts = useRef<LatLng[]>([]);

  useEffect(() => {
    const container = map.getContainer();
    if (enabled) container.style.cursor = "crosshair";
    else container.style.cursor = "";
    // alla pulizia: ripristina cursore e pan
    return () => {
      container.style.cursor = "";
      map.dragging.enable();
    };
  }, [enabled, map]);

  useMapEvents({
    mousedown(e) {
      if (!enabled) return;
      drawing.current = true;
      pts.current = [[e.latlng.lat, e.latlng.lng]];
      map.dragging.disable();
      onDraft([...pts.current]);
    },
    mousemove(e) {
      if (!enabled || !drawing.current) return;
      if (pts.current.length >= MAX_POINTS) return;
      const last = pts.current[pts.current.length - 1];
      const lastPx = map.latLngToContainerPoint(L.latLng(last[0], last[1]));
      if (lastPx.distanceTo(e.containerPoint) < MIN_PX_GAP) return;
      pts.current.push([e.latlng.lat, e.latlng.lng]);
      onDraft([...pts.current]);
    },
    mouseup() {
      if (!enabled || !drawing.current) return;
      drawing.current = false;
      map.dragging.enable();
      onDraft([...pts.current]);
    },
  });

  return null;
}

export default function MapView({
  center,
  stores,
  radiusKm,
  drawMode = false,
  draftArea = [],
  savedArea = null,
  onDraftChange,
}: Props) {
  const polygon = drawMode ? draftArea : savedArea ?? [];
  // Il cerchio del raggio si mostra solo se non c'è un'area personalizzata
  const showCircle = !drawMode && (!savedArea || savedArea.length < 3);

  return (
    <MapContainer center={center} zoom={11} className="h-full w-full">
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='© <a href="https://www.openstreetmap.org/">OpenStreetMap</a>'
      />

      {drawMode && onDraftChange && (
        <FreehandHandler enabled={drawMode} onDraft={onDraftChange} />
      )}

      {/* Cerchio raggio ricerca */}
      {showCircle && (
        <Circle
          center={center}
          radius={radiusKm * 1000}
          pathOptions={{ color: "#16a34a", fillColor: "#16a34a", fillOpacity: 0.05 }}
        />
      )}

      {/* Poligono area (tracciato a mano libera o area confermata) */}
      {polygon.length >= 3 && (
        <Polygon positions={polygon} pathOptions={AREA_STYLE} />
      )}

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
