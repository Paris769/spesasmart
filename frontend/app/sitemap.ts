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
  try {
    const r = await fetch(`${API}/products/seo/sitemap?limit=5000`, {
      next: { revalidate },
    });
    if (!r.ok) return base;
    const items: { id: string; updated_at?: string }[] = await r.json();
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
    return base;
  }
}
