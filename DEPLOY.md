# SpesaSmart — Deploy gratuito completo

Stack: **Vercel** (frontend) + **Render** (backend) + **Supabase** (database) + **Upstash** (Redis)
Costo: **0 €/mese**

---

## STEP 1 — GitHub (5 minuti)

1. Vai su https://github.com e crea un account (gratis)
2. Crea un nuovo repository: `spesasmart` (pubblico o privato)
3. Dalla cartella del progetto sul tuo PC, esegui:

```bash
git init
git add .
git commit -m "primo commit"
git branch -M main
git remote add origin https://github.com/TUO-USERNAME/spesasmart.git
git push -u origin main
```

---

## STEP 2 — Supabase / Database (10 minuti)

1. Vai su https://supabase.com → **Start for free** → crea account
2. Crea nuovo progetto:
   - Nome: `spesasmart`
   - Password database: scegli una password sicura (salvala!)
   - Regione: **Frankfurt (EU)** ← importante per GDPR
3. Aspetta ~2 minuti che il progetto si inizializzi
4. Vai su **SQL Editor** (menu a sinistra) → **New query**
5. Copia e incolla il contenuto di `infra/init.sql` → **Run**
6. Verifica: vai su **Table Editor** → dovresti vedere le tabelle `chains`, `stores`, `products`, ecc.
7. Prendi la **connection string**:
   - Settings → Database → Connection string → **URI**
   - Appare così: `postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres`
   - **Sostituisci** `postgresql://` con `postgresql+asyncpg://`
   - Salvala: ti serve nel prossimo step

---

## STEP 3 — Upstash / Redis (5 minuti)

1. Vai su https://upstash.com → **Start for free** → crea account
2. Crea nuovo database Redis:
   - Nome: `spesasmart-cache`
   - Regione: **EU-West-1 (Ireland)**
   - Plan: **Free**
3. Una volta creato, clicca sul database → **Details**
4. Copia la **REDIS_URL** (formato: `rediss://:PASSWORD@ENDPOINT.upstash.io:6380`)
5. Salvala: ti serve nel prossimo step

---

## STEP 4 — Render / Backend (10 minuti)

1. Vai su https://render.com → **Get Started for Free** → crea account
2. **New** → **Web Service**
3. Collega il repository GitHub `spesasmart`
4. Configura il servizio:
   - **Name**: `spesasmart-backend`
   - **Root Directory**: `backend`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: Free
5. Aggiungi le variabili d'ambiente (sezione **Environment**):

   | Key | Value |
   |-----|-------|
   | `DATABASE_URL` | la stringa Supabase dello Step 2 |
   | `REDIS_URL` | la stringa Upstash dello Step 3 |
   | `SECRET_KEY` | scegli 32+ caratteri casuali |
   | `PEPESTO_API_KEY` | (lascia vuoto per ora) |

6. Clicca **Create Web Service**
7. Aspetta il primo deploy (~3-5 minuti)
8. Prendi l'URL del servizio: `https://spesasmart-backend.onrender.com`
9. Verifica aprendo: `https://spesasmart-backend.onrender.com/health`
   - Deve rispondere: `{"status": "ok", "version": "0.1.0"}`

### Deploy hook (per CI/CD automatico)
- Settings → **Deploy Hook** → copia l'URL
- Aggiungilo nei **GitHub Secrets** come `RENDER_DEPLOY_HOOK`

---

## STEP 5 — Vercel / Frontend (5 minuti)

1. Vai su https://vercel.com → **Start Deploying** → crea account con GitHub
2. **Add New Project** → importa il repository `spesasmart`
3. Configura:
   - **Root Directory**: `frontend`
   - **Framework Preset**: Next.js (rilevato automaticamente)
4. Aggiungi variabile d'ambiente:
   - `NEXT_PUBLIC_API_URL` = `https://spesasmart-backend.onrender.com/api/v1`
5. Clicca **Deploy**
6. Il sito sarà disponibile su: `https://spesasmart.vercel.app`

### Token per CI/CD automatico
- Account Settings → **Tokens** → crea token `spesasmart-deploy`
- Aggiungi nei GitHub Secrets:
  - `VERCEL_TOKEN` = il token appena creato
  - `VERCEL_ORG_ID` = Settings → General → Your ID
  - `VERCEL_PROJECT_ID` = nel progetto → Settings → General → Project ID

---

## STEP 6 — UptimeRobot / Anti-sleep (3 minuti)

Il backend Render free si addormenta dopo 15 minuti di inattività.
UptimeRobot lo pinga ogni 5 minuti, gratis.

1. Vai su https://uptimerobot.com → crea account gratuito
2. **Add New Monitor**:
   - Type: **HTTP(s)**
   - Friendly Name: `SpesaSmart Backend`
   - URL: `https://spesasmart-backend.onrender.com/ping`
   - Monitoring Interval: **5 minutes**
3. Salva → il backend resterà sempre sveglio

---

## STEP 7 — Verifica finale

Apri il browser e vai su `https://spesasmart.vercel.app`

Dovresti vedere:
- ✅ Homepage con barra di ricerca
- ✅ Bottone "Usa la mia posizione"
- ✅ Selettore raggio km (1, 3, 5, 10, 20)
- ✅ Navigazione: Cerca / Lista spesa / Mappa negozi / Scanner

---

## Aggiornamenti futuri

Ogni volta che fai `git push origin main`:
1. GitHub Actions esegue i test
2. Render rideploya il backend automaticamente
3. Vercel rideploya il frontend automaticamente

```bash
# Workflow quotidiano di sviluppo
git add .
git commit -m "aggiunta funzionalità X"
git push origin main
# → deploy automatico in ~3 minuti
```

---

## Migrazione futura (quando cresci)

Quando superi i limiti gratuiti, cambia solo le variabili d'ambiente:

```
DATABASE_URL  → Hetzner/Railway PostgreSQL
REDIS_URL     → Hetzner/Railway Redis
```

Il codice non cambia. Zero refactoring.

---

## Riepilogo URL

| Servizio | URL |
|---|---|
| Frontend | https://spesasmart.vercel.app |
| Backend API | https://spesasmart-backend.onrender.com |
| API Docs | https://spesasmart-backend.onrender.com/docs |
| Health check | https://spesasmart-backend.onrender.com/health |
| Dashboard DB | https://supabase.com/dashboard |
| Dashboard Redis | https://console.upstash.com |
