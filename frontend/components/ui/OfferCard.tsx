"use client";
import Link from "next/link";
import { NearbyOffer } from "@/lib/api";
import {
  CalendarDays,
  Globe,
  MapPin,
  Newspaper,
  ShoppingCart,
  Tag,
} from "lucide-react";

// Pallino col colore di brand della catena (come in PriceCard: l'arancio
// resta riservato ai deal).
const CHAIN_DOT: Record<string, string> = {
  esselunga: "#E2001A",
  conad: "#E30613",
  carrefour: "#004E9F",
  coop: "#E2001A",
  lidl: "#0050AA",
  eurospin: "#FFDD00",
  md: "#F58220",
  aldi: "#004B93",
  penny: "#CD1719",
  pam: "#C8102E",
  famila: "#E2001A",
  ilgigante: "#F39200",
  italmark: "#0093D0",
};

/** "2026-08-15" -> "15 agosto" (null se la data non e' valida). */
function fmtExpiry(iso: string): string | null {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return d.toLocaleDateString("it-IT", { day: "numeric", month: "long" });
}

export default function OfferCard({ offer }: { offer: NearbyOffer }) {
  const dot = CHAIN_DOT[offer.chain_slug] ?? "#6B7280";
  const [eur, cent] = Number(offer.price).toFixed(2).split(".");
  const isFlyer = offer.source === "flyer";
  const expiry = offer.promo_expires ? fmtExpiry(offer.promo_expires) : null;
  const hasStrike =
    offer.original_price != null && offer.original_price > offer.price;

  return (
    <Link
      href={`/p/${offer.product_id}`}
      className="relative overflow-hidden rounded-2xl p-3.5 flex gap-3 bg-white border border-stone-200 shadow-card animate-pop-in transition hover:shadow-cardHover active:scale-[0.99]"
    >
      {/* immagine prodotto */}
      <div className="shrink-0 w-[60px] h-[60px] rounded-xl bg-white border border-stone-200 grid place-items-center overflow-hidden">
        {offer.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={offer.image_url}
            alt=""
            className="w-full h-full object-contain"
          />
        ) : (
          <ShoppingCart size={22} className="text-stone-300" />
        )}
      </div>

      {/* corpo */}
      <div className="flex-1 min-w-0 flex flex-col gap-1">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-deep">
            <span className="w-2 h-2 rounded-full" style={{ background: dot }} />
            {offer.chain_name}
          </span>
          {offer.discount_pct != null && offer.discount_pct >= 1 && (
            <span className="text-[11px] font-bold text-white bg-accent px-1.5 py-0.5 rounded-pill">
              -{offer.discount_pct}%
            </span>
          )}
          {isFlyer && (
            <span className="inline-flex items-center gap-0.5 text-[10px] font-bold text-amber-800 bg-amber-100 px-1.5 py-0.5 rounded-pill">
              <Newspaper size={11} /> volantino
            </span>
          )}
        </div>

        <p className="text-[13px] font-medium text-stone-900 leading-tight line-clamp-2">
          {offer.product_name}
          {offer.brand && (
            <span className="text-stone-500 font-normal"> - {offer.brand}</span>
          )}
        </p>

        <div className="flex items-end gap-2 flex-wrap">
          <span className="text-deep font-extrabold tnum leading-none flex items-start">
            <span className="text-[13px] mt-0.5 mr-0.5">&euro;</span>
            <span className="text-[24px] tracking-tight">{eur}</span>
            <span className="text-[14px] mt-0.5">,{cent}</span>
          </span>
          {hasStrike && (
            <span className="text-sm line-through text-stone-400 tnum mb-0.5">
              &euro;{Number(offer.original_price).toFixed(2)}
            </span>
          )}
          {offer.price_per_unit != null && (
            <span className="text-[11px] text-stone-500 tnum mb-0.5">
              &euro;{Number(offer.price_per_unit).toFixed(2).replace(".", ",")}/u
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5 flex-wrap text-[11px] text-stone-500">
          {offer.distance_km != null ? (
            <span className="inline-flex items-center gap-0.5">
              <MapPin size={11} /> {offer.distance_km} km - {offer.store_name}
            </span>
          ) : (
            <span className="inline-flex items-center gap-0.5">
              <Globe size={11} /> Spesa online
            </span>
          )}
          {expiry && (
            <span className="inline-flex items-center gap-0.5 text-amber-800 bg-amber-50 px-1.5 py-0.5 rounded-pill font-medium">
              <CalendarDays size={11} /> fino al {expiry}
            </span>
          )}
          {offer.promo_label && (
            <span className="inline-flex items-center gap-1 bg-accent-50 text-accent font-semibold px-1.5 py-0.5 rounded-pill max-w-[180px] truncate">
              <Tag size={11} className="shrink-0" /> {offer.promo_label}
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
