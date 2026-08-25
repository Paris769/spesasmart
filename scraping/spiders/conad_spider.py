"""
Conad price scraper — spesaonline.conad.it

Flusso:
  1. GET /search/_jcr_content/root/search.loader.html?query=*&bassiFissi=true&page=N
     Risposta: HTML con prodotti embeddati come data-product (JSON con entity encoding)
  2. Estrae prodotti da data-product attributes
  3. Salva solo prodotti con basePrice > 0 (programma "Bassi e Fissi" — prezzi
     garantiti uguali in tutti i punti vendita Conad)

NB sul filtro: senza un punto vendita in sessione Conad espone il prezzo SOLO per
i "Bassi e Fissi" (verificato: basePrice>0 coincide sempre con bassiFissi=true).
Senza filtro il sito dichiara 5058 prodotti su 127 pagine di cui ~4300 senza
prezzo, tutte scaricate per niente; col filtro sono 759 su 19 pagine.
ATTENZIONE: il parametro di ricerca e' `query`, non `q` — con `q` il server
ignora tutto e restituisce sempre il catalogo anonimo completo.
  4. Upsert DB con negozio virtuale "Conad Online" (coords: sede Bologna)

Nota: prezzi store-specifici richiedono autenticazione; i "bassiFissi" sono
prezzi nazionali fissi applicabili come proxy per la comparazione.
"""
import asyncio
import html
import json
import logging
import re
from datetime import datetime, timezone

import asyncpg
import httpx

from ..aliases import preserve_flyer_promos
from ..ean import canonical_ean

log = logging.getLogger("conad")

# EAN reale dal JSON-LD della scheda prodotto ("gtin"/"gtin13"). La lista dei
# risultati espone solo il codice interno Conad: senza l'EAN i prodotti non si
# uniscono a quelli delle altre catene e restano fuori dal confronto prezzi.
_EAN_RE = re.compile(r'"gtin\d*"\s*:\s*"(\d+)"')

BASE_URL = "https://spesaonline.conad.it"
SEARCH_URL = f"{BASE_URL}/search/_jcr_content/root/search.loader.html"
PAGE_SIZE = 40
RATE = 2.5         # secondi tra le richieste (Conad rate-limita a ~1.5s → 429)
RETRY_429_SLEEP = 30  # backoff lungo quando Conad risponde 429
MAX_ATTEMPTS = 4

# ── Punti vendita ────────────────────────────────────────────────────────────
# API pubblica del sito corporate (nessuna autenticazione): una sola POST dal
# centro d'Italia con raggio 800 km restituisce l'intera rete (~3200 negozi).
# Il costo e' quasi tutto fisso per richiesta, quindi conviene una chiamata sola
# invece di una griglia di punti.
POS_URL = "https://www.conad.it/api/corporate/it-it.retrievePointOfService.json"
POS_BODY = {"latitudine": "42.0000", "longitudine": "12.5000", "raggioRicerca": "800"}
POS_TIMEOUT = 300           # misurato ~180 s
POS_MIN_EXPECTED = 2000     # sotto questa soglia la risposta e' troncata: non scrivere
# Insegne non alimentari: un PetStore o una pompa di benzina non e' "il super
# sotto casa" e falserebbe il confronto della spesa.
INSEGNE_ESCLUSE = {
    "PET STORE CONAD", "CONAD SELF 24h", "PARAFARMACIA CONAD", "BENESSITY CONAD",
}
ITALIA_BBOX = (35.0, 47.5, 6.0, 19.0)  # lat_min, lat_max, lng_min, lng_max

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
    "Referer": "https://spesaonline.conad.it/search",
}

_CONAD_LNG = 11.3426  # Bologna (sede Conad)
_CONAD_LAT = 44.4939

# Regex per estrarre tutti i data-product dall'HTML
_PRODUCT_RE = re.compile(r'data-product="([^"]+)"')
_TOTAL_RE = re.compile(r"(\d+)\s+risultati")


