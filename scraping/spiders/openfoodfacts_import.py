"""
Importa prodotti da Open Food Facts per barcode lookup e dati nutrizionali.
Uso: python openfoodfacts_import.py --barcode 8001120600165
     python openfoodfacts_import.py --bulk   (scarica dump italiano)
"""
import argparse
import asyncio
import httpx
import asyncpg
import os

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://spesasmart:spesasmart_dev@localhost:5432/spesasmart",
).replace("postgresql+asyncpg://", "postgresql://")

OFF_API = "https://world.openfoodfacts.org/api/v0/product/{barcode}.json"


async def import_by_barcode(barcode: str, conn: asyncpg.Connection):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(OFF_API.format(barcode=barcode))

    if resp.status_code != 200:
        print(f"[OFF] Errore HTTP {resp.status_code}")
        return

    data = resp.json()
    if data.get("status") != 1:
        print(f"[OFF] Prodotto {barcode} non trovato")
        return

    p = data["product"]
    name = p.get("product_name_it") or p.get("product_name") or ""
    if not name:
        print(f"[OFF] Nome non disponibile per {barcode}")
        return

    existing = await conn.fetchval(
        "SELECT id FROM products WHERE barcode = $1", barcode
    )

    if existing:
        await conn.execute(
            "UPDATE products SET name = $1, brand = $2, image_url = $3 WHERE id = $4",
            name,
            p.get("brands", ""),
            p.get("image_front_url") or p.get("image_url", ""),
            existing,
        )
        print(f"[OFF] Aggiornato: {name}")
    else:
        await conn.execute(
            """
            INSERT INTO products (barcode, name, brand, image_url, source)
            VALUES ($1, $2, $3, $4, 'open_food_facts')
            ON CONFLICT DO NOTHING
            """,
            barcode,
            name,
            p.get("brands", ""),
            p.get("image_front_url") or p.get("image_url", ""),
        )
        print(f"[OFF] Inserito: {name}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--barcode", help="Singolo barcode da importare")
    args = parser.parse_args()

    conn = await asyncpg.connect(DB_URL)
    try:
        if args.barcode:
            await import_by_barcode(args.barcode, conn)
        else:
            print("Usa --barcode <codice> per importare un prodotto specifico")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
