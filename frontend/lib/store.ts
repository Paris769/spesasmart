import { create } from "zustand";
import { persist } from "zustand/middleware";

interface Location {
  lat: number;
  lng: number;
  label: string;
}

/** Punto del poligono area di ricerca: [lat, lng]. */
export type AreaPoint = [number, number];

interface AppState {
  location: Location | null;
  radiusKm: number;
  /** Poligono personalizzato disegnato sulla mappa. Se valorizzato
   *  (>= 3 punti) sostituisce il raggio nel filtro di ricerca. */
  searchArea: AreaPoint[] | null;
  setLocation: (loc: Location) => void;
  setRadius: (km: number) => void;
  setSearchArea: (area: AreaPoint[] | null) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      location: null,
      radiusKm: 5,
      searchArea: null,
      setLocation: (loc) => set({ location: loc }),
      // impostare un raggio annulla l'area personalizzata
      setRadius: (km) => set({ radiusKm: km, searchArea: null }),
      setSearchArea: (area) =>
        set({ searchArea: area && area.length >= 3 ? area : null }),
    }),
    { name: "spesasmart-prefs" }
  )
);
