"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  BadgePercent,
  Bot,
  CheckCircle2,
  ChevronDown,
  CircleSlash,
  Loader2,
  Mail,
  MapPin,
  Receipt,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import PurchasePlan from "@/components/ui/PurchasePlan";
import RebuildResult from "@/components/ui/RebuildResult";
import { useAppStore } from "@/lib/store";
import { DEFAULT_LOCATION, requestBrowserLocation } from "@/lib/location";
import { isValidEmail, readStoredEmail, storeEmail } from "@/lib/email";
import {
  apiError,
  assistantChat,
  AssistantAction,
  AssistantMessage,
  AssistantNeeds,
  getLastPurchase,
  isMissingEndpoint,
  purchaseItemsToRebuildItems,
  QuickOptimizeResult,
  rebuildFromItems,
  RebuildResultData,
} from "@/lib/api";

/**
 * Box assistente: una barra invitante che si apre in una conversazione.
 *
 * REGOLA DI PROGETTO: il backend (Render) si deploya separatamente dal
 * frontend (Vercel), quindi /assistant/chat puo' rispondere 503
 * "assistant_unavailable" (chiave AI assente) o non esistere affatto. In quel
 * caso NON si mostra un errore tecnico: si offre il percorso senza LLM
 * (ultima spesa + /rebuild/from-items) e i link alle funzioni che girano
 * comunque (/agente, /offerte, /spese).
 */

const SUGGESTIONS = [
  "Rifai la mia ultima spesa scegliendo le offerte",
  "Tieni le mie marche ma fammi risparmiare",
  "Cosa conviene comprare questa settimana",
  "Fammi la spesa della settimana",
];

/** Messaggio mostrato quando parte il percorso diretto (senza AI). */
const DIRECT_MSG = "Rifai l'ultima spesa con le offerte";

/** Turni di conversazione inviati al backend come contesto. */
const MAX_HISTORY = 10;

type AssistantKind = "reply" | "unavailable" | "ratelimit" | "error";

type Turn =
  | { id: string; role: "user"; text: string }
  | {
      id: string;
      role: "assistant";
      kind: AssistantKind;
      text: string;
      actions?: AssistantAction[];
      plan?: QuickOptimizeResult | null;
      rebuild?: RebuildResultData | null;
      needs?: AssistantNeeds | null;
      /** Messaggio utente che ha prodotto il turno: serve per rinviarlo
       *  dopo aver soddisfatto un `needs`. */
      source?: string;
    };

type AssistantTurn = Extract<Turn, { role: "assistant" }>;

/** "2026-10-02" -> "02/10/2026"; stringa vuota se la data non c'e'. */
function formatDay(value?: string | null): string {
  const parts = (value || "").slice(0, 10).split("-");
  return parts.length === 3 && parts[0].length === 4 ? `${parts[2]}/${parts[1]}/${parts[0]}` : "";
}

let turnSeq = 0;
function nextId() {
  turnSeq += 1;
  return `t${turnSeq}`;
}

function toHistory(all: Turn[]): AssistantMessage[] {
  const out: AssistantMessage[] = [];
  for (const turn of all) {
    if (turn.role === "user") {
      if (turn.text.trim()) out.push({ role: "user", content: turn.text });
      continue;
    }
    if (turn.kind === "reply" && turn.text.trim()) {
      out.push({ role: "assistant", content: turn.text });
    }
  }
  return out.slice(-MAX_HISTORY);
}

/** PurchasePlan si fida della forma di QuickOptimizeResult: normalizzo prima
 *  di passarglielo, perche' qui il dato arriva da un backend in evoluzione. */
function safePlan(plan?: QuickOptimizeResult | null): QuickOptimizeResult | null {
  if (!plan || typeof plan !== "object") return null;
  const best = plan.best_single;
  if (!best) return null; // senza best_single PurchasePlan non mostra nulla
  return {
    ...plan,
    n_items: plan.n_items ?? 0,
    n_findable: plan.n_findable ?? 0,
    best_single: { ...best, items: Array.isArray(best.items) ? best.items : [] },
    single_ranking: Array.isArray(plan.single_ranking) ? plan.single_ranking : [],
    multi_store: {
      total: plan.multi_store?.total ?? 0,
      savings_vs_single: plan.multi_store?.savings_vs_single ?? 0,
      stores: (plan.multi_store?.stores || []).map((s) => ({
        ...s,
        items: Array.isArray(s.items) ? s.items : [],
      })),
    },
    not_found: Array.isArray(plan.not_found) ? plan.not_found : [],
  };
}

