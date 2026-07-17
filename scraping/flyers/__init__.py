"""
POC volantini discount (Fase 3) — pipeline volantino → promo strutturate.

Moduli:
    fetch    — scarica i volantini correnti (PDF o feed strutturati)
    extract  — pagina volantino → JSON strutturato (Claude vision o feed)
    match    — item estratti → product_id del catalogo (EAN / brand+nome)
    load     — scrive le promo matchate in prices (default: dry-run)
    eval     — misura del tasso di errore vs ground truth etichettata

Vedi README.md in questa directory per fonti, formati e note legali.
"""
