#!/usr/bin/env node
/**
 * SpesaSmart — Agente della spesa (locale, autonomo).
 *
 * Fa da solo l'intero giro: prende la lista, chiede all'app il piano più
 * conveniente, apre il sito del supermercato e riempie il carrello. Si ferma
 * SEMPRE prima del pagamento.
 *
 * Uso:
 *   node shopper.mjs --login                       # apri il browser e accedi (una volta)
 *   node shopper.mjs latte, pasta, caffe           # fai la spesa
 *   node shopper.mjs --dry-run latte, pasta        # mostra il piano senza toccare il carrello
 *   node shopper.mjs --lat 45.36 --lng 9.69 pane   # posizione esplicita
 *
 * Sicurezza (per costruzione):
 *  • Le credenziali NON passano da qui: accedi tu nel browser, la sessione
 *    resta in un profilo locale dedicato (cartella .profile, mai committata).
 *  • L'agente non invia ordini e non paga: apre il carrello e si ferma.
 *  • Nessun aggiramento di CAPTCHA/anti-bot: se il sito blocca, l'agente si ferma.
 */
import { chromium } from "playwright-core";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as esselunga from "./adapters/esselunga.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PROFILE_DIR = path.join(HERE, ".profile");
const API = process.env.SPESASMART_API || "https://spesasmart-backend-4kyf.onrender.com/api/v1";
const DEFAULT_POS = { lat: 45.4642, lng: 9.19 }; // Milano

const ADAPTERS = { esselunga };

// ── CLI ─────────────────────────────────────────────────────────────────────
function parseArgs(argv) {
  const out = { items: [], dryRun: false, loginOnly: false, radius: 50, ...DEFAULT_POS };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--dry-run") out.dryRun = true;
    else if (a === "--login") out.loginOnly = true;
    else if (a === "--lat") out.lat = parseFloat(argv[++i]);
    else if (a === "--lng") out.lng = parseFloat(argv[++i]);
    else if (a === "--radius") out.radius = parseFloat(argv[++i]);
    else if (a === "--chain") out.chain = argv[++i];
    else rest.push(a);
  }
  out.items = rest
    .join(" ")
    .split(/[,;]/)
    .map((s) => s.trim())
    .filter((s) => s.length >= 2);
  return out;
}

const log = (...a) => console.log(...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── Piano dalla web app ─────────────────────────────────────────────────────
async function fetchPlan({ items, lat, lng, radius }) {
  const res = await fetch(`${API}/lists/optimize-quick`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      items: items.map((q) => ({ query: q, quantity: 1 })),
      lat,
      lng,
      radius_km: radius,
      strategy: "cheapest",
    }),
  });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}

/** Sceglie l'offerta della catena automatizzabile (oggi: Esselunga). */
function pickChainOffer(plan, wanted = "esselunga") {
  return (plan.single_ranking || []).find((s) => s.chain_slug === wanted) || null;
}

// ── Browser ─────────────────────────────────────────────────────────────────
async function openBrowser() {
  // Usa il Chrome/Edge installato: nessun download di browser.
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    channel: process.env.BROWSER_CHANNEL || "chrome",
    headless: false,
    viewport: null,
    args: ["--start-maximized"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  return { ctx, page };
}

/** Attende che l'utente completi il login (non tocchiamo le credenziali). */
async function waitForLogin(page, adapter, timeoutMs = 300000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    if (await adapter.isLoggedIn(page).catch(() => false)) return true;
    await sleep(2000);
  }
  return false;
}

