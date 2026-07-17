"use client";
import { useEffect, useState, FormEvent } from "react";
import { Bell, CheckCircle2 } from "lucide-react";
import { createWatch } from "@/lib/api";

// Isola client per la pagina prodotto (server-rendered): avviso di prezzo.
// POST /watches {product_id, email, threshold_price?}. Se l'endpoint non
// esiste ancora, mostra un messaggio grazioso senza rompere la pagina.

const EMAIL_KEY = "spesasmart_email";

interface Props {
  productId: string;
  productName?: string;
}

export default function PriceWatch({ productId, productName }: Props) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [threshold, setThreshold] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "done" | "error">("idle");
  const [errorText, setErrorText] = useState<string | null>(null);
  // Soglia effettivamente inviata al backend: la conferma deve riflettere
  // cio che e stato creato davvero (null = avviso a ogni ribasso).
  const [savedThreshold, setSavedThreshold] = useState<number | null>(null);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(EMAIL_KEY);
      if (stored) setEmail(stored);
    } catch {
      // storage non disponibile
    }
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const cleanEmail = email.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(cleanEmail)) {
      setErrorText("Inserisci un'email valida per ricevere l'avviso.");
      return;
    }
    const cleanThreshold = threshold.trim();
    let thresholdPrice: number | undefined;
    if (cleanThreshold) {
      const parsed = Number(cleanThreshold.replace(",", "."));
      if (!Number.isFinite(parsed) || parsed <= 0) {
        setErrorText("Soglia non valida: inserisci solo un numero, es. 1,49, oppure lascia vuoto.");
        return;
      }
      thresholdPrice = parsed;
    }

    setStatus("saving");
    setErrorText(null);
    try {
      await createWatch(productId, cleanEmail, thresholdPrice);
      try {
        localStorage.setItem(EMAIL_KEY, cleanEmail);
      } catch {
        // storage non disponibile
      }
      setSavedThreshold(thresholdPrice ?? null);
      setStatus("done");
    } catch {
      setStatus("error");
      setErrorText(
        "Non riesco ad attivare l'avviso ora: il servizio potrebbe non essere ancora disponibile. Riprova piu tardi."
      );
    }
  };

  if (status === "done") {
    return (
      <div className="rounded-card border border-green-200 bg-green-50 p-3 flex gap-2 text-sm text-green-800">
        <CheckCircle2 size={17} className="shrink-0 mt-0.5" />
        <p>
          Avviso attivo: ti scrivo a <strong>{email.trim()}</strong> se
          {savedThreshold != null
            ? ` il prezzo scende sotto EUR ${savedThreshold.toFixed(2)}`
            : " il prezzo scende, a ogni ribasso"}.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-card border border-stone-200 bg-white p-3 shadow-card flex flex-col gap-2">
      {!open ? (
        <button
          onClick={() => setOpen(true)}
          className="inline-flex items-center justify-center gap-2 bg-stone-900 text-white px-4 py-2.5 rounded-xl text-sm font-bold active:scale-[0.99] transition"
        >
          <Bell size={16} /> Avvisami se scende di prezzo
        </button>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-2">
          <p className="text-sm font-bold text-deep flex items-center gap-1.5">
            <Bell size={15} className="text-primary" /> Avviso di prezzo
            {productName ? <span className="font-normal text-stone-400 truncate">- {productName}</span> : null}
          </p>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="La tua email, es. nome@esempio.it"
            className="border-2 border-stone-200 focus:border-primary rounded-xl px-3 py-2 text-sm outline-none"
          />
          <input
            type="text"
            inputMode="decimal"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            placeholder="Soglia opzionale, es. 1,49 (vuoto = qualsiasi ribasso)"
            className="border-2 border-stone-200 focus:border-primary rounded-xl px-3 py-2 text-sm outline-none"
          />
          <button
            type="submit"
            disabled={status === "saving"}
            className="inline-flex items-center justify-center gap-2 bg-primary text-white px-4 py-2.5 rounded-xl text-sm font-bold disabled:opacity-60 active:scale-[0.99] transition"
          >
            {status === "saving" ? "Attivo l'avviso..." : "Attiva avviso"}
          </button>
          {errorText && (
            <p className="text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-xl px-3 py-2">{errorText}</p>
          )}
          <p className="text-[11px] text-stone-400">
            Usiamo l'email solo per questo avviso. Puoi disattivarlo dal link nell'email.
          </p>
        </form>
      )}
    </div>
  );
}
