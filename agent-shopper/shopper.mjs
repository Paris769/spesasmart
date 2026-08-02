#!/usr/bin/env node
/**
 * SpesaSmart — Agente della spesa (locale, autonomo).
 *
 * Fa da solo l'intero giro: prende la lista, chiede all'app il piano più
 * conveniente, apre il sito del supermercato e riempie il carrello. Si ferma
 * SEMPRE prima del pagamento.
 *
 * Uso:
 *   node shopper.mjs --login                        # accedi una volta sola
 *   node shopper.mjs "latte, pasta, caffe"          # fai la spesa
 *   node shopper.mjs --dry-run "latte, pasta"       # mostra il piano, non tocca il carrello
 *   node shopper.mjs --lat 41.90 --lng 12.49 "pane" # posizione esplicita (entrambe)
 *
 * Sicurezza (per costruzione):
 *  • Le credenziali NON passano da qui: accedi tu nel browser, la sessione
 *    resta in un profilo locale FUORI da cartelle sincronizzate.
 *  • L'agente non invia ordini e non paga: apre il carrello e si ferma.
 *  • Nessun aggiramento di CAPTCHA/anti-bot: se il sito blocca, l'agente si ferma.
 */
import { chromium } from "playwright-core";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import * as esselunga from "./adapters/esselunga.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));

/**
 * Il profilo contiene i COOKIE DI SESSIONE del supermercato: non deve finire in
 * una cartella sincronizzata sul cloud (il progetto vive sotto OneDrive).
 * Lo teniamo nei dati applicativi locali dell'utente.
 */
const PROFILE_DIR =
  process.env.SPESASMART_PROFILE ||
  path.join(
    process.env.LOCALAPPDATA || path.join(os.homedir(), ".local", "share"),
    "SpesaSmart",
    "browser-profile"
  );
const LEGACY_PROFILE = path.join(HERE, ".profile");

const API = process.env.SPESASMART_API || "https://spesasmart-backend-4kyf.onrender.com/api/v1";
const DEFAULT_POS = { lat: 45.4642, lng: 9.19, label: "Milano" };

const ADAPTERS = { esselunga };
const FLAGS_CON_VALORE = new Set(["--lat", "--lng", "--radius", "--chain"]);

