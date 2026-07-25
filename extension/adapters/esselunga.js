/**
 * Adapter Esselunga (catena PILOTA).
 *
 * Automatizza "aggiungi al carrello" sul sito Esselunga usando la sessione
 * dell'utente GIÀ AUTENTICATO. Non gestisce credenziali: il login lo fa l'utente
 * sul sito ufficiale.
 *
 * ⚠️ I SELETTORI qui sotto sono un PUNTO DI PARTENZA e vanno VERIFICATI sul sito
 * reale loggato (variano e cambiano nel tempo). L'adapter è volutamente difensivo:
 * se non trova gli elementi ritorna "not_found"/"blocked" e il flusso ricade sul
 * deep-link manuale. Nessun tentativo di aggirare CAPTCHA/anti-bot.
 *
 * ⚠️ PRIMA DELL'USO IN PRODUZIONE: revisione legale dei ToS Esselunga
 * sull'automazione della sessione utente (vedi docs/CART_AUTOMATION_ARCHITECTURE.md).
 */

export const meta = {
  slug: "esselunga",
  name: "Esselunga",
  hosts: ["www.esselunga.it", "spesaonline.esselunga.it"],
  shopUrl: "https://spesaonline.esselunga.it/",
  cartUrl: "https://spesaonline.esselunga.it/commerce/nav/auth/spesa/carrello.html",
};

/**
 * Selettori da verificare/aggiornare sul sito reale. Tenuti in un unico posto
 * così l'aggiornamento è un edit di configurazione, non di logica.
 */
export const SELECTORS = {
  // Indicatore che l'utente è loggato (es. area utente / logout visibile).
  loggedIn: [
    "[href*='logout']",
    "[data-testid*='account']",
    ".header-user, .user-logged, .area-utente",
  ],
  // Pulsante "aggiungi al carrello" nella pagina/scheda prodotto.
  addToCart: [
    "button[aria-label*='arrello' i]",
    "button[title*='arrello' i]",
    "[data-testid*='add-to-cart']",
    "[data-action*='add-to-cart']",
    "button.add-to-cart, button.aggiungi",
  ],
  // Campo/stepper quantità (opzionale).
  quantityInput: ["input[name*='quant' i]", "input[aria-label*='quant' i]", ".quantity input"],
  quantityPlus: ["button[aria-label*='aument' i]", ".quantity .plus, .qty-plus"],
  // Contatore articoli nel carrello (per verifica).
  cartCount: ["[data-testid*='cart-count']", ".cart-count, .badge-carrello, .header-cart .count"],
};

// ── Funzioni "in-page": vengono INIETTATE nella pagina (chrome.scripting).
// Devono essere autonome (ricevono i selettori come argomento, niente closure).

/** true se in pagina risulta una sessione autenticata. */
export function pageIsLoggedIn(sel) {
  return sel.loggedIn.some((s) => document.querySelector(s));
}

/** Legge il numero di articoli nel carrello (0 se non trovato). */
export function pageCartCount(sel) {
  for (const s of sel.cartCount) {
    const el = document.querySelector(s);
    if (el) {
      const n = parseInt((el.textContent || "").replace(/\D+/g, ""), 10);
      if (!Number.isNaN(n)) return n;
    }
  }
  return 0;
}

/**
 * Aggiunge il prodotto della pagina corrente al carrello.
 * Ritorna { status: "added"|"not_found"|"blocked", before, after }.
 * NON procede oltre l'aggiunta: nessun checkout, nessun pagamento.
 */
export function pageAddToCart(sel, qty) {
  const q = document.querySelector.bind(document);
  const findFirst = (list) => list.map(q).find(Boolean) || null;

  const before = (() => {
    for (const s of sel.cartCount) {
      const el = q(s);
      if (el) {
        const n = parseInt((el.textContent || "").replace(/\D+/g, ""), 10);
        if (!Number.isNaN(n)) return n;
      }
    }
    return null;
  })();

  // Imposta la quantità se c'è un campo dedicato (best-effort).
  if (qty && qty > 1) {
    const qi = findFirst(sel.quantityInput);
    if (qi) {
      qi.value = String(qty);
      qi.dispatchEvent(new Event("input", { bubbles: true }));
      qi.dispatchEvent(new Event("change", { bubbles: true }));
    } else {
      const plus = findFirst(sel.quantityPlus);
      if (plus) for (let i = 1; i < qty; i++) plus.click();
    }
  }

  const btn = findFirst(sel.addToCart);
  if (!btn) return { status: "not_found", before, after: before };
  if (btn.disabled) return { status: "blocked", before, after: before };
  btn.click();
  return { status: "added", before, after: null };
}
