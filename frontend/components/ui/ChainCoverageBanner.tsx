"use client";
import { useEffect, useState } from "react";
import { Info } from "lucide-react";
import { ChainCoverage, getChainsCoverage } from "@/lib/api";

// Banner copertura catene: legge /stores/coverage e distingue le catene con
// confronto prezzi completo da quelle con sole offerte/volantino.
// Se l'endpoint non esiste ancora (404/503) o fallisce, mostra il testo
// statico attuale: il frontend deve funzionare anche col backend non aggiornato.

const FALLBACK_TEXT =
  "SpesaSmart confronta le catene con dati disponibili: Carrefour, Conad, " +
  "Esselunga, Famila, Il Gigante, Iper, Coop/Ipercoop, Lidl, Eurospin, " +
  "Aldi, MD, Penny e Pam. Alcuni prezzi, negozi o offerte possono non " +
  "comparire per zona, prodotto, disponibilita online o aggiornamento dati.";

export default function ChainCoverageBanner() {
  const [chains, setChains] = useState<ChainCoverage[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getChainsCoverage()
      .then((data) => {
        if (!cancelled) setChains(data);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const full = (chains || []).filter((c) => c.tier === "full");
  const promo = (chains || []).filter((c) => c.tier === "promo");

  // Loading, errore o risposta vuota: testo generico attuale.
  if (failed || chains === null || (full.length === 0 && promo.length === 0)) {
    return (
      <div className="rounded-card border border-amber-200 bg-amber-50 px-3 py-2 flex gap-2 text-xs text-amber-900">
        <Info size={16} className="mt-0.5 shrink-0" />
        <p>{FALLBACK_TEXT}</p>
      </div>
    );
  }

  return (
    <div className="rounded-card border border-stone-200 bg-white px-3 py-2.5 shadow-card flex gap-2 text-xs text-stone-600">
      <Info size={16} className="mt-0.5 shrink-0 text-primary" />
      <div className="flex flex-col gap-1">
        {full.length > 0 && (
          <p>
            <span className="font-bold text-deep">Confronto completo:</span>{" "}
            {full.map((c) => c.name).join(", ")}.
          </p>
        )}
        {promo.length > 0 && (
          <p>
            <span className="font-bold text-deep">Solo offerte e volantino:</span>{" "}
            {promo.map((c) => c.name).join(", ")}.
          </p>
        )}
        <p className="text-stone-400">
          Alcuni prezzi, negozi o offerte possono non comparire per zona,
          prodotto, disponibilita online o aggiornamento dati.
        </p>
      </div>
    </div>
  );
}
