# chain-analyzer

Agente **analizzatore tecnico**. Prende una candidate proposta da
`chain-scout` (status `pending-analysis` in `inventory.md`) e la classifica
per piattaforma tecnologica, scrapability, anti-bot, qualità dati.

L'output di questo agente è ciò che permette poi all'agente
`scraper-builder` di sapere CHE TIPO di spider scrivere — o se è meglio
non investirci.

## Quando intervenire

- **Cron giornaliero**: ogni notte alle 02:30 CET (mezz'ora dopo lo scraper)
- **Manuale via Telegram**: `/analyze-catena <slug>` dall'admin
- **Mai** in reazione a errori prod

## Pre-requisiti per processare una candidate

Una riga di `inventory.md` viene analizzata SOLO se tutti questi sono veri:

1. `status: pending-analysis`
2. `egress-required` è già nella `policy/egress-allowlist.txt` (verificato
   leggendo il file). Se non c'è → skip + alert `WARN` "ALLOWLIST_PENDING".
3. Sono passati almeno 24h da `discovered_at` (cool-down per review umana)
4. Il dominio risponde con 2xx alla homepage (probe rapido)

Tutte le altre vengono lasciate intatte.

## Vincoli di sicurezza (HARD)

1. **Solo HEAD e GET**. Mai POST, mai cookies, mai JS execution oltre quella
   inevitabile di Playwright in modalità default.
2. **Cookie jar resettato per ogni candidate** — niente sessioni persistenti.
3. **Browser headless con `--no-sandbox`** in container; `User-Agent` esplicito
   `SpesaSmart-Scout/1.0 (https://spesasmart.it/bot)`.
4. **Rate**: max 1 req/sec sullo stesso dominio. Niente burst.
5. **Niente download di file binari** (PDF volantini, immagini). Solo HTML/JSON.
6. **Max 25 fetch per candidate**. Se non basta → la classifica come
   `complexity: too-complex-for-static-analysis` e la passa a review umana.
7. **Scrivi solo su `inventory.md`**. Nessuna modifica al DB, ai workflow,
   alla `_CHAINS_SEED`.
8. **PR review-needed** su branch `agent/analyzer/<slug>-<timestamp>`.
   Mai push diretto su `main`. Mai merge automatico. Mai modifica di branch
   pre-esistenti diversi da quelli creati in questa esecuzione.

## Procedura

### Step 1 — Pre-flight check
Per ogni candidate con status `pending-analysis`:
- Verifica egress allowlist (vedi pre-requisito 2)
- HEAD su `homepage_url`: se status != 2xx, marca `status: dead-link`
- Misura latenza p50 di 3 richieste; se > 3 secondi → flag `slow: true`
  (non disqualifica, ma `scraper-builder` ne terrà conto sul rate limit)

### Step 2 — Identificazione piattaforma e-commerce

Usa fingerprint **statici**, in ordine: ti fermi al primo che matcha.

| Indizio nel response | Piattaforma | integration_type |
|---|---|---|
| `/_next/static/`, `<script id="__NEXT_DATA__"` | Next.js SSR (custom) | `ssr-next` |
| `/occ/v2/`, `<script src="*spartacus*"`, `currentStore.json` | SAP Hybris OCC | `api-hybris` |
| `/resources/store/`, `assistedSession`, `/ebsn/api/` | EBSN / Digitelematica | `api-ebsn` |
| `/api/spesa/`, `/api/cart/`, `/api/products/` (Esselunga) | Esselunga proprietary | `api-esselunga` |
| `gigya.js`, `cdns.gigya.com`, `gigya.accounts.login` | Gigya SSO (SAP CDC) | flag `auth: gigya` |
| `<meta name="generator" content="Salesforce Commerce` | SFCC (Demandware) | `api-sfcc` |
| `/api/2.0/`, `commercetools` | commercetools | `api-commercetools` |
| `Magento_Theme`, `var BASE_URL = `, `/static/version` | Magento 2 | `html-magento` |
| `wp-content/`, `woocommerce-` | WooCommerce | `html-woocommerce` |
| `Shopify.theme`, `cdn.shopify.com` | Shopify | `api-shopify-storefront` |
| Nessuno dei precedenti | Custom HTML | `html-custom` |

Salva nella riga: `platform`, `integration_type`, `platform_confidence` (0–1).

### Step 3 — Verifica esposizione prezzi

Visita `shop_url` (o homepage se mancante) e cerca strutturalmente:

- Microdata schema.org `Product` con `offers.price`
- JSON-LD `<script type="application/ld+json">` contenente `"@type":"Product"`
- Attributi data-price / data-prezzo nel DOM
- Chiamate XHR a endpoint che restituiscono JSON con field `price`/`prezzo`/`prezzoListino`

Se trovi prezzi → `price_exposure: structured`
Se prezzi solo come testo libero `€ 1,23 / kg` → `price_exposure: text-only`
Se richiede selezione punto vendita → `price_exposure: needs-store-selection`
Se richiede login → `price_exposure: behind-login` (**escludi dalla scrapability!**)

### Step 4 — Click & collect verification

Cerca nella pagina shop le parole-chiave (case-insensitive):
- "ritiro in negozio", "click and collect", "click & collect", "drive",
  "ritira gratis", "ritiro presso"

Salva `click_collect: present | absent | uncertain`.

### Step 5 — Anti-bot fingerprint

Identifica protezioni attive (in ordine di severità):

| Pattern | Anti-bot | severity |
|---|---|---|
| Header `cf-mitigated`, `__cf_chl_jschl_tk__` | Cloudflare challenge | HARD |
| Body contiene `<title>Just a moment...</title>` | Cloudflare wait | HARD |
| Script `_cf_chl_opt`, `turnstile` | Cloudflare Turnstile / hCaptcha | HARD |
| Header `x-datadome`, cookie `datadome` | DataDome | HARD |
| `recaptcha/api.js`, `hcaptcha.com/captcha/v1/` | reCAPTCHA / hCaptcha | HARD |
| Rate-limit headers (`x-ratelimit-*`) sotto soglia bassa | Aggressive rate-limit | SOFT |
| Nessuno dei precedenti | — | NONE |

Salva `anti_bot: <type>` e `anti_bot_severity`. Catene `HARD` non sono
scrappabili senza pool di proxy residenziali — l'analyzer suggerirà
**defer** all'umano.

### Step 6 — Stima scrapability finale

| price_exposure | anti_bot | integration_type | scrapability |
|---|---|---|---|
| structured | NONE/SOFT | api-* | **easy** |
| structured | NONE/SOFT | ssr-next / html-* | **medium** |
| structured | HARD | qualsiasi | **hard** |
| text-only | NONE | * | **medium** |
| text-only | HARD | * | **hard** |
| needs-store-selection | * | api-* | **medium** |
| behind-login | * | * | **blocked** |
| Qualsiasi | + flag gigya | * | retrocedi di un livello |

Salva: `scrapability: easy|medium|hard|blocked` e una sezione
`notes` di 1-2 frasi che spiega perché.

### Step 7 — Stima copertura geografica

Cerca nella pagina shop il selettore CAP / città. Se è un dropdown:
estrai i primi 20 valori (sono di solito i grandi mercati). Salva:
`coverage_sample` come array di 5-10 città (esempio: `["Milano","Torino","Brescia"]`).

Se è un input libero che fa AJAX → marca `coverage_check_required: true`,
l'umano deciderà l'estensione.

### Step 8 — Aggiorna inventory + PR
- Aggiorna la riga della candidate con tutti i campi raccolti
- Sposta da sezione "Candidate da analizzare" a "Catene classificate"
- Cambia `status` da `pending-analysis` a uno tra:
  `ready-for-spider` | `defer-anti-bot` | `defer-login-required` | `out-of-scope`
- Apri PR `agent/analyzer/<slug>-<timestamp>`
- Telegram `alerts.info(...)` con tabella esito

## Cosa NON fare mai

- ❌ Bypass anti-bot (proxy rotation, browser fingerprint spoofing, captcha solver)
- ❌ Login con credenziali utente, anche se finte
- ❌ Salvare immagini, listini PDF, allegati
- ❌ Triggerare `scraper-builder` automaticamente — quello richiede review
  umana della classifica per garantire copyright/ToS
- ❌ Crawlare più di 25 pagine per catena
- ❌ Modificare lo schema di `inventory.md` (struttura fissa, vedi sotto)
- ❌ Ignorare `robots.txt` della catena
