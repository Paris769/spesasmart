"""
POC volantini discount — pagina volantino → JSON strutturato.

Tre modalità:
  vision      PDF scaricato da fetch.py → immagini pagina (pypdfium2) →
              Claude vision (structured outputs, JSON garantito dallo schema).
              Richiede credenziali Anthropic (env ANTHROPIC_API_KEY oppure
              profilo `ant auth login`).
  structured  feed già strutturati (eurospin, md scaricati da fetch.py) →
              normalizzazione allo stesso schema, senza chiamate API.
  --mock      usa la fixture fixtures/mock_extracted.json (15 item realistici)
              per testare il resto della pipeline senza chiave API.

Schema item estratto:
  {name, brand, price, original_price, unit_size, price_per_unit_claimed,
   promo_from, promo_to, requires_card, confidence, page_ref}

Output: out/<chain>/<data>/extracted.json

Uso:
    python -m scraping.flyers.extract --mock
    python -m scraping.flyers.extract --chain eurospin           # structured
    python -m scraping.flyers.extract --chain lidl               # vision (PDF)
    python -m scraping.flyers.extract --chain lidl --max-pages 2 # test economico
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("flyers.extract")

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "out"
FIXTURE = BASE_DIR / "fixtures" / "mock_extracted.json"

# Modello di default per l'estrazione vision. Override con --model
# (es. claude-haiku-4-5 per run economici una volta validata la qualità).
DEFAULT_MODEL = "claude-opus-4-8"
MAX_TOKENS = 16000
TARGET_LONG_EDGE = 2000  # px — bilanciamento qualità OCR / token immagine

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "brand": {"type": ["string", "null"]},
                    "price": {"type": ["number", "null"]},
                    "original_price": {"type": ["number", "null"]},
                    "unit_size": {"type": ["string", "null"]},
                    "price_per_unit_claimed": {"type": ["number", "null"]},
                    "promo_from": {"type": ["string", "null"]},
                    "promo_to": {"type": ["string", "null"]},
                    "requires_card": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "name", "brand", "price", "original_price", "unit_size",
                    "price_per_unit_claimed", "promo_from", "promo_to",
                    "requires_card", "confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

PROMPT = """\
Questa è una pagina di un volantino promozionale di un supermercato discount \
italiano ({chain}). Estrai OGNI prodotto in vendita visibile nella pagina.

Per ciascun prodotto compila:
- name: nome del prodotto come stampato (senza il brand se separabile)
- brand: marca, se stampata; null se assente (es. ortofrutta sfusa)
- price: prezzo promozionale in euro (il prezzo grande in evidenza)
- original_price: prezzo barrato/precedente se presente, altrimenti null
- unit_size: pezzatura come stampata, es. "500 g", "6x1,5 L", "al kg"
- price_per_unit_claimed: prezzo al kg/litro DICHIARATO nella pagina, se \
stampato (solo il numero in euro), altrimenti null
- promo_from / promo_to: date di validità in formato YYYY-MM-DD se leggibili \
nella pagina, altrimenti null
- requires_card: true solo se l'offerta richiede esplicitamente la carta \
fedeltà o l'app
- confidence: la tua confidenza 0-1 che TUTTI i campi di questo item siano \
corretti (abbassa se il testo è piccolo, tagliato o ambiguo)

Regole:
- NON inventare prodotti né prezzi: se un prezzo non è leggibile usa null e \
confidence bassa.
- Ignora elementi non-prodotto (loghi, slogan, ricette, regolamenti concorsi).
- I prezzi italiani usano la virgola: "1,99" → 1.99.
"""


# ── Rendering PDF → immagini ─────────────────────────────────────────────────

def render_pdf_pages(pdf_path: Path, max_pages: int | None = None) -> list[bytes]:
    """Converte un PDF in immagini PNG di pagina (richiede pypdfium2)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        sys.exit(
            "pypdfium2 non installato — pip install -r scraping/flyers/requirements.txt"
        )

    pdf = pdfium.PdfDocument(str(pdf_path))
    pages: list[bytes] = []
    n = len(pdf)
    if max_pages:
        n = min(n, max_pages)
    for i in range(n):
        page = pdf[i]
        w, h = page.get_size()
        scale = TARGET_LONG_EDGE / max(w, h)
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        pages.append(buf.getvalue())
        log.info("  pagina %d/%d renderizzata (%dx%d px, %.0f KB)",
                 i + 1, n, pil.width, pil.height, len(pages[-1]) / 1024)
    pdf.close()
    return pages


# ── Estrazione vision ────────────────────────────────────────────────────────

def extract_page(client, model: str, chain: str, png: bytes) -> tuple[list[dict], dict]:
    """Una pagina → lista item + usage. JSON valido garantito dallo schema."""
    with client.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS,
        output_config={
            "format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}
        },
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.standard_b64encode(png).decode(),
                    },
                },
                {"type": "text", "text": PROMPT.format(chain=chain)},
            ],
        }],
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "max_tokens":
        log.warning("  risposta troncata a max_tokens: pagina molto densa, "
                    "gli item parziali potrebbero mancare")
    text = next((b.text for b in response.content if b.type == "text"), "{}")
    try:
        items = json.loads(text).get("items", [])
    except json.JSONDecodeError:
        log.error("  JSON non parsabile (stop_reason=%s)", response.stop_reason)
        items = []
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return items, usage


