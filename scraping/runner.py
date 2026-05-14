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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

DB_URL = (
    os.getenv("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql://")
)

# Scope: SpesaSmart confronta solo prezzi reali estratti da siti di spesa
# online + click & collect. Inseriamo nel DB esclusivamente catene che
# offrono almeno uno dei due servizi al consumatore.
#
# Una catena "fisica pura" (Lidl, Eurospin, MD oggi) sta in `_INACTIVE_CHAINS`:
# se è già stata inserita in passato la disattiviamo (is_active=FALSE,
# has_online_shop=FALSE), preservando però lo storico prezzi/negozi per
# eventuale futura riattivazione.
_CHAINS_SEED = [
    # (name, slug, has_online_shop, shop_url, integration_type)
    ("Esselunga",   "esselunga",   True, "https://www.esselunga.it/area-utente/spesa/home.html",   "redirect"),
    ("Conad",       "conad",       True, "https://www.conad.it/conad/home.html",                    "redirect"),
    ("Carrefour",   "carrefour",   True, "https://www.carrefour.it/spesa-online/",                   "redirect"),
    ("Coop",        "coop",        True, "https://www.cooponline.it",                                "redirect"),
    ("Pam",         "pam",         True, "https://www.pampanorama.it/spesa-online",                 "redirect"),
    ("Famila",      "famila",      True, "https://www.cosicomodo.it/famila",                        "api"),
    # IperDrive è la spesa online del gruppo Finiper Iper (EBSN / Digitelematica)
    ("Iper",        "iper",        True, "https://www.iperdrive.it/",                                "api"),
    # U2 Supermercato (gruppo Finiper Unes) — stessa piattaforma EBSN di IperDrive
    ("U2",          "u2",          True, "https://www.u2supermercato.it/spesa-online",              "api"),
    # Catene online-only / regionali da scrappare via scraper-builder
    ("Crai",        "crai",        True, "https://www.craispesaonline.it/",                          "redirect"),
    ("Bennet",      "bennet",      True, "https://www.bennet.com/spesa-online",                     "redirect"),
    ("Tigros",      "tigros",      True, "https://spesaonline.tigros.it/",                           "redirect"),
    ("Il Gigante",  "il-gigante",  True, "https://www.ilgigante.net/spesa-online",                  "redirect"),
]

# Catene fisiche pure (no spesa online, no click & collect consumer).
# Se sono già in DB le marchiamo inattive ma non le cancelliamo, così
# preserviamo lo storico per eventuali futuri lanci di servizi online.
_INACTIVE_CHAINS = ["lidl", "eurospin", "md"]


async def ensure_chains(conn: asyncpg.Connection) -> None:
    """Sincronizza le catene nel DB con lo scope corrente.

    - UPSERT delle catene attive: aggiorna shop_url / has_online_shop / integration
      se cambiate (così rigirare lo script ripristina la verità dichiarata qui).
    - Disattiva le catene fisiche pure: is_active=FALSE, has_online_shop=FALSE.
      I negozi e i prezzi storici restano in DB; le query del backend filtrano
      su has_online_shop=TRUE quindi non appariranno all'utente.
    """
    for name, slug, has_shop, shop_url, integration in _CHAINS_SEED:
        await conn.execute(
            """INSERT INTO chains (name, slug, has_online_shop, shop_url, integration_type, is_active)
               VALUES ($1, $2, $3, $4, $5, TRUE)
               ON CONFLICT (slug) DO UPDATE SET
                   name             = EXCLUDED.name,
                   has_online_shop  = EXCLUDED.has_online_shop,
                   shop_url         = EXCLUDED.shop_url,
                   integration_type = EXCLUDED.integration_type,
                   is_active        = TRUE""",
            name, slug, has_shop, shop_url, integration,
        )

    if _INACTIVE_CHAINS:
        await conn.execute(
            """UPDATE chains
                  SET is_active = FALSE,
                      has_online_shop = FALSE
                WHERE slug = ANY($1::text[])""",
            _INACTIVE_CHAINS,
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
        if discover_only:
            print(f"\n=== Negozi Famila upsert: {count} ===")
        else:
            # Dopo la discovery, scrapa anche i prezzi via CosìComodo
            price_spider = CosiComodoSpider(client, conn, dry_run=dry_run)
            await price_spider.scrape_prices()


async def main(args: argparse.Namespace) -> None:
    if not DB_URL:
        sys.exit("Errore: DATABASE_URL non impostata")

    conn = await asyncpg.connect(DB_URL)
    try:
        await ensure_chains(conn)
        # Lista delle chain attivabili da "--chain all".
        # Solo catene con spider implementato E spesa online attiva.
        # Le nuove (crai, bennet, tigros, il-gigante, u2) entreranno qui appena
        # i rispettivi spider sono pronti — vedi agente scraper-builder.
        chains = (
            [args.chain]
            if args.chain != "all"
            else ["esselunga", "conad", "carrefour", "iper", "famila", "cosicomodo"]
        )

        for chain in chains:
            if chain == "esselunga":
                await run_esselunga(conn, args.dry_run, args.discover_only)
            elif chain == "conad":
                await run_conad(conn, args.dry_run)
            elif chain == "carrefour":
                await run_carrefour(conn, args.dry_run)
            elif chain == "iper":
                await run_iper(conn, args.dry_run, args.discover_only)
            elif chain == "famila":
                await run_famila(conn, args.dry_run, args.discover_only)
            elif chain == "cosicomodo":
                # Scrapa solo i prezzi CosìComodo (senza ri-discovery negozi Famila)
                async with httpx.AsyncClient() as client:
                    spider = CosiComodoSpider(client, conn, dry_run=args.dry_run)
                    await spider.scrape_prices()
            elif chain in {"lidl", "eurospin", "md"}:
                logging.warning(
                    "Chain '%s' fuori scope (nessuna spesa online consumer) — skip",
                    chain,
                )
            else:
                logging.warning("Chain '%s' non ancora implementata", chain)
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SpesaSmart scraper runner")
    parser.add_argument(
        "--chain",
        choices=["esselunga", "conad", "carrefour", "iper", "famila", "cosicomodo", "all"],
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
