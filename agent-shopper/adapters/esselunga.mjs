/**
 * Adapter Esselunga per l'agente locale.
 *
 * Selettori ricavati dal DOM reale del sito (sessione autenticata inclusa) e
 * verificati sul campo: l'aggiunta al carrello e la conferma via contatore sono
 * state provate con successo su spesaonline.esselunga.it.
 *
 * L'agente NON gestisce credenziali: il login lo fa l'utente una volta nel
 * profilo browser dedicato, che resta sul suo computer.
 */

export const meta = {
  slug: "esselunga",
  name: "Esselunga",
  homeUrl: "https://spesaonline.esselunga.it/commerce/nav/supermercato/store/home",
  cartUrl: "https://spesaonline.esselunga.it/commerce/nav/supermercato/checkout/trolley",
};

export const SEL = {
  // "Accedi" presente in navbar = sessione NON autenticata.
  loginButtons: ".esselunga-navbar-right-item-list_v2__item__button",
  navbar: "[class*='esselunga-navbar']",
  // Bottone aggiungi: aria-label "Aggiungi al carrello <prodotto>".
  addToCart: [
    'button[aria-label^="Aggiungi al carrello"]',
    'button[aria-label*="Aggiungi al carrello"]',
    ".esselunga-product-detail-item-right-action-add-to-cart button",
    "button.el-product-card-b__add-to-cart-btn",
  ],
  // Quantità: <select> AngularJS con option value="number:N".
  quantitySelect: [
    "select.esselunga-product-quantity-select",
    'select[aria-label="Quantità"]',
    "select[id^='slQta']",
  ],
  // Contatore articoli: prova PERSISTENTE dell'aggiunta (il toast sparisce).
  cartCount: [
    ".esselunga-navbar-right-item-list_v2__item__button__item-quantity",
    'a[aria-label="Carrello"] span',
  ],
};

/**
 * Stato della sessione: "logged" | "guest" | "unknown".
 *
 * Serve un segnale POSITIVO di login (il saluto "Ciao, <nome>" o una voce
 * dell'area utente): la sola assenza del bottone "Accedi" non basta, perché
 * finché la SPA non ha renderizzato la navbar nessun bottone è presente e si
 * finirebbe per credersi connessi quando non lo si è (falso positivo osservato).
 */
export async function loginState(page) {
  return page.evaluate((sel) => {
    const navbar = document.querySelector(sel.navbar);
    const testo = navbar ? navbar.textContent || "" : "";
    // Segnale positivo: saluto o voci dell'area utente.
    if (/ciao[,\s]/i.test(testo)) return "logged";
    if (document.querySelector("[href*='logout'], [href*='account/dashboard']")) return "logged";
    // Segnale negativo: bottone "Accedi" renderizzato.
    let accedi = false;
    document.querySelectorAll(sel.loginButtons).forEach((b) => {
      const t = ((b.textContent || "") + " " + (b.getAttribute("aria-label") || "")).toLowerCase();
      if (t.includes("accedi")) accedi = true;
    });
    if (accedi) return "guest";
    return "unknown"; // pagina non ancora renderizzata: non decidere
  }, SEL);
}

/** true solo con conferma positiva della sessione. */
export async function isLoggedIn(page) {
  return (await loginState(page)) === "logged";
}

/**
 * Attende che la scheda prodotto sia realmente utilizzabile.
 * Serve perché addToCart gira dentro page.evaluate, che NON ha l'attesa
 * automatica dei locator Playwright: senza questo gate, su rete lenta il
 * bottone non è ancora nel DOM e il prodotto verrebbe saltato come
 * "non trovato" pur essendo perfettamente disponibile.
 */
export async function waitReady(page, timeout = 20000) {
  return page
    .waitForSelector(SEL.addToCart.join(","), { state: "attached", timeout })
    .then(() => true)
    .catch(() => false);
}

/** Numero articoli nel carrello (null se il contatore non è presente). */
export async function cartCount(page) {
  return page.evaluate((sel) => {
    for (const s of sel.cartCount) {
      const el = document.querySelector(s);
      if (el) {
        const n = parseInt((el.textContent || "").replace(/\D+/g, ""), 10);
        if (!Number.isNaN(n)) return n;
      }
    }
    return null;
  }, SEL);
}

/**
 * Aggiunge al carrello il prodotto della pagina corrente.
 * Ritorna "added" | "blocked" (esaurito/disabilitato) | "not_found".
 * Nessun checkout, nessun pagamento.
 */
export async function addToCart(page, qty = 1) {
  return page.evaluate(
    ({ sel, qty }) => {
      const findFirst = (list) => {
        for (const s of list) {
          const el = document.querySelector(s);
          if (el) return el;
        }
        return null;
      };
      // Quantità (option value="number:N")
      if (qty > 1) {
        const q = findFirst(sel.quantitySelect);
        if (q && q.tagName === "SELECT") {
          const want = String(qty);
          for (const o of q.options) {
            const label = (o.textContent || "").trim();
            if (label === want || o.value === want || o.value.endsWith(":" + want)) {
              q.value = o.value;
              q.dispatchEvent(new Event("change", { bubbles: true }));
              q.dispatchEvent(new Event("input", { bubbles: true }));
              break;
            }
          }
        }
      }
      const btn = findFirst(sel.addToCart);
      if (!btn) return "not_found";
      if (btn.disabled || btn.getAttribute("aria-disabled") === "true") return "blocked";
      btn.click();
      return "added";
    },
    { sel: SEL, qty }
  );
}
