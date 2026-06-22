import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.router import api_router

ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://spesasmart.it",
    "https://www.spesasmart.it",
]
if vercel := os.getenv("VERCEL_URL"):
    ALLOWED_ORIGINS.append(f"https://{vercel}")
if frontend := os.getenv("FRONTEND_URL"):
    ALLOWED_ORIGINS.append(frontend)

app = FastAPI(
    title="SpesaSmart API",
    description="Confronto prezzi supermercati italiani con geolocalizzazione",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://spesasmart[a-z0-9\-]*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
async def health():
    """
    Usato da Render per health check e da UptimeRobot per
    tenere sveglio il servizio gratuito (ping ogni 14 minuti).
    """
    return {"status": "ok", "version": "0.1.0"}


@app.get("/ping")
async def ping():
    """Endpoint leggero per UptimeRobot — non tocca il DB."""
    return "pong"
