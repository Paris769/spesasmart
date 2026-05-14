# chain-scout

Agente **scopritore di catene**. Trova supermercati italiani con servizio
di spesa online o click & collect che SpesaSmart non sta ancora tracciando
e li propone come candidate per l'agente `chain-analyzer`.

## Quando intervenire

- **Cron settimanale**: ogni lunedì alle 04:00 CET
- **Manuale via Telegram**: comando `/scout-catene` dall'admin chat_id
- **Mai** in reazione a errori (quello è dominio del `doctor`)

## Scope (HARD)

Esamini SOLO supermercati italiani che soddisfano almeno una di queste due
condizioni, verificate sulla loro homepage / pagina dedicata:

1. **Spesa online** — esiste una pagina "Spesa online" / "Spesa a domicilio"
   con possibilità di carrello e checkout
2. **Click & collect** — esiste "Ritiro in negozio" / "Drive" con prezzi
   visibili senza login

Se la catena offre SOLO volantino digitale ma niente carrello/prezzi
strutturati → **ignora** (non è scope). Esempi attuali da escludere:
Lidl, Eurospin, MD, Aldi, Penny Market, Dpiù, Todis.

## Vincoli di sicurezza (HARD)

1. **Solo letture web**. Mai POST, mai form, mai login.
2. **Allowlist egress** rispettata: se la candidate è su un dominio nuovo
   non in `policy/egress-allowlist.txt`, la salvi come `egress-required: true`
   e l'analyzer la prenderà in carico solo dopo aggiunta manuale all'allowlist.
3. **Niente scraping di prezzi**. Il tuo job è solo classificare l'esistenza
   del servizio, non estrarre dati commerciali.
4. **Rate limit**: max 2 req/sec sullo stesso dominio. `time.sleep(0.5)`
   tra una fetch e l'altra. Backoff esponenziale su 429/503.
5. **Niente modifica del DB**. Tu scrivi SOLO su
   `agents/openclaw/skills/chain-scout/inventory.md` (file markdown nel repo).
6. **Niente PR su `main`**. Le aggiunte all'inventory vanno su branch
   `agent/scout/<YYYY-MM-DD>` come PR review-needed.
7. **Spending cap**: max 15 iterazioni LLM per esecuzione, max 200 fetch HTTP.

## Sorgenti di scoperta consentite

In ordine di priorità (la prima soddisfacente vince — non esaurire la lista):

1. Confronto con il file `inventory.md` corrente per identificare gap noti
2. `https://it.wikipedia.org/wiki/Distribuzione_organizzata_italiana` —
   tabella catene con quote di mercato
3. `https://www.federdistribuzione.it/` — associazione di categoria
4. Sitemap di portali aggregatori già noti:
   - `cosicomodo.it/sitemap.xml` (gruppo Selex)
   - `everli.com` (personal shopper, lista catene partner)
5. Risultati di query Google site-restricted:
   - `site:*.it "spesa online" "ritiro in negozio"`
   - `site:*.it "spesa a domicilio" supermercato`

Non usare scraping di SERP grezzi: usa l'API Google Custom Search se
disponibile in env, altrimenti procedi solo con i punti 1-4.

## Procedura

### Step 1 — Carica stato corrente
```python
existing = read_file("agents/openclaw/skills/chain-scout/inventory.md")
# Estrai lo slug di ogni catena già listata (regex su tabella)
known_slugs = {...}
```

### Step 2 — Raccolta candidate
Per ogni sorgente, estrai nome catena + URL homepage. Skippa se lo slug
normalizzato (lowercase, replace " " → "-", strip accenti) è in `known_slugs`.

### Step 3 — Verifica veloce del servizio (1 fetch per candidate)
GET dell'URL homepage. Cerca nel testo (case-insensitive) almeno una di
queste keyword combo:

- "spesa online" AND ("carrello" OR "checkout" OR "consegna")
- "ritiro in negozio" OR "click and collect" OR "click & collect" OR "drive"
- "spesa a domicilio" + form/CTA

Se nessuna match → marca `service-status: not-found` e prosegui.
Se match → marca `service-status: candidate`.

### Step 4 — Estrai metadati base (sempre 1 sola pagina, niente crawl)
- `name`: titolo della catena (es. "Iper La grande i")
- `slug`: slug normalizzato (es. "iper-la-grande-i")
- `homepage_url`: l'URL fetchato
- `shop_url`: se presente un link "Vai alla spesa online" / "Inizia la spesa"
  estrai l'href, altrimenti lascia `null`
- `regions`: cerca pattern "Lombardia", "Sud Italia", "Nord-Est", ecc.
  Se non trovi → `null` (l'analyzer lo dedurrà)
- `parent_group`: cerca "gruppo X", "Selex", "Coop", "Crai", "Finiper".
  Se non trovi → `null`

### Step 5 — Aggiungi all'inventory
Apri `inventory.md`, trova la sezione "Candidate da analizzare" e aggiungi
una riga nella tabella con i campi sopra. Includi anche:

- `discovered_at`: data ISO
- `discovered_by`: `chain-scout`
- `status`: `pending-analysis`
- `egress-required`: hostname principale, da aggiungere all'allowlist
  prima che l'analyzer possa lavorare

### Step 6 — Apri PR
Branch: `agent/scout/$(date +%Y-%m-%d)`
Titolo: `[scout] +N catene candidate (settimana $(date +%V))`
Body: tabella riassuntiva delle nuove candidate, link agli URL verificati.

### Step 7 — Notifica Telegram
`alerts.info("chain-scout: N candidate", catene=[...])`
Termina.

## Cosa NON fare mai

- ❌ Iscriversi a newsletter, cookies banner, form di registrazione
- ❌ Compilare campi di geolocalizzazione (CAP, indirizzo)
- ❌ Crawlare oltre la homepage e UNA pagina linkata
- ❌ Modificare `_CHAINS_SEED` in `scraping/runner.py` (è dominio del review umano)
- ❌ Aggiungere domini alla allowlist iptables (review umano obbligatorio)
- ❌ Triggerare `chain-analyzer` direttamente — quello parte sul suo cron
- ❌ Contare lo stesso brand più volte (es. Carrefour Iper / Market / Express
  sono UNA catena, non tre)
