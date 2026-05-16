"""
Normalizzazione barcode EAN/GTIN e fingerprint prodotto per il dedup.

Problema risolto: ogni scraper salva il prodotto con un barcode diverso
(Carrefour usa l'EAN reale, Conad `conad-{cod}`, Esselunga `esselunga-{cod}`).
Lo stesso prodotto fisico finisce in più righe `products` separate.

Questo modulo fornisce:
  - canonical_ean()      → forma canonica GTIN-13 (match esatto e sicuro)
  - normalize_quantity() → quantità normalizzata ("1 l" → "1000ml")
  - name_token_jaccard() → similarità fuzzy tra nomi prodotto
"""
from __future__ import annotations

import re
import unicodedata

_SYNTHETIC_PREFIXES = ("conad-", "esselunga-", "eurospin-", "eurospin_")


def _strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def _gtin_check_ok(code: str) -> bool:
    """Valida il check digit di un codice GTIN (EAN-8/12/13/14)."""
    if not code.isdigit() or len(code) < 8:
        return False
    digits = [int(c) for c in code]
    check = digits[-1]
    body = digits[:-1]
    total = 0
    for i, d in enumerate(reversed(body)):
        total += d * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


def canonical_ean(raw) -> str | None:
    """
    Normalizza un barcode al formato GTIN-13 canonico (13 cifre), oppure
    None se non è un EAN reale (codice sintetico, peso variabile, check
    digit errato, lunghezza non valida).
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    if any(s.startswith(p) for p in _SYNTHETIC_PREFIXES):
        return None
    digits = re.sub(r"\D", "", s)
    if len(digits) == 12:                       # UPC-A → pad a 13
        digits = "0" + digits
    elif len(digits) == 14 and digits[0] == "0":  # GTIN-14 con leading zero
        digits = digits[1:]
    if len(digits) not in (8, 13):
        return None
    if not _gtin_check_ok(digits):
        return None
    canon = digits.zfill(13)
    # I codici a peso variabile / interni (EAN-13 con prefisso 2) non sono
    # GTIN reali e non identificano un prodotto a livello nazionale.
    if canon.startswith("2"):
        return None
    return canon


# ── Fingerprint per il matching fuzzy ────────────────────────────────────────

_UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(litri|litro|kilogrammi|grammi|lt|ml|cl|kg|gr|l|g)\b"
)
_STOPWORDS = {
    "di", "da", "il", "la", "le", "lo", "un", "una", "con", "al", "alla",
    "allo", "the", "in", "per", "del", "della", "dei", "delle", "conf",
    "and", "the",
}


def normalize_quantity(text: str) -> str | None:
    """
    Estrae la quantità dal testo e la normalizza a un'unità di base.
    Es.: "1 l" → "1000ml", "500 g" → "500g", "1,5 L" → "1500ml".
    Ritorna None se non trova una quantità.
    """
    if not text:
        return None
    t = _strip_accents(text.lower())
    found: str | None = None
    for m in _UNIT_RE.finditer(t):
        try:
            val = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        unit = m.group(2)
        if unit in ("l", "lt", "litro", "litri"):
            found = f"{int(round(val * 1000))}ml"
        elif unit == "cl":
            found = f"{int(round(val * 10))}ml"
        elif unit == "ml":
            found = f"{int(round(val))}ml"
        elif unit in ("kg", "kilogrammi"):
            found = f"{int(round(val * 1000))}g"
        elif unit in ("g", "gr", "grammi"):
            found = f"{int(round(val))}g"
    return found


def _tokens(text: str) -> set[str]:
    """Token significativi di un nome prodotto (senza quantità né stopword)."""
    t = _strip_accents((text or "").lower())
    t = _UNIT_RE.sub(" ", t)
    words = re.findall(r"[a-z0-9]+", t)
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def norm_brand(brand) -> str:
    """Brand normalizzato (minuscolo, senza accenti) per il blocking."""
    return _strip_accents((brand or "").lower()).strip()


def name_token_jaccard(name_a: str, name_b: str) -> float:
    """
    Similarità di Jaccard tra i token significativi di due nomi prodotto.
    1.0 = stessi token, 0.0 = nessun token in comune.
    """
    ta, tb = _tokens(name_a), _tokens(name_b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0
