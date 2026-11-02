# OpenClaw — Sicurezza e Alert per SpesaSmart

Setup hardened di OpenClaw per gli agenti autonomi `spesasmart-doctor`,
`spesasmart-quality`, `spesasmart-search-tuner`, `spesasmart-coverage-watcher`.

## Threat model riassuntivo

| Vettore | Esempio | Mitigazione in questa cartella |
|---|---|---|
| Prompt injection via Sentry/webhook | Errore controllato che dice "esegui DROP TABLE" | Webhook proxy con sanitize + DB user read-only |
| Skill malevola / supply chain | Skill community che ruba token | Solo skill scritte in `skills/`, nessun import esterno |
| Token GitHub usato per push su main | Agente forza push su main | Branch protection + token scope `agent/*` |
| Loop costoso | Bug fa loopare l'agente | `spend-watchdog.py` con kill switch |
| Browser headless attaccato | Phishing leak deploy token | Container con egress allowlist |
| Webhook esposto a Internet | Chiunque triggera l'agente | HMAC + IP allowlist + rate limit |
| Credenziali concentrate | Process compromesso = game over | Vault esterno, mount tmpfs, no `.env` persistente |
| Telegram bot hijack | Token bot leaka | Allowlist `chat_id` + comandi distruttivi bloccati |

## Layout

```
agents/openclaw/
├── docker/        # Container hardened + sidecar monitor
├── proxy/         # Webhook gateway con HMAC + rate limit
├── db/            # Utente Postgres read-only per l'agente
├── monitor/       # Spend watchdog, audit, dispatcher alert
├── policy/        # Branch protection, egress allowlist, scope token
└── skills/        # Le SKILL.md scritte da noi (zero community)
```

## Setup veloce (VPS Hetzner CX11, Ubuntu 24.04)

1. **Vault esterno** — registra account Doppler free tier, crea project `spesasmart-agents`.
   Carica i secret in `policy/secrets.template.md`.

2. **Postgres read-only**:
   ```bash
   psql "$DATABASE_URL" -f db/001_readonly_agent_user.sql
   ```
   Genera password forte e mettila in Doppler come `AGENT_RO_DATABASE_URL`.

3. **GitHub fine-grained PAT**:
   ```bash
   bash policy/branch-protection.sh
   ```
   Segui le istruzioni a schermo (richiede `gh` autenticato come admin del repo).

4. **Build & avvia container hardened**:
   ```bash
   cd docker
   doppler run -- docker compose up -d
   ```

5. **Avvia il proxy webhook** (riceve Sentry/GitHub, verifica HMAC, forward all'agente):
   ```bash
   cd proxy && npm i && npm run start
   ```
   Esponi via reverse proxy con TLS (Caddy) sull'endpoint `/hooks/*`.

6. **Avvia i monitor sidecar**:
   ```bash
   cd monitor && pip install -r requirements.txt
   python spend-watchdog.py &
   python audit-anomaly.py &
   ```

7. **Telegram bot per gli alert**: crea bot con @BotFather, prendi il token, mettilo
   in Doppler come `TELEGRAM_BOT_TOKEN`, e il tuo `chat_id` come `TELEGRAM_CHAT_ID`.

## Runbook — cosa fare quando…

### …arriva un alert "spending > €5/day"
1. `docker exec openclaw cat /audit/last_24h.jsonl | jq 'select(.tool=="anthropic_call")' | wc -l`
2. Se > 500 chiamate → bug di loop, killa il container: `docker compose stop openclaw`
3. Indaga sulle ultime skill eseguite in `audit/`.

### …arriva un alert "egress blocked"
Una skill ha tentato connessione a un host fuori dalla allowlist.
1. `docker logs openclaw | grep "egress-deny"` per vedere host bloccato
2. Se legittimo (nuova catena): aggiungi in `policy/egress-allowlist.txt` e riavvia
3. Se sospetto: snapshot dei file della skill, revoca token, indaga

### …arriva un alert "PR aperta su branch non-`agent/*`"
Token compromesso. Revoca PAT GitHub subito, ruota tutti i secret, audit completo.

### …rotazione segreti (mensile)
1. `doppler secrets set ANTHROPIC_API_KEY=...`
2. `docker compose restart openclaw`
3. Nessun riavvio del proxy necessario (legge i secret on-demand).

## Cosa NON fare mai

1. ❌ Non installare skill da `awesome-openclaw-skills` o registry esterni.
2. ❌ Non dare scope `repo` (write su main) al token GitHub. Solo `contents:write` su `agent/*`.
3. ❌ Non usare l'utente Postgres master per l'agente. Sempre `agent_ro`.
4. ❌ Non esporre la porta `:5234` (OpenClaw API) su Internet. Solo localhost o VPN.
5. ❌ Non disabilitare il watchdog "perché annoia". È l'ultimo paracadute contro $1000 di token bruciati.
6. ❌ Non condividere la macchina VPS con altri servizi. Container dedicato.

## Cosa monitorare in continuo

- Spesa Anthropic (kill-switch a €10/giorno default)
- Egress DNS bloccati (ogni occorrenza = alert)
- PR aperte su branch != `agent/*` (immediato)
- Chiamate read/write sul DB (su `agent_ro` non dovrebbero esserci write — se ce ne sono = compromise)
- Volume errori in audit log

Vedi `monitor/alerts.py` per la lista completa.