// ── Flusso principale ───────────────────────────────────────────────────────
async function main() {
  const args = parseArgs(process.argv.slice(2));
  const adapter = ADAPTERS[args.chain || "esselunga"];
  if (!adapter) {
    log(`Catena non supportata. Disponibili: ${Object.keys(ADAPTERS).join(", ")}`);
    process.exit(1);
  }

  // Solo login: apri il browser, accedi, chiudi. La sessione resta nel profilo.
  if (args.loginOnly) {
    const { ctx, page } = await openBrowser();
    log(`\n🔐  Apro ${adapter.meta.name}. Accedi tu nel browser: la sessione resta`);
    log("    in questo profilo locale e le prossime volte non servirà più.\n");
    await page.goto(adapter.meta.homeUrl, { waitUntil: "domcontentloaded" });
    const ok = await waitForLogin(page, adapter);
    log(ok ? "✅  Accesso rilevato: sei pronto.\n" : "⏳  Nessun accesso rilevato (tempo scaduto).\n");
    await ctx.close();
    return;
  }

  if (!args.items.length) {
    log('Uso: node shopper.mjs "latte, pasta, caffe"   (oppure --login la prima volta)');
    process.exit(1);
  }

  log(`\n🧾  Lista: ${args.items.join(", ")}`);
  log("🧠  Chiedo all'app il piano più conveniente…");
  const plan = await fetchPlan(args);
  const offer = pickChainOffer(plan, adapter.meta.slug);

  if (!offer) {
    log(`\n⚠️  ${adapter.meta.name} non ha questi prodotti nella tua zona.`);
    log(`    Catene disponibili: ${(plan.single_ranking || []).map((s) => s.chain_slug).join(", ")}`);
    return;
  }

  const items = offer.items.filter((it) => it.product_url);
  log(`\n🛒  ${adapter.meta.name}: ${items.length} prodotti — totale € ${offer.total.toFixed(2)}`);
  for (const it of items) log(`    • ${it.product_name} — € ${it.price.toFixed(2)}`);
  if (plan.not_found?.length) log(`    (non trovati: ${plan.not_found.join(", ")})`);

  if (args.dryRun) {
    log("\n🔎  Dry-run: nessuna modifica al carrello.\n");
    return;
  }

  const { ctx, page } = await openBrowser();
  try {
    await page.goto(adapter.meta.homeUrl, { waitUntil: "domcontentloaded" });
    await sleep(2500);

    if (!(await adapter.isLoggedIn(page))) {
      log("\n🔐  Non risulti connesso: accedi nel browser che si è aperto.");
      log("    (le credenziali le inserisci tu — l'agente non le vede né le salva)");
      if (!(await waitForLogin(page, adapter))) {
        log("⏳  Accesso non completato: mi fermo senza toccare il carrello.\n");
        return;
      }
    }
    log("✅  Sessione attiva.\n");

    let added = 0;
    for (const [i, it] of items.entries()) {
      const label = it.product_name.slice(0, 46);
      process.stdout.write(`  [${i + 1}/${items.length}] ${label} … `);
      try {
        await page.goto(it.product_url, { waitUntil: "domcontentloaded" });
        await sleep(1200);
        const before = await adapter.cartCount(page);
        const res = await adapter.addToCart(page, it.quantity || 1);
        if (res !== "added") {
          log(res === "blocked" ? "non disponibile" : "non trovato sulla pagina");
          continue;
        }
        // Conferma sul CONTATORE (il messaggio a schermo sparisce troppo in fretta).
        let ok = false;
        for (let a = 0; a < 5 && !ok; a++) {
          await sleep(700);
          const after = await adapter.cartCount(page);
          ok = before == null ? after != null && after > 0 : after != null && after > before;
        }
        log(ok ? "aggiunto ✅" : "da verificare ⚠️");
        if (ok) added++;
      } catch (e) {
        log(`errore (${String(e).slice(0, 40)})`);
      }
    }

    log(`\n🎯  Aggiunti ${added}/${items.length} prodotti.`);
    await page.goto(adapter.meta.cartUrl, { waitUntil: "domcontentloaded" });
    log("🧺  Ho aperto il carrello: controlla, scegli lo slot e completa TU l'ordine.");
    log("    (l'agente non invia ordini e non paga)\n");
    log("    Premi INVIO per chiudere il browser.");
    await new Promise((r) => process.stdin.once("data", r));
  } finally {
    await ctx.close();
  }
}

main().catch((e) => {
  console.error("Errore:", e.message);
  process.exit(1);
});
