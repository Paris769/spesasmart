---
name: db-credential-sync
description: >-
  Sincronizza/ruota la password del database e propaga DATABASE_URL identico a
  Render e GitHub Actions con flusso "enter-once" (un solo punto di inserimento) e
  test fail-fast prima della propagazione. Usa quando l'utente dice cose come
  "sincronizza password database", "ruota credenziali Supabase Render GitHub",
  "DATABASE_URL non combacia", "password authentication failed", "ho resettato la
  password Supabase e ora il deploy non si connette", "aggiorna il secret
  DATABASE_URL". Progetto SpesaSmart: Supabase ref xlbfgufgprdarwlpziwl, Session
  pooler eu-central-1, backend Render spesasmart-backend, repo Paris769/spesasmart.
---

# db-credential-sync

Obiettivo: digitare la password del DB UNA volta sola e propagarla byte-per-byte
identica a (a) GitHub Actions secret `DATABASE_URL` e (b) Render env var
`DATABASE_URL`, con verifica della connessione PRIMA di propagare. Elimina la causa
ricorrente di "password authentication failed": il reinserimento manuale in piu' punti.

## Causa radice (perche' fallisce)
Le password generate da Supabase ("Generate a password") contengono caratteri
speciali (`#`, `@`, `:`, `/`, `&`) che NON sono URL-safe: dentro una stringa
`postgresql://user:PASS@host` rompono il parsing (es. `#` tronca la password) e
producono l'errore fuorviante "password authentication failed" anche quando la
password e' "giusta". Due rimedi, entrambi applicati da questa skill:
1. Usare una password SOLO ALFANUMERICA lunga (>=32) → intrinsecamente URL-safe.
2. Lo script passa comunque la password ad asyncpg come parametro separato (niente
   parsing URI), cosi' i caratteri speciali non rompono nulla.

## Parametri fissi del progetto (Session pooler, non cambiano al reset password)
- host: `aws-1-eu-central-1.pooler.supabase.com`
- port: `5432`
- user: `postgres.xlbfgufgprdarwlpziwl`
- database: `postgres`
- formato Render: `postgresql+asyncpg://...` (gli scraper fanno
  `.replace('postgresql+asyncpg://','postgresql://')`)

## Regole d'oro
1. La password si scrive UNA volta sola, in `.db_url.local` (gitignored).
2. Ogni altro punto riceve quel valore via script, mai ridigitato a mano.
3. Preferisci una password SOLO ALFANUMERICA lunga (>=32 char): azzera i problemi
   di URL-encoding. Se tieni caratteri speciali, lo script passa comunque la
   password ad asyncpg come parametro separato.
4. Mai stampare/loggare il segreto. Mai passarlo come argomento CLI.

## Procedura
1. Su Supabase: Project Settings > Database > Reset database password. Imposta una
   stringa ALFANUMERICA lunga e copiala subito (appare una sola volta).
2. Scrivi la connection string in `.db_url.local` SENZA newline finale:
   ```powershell
   Set-Content -NoNewline -Path .db_url.local -Value 'postgresql+asyncpg://postgres.xlbfgufgprdarwlpziwl:LA_PASSWORD@aws-1-eu-central-1.pooler.supabase.com:5432/postgres'
   ```
   (oppure aprire `.db_url.local` in un editor e sostituire il placeholder della password.)
3. Verifica che `.gitignore` contenga `.db_url.local` e `.render.key`. `git status`
   NON deve mostrarli.
