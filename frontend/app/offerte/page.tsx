"use client";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { BadgePercent, RefreshCw, SearchX } from "lucide-react";
import { getNearbyOffers } from "@/lib/api";
import { useAppStore } from "@/lib/store";
import LocationBar from "@/components/ui/LocationBar";
import EmptyState from "@/components/ui/EmptyState";
import OfferCard from "@/components/ui/OfferCard";
import { PriceCardSkeletonList } from "@/components/ui/PriceCardSkeleton";

export default function OffersPage() {
  const { location, radiusKm } = useAppStore();
  const [chainFilter, setChainFilter] = useState<string | null>(null);

  const {
    data: offers,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ["offers-nearby", location?.lat, location?.lng, radiusKm],
    queryFn: () => getNearbyOffers(location!.lat, location!.lng, radiusKm),
    enabled: !!location,
    staleTime: 15 * 60 * 1000, // allineato alla cache del backend (15 min)
    retry: (failureCount, err) =>
      (err as AxiosError)?.response?.status === 404 ? false : failureCount < 2,
  });

  // Backend vecchio senza /offers/nearby: niente errore rosso, solo un avviso.
  const notDeployed =
    isError && (error as AxiosError | undefined)?.response?.status === 404;

  // Chips dalle catene realmente presenti nei risultati
  const chains = useMemo(() => {
    const seen = new Map<string, string>();
    for (const o of offers ?? []) {
      if (!seen.has(o.chain_slug)) seen.set(o.chain_slug, o.chain_name);
    }
    return Array.from(seen, ([slug, name]) => ({ slug, name }));
  }, [offers]);

  // Se il filtro attivo non esiste piu' nei risultati (es. cambio zona), reset
  useEffect(() => {
    if (chainFilter && !chains.some((c) => c.slug === chainFilter)) {
      setChainFilter(null);
    }
  }, [chains, chainFilter]);

  const visible = useMemo(
    () =>
      (offers ?? []).filter((o) => !chainFilter || o.chain_slug === chainFilter),
    [offers, chainFilter]
  );

  return (
    <div className="flex flex-col gap-4">
      <LocationBar />

      <div className="flex items-center gap-2">
        <h1 className="text-xl font-bold text-deep">Offerte vicino a te</h1>
        {offers && offers.length > 0 && (
          <span className="text-xs font-semibold text-accent bg-accent-50 px-2 py-0.5 rounded-pill">
            {offers.length} promo
          </span>
        )}
      </div>

      {!location && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800">
          Attiva la posizione (o scegli una citta) per vedere le offerte dei
          supermercati della tua zona.
        </div>
      )}

      {location && isLoading && <PriceCardSkeletonList n={6} />}

      {location && notDeployed && (
        <EmptyState
          Icon={BadgePercent}
          title="Offerte in arrivo"
          subtitle="Stiamo preparando le promozioni della tua zona. Riprova tra poco."
        />
      )}

      {location && isError && !notDeployed && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700 flex flex-col items-start gap-2">
          <p>Non riusciamo a caricare le offerte in questo momento.</p>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-1 text-[13px] font-semibold px-3 py-1.5 rounded-btn bg-stone-900 text-white hover:bg-stone-700 transition disabled:opacity-60"
          >
            <RefreshCw size={13} className={isFetching ? "animate-spin" : ""} />
            Riprova
          </button>
        </div>
      )}

      {location && !isLoading && !isError && offers && offers.length === 0 && (
        <EmptyState
          Icon={SearchX}
          title="Nessuna offerta trovata"
          subtitle="Prova ad allargare il raggio di ricerca o a cambiare zona."
        />
      )}

      {location && !isLoading && !isError && offers && offers.length > 0 && (
        <>
          {chains.length > 1 && (
            <div className="flex gap-1.5 flex-wrap">
              <button
                onClick={() => setChainFilter(null)}
                className={`text-xs px-3 py-1.5 rounded-pill border transition ${
                  chainFilter === null
                    ? "bg-primary text-white border-primary"
                    : "bg-white border-stone-200 text-stone-600 hover:border-primary"
                }`}
              >
                Tutte
              </button>
              {chains.map((c) => (
                <button
                  key={c.slug}
                  onClick={() =>
                    setChainFilter((cur) => (cur === c.slug ? null : c.slug))
                  }
                  className={`text-xs px-3 py-1.5 rounded-pill border transition ${
                    chainFilter === c.slug
                      ? "bg-primary text-white border-primary"
                      : "bg-white border-stone-200 text-stone-600 hover:border-primary"
                  }`}
                >
                  {c.name}
                </button>
              ))}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {visible.map((o) => (
              <OfferCard key={`${o.product_id}-${o.chain_slug}`} offer={o} />
            ))}
          </div>

          {visible.length === 0 && (
            <EmptyState
              Icon={SearchX}
              title="Nessuna offerta per questa catena"
              subtitle="Togli il filtro per vedere tutte le promozioni della zona."
            />
          )}

          <p className="text-[11px] text-stone-400 text-center pb-2">
            Le migliori promozioni entro {radiusKm} km, piu' la spesa online
            disponibile nella tua zona. Una offerta per prodotto e catena.
          </p>
        </>
      )}
    </div>
  );
}