def run_vision(chain: str, day: str, model: str,
               max_pages: int | None, max_flyers: int) -> dict | None:
    try:
        import anthropic
    except ImportError:
        sys.exit("SDK anthropic non installato — pip install -r scraping/flyers/requirements.txt")

    chain_dir = OUT_DIR / chain / day
    manifest_path = chain_dir / "manifest.json"
    if not manifest_path.exists():
        log.error("Nessun manifest in %s — esegui prima fetch.py", chain_dir)
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pdfs = [i for i in manifest.get("items", [])
            if i.get("type") == "pdf" and i.get("path")][:max_flyers]
    if not pdfs:
        log.error("Nessun PDF nel manifest di %s (fonte structured? usa senza --vision)", chain)
        return None

    try:
        client = anthropic.Anthropic()
        # probe gratuito: valida le credenziali prima di renderizzare i PDF
        client.messages.count_tokens(
            model=model, messages=[{"role": "user", "content": "ping"}]
        )
    except Exception as exc:  # credenziali assenti o non valide
        log.error("Credenziali Anthropic non disponibili: %s", str(exc)[:160])
        log.error("Serve ANTHROPIC_API_KEY nell'env (o `ant auth login`). "
                  "In alternativa: --mock")
        return None

    all_items: list[dict] = []
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    for entry in pdfs:
        pdf_path = chain_dir / entry["path"]
        log.info("PDF %s (%s pagine dichiarate)", pdf_path.name, entry.get("pages", "?"))
        pages = render_pdf_pages(pdf_path, max_pages)
        for page_no, png in enumerate(pages, start=1):
            log.info("  estrazione pagina %d/%d …", page_no, len(pages))
            try:
                items, usage = extract_page(client, model, chain, png)
            except anthropic.AuthenticationError:
                log.error("Chiave API non valida — run reale impossibile")
                return None
            for it in items:
                it["page_ref"] = page_no
                it["flyer"] = entry.get("slug")
            all_items.extend(items)
            total_usage["input_tokens"] += usage["input_tokens"]
            total_usage["output_tokens"] += usage["output_tokens"]
            log.info("  → %d item (in=%d out=%d token)",
                     len(items), usage["input_tokens"], usage["output_tokens"])

    return {
        "chain": chain,
        "flyer_id": pdfs[0].get("slug"),
        "source": "vision",
        "model": model,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "usage": total_usage,
        "items": all_items,
    }


# ── Normalizzazione feed strutturati (eurospin, md) ──────────────────────────

def _es_prop(product: dict, code: str):
    for p in product.get("properties", []):
        if p.get("code") == code:
            vals = p.get("values") or []
            return vals[0] if vals else None
    return None


def _fmt_es_date(raw: str | None) -> str | None:
    # "20260702000000" → "2026-07-02"
    if raw and re.fullmatch(r"\d{14}", str(raw)):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return None


def _clean_ws(text) -> str | None:
    """Collassa spazi/nuove righe: i titoli dei feed contengono \\n interni."""
    if not text:
        return None
    return re.sub(r"\s+", " ", str(text)).strip() or None


