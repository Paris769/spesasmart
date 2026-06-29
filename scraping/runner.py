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
from .spiders.eurospin_spider import EurospinSpider
from .spiders.iper_spider import IperSpider
from .spiders.famila_spider import FamilaSpider
from .spiders.cosicomodo_spider import CosiComodoSpider
from .enrich_images import enrich_images
from .dedup_products import dedup
from .prune import prune_prices

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

DB_URL = (
    os.getenv("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql://")
)

# Chains that must exist in the DB. Inserted on first run if missing.
_CHAINS_SEED = [
    ("Esselunga", "esselunga", True,  "https://www.esselunga.it/area-utente/spesa/home.html", "redirect"),
    ("Conad",     "conad",     True,  "https://www.conad.it/conad/home.html",                  "redirect"),
    ("Carrefour", "carrefour", True,  "https://www.carrefour.it/spesa-online/",                 "redirect"),
    ("Coop",      "coop",      True,  "https://www.cooponline.it",                              "redirect"),
    ("Lidl",      "lidl",      False, None,                                                      "none"),
    ("Eurospin",  "eurospin",  False, None,                                                      "none"),
    ("Pam",       "pam",       True,  "https://www.pampanorama.it/spesa-online",                "redirect"),
    ("MD",        "md",        False, None,                                                      "none"),
    ("Aldi",      "aldi",      False, None,                                                      "none"),
    ("Penny",     "penny",     False, None,                                                      "none"),
    ("Iper",      "iper",      False, None,                                                      "none"),
    ("Famila",    "famila",    True,  "https://www.cosicomodo.it/famila",                       "api"),
    ("Il Gigante", "ilgigante", True, "https://www.cosicomodo.it/ilgigante",                    "api"),
    ("Italmark",  "italmark",  True,  "https://www.cosicomodo.it/italmark",                     "api"),
]


async def ensure_chains(conn: asyncpg.Connection) -> None:
    """
    Insert any missing chains so spiders can always find their chain_id.

    Su catena già esistente AGGIORNA i campi di cui il seed è la fonte di
    verità (nome, shop_url, integration_type, has_online_shop): un tempo era
    DO NOTHING, perciò catene create prima che il seed avesse lo shop_url —
    es. Famila — restavano senza URL d'acquisto e senza pulsante "Acquista".
    """
    for name, slug, has_shop, shop_url, integration in _CHAINS_SEED:
        await conn.execute(
            """INSERT INTO chains (name, slug, has_online_shop, shop_url, integration_type, is_active)
               VALUES ($1, $2, $3, $4, $5, TRUE)
               ON CONFLICT (slug) DO UPDATE SET
                   name             = EXCLUDED.name,
                   has_online_shop  = EXCLUDED.has_online_shop,
                   shop_url         = EXCLUDED.shop_url,
                   integration_type = EXCLUDED.integration_type""",
            name, slug, has_shop, shop_url, integration,
        )


