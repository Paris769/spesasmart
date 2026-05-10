"""
Spider per Pepesto API — copre Esselunga e Conad.
Documentazione: https://www.pepesto.com/
Richiede PEPESTO_API_KEY nell'ambiente.
"""
import os
import asyncio
import httpx
import asyncpg
from datetime import datetime

PEPESTO_BASE = "https://api.pepesto.com/api"
API_KEY = os.getenv("PEPESTO_API_KEY", "")

SUPERMARKETS = [
    {"domain": "spesaonline.esselunga.it", "chain_slug": "esselunga"},
    {"domain": "spesaonline.conad.it",     "chain_slug": "conad"},
]

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://spesasmart:spesasmart_dev@localhost:5432/spesasmart",
).replace("postgresql+asyncpg://", "postgresql://")


async def fetch_catalog(client: httpx.AsyncClient, domain: str) -> list[dict]:
    resp = await client.post(
        f"{PEPESTO_BASE}/catalog",
        json={"supermarket_domain": domain},
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("products", [])


async def upsert_products(conn: asyncpg.Connection, chain_slug: str, products: list[dict]):
    chain_id = await conn.fetchval(
        "SELECT id FROM chains WHERE slug = $1", chain_slug
    )
    if not chain_id:
        print(f"[WARN] Catena non trovata: {chain_slug}")
        return

    # Per ora inseriamo nel primo negozio disponibile (MVP)
    store_id = await conn.fetchval(
        "SELECT id FROM stores WHERE chain_id = $1 LIMIT 1", chain_id
    )
    if not store_id:
        print(f"[WARN] Nessun negozio per catena {chain_slug}")
        return

    now = datetime.utcnow()
    inserted = 0

    for p in products:
        name = p.get("name") or p.get("title", "")
        barcode = p.get("barcode") or p.get("ean")
        price_raw = p.get("price") or p.get("current_price")
        if not name or not price_raw:
            continue

        try:
            price = float(str(price_raw).replace(",", ".").replace("€", "").strip())
        except (ValueError, TypeError):
            continue

        # Upsert prodotto
        product_id = await conn.fetchval(
            """
            INSERT INTO products (barcode, name, brand, image_url, source)
            VALUES ($1, $2, $3, $4, 'pepesto')
            ON CONFLICT (barcode) DO UPDATE
                SET name = EXCLUDED.name,
                    updated_at = NOW()
            RETURNING id
            """,
            barcode,
            name,
            p.get("brand", ""),
            p.get("image_url") or p.get("image", ""),
        ) if barcode else await conn.fetchval(
            """
            INSERT INTO products (name, brand, image_url, source)
            VALUES ($1, $2, $3, 'pepesto')
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            name,
            p.get("brand", ""),
            p.get("image_url") or p.get("image", ""),
        )

        if not product_id:
            continue

        # Segna vecchi prezzi come non correnti
        await conn.execute(
            "UPDATE prices SET is_current = FALSE WHERE product_id = $1 AND store_id = $2",
            product_id, store_id,
        )

        original = p.get("original_price")
        promo = p.get("promo_label") or p.get("discount_label")

        await conn.execute(
            """
            INSERT INTO prices (product_id, store_id, price, original_price, promo_label,
                                price_per_unit, in_stock, source, scraped_at)
            VALUES ($1, $2, $3, $4, $5, $6, TRUE, 'pepesto', $7)
            """,
            product_id, store_id, price,
            float(original) if original else None,
            promo,
            p.get("price_per_unit"),
            now,
        )
        inserted += 1

    print(f"[{chain_slug}] Inseriti/aggiornati {inserted} prezzi")


async def run():
    if not API_KEY:
        print("[ERROR] PEPESTO_API_KEY non impostata — imposta la variabile d'ambiente")
        return

    conn = await asyncpg.connect(DB_URL)
    try:
        async with httpx.AsyncClient() as client:
            for sm in SUPERMARKETS:
                print(f"[{sm['chain_slug']}] Scaricamento catalogo...")
                try:
                    products = await fetch_catalog(client, sm["domain"])
                    print(f"[{sm['chain_slug']}] {len(products)} prodotti ricevuti")
                    await upsert_products(conn, sm["chain_slug"], products)
                except Exception as e:
                    print(f"[{sm['chain_slug']}] ERRORE: {e}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
