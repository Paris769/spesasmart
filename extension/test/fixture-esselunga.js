/**
 * Fixture: DOM REALE catturato da spesaonline.esselunga.it (2026-07-25).
 * Non è un mock scritto a mano: è l'HTML che il sito produce davvero, usato per
 * testare l'adapter contro la struttura vera (AngularJS, value="number:N", ecc).
 */

// Navbar in stato OSPITE: contiene il bottone "Accedi".
export const NAVBAR_GUEST = `
<div class="esselunga-navbar-right-item-list_v2__item">
  <button class="esselunga-navbar-right-item-list_v2__item__button" aria-label="Accedi">
    <i class="icon-account esselunga-navbar-right-item-list_v2__item__button__icon"></i>
  </button>
</div>
<div class="esselunga-navbar-right-item-list_v2__item">
  <button class="esselunga-navbar-right-item-list_v2__item__button">Accedi</button>
</div>`;

// Navbar in stato LOGGATO: niente "Accedi"; resta la barra con altre azioni
// (es. il bottone "Cerca" della navbar mobile realmente osservato sul sito).
export const NAVBAR_LOGGED = `
<div class="esselunga-navbar-right-item-list_v2__item esselunga-navbar-right-item-list_v2__item__mob">
  <button class="esselunga-navbar-right-item-list_v2__item__button" aria-label="Cerca">
    <i class="icon-search esselunga-navbar-right-item-list_v2__item__button__icon"></i>
  </button>
</div>`;

// Blocco quantità REALE: <select> AngularJS con value="number:N".
export const QUANTITY_BLOCK = `
<div class="esselunga-product-detail-item-right-action-quantita">
  <el-product-quantity>
    <label class="sr-only" for="slQta_">Quantità</label>
    <select id="slQta_" class="esselunga-product-quantity-select ng-pristine ng-untouched ng-valid ng-not-empty el-show" aria-label="Quantità" aria-invalid="false">
      <option label="1" value="number:1" selected="selected">1</option>
      <option label="2" value="number:2">2</option>
      <option label="3" value="number:3">3</option>
      <option label="4" value="number:4">4</option>
      <option label="5" value="number:5">5</option>
      <option label="6" value="number:6">6</option>
      <option label="7" value="number:7">7</option>
      <option label="8" value="number:8">8</option>
      <option label="9" value="number:9">9</option>
      <option label="..." value="string:...">...</option>
    </select>
  </el-product-quantity>
</div>`;

// Bottone aggiungi REALE (aria-label con nome prodotto).
export const ADD_BLOCK = `
<div class="esselunga-product-detail-item-right-action-add-to-cart">
  <div>
    <button class="el-btn px-5" aria-label='Aggiungi al carrello Esselunga Bio Farina di grano tenero tipo "00" 1000 g'>
      <span class="position-relative"></span>
      <i class="icon-cart-empty el-font-lg el-show"></i>
      <div class="el-spinner el-hide"><div></div><div></div><div></div><div></div></div>
    </button>
  </div>
</div>`;

// Bottone aggiungi in stato ESAURITO (ng-disabled reale → disabled).
export const ADD_BLOCK_OUT_OF_STOCK = `
<div class="esselunga-product-detail-item-right-action-add-to-cart">
  <div class="disabled">
    <button class="el-btn px-5" disabled aria-label="Aggiungi al carrello Prodotto Esaurito 500 g"></button>
  </div>
</div>`;

// Toast di conferma REALE (parte con ng-hide).
export const FEEDBACK_TOAST = `
<div id="actionFeedback" class="carrello-aggiunta esselunga-feedback-toast ng-hide" role="alert" aria-live="assertive" aria-hidden="true">
  <div class="esselunga-feedback-toast-content-header el-fw400"><span><i class="el-font-lg"></i></span><span></span></div>
</div>`;

/** Compone una pagina prodotto realistica. */
export function buildProductPage({ logged = false, outOfStock = false } = {}) {
  return (
    (logged ? NAVBAR_LOGGED : NAVBAR_GUEST) +
    QUANTITY_BLOCK +
    (outOfStock ? ADD_BLOCK_OUT_OF_STOCK : ADD_BLOCK) +
    FEEDBACK_TOAST
  );
}
