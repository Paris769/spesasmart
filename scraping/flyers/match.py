"""
POC volantini discount — item estratti → product_id del catalogo.

Strategia a cascata (riusa la logica di scraping/ean.py, la stessa del dedup):
  a. barcode  — match esatto su EAN canonico (raro: i volantini quasi mai
                riportano l'EAN) + tabella product_aliases; per i feed
                strutturati (eurospin, md) il codice interno del volantino
                coincide col barcode sintetico '<chain>_<code>' già usato
                dagli spider → match esatto quasi totale
  b. brand+nome — blocking per (brand normalizzato, quantità normalizzata),
                poi `same_product_name` (token identici → score 1.0) o
                Jaccard dei token >= soglia
  c. nome     — per item senza brand (es. ortofrutta): blocking per quantità
                + Jaccard con soglia più alta (0.75)
  sotto soglia → unmatched

Output (accanto a extracted.json):
  matched.json       — [{flyer_item, product_id|null, match_score, match_method}]
  new_products.json  — unmatched con confidence estrazione alta: candidati
                       NUOVI prodotti (barcode NULL) — NON inseriti nel DB

DB: legge DATABASE_URL, con fallback su .db_url.local nella root del repo.
Solo SELECT: questo modulo non scrive mai.

Uso:
    python -m scraping.flyers.match scraping/flyers/out/mock/<data>/extracted.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

import asyncpg

from ..ean import (
    canonical_ean,
    name_token_jaccard,
    norm_brand,
    normalize_quantity,
    same_product_name,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("flyers.match")

REPO_ROOT = Path(__file__).resolve().parents[2]

FUZZY_THRESHOLD = 0.55       # con brand+quantità uguali
NOBRAND_THRESHOLD = 0.75     # senza brand: serve molta più somiglianza
NEW_PRODUCT_MIN_CONFIDENCE = 0.7


def db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        local = REPO_ROOT / ".db_url.local"
        if local.exists():
            url = local.read_text(encoding="utf-8").strip()
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def load_catalog(conn: asyncpg.Connection) -> tuple[list[dict], dict[str, str]]:
    products = [
        dict(r) for r in await conn.fetch(
            "SELECT id, barcode, name, brand FROM products"
        )
    ]
    aliases = {
        r["alias_barcode"]: str(r["product_id"])
        for r in await conn.fetch(
            "SELECT alias_barcode, product_id FROM product_aliases"
        )
    }
    log.info("Catalogo: %d prodotti, %d alias barcode", len(products), len(aliases))
    return products, aliases


class Matcher:
    def __init__(self, products: list[dict], aliases: dict[str, str],
                 chain: str | None = None):
        self.aliases = aliases
        self.chain = chain
        self.by_ean: dict[str, str] = {}
        self.by_raw_barcode: dict[str, str] = {}
        self.by_brand_qty: dict[tuple[str, str], list[dict]] = {}
        self.by_qty: dict[str, list[dict]] = {}
        for p in products:
            pid = str(p["id"])
            if p["barcode"]:
                self.by_raw_barcode.setdefault(str(p["barcode"]), pid)
            ean = canonical_ean(p["barcode"])
            if ean and ean not in self.by_ean:
                self.by_ean[ean] = pid
            qty = normalize_quantity(p["name"] or "")
            brand = norm_brand(p["brand"])
            if qty:
                self.by_qty.setdefault(qty, []).append(p)
                if brand:
                    self.by_brand_qty.setdefault((brand, qty), []).append(p)

    def match(self, item: dict) -> tuple[str | None, float, str | None]:
        """Ritorna (product_id, score, method)."""
        # a. barcode esatto (se il volantino lo riporta) + alias + barcode
        #    sintetico '<chain>_<code>' degli spider esistenti
        raw_bc = item.get("barcode") or item.get("source_code")
        if raw_bc:
            ean = canonical_ean(raw_bc)
            if ean and ean in self.by_ean:
                return self.by_ean[ean], 1.0, "ean"
            candidates = [str(raw_bc)]
            if self.chain:
                candidates += [f"{self.chain}_{raw_bc}", f"{self.chain}-{raw_bc}"]
            for bc in candidates:
                if bc in self.by_raw_barcode:
                    return self.by_raw_barcode[bc], 1.0, "synthetic_code"
                if bc in self.aliases:
                    return self.aliases[bc], 1.0, "alias"

        name = item.get("name") or ""
        # la pezzatura spesso sta in unit_size, non nel nome: prova entrambi
        qty = (normalize_quantity(name)
               or normalize_quantity(item.get("unit_size") or ""))
        brand = norm_brand(item.get("brand"))
        full_name = f"{name} {item.get('unit_size') or ''}".strip()

        # b. brand + quantità + nome
        if brand and qty:
            best: tuple[float, str] | None = None
            for p in self.by_brand_qty.get((brand, qty), []):
                if same_product_name(name, p["name"] or ""):
                    return str(p["id"]), 1.0, "brand_name_exact"
                score = max(
                    name_token_jaccard(name, p["name"] or ""),
                    name_token_jaccard(full_name, p["name"] or ""),
                )
                if score >= FUZZY_THRESHOLD and (best is None or score > best[0]):
                    best = (score, str(p["id"]))
            if best:
                return best[1], best[0], "brand_name_fuzzy"

        # c. solo nome (item senza brand), quantità uguale obbligatoria
        if qty and not brand:
            best = None
            for p in self.by_qty.get(qty, []):
                score = max(
                    name_token_jaccard(name, p["name"] or ""),
                    name_token_jaccard(full_name, p["name"] or ""),
                )
                if score >= NOBRAND_THRESHOLD and (best is None or score > best[0]):
                    best = (score, str(p["id"]))
            if best:
                return best[1], best[0], "name_fuzzy"

        return None, 0.0, None


async def run(args: argparse.Namespace) -> None:
    extracted_path = Path(args.extracted)
    if not extracted_path.exists():
        sys.exit(f"File non trovato: {extracted_path}")
    extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
    items = extracted.get("items", [])
    log.info("Item da matchare: %d (%s, source=%s)",
             len(items), extracted.get("chain"), extracted.get("source"))

    url = db_url()
    if not url:
        sys.exit("Nessuna DATABASE_URL e nessun .db_url.local — impossibile "
                 "leggere il catalogo")
    conn = await asyncpg.connect(url)
    try:
        products, aliases = await load_catalog(conn)
    finally:
        await conn.close()

    matcher = Matcher(products, aliases, chain=extracted.get("chain"))
    matched: list[dict] = []
    new_products: list[dict] = []
    counts: dict[str, int] = {}
    for item in items:
        pid, score, method = matcher.match(item)
        matched.append({
            "flyer_item": item,
            "product_id": pid,
            "match_score": round(score, 3),
            "match_method": method,
        })
        counts[method or "unmatched"] = counts.get(method or "unmatched", 0) + 1
        if pid is None and (item.get("confidence") or 0) >= NEW_PRODUCT_MIN_CONFIDENCE:
            # Barcode sintetico stabile '<chain>_<code>' (stesso pattern degli
            # spider); se il feed riporta un EAN-13 reale valido, usa quello
            # (solo 13 cifre: gli 8 cifre interni possono superare per caso
            # il check digit GTIN-8).
            code = item.get("source_code")
            chain_slug = extracted.get("chain")
            proposed = None
            if code:
                proposed = f"{chain_slug}_{code}"
                if len(re.sub(r"\D", "", str(code))) == 13:
                    proposed = canonical_ean(code) or proposed
            new_products.append({
                "barcode": proposed,
                "source_code": code,
                "name": item.get("name"),
                "brand": item.get("brand"),
                "unit_size": item.get("unit_size"),
                "price": item.get("price"),
                "source": f"flyer_{chain_slug}",
                "extraction_confidence": item.get("confidence"),
            })

    out_dir = extracted_path.parent
    (out_dir / "matched.json").write_text(
        json.dumps({"chain": extracted.get("chain"),
                    "source": extracted.get("source"),
                    "matches": matched},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "new_products.json").write_text(
        json.dumps(new_products, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    n_matched = sum(1 for m in matched if m["product_id"])
    log.info("=== Risultato match: %d/%d matchati (%.0f%%) ===",
             n_matched, len(matched),
             100 * n_matched / len(matched) if matched else 0)
    for method, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        log.info("  %-18s %d", method, n)
    log.info("Candidati nuovi prodotti (NON inseriti): %d → new_products.json",
             len(new_products))
    log.info("Output: %s", out_dir / "matched.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Matcha item volantino col catalogo")
    parser.add_argument("extracted", help="Percorso di extracted.json")
    asyncio.run(run(parser.parse_args()))
