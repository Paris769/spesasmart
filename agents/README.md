# SpesaSmart — Agente autonomo (Guardian)

`scraping/guardian.py` è l'agente di **sorveglianza e auto-riparazione** della
piattaforma. Automatizza le diagnosi e i rimedi che prima richiedevano
intervento manuale. Gira ogni 6 ore via `.github/workflows/guardian.yml`.

## Capacità

| Area | Cosa fa | Problema reale che previene |
|------|---------|------------------------------|
| **Liveness API** | Sonda gli endpoint dei cataloghi (Esselunga `route/v1`, CosìComodo `search-by-category`, Carrefour grid) e valida la *struttura* della risposta, non solo lo stato HTTP. | Esselunga `facet`→HTTP 204: lo spider era muto da giorni senza che nessuno se ne accorgesse. |
| **Salute DB** | Raggiungibilità, stato **sola-lettura** (disco pieno), dimensione vs limite del piano. | Il DB Render andato in sola-lettura per disco pieno, che bloccava tutti gli scraper. |
| **Freschezza** | Ultimo `scraped_at` per catena; segnala i cataloghi fermi oltre soglia. | Cataloghi che smettono di aggiornarsi silenziosamente. |
| **Copertura** | % prodotti con ≥2 negozi (la metrica "1 negozio") + conteggi. | Il problema "1 negozio" segnalato dall'utente. |

## Auto-riparazione

- **DB vicino al limite o in sola-lettura** → `prune` dello storico prezzi.
- **Catena ferma + endpoint vivo** → ri-scrape mirato (solo con `--heal all`).
- **Dopo qualsiasi scrape** → `dedup`.

L'agente **fallisce (exit 1) solo quando serve l'uomo**: API cambiata (richiede
fix allo spider) o DB non scrivibile non risolvibile col prune. I problemi
auto-riparabili non fanno fallire il run.

## Uso

```bash
python -m scraping.guardian                 # check + heal sicuro (prune, dedup)
python -m scraping.guardian --heal all       # include ri-scrape catene ferme
python -m scraping.guardian --check-only      # solo diagnosi
python -m scraping.guardian --probe-only      # solo liveness endpoint (no DB)
```

## Soglie (env)

| Variabile | Default | Significato |
|-----------|---------|-------------|
| `GUARDIAN_DB_LIMIT_MB` | 500 | Limite storage del piano DB |
| `GUARDIAN_DB_WARN_PCT` | 80 | Soglia % oltre cui fa il prune |
| `GUARDIAN_COVERAGE_MIN_PCT` | 40 | Copertura minima 2+ negozi prima dell'avviso |

## Output

- Annotazioni GitHub Actions (`::error::` / `::warning::`).
- Job summary leggibile nella pagina del workflow.
- `guardian-report.json` come artifact (14 giorni).

## Estendere le sonde

Per aggiungere il monitoraggio di una nuova catena, aggiungi una `Probe` alla
lista `PROBES` in `scraping/guardian.py` con un `validator` che controlli la
struttura attesa della risposta. I test in `scraping/tests/test_guardian.py`
documentano il pattern vivo-vs-cambiato.
