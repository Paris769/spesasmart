/**
 * Adapter Esselunga (catena PILOTA).
 *
 * Automatizza "aggiungi al carrello" su spesaonline.esselunga.it usando la
 * sessione dell'utente GIÀ AUTENTICATO. Non gestisce credenziali: il login lo fa
 * l'utente sul sito ufficiale.
 *
 * I selettori sono stati ricavati dal DOM REALE della pagina prodotto Esselunga
 * (AngularJS) il 2026-07-24. Restano da RI-verificare periodicamente (il sito
 * cambia) e soprattutto nella pagina da UTENTE LOGGATO, che può differire.
 * L'adapter è difensivo: se non trova gli elementi ritorna "not_found"/"blocked"
 * e il flusso ricade sul deep-link manuale. Nessun aggiramento di CAPTCHA/anti-bot.
 *
 * ⚠️ PRIMA DELL'USO IN PRODUZIONE: revisione legale dei ToS Esselunga
 * sull'automazione della sessione utente (vedi docs/CART_AUTOMATION_ARCHITECTURE.md).
 */

export const meta = {
  slug: "esselunga",
  name: "Esselunga",
  hosts: ["www.esselunga.it", "spesaonline.esselunga.it"],
  shopUrl: "https://spesaonline.esselunga.it/commerce/nav/supermercato/store/home",
  // URL reale del carrello, letto dal link in navbar di una sessione
  // autenticata (quello ipotizzato in origine dava "Risorsa non esistente").
  cartUrl: "https://spesaonline.esselunga.it/commerce/nav/supermercato/checkout/trolley",
};

/** Selettori ricavati dal DOM reale (verificare nella pagina da loggato). */
export const SELECTORS = {
  // Bottone "Accedi" nella navbar: se PRESENTE l'utente NON è loggato.
  loginButtons: ".esselunga-navbar-right-item-list_v2__item__button",
  navbar: ".esselunga-navbar-right-item-list_v2__item, [class*='esselunga-navbar']",
  // Pulsante aggiungi: aria-label "Aggiungi al carrello <nome prodotto>".
  addToCart: [
    'button[aria-label^="Aggiungi al carrello"]',
    'button[aria-label*="Aggiungi al carrello"]',
    ".esselunga-product-detail-item-right-action-add-to-cart button",
    // Card prodotto (home/listing): usato se il link porta a un elenco invece
    // che alla scheda dettaglio.
    "button.el-product-card-b__add-to-cart-btn",
  ],
  // Icona/link carrello in navbar (visibile da loggati).
  cartIcon: [
    'a[aria-label="Carrello"]',
    ".esselunga-navbar-right-item-list_v2__item__button i.icon-cart-empty",
    "[class*='esselunga-navbar'] [class*='icon-cart']",
  ],
  // Contatore articoli nel carrello: è la PROVA PERSISTENTE dell'aggiunta.
  // Il toast di conferma sparisce dopo pochi istanti, il contatore no.
  cartCount: [
    ".esselunga-navbar-right-item-list_v2__item__button__item-quantity",
    'a[aria-label="Carrello"] span',
  ],
  // Quantità: è un <select> (opzioni 1..N), non un input.
  quantitySelect: [
    "select.esselunga-product-quantity-select",
    'select[aria-label="Quantità"]',
    "select[id^='slQta']",
  ],
  // Toast di conferma: quando perde la classe ng-hide, l'aggiunta è avvenuta.
  addFeedback: "#actionFeedback.carrello-aggiunta, #actionFeedback",
};

// ── Funzioni "in-page": INIETTATE nella pagina (chrome.scripting). Autonome,
// ricevono i selettori come argomento (niente closure sul modulo).

const _firstMatch = (list) => {
  for (const s of list) {
    const el = document.querySelector(s);
    if (el) return el;
  }
  return null;
};

/** true se risulta una sessione autenticata (nessun bottone "Accedi"). */
export function pageIsLoggedIn(sel) {
  const btns = document.querySelectorAll(sel.loginButtons);
  for (const b of btns) {
    const t = ((b.textContent || "") + " " + (b.getAttribute("aria-label") || ""))
      .trim()
      .toLowerCase();
    if (t.includes("accedi")) return false; // "Accedi" visibile → non loggato
  }
  // Navbar presente e nessun "Accedi": assumiamo loggato.
  return !!document.querySelector(sel.navbar);
}