def normalize_eurospin(chain_dir: Path) -> list[dict]:
    items: list[dict] = []
    for path in sorted(chain_dir.glob("products_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        promo = data.get("promotion") or {}
        promo_from = _fmt_es_date(promo.get("startDate"))
        promo_to = _fmt_es_date(promo.get("endDate"))
        for prod in data.get("products", []):
            title = _es_prop(prod, "TITLE") or prod.get("description") or ""
            desc = _es_prop(prod, "DESCRIPTION")
            items.append({
                "name": _clean_ws(str(title).title()),
                "brand": (_clean_ws(_es_prop(prod, "MARK")) or None),
                "price": _es_prop(prod, "END-PRICE"),
                "original_price": _es_prop(prod, "INITIAL-PRICE"),
                "unit_size": _clean_ws(desc),
                "price_per_unit_claimed": _es_prop(prod, "END-KG-LT-PRICE"),
                "promo_from": promo_from,
                "promo_to": promo_to,
                "requires_card": False,
                "confidence": 1.0,  # feed ufficiale, non stimato da vision
                "page_ref": _es_prop(prod, "PAGE"),
                "source_code": (prod.get("code") or {}).get("value"),
                "flyer": promo.get("alias"),
            })
    return items


# Sezioni MD che NON sono prodotti da supermercato (pacchetti vacanza,
# crociere): prezzi a 3-4 cifre che inquinerebbero il catalogo.
_MD_SKIP_SECTION_RE = re.compile(r"VIAGGI", re.I)


def _md_date(raw) -> str | None:
    # "2026-08-07T00:00:00" → "2026-08-07"
    if raw and re.match(r"^\d{4}-\d{2}-\d{2}", str(raw)):
        return str(raw)[:10]
    return None


def _md_qty_norm(prod: dict) -> str | None:
    """weight+weight_um del feed MD → quantità totale normalizzata ('1200ml',
    '450g'). Il feed pre-moltiplica i multipack (6x200ml → weight=1200)."""
    try:
        w = float(str(prod.get("weight") or 0).replace(",", "."))
    except ValueError:
        return None
    um = str(prod.get("weight_um") or "").strip().lower()
    if w <= 0 or not um:
        return None
    if um in ("kg",):
        return f"{int(round(w * 1000))}g"
    if um in ("g", "gr"):
        return f"{int(round(w))}g"
    if um in ("l", "lt"):
        return f"{int(round(w * 1000))}ml"
    if um in ("cl",):
        return f"{int(round(w * 10))}ml"
    if um in ("ml",):
        return f"{int(round(w))}ml"
    return None


def normalize_md(chain_dir: Path) -> list[dict]:
    items: list[dict] = []
    for path in sorted(chain_dir.glob("products_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for prod in data.get("products", []):
            section = str(prod.get("section") or "")
            if _MD_SKIP_SECTION_RE.search(section):
                continue  # pacchetti viaggio, non prodotti
            price = prod.get("priceOff") or prod.get("price")
            original = prod.get("price") if prod.get("priceOff") else None
            try:
                price = float(price) if price else None
                original = float(original) if original else None
            except (TypeError, ValueError):
                price, original = None, None
            if original and price and original <= price:
                original = None
            items.append({
                "name": _clean_ws(prod.get("name") or prod.get("title")),
                "brand": _clean_ws(prod.get("brand")),
                "price": price,
                "original_price": original,
                "unit_size": _clean_ws(prod.get("description")),
                "price_per_unit_claimed": None,
                "promo_from": _md_date(prod.get("sellOutStart")),
                "promo_to": _md_date(prod.get("sellOutEnd")),
                "requires_card": False,
                "confidence": 1.0,
                "page_ref": prod.get("page"),
                "source_code": str(prod.get("code") or prod.get("idProduct") or "") or None,
                "qty_norm": _md_qty_norm(prod),
                "flyer": data.get("flyer_code"),
            })
    return items


def run_structured(chain: str, day: str) -> dict | None:
    chain_dir = OUT_DIR / chain / day
    if not (chain_dir / "manifest.json").exists():
        log.error("Nessun manifest in %s — esegui prima fetch.py", chain_dir)
        return None
    items = (normalize_eurospin(chain_dir) if chain == "eurospin"
             else normalize_md(chain_dir))
    items = [i for i in items if i.get("name") and i.get("price")]
    return {
        "chain": chain,
        "flyer_id": items[0].get("flyer") if items else None,
        "source": "structured",
        "model": None,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "usage": None,
        "items": items,
    }


# ── Mock ─────────────────────────────────────────────────────────────────────

def run_mock(day: str) -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["extracted_at"] = datetime.now(timezone.utc).isoformat()
    out = OUT_DIR / "mock" / day
    out.mkdir(parents=True, exist_ok=True)
    # copia anche la GT dimostrativa accanto all'estratto
    shutil.copy(BASE_DIR / "fixtures" / "mock_ground_truth.json",
                out / "ground_truth.json")
    return data


def main(args: argparse.Namespace) -> None:
    day = args.date or date.today().isoformat()

    if args.mock:
        result = run_mock(day)
        chain = "mock"
    elif args.chain in ("eurospin", "md"):
        result = run_structured(args.chain, day)
        chain = args.chain
    elif args.chain in ("lidl", "aldi"):
        result = run_vision(args.chain, day, args.model, args.max_pages,
                            args.max_flyers)
        chain = args.chain
    else:
        sys.exit("Specifica --chain lidl|aldi|eurospin|md oppure --mock")

    if not result:
        sys.exit(1)

    out_dir = OUT_DIR / chain / day
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "extracted.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    log.info("extracted.json: %d item (%s) → %s",
             len(result["items"]), result["source"], out_path)
    if result.get("usage"):
        u = result["usage"]
        cost = u["input_tokens"] / 1e6 * 5.0 + u["output_tokens"] / 1e6 * 25.0
        log.info("Token usati: in=%d out=%d — costo stimato $%.3f (%s)",
                 u["input_tokens"], u["output_tokens"], cost, result["model"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Estrae item strutturati dai volantini")
    parser.add_argument("--chain", choices=["lidl", "aldi", "eurospin", "md"])
    parser.add_argument("--mock", action="store_true",
                        help="Usa la fixture mock (nessuna chiamata API)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: oggi)")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Limita le pagine per PDF (test economici)")
    parser.add_argument("--max-flyers", type=int, default=1,
                        help="Max PDF da processare (default: 1)")
    main(parser.parse_args())
