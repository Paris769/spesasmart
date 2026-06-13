import type { MetadataRoute } from "next";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";
const SITE = process.env.NEXT_PUBLIC_SITE_URL || "https://spesasmart-seven.vercel.app";

export const revalidate = 21600; // 6h

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const base: MetadataRoute.Sitemap = [
    { url: SITE, changeFrequency: "daily", priority: 1 },
    { url: `${SITE}/lista`, changeFrequency: "weekly", priority: 0.8 },
    { url: `${SITE}/mappa`, changeFrequency: "monthly", priority: 0.5 },
  ];
  // Il backend (Render free) può essere in sleep al momento della
  // rigenerazione: un singolo fetch fallirebbe e produrrebbe una sitemap vuota
  // (solo le pagine base). Ritentiamo con timeout generoso per dargli il tempo
  // di svegliarsi — meglio una rigenerazione lenta che una sitemap senza prodotti.
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const r = await fetch(`${API}/products/seo/sitemap?limit=5000`, {
        next: { revalidate },
        signal: AbortSignal.timeout(55000),
      });
      if (!r.ok) continue;
      const items: { id: string; updated_at?: string }[] = await r.json();
      if (!items.length) continue;
      return [
        ...base,
        ...items.map((it) => ({
          url: `${SITE}/p/${it.id}`,
          lastModified: it.updated_at ? new Date(it.updated_at) : undefined,
          changeFrequency: "weekly" as const,
          priority: 0.6,
        })),
      ];
    } catch {
      // timeout/cold start: ritenta
    }
  }
  return base;
}
