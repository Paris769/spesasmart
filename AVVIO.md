# SpesaSmart — Avvio rapido

## Prerequisiti
- Docker Desktop installato e avviato
- Node.js 20+ (per sviluppo frontend locale)

## 1. Prima esecuzione

```bash
# Copia il file delle variabili d'ambiente
cp .env.example .env
# (opzionale) aggiungi la tua PEPESTO_API_KEY nel file .env

# Avvia tutti i servizi
docker-compose up -d

# Verifica che tutto sia running
docker-compose ps
```

## 2. Verifica backend

Apri nel browser: http://localhost:8000/docs
Troverai la documentazione interattiva Swagger di tutte le API.

## 3. Verifica frontend

Apri nel browser: http://localhost:3000

## 4. Importare prodotti di test

```bash
# Importa un prodotto da Open Food Facts tramite barcode
docker exec spesasmart_backend python scraping/spiders/openfoodfacts_import.py --barcode 8001120600165

# Esegui il crawler Pepesto (richiede PEPESTO_API_KEY)
docker exec spesasmart_backend python scraping/spiders/pepesto_spider.py
```

## 5. Struttura del progetto

```
supermercati/
├── backend/            # FastAPI — API REST
│   └── app/
│       ├── api/v1/    # Endpoint: auth, products, stores, lists, scan
│       ├── core/      # Config, sicurezza JWT
│       ├── db/        # Session SQLAlchemy
│       └── models/    # Modelli ORM
├── frontend/           # Next.js 14 — interfaccia utente
│   ├── app/           # Pagine: /, /mappa, /lista, /scanner
│   ├── components/    # Navbar, LocationBar, PriceCard, MapView
│   └── lib/           # API client, Zustand store
├── scraping/           # Crawler dati prezzi
│   └── spiders/       # pepesto_spider.py, openfoodfacts_import.py
├── infra/
│   └── init.sql       # Schema database + dati iniziali
└── docker-compose.yml  # Orchestrazione locale
```

## 6. API principali

| Endpoint | Descrizione |
|---|---|
| `GET /api/v1/stores/nearby?lat=&lng=&radius_km=` | Negozi vicini |
| `GET /api/v1/products/search?q=latte` | Ricerca prodotti |
| `GET /api/v1/products/{id}/prices?lat=&lng=&radius_km=` | Prezzi nei negozi vicini |
| `POST /api/v1/lists/{id}/optimize` | Ottimizza lista spesa |
| `GET /api/v1/scan/{barcode}?lat=&lng=` | Scan barcode |
| `POST /api/v1/auth/register` | Registrazione |
| `POST /api/v1/auth/login` | Login |
