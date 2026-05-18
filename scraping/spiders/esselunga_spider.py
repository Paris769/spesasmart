"""
Esselunga price scraper — piattaforma spesaonline.esselunga.it/commerce/

Flusso:
  1. GET  /commerce/resources/route/v1/supermercato
        → leftMenuItems: albero categorie completo (843 voci), da cui si
          estraggono tutti i productSetId unici (~710).
  2. POST /commerce/resources/displayable/productset
        body {productSetIds:[…tutti…], length:100, start:N}
        → catalogo completo paginato (rowCount ~25.000 prodotti).
  3. Upsert prodotti + prezzi a batch (una transazione per pagina).

L'API è PUBBLICA (nessun login, nessuna sessione). Esselunga non espone i
prezzi per-negozio via web, quindi tutti i prezzi sono associati a un negozio
virtuale "Esselunga Online" (external_id = esselunga-online).

Storia: la vecchia API /commerce/resources/search/facet ora risponde HTTP 204.
La nuova coppia route + productset è stata individuata via reverse-engineering
del sito.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

import asyncpg
import httpx

from ..aliases import resolve_existing

log = logging.getLogger("esselunga")

COMMERCE_BASE = "https://spesaonline.esselunga.it/commerce"
ROUTE_URL = f"{COMMERCE_BASE}/resources/route/v1/supermercato"
PRODUCTSET_URL = f"{COMMERCE_BASE}/resources/displayable/productset"
PAGE_SIZE = 100      # il server limita la pagina a 100 entità
RATE = 1.0           # secondi tra una richiesta e l'altra

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "it-IT,it;q=0.9",
    "Content-Type": "application/json",
    "Referer": "https://spesaonline.esselunga.it/",
    "X-PAGE-PATH": "supermercato",
}

# Sede Esselunga (Milano) — coordinate del negozio online virtuale
_ESSELUNGA_LNG = 9.1859
_ESSELUNGA_LAT = 45.4654


class EsselungaSpider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        conn: asyncpg.Connection,
        dry_run: bool = False,
    ):
        self.client = client
        self.conn = conn
        self.dry_run = dry_run
        self._t_last = 0.0

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._t_last
        if elapsed < RATE:
            await asyncio.sleep(RATE - elapsed)
        self._t_last = loop.time()

    async def _get(self, url: str) -> dict | None:
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.get(
                    url, headers=HEADERS, follow_redirects=True, timeout=45
                )
                if r.status_code == 200:
                    return r.json()
                log.warning("GET HTTP %s tentativo %d — %s",
                            r.status_code, attempt + 1, url)
                if r.status_code in (401, 403, 404):
                    return None
            except (httpx.RequestError, ValueError) as exc:
                log.warning("GET tentativo %d errore: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    async def _post(self, url: str, body: dict) -> dict | None:
        await self._throttle()
        for attempt in range(3):
            try:
                r = await self.client.post(
                    url, json=body, headers=HEADERS, timeout=45
                )
                if r.status_code == 200:
                    return r.json()
                log.warning("POST HTTP %s tentativo %d — %s",
                            r.status_code, attempt + 1, r.text[:200])
                if r.status_code in (401, 403, 404):
                    return None
            except (httpx.RequestError, ValueError) as exc:
                log.warning("POST tentativo %d errore: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    # ------------------------------------------------------------------
    # Store management
    # ------------------------------------------------------------------

    async def discover_stores(self) -> list[dict]:
        """Ritorna il descrittore del negozio online virtuale Esselunga."""
        return [{"id": "online", "name": "Esselunga Online", "type": "ecommerce"}]

    async def match_stores(self, es_stores: list[dict]) -> dict[str, str]:
        """Trova o crea lo store 'Esselunga Online'. Ritorna {'online': uuid}."""
        row = await self.conn.fetchrow(
            """
            SELECT s.id
            FROM stores s
            JOIN chains c ON s.chain_id = c.id
            WHERE c.slug = 'esselunga' AND s.external_id = 'esselunga-online'
            """
        )
        if row:
            log.info("Esselunga Online store trovato: %s", row["id"])
            return {"online": str(row["id"])}

        if self.dry_run:
            log.info("[DRY] Creerebbe Esselunga Online store")
            return {"online": "00000000-0000-0000-0000-000000000000"}

        chain_id = await self.conn.fetchval(
            "SELECT id FROM chains WHERE slug = 'esselunga'"
        )
        if not chain_id:
            log.error("Chain 'esselunga' non trovata nel DB — esegui init.sql")
            return {}

        new_id = await self.conn.fetchval(
            """
            INSERT INTO stores
                (chain_id, name, address, city, province, postal_code,
                 coordinates, external_id, has_delivery, has_click_collect, is_active)
            VALUES
                ($1, 'Esselunga Online', 'E-commerce', 'Milano', 'MI', '20100',
                 ST_SetSRID(ST_MakePoint($2, $3), 4326),
                 'esselunga-online', TRUE, TRUE, TRUE)
            RETURNING id
            """,
            chain_id, _ESSELUNGA_LNG, _ESSELUNGA_LAT,
        )
        log.info("Creato Esselunga Online store: %s", new_id)
        return {"online": str(new_id)}

    # ------------------------------------------------------------------
    # Category / productSet discovery
    # ------------------------------------------------------------------

    async def _get_product_sets(self) -> list[int]:
        """
        Estrae tutti i productSetId unici dall'albero categorie
        (route/v1/supermercato → leftMenuItems).
        """
        data = await self._get(ROUTE_URL)
        if not data:
            return []
        menu_items = data.get("leftMenuItems") or []
        sets: set[int] = set()
        for item in menu_items:
            for ps in item.get("menuItemProductSets") or []:
                pk = ps.get("pk") or {}
                sid = pk.get("productSetId")
                if sid is not None:
                    sets.add(int(sid))
        log.info("Trovati %d productSet unici da %d voci di menu",
                 len(sets), len(menu_items))
        return sorted(sets)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_unit_price(label: str | None) -> float | None:
        """Parsa "1,65 € / l" → 1.65, "0,22 € / 100 g" → 2.20 (per-kg/per-litro)."""
        if not label:
            return None
        m = re.search(
            r"(\d+[,.]?\d*)\s*€\s*/\s*(\d+)?\s*(kg|g|l|cl|ml|pz|unità|conf\.?)",
            label,
            re.IGNORECASE,
        )
        if not m:
            return None
        try:
            val = float(m.group(1).replace(",", "."))
            qty = float(m.group(2)) if m.group(2) else 1.0
            unit = m.group(3).lower()
            if qty == 0:
                return None
            if unit == "g":
                return round(val / qty * 1000, 4)
            if unit == "kg":
                return round(val / qty, 4)
            if unit == "ml":
                return round(val / qty * 1000, 4)
            if unit == "cl":
                return round(val / qty * 100, 4)
            if unit == "l":
                return round(val / qty, 4)
        except (ValueError, ZeroDivisionError):
            pass
        return None

    @staticmethod
    def _extract_promo(product: dict) -> str | None:
        promos = product.get("promotionsDetail") or []
        if not promos:
            return None
        p = promos[0]
        if isinstance(p, dict):
            return (
                p.get("label")
                or p.get("description")
                or p.get("title")
                or str(p)[:50]
            )
        return str(p)[:50]

    def _normalize(self, p: dict) -> dict | None:
        """Estrae i campi utili da un prodotto, o None se non valido."""
        code = str(p.get("code") or p.get("id") or "").strip()
        if not code:
            return None
        name = (p.get("description") or "").strip()
        if not name:
            return None

        # discountedPrice = prezzo attuale; price = prezzo di listino
        raw_current = p.get("discountedPrice")
        raw_original = p.get("price")
        if raw_current is None:
            raw_current = raw_original
            raw_original = None
        try:
            current_price = float(raw_current)
            original_price = (
                float(raw_original) if raw_original is not None else None
            )
        except (ValueError, TypeError):
            return None
        if current_price <= 0:
            return None
        # original ha senso solo se c'è davvero uno sconto
        if original_price is not None and original_price <= current_price:
            original_price = None

        return {
            "barcode": f"esselunga-{code}",
            "name": name,
            "brand": (p.get("brand") or "").strip() or None,
            "image_url": p.get("imageURL") or p.get("imageUrl") or None,
            "price": current_price,
            "original_price": original_price,
            "promo_label": self._extract_promo(p),
            "price_per_unit": self._parse_unit_price(p.get("label")),
            "in_stock": not bool(p.get("outOfStock", False)),
            # link diretto alla scheda prodotto (lo slug finale è cosmetico:
            # la SPA Esselunga risolve il prodotto dal solo codice numerico)
            "product_url": (
                "https://spesaonline.esselunga.it/commerce/nav/supermercato"
                f"/store/prodotto/{code}/p"
            ),
        }

    # ------------------------------------------------------------------
    # DB upsert (batch — una transazione per pagina)
    # ------------------------------------------------------------------

    async def _upsert_products_batch(
        self, products: list[dict], store_uuid: str
    ) -> int:
        """Upsert di una pagina di prodotti in ~5 round-trip DB."""
        by_bc: dict[str, dict] = {}
        for raw in products:
            n = self._normalize(raw)
            if n:
                by_bc[n["barcode"]] = n
        if not by_bc:
            return 0
        if self.dry_run:
            for n in list(by_bc.values())[:5]:
                log.info("[DRY] %-50s  €%.2f", n["name"][:50], n["price"])
            return len(by_bc)

        barcodes = list(by_bc.keys())
        async with self.conn.transaction():
            # risolve i barcode esistenti (prodotti veri + alias del dedup)
            id_by_bc, direct_bcs = await resolve_existing(self.conn, barcodes)

            new_bcs = [bc for bc in barcodes if bc not in id_by_bc]
            if new_bcs:
                rows = await self.conn.fetch(
                    """INSERT INTO products (barcode, name, brand, image_url, source)
                       SELECT * FROM unnest($1::text[], $2::text[], $3::text[],
                                            $4::text[], $5::text[])
                       RETURNING id, barcode""",
                    new_bcs,
                    [by_bc[b]["name"] for b in new_bcs],
                    [by_bc[b]["brand"] for b in new_bcs],
                    [by_bc[b]["image_url"] for b in new_bcs],
                    ["esselunga_web"] * len(new_bcs),
                )
                for r in rows:
                    id_by_bc[r["barcode"]] = r["id"]

            # UPDATE solo per i barcode che sono prodotti veri: per quelli
            # risolti via alias si scrive solo il prezzo, senza toccare i
            # dati del prodotto superstite.
            upd = [bc for bc in barcodes if bc in direct_bcs]
            if upd:
                await self.conn.execute(
                    """UPDATE products AS p SET
                           name = v.name,
                           brand = COALESCE(v.brand, p.brand),
                           image_url = COALESCE(p.image_url, v.image_url),
                           updated_at = NOW()
                       FROM unnest($1::uuid[], $2::text[], $3::text[], $4::text[])
                            AS v(id, name, brand, image_url)
                       WHERE p.id = v.id""",
                    [id_by_bc[b] for b in upd],
                    [by_bc[b]["name"] for b in upd],
                    [by_bc[b]["brand"] for b in upd],
                    [by_bc[b]["image_url"] for b in upd],
                )

            all_ids = [id_by_bc[b] for b in barcodes]
            await self.conn.execute(
                "UPDATE prices SET is_current = FALSE "
                "WHERE store_id = $1 AND product_id = ANY($2::uuid[])",
                store_uuid, all_ids,
            )
            await self.conn.execute(
                """INSERT INTO prices
                       (product_id, store_id, price, original_price, promo_label,
                        price_per_unit, in_stock, is_current, source,
                        product_url, scraped_at)
                   SELECT v.id, $2, v.price, v.orig, v.promo, v.ppu, v.instock,
                          TRUE, 'esselunga_web', v.url, $8
                   FROM unnest($1::uuid[], $3::numeric[], $4::numeric[], $5::text[],
                               $6::numeric[], $7::boolean[], $9::text[])
                        AS v(id, price, orig, promo, ppu, instock, url)""",
                all_ids,
                store_uuid,
                [by_bc[b]["price"] for b in barcodes],
                [by_bc[b]["original_price"] for b in barcodes],
                [by_bc[b]["promo_label"] for b in barcodes],
                [by_bc[b]["price_per_unit"] for b in barcodes],
                [by_bc[b]["in_stock"] for b in barcodes],
                datetime.now(timezone.utc),
                [by_bc[b]["product_url"] for b in barcodes],
            )
        return len(barcodes)

    # ------------------------------------------------------------------
    # Catalog scrape
    # ------------------------------------------------------------------

    async def _scrape_catalog(
        self, product_sets: list[int], store_uuid: str
    ) -> int:
        """Pagina l'intero catalogo via productset POST. Ritorna prezzi scritti."""
        start = 0
        total: int | None = None
        count = 0
        errors = 0
        seen: set[str] = set()

        while total is None or start < total:
            data = await self._post(
                PRODUCTSET_URL,
                {"productSetIds": product_sets, "length": PAGE_SIZE, "start": start},
            )
            if not data:
                errors += 1
                if errors >= 3:
                    log.error("3 errori consecutivi a start=%d, interrompo", start)
                    break
                await asyncio.sleep(5)
                continue
            errors = 0

            if total is None:
                total = int(data.get("rowCount") or 0)
                log.info("Catalogo Esselunga: %d prodotti totali", total)
                if total == 0:
                    break

            entities = data.get("entities") or []
            if not entities:
                break

            # entità duplicate tra productSet diversi: dedup sul code
            fresh = []
            for e in entities:
                code = str(e.get("code") or e.get("id") or "")
                if code and code not in seen:
                    seen.add(code)
                    fresh.append(e)

            try:
                count += await self._upsert_products_batch(fresh, store_uuid)
            except Exception as exc:  # noqa: BLE001
                log.warning("Errore batch a start=%d: %s", start, exc)

            start += len(entities)
            if start % 1000 < PAGE_SIZE:
                log.info("  …%d/%d prodotti", min(start, total), total)
            if len(entities) < PAGE_SIZE:
                break

        return count

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> int:
        log.info("=== Esselunga spider avviato (dry_run=%s) ===", self.dry_run)

        store_map = await self.match_stores(await self.discover_stores())
        if not store_map:
            log.error("Nessuno store disponibile — interruzione")
            return 0
        store_uuid = store_map["online"]
        log.info("Store UUID: %s", store_uuid)

        product_sets = await self._get_product_sets()
        if not product_sets:
            log.error("Nessun productSet trovato — API route non disponibile")
            return 0

        total = await self._scrape_catalog(product_sets, store_uuid)
        log.info("=== Fine. Prezzi totali scritti: %d ===", total)
        return total
