import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";

// Pagina prodotto SERVER-RENDERED e indicizzabile: chi cerca "prezzo <prodotto>"
// su Google atterra qui. Genera meta tag + dati strutturati (schema.org Product
// + AggregateOffer) per i rich result. ISR: rigenerata ogni ora.

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";
const SITE = process.env.NEXT_PUBLIC_SITE_URL || "https://spesasmart-seven.vercel.app";

export const revalidate = 3600;

interface Offer {
  chain_name: string;
  chain_slug: string;
  price: number;
  shop_url: string | null;
  product_url: string | null;
}
interface ProductSEO {
  id: string;
  barcode: string | null;
  name: string;
  brand: string | null;
  image_url: string | null;
  min_price: number | null;
  max_price: number | null;
  store_count: number;
  offers: Offer[];
}

async function getProduct(id: string): Promise<ProductSEO | null> {
  try {
    const r = await fetch(`${API}/products/${id}`, { next: { revalidate } });
    if (!r.ok) return null;
    return (await r.json()) as ProductSEO;
  } catch {
    return null;
  }
}

const goUrl = (url: string | null, chain?: string) =>
  url
    ? `${API}/go?u=${encodeURIComponent(url)}${chain ? `&chain=${chain}` : ""}`
    : "#";

export async function generateMetadata({
  params,
}: {
  params: { id: string };
}): Promise<Metadata> {
  const p = await getProduct(params.id);
  if (!p) return { title: "Prodotto non trovato — SpesaSmart" };
  const from = p.min_price != null ? ` da €${Number(p.min_price).toFixed(2)}` : "";
  const title = `${p.name}${from} — Confronta i prezzi | SpesaSmart`;
  const description = `${p.name}${p.brand ? ` (${p.brand})` : ""}: confronta i prezzi in ${p.store_count} supermercati${from}. Trova dove costa meno con SpesaSmart.`;
  return {
    title,
    description,
    alternates: { canonical: `/p/${params.id}` },
    openGraph: {
      title,
      description,
      url: `${SITE}/p/${params.id}`,
      images: p.image_url ? [{ url: p.image_url }] : [],
      type: "website",
    },
  };
}

export default async function ProductPage({ params }: { params: { id: string } }) {
  const p = await getProduct(params.id);
  if (!p) notFound();

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "Product",
    name: p.name,
    ...(p.image_url ? { image: p.image_url } : {}),
    ...(p.brand ? { brand: { "@type": "Brand", name: p.brand } } : {}),
    ...(p.barcode && /^[0-9]{8,14}$/.test(p.barcode) ? { gtin: p.barcode } : {}),
    ...(p.offers.length
      ? {
          offers: {
            "@type": "AggregateOffer",
            priceCurrency: "EUR",
            lowPrice: p.min_price,
            highPrice: p.max_price,
            offerCount: p.store_count,
            offers: p.offers.map((o) => ({
              "@type": "Offer",
              price: o.price,
              priceCurrency: "EUR",
              availability: "https://schema.org/InStock",
              seller: { "@type": "Organization", name: o.chain_name },
            })),
          },
        }
      : {}),
  };

  return (
    <article className="flex flex-col gap-5">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <nav className="text-[13px] text-stone-400">
        <Link href="/" className="hover:text-primary">
          SpesaSmart
        </Link>{" "}
        / Prezzi
      </nav>

      <header className="flex gap-4 items-start">
        {p.image_url && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={p.image_url}
            alt={p.name}
            className="w-24 h-24 object-contain rounded-card border border-stone-200 bg-white shrink-0"
          />
        )}
        <div>
          <h1 className="text-2xl font-bold text-deep leading-tight">{p.name}</h1>
          {p.brand && <p className="text-stone-500">{p.brand}</p>}
          {p.min_price != null && (
            <p className="mt-2 text-stone-700">
              A partire da{" "}
              <strong className="text-price text-deep tnum">
                €{Number(p.min_price).toFixed(2)}
              </strong>{" "}
              in {p.store_count} supermercat{p.store_count > 1 ? "i" : "o"}
            </p>
          )}
        </div>
      </header>

      {p.offers.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold text-deep mb-2">
            Confronto prezzi
          </h2>
          <ul className="bg-white border border-stone-200 rounded-card shadow-card divide-y divide-stone-100">
            {p.offers.map((o, i) => (
              <li key={i} className="flex items-center gap-3 px-4 py-3">
                <span className="flex-1 font-medium text-stone-800">
                  {o.chain_name}
                  {i === 0 && (
                    <span className="ml-2 text-[11px] font-bold text-primary">
                      ✓ migliore
                    </span>
                  )}
                </span>
                <span className="font-bold text-deep tnum">
                  €{Number(o.price).toFixed(2)}
                </span>
                {(o.product_url || o.shop_url) && (
                  <a
                    href={goUrl(o.product_url || o.shop_url, o.chain_slug)}
                    target="_blank"
                    rel="noopener noreferrer sponsored"
                    className="text-[13px] bg-primary text-white px-3 py-1.5 rounded-btn font-semibold"
                  >
                    Acquista
                  </a>
                )}
              </li>
            ))}
          </ul>
          <p className="text-[11px] text-stone-400 mt-1.5">
            Classifica ordinata solo per prezzo. Alcuni link possono essere affiliati (ADV).
          </p>
        </section>
      )}

      <section className="rounded-card bg-hero-grad text-white p-5 relative overflow-hidden">
        <div className="absolute inset-0 bg-mesh" aria-hidden />
        <div className="relative">
          <h2 className="text-xl font-bold">Risparmia sulla spesa di tutti i giorni</h2>
          <p className="text-white/85 text-sm mt-1">
            Confronta i prezzi di migliaia di prodotti nei supermercati vicino a te.
          </p>
          <Link
            href="/"
            className="inline-block mt-3 bg-white text-deep font-semibold px-4 py-2 rounded-btn"
          >
            Cerca un prodotto →
          </Link>
        </div>
      </section>
    </article>
  );
}
