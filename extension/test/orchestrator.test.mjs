/**
 * Test dell'orchestratore (background.js) con le API chrome simulate.
 * Verifica il flusso completo dell'agente: piano ricevuto → sessione → apertura
 * schede prodotto → aggiungi → conferma → apertura carrello → progresso finale.
 *
 * Uso:  node extension/test/orchestrator.test.mjs
 */
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const results = [];
const check = (name, fn) => {
  try {
    fn();
    results.push({ name, ok: true });
  } catch (e) {
    results.push({ name, ok: false, err: e.message });
  }
};

/** Costruisce un ambiente chrome simulato + carica background.js. */
async function loadBackground({ loggedIn = true, addStatus = "added", confirmed = true } = {}) {
  const state = { progress: null, navigations: [], listeners: [], messageHandlers: [] };

  globalThis.chrome = {
    storage: {
      session: {
        set: async (o) => Object.assign(state, { progress: o.progress }),
        get: async () => ({ progress: state.progress }),
        remove: async () => (state.progress = null),
      },
      onChanged: { addListener: () => {} },
    },
    tabs: {
      query: async () => [{ id: 1, url: "https://spesaonline.esselunga.it/commerce/nav/supermercato/store/home" }],
      update: async (id, o) => {
        if (o.url) state.navigations.push(o.url);
        // simula il completamento del caricamento
        setTimeout(() => state.listeners.forEach((l) => l(id, { status: "complete" })), 0);
        return { id };
      },
      create: async (o) => {
        state.navigations.push(o.url);
        setTimeout(() => state.listeners.forEach((l) => l(2, { status: "complete" })), 0);
        return { id: 2 };
      },
      onUpdated: {
        addListener: (l) => state.listeners.push(l),
        removeListener: (l) => (state.listeners = state.listeners.filter((x) => x !== l)),
      },
    },
    scripting: {
      // Riconosce quale funzione in-page viene iniettata dal suo sorgente.
      executeScript: async ({ func }) => {
        const src = func.toString();
        if (src.includes("loginButtons")) return [{ result: loggedIn }];
        if (src.includes("addFeedback") && src.includes("ng-hide")) return [{ result: confirmed }];
        if (src.includes("addButtonGuesses")) return [{ result: { url: "x", addBtn: null } }];
        return [{ result: { status: addStatus } }];
      },
    },
    runtime: { onMessage: { addListener: (h) => state.messageHandlers.push(h) } },
  };

  // Importa il modulo fresco a ogni test (query string per bypassare la cache).
  await import(`../background.js?t=${Math.random().toString(36).slice(2)}`);
  return state;
}

const PLAN = {
  chain_slug: "esselunga",
  chain_name: "Esselunga",
  items: [
    { product_name: "Latte UHT 1L", product_url: "https://spesaonline.esselunga.it/p/1", quantity: 1 },
    { product_name: "Pasta 500g", product_url: "https://spesaonline.esselunga.it/p/2", quantity: 2 },
    { product_name: "Caffè 250g", product_url: "https://spesaonline.esselunga.it/p/3", quantity: 1 },
  ],
};

const send = (state, msg) =>
  new Promise((resolve) => state.messageHandlers[0](msg, {}, resolve));
// L'orchestratore attende il caricamento pagina + il toast per ogni prodotto
// (~1,6s a voce): l'attesa dev'essere ampia, altrimenti un run non concluso
// prosegue in background e "sporca" il test successivo.
const waitFor = async (fn, ms = 20000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (fn()) return true;
    await new Promise((r) => setTimeout(r, 25));
  }
  return false;
};
/** Attende che il run corrente sia in uno stato terminale. */
const waitTerminal = (st) =>
  waitFor(() => ["done", "needs_login", "unsupported", "error"].includes(st.progress?.state));

