"""
Retention dei prezzi storici.

La tabella `prices` è una serie temporale: ogni scrape inserisce nuove righe
e marca le precedenti is_current=FALSE, senza mai cancellarle. Senza pulizia
cresce all'infinito finché il disco del DB si riempie (→ DB in sola-lettura,
scraper bloccati).

All'app servono solo le righe is_current=TRUE; lo storico (is_current=FALSE)
serve al grafico prezzi per un periodo limitato. Questo job elimina lo storico
più vecchio di PRICES_RETENTION_DAYS, mantenendo la tabella di dimensione
stabile. Va eseguito PRIMA degli scrape (libera spazio per i nuovi inserimenti).
"""
import logging
import os

import asyncpg

log = logging.getLogger("prune")

# Giorni di storico prezzi da conservare (oltre alle righe correnti).
RETENTION_DAYS = int(os.getenv("PRICES_RETENTION_DAYS", "30"))

# Cancellazione a batch: niente lock lunghi né sforamenti di statement timeout.
_BATCH = 20000


async def prune_prices(conn: asyncpg.Connection, days: int | None = None) -> int:
    """
    Elimina le righe `prices` storiche (is_current=FALSE) più vecchie di
    `days` giorni. Ritorna il numero di righe eliminate.
    """
    days = RETENTION_DAYS if days is None else days
    log.info("Prune: elimino i prezzi storici oltre %d giorni", days)

    total = 0
    while True:
        status = await conn.execute(
            f"""DELETE FROM prices WHERE ctid IN (
                    SELECT ctid FROM prices
                    WHERE is_current = FALSE
                      AND scraped_at < NOW() - make_interval(days => $1)
                    LIMIT {_BATCH}
                )""",
            days,
        )
        # asyncpg ritorna lo status tipo "DELETE 20000"
        try:
            deleted = int(status.split()[-1])
        except (ValueError, IndexError):
            deleted = 0
        total += deleted
        if deleted == 0:
            break
        log.info("  …%d righe storiche eliminate", total)

    log.info("=== Prune completato: %d righe storiche eliminate ===", total)

    # VACUUM: rende lo spazio riutilizzabile dalla tabella (no VACUUM FULL,
    # che richiederebbe spazio libero e un lock esclusivo).
    try:
        await conn.execute("VACUUM (ANALYZE) prices")
        log.info("VACUUM prices completato")
    except Exception as exc:  # noqa: BLE001
        log.warning("VACUUM saltato: %s", exc)

    return total
