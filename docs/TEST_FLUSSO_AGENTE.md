# Test del flusso agente — catena pilota Esselunga

Esito del collaudo completo (2026-07-25). Ogni anello della catena è stato
esercitato: **backend → piano → UI → contratto messaggi → estensione → adapter**.

## Come rilanciare i test

```bash
# 1) Adapter contro il DOM REALE Esselunga (18 test)
cd extension && python -m http.server 8099
#    apri http://localhost:8099/test/verify.html        → ALL PASS

# 2) Bridge web app ↔ estensione (9 test)
#    apri http://localhost:8099/test/bridge.test.html   → ALL PASS

# 3) Orchestratore end-to-end, chrome simulato (17 test)
node extension/test/orchestrator.test.mjs               → ALL PASS
```

## Copertura

| Anello | Come è stato testato | Esito |
|---|---|---|
| Backend `optimize-quick` | Chiamate reali all'API in produzione | ✅ Esselunga nel piano, link `spesaonline.esselunga.it` validi |
| UI Lista (filtri, piano) | Browser sul sito live | ✅ filtri, calcolo, conferma, pulsante auto-carrello |
| Contratto messaggi | `test/bridge.test.html` (9) | ✅ inoltro, ACK, rifiuto origini estranee |
| Orchestratore | `test/orchestrator.test.mjs` (17) | ✅ login, sequenza prodotti, conferma, carrello |
| Adapter Esselunga | `test/verify.html` (18) su DOM reale | ✅ quantità Angular, esaurito, diagnostica |

**44 test automatici verdi** + verifica manuale sul sito in produzione.

### Garanzie di sicurezza verificate (non solo dichiarate)
- Nessun checkout/pagamento automatico (`T1e`).
- Se l'utente non è loggato l'agente **si ferma** e non apre schede (`T2a/T2b`).
- Nessun "aggiunto" senza conferma reale del sito (`T3a/T3b`) — niente falsi positivi.
- Prodotto esaurito → `blocked`, nessun click forzato (`T4a/T4b`).
- Il payload verso l'estensione **non contiene credenziali** (`T5a` bridge).
- Il manifest non chiede permessi eccessivi (niente cookies/webRequest/identity).

## Difetti trovati DAI test e corretti

1. **Esselunga irraggiungibile nel piano** (PR #61)
   Il confronto mostrava i primi 5 negozi in assoluto: con catene capillari
   (Famila, 74 negozi) erano **tutti lo stesso marchio**, quindi Esselunga non
   compariva mai — né nel confronto né per l'auto-carrello.
   → Ora il ranking tiene il **miglior negozio per catena** (6 catene distinte).

2. **Ricerca e piano guardavano aree diverse** (PR #62)
   Senza GPS l'autocomplete cercava a livello **nazionale** mentre il piano usava
   Milano: sceglievi un prodotto reale e il piano rispondeva "Non ho trovato"
   proprio su quello.
   → `DEFAULT_LOCATION` condivisa (`lib/location.ts`) tra ricerca e piano.

3. **"latte" pescava un formaggio** (PR #63)
   `Latte Montagna Alto Adige Stelvio DOP` a €19,49 entrava nei piani come latte.
   → Aggiunti stelvio/dop/formaggio/stagionato/asiago/… ai termini irrilevanti
   per "latte", sia nel piano sia nella ricerca.

## ✅ Verifica sulla pagina DA UTENTE AUTENTICATO (2026-07-25)

Era il rischio residuo principale: la pagina prodotto vista **da loggato**
poteva avere una struttura diversa da quella pubblica su cui erano tarati i
selettori. Verificato con l'utente autenticato sul sito reale (sola lettura del
DOM, nessuna modifica al carrello):

| Controllo dell'adapter | Esito da loggato |
|---|---|
| Rilevamento sessione (`pageIsLoggedIn`) | ✅ riconosce l'utente autenticato |
| Bottone `button[aria-label^="Aggiungi al carrello"]` | ✅ trovato col **primo** selettore, abilitato |
| Select quantità `select.esselunga-product-quantity-select` | ✅ trovato, opzioni `number:1/2/3` |
| Toast conferma `#actionFeedback` | ✅ presente |

**Conclusione: la struttura da loggato coincide con quella pubblica.** I selettori
dell'adapter sono validi nella sessione autenticata — non serve alcun adattamento.

## Cosa resta fuori dai test automatici

Solo il **click reale di aggiunta al carrello**, che per progettazione avviene nel
browser dell'utente dove è installata l'estensione (agisce sulla sessione già
aperta, non conserva password) e modifica un carrello vero. Tutto ciò che lo
precede — rilevamento login, individuazione degli elementi, quantità, conferma —
è verificato sulla pagina autenticata reale. Se il sito cambiasse, il popup
dell'estensione mostra una **diagnostica copiabile** per correggere i selettori
in un passaggio.

Resta inoltre il gate legale: **revisione dei ToS Esselunga** sull'automazione
della sessione utente (checklist in `extension/README.md`).