4. (Opzionale, per Render via API) imposta in env `RENDER_API_KEY` e
   `RENDER_SERVICE_ID` (formato `srv-...`; per spesasmart-backend e' `srv-d80gk3faqgkc73a3ul50`).
5. Esegui lo script: testa la connessione (fail-fast) e, solo se OK, propaga:
   ```
   python scripts/sync_db_url.py            # GitHub + Render (se env presenti)
   python scripts/sync_db_url.py --deploy   # propaga e scatena deploy Render
   python scripts/sync_db_url.py --no-render
   ```
6. Se l'API Render non e' configurata, incolla manualmente UNA volta il contenuto
   identico di `.db_url.local` in Render Dashboard > spesasmart-backend >
   Environment > DATABASE_URL, poi Manual Deploy.
7. Verifica: `GET /health` del backend e un run degli scraper su GitHub Actions.

## Come funziona lo script `scripts/sync_db_url.py`
- Sorgente unica del segreto: `.db_url.local` (fallback su env `DATABASE_URL`).
- Test: scompone il DSN e passa i campi ad `asyncpg.connect(host=..., user=...,
  password=..., ssl="require", timeout=5)` come keyword args → nessun
  percent-encoding necessario. Classifica gli errori (28P01 password errata,
  28000 tenant/user not found, timeout IPv6, rete) senza mai stampare il segreto.
- GitHub: `gh secret set DATABASE_URL --repo Paris769/spesasmart` con il valore su
  STDIN (assente da argv e history).
- Render: `PUT /v1/services/{id}/env-vars/DATABASE_URL` body `{"value": ...}`
  (idempotente). L'update non ridepoya da solo: usa `--deploy` o Manual Deploy.

## Diagnosi rapida "password authentication failed"
- `28P01` → il valore propagato non combacia col DB: ri-esegui dal passo 1
  (preferendo una password alfanumerica).
- `28000` / "Tenant or user not found" → user errato (deve essere
  `postgres.xlbfgufgprdarwlpziwl`) o progetto Supabase in pausa.
- timeout → stai usando il direct host IPv6 invece del Session pooler IPv4.

## Due modalita': MANUALE (sync_db_url.py) vs AUTOMATICA (rotate_db_credentials.py)
Esistono ora DUE script complementari. Scegli in base al fatto che tu voglia
*inserire* una password gia' nota oppure *generare e ruotare* la password senza
toccare la dashboard.

### Modalita' A — MANUALE / "enter-once" — `scripts/sync_db_url.py`
Caso d'uso: hai appena fatto "Reset database password" dalla Dashboard Supabase e
vuoi solo propagare il valore. Tu scrivi la password in `.db_url.local`, lo script
testa e propaga. Resta la via di riferimento per la super-password `postgres`
quando preferisci generarla da dashboard. (Vedi "Procedura" sopra.)

### Modalita' B — AUTOMATICA / rotazione end-to-end — `scripts/rotate_db_credentials.py`
Caso d'uso: rotazione periodica SENZA aprire la dashboard, schedulabile in CI.
Sfrutta l'endpoint UFFICIALE della Supabase Management API
`PATCH /v1/projects/{ref}/database/password` (confermato dall'OpenAPI spec
`api_v1_openapi.json`, operationId `v1-update-database-password`) che imposta via
API la password del ruolo `postgres`. Lo script:
1. genera una password ALFANUMERICA forte (`secrets`, >=32, intrinsecamente
   URL-safe);
2. `PATCH .../database/password` con header `Authorization: Bearer <SUPABASE_PAT>`
   e body `{"password": "..."}`;
3. attende con retry+backoff esponenziale che il Session pooler accetti la nuova
   password (la propagazione sul pooler non e' istantanea), testando con asyncpg a
   parametri separati e `ssl="require"`;
4. solo dopo il test OK: `gh secret set DATABASE_URL` (STDIN) → Render
   `PUT env-vars/DATABASE_URL` → Render `POST deploys` → poll dello stato fino a
   `live` (o fallimento terminale `build_failed`/`update_failed`/`pre_deploy_failed`/
   `canceled`/`deactivated`);
5. fail-safe: in caso di propagazione fallita DOPO il cambio password, scrive la
   nuova connection string in `.db_url.local` (chmod 600, mai loggata) cosi' lo
   stato e' recuperabile rilanciando `sync_db_url.py`.
Flag: `--dry-run` (non cambia nulla, mostra solo i passi), `--skip-deploy`
(propaga senza far ripartire il deploy Render).

Credenziali (env o file gitignored): `SUPABASE_PAT` (o `.supabase.pat`),
`RENDER_API_KEY` (o `.render.key`), `RENDER_SERVICE_ID` (default
`srv-d80gk3faqgkc73a3ul50`), `GITHUB_REPO` (default `Paris769/spesasmart`).

Schedulazione: `.github/workflows/rotate-credentials.yml` (cron mensile
`30 3 1 * *` UTC + `workflow_dispatch`). L'orario e' una finestra "morta" scelta
per non collidere con gli altri workflow che usano lo stesso `DATABASE_URL`
(scraper 02:00, cosicomodo 04:30, guardian ogni 6h a 00/06/12/18 UTC): durante la
rotazione una connessione attiva con la vecchia password riceverebbe `28P01`.

### Quale usare
- Hai gia' la password (reset manuale dashboard) → **A** (`sync_db_url.py`).
- Vuoi rotazione automatica/periodica senza dashboard → **B**
  (`rotate_db_credentials.py`), che riusa identica la logica di test fail-fast +
  propagazione di A.