// -- Markdown minimale (grassetto, corsivo, elenchi): nessuna dipendenza ------

function inlineNodes(text: string, keyBase: string): React.ReactNode[] {
  const chunks = text.split(/(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`)/g);
  const nodes: React.ReactNode[] = [];
  chunks.forEach((chunk, i) => {
    if (!chunk) return;
    const key = `${keyBase}-${i}`;
    if (chunk.length > 4 && chunk.startsWith("**") && chunk.endsWith("**")) {
      nodes.push(
        <strong key={key} className="font-bold text-deep">
          {chunk.slice(2, -2)}
        </strong>
      );
      return;
    }
    if (chunk.length > 2 && chunk.startsWith("`") && chunk.endsWith("`")) {
      nodes.push(
        <code key={key} className="rounded bg-stone-100 px-1 text-[12px]">
          {chunk.slice(1, -1)}
        </code>
      );
      return;
    }
    if (chunk.length > 2 && chunk.startsWith("*") && chunk.endsWith("*")) {
      nodes.push(<em key={key}>{chunk.slice(1, -1)}</em>);
      return;
    }
    nodes.push(<span key={key}>{chunk}</span>);
  });
  return nodes;
}

function MiniMarkdown({ text }: { text: string }) {
  const lines = text.replace(/\r/g, "").split("\n");
  const blocks: React.ReactNode[] = [];
  let bullets: string[] = [];

  const flushBullets = (at: number) => {
    if (!bullets.length) return;
    const current = bullets;
    bullets = [];
    blocks.push(
      <ul key={`ul-${at}`} className="ml-4 list-disc space-y-0.5">
        {current.map((b, i) => (
          <li key={`li-${at}-${i}`}>{inlineNodes(b, `li-${at}-${i}`)}</li>
        ))}
      </ul>
    );
  };

  lines.forEach((raw, index) => {
    const line = raw.trim();
    const bullet = line.match(/^(?:[-*]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      bullets.push(bullet[1]);
      return;
    }
    flushBullets(index);
    if (!line) return;
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (heading) {
      blocks.push(
        <p key={`h-${index}`} className="text-[13px] font-extrabold text-deep">
          {inlineNodes(heading[1], `h-${index}`)}
        </p>
      );
      return;
    }
    blocks.push(<p key={`p-${index}`}>{inlineNodes(line, `p-${index}`)}</p>);
  });
  flushBullets(lines.length);

  return (
    <div className="flex flex-col gap-1.5 text-[13px] leading-relaxed text-stone-700">
      {blocks}
    </div>
  );
}

// -- Pezzi di UI --------------------------------------------------------------

function ActionList({ actions }: { actions: AssistantAction[] }) {
  const rows = actions.filter((a) => a && (a.summary || a.tool));
  if (!rows.length) return null;
  return (
    <ul className="flex flex-col gap-1 rounded-xl bg-surface px-2.5 py-2">
      {rows.map((action, i) => (
        <li key={`${action.tool || "tool"}-${i}`} className="flex items-start gap-1.5 text-[11px] text-stone-500">
          {action.ok === false ? (
            <CircleSlash size={12} className="mt-[2px] shrink-0 text-amber-500" />
          ) : (
            <CheckCircle2 size={12} className="mt-[2px] shrink-0 text-primary" />
          )}
          <span>{action.summary || action.tool}</span>
        </li>
      ))}
    </ul>
  );
}

function FallbackPanel({
  lead,
  busy,
  onDirectRebuild,
}: {
  lead: string;
  busy: boolean;
  onDirectRebuild: () => void;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3">
      <p className="flex items-start gap-1.5 text-[13px] font-semibold text-amber-900">
        <AlertTriangle size={14} className="mt-[2px] shrink-0" />
        {lead}
      </p>
      <button
        type="button"
        onClick={onDirectRebuild}
        disabled={busy}
        className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-stone-900 px-3 text-[13px] font-bold text-white transition active:scale-[0.99] disabled:opacity-50"
      >
        {busy ? <Loader2 size={14} className="animate-spin" /> : <BadgePercent size={14} />}
        {DIRECT_MSG}
      </button>
      <div className="flex flex-wrap gap-2">
        <Link
          href="/agente"
          className="rounded-pill border border-amber-300 bg-white px-3 py-1 text-[12px] font-semibold text-amber-900"
        >
          Agente spesa
        </Link>
        <Link
          href="/offerte"
          className="rounded-pill border border-amber-300 bg-white px-3 py-1 text-[12px] font-semibold text-amber-900"
        >
          Offerte vicino a te
        </Link>
        <Link
          href="/spese"
          className="rounded-pill border border-amber-300 bg-white px-3 py-1 text-[12px] font-semibold text-amber-900"
        >
          Le mie spese
        </Link>
      </div>
    </div>
  );
}

// -- Box --------------------------------------------------------------------

export default function AssistantBox({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const { location, radiusKm, setLocation } = useAppStore();
  const [open, setOpen] = useState(defaultOpen);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [aiBlocked, setAiBlocked] = useState(false);
  const [email, setEmail] = useState("");
  const [emailDraft, setEmailDraft] = useState("");
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const feedRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const stored = readStoredEmail();
    if (stored) {
      setEmail(stored);
      setEmailDraft(stored);
    }
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const feed = feedRef.current;
    if (feed) feed.scrollTop = feed.scrollHeight;
  }, [turns, busy, open]);

  const pushUser = (text: string) =>
    setTurns((prev) => [...prev, { id: nextId(), role: "user", text }]);

  const pushAssistant = (turn: Omit<AssistantTurn, "id" | "role">) =>
    setTurns((prev) => [...prev, { id: nextId(), role: "assistant", ...turn }]);

  const send = async (
    message: string,
    opts?: { emailOverride?: string; latOverride?: number; lngOverride?: number }
  ) => {
    const text = message.trim();
    if (!text || busy) return;
    const history = toHistory(turns);
    pushUser(text);
    setInput("");
    setBusy(true);
    try {
      const at = location ?? DEFAULT_LOCATION;
      const mail = (opts?.emailOverride ?? email).trim();
      const res = await assistantChat({
        message: text,
        email: isValidEmail(mail) ? mail : undefined,
        lat: opts?.latOverride ?? at.lat,
        lng: opts?.lngOverride ?? at.lng,
        radius_km: radiusKm,
        history,
      });
      setAiBlocked(false);
      pushAssistant({
        kind: "reply",
        text: (res.reply || "").trim(),
        actions: Array.isArray(res.actions) ? res.actions : undefined,
        plan: res.plan || undefined,
        rebuild: res.rebuild || undefined,
        needs: res.needs || undefined,
        source: text,
      });
    } catch (err) {
      const info = apiError(err);
      if (info.status === 503 || isMissingEndpoint(info)) {
        // Chiave AI assente oppure backend ancora senza la rotta: stesso esito
        // per l'utente, stessa risposta utile.
        setAiBlocked(true);
        pushAssistant({ kind: "unavailable", text: "", source: text });
      } else if (info.status === 429) {
        pushAssistant({ kind: "ratelimit", text: "", source: text });
      } else {
        pushAssistant({
          kind: "error",
          text:
            info.timeout || info.network
              ? "Il servizio non ha risposto in tempo: succede quando il backend era in pausa e si sta riavviando. Riprova tra qualche secondo."
              : "Non riesco a contattare l'assistente in questo momento. Riprova tra poco.",
          source: text,
        });
      }
    } finally {
      setBusy(false);
    }
  };

  /** Percorso SENZA AI: ultima spesa salvata + /rebuild/from-items.
   *  E' il flusso principale oggi, perche' non richiede la chiave AI. */
  const runDirectRebuild = async (opts?: { emailOverride?: string }) => {
    if (busy) return;
    const mail = (opts?.emailOverride ?? email).trim();
    if (!isValidEmail(mail)) {
      pushUser(DIRECT_MSG);
      pushAssistant({
        kind: "reply",
        text: "Per ritrovare la tua ultima spesa mi serve l'email con cui l'hai salvata.",
        needs: { type: "email", message: "Inserisci l'email delle tue spese." },
        source: DIRECT_MSG,
      });
      return;
    }
    pushUser(DIRECT_MSG);
    setBusy(true);
    try {
      const last = await getLastPurchase(mail);
      const items = purchaseItemsToRebuildItems(last?.items);
      if (!items.length) {
        pushAssistant({
          kind: "reply",
          text: "Ho trovato una spesa salvata, ma senza prodotti leggibili: caricane una dalla pagina Spese e riprovo subito.",
          needs: { type: "no_purchases", message: "" },
          source: DIRECT_MSG,
        });
        return;
      }
      const at = location ?? DEFAULT_LOCATION;
      const data = await rebuildFromItems(items, at.lat, at.lng, radiusKm, {
        keep_brand: true,
      });
      const when = formatDay(last?.purchase_date);
      pushAssistant({
        kind: "reply",
        text: `Ho ripreso la tua ultima spesa${when ? ` del ${when}` : ""} (${items.length} prodotti) e ho cercato le offerte vicino a te, tenendo le tue marche dove possibile.`,
        actions: [
          { tool: "purchases.last", summary: "ho consultato la tua ultima spesa", ok: true },
          {
            tool: "rebuild.from_items",
            summary: `ho cercato le offerte entro ${radiusKm} km mantenendo le marche`,
            ok: true,
          },
        ],
        rebuild: data,
        source: DIRECT_MSG,
      });
    } catch (err) {
      const info = apiError(err);
      if (info.status === 404 && info.detail === "no_purchases") {
        pushAssistant({
          kind: "reply",
          text: "Non trovo spese salvate per questa email: appena ne carichi una posso rifarla con le offerte.",
          needs: { type: "no_purchases", message: "" },
          source: DIRECT_MSG,
        });
      } else if (isMissingEndpoint(info)) {
        pushAssistant({
          kind: "error",
          text: "Questa funzione non e' ancora attiva sul server (frontend e backend si aggiornano in momenti diversi). Nel frattempo puoi usare l'agente spesa o le offerte.",
          source: DIRECT_MSG,
        });
      } else {
        pushAssistant({
          kind: "error",
          text:
            info.timeout || info.network
              ? "Il servizio non ha risposto in tempo: il backend era in pausa e si sta riavviando. Riprova tra qualche secondo."
              : "Non riesco a ricostruire la spesa in questo momento. Riprova tra poco.",
          source: DIRECT_MSG,
        });
      }
    } finally {
      setBusy(false);
    }
  };

  const submitEmail = (value: string, source?: string) => {
    const mail = value.trim();
    if (!isValidEmail(mail)) return;
    setEmail(mail);
    setEmailDraft(mail);
    storeEmail(mail);
    if (source === DIRECT_MSG) {
      void runDirectRebuild({ emailOverride: mail });
      return;
    }
    if (source) void send(source, { emailOverride: mail });
  };

  const enableLocation = async (source?: string) => {
    setLocating(true);
    setLocationError(null);
    try {
      const pos = await requestBrowserLocation();
      setLocation(pos);
      if (source === DIRECT_MSG) {
        void runDirectRebuild();
      } else if (source) {
        void send(source, { latOverride: pos.lat, lngOverride: pos.lng });
      }
    } catch (err) {
      setLocationError(
        err instanceof Error
          ? err.message
          : "Posizione non disponibile: scegli una citta' dalla barra posizione."
      );
    } finally {
      setLocating(false);
    }
  };

  const renderNeeds = (needs: AssistantNeeds, source?: string) => {
    if (needs.type === "email") {
      return (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submitEmail(emailDraft, source);
          }}
          className="flex flex-col gap-2 rounded-xl border border-primary/25 bg-primary-50 p-3"
        >
          <label htmlFor="assistant-email" className="text-[12px] font-bold text-deep">
            {needs.message || "La tua email (e' la chiave del tuo storico spese)"}
          </label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Mail
                size={15}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-stone-400"
              />
              <input
                id="assistant-email"
                type="email"
                inputMode="email"
                autoComplete="email"
                value={emailDraft}
                onChange={(e) => setEmailDraft(e.target.value)}
                placeholder="nome@email.it"
                className="h-10 w-full rounded-xl border border-stone-200 pl-9 pr-3 text-[13px] outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
              />
            </div>
            <button
              type="submit"
              disabled={!isValidEmail(emailDraft) || busy}
              className="h-10 shrink-0 rounded-xl bg-primary px-3 text-[13px] font-bold text-white transition active:scale-[0.99] disabled:opacity-50"
            >
              Continua
            </button>
          </div>
          <p className="text-[11px] text-stone-500">
            Serve solo per ritrovare le tue spese: niente password.
          </p>
        </form>
      );
    }

    if (needs.type === "location") {
      return (
        <div className="flex flex-col gap-2 rounded-xl border border-accent/30 bg-accent-50 p-3">
          <p className="text-[12px] font-bold text-accent-600">
            {needs.message || "Mi serve la tua posizione per trovare le offerte vicine."}
          </p>
          <button
            type="button"
            onClick={() => void enableLocation(source)}
            disabled={locating || busy}
            className="inline-flex h-10 items-center justify-center gap-2 self-start rounded-xl bg-accent px-3 text-[13px] font-bold text-white transition active:scale-[0.99] disabled:opacity-50"
          >
            {locating ? <Loader2 size={14} className="animate-spin" /> : <MapPin size={14} />}
            {locating ? "Rilevo la posizione..." : "Attiva la posizione"}
          </button>
          {locationError && <p className="text-[11px] text-red-600">{locationError}</p>}
          <p className="text-[11px] text-stone-500">
            Senza GPS uso {DEFAULT_LOCATION.label} come riferimento: i prezzi potrebbero
            non essere quelli dei tuoi negozi.
          </p>
        </div>
      );
    }

    if (needs.type === "no_purchases") {
      return (
        <div className="flex flex-col gap-2 rounded-xl border border-stone-200 bg-surface p-3">
          <p className="text-[12px] font-bold text-deep">
            {needs.message || "Non ho ancora una tua spesa da cui partire."}
          </p>
          <p className="text-[12px] text-stone-600">
            Carica uno scontrino o salva una spesa a mano: da quel momento posso rifarla
            scegliendo le offerte.
          </p>
          <Link
            href="/spese"
            className="inline-flex h-10 items-center justify-center gap-2 self-start rounded-xl bg-primary px-3 text-[13px] font-bold text-white transition active:scale-[0.99]"
          >
            <Receipt size={14} /> Vai alle mie spese
          </Link>
        </div>
      );
    }

    if (!needs.message) return null;
    return (
      <p className="rounded-xl border border-stone-200 bg-surface p-3 text-[12px] text-stone-600">
        {needs.message}
      </p>
    );
  };

  const renderAssistantTurn = (turn: AssistantTurn) => {
    if (turn.kind === "unavailable") {
      return (
        <FallbackPanel
          lead="L'assistente AI non e' ancora attivo: posso comunque rifare la tua ultima spesa con le offerte, senza AI."
          busy={busy}
          onDirectRebuild={() => void runDirectRebuild()}
        />
      );
    }
    if (turn.kind === "ratelimit") {
      return (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-900">
          Troppe richieste di seguito: riprova tra un minuto. Nel frattempo puoi guardare
          le <Link href="/offerte" className="font-bold underline">offerte vicino a te</Link>.
        </div>
      );
    }
    if (turn.kind === "error") {
      return (
        <div className="flex flex-col gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-[13px] text-red-700">
          <p>{turn.text}</p>
          {turn.source && (
            <button
              type="button"
              onClick={() => void send(turn.source as string)}
              disabled={busy}
              className="self-start rounded-xl bg-red-600 px-3 py-1.5 text-[12px] font-bold text-white transition active:scale-[0.99] disabled:opacity-50"
            >
              Riprova
            </button>
          )}
        </div>
      );
    }

    const plan = safePlan(turn.plan);
    const rebuildItems = Array.isArray(turn.rebuild?.items) ? turn.rebuild?.items : null;

    return (
      <div className="flex flex-col gap-2">
        {turn.text ? (
          <MiniMarkdown text={turn.text} />
        ) : (
          <p className="text-[13px] text-stone-500">Fatto.</p>
        )}
        {turn.actions && turn.actions.length > 0 && <ActionList actions={turn.actions} />}
        {rebuildItems && rebuildItems.length > 0 && turn.rebuild && (
          <RebuildResult data={turn.rebuild} />
        )}
        {plan && <PurchasePlan result={plan} />}
        {turn.needs && renderNeeds(turn.needs, turn.source)}
      </div>
    );
  };

  if (!open) {
    return (
      <section className="rounded-card border border-primary/25 bg-white shadow-card">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-expanded={false}
          aria-controls="assistant-panel"
          className="flex w-full items-center gap-3 px-3 py-3 text-left sm:px-4"
        >
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary-50 text-primary">
            <Sparkles size={19} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-extrabold text-deep">
              Assistente SpesaSmart
            </span>
            <span className="block truncate text-[12px] text-stone-500">
              &quot;Rifai la mia ultima spesa scegliendo le offerte&quot;
            </span>
          </span>
          <span className="shrink-0 rounded-pill bg-primary px-3 py-1.5 text-[12px] font-bold text-white">
            Chiedi
          </span>
        </button>
      </section>
    );
  }

  return (
    <section
      id="assistant-panel"
      className="overflow-hidden rounded-card border border-primary/25 bg-white shadow-card"
    >
      <div className="flex items-center gap-3 border-b border-stone-100 bg-surface px-3 py-2.5 sm:px-4">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-primary-50 text-primary">
          <Bot size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-extrabold text-deep">Assistente SpesaSmart</p>
          <p className="truncate text-[11px] text-stone-500">
            Rifa&apos; la tua spesa con le offerte, tenendo le tue marche
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="Chiudi assistente"
          className="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-stone-500 transition hover:bg-stone-100"
        >
          <ChevronDown size={18} className="hidden sm:block" />
          <X size={18} className="sm:hidden" />
        </button>
      </div>

      <div
        ref={feedRef}
        aria-live="polite"
        aria-busy={busy}
        className="max-h-[62vh] overflow-y-auto px-3 py-3 sm:px-4"
      >
        {turns.length === 0 && (
          <div className="flex flex-col gap-3">
            <p className="text-[13px] text-stone-600">
              Dimmi cosa vuoi ottenere: controllo le tue spese salvate, cerco le offerte
              nei negozi vicini e ti propongo le sostituzioni mantenendo le marche che
              compri di solito.
            </p>
            <div className="flex flex-col gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => void send(suggestion)}
                  disabled={busy}
                  className="flex items-center gap-2 rounded-xl border border-stone-200 bg-surface px-3 py-2 text-left text-[13px] font-semibold text-deep transition hover:border-primary/40 active:scale-[0.995] disabled:opacity-50"
                >
                  <Sparkles size={14} className="shrink-0 text-primary" />
                  {suggestion}
                </button>
              ))}
            </div>
            {aiBlocked && (
              <FallbackPanel
                lead="L'assistente AI non e' ancora attivo: queste scorciatoie funzionano comunque."
                busy={busy}
                onDirectRebuild={() => void runDirectRebuild()}
              />
            )}
          </div>
        )}

        {turns.length > 0 && (
          <ol className="flex flex-col gap-3">
            {turns.map((turn) =>
              turn.role === "user" ? (
                <li key={turn.id} className="flex justify-end">
                  <p className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-3 py-2 text-[13px] font-semibold text-white">
                    {turn.text}
                  </p>
                </li>
              ) : (
                <li key={turn.id} className="flex flex-col gap-2">
                  {renderAssistantTurn(turn)}
                </li>
              )
            )}
            {busy && (
              <li className="flex items-center gap-2 text-[12px] text-stone-500">
                <Loader2 size={14} className="animate-spin text-primary" />
                Sto lavorando: controllo spese, prezzi e offerte...
              </li>
            )}
          </ol>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
        className="flex items-center gap-2 border-t border-stone-100 px-3 py-2.5 sm:px-4"
      >
        <label htmlFor="assistant-input" className="sr-only">
          Chiedi all&apos;assistente
        </label>
        <input
          id="assistant-input"
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Es. rifai la mia ultima spesa con le offerte"
          className="h-11 min-w-0 flex-1 rounded-xl border border-stone-200 px-3 text-[13px] outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
        />
        <button
          type="submit"
          disabled={busy || input.trim().length < 2}
          aria-label="Invia richiesta"
          className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-primary text-white transition active:scale-[0.97] disabled:opacity-40"
        >
          {busy ? <Loader2 size={17} className="animate-spin" /> : <Send size={17} />}
        </button>
      </form>
    </section>
  );
}
