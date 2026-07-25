# SpesaSmart — Estensione Auto-carrello (pilota Esselunga)

Riempie automaticamente il carrello del supermercato con i prodotti del piano
SpesaSmart, **usando la sessione dell'utente già autenticato**. È la Fase 2
dell'architettura in [`docs/CART_AUTOMATION_ARCHITECTURE.md`](../docs/CART_AUTOMATION_ARCHITECTURE.md).

## Principio di sicurezza
- Le **credenziali le inserisci TU** sul sito ufficiale del supermercato.
- L'estensione **non legge e non salva** password né dati di pagamento.
- L'estensione fa solo `aggiungi al carrello`. **Il checkout e il pagamento
  restano un'azione tua**: nessun ordine viene inviato in automatico.
- Nessun tentativo di aggirare CAPTCHA/anti-bot: se il sito blocca, ci si ferma.

## Come si installa (sviluppo, "unpacked")
1. Chrome/Edge → `chrome://extensions` → attiva **Modalità sviluppatore**.
2. **Carica estensione non pacchettizzata** → seleziona la cartella `extension/`.
3. Apri SpesaSmart → **Lista** → calcola il piano → **"Riempi il carrello"**.
4. Se non sei loggato su Esselunga, l'estensione apre il sito: **accedi tu**,
   poi ripremi "Riempi il carrello".

## Stato: PILOTA — cosa manca prima della produzione
1. **Revisione legale dei ToS Esselunga** sull'automazione della sessione utente
   (potrebbe vietarla → rischio account). Gate bloccante.
2. **Verifica dei selettori** in `adapters/esselunga.js` (`SELECTORS`): vanno
   controllati/aggiornati sul sito reale loggato. Ora sono best-effort e
   difensivi (se non trovano, ricade sul deep-link manuale).
3. Gestione robusta delle SPA/navigazioni e dei tempi di caricamento.

## Struttura
- `manifest.json` — MV3, permessi minimi, host solo Esselunga + SpesaSmart.
- `background.js` — orchestratore: apre le schede, aggiunge, tiene il progresso.
- `adapters/esselunga.js` — selettori + funzioni in-page (login/aggiungi/conta).
- `bridge.content.js` — riceve il piano dalla web app (stessa origine) e lo inoltra.
- `popup.html` / `popup.js` — avanzamento e stato.

## Aggiungere un'altra catena
Duplica `adapters/esselunga.js`, aggiorna `meta` + `SELECTORS`, registralo in
`ADAPTERS` dentro `background.js` e aggiungi gli host in `manifest.json`. La via
pulita a lungo termine resta però l'**API/partnership ufficiale** (opzione A).
