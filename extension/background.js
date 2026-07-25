/**
 * Service worker: orchestra il riempimento del carrello.
 *
 * Riceve il piano dalla web app (via bridge.content.js), apre il sito della
 * catena nella sessione dell'utente, e per ogni prodotto apre la scheda e
 * clicca "aggiungi al carrello". Aggiorna il progresso in storage.session (il
 * popup lo mostra). NON fa login (lo fa l'utente) e NON fa checkout/pagamento.
 */
import * as esselunga from "./adapters/esselunga.js";

const ADAPTERS = { esselunga };

const setProgress = (p) => chrome.storage.session.set({ progress: p });
const getProgress = async () => (await chrome.storage.session.get("progress")).progress || null;

function awaitTabComplete(tabId, timeoutMs = 20000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    };
    const listener = (id, info) => {
      if (id === tabId && info.status === "complete") finish();
    };
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(finish, timeoutMs);
  });
}

async function findOrCreateTab(hosts, shopUrl) {
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find((t) => t.url && hosts.some((h) => t.url.includes(h)));
  if (existing) {
    await chrome.tabs.update(existing.id, { active: true });
    return existing.id;
  }
  const created = await chrome.tabs.create({ url: shopUrl, active: true });
  await awaitTabComplete(created.id);
  return created.id;
}

async function inPage(tabId, func, args) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return res?.result;
}

async function runPlan(payload) {
  const adapter = ADAPTERS[payload.chain_slug];
  const items = (payload.items || []).filter((it) => it.product_url);

  if (!adapter) {
    await setProgress({
      state: "unsupported",
      chain: payload.chain_name || payload.chain_slug,
      message: "Catena non ancora supportata dall'estensione (pilota: Esselunga).",
    });
    return;
  }
  if (!items.length) {
    await setProgress({ state: "error", message: "Nessun prodotto con link diretto nel piano." });
    return;
  }

  const tabId = await findOrCreateTab(adapter.meta.hosts, adapter.meta.shopUrl);

  // 1) Login: lo fa l'UTENTE. Se non risulta loggato, ci fermiamo e glielo diciamo.
  await awaitTabComplete(tabId, 8000);
  const logged = await inPage(tabId, esselunga.pageIsLoggedIn, [esselunga.SELECTORS]).catch(() => false);
  if (!logged) {
    await chrome.tabs.update(tabId, { url: adapter.meta.shopUrl, active: true });
    await setProgress({
      state: "needs_login",
      chain: adapter.meta.name,
      total: items.length,
      done: 0,
      message: `Accedi al tuo account ${adapter.meta.name} nella scheda aperta, poi premi di nuovo "Riempi il carrello".`,
    });
    return;
  }

  // 2) Per ogni prodotto: apri la scheda e aggiungi al carrello.
  const results = [];
  let diag = null; // snapshot del primo fallimento, per aggiustare i selettori
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    await setProgress({
      state: "running",
      chain: adapter.meta.name,
      total: items.length,
      done: i,
      current: it.product_name,
      results,
    });
    try {
      await chrome.tabs.update(tabId, { url: it.product_url, active: true });
      await awaitTabComplete(tabId);
      // La SPA AngularJS può popolare il DOM dopo il "complete": piccola attesa.
      await new Promise((r) => setTimeout(r, 700));
      const r = await inPage(tabId, esselunga.pageAddToCart, [esselunga.SELECTORS, it.quantity || 1]);
      let status = r?.status || "not_found";
      if (status === "added") {
        // Conferma tramite toast (#actionFeedback perde ng-hide quando aggiunge).
        await new Promise((res) => setTimeout(res, 900));
        const ok = await inPage(tabId, esselunga.pageAddConfirmed, [esselunga.SELECTORS]).catch(() => false);
        status = ok ? "added" : "unconfirmed";
      }
      results.push({ name: it.product_name, status });
      // Al primo prodotto non aggiunto, fotografa la pagina per la diagnosi.
      if (status !== "added" && !diag) {
        diag = await inPage(tabId, esselunga.pageDiagnostics, [esselunga.SELECTORS]).catch(() => null);
      }
    } catch (e) {
      results.push({ name: it.product_name, status: "error" });
    }
  }

  // 3) Fine: apri il carrello per la revisione UMANA. Nessun checkout automatico.
  await chrome.tabs.update(tabId, { url: adapter.meta.cartUrl, active: true });
  const added = results.filter((r) => r.status === "added").length;
  await setProgress({
    state: "done",
    chain: adapter.meta.name,
    total: items.length,
    done: items.length,
    added,
    results,
    diag,
    message: `Aggiunti ${added}/${items.length}. Controlla il carrello, scegli lo slot e completa TU l'ordine.`,
  });
}

// Messaggi dal bridge (web app) e dal popup.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "CART_PLAN" && msg.payload) {
    runPlan(msg.payload);
    sendResponse({ ok: true });
    return true;
  }
  if (msg?.type === "GET_PROGRESS") {
    getProgress().then((p) => sendResponse({ progress: p }));
    return true;
  }
  if (msg?.type === "RESET") {
    chrome.storage.session.remove("progress").then(() => sendResponse({ ok: true }));
    return true;
  }
});