const log = (...a) => console.log(...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── CLI ─────────────────────────────────────────────────────────────────────
function parseArgs(argv) {
  const out = {
    items: [],
    dryRun: false,
    loginOnly: false,
    radius: 50,
    lat: DEFAULT_POS.lat,
    lng: DEFAULT_POS.lng,
    posLabel: `${DEFAULT_POS.label} (posizione predefinita)`,
    posExplicit: false,
    parziale: false,
  };
  const rest = [];
  const errori = [];
  let lat = null;
  let lng = null;

  const valore = (flag, v) => {
    // Un flag senza valore non deve "mangiarsi" il flag successivo.
    if (v === undefined || v.startsWith("--")) {
      errori.push(`${flag} richiede un valore (es. ${flag} 45.46)`);
      return null;
    }
    return v;
  };

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--dry-run") out.dryRun = true;
    else if (a === "--login") out.loginOnly = true;
    else if (a === "--anche-parziale") out.parziale = true;
    else if (FLAGS_CON_VALORE.has(a)) {
      const v = valore(a, argv[i + 1]);
      if (v === null) continue;
      i++;
      if (a === "--lat") lat = parseFloat(v);
      else if (a === "--lng") lng = parseFloat(v);
      else if (a === "--radius") out.radius = parseFloat(v);
      else out.chain = v;
    } else if (a.startsWith("-")) {
      // Un'opzione sconosciuta non deve diventare un articolo della spesa.
      errori.push(`opzione sconosciuta: ${a}`);
    } else rest.push(a);
  }

  if ((lat === null) !== (lng === null)) {
    errori.push("--lat e --lng vanno indicati insieme (o nessuno dei due)");
  } else if (lat !== null && lng !== null) {
    if (Number.isNaN(lat) || Number.isNaN(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) {
      errori.push("coordinate non valide (lat tra -90 e 90, lng tra -180 e 180)");
    } else {
      out.lat = lat;
      out.lng = lng;
      out.posExplicit = true;
      out.posLabel = `${lat}, ${lng}`;
    }
  }
  if (Number.isNaN(out.radius) || out.radius <= 0) errori.push("--radius deve essere un numero positivo");

  out.items = rest
    .join(" ")
    .split(/[,;]/)
    .map((s) => s.trim())
    .filter((s) => s.length >= 2);
  out.errori = errori;
  return out;
}

// ── Piano dalla web app ─────────────────────────────────────────────────────
async function fetchPlan({ items, lat, lng, radius }) {
  let ultimo;
  for (let tentativo = 0; tentativo < 2; tentativo++) {
    try {
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
        signal: AbortSignal.timeout(45000),
      });
      if (res.ok) return await res.json();
      if ([502, 503, 504].includes(res.status) && tentativo === 0) {
        log("    (il server si sta svegliando: riprovo tra poco…)");
        await sleep(8000);
        ultimo = new Error(`server non pronto (${res.status})`);
        continue;
      }
      throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      ultimo = e;
      const rete = e.name === "TimeoutError" || e.name === "AbortError" || e.cause;
      if (rete && tentativo === 0) {
        log("    (nessuna risposta: riprovo una volta…)");
        await sleep(4000);
        continue;
      }
      break;
    }
  }
  // Messaggi comprensibili al posto degli errori tecnici.
  const t = String(ultimo?.message || ultimo);
  if (/502|503|504|non pronto/.test(t))
    throw new Error(
      "Il server di SpesaSmart si stava riavviando (succede dopo un periodo di inattività).\n" +
        "   Riprova fra un minuto: è normale al primo comando della giornata."
    );
  if (/Timeout|Abort|fetch failed|ENOTFOUND|ECONNREFUSED/i.test(t + String(ultimo?.cause || "")))
    throw new Error("Non riesco a contattare SpesaSmart. Controlla la connessione e riprova.");
  if (/HTTP 4\d\d/.test(t))
    throw new Error("L'app non ha capito la richiesta: controlla come hai scritto gli articoli (separali con la virgola).");
  throw new Error(`Non riesco a ottenere il piano: ${t}`);
}

/** Sceglie l'offerta della catena automatizzabile (oggi: Esselunga). */
function pickChainOffer(plan, wanted = "esselunga") {
  return (plan.single_ranking || []).find((s) => s.chain_slug === wanted) || null;
}

// ── Browser ─────────────────────────────────────────────────────────────────
async function openBrowser() {
  fs.mkdirSync(PROFILE_DIR, { recursive: true });
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    channel: process.env.BROWSER_CHANNEL || "chrome",
    headless: false,
    viewport: null,
    args: ["--start-maximized"],
  });
  // Il sito del supermercato è pesante (SPA + molte risorse): con i 30s di
  // default la prima apertura su profilo pulito andava in timeout.
  ctx.setDefaultNavigationTimeout(90000);
  ctx.setDefaultTimeout(30000);
  const page = ctx.pages()[0] || (await ctx.newPage());
  return { ctx, page };
}

/** Navigazione robusta: attende il commit e lascia renderizzare la SPA. */
async function goto(page, url, { tries = 3 } = {}) {
  let lastErr;
  for (let i = 0; i < tries; i++) {
    try {
      await page.goto(url, { waitUntil: "commit", timeout: 90000 });
      await sleep(500);
      return true;
    } catch (e) {
      lastErr = e;
      await sleep(1500);
    }
  }
  throw new Error(`navigazione fallita (${tries} tentativi): ${String(lastErr).slice(0, 90)}`);
}

/** Attende il login dell'utente. Solo conferma POSITIVA della sessione. */
async function waitForLogin(page, adapter, timeoutMs = 300000) {
  const t0 = Date.now();
  let ultimoAvviso = 0;
  while (Date.now() - t0 < timeoutMs) {
    const st = await adapter.loginState(page).catch(() => "unknown");
    if (st === "logged") return true;
    const passati = Math.round((Date.now() - t0) / 1000);
    if (passati - ultimoAvviso >= 30) {
      ultimoAvviso = passati;
      log(`    …aspetto il tuo accesso (${passati}s di 300)`);
    }
    await sleep(2000);
  }
  return false;
}

