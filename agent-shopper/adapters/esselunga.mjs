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

/** true se la sessione del browser risulta autenticata. */
export async function isLoggedIn(page) {
  return page.evaluate((sel) => {
    let accedi = false;
    document.querySelectorAll(sel.loginButtons).forEach((b) => {
      const t = ((b.textContent || "") + " " + (b.getAttribute("aria-label") || "")).toLowerCase();
      if (t.includes("accedi")) accedi = true;
    });
    return !accedi && !!document.querySelector(sel.navbar);
  }, SEL);
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
