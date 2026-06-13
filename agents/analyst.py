"""
Agente ANALYST — calcola i KPI reali dell'app e li scrive in state/metrics.json.

È la "fonte di verità" condivisa: gli altri agenti leggono questi numeri invece
di ricalcolarli (e invece di "inventarli"). Sola lettura sul DB.
"""
import asyncio
import datetime

from agents.common.db import connect
from agents.common.state import write_state


async def main() -> None:
    conn = await connect()
    try:
        total = await conn.fetchval("SELECT count(*) FROM products") or 0
        with_img = await conn.fetchval(
            "SELECT count(*) FROM products WHERE image_url IS NOT NULL AND image_url<>''"
        ) or 0
        prices_cur = await conn.fetchval(
            "SELECT count(*) FROM prices WHERE is_current"
        ) or 0
        cov = await conn.fetchrow(
            """
            WITH pp AS (
                SELECT product_id, count(DISTINCT store_id) AS n
                FROM prices WHERE is_current = TRUE GROUP BY product_id
            )
            SELECT count(*) AS tot, count(*) FILTER (WHERE n >= 2) AS multi FROM pp
            """
        )
        chains = await conn.fetch(
            """
            SELECT c.slug,
                   c.has_online_shop,
                   count(p.id) FILTER (WHERE p.is_current) AS prezzi,
                   round(EXTRACT(EPOCH FROM (now() - max(p.scraped_at)))/3600, 1) AS eta_ore
            FROM chains c
            LEFT JOIN stores s ON s.chain_id = c.id
            LEFT JOIN prices p ON p.store_id = s.id
            GROUP BY c.slug, c.has_online_shop
            ORDER BY prezzi DESC NULLS LAST
            """
        )
        zero = await conn.fetch(
            """
            SELECT lower(query) AS q, count(*) AS n
            FROM search_log
            WHERE n_results = 0 AND created_at > now() - interval '30 days'
            GROUP BY lower(query) ORDER BY n DESC LIMIT 25
            """
        )
        top = await conn.fetch(
            """
            SELECT lower(query) AS q, count(*) AS n
            FROM search_log
            WHERE created_at > now() - interval '30 days'
            GROUP BY lower(query) ORDER BY n DESC LIMIT 25
            """
        )

        metrics = {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "products_total": total,
            "products_with_image": with_img,
            "image_pct": round(100 * with_img / total, 1) if total else 0,
            "prices_current": prices_cur,
            "coverage_multi_pct": (
                round(100 * cov["multi"] / cov["tot"], 1) if cov and cov["tot"] else 0
            ),
            # Conversione esplicita: i numeric del DB (Decimal) altrimenti
            # finirebbero come stringhe in JSON (default=str) e romperebbero i
            # confronti negli agenti a valle.
            "chains": [
                {
                    "slug": r["slug"],
                    "has_online_shop": bool(r["has_online_shop"]),
                    "prezzi": int(r["prezzi"] or 0),
                    "eta_ore": float(r["eta_ore"]) if r["eta_ore"] is not None else None,
                }
                for r in chains
            ],
            "zero_result_searches": [
                {"q": r["q"], "n": int(r["n"])} for r in zero
            ],
            "top_searches": [
                {"q": r["q"], "n": int(r["n"])} for r in top
            ],
        }
        write_state("metrics.json", metrics)
        print(
            f"analyst: {total} prodotti, foto {metrics['image_pct']}%, "
            f"copertura 2+ negozi {metrics['coverage_multi_pct']}%, "
            f"{len(zero)} query a zero risultati"
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