// ── Flusso principale ───────────────────────────────────────────────────────
async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.errori.length) {
    for (const e of args.errori) log(`⚠️  ${e}`);
    log('\nUso: node shopper.mjs "latte, pasta, caffe"   (--login la prima volta)');
    process.exitCode = 1;
    return;
  }

  const adapter = ADAPTERS[args.chain || "esselunga"];
  if (!adapter) {
    log(`Catena non supportata. Disponibili: ${Object.keys(ADAPTERS).join(", ")}`);
    process.exitCode = 1;
    return;
  }

  // Il vecchio profilo stava dentro la cartella sincronizzata: avvisa una volta.
  if (fs.existsSync(LEGACY_PROFILE)) {
    log(`ℹ️  Trovato un vecchio profilo in ${LEGACY_PROFILE} (dentro OneDrive).`);
    log(`   Ora uso ${PROFILE_DIR}. Puoi eliminare la vecchia cartella .profile.\n`);
  }

  if (args.loginOnly) {
    const { ctx, page } = await openBrowser();
    try {
      log(`\n🔐  Sto aprendo ${adapter.meta.name} in una finestra dedicata.`);
      log("    👉 CERCA LA FINESTRA CHROME CHE SI È APPENA APERTA e accedi lì.");
      log("    La sessione resta su questo computer: le prossime volte non servirà più.\n");
      await goto(page, adapter.meta.homeUrl);
      await page.bringToFront().catch(() => {});
      const ok = await waitForLogin(page, adapter);
      if (ok) log("✅  Accesso rilevato: sei pronto.\n");
      else {
        log("⏳  Nessun accesso rilevato (5 minuti scaduti). Rilancia: npm run login\n");
        process.exitCode = 1;
      }
    } finally {
      await ctx.close();
    }
    return;
  }

  if (!args.items.length) {
    log('Uso: node shopper.mjs "latte, pasta, caffe"   (--login la prima volta)');
    process.exitCode = 1;
    return;
  }

  log(`\n🧾  Lista: ${args.items.join(", ")}`);
  log(`📍  Zona: ${args.posLabel} — raggio ${args.radius} km`);
  if (!args.posExplicit) log("    Non è la tua città? Aggiungi:  --lat 41.90 --lng 12.49");
  log("\n🧠  Chiedo all'app il piano più conveniente…");

  let plan;
  try {
    plan = await fetchPlan(args);
  } catch (e) {
    log(`\n⚠️  ${e.message}\n`);
    process.exitCode = 1;
    return;
  }

  const offer = pickChainOffer(plan, adapter.meta.slug);
  if (!offer) {
    log(`\n⚠️  Su ${adapter.meta.name} non ho trovato questi prodotti entro ${args.radius} km da ${args.posLabel}.`);
    const altre = (plan.single_ranking || []).map((s) => s.chain_slug).filter((c) => c !== adapter.meta.slug);
    if (altre.length) log(`    Li ho trovati però da: ${[...new Set(altre)].join(", ")}`);
    process.exitCode = 1;
    return;
  }

  // Copertura reale: quali voci della TUA lista finiranno davvero nel carrello.
  const items = offer.items.filter((it) => it.product_url);
  const coperte = new Set(offer.items.map((i) => (i.query || "").trim().toLowerCase()));
  const introvabili = new Set((plan.not_found || []).map((q) => q.trim().toLowerCase()));
  const mancanti = args.items.filter((q) => !coperte.has(q.trim().toLowerCase()));
  const altrove = mancanti.filter((q) => !introvabili.has(q.trim().toLowerCase()));
  const totale = items.reduce((s, i) => s + i.price * (i.quantity || 1), 0);

  log(`\n🛒  ${adapter.meta.name}: ${items.length} prodotti su ${args.items.length} della tua lista — € ${totale.toFixed(2)}`);
  for (const it of items) log(`    • ${it.product_name} — € ${it.price.toFixed(2)}`);

  if (mancanti.length) {
    log(`\n⚠️  ATTENZIONE: ${mancanti.length} voci NON finiranno in questo carrello:`);
    if (altrove.length) log(`    • disponibili in altre catene: ${altrove.join(", ")}`);
    const nessuno = mancanti.filter((q) => introvabili.has(q.trim().toLowerCase()));
    if (nessuno.length) log(`    • non trovate da nessuna parte: ${nessuno.join(", ")}`);
  }

  if (args.dryRun) {
    log("\n🔎  Dry-run: nessuna modifica al carrello.\n");
    return;
  }

  // Copertura bassa: meglio chiedere conferma che riempire mezzo carrello.
  if (items.length / args.items.length < 0.5 && !args.parziale) {
    log(`\n🛑  ${adapter.meta.name} copre meno della metà della lista.`);
    log("    Se va bene lo stesso, rilancia aggiungendo:  --anche-parziale\n");
    return;
  }

  const { ctx, page } = await openBrowser();
  let added = 0;
  let sessionePersa = false;
  try {
    await goto(page, adapter.meta.homeUrl);
    await page.bringToFront().catch(() => {});

    if (!(await adapter.isLoggedIn(page))) {
      log("\n🔐  Non risulti connesso.");
      log("    👉 CERCA LA FINESTRA CHROME CHE SI È APPENA APERTA e accedi lì (hai 5 minuti).");
      log("    (le credenziali le inserisci tu — l'agente non le vede né le salva)");
      if (!(await waitForLogin(page, adapter))) {
        log("⏳  Accesso non completato: mi fermo senza toccare il carrello.\n");
        process.exitCode = 1;
        return;
      }
    }
    log("✅  Sessione attiva.\n");

    for (const [i, it] of items.entries()) {
      const label = it.product_name.slice(0, 46);
      process.stdout.write(`  [${i + 1}/${items.length}] ${label} … `);
      try {
        // Ritenta l'intera sequenza: una pagina non renderizzata non è un
        // "prodotto inesistente", è solo lenta.
        let res = "not_found";
        let before = null;
        let pronta = false;
        for (let tentativo = 0; tentativo < 2 && res === "not_found"; tentativo++) {
          await goto(page, it.product_url, { tries: 2 });
          pronta = await adapter.waitReady(page);
          if (!pronta) continue;
          before = await adapter.cartCount(page);
          res = await adapter.addToCart(page, it.quantity || 1);
        }

        if (res !== "added") {
          log(
            res === "blocked"
              ? "non disponibile"
              : !pronta
              ? "pagina troppo lenta, saltato"
              : "bottone non trovato (il sito potrebbe essere cambiato)"
          );
          continue;
        }

        let ok = false;
        let after = before;
        for (let a = 0; a < 5 && !ok; a++) {
          await sleep(700);
          after = await adapter.cartCount(page);
          ok = before == null ? after != null && after > 0 : after != null && after > before;
        }
        if (ok) {
          added++;
          log("aggiunto ✅");
        } else {
          // Nessun avanzamento del contatore: la sessione potrebbe essere caduta.
          const st = await adapter.loginState(page).catch(() => "unknown");
          if (st === "guest") {
            sessionePersa = true;
            log("sessione scaduta ⚠️");
            break;
          }
          log("da verificare ⚠️");
        }
      } catch (e) {
        log(`errore (${String(e.message || e).slice(0, 45)})`);
      }
    }

    if (sessionePersa) {
      log("\n🔐  La sessione è scaduta durante la spesa: accedi di nuovo e rilancia il comando.");
      process.exitCode = 1;
    }

    log(`\n🎯  Aggiunti ${added} prodotti su ${items.length} tentati.`);
    if (mancanti.length) log(`    Restano fuori ${mancanti.length} voci della tua lista: ${mancanti.join(", ")}`);

    if (added > 0 && !sessionePersa) {
      await goto(page, adapter.meta.cartUrl, { tries: 2 }).catch(() => {});
      log("🧺  Ho aperto il carrello: controlla, scegli lo slot e completa TU l'ordine.");
      log("    (l'agente non invia ordini e non paga)");
      log("    Il carrello è legato al tuo account: lo ritrovi anche nel browser di sempre.\n");
    } else if (!sessionePersa) {
      log("    Nessun prodotto aggiunto: il carrello non è stato modificato.\n");
      process.exitCode = 1;
    }

    if (process.stdin.isTTY) {
      log("    Premi INVIO per chiudere il browser.");
      await new Promise((r) => process.stdin.once("data", r));
    } else {
      await sleep(8000);
    }
  } finally {
    await ctx.close();
  }
}

main().catch((e) => {
  console.error("Errore:", e.message, e.cause ? `(${e.cause})` : "");
  process.exit(1);
});
