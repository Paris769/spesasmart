# SpesaSmart — Agente della spesa (locale)

Agente autonomo che gira **sul tuo computer**: gli dai la lista, lui chiede
all'app il piano più conveniente, apre il sito del supermercato e **riempie il
carrello da solo**. Si ferma sempre prima del pagamento.

È l'alternativa all'estensione Chrome: stesso risultato, ma **non va installata
né ricaricata** — è un comando.

## Requisiti
- Node.js (già presente) e **Google Chrome** installato (usa quello, non scarica browser).

## Primo avvio: accedi una volta sola
```bash
cd agent-shopper
npm install
npm run login
```
Si apre un browser dedicato: **accedi tu** al supermercato. La sessione resta in
un profilo locale (`agent-shopper/.profile`, mai versionato) e le volte
successive non servirà più.

## Fare la spesa
```bash
node shopper.mjs "latte, pasta, caffe"
```
L'agente: calcola il piano → apre il sito → aggiunge ogni prodotto → verifica il
contatore del carrello → apre il carrello e si ferma.

Opzioni utili:
```bash
node shopper.mjs --dry-run "latte, pasta"        # mostra il piano, non tocca il carrello
node shopper.mjs --lat 45.36 --lng 9.69 "pane"   # posizione esplicita
node shopper.mjs --radius 30 "olio, riso"        # raggio di ricerca in km
```

## Cosa ti dice l'agente (trasparenza)

Prima di toccare il carrello l'agente dichiara sempre:
- **la zona** interrogata e il raggio (se non passi `--lat/--lng` usa Milano e te lo dice);
- **quante voci della TUA lista** finiranno davvero in quel carrello, ed elenca
  quelle che restano fuori (disponibili in altre catene, o introvabili).

Se la catena copre **meno della metà** della lista si ferma e chiede conferma
(`--anche-parziale`): meglio dirtelo che riempirti mezzo carrello in silenzio.

## Dove vive la sessione

Il profilo browser (che contiene i cookie di sessione) sta in
`%LOCALAPPDATA%/SpesaSmart/browser-profile` — **fuori** da OneDrive, per non
sincronizzare le tue credenziali di sessione sul cloud. Puoi cambiarlo con la
variabile d'ambiente `SPESASMART_PROFILE`.

## Sicurezza — per costruzione
- **Le credenziali non passano dall'agente**: le inserisci tu nel browser e
  restano nel profilo locale sul tuo computer. Il codice non le legge né le salva.
- **Nessun ordine, nessun pagamento**: l'agente aggiunge al carrello e si ferma;
  slot, checkout e pagamento restano atti tuoi.
- **Niente aggiramento di CAPTCHA/anti-bot**: se il sito blocca, l'agente si ferma.
- Se non sei connesso, **aspetta che tu acceda** invece di procedere alla cieca.

## Stato
Catena supportata: **Esselunga** (pilota). I selettori sono ricavati dal DOM reale
e verificati sul campo (aggiunta al carrello e conferma via contatore provate con
successo).

Per aggiungere una catena: duplica `adapters/esselunga.mjs`, aggiorna `meta` e
`SEL`, e registrala in `ADAPTERS` dentro `shopper.mjs`.

> ⚠️ Prima di un uso stabile o di distribuirlo ad altri utenti: far verificare a
> un legale i ToS della catena sull'automazione della sessione utente
> (checklist in `../extension/README.md`).
