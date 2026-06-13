import type { MetadataRoute } from "next";

// Manifest PWA: rende l'app installabile (add-to-homescreen) e dà il tema.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "SpesaSmart — Confronta i prezzi della spesa",
    short_name: "SpesaSmart",
    description:
      "Confronta i prezzi dei supermercati vicino a te e risparmia sulla spesa.",
    start_url: "/",
    display: "standalone",
    background_color: "#FAF8F5",
    theme_color: "#16A34A",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml" }],
  };
}
