"""Connessione DB condivisa per gli agenti (asyncpg)."""
import os

import asyncpg

DB_URL = (
    os.getenv("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql://")
)


async def connect() -> asyncpg.Connection:
    if not DB_URL:
        raise SystemExit("DATABASE_URL non impostata")
    return await asyncpg.connect(DB_URL)