class ConadSpider:
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
        # codice interno Conad -> id prodotto gia' risolto in passato.
        # Precaricata da product_aliases: e' cio' che rende sostenibile il
        # recupero dell'EAN, altrimenti servirebbe una richiesta per prodotto
        # a ogni esecuzione (~30 minuti buttati ogni notte).
        self._alias_cache: dict[str, object] = {}
        self._ean_scaricati = 0

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._t_last
        if elapsed < RATE:
            await asyncio.sleep(RATE - elapsed)
        self._t_last = loop.time()

    async def _get_page(self, page: int) -> str | None:
        await self._throttle()
        params = {"query": "*", "bassiFissi": "true", "page": page}
        for attempt in range(MAX_ATTEMPTS):
            try:
                r = await self.client.get(
                    SEARCH_URL, params=params, headers=HEADERS, timeout=30
                )
                if r.status_code == 200:
                    return r.text
                log.warning("HTTP %s pagina %d tentativo %d", r.status_code, page, attempt + 1)
                if r.status_code in (403, 404):
                    return None
                if r.status_code == 429:
                    # Rate-limit: backoff lungo (rispetta Retry-After se presente)
                    retry_after = r.headers.get("Retry-After")
                    delay = RETRY_429_SLEEP
                    if retry_after and retry_after.isdigit():
                        delay = max(delay, int(retry_after))
                    log.info("429 — attesa %ds prima di ritentare pagina %d", delay, page)
                    await asyncio.sleep(delay)
                    continue
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore: %s", attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    # ------------------------------------------------------------------
    # Store management
    # ------------------------------------------------------------------

    async def match_stores(self) -> str | None:
        """Trova o crea il negozio virtuale 'Conad Online' nel DB."""
        row = await self.conn.fetchrow(
            """
            SELECT s.id
            FROM stores s
            JOIN chains c ON s.chain_id = c.id
            WHERE c.slug = 'conad' AND s.external_id = 'conad-online'
            """
        )
        if row:
            log.info("Conad Online store trovato: %s", row["id"])
            return str(row["id"])

        if self.dry_run:
            log.info("[DRY] Creerebbe Conad Online store")
            return "00000000-0000-0000-0000-000000000000"

        chain_id = await self.conn.fetchval(
            "SELECT id FROM chains WHERE slug = 'conad'"
        )
        if not chain_id:
            log.error("Chain 'conad' non trovata nel DB — aggiungila in init.sql")
            return None

        new_id = await self.conn.fetchval(
            """
            INSERT INTO stores
                (chain_id, name, address, city, province, postal_code,
                 coordinates, external_id, has_delivery, has_click_collect, is_active)
            VALUES
                ($1, 'Conad Online', 'E-commerce', 'Bologna', 'BO', '40127',
                 ST_SetSRID(ST_MakePoint($2, $3), 4326),
                 'conad-online', TRUE, TRUE, TRUE)
            RETURNING id
            """,
            chain_id, _CONAD_LNG, _CONAD_LAT,
        )
        log.info("Creato Conad Online store: %s", new_id)
        return str(new_id)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_products(page_html: str) -> list[dict]:
        """Estrae e parsa i data-product dall'HTML della pagina."""
        products = []
        for m in _PRODUCT_RE.finditer(page_html):
            raw = html.unescape(m.group(1))
            try:
                obj = json.loads(raw)
                products.append(obj)
            except json.JSONDecodeError:
                pass
        return products

    @staticmethod
    def _get_total(page_html: str) -> int | None:
        m = _TOTAL_RE.search(page_html)
        return int(m.group(1)) if m else None

    @staticmethod
    def _parse_unit_price(product: dict) -> float | None:
        """
        Calcola il prezzo al kg/litro dai campi netQuantity e netQuantityUm.
        Normalizza a per-kg (solidi) o per-litro (liquidi).
        """
        base = product.get("basePrice") or 0.0
        qty = product.get("netQuantity") or 0.0
        um = (product.get("netQuantityUm") or "").upper()
        if not base or not qty:
            return None
        try:
            qty = float(qty)
            base = float(base)
            if qty <= 0:
                return None
            if um == "KG":
                return round(base / qty, 4)
            if um == "G":
                return round(base / qty * 1000, 4)
            if um == "LT":
                return round(base / qty, 4)
            if um == "ML":
                return round(base / qty * 1000, 4)
            if um == "CL":
                return round(base / qty * 100, 4)
        except (ValueError, ZeroDivisionError):
            pass
        return None

    # ------------------------------------------------------------------
    # DB upsert
    # ------------------------------------------------------------------

    async def _upsert_product_price(
        self, p: dict, store_uuid: str, page_ids: list | None = None
    ) -> bool:
        code = str(p.get("code") or "").strip()
        if not code:
            return False

        base_price = float(p.get("basePrice") or 0.0)
        if base_price <= 0:
            return False  # salta prodotti senza prezzo

        name = (p.get("nome") or "").strip()
        if not name:
            return False

        brand = (p.get("marchio") or "").strip() or None
        alias = f"conad-{code}"

        # Costruisci URL immagine (le list page hanno già l'URL completo)
        img = p.get("defaultImgSrc") or ""
        if img and img.startswith("/"):
            img = BASE_URL + img
        image_url = img or None

        price_per_unit = self._parse_unit_price(p)
        promo_label = "Bassi e Fissi" if p.get("bassiFissi") else None
        # Link alla scheda prodotto: senza, il pulsante "Acquista" porta alla
        # home della catena e l'auto-carrello non puo' aggiungere l'articolo.
        # Lo slug e' irrilevante: Conad reindirizza in base al solo codice.
        product_url = f"{BASE_URL}/p/x--{code}"

        if self.dry_run:
            log.info(
                "[DRY] %-55s  €%.2f%s",
                name[:55],
                base_price,
                f"  ({price_per_unit:.2f}/kg)" if price_per_unit else "",
            )
            return True

        # 1) Codice gia' risolto in un'esecuzione precedente? Nessuna richiesta.
        prod_id = self._alias_cache.get(alias)

        if prod_id is None:
            # 2) Prima volta che vediamo questo codice: leggiamo l'EAN reale
            #    dalla scheda prodotto. E' il passaggio che rende i prodotti
            #    Conad confrontabili con le altre catene (il codice interno
            #    non aggancia nulla). Si paga una volta sola per prodotto.
            ean = await self._fetch_ean(code)
            barcode = ean or alias

            prod_id = await self.conn.fetchval(
                "SELECT id FROM products WHERE barcode = $1 LIMIT 1", barcode
            )
            if prod_id is None:
                prod_id = await self.conn.fetchval(
                    """
                    INSERT INTO products (barcode, name, brand, image_url, source)
                    VALUES ($1, $2, $3, $4, 'conad_web')
                    RETURNING id
                    """,
                    barcode, name, brand, image_url,
                )
            # 3) Memorizza la corrispondenza: dalla prossima volta niente rete.
            await self.conn.execute(
                "INSERT INTO product_aliases (alias_barcode, product_id) "
                "VALUES ($1, $2) ON CONFLICT (alias_barcode) DO NOTHING",
                alias, prod_id,
            )
            self._alias_cache[alias] = prod_id
        else:
            await self.conn.execute(
                """
                UPDATE products
                SET name      = $2,
                    brand     = COALESCE($3, brand),
                    image_url = COALESCE($4, image_url),
                    updated_at = NOW()
                WHERE id = $1
                """,
                prod_id, name, brand, image_url,
            )

        await self.conn.execute(
            "UPDATE prices SET is_current = FALSE WHERE product_id = $1 AND store_id = $2",
            prod_id, store_uuid,
        )
        await self.conn.execute(
            """
            INSERT INTO prices
                (product_id, store_id, price, original_price, promo_label,
                 price_per_unit, in_stock, is_current, source, scraped_at,
                 product_url)
            VALUES ($1, $2, $3, NULL, $4, $5, TRUE, TRUE, 'conad_web', $6, $7)
            """,
            prod_id, store_uuid,
            base_price, promo_label,
            price_per_unit,
            datetime.now(timezone.utc),
            product_url,
        )
        # Accumulato per l'ereditarietà promo volantino (una chiamata a
        # preserve_flyer_promos per pagina, non per prodotto).
        if page_ids is not None:
            page_ids.append(prod_id)
        return True



    # ------------------------------------------------------------------
    # Codice a barre reale
    # ------------------------------------------------------------------

    async def _carica_alias(self) -> None:
        """Precarica i codici Conad gia' risolti in passato (una sola query)."""
        rows = await self.conn.fetch(
            "SELECT alias_barcode, product_id FROM product_aliases "
            "WHERE alias_barcode LIKE 'conad-%'"
        )
        self._alias_cache = {r["alias_barcode"]: r["product_id"] for r in rows}
        log.info("Codici Conad gia' risolti in memoria: %d", len(self._alias_cache))

    async def _get_detail(self, code: str) -> str | None:
        """Scarica la scheda prodotto (lo slug e' irrilevante: Conad
        reindirizza alla pagina canonica dal solo codice)."""
        await self._throttle()
        url = f"{BASE_URL}/p/x--{code}"
        for attempt in range(MAX_ATTEMPTS):
            try:
                r = await self.client.get(
                    url, headers=HEADERS, timeout=30, follow_redirects=True
                )
                if r.status_code == 200:
                    return r.text
                if r.status_code in (403, 404):
                    return None
                if r.status_code == 429:
                    await asyncio.sleep(RETRY_429_SLEEP)
                    continue
            except httpx.RequestError as exc:
                log.warning("Scheda %s tentativo %d: %s", code, attempt + 1, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    async def _fetch_ean(self, code: str) -> str | None:
        """
        Legge l'EAN reale dalla scheda prodotto (JSON-LD `gtin`).
        Best-effort: se non e' recuperabile si torna None e il chiamante
        ripiega sul codice interno.
        """
        page = await self._get_detail(code)
        if not page:
            return None
        m = _EAN_RE.search(page)
        if not m:
            return None
        self._ean_scaricati += 1
        return canonical_ean(m.group(1))

    # ------------------------------------------------------------------
    # Punti vendita
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_store_entry(pv: dict) -> dict | None:
        """Normalizza un punto vendita dell'API corporate (None se da scartare)."""
        # anacanId e' la chiave STABILE pubblicata da Conad (compare anche nell'URL
        # della scheda negozio). Va trattato come STRINGA: inizia sempre per zero e
        # un cast a intero ne cambierebbe il valore, creando un doppione per ogni
        # negozio al run successivo — e' esattamente cosi' che nacquero i 1.112
        # doppioni Eurospin di luglio.
        anacan = str(pv.get("anacanId") or "").strip()
        lat, lng = pv.get("latitudine"), pv.get("longitudine")
        if not anacan or lat is None or lng is None:
            return None
        if (pv.get("descrizioneInsegna") or "").strip() in INSEGNE_ESCLUSE:
            return None
        lat_min, lat_max, lng_min, lng_max = ITALIA_BBOX
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            return None
        if not (lat_min <= lat <= lat_max and lng_min <= lng <= lng_max):
            return None

        external_id = f"conad-{anacan}"
        # Cintura di sicurezza: i suffissi -online/-offerte identificano i negozi
        # virtuali su cui poggiano i filtri del serving. Un punto vendita reale
        # non deve mai finire in quell'insieme.
        if external_id.endswith(("-online", "-offerte")):
            return None

        citta = (pv.get("nomeComune") or "").title()
        return {
            "external_id": external_id,
            "name": f"{(pv.get('pdvTitle') or 'Conad').strip()} {citta}".strip(),
            "address": (pv.get("indirizzo") or "").title() or None,
            "city": citta or None,
            "province": (pv.get("codiceProvincia") or "").strip() or None,
            "postal_code": (pv.get("cap") or "").strip() or None,
            "lat": lat,
            "lng": lng,
            "has_delivery": pv.get("spesaACasa") == "S",
            "has_click_collect": pv.get("ordinaRitira") == "S",
        }

    async def discover_stores(self) -> int:
        """Scarica la rete Conad dall'API corporate e la inserisce nel database."""
        log.info("Scarico i punti vendita da %s", POS_URL)
        try:
            r = await self.client.post(
                POS_URL, json=POS_BODY, headers={**HEADERS, "Content-Type": "application/json"},
                timeout=POS_TIMEOUT,
            )
        except httpx.RequestError as exc:
            log.error("Richiesta punti vendita fallita: %s", exc)
            return 0
        if r.status_code != 200:
            log.error("HTTP %s dall'API punti vendita", r.status_code)
            return 0

        raw = (r.json() or {}).get("data") or []
        log.info("Punti vendita ricevuti: %d", len(raw))
        if len(raw) < POS_MIN_EXPECTED:
            # Risposta troncata o parziale: meglio non scrivere nulla che
            # disattivare per errore meta' rete.
            log.error(
                "Risposta incompleta (%d < %d attesi): non modifico il database",
                len(raw), POS_MIN_EXPECTED,
            )
            return 0

        negozi = [n for n in (self._parse_store_entry(pv) for pv in raw) if n]
        log.info("Punti vendita alimentari validi: %d", len(negozi))
        if self.dry_run:
            for n in negozi[:5]:
                log.info("[DRY] %s — %s (%s)", n["external_id"], n["name"], n["province"])
            return len(negozi)

        chain_id = await self.conn.fetchval("SELECT id FROM chains WHERE slug = 'conad'")
        if not chain_id:
            log.error("Catena conad assente nel database")
            return 0

        # Inserimento in blocco dentro una transazione: 2.962 execute() separati
        # sul pooler facevano cadere la connessione a meta' import.
        righe = [
            (chain_id, n["name"], n["address"], n["city"], n["province"], n["postal_code"],
             n["lng"], n["lat"], n["external_id"], n["has_delivery"], n["has_click_collect"])
            for n in negozi
        ]
        async with self.conn.transaction():
            await self.conn.executemany(
                """
                INSERT INTO stores (chain_id, name, address, city, province, postal_code,
                                    coordinates, external_id, has_delivery, has_click_collect,
                                    is_active)
                VALUES ($1,$2,$3,$4,$5,$6, ST_SetSRID(ST_MakePoint($7,$8),4326), $9,$10,$11,TRUE)
                ON CONFLICT (chain_id, external_id) WHERE external_id IS NOT NULL
                DO UPDATE SET
                    name = EXCLUDED.name,
                    address = EXCLUDED.address,
                    city = EXCLUDED.city,
                    province = EXCLUDED.province,
                    postal_code = EXCLUDED.postal_code,
                    coordinates = EXCLUDED.coordinates,
                    has_delivery = EXCLUDED.has_delivery,
                    has_click_collect = EXCLUDED.has_click_collect,
                    is_active = TRUE
                """,
                righe,
            )
        scritti = len(righe)
        log.info("Punti vendita Conad scritti: %d", scritti)
        return scritti

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self) -> int:
        log.info("=== Conad spider avviato (dry_run=%s) ===", self.dry_run)

        if not self.dry_run:
            await self._carica_alias()

        store_uuid = await self.match_stores()
        if not store_uuid:
            log.error("Nessuno store disponibile — interruzione")
            return 0
        log.info("Store UUID: %s", store_uuid)

        # Pagina 1 per ottenere il totale
        first_page = await self._get_page(1)
        if not first_page:
            log.error("Impossibile ottenere la prima pagina")
            return 0

        total = self._get_total(first_page) or 0
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        log.info("Totale prodotti: %d — pagine: %d", total, total_pages)

        grand_total = 0

        for page_num in range(1, total_pages + 1):
            if page_num == 1:
                page_html = first_page
            else:
                page_html = await self._get_page(page_num)
                if not page_html:
                    log.warning("Pagina %d non ottenuta, salto", page_num)
                    continue

            products = self._parse_products(page_html)
            priced = 0
            page_ids: list = []
            for product in products:
                try:
                    if await self._upsert_product_price(
                        product, store_uuid, page_ids
                    ):
                        priced += 1
                except Exception as exc:
                    log.warning("Errore prodotto %s: %s", product.get("code"), exc)

            # Eredita i metadati promo dei volantini validi appena spenti
            if page_ids:
                try:
                    await preserve_flyer_promos(self.conn, [store_uuid], page_ids)
                except Exception as exc:
                    log.warning("Errore ereditarietà promo pagina %d: %s", page_num, exc)

            grand_total += priced
            if page_num % 10 == 0 or page_num == total_pages:
                log.info(
                    "Pagina %d/%d — prodotti con prezzo questa pagina: %d — totale: %d",
                    page_num, total_pages, priced, grand_total,
                )

        log.info(
            "=== Fine. Prezzi scritti: %d — codici a barre recuperati stavolta: %d "
            "(i gia' noti non costano richieste) ===",
            grand_total, self._ean_scaricati,
        )
        return grand_total
