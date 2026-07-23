# Auto-carrello — Architettura (estensione client-side)

> Stato: **proposta di design**, non ancora implementata.
> Obiettivo: dato un elenco di prodotti + scelta consegna/ritiro + filtri
> (prezzo più basso, disponibilità, meno negozi), riempire automaticamente il
> carrello sui siti dei supermercati **senza mai custodire le credenziali degli
> utenti sui nostri server**.

## 1. Il principio non negoziabile

**Le credenziali dei supermercati e i dati di pagamento non toccano mai i server
SpesaSmart.** L'automazione gira nel browser dell'utente, sulle pagine dove
l'utente è **già autenticato con la propria sessione**. L'invio finale
dell'ordine e il pagamento richiedono **sempre** un clic umano esplicito.

Questo distingue nettamente l'opzione B (questa) dall'opzione "vault lato server"
(scartata): lì diventeremmo custodi di migliaia di password → breach
catastrofico + GDPR + PCI-DSS. Qui il rischio credenziali è strutturalmente
azzerato.

## 2. Componenti

```
┌─────────────────────────┐        piano carrello        ┌──────────────────────┐
│   SpesaSmart Web App     │  ──────────────────────────▶ │   Estensione (MV3)    │
│   (Next.js, esistente)   │   externally_connectable /   │   - service worker     │
│                          │   postMessage firmato        │   - popup progresso    │
│  optimize-quick → piano  │ ◀──────────────────────────  │   - content scripts    │
│  {catena, prodotto, url, │        stato/progresso        │     per dominio catena │
│   qty, filtro}           │                              └──────────┬───────────┘
└─────────────────────────┘                                          │
                                                                     │ usa la sessione
                                                                     │ GIÀ loggata
                                                                     ▼
                                                        ┌──────────────────────────┐
                                                        │  Sito del supermercato     │
                                                        │  (login fatto dall'utente) │
                                                        └──────────────────────────┘
```

1. **Web App (esiste già)** — produce il *piano carrello*: la lista ottimizzata
   `{catena, prodotto, product_url, quantità}` in base ai filtri. La logica di
   ottimizzazione prezzo/disponibilità è già in `optimize-quick`.
