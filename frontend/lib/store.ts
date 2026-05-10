import { create } from "zustand";
import { persist } from "zustand/middleware";

interface Location {
  lat: number;
  lng: number;
  label: string;
}

interface AppState {
  location: Location | null;
  radiusKm: number;
  setLocation: (loc: Location) => void;
  setRadius: (km: number) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      location: null,
      radiusKm: 5,
      setLocation: (loc) => set({ location: loc }),
      setRadius: (km) => set({ radiusKm: km }),
    }),
    { name: "spesasmart-prefs" }
  )
);
