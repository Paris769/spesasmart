/**
 * Bridge: gira sulle pagine SpesaSmart. Ascolta il piano carrello inviato dalla
 * web app via window.postMessage e lo inoltra al service worker. Accetta solo
 * messaggi dalla stessa origine (niente input da terzi).
 */
window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  if (event.origin !== window.location.origin) return;
  const data = event.data;
  if (!data || data.source !== "spesasmart" || data.type !== "CART_PLAN") return;

  chrome.runtime.sendMessage({ type: "CART_PLAN", payload: data.payload }, () => {
    // Segnala alla pagina che l'estensione ha preso in carico il piano.
    window.postMessage({ source: "spesasmart-ext", type: "CART_PLAN_ACK" }, window.location.origin);
  });
});

// Annuncia la presenza dell'estensione così la web app può mostrare il pulsante.
window.postMessage({ source: "spesasmart-ext", type: "EXT_PRESENT" }, window.location.origin);
