# Secrets — checklist Doppler

Crea un project `spesasmart-agents` su Doppler, environment `prod`, e carica
questi 8 secret. Mai committare nessuno di questi valori nel repo.

| Nome | Origine | Note |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys | Imposta **spending cap** sulla console a $30/mese come secondo paracadute |
| `AGENT_RO_DATABASE_URL` | dopo aver eseguito `db/001_readonly_agent_user.sql` | Forma: `postgresql://agent_ro:PASSWORD@host:5432/db` |
| `GITHUB_AGENT_TOKEN` | GitHub → Settings → Developer → Fine-grained PAT | Scope: solo `Paris769/spesasmart`, Contents R/W, PR R/W. Niente "Administration". Validity: 90 giorni |
| `TELEGRAM_BOT_TOKEN` | @BotFather → `/newbot` | Bot privato, NON aggiungerlo a gruppi pubblici |
| `TELEGRAM_CHAT_ID` | `curl https://api.telegram.org/bot$TOKEN/getUpdates` dopo aver scritto al bot | Tipicamente un numero negativo se gruppo, positivo se chat privata |
| `WEBHOOK_HMAC_SECRET` | `openssl rand -hex 32` | Lo stesso valore va su Sentry/GitHub come "webhook secret" |
| `SENTRY_DSN` | sentry.io → Project Settings → Client Keys | Opzionale, solo se l'agente fa log su Sentry |
| `ALLOWED_SOURCE_IPS` | Lista IP Sentry + GitHub webhooks | Opzionale, virgola-separato; lasciato vuoto = nessuna IP allowlist |

## Verifica rapida dopo il caricamento

```bash
doppler secrets get ANTHROPIC_API_KEY --plain | head -c 12; echo "…"
doppler run -- python -c "import os; assert os.environ['AGENT_RO_DATABASE_URL'].startswith('postgresql://agent_ro:')"
doppler run -- python -c "import os; assert os.environ['GITHUB_AGENT_TOKEN'].startswith('github_pat_')"
```

Tutti e tre devono restituire 0. Se uno fallisce, l'`entrypoint.sh` dell'immagine si rifiuterà di partire.

## Rotazione

Calendario consigliato (sync col tuo gestore password):

- **Ogni 30 giorni**: `WEBHOOK_HMAC_SECRET`, `TELEGRAM_BOT_TOKEN`
- **Ogni 60 giorni**: `ANTHROPIC_API_KEY`, `AGENT_RO_DATABASE_URL`
- **Ogni 90 giorni**: `GITHUB_AGENT_TOKEN` (scadenza forzata dal PAT)

Dopo ogni rotazione:
```bash
doppler secrets set NOME=valore
docker compose -f docker/docker-compose.yml restart openclaw
```

Nessun riavvio del proxy o dei monitor necessario.