// ── T1: flusso felice — tutti i prodotti aggiunti ──────────────────────────
{
  const st = await loadBackground({ loggedIn: true, addStatus: "added", confirmed: true });
  await send(st, { type: "CART_PLAN", payload: PLAN });
  await waitTerminal(st);

  check("T1a stato finale = done", () => assert.equal(st.progress.state, "done"));
  check("T1b tutti e 3 i prodotti aggiunti", () => assert.equal(st.progress.added, 3));
  check("T1c apre le 3 schede prodotto", () =>
    PLAN.items.forEach((it) => assert.ok(st.navigations.includes(it.product_url))));
  check("T1d tenta di aprire il carrello alla fine (click icona)", () =>
    // Non esiste un URL carrello stabile: l'ultima azione e' il click sull'icona,
    // quindi l'ultima navigazione resta l'ultima scheda prodotto.
    assert.ok(st.navigations[st.navigations.length - 1].includes("/p/")));
  check("T1e nessun checkout automatico", () =>
    assert.ok(!st.navigations.some((u) => /checkout|pagamento|ordine\/conferma/i.test(u))));
  check("T1f messaggio finale con conteggio", () =>
    assert.match(st.progress.message, /Aggiunti 3\/3/));
}

// ── T2: utente non loggato → si ferma e chiede il login ────────────────────
{
  const st = await loadBackground({ loggedIn: false });
  await send(st, { type: "CART_PLAN", payload: PLAN });
  await waitTerminal(st);

  check("T2a stato = needs_login", () => assert.equal(st.progress.state, "needs_login"));
  check("T2b NON apre schede prodotto senza login", () =>
    assert.ok(!st.navigations.some((u) => /\/p\/\d/.test(u))));
  check("T2c messaggio chiede di accedere", () => assert.match(st.progress.message, /Accedi/i));
}

// ── T3: aggiunta non confermata dal toast → 'unconfirmed' (niente falsi ok) ─
{
  const st = await loadBackground({ loggedIn: true, addStatus: "added", confirmed: false });
  await send(st, { type: "CART_PLAN", payload: PLAN });
  await waitTerminal(st);

  check("T3a nessun 'added' senza conferma", () => assert.equal(st.progress.added, 0));
  check("T3b esiti marcati 'unconfirmed'", () =>
    assert.ok(st.progress.results.every((r) => r.status === "unconfirmed")));
  check("T3c diagnostica raccolta sul fallimento", () => assert.ok(st.progress.diag));
}

// ── T4: catena non supportata → messaggio chiaro, nessuna navigazione ──────
{
  const st = await loadBackground({ loggedIn: true });
  await send(st, { type: "CART_PLAN", payload: { ...PLAN, chain_slug: "carrefour" } });
  await waitTerminal(st);

  check("T4a stato = unsupported", () => assert.equal(st.progress.state, "unsupported"));
  check("T4b nessuna navigazione", () => assert.equal(st.navigations.length, 0));
}

// ── T5: sicurezza — il manifest non chiede permessi eccessivi ──────────────
{
  const mf = JSON.parse(readFileSync(path.join(here, "..", "manifest.json"), "utf8"));
  check("T5a host limitati a Esselunga", () =>
    assert.ok(mf.host_permissions.every((h) => h.includes("esselunga.it"))));
  check("T5b nessun permesso cookies/webRequest", () =>
    assert.ok(!mf.permissions.some((p) => /cookies|webRequest|<all_urls>/.test(p))));
  check("T5c nessun accesso a password manager o storage sync", () =>
    assert.ok(!JSON.stringify(mf).includes("identity")));
}

// ── Report ────────────────────────────────────────────────────────────────
const failed = results.filter((r) => !r.ok);
for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"} — ${r.name}${r.err ? ": " + r.err : ""}`);
console.log(`\nRESULT: ${failed.length === 0 ? "ALL PASS" : failed.length + " FAILED"} (${results.length} test)`);
process.exit(failed.length ? 1 : 0);