async def ensure_schema(conn: asyncpg.Connection) -> None:
    """
    Migrazioni idempotenti dello schema: colonne/tabelle aggiunte dopo il
    deploy iniziale. Eseguite a ogni run, innocue se già applicate.
    """
    await conn.execute(
        "ALTER TABLE prices ADD COLUMN IF NOT EXISTS product_url TEXT"
    )
    # Alias barcode → prodotto (vedi commento in init.sql): evita la "churn"
    # del dedup, cioè la ricreazione di doppioni a ogni scrape.
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS product_aliases (
               alias_barcode TEXT PRIMARY KEY,
               product_id    UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
               created_at    TIMESTAMPTZ DEFAULT NOW()
           )"""
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_product_aliases_product "
        "ON product_aliases(product_id)"
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


async def run_eurospin(
    conn: asyncpg.Connection, dry_run: bool, discover_only: bool
) -> None:
    async with httpx.AsyncClient() as client:
        spider = EurospinSpider(client, conn, dry_run=dry_run)
        if discover_only:
            count = await spider.discover_stores()
            print(f"\n=== Negozi Eurospin upsert: {count} ===")
        else:
            await spider.run()


async def run_iper(
    conn: asyncpg.Connection, dry_run: bool, discover_only: bool
) -> None:
    async with httpx.AsyncClient() as client:
        spider = IperSpider(client, conn, dry_run=dry_run)
        count = await spider.discover_stores()
        if discover_only:
            print(f"\n=== Negozi Iper upsert: {count} ===")


async def run_famila(
    conn: asyncpg.Connection, dry_run: bool, discover_only: bool
) -> None:
    async with httpx.AsyncClient() as client:
        spider = FamilaSpider(client, conn, dry_run=dry_run)
        count = await spider.discover_stores()
        print(f"\n=== Negozi Famila upsert: {count} ===")
        # NB: il price scraping via CosìComodo è DISATTIVATO — lo spider non
        # ha la mappatura negozio→baseSiteId (vedi diagnosi nel docstring di
        # cosicomodo_spider.py). Riattivare quando il fix è completo:
        #     price_spider = CosiComodoSpider(client, conn, dry_run=dry_run)
        #     await price_spider.scrape_prices()
        _ = discover_only  # discovery sempre eseguita finché il price-scrape è off


async def prepare_connection(max_attempts: int = 10) -> asyncpg.Connection:
    for attempt in range(max_attempts):
        conn = await asyncpg.connect(DB_URL)
        try:
            await conn.execute("SET statement_timeout = 0")
            await conn.execute("SET default_transaction_read_only = off")
            await conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
            await ensure_schema(conn)
            await ensure_chains(conn)
            return conn
        except (asyncpg.exceptions.CannotConnectNowError, asyncpg.exceptions.ReadOnlySQLTransactionError):
            await conn.close()
            if attempt == max_attempts - 1:
                raise
            logging.warning(
                "Connessione DB iniziale non scrivibile o non pronta: ritento (%d/%d)",
                attempt + 1,
                max_attempts,
            )
            await asyncio.sleep(min(30, 5 * (attempt + 1)))

    raise RuntimeError("Connessione DB non disponibile")


async def run_chain(conn: asyncpg.Connection, chain: str, args: argparse.Namespace) -> None:
    if chain == "esselunga":
        await run_esselunga(conn, args.dry_run, args.discover_only)
    elif chain == "conad":
        await run_conad(conn, args.dry_run)
    elif chain == "carrefour":
        await run_carrefour(conn, args.dry_run)
    elif chain == "eurospin":
        await run_eurospin(conn, args.dry_run, args.discover_only)
    elif chain == "iper":
        await run_iper(conn, args.dry_run, args.discover_only)
    elif chain == "famila":
        await run_famila(conn, args.dry_run, args.discover_only)
    elif chain == "cosicomodo":
        # Scrapa solo i prezzi CosiComodo (senza ri-discovery negozi Famila)
        async with httpx.AsyncClient() as client:
            spider = CosiComodoSpider(client, conn, dry_run=args.dry_run)
            await spider.scrape_prices()
    elif chain == "images":
        # Arricchimento immagini mancanti da Open Food Facts
        await enrich_images(conn, dry_run=args.dry_run)
    elif chain == "dedup":
        # Unisce i prodotti duplicati tra catene (--dry-run = anteprima)
        await dedup(conn, apply=not args.dry_run)
    elif chain == "prune":
        # Retention: elimina lo storico prezzi vecchio (vedi prune.py)
        if args.dry_run:
            logging.info("[DRY] prune saltato")
        else:
            await prune_prices(conn)
    else:
        logging.warning("Chain '%s' non ancora implementata", chain)


async def main(args: argparse.Namespace) -> None:
    if not DB_URL:
        sys.exit("Errore: DATABASE_URL non impostata")

    conn = await prepare_connection()
    try:
        chains = (
            [args.chain]
            if args.chain != "all"
            # 'prune' in testa: libera lo spazio dello storico prezzi prima
            # dei nuovi inserimenti (evita che il disco del DB si riempia).
            # 'cosicomodo' escluso dal run 'all': scrape per-negozio lungo,
            # ha un suo workflow dedicato.
            # 'dedup' va in coda: unisce i prodotti duplicati dopo lo scrape.
            else ["prune", "esselunga", "conad", "carrefour", "eurospin",
                  "iper", "famila", "dedup"]
        )

        for chain in chains:
            for attempt in range(2):
                try:
                    await run_chain(conn, chain, args)
                    break
                except (asyncpg.exceptions.CannotConnectNowError, asyncpg.exceptions.ReadOnlySQLTransactionError):
                    if attempt == 1:
                        raise
                    logging.warning(
                        "Connessione DB in sola lettura durante '%s': riapro e ritento una volta",
                        chain,
                    )
                    await conn.close()
                    conn = await prepare_connection()
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SpesaSmart scraper runner")
    parser.add_argument(
        "--chain",
        choices=["esselunga", "conad", "carrefour", "coop", "lidl", "eurospin", "aldi", "md", "penny", "iper", "famila", "cosicomodo", "images", "dedup", "prune", "all"],
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