/**
 * Diagnostica: fotografa cosa c'è (o manca) nella pagina corrente. Serve quando
 * un'aggiunta fallisce sulla pagina da LOGGATO (che può differire da quella
 * pubblica): l'utente incolla questo e i selettori si correggono in un colpo.
 */
export function pageDiagnostics(sel) {
  const probe = (list) => {
    for (const s of list) {
      const el = document.querySelector(s);
      if (el) return { matched: s, tag: el.tagName, aria: el.getAttribute("aria-label") || null };
    }
    return null;
  };
  // Candidati "aggiungi" anche fuori dai selettori noti (per capire come sono
  // fatti sulla pagina loggata): bottoni con testo/aria "carrello"/"aggiungi".
  const guesses = [];
  document.querySelectorAll("button,[role=button]").forEach((el) => {
    const s = ((el.textContent || "") + " " + (el.getAttribute("aria-label") || "")).toLowerCase();
    if (/carrello|aggiungi/.test(s) && guesses.length < 6) {
      guesses.push({
        tag: el.tagName,
        aria: (el.getAttribute("aria-label") || "").slice(0, 60),
        cls: (el.className || "").toString().slice(0, 60),
      });
    }
  });
  return {
    url: location.href,
    title: document.title.slice(0, 80),
    addBtn: probe(sel.addToCart),
    qtySelect: probe(sel.quantitySelect),
    feedbackPresent: !!document.querySelector("#actionFeedback"),
    addButtonGuesses: guesses,
  };
}

/**
 * Apre il carrello cliccando l'icona in navbar (nessun URL fisso: quello
 * ipotizzato non esiste). Ritorna true se ha trovato e cliccato l'icona.
 */
export function pageOpenCart(sel) {
  for (const s of sel.cartIcon) {
    const el = document.querySelector(s);
    if (el) {
      (el.closest("button") || el.closest("a") || el).click();
      return true;
    }
  }
  return false;
}

/**
 * Numero di articoli nel carrello, letto dal contatore in navbar.
 * Ritorna null se il contatore non è presente (es. carrello vuoto: Esselunga
 * nasconde il link quando la quantità è 0).
 */
export function pageCartCount(sel) {
  for (const s of sel.cartCount) {
    const el = document.querySelector(s);
    if (el) {
      const n = parseInt((el.textContent || "").replace(/\D+/g, ""), 10);
      if (!Number.isNaN(n)) return n;
    }
  }
  return null;
}

/**
 * true se l'aggiunta risulta avvenuta.
 * Usa il CONTATORE del carrello (persistente) e non il toast: quest'ultimo
 * sparisce dopo pochi istanti, causando falsi "non confermato".
 * `before` è il conteggio letto prima del click (può essere null = carrello vuoto).
 */
export function pageAddConfirmed(sel, before) {
  for (const s of sel.cartCount) {
    const el = document.querySelector(s);
    if (el) {
      const n = parseInt((el.textContent || "").replace(/\D+/g, ""), 10);
      if (!Number.isNaN(n)) return before == null ? n > 0 : n > before;
    }
  }
  // Nessun contatore: ripiego sul toast, se per caso è ancora visibile.
  const t = document.querySelector("#actionFeedback");
  return t ? !t.classList.contains("ng-hide") : false;
}

/**
 * Aggiunge il prodotto della pagina corrente al carrello.
 * Ritorna { status: "added"|"not_found"|"blocked" }.
 * NON procede oltre l'aggiunta: nessun checkout, nessun pagamento.
 */
export function pageAddToCart(sel, qty) {
  const findFirst = (list) => {
    for (const s of list) {
      const el = document.querySelector(s);
      if (el) return el;
    }
    return null;
  };

  // Quantità: seleziona l'opzione giusta nel <select> (AngularJS).
  if (qty && qty > 1) {
    const q = findFirst(sel.quantitySelect);
    if (q && q.tagName === "SELECT") {
      const want = String(qty);
      let chosen = null;
      for (const o of q.options) {
        const label = (o.textContent || "").trim();
        if (label === want || o.value === want || o.value.endsWith(":" + want)) {
          chosen = o.value;
          break;
        }
      }
      if (chosen != null) {
        q.value = chosen;
        q.dispatchEvent(new Event("change", { bubbles: true }));
        q.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
  }

  const btn = findFirst(sel.addToCart);
  if (!btn) return { status: "not_found" };
  if (btn.disabled || btn.getAttribute("aria-disabled") === "true")
    return { status: "blocked" };
  btn.click();
  return { status: "added" };
}
