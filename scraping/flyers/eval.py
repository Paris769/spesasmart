"""
POC volantini discount — misura del tasso di errore dell'estrazione.

Confronta extracted.json con una ground truth etichettata A MANO guardando il
volantino originale. Metriche:

  precision prezzi   — % di item accoppiati col prezzo esatto (±0,01 €)
  recall item        — % di item della ground truth trovati dall'estrazione
  errori pezzatura   — % di item accoppiati con unit_size diversa (quantità
                       normalizzata, es. "1 L" == "1000 ml")

Soglie kill-switch (sul tasso di ERRORE prezzi = 1 - precision):
  < 5%    → GO           (la strada volantini è percorribile)
  5-10%   → RIVEDERE     (migliorare prompt/risoluzione e rimisurare)
  > 10%   → STOP         (abbandonare la strada volantini)

Uso:
    # 1. genera il template da compilare a mano
    python -m scraping.flyers.eval <extracted.json> --init

    # 2. compila ground_truth.json guardando il volantino, poi:
    python -m scraping.flyers.eval <extracted.json> <ground_truth.json>
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from ..ean import name_token_jaccard, normalize_quantity, same_product_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("flyers.eval")

PRICE_TOLERANCE = 0.01
PAIR_THRESHOLD = 0.5  # somiglianza minima nome per accoppiare GT ↔ estratto

TEMPLATE = {
    "_istruzioni": (
        "Compila questo file A MANO guardando il volantino originale (PDF in "
        "out/<chain>/<data>/). Per OGNI prodotto del volantino (o di un "
        "sottoinsieme di pagine, indicato in 'pages_labeled') aggiungi un "
        "oggetto in 'items' con: name (come stampato), brand (null se "
        "assente), price (prezzo promo, punto decimale), original_price "
        "(barrato, null se assente), unit_size (pezzatura come stampata, es. "
        "'500 g', '6x1,5 L', 'al kg'), page_ref (numero pagina). Non copiare "
        "i valori da extracted.json: il senso è misurarne gli errori. "
        "Etichettare 2-3 pagine (~30-50 item) basta per una prima misura."
    ),
    "chain": "",
    "flyer_id": "",
    "labeled_by": "",
    "pages_labeled": [],
    "items": [
        {
            "name": "ESEMPIO Latte intero UHT",
            "brand": "EsempioBrand",
            "price": 0.99,
            "original_price": 1.19,
            "unit_size": "1 L",
            "page_ref": 1,
        }
    ],
}


def _similarity(a: dict, b: dict) -> float:
    """Somiglianza GT↔estratto: nome (+ brand come tie-breaker)."""
    name_a = f"{a.get('brand') or ''} {a.get('name') or ''}".strip()
    name_b = f"{b.get('brand') or ''} {b.get('name') or ''}".strip()
    if same_product_name(name_a, name_b):
        return 1.0
    return name_token_jaccard(name_a, name_b)


def pair_items(gt_items: list[dict], ex_items: list[dict]) -> list[tuple[dict, dict, float]]:
    """Accoppiamento greedy per somiglianza decrescente (stessa pagina prima)."""
    candidates: list[tuple[float, int, int]] = []
    for i, g in enumerate(gt_items):
        for j, e in enumerate(ex_items):
            sim = _similarity(g, e)
            if sim >= PAIR_THRESHOLD:
                same_page = (g.get("page_ref") is not None
                             and g.get("page_ref") == e.get("page_ref"))
                candidates.append((sim + (0.05 if same_page else 0), i, j))
    candidates.sort(reverse=True)
    used_g: set[int] = set()
    used_e: set[int] = set()
    pairs: list[tuple[dict, dict, float]] = []
    for sim, i, j in candidates:
        if i in used_g or j in used_e:
            continue
        used_g.add(i)
        used_e.add(j)
        pairs.append((gt_items[i], ex_items[j], min(sim, 1.0)))
    return pairs


_MULTI_RE = re.compile(r"(\d+)\s*x\s*\d", re.I)


def norm_size(raw) -> str | None:
    """Pezzatura normalizzata, preservando il multipack (es. "2x250 g" ≠ "250 g")."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    qty = normalize_quantity(s)
    m = _MULTI_RE.search(s)
    if qty and m:
        return f"{m.group(1)}x{qty}"
    return qty or s or None


