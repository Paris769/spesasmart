import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import PriceWatch from "@/components/ui/PriceWatch";
import PromoCheck from "@/components/ui/PromoCheck";
import AvvisoConfrontoMisto from "@/components/ui/AvvisoConfrontoMisto";
import OrigineBadge from "@/components/ui/OrigineBadge";
import { confrontoMisto, parseOrigine } from "@/lib/priceOrigin";

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
  in_stock: boolean;
  shop_url: string | null;
  product_url: string | null;
  /** Provenienza dichiarata dal backend (vedi lib/api.ts PriceResult.origine).
   *  Senza questi due campi la pagina perdeva la provenienza al parse, ed e'
   *  la pagina che Google indicizza. Opzionali: backend vecchio = niente
   *  etichetta e niente structured data sulle offerte. */
  origine?: string | null;
  origine_label?: string | null;
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
  /** Avviso sul confronto misto, calcolato dal backend sulle stesse offerte
   *  che questa pagina mostra. */
  avviso?: string | null;
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
  // "da EUR X" finisce nel <title> e nella description, cioe' nel testo che
  // Google mostra in SERP: e' la prima affermazione di prezzo che l'utente
  // legge, e in SERP non c'e' nessuna etichetta accanto a smentirla.
  //
  // min_price NON e' quel numero: GET /products/{id} non filtra promo_expires
  // e il DISTINCT ON per catena ordina per prezzo crescente, quindi preferisce
  // sistematicamente il volantino al listino. MISURATO il 29/10/2026 sulle
  // righe servite: per 429 prodotti su 79.800 la riga col prezzo piu' basso e'
  // un'offerta GIA' SCADUTA. In SERP l'utente leggeva "da EUR 0,89", cliccava
  // e in pagina trovava accanto a quel numero il badge rosso "offerta scaduta
  // il 18/10".
  // Quindi il numero del titolo si prende dalle sole offerte OTTENIBILI: non
  // le scadute e non quelle segnalate da un utente (non verificate; oggi
  // nessun prodotto ha il minimo su una riga segnalata, misurato).
  //
  // COSTO SEO ACCETTATO, come per l'AggregateOffer qui sotto: se il backend
  // non dichiara l'origine (finestra di deploy Vercel/Render) il frammento
  // sparisce da tutti i titoli finche' Render non e' allineato. Un titolo
  // senza prezzo rende meno; un titolo con un prezzo che non si puo' pagare
  // costa la fiducia di chi ci clicca.
  const prezziOttenibili = p.offers
    .filter((o) => {
      const origine = parseOrigine(o.origine);
      return origine !== null && origine !== "scaduta" && origine !== "segnalato";
    })
    .map((o) => Number(o.price))
    .filter((n) => Number.isFinite(n));
  const minOttenibile = prezziOttenibili.length ? Math.min(...prezziOttenibili) : null;
  const from = minOttenibile != null ? ` da €${minOttenibile.toFixed(2)}` : "";
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

  // Structured data: va fuori dall'app e diventa una dichiarazione
  // commerciale nei rich result di Google. Ci vanno SOLO le offerte che una
  // persona puo' davvero comprare a quel prezzo da casa, cioe' quelle di
  // provenienza "online". min_price/max_price sono aggregati su origini
  // mescolate (il DISTINCT ON per catena preferisce sistematicamente il
  // volantino al listino), quindi lowPrice/highPrice si ricalcolano sulle
  // sole offerte pubblicate: un prezzo da volantino settimanale esposto a
  // Google come prezzo del prodotto manda l'utente in negozio a pagare altro.
  // Se nessuna offerta e' online (o il backend non dichiara la provenienza)
  // l'AggregateOffer non viene emesso: meglio nessun rich result che uno
  // sbagliato.
  //
  // COSTO SEO ACCETTATO, messo a verbale perche' non e' un dettaglio: questo
  // gate tolse il blocco `offers` a pagine che prima lo avevano, e sono due
  // casi distinti.
  // 1) Finestra di deploy: Vercel si aggiorna prima di Render, il backend non
  //    manda ancora `origine`, parseOrigine ritorna null su tutte le offerte
  //    e OGNI pagina prodotto indicizzata perde i rich result finche' Render
  //    non e' allineato. E' transitorio ma riguarda tutto il catalogo.
  // 2) Permanente: un prodotto i cui soli prezzi vengono da
  //    aldi/md/lidl/penny/flyer/eurospin_web non ha nessuna offerta "online" e
  //    la sua pagina non avra' mai l'AggregateOffer.
  // Si accetta perche' l'alternativa e' esporre a Google come prezzo del
  // prodotto un volantino valido una settimana in un solo negozio. Se domani
  // si volesse recuperare la copertura, la strada e' emettere le offerte
  // online E quelle di scaffale con `priceValidUntil` e `availableAtOrFrom`
  // corretti, non allargare il filtro.
  const offerteOnline = p.offers.filter((o) => parseOrigine(o.origine) === "online");
  const prezziOnline = offerteOnline.map((o) => Number(o.price));
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "Product",
    name: p.name,
    ...(p.image_url ? { image: p.image_url } : {}),
    ...(p.brand ? { brand: { "@type": "Brand", name: p.brand } } : {}),
    ...(p.barcode && /^[0-9]{8,14}$/.test(p.barcode) ? { gtin: p.barcode } : {}),
    ...(offerteOnline.length
      ? {
          offers: {
            "@type": "AggregateOffer",
            priceCurrency: "EUR",
            lowPrice: Math.min(...prezziOnline),
            highPrice: Math.max(...prezziOnline),
            offerCount: offerteOnline.length,
            offers: offerteOnline.map((o) => ({
              "@type": "Offer",
              price: o.price,
              priceCurrency: "EUR",
              availability: o.in_stock === false
                ? "https://schema.org/OutOfStock"
                : "https://schema.org/InStock",
              seller: { "@type": "Organization", name: o.chain_name },
            })),
          },
        }
      : {}),
  };

  // L'avviso si mostra sulle offerte che l'utente VEDE in questa pagina. Il
  // testo e' quello del backend quando lo manda, cosi' la frase resta una
  // sola in tutta l'app.
  const mostraAvviso = confrontoMisto(p.offers.map((o) => parseOrigine(o.origine)));
  // Provenienza del prezzo piu' basso, quello dell'intestazione: "a partire
  // da" cambia significato se quel numero e' un'offerta a tempo da fare in
  // negozio. L'offerta non si prende per posizione (la lista mette prima i
  // disponibili, poi il prezzo: offers[0] non e' detto sia la piu' economica)
  // e l'etichetta si mostra solo se quel prezzo e' davvero quello scritto
  // accanto, altrimenti si etichetterebbe un numero con la natura di un
  // altro.
  const offertaMinima = p.offers.reduce<Offer | null>(
    (min, o) => (min === null || Number(o.price) < Number(min.price) ? o : min),
    null
  );
  const etichettaMinimo =
    offertaMinima &&
    p.min_price != null &&
    Math.abs(Number(offertaMinima.price) - Number(p.min_price)) < 0.005
      ? offertaMinima
      : null;
  const origineMigliore = parseOrigine(etichettaMinimo?.origine);

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
            <>
              <p className="mt-2 text-stone-700">
                A partire da{" "}
                <strong className="text-price text-deep tnum">
                  €{Number(p.min_price).toFixed(2)}
                </strong>{" "}
                in {p.store_count} supermercat{p.store_count > 1 ? "i" : "o"}
              </p>
              {origineMigliore && (
                <p className="mt-1">
                  <OrigineBadge
                    origine={origineMigliore}
                    label={etichettaMinimo?.origine_label}
                  />
                </p>
              )}
            </>
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
                {/* nome catena ed etichetta in colonna: il <li> e' flex, se
                    l'etichetta stesse sulla stessa riga del nome spingerebbe
                    il prezzo fuori dallo schermo del telefono. */}
                <span className="flex-1 min-w-0 flex flex-col items-start">
                  <span className="font-medium text-stone-800">
                    {o.chain_name}
                    {i === 0 && (
                      <span className="ml-2 text-[11px] font-bold text-primary">
                        ✓ migliore
                      </span>
                    )}
                  </span>
                  <OrigineBadge
                    origine={parseOrigine(o.origine)}
                    label={o.origine_label}
                  />
                </span>
                <span className="font-bold text-deep tnum">
                  €{Number(o.price).toFixed(2)}
                </span>
                {(o.product_url || o.shop_url) && (
                  <a
                    href={goUrl(o.product_url || o.shop_url, o.chain_slug)}
                    target="_blank"
                    rel="noopener noreferrer sponsored"
                    className={`text-[13px] px-3 py-1.5 rounded-btn font-semibold ${
                      o.in_stock === false
                        ? "bg-stone-200 text-stone-700"
                        : "bg-primary text-white"
                    }`}
                  >
                    {o.in_stock === false ? "Verifica" : "Acquista"}
                  </a>
                )}
              </li>
            ))}
          </ul>
          {mostraAvviso && (
            <div className="mt-2">
              <AvvisoConfrontoMisto testo={p.avviso} />
            </div>
          )}
          <p className="text-[11px] text-stone-400 mt-1.5">
            Disponibili mostrati prima, poi prezzo. Alcuni link possono essere affiliati (ADV).
          </p>
        </section>
      )}

      {/* Isole client: avviso di prezzo + verifica offerta (fallback grazioso
          se le API non esistono ancora, il rendering server resta invariato). */}
      <PriceWatch productId={p.id} productName={p.name} />
      <PromoCheck productId={p.id} />

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
