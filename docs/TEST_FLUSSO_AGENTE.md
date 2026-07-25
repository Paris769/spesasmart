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

## Cosa resta fuori dai test automatici

L'unico passaggio non automatizzabile è l'**esecuzione reale sul sito Esselunga
con l'utente autenticato**: richiede le credenziali dell'utente e la sua
sessione, e per progettazione avviene nel suo browser (l'estensione agisce sulla
sessione già aperta, non conserva password). Il comportamento dell'adapter è però
verificato contro il DOM reale del sito, quindi il rischio residuo è limitato a
eventuali differenze della pagina **da loggato**; in quel caso il popup
dell'estensione mostra una **diagnostica copiabile** che permette di correggere
i selettori in un passaggio.

Resta inoltre il gate legale: **revisione dei ToS Esselunga** sull'automazione
della sessione utente (checklist in `extension/README.md`).
