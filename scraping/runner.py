"""
SpesaSmart scraper runner.

Uso:
    python -m scraping.runner                   # tutti i chain
    python -m scraping.runner --chain esselunga
    python -m scraping.runner --chain conad
    python -m scraping.runner --chain esselunga --dry-run
    python -m scraping.runner --chain esselunga --discover-only
"""
import argparse
import asyncio
import logging
import os
import sys

import asyncpg
import httpx

from .spiders.esselunga_spider import EsselungaSpider
from .spiders.conad_spider import ConadSpider
from .spiders.carrefour_spider import CarrefourSpider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

DB_URL = (
    os.getenv("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql://")
)


async def run_esselunga(conn: asyncpg.Connection, dry_run: bool, discover_only: bool) -> None:
    async with httpx.AsyncClient() as client:
        spider = EsselungaSpider(client, conn, dry_run=dry_run or discover_only)

        if discover_only:
            stores = await spider.discover_stores()
            if stores:
                print("\n=== Negozi Esselunga trovati ===")
                for s in stores:
                    sid = s.get("id") or s.get("storeId") or s.get("idNegozio")
                    name = s.get("name") or s.get("nome") or s.get("descr")
                    city = s.get("city") or s.get("citta") or s.get("comune")
                    cap  = s.get("zipCode") or s.get("cap")
                    print(f"  id={sid:<6}  {name or '?':<35}  {city or '?':<20}  CAP={cap}")
            return

        await spider.run()


async def run_conad(conn: asyncpg.Connection, dry_run: bool) -> None:
    async with httpx.AsyncClient() as client:
        spider = ConadSpider(client, conn, dry_run=dry_run)
        await spider.run()


async def run_carrefour(conn: asyncpg.Connection, dry_run: bool) -> None:
    async with httpx.AsyncClient() as client:
        spider = CarrefourSpider(client, conn, dry_run=dry_run)
        await spider.run()


async def main(args: argparse.Namespace) -> None:
    if not DB_URL:
        sys.exit("Errore: DATABASE_URL non impostata")

    conn = await asyncpg.connect(DB_URL)
    try:
        chains = [args.chain] if args.chain != "all" else ["esselunga", "conad", "carrefour"]

        for chain in chains:
            if chain == "esselunga":
                await run_esselunga(conn, args.dry_run, args.discover_only)
            elif chain == "conad":
                await run_conad(conn, args.dry_run)
            elif chain == "carrefour":
                await run_carrefour(conn, args.dry_run)
            else:
                logging.warning("Chain '%s' non ancora implementata", chain)
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SpesaSmart scraper runner")
    parser.add_argument(
        "--chain",
        choices=["esselunga", "conad", "carrefour", "all"],
        default="all",
        help="Quale chain scrape (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stampa i prezzi senza scrivere nel DB",
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Stampa solo i negozi trovati dall'API senza scrape prezzi",
    )
    asyncio.run(main(parser.parse_args()))
