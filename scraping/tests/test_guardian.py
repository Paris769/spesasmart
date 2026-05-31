"""Test delle validazioni delle sonde del Guardian (no rete).

Verificano che un endpoint "vivo" sia distinto da uno "cambiato": è la logica
che ha intercettato il bug Esselunga facet→HTTP 204 e che protegge dai futuri
cambi-API silenziosi.
"""
from scraping.guardian import _esselunga_ok, _cosicomodo_ok, PROBES


def _carrefour_validator():
    return next(p.validator for p in PROBES if p.name == "carrefour")


# ── Esselunga ──────────────────────────────────────────────────────────────

def test_esselunga_ok_con_catalogo_pieno():
    payload = {
        "leftMenuItems": [
            {"menuItemProductSets": [{"pk": {"productSetId": i}} for i in range(6)]}
            for _ in range(20)
        ]
    }
    assert _esselunga_ok(payload) is True


def test_esselunga_giu_se_vuoto():
    # è il caso del 204/struttura svuotata: nessun menu → GIÙ
    assert _esselunga_ok({}) is False
    assert _esselunga_ok({"leftMenuItems": []}) is False
    assert _esselunga_ok(None) is False


def test_esselunga_giu_se_pochi_set():
    # menu presenti ma senza productSet sufficienti → struttura sospetta
    payload = {"leftMenuItems": [{"menuItemProductSets": []} for _ in range(20)]}
    assert _esselunga_ok(payload) is False


# ── CosìComodo ───────────────────────────────────────────────────────────────

def test_cosicomodo_ok_con_prodotti():
    assert _cosicomodo_ok({"products": [{"code": "1"}]}) is True


def test_cosicomodo_ok_con_paginazione():
    assert _cosicomodo_ok({"products": [], "pagination": {"totalResults": 120}}) is True


def test_cosicomodo_giu_se_vuoto():
    assert _cosicomodo_ok({}) is False
    assert _cosicomodo_ok({"products": [], "pagination": {}}) is False
    assert _cosicomodo_ok(None) is False


# ── Carrefour ────────────────────────────────────────────────────────────────

def test_carrefour_ok_con_grid():
    v = _carrefour_validator()
    assert v('<div data-option-cgid="frutta">...</div>') is True


def test_carrefour_giu_senza_grid():
    v = _carrefour_validator()
    assert v("<html>pagina cambiata, nessuna griglia</html>") is False
    assert v(None) is False