2. **Estensione browser (nuova, Manifest V3)**
   - **Content script per catena**: sa (a) cercare/individuare un prodotto,
     (b) cliccare "aggiungi al carrello", (c) leggere lo stato del carrello.
   - **Service worker**: orchestra il piano, dialoga con la web app, tiene il
     progresso, gestisce i fallback.
   - **Popup**: mostra avanzamento, permette avvia/pausa, indica quali catene
     sono "collegate" (= l'utente è loggato in quella scheda).
   - **Nessuno storage di credenziali**: si appoggia ai cookie di sessione già
     presenti nel browser dell'utente. Se non loggato, apre la pagina di login
     e **l'utente accede da sé** (o col password manager del suo browser).
     L'estensione non legge né salva la password.
3. **Adapter per catena** — un modulo versionato per supermercato che implementa
   un'interfaccia comune. Isolati: se una catena cambia sito e si rompe, le
   altre continuano a funzionare.
4. **Bridge piano-carrello** — la web app pubblica il piano; l'estensione lo
   legge via canale sicuro (`externally_connectable` limitato al dominio
   SpesaSmart, o token di handoff firmato).

## 3. Interfaccia Adapter (design-as-code)

Ogni catena implementa questo contratto. Volutamente minimale e *fail-safe*:
se un passo non riesce, si degrada al deep-link (flusso handoff attuale).

```ts
export interface CartAdapter {
  chainSlug: string;
  /** domini su cui l'estensione ha i permessi (host_permissions) */
  hosts: string[];

  /** true se la sessione corrente del browser è autenticata su questa catena. */
  isLoggedIn(): Promise<boolean>;

  /** Apre la pagina di login. L'UTENTE si autentica: noi non tocchiamo la password. */
  promptLogin(): Promise<void>;

  /** Individua il prodotto sul sito (per url diretto, EAN o ricerca testuale). */
  locate(item: PlanItem): Promise<{ found: boolean; productPageUrl?: string }>;

  /** Aggiunge al carrello la quantità richiesta. Ritorna l'esito verificato. */
  addToCart(item: PlanItem, qty: number): Promise<AddResult>;

  /** Conta gli articoli nel carrello (per verifica idempotente). */
  getCartCount(): Promise<number>;

  /** Apre la pagina carrello per la revisione umana finale. */
  openCart(): Promise<void>;
}

type AddResult =
  | { status: "added"; verifiedQty: number }
  | { status: "out_of_stock" }
  | { status: "not_found" }
  | { status: "needs_login" }
  | { status: "blocked" };   // anti-bot / CAPTCHA → STOP, fallback handoff
```

Nota di design: `addToCart` **verifica** il risultato leggendo il carrello, così
il piano è idempotente e ri-eseguibile senza duplicare articoli.

## 4. Flusso end-to-end

1. In web app: utente costruisce la lista + sceglie consegna/ritiro + filtro
   (*prezzo più basso* / *disponibilità* / *meno negozi*).
2. Web app calcola il **piano carrello** (già fatto da `optimize-quick`).
3. Utente clicca **"Riempi il carrello"** → la web app consegna il piano
   all'estensione.
4. Per ogni catena del piano, l'estensione:
   a. `isLoggedIn()` → se no, `promptLogin()` e **l'utente accede**;
   b. per ogni articolo: `locate()` → `addToCart()` → verifica;
   c. mostra il progresso nel popup (es. 7/12 aggiunti).
5. A fine catena, `openCart()`: l'utente **rivede il carrello, sceglie lo slot,
   e fa checkout + pagamento a mano**. L'app non invia ordini e non paga.

## 5. Modello di sicurezza

| Dato | Dove vive | I nostri server lo vedono? |
|---|---|---|
| Password del supermercato | browser / keychain dell'utente | **MAI** |
| Cookie di sessione | browser dell'utente | **MAI** |
| Dati di pagamento | checkout della catena | **MAI** |
| Piano carrello (prodotti, prezzi) | web app + estensione | sì (non sensibile) |
| Flag "collegato" + preferenze | nostro DB (opzionale) | sì (solo booleano) |

Conseguenze: privacy policy + consenso servono solo per flag e preferenze
(dati personali minimi). Nessuna credenziale conservata = categoria di rischio
completamente diversa.

## 6. Rischio legale / ToS — onesto

- **Molti ToS di e-commerce grocery vietano l'accesso automatizzato/bot.**
  Automatizzare anche lato client può violarli → rischio di **sospensione
  dell'account dell'utente** e potenziale azione verso SpesaSmart come
  facilitatore.
- **Mitigazioni**:
  1. **Consenso esplicito per catena** ("questo automatizza la TUA sessione, sotto
     la tua responsabilità");
  2. **Human-in-the-loop**: noi non inviamo mai l'ordine e non paghiamo;
  3. **Ritmo umano e rispettoso** (non per eludere l'anti-bot — quello è una
     linea che non attraversiamo — ma per non gravare sui siti);
  4. **Meglio ancora**: chiedere il via libera / partnership alla catena →
     converte l'opzione B nell'opzione A (legittima e monetizzabile).
- **CAPTCHA / anti-bot**: **non** costruiamo elusione. Se una catena blocca
  attivamente l'automazione, quella catena è fuori finché non c'è un'API
  ufficiale. L'adapter deve degradare al flusso handoff.

### Matrice indicativa (da validare con un legale)
Partire solo dalle catene più permissive / dove esiste una relazione. Da valutare
per ciascuna: (1) clausola ToS sull'automazione, (2) presenza di anti-bot al
login/checkout, (3) esistenza di un canale ufficiale/partnership.

> ⚠️ Questa classificazione la deve confermare un legale sui ToS aggiornati:
> il codice non può dedurla.

## 7. Realtà della manutenzione

Ogni adapter è accoppiato al DOM/API della catena → **si rompe quando la catena
cambia il sito**. Serve monitoraggio + budget di manutenzione. Perciò: partire
con **1–2 catene pilota**, dimostrare il valore, poi espandere. Il Guardian
(agente ops già esistente) può sondare gli adapter e aprire una Issue quando uno
smette di funzionare.

## 8. Piano a fasi

- **Fase 0 (fatta)**: handoff + deep-link + "inserisci il prossimo".
- **Fase 1 (subito, senza estensione)**: piano carrello con **filtri**
  (prezzo più basso / disponibilità / meno negozi) in web app. Estende
  `optimize-quick` e `PurchasePlan`. Zero credenziali.
- **Fase 2 (pilota)**: scheletro estensione MV3 + interfaccia adapter + **1
  catena pilota** dove l'automazione è fattibile/permessa, con **login umano** e
  **checkout umano**.
- **Fase 3**: più adapter, UI di progresso, sync dei flag "collegato".
- **Fase 4**: perseguire **API/partnership ufficiali** (opzione A) per sostituire
  gli adapter fragili con integrazioni legittime.

## 9. Decisioni che spettano a te (titolare)

1. **Revisione legale** dei ToS delle catene target (serve il parere di un
   avvocato; io posso elencare le clausole da controllare).
2. **Catena pilota**: ✅ **Esselunga** (scelta 2026-07-23). Nota: prima di
   scrivere l'adapter serve la revisione legale dei ToS Esselunga (l'automazione
   della sessione utente potrebbe violarli) e la verifica dell'anti-bot su login
   e checkout. Esselunga ha consegna a domicilio + ritiro (`clicca e vai`), utile
   per testare entrambe le modalità di fulfillment.
3. **Appetito per la manutenzione** (gli adapter si rompono).
4. **Partnership ora?** Convertire il rischio in legittimità + ricavi.

### Stato implementazione
- **Fase 1 — FATTA** (2026-07-23): `optimize-quick` supporta il parametro
  `strategy` (`cheapest` / `availability` / `fewest_stores`), propaga `in_stock`
  per ogni voce e rimanda `recommended_plan` + `in_stock_count`. Nella tab Lista
  c'è il selettore di filtro; il piano si ricalcola al cambio strategia; gli
  articoli esauriti sono marcati "esaurito". Nessuna credenziale coinvolta.
- **Fase 2 — da avviare**: scheletro estensione MV3 + `CartAdapter` per Esselunga,
  subordinata alla revisione legale del punto 2.

## 10. Cosa posso costruire vs cosa no

**Posso**:
- API + UI del **piano carrello con filtri** (Fase 1);
- **scheletro estensione** MV3 + interfaccia adapter + **un adapter pilota** che
  fa ricerca/aggiungi-al-carrello sulla sessione già autenticata dell'utente, con
  checkout umano;
- il modello dei **flag "collegato"**.

**Non costruisco** (linee ferme):
- vault credenziali lato server + login headless (opzione C);
- elusione di CAPTCHA / anti-bot;
- invio automatico di ordini o pagamenti.

## 11. Prossimo passo consigliato

Partire dalla **Fase 1** (piano carrello con filtri): è utile da sola, non tocca
credenziali, ed è il substrato su cui la Fase 2 (estensione) si innesta. In
parallelo, avviare la revisione legale della catena pilota.
