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
   (potrebbe vietarla → rischio account). Gate bloccante. Checklist sotto.
2. **Verifica dei selettori sulla pagina da LOGGATO**: i `SELECTORS` in
   `adapters/esselunga.js` sono stati ricavati dal DOM reale della pagina
   prodotto pubblica (2026-07-24) e la logica è verificata (`test/verify.html`,
   tutti PASS). Resta da confermare che la pagina da utente autenticato abbia la
   stessa struttura (il carrello vero è dietro il login).
3. Gestione robusta delle SPA/navigazioni e dei tempi di caricamento.

### Test automatici del flusso (senza login, senza toccare il sito)
Due suite, entrambe da tenere verdi:

1. **Adapter contro il DOM REALE** (18 test) — `test/verify.html` usa
   `test/fixture-esselunga.js`, cioè l'HTML realmente prodotto da
   spesaonline.esselunga.it (AngularJS, `value="number:N"`, toast `#actionFeedback`).
   Copre: login sì/no, quantità, prodotto esaurito, pagina inattesa, diagnostica.
   ```
   cd extension && python -m http.server 8099
   # apri http://localhost:8099/test/verify.html  → RESULT: ALL PASS
   ```
2. **Bridge web app ↔ estensione** (9 test) — `test/bridge.test.html`: inoltro
   del piano, ACK, e le protezioni (ignora messaggi da origini/tipi estranei,
   nessuna credenziale nel payload). Stesso server: apri
   `http://localhost:8099/test/bridge.test.html`.
3. **Orchestratore end-to-end** (17 test) — `test/orchestrator.test.mjs` simula le
   API di Chrome e verifica l'intero flusso: piano → login → apertura schede →
   aggiunta → conferma → carrello, più i vincoli di sicurezza (nessun checkout
   automatico, stop se non loggato, nessun "aggiunto" senza conferma, permessi
   del manifest minimi).
   ```
   node extension/test/orchestrator.test.mjs   # → RESULT: ALL PASS
   ```

### Checklist ToS da far verificare a un legale
- Il contratto/ToS di Esselunga Spesa Online **vieta l'accesso automatizzato,
  bot, scraping o strumenti di terze parti**? (cercare "automatizzato", "robot",
  "software", "terze parti", "uso consentito").
- È ammesso che **un software agisca sull'account per conto dell'utente**?
- Ci sono limiti su **frequenza/volume** delle richieste?
- L'automazione lato client (estensione dell'utente, sulla sua sessione) è
  trattata diversamente da un bot server-side?
- Qual è la **conseguenza** di una violazione (sospensione account)?
Esito atteso: GO (permesso o tollerato per uso personale) / NO-GO (vietato) /
MEGLIO-PARTNERSHIP (chiedere un accordo/API ufficiale = opzione A).

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
