"""
Volantini discount — scrive le promo matchate in prices (produzione).

Default: DRY-RUN — stampa cosa scriverebbe, non tocca il DB.
Con --apply esegue l'upsert IDEMPOTENTE: ri-eseguire lo stesso volantino non
duplica righe (se per (product, store) esiste già una riga flyer corrente con
stesso prezzo e stessa scadenza, la riga viene saltata).

Garanzie di scrittura:
  * source='flyer' su ogni riga scritta; le righe con source diverso NON
    vengono mai toccate (né UPDATE né disattivazione);
  * le righe quarantined non vengono MAI modificate;
  * prezzi accettati solo in [MIN_PRICE, MAX_PRICE] (0.10–500);
  * promo scadute (promo_to < oggi) o non ancora iniziate (promo_from > oggi)
    non vengono scritte;
  * a ogni --apply le righe flyer della catena con promo_expires passato
    vengono marcate is_current=FALSE (sweep scadenze); le righe flyer SENZA
    scadenza (es. MD) vengono disattivate quando il prodotto esce dal
    volantino corrente (supersede);
  * price_per_unit: usa il valore dichiarato dal feed, altrimenti lo calcola
    dalla pezzatura (qty_norm del feed MD o unit_size parsabile).

Gate di applicazione (enforced con --apply): match rate >= --min-match-rate
(default 0.90) sul file matched.json, e almeno una riga valida da scrivere.

NUOVI PRODOTTI (--with-new-products): gli item unmatched con confidence alta
e prezzo valido vengono inseriti in products con barcode sintetico stabile
'<chain>_<code>' (stesso pattern di eurospin_spider/md_spider; se il feed
riporta un EAN reale valido si usa quello), brand/nome normalizzati e
unit/unit_quantity derivati dalla pezzatura. Il flag è separato da --apply:
senza --apply mostra solo l'anteprima.

store_id: lo store "nazionale" virtuale della catena (external_id
'<slug>-online' / '<slug>-offerte' / 'md-pv-1'); in mancanza (es. Eurospin)
il prezzo nazionale è replicato su un campione di store fisici attivi
(--store-limit, stesso approccio e ordinamento di eurospin_spider).

Uso:
    python -m scraping.flyers.load out/eurospin/<data>/matched.json            # dry-run
    python -m scraping.flyers.load out/eurospin/<data>/matched.json --apply
    python -m scraping.flyers.load out/eurospin/<data>/matched.json --apply --with-new-products
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import asyncpg

from ..aliases import resolve_existing
from ..ean import canonical_ean, normalize_quantity
from .match import db_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("flyers.load")

SOURCE = "flyer"
MIN_MATCH_SCORE = 0.55       # sotto questa soglia non scriviamo il prezzo
MIN_CONFIDENCE = 0.8         # confidenza minima dell'estrazione (vision)
NEW_PRODUCT_MIN_CONFIDENCE = 0.7
MIN_PRICE = 0.10             # come MIN_VALID_PRICE del backend
MAX_PRICE = 500.0

# Pezzatura "N x Q unità" o "Q unità" (es. "6x1,5 L", "400 g", "4 x 100 g")
_PACK_RE = re.compile(
    r"^(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*"
    r"(kg|gr|g|grammi|lt|litri|litro|l|cl|ml)\.?$",
    re.I,
)
_PER_KG_RE = re.compile(r"^al\s+kg\b", re.I)
_PER_L_RE = re.compile(r"^al\s+(litro|lt|l)\b", re.I)

_TO_G = {"kg": 1000.0, "g": 1.0, "gr": 1.0, "grammi": 1.0}
_TO_ML = {"l": 1000.0, "lt": 1000.0, "litro": 1000.0, "litri": 1000.0,
          "cl": 10.0, "ml": 1.0}


def parse_pack(item: dict) -> tuple[str, float] | None:
    """Pezzatura totale dell'item → (unità base 'kg'|'l', quantità).
    Usa qty_norm (feed MD, multipack già moltiplicati), poi unit_size."""
    qn = item.get("qty_norm")
    if qn:
        m = re.fullmatch(r"(\d+)(g|ml)", str(qn))
        if m:
            val = float(m.group(1))
            return ("kg", val / 1000.0) if m.group(2) == "g" else ("l", val / 1000.0)
    us = (item.get("unit_size") or "").strip()
    if not us:
        return None
    if _PER_KG_RE.match(us):
        return ("kg", 1.0)
    if _PER_L_RE.match(us):
        return ("l", 1.0)
    m = _PACK_RE.match(us)
    if not m:
        return None
    mult = int(m.group(1)) if m.group(1) else 1
    try:
        qty = float(m.group(2).replace(",", "."))
    except ValueError:
        return None
    unit = m.group(3).lower()
    if unit in _TO_G:
        return ("kg", mult * qty * _TO_G[unit] / 1000.0)
    if unit in _TO_ML:
        return ("l", mult * qty * _TO_ML[unit] / 1000.0)
    return None


def price_per_unit(item: dict) -> float | None:
    """€/kg o €/l: il valore dichiarato dal feed vince; altrimenti calcolato
    dalla pezzatura quando parsabile senza ambiguità."""
    claimed = item.get("price_per_unit_claimed")
    if claimed:
        try:
            return round(float(claimed), 4)
        except (TypeError, ValueError):
            pass
    pack = parse_pack(item)
    if not pack or not item.get("price"):
        return None
    _, qty = pack
    if qty <= 0:
        return None
    return round(float(item["price"]) / qty, 4)


def promo_label(item: dict, chain: str) -> str:
    price, orig = item.get("price"), item.get("original_price")
    try:
        price = float(price) if price else None
        orig = float(orig) if orig else None
    except (TypeError, ValueError):
        price, orig = None, None
    if orig and price and orig > price:
        pct = round((orig - price) / orig * 100)
        label = f"Volantino {chain.capitalize()} -{pct}%"
    else:
        label = f"Volantino {chain.capitalize()}"
    if item.get("requires_card"):
        label += " (con carta)"
    return label


def parse_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


async def national_store_ids(
    conn: asyncpg.Connection, chain: str, store_limit: int
) -> list[str]:
    """Store 'nazionale' virtuale della catena (online/offerte/md-pv-1);
    in mancanza, un campione di store fisici attivi (volantino nazionale)."""
    row = await conn.fetchrow(
        """
        SELECT s.id, s.external_id
        FROM stores s
        JOIN chains c ON c.id = s.chain_id
        WHERE c.slug = $1
          AND s.is_active = TRUE
          AND (s.external_id LIKE '%-online'
               OR s.external_id LIKE '%-offerte'
               OR s.external_id = 'md-pv-1')
        ORDER BY (s.external_id LIKE '%-online') DESC, s.external_id
        LIMIT 1
        """,
        chain,
    )
    if row:
        log.info("Store nazionale %s: %s (%s)", chain, row["external_id"], row["id"])
        return [str(row["id"])]

    rows = await conn.fetch(
        """
        SELECT s.id
        FROM stores s
        JOIN chains c ON c.id = s.chain_id
        WHERE c.slug = $1 AND s.is_active = TRUE
        ORDER BY
          CASE WHEN s.province = ANY($2::text[]) THEN 0 ELSE 1 END,
          s.province NULLS LAST, s.city NULLS LAST, s.external_id
        LIMIT $3
        """,
        chain,
        ["MI", "RM", "TO", "NA", "BO", "FI", "PA", "GE", "VR", "PD"],
        store_limit,
    )
    if rows:
        log.info("Nessuno store virtuale per %s: uso %d store fisici attivi "
                 "(prezzo nazionale)", chain, len(rows))
    return [str(r["id"]) for r in rows]


def _valid_price(item: dict) -> float | None:
    try:
        p = float(item.get("price"))
    except (TypeError, ValueError):
        return None
    return p if MIN_PRICE <= p <= MAX_PRICE else None


def build_price_rows(matches: list[dict], chain: str,
                     today: date) -> tuple[list[dict], dict[str, int]]:
    """Filtra i match e costruisce le righe prezzo (una per product_id)."""
    skipped = {"unmatched": 0, "low_score": 0, "low_confidence": 0,
               "no_price": 0, "price_out_of_range": 0,
               "expired": 0, "not_yet_valid": 0}
    by_pid: dict[str, dict] = {}
    for m in matches:
        item = m["flyer_item"]
        if not m.get("product_id"):
            skipped["unmatched"] += 1
            continue
        if (m.get("match_score") or 0) < MIN_MATCH_SCORE:
            skipped["low_score"] += 1
            continue
        if (item.get("confidence") or 0) < MIN_CONFIDENCE:
            skipped["low_confidence"] += 1
            continue
        if not item.get("price"):
            skipped["no_price"] += 1
            continue
        price = _valid_price(item)
        if price is None:
            skipped["price_out_of_range"] += 1
            continue
        promo_from = parse_date(item.get("promo_from"))
        promo_to = parse_date(item.get("promo_to"))
        if promo_to and promo_to < today:
            skipped["expired"] += 1
            continue
        if promo_from and promo_from > today:
            skipped["not_yet_valid"] += 1
            continue
        try:
            orig = float(item["original_price"]) if item.get("original_price") else None
        except (TypeError, ValueError):
            orig = None
        if orig is not None and orig <= price:
            orig = None
        row = {
            "product_id": str(m["product_id"]),
            "price": round(price, 2),
            "original_price": orig,
            "promo_label": promo_label(item, chain)[:200],
            "promo_expires": promo_to,
            "price_per_unit": price_per_unit(item),
            "name": item.get("name"),
            "score": m.get("match_score") or 0,
        }
        prev = by_pid.get(row["product_id"])
        # stesso prodotto in più item: tieni score più alto, poi prezzo più basso
        if (prev is None or (row["score"], -row["price"])
                > (prev["score"], -prev["price"])):
            by_pid[row["product_id"]] = row
    return list(by_pid.values()), skipped


# ── Nuovi prodotti (unmatched ad alta confidenza) ────────────────────────────

def _unit_fields(item: dict) -> tuple[str | None, float | None]:
    """products.unit / unit_quantity dalla pezzatura (es. 'kg', 0.4 per 400 g)."""
    pack = parse_pack(item)
    if pack:
        return pack[0], round(pack[1], 3)
    # fallback: quantità nel nome (es. "Acqua 1,5 l")
    qn = normalize_quantity(item.get("name") or "")
    if qn:
        m = re.fullmatch(r"(\d+)(g|ml)", qn)
        if m:
            val = float(m.group(1)) / 1000.0
            return ("kg", round(val, 3)) if m.group(2) == "g" else ("l", round(val, 3))
    return None, None


def build_new_product_candidates(matches: list[dict], chain: str,
                                 today: date) -> list[dict]:
    """Candidati products dagli unmatched: stessi controlli delle righe prezzo
    (prezzo in range, date valide) + confidence + codice sorgente stabile."""
    out: dict[str, dict] = {}
    for m in matches:
        item = m["flyer_item"]
        if m.get("product_id"):
            continue
        if (item.get("confidence") or 0) < NEW_PRODUCT_MIN_CONFIDENCE:
            continue
        code = item.get("source_code")
        name = re.sub(r"\s+", " ", str(item.get("name") or "")).strip()
        if not code or not name:
            continue
        if _valid_price(item) is None:
            continue
        promo_to = parse_date(item.get("promo_to"))
        promo_from = parse_date(item.get("promo_from"))
        if (promo_to and promo_to < today) or (promo_from and promo_from > today):
            continue
        # Barcode sintetico stabile '<chain>_<code>' (pattern degli spider);
        # se il feed riporta un EAN-13 reale valido, usa direttamente quello.
        # Solo 13 cifre: i codici interni a 8 cifre possono superare per caso
        # il check digit GTIN-8 e diventerebbero EAN fasulli.
        barcode = f"{chain}_{code}"
        if len(re.sub(r"\D", "", str(code))) == 13:
            barcode = canonical_ean(code) or barcode
        if len(barcode) > 50:  # limite colonna products.barcode
            continue
        brand = (str(item.get("brand") or "").strip() or None)
        unit, unit_qty = _unit_fields(item)
        out.setdefault(barcode, {
            "barcode": barcode,
            "name": name[:500],
            "brand": brand[:200] if brand else None,
            "unit": unit,
            "unit_quantity": unit_qty,
            "item": item,
            "match": m,
        })
    return list(out.values())


async def insert_new_products(
    conn: asyncpg.Connection, candidates: list[dict]
) -> tuple[int, dict[str, str]]:
    """Inserisce i candidati non ancora presenti (idempotente via barcode e
    alias). Ritorna (inseriti, {barcode: product_id per TUTTI i candidati})."""
    barcodes = [c["barcode"] for c in candidates]
    id_by_bc, _ = await resolve_existing(conn, barcodes)
    new = [c for c in candidates if c["barcode"] not in id_by_bc]
    if new:
        rows = await conn.fetch(
            """
            INSERT INTO products (barcode, name, brand, unit, unit_quantity,
                                  source)
            SELECT * FROM unnest($1::text[], $2::text[], $3::text[],
                                 $4::text[], $5::numeric[], $6::text[])
            RETURNING id, barcode
            """,
            [c["barcode"] for c in new],
            [c["name"] for c in new],
            [c["brand"] for c in new],
            [c["unit"] for c in new],
            [c["unit_quantity"] for c in new],
            [SOURCE] * len(new),
        )
        for r in rows:
            id_by_bc[r["barcode"]] = r["id"]
    return len(new), {bc: str(pid) for bc, pid in id_by_bc.items()}


# ── Upsert idempotente ───────────────────────────────────────────────────────

async def sweep_expired(conn: asyncpg.Connection, chain: str,
                        apply: bool) -> int:
    """Righe flyer della catena con promo_expires passato → is_current=FALSE.
    Non tocca mai righe con source diverso da 'flyer' né righe quarantined."""
    cond = """
        source = 'flyer'
        AND is_current = TRUE
        AND NOT quarantined
        AND promo_expires IS NOT NULL
        AND promo_expires < CURRENT_DATE
        AND store_id IN (
            SELECT s.id FROM stores s
            JOIN chains c ON c.id = s.chain_id
            WHERE c.slug = $1
        )
    """
    if not apply:
        n = await conn.fetchval(
            f"SELECT count(*) FROM prices WHERE {cond}", chain
        )
        return int(n or 0)
    tag = await conn.execute(
        f"UPDATE prices SET is_current = FALSE WHERE {cond}", chain
    )
    return int(tag.split()[-1])


async def upsert_prices(
    conn: asyncpg.Connection, rows: list[dict], store_ids: list[str],
    apply: bool, supersede_missing: bool,
) -> dict[str, int]:
    """Upsert idempotente: per (product, store) con riga flyer corrente
    identica (stesso prezzo e scadenza) non scrive nulla; altrimenti
    disattiva SOLO le righe flyer correnti non quarantined e inserisce."""
    stats = {"written": 0, "unchanged": 0, "deactivated": 0, "superseded": 0}
    product_ids = [r["product_id"] for r in rows]

    existing = await conn.fetch(
        """
        SELECT product_id::text, store_id::text, price, promo_expires, quarantined
        FROM prices
        WHERE store_id = ANY($1::uuid[])
          AND product_id = ANY($2::uuid[])
          AND is_current = TRUE
          AND source = 'flyer'
        """,
        store_ids, product_ids,
    )
    current = {(r["product_id"], r["store_id"]): r for r in existing}

    to_insert: list[tuple] = []
    dirty_pairs: set[tuple[str, str]] = set()
    now = datetime.now(timezone.utc)
    for r in rows:
        for sid in store_ids:
            cur = current.get((r["product_id"], sid))
            if cur is not None and float(cur["price"]) == float(r["price"]) \
                    and cur["promo_expires"] == r["promo_expires"]:
                stats["unchanged"] += 1
                continue
            if cur is not None and not cur["quarantined"]:
                dirty_pairs.add((r["product_id"], sid))
            to_insert.append((
                r["product_id"], sid, r["price"], r["original_price"],
                r["promo_label"], r["promo_expires"], r["price_per_unit"],
                SOURCE, now,
            ))

    stats["written"] = len(to_insert)
    if not apply:
        return stats

    async with conn.transaction():
        if dirty_pairs:
            tag = await conn.execute(
                """
                UPDATE prices SET is_current = FALSE
                FROM unnest($1::uuid[], $2::uuid[]) AS v(pid, sid)
                WHERE prices.product_id = v.pid
                  AND prices.store_id = v.sid
                  AND prices.is_current = TRUE
                  AND prices.source = 'flyer'
                  AND NOT prices.quarantined
                """,
                [p for p, _ in dirty_pairs], [s for _, s in dirty_pairs],
            )
            stats["deactivated"] = int(tag.split()[-1])
        if to_insert:
            await conn.execute(
                """
                INSERT INTO prices
                    (product_id, store_id, price, original_price, promo_label,
                     promo_expires, price_per_unit, in_stock, is_current,
                     source, scraped_at)
                SELECT v.pid, v.sid, v.price, v.orig, v.label, v.expires,
                       v.ppu, TRUE, TRUE, v.src, v.at
                FROM unnest($1::uuid[], $2::uuid[], $3::numeric[],
                            $4::numeric[], $5::text[], $6::date[],
                            $7::numeric[], $8::text[], $9::timestamptz[])
                     AS v(pid, sid, price, orig, label, expires, ppu, src, at)
                """,
                [t[0] for t in to_insert], [t[1] for t in to_insert],
                [t[2] for t in to_insert], [t[3] for t in to_insert],
                [t[4] for t in to_insert], [t[5] for t in to_insert],
                [t[6] for t in to_insert], [t[7] for t in to_insert],
                [t[8] for t in to_insert],
            )
        if supersede_missing and product_ids:
            # Righe flyer SENZA scadenza (es. MD) di prodotti usciti dal
            # volantino corrente: senza promo_expires non le spegnerebbe
            # mai nessuno.
            tag = await conn.execute(
                """
                UPDATE prices SET is_current = FALSE
                WHERE store_id = ANY($1::uuid[])
                  AND is_current = TRUE
                  AND source = 'flyer'
                  AND NOT quarantined
                  AND promo_expires IS NULL
                  AND product_id != ALL($2::uuid[])
                """,
                store_ids, product_ids,
            )
            stats["superseded"] = int(tag.split()[-1])
    return stats


# ── Main ─────────────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace) -> None:
    matched_path = Path(args.matched)
    if not matched_path.exists():
        sys.exit(f"File non trovato: {matched_path}")
    data = json.loads(matched_path.read_text(encoding="utf-8"))
    chain = args.chain or data.get("chain")
    if chain == "mock":
        chain = "lidl"  # la fixture mock simula un volantino Lidl
    matches = data.get("matches", [])
    today = date.today()

    # ── Gate: match rate ──
    n_matched = sum(1 for m in matches if m.get("product_id"))
    match_rate = n_matched / len(matches) if matches else 0.0
    log.info("Match rate %s: %d/%d = %.1f%% (gate: >= %.0f%%)",
             chain, n_matched, len(matches), 100 * match_rate,
             100 * args.min_match_rate)
    if match_rate < args.min_match_rate:
        msg = (f"GATE FALLITO: match rate {100 * match_rate:.1f}% < "
               f"{100 * args.min_match_rate:.0f}% — nessuna scrittura")
        if args.apply:
            sys.exit(msg)
        log.warning(msg)

    rows, skipped = build_price_rows(matches, chain, today)
    log.info("Righe prezzo valide: %d — scartate: %s", len(rows), skipped)

    candidates = (build_new_product_candidates(matches, chain, today)
                  if args.with_new_products else [])
    if args.with_new_products:
        log.info("Candidati nuovi prodotti: %d", len(candidates))

    if not rows and not candidates:
        log.warning("Niente da scrivere per %s", chain)
        return

    url = db_url()
    if not url:
        sys.exit("Nessuna DATABASE_URL e nessun .db_url.local")
    conn = await asyncpg.connect(url)
    try:
        store_ids = await national_store_ids(conn, chain, args.store_limit)
        if not store_ids:
            sys.exit(f"Nessuno store per la catena '{chain}' nel DB")

        n_expired = await sweep_expired(conn, chain, apply=args.apply)

        products_inserted = 0
        if candidates:
            if args.apply:
                products_inserted, id_by_bc = await insert_new_products(
                    conn, candidates
                )
                for c in candidates:
                    pid = id_by_bc.get(c["barcode"])
                    if not pid:
                        continue
                    c["match"]["product_id"] = pid
                    c["match"]["match_score"] = 1.0  # codice sorgente = barcode
                    c["match"]["match_method"] = "new_product"
                # le righe prezzo dei nuovi prodotti entrano nell'upsert
                extra_rows, _ = build_price_rows(
                    [c["match"] for c in candidates], chain, today
                )
                known = {r["product_id"] for r in rows}
                rows += [r for r in extra_rows if r["product_id"] not in known]
            else:
                for c in candidates[:20]:
                    log.info("[DRY][NEW] %-46s %-20s → barcode %s",
                             c["name"][:46], (c["brand"] or "")[:20], c["barcode"])
                if len(candidates) > 20:
                    log.info("[DRY][NEW] … e altri %d candidati", len(candidates) - 20)

        stats = await upsert_prices(
            conn, rows, store_ids,
            apply=args.apply,
            supersede_missing=not args.no_supersede,
        )

        mode = "APPLY" if args.apply else "DRY-RUN"
        if not args.apply:
            for r in rows[:20]:
                log.info(
                    "[DRY] %-50s €%.2f%s%s → product %s",
                    (r["name"] or "")[:50], r["price"],
                    f" (orig €{r['original_price']:.2f})" if r["original_price"] else "",
                    f" fino al {r['promo_expires']:%Y-%m-%d}" if r["promo_expires"] else "",
                    r["product_id"],
                )
            if len(rows) > 20:
                log.info("[DRY] … e altre %d righe", len(rows) - 20)

        log.info(
            "=== %s %s: prezzi scritti=%d invariati=%d disattivati=%d "
            "superseded=%d scadenze spente=%d nuovi prodotti=%d "
            "(%d prodotti × %d store) ===",
            mode, chain, stats["written"], stats["unchanged"],
            stats["deactivated"], stats["superseded"], n_expired,
            products_inserted, len(rows), len(store_ids),
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Carica le promo volantino in prices")
    parser.add_argument("matched", help="Percorso di matched.json")
    parser.add_argument("--chain", default=None,
                        help="Slug catena (default: dal file matched.json)")
    parser.add_argument("--store-limit", type=int, default=250,
                        help="Max store fisici se manca lo store virtuale "
                             "nazionale (default: 250, come eurospin_spider)")
    parser.add_argument("--apply", action="store_true",
                        help="Scrive davvero nel DB (default: dry-run)")
    parser.add_argument("--with-new-products", action="store_true",
                        help="Inserisce i prodotti unmatched ad alta confidenza "
                             "con barcode sintetico stabile")
    parser.add_argument("--min-match-rate", type=float, default=0.90,
                        help="Gate: match rate minimo per applicare (default 0.90)")
    parser.add_argument("--no-supersede", action="store_true",
                        help="Non disattivare le righe flyer senza scadenza "
                             "di prodotti usciti dal volantino")
    asyncio.run(run(parser.parse_args()))