def evaluate(gt: dict, extracted: dict) -> dict:
    gt_items = gt.get("items", [])
    ex_items = extracted.get("items", [])
    pages = set(gt.get("pages_labeled") or [])
    if pages:
        ex_items = [e for e in ex_items if e.get("page_ref") in pages]

    pairs = pair_items(gt_items, ex_items)

    price_ok = price_bad = size_ok = size_bad = 0
    errors: list[str] = []
    for g, e, _sim in pairs:
        gp, ep = g.get("price"), e.get("price")
        if gp is not None and ep is not None and abs(float(gp) - float(ep)) <= PRICE_TOLERANCE:
            price_ok += 1
        else:
            price_bad += 1
            errors.append(
                f"PREZZO  «{g.get('name')}»: vero {gp} ≠ estratto {ep}"
            )
        gs, es = norm_size(g.get("unit_size")), norm_size(e.get("unit_size"))
        if gs == es or (gs and es and gs == es):
            size_ok += 1
        else:
            size_bad += 1
            errors.append(
                f"PEZZATURA «{g.get('name')}»: vera «{g.get('unit_size')}» ≠ "
                f"estratta «{e.get('unit_size')}»"
            )

    n_pairs = len(pairs)
    recall = n_pairs / len(gt_items) if gt_items else 0.0
    price_precision = price_ok / n_pairs if n_pairs else 0.0
    price_error_rate = 1 - price_precision
    size_error_rate = size_bad / n_pairs if n_pairs else 0.0
    missed = [g.get("name") for g in gt_items
              if not any(p[0] is g for p in pairs)]

    if price_error_rate < 0.05:
        verdict = "GO — tasso errore prezzi sotto il 5%: strada percorribile"
    elif price_error_rate <= 0.10:
        verdict = ("RIVEDERE — tasso errore prezzi 5-10%: migliorare "
                   "prompt/risoluzione e rimisurare")
    else:
        verdict = "STOP — tasso errore prezzi oltre il 10%: abbandonare la strada volantini"

    return {
        "gt_items": len(gt_items),
        "extracted_items": len(ex_items),
        "paired": n_pairs,
        "item_recall": round(recall, 4),
        "price_precision": round(price_precision, 4),
        "price_error_rate": round(price_error_rate, 4),
        "size_error_rate": round(size_error_rate, 4),
        "missed_items": missed,
        "errors": errors,
        "verdict": verdict,
    }


def main(args: argparse.Namespace) -> None:
    extracted_path = Path(args.extracted)
    if not extracted_path.exists():
        sys.exit(f"File non trovato: {extracted_path}")
    extracted = json.loads(extracted_path.read_text(encoding="utf-8"))

    if args.init:
        tpl = dict(TEMPLATE)
        tpl["chain"] = extracted.get("chain") or ""
        tpl["flyer_id"] = extracted.get("flyer_id") or ""
        out = extracted_path.parent / "ground_truth.template.json"
        out.write_text(json.dumps(tpl, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        log.info("Template creato: %s — compilarlo a mano e rinominarlo "
                 "ground_truth.json", out)
        return

    gt_path = Path(args.ground_truth) if args.ground_truth else \
        extracted_path.parent / "ground_truth.json"
    if not gt_path.exists():
        sys.exit(f"Ground truth non trovata: {gt_path} — genera il template "
                 f"con --init e compilalo a mano")
    gt = json.loads(gt_path.read_text(encoding="utf-8"))

    result = evaluate(gt, extracted)

    log.info("=== EVAL %s (%s) ===", extracted.get("chain"), extracted.get("source"))
    log.info("Item ground truth : %d", result["gt_items"])
    log.info("Item estratti     : %d", result["extracted_items"])
    log.info("Accoppiati        : %d", result["paired"])
    log.info("Recall item       : %.1f%%", result["item_recall"] * 100)
    log.info("Precision prezzi  : %.1f%%  (errore %.1f%%)",
             result["price_precision"] * 100, result["price_error_rate"] * 100)
    log.info("Errori pezzatura  : %.1f%%", result["size_error_rate"] * 100)
    for e in result["errors"]:
        log.info("  ✗ %s", e)
    for m in result["missed_items"]:
        log.info("  ∅ non estratto: %s", m)
    log.info("VERDETTO: %s", result["verdict"])

    out = extracted_path.parent / "eval_report.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    log.info("Report: %s", out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Misura il tasso di errore dell'estrazione")
    parser.add_argument("extracted", help="Percorso di extracted.json")
    parser.add_argument("ground_truth", nargs="?", default=None,
                        help="Percorso ground_truth.json (default: accanto a extracted)")
    parser.add_argument("--init", action="store_true",
                        help="Genera ground_truth.template.json e termina")
    main(parser.parse_args())
