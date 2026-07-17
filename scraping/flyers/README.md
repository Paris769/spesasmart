# POC volantini discount (Fase 3)

Pipeline: **volantino → promo strutturate → match col catalogo → prices**,
con framework di misura del tasso di errore e kill-switch.

I discount (Lidl, Eurospin, MD, Aldi, Penny, In's) non hanno e-commerce
full-catalog: i volantini promozionali sono l'unica fonte ricca, pubblica e
geolocalizzabile dei loro prezzi.

## Moduli

| Modulo | Cosa fa | Comando tipo |
|---|---|---|
| `fetch.py` | scarica i volantini correnti (PDF o feed JSON) in `out/<chain>/<data>/` | `python -m scraping.flyers.fetch --chain lidl` |
| `extract.py` | pagina → JSON strutturato (Claude vision, feed strutturato o `--mock`) | `python -m scraping.flyers.extract --chain eurospin` |
| `match.py` | item estratti → `product_id` (EAN → brand+nome → nome; riusa `scraping/ean.py`) | `python -m scraping.flyers.match out/…/extracted.json` |
| `load.py` | scrive le promo in `prices` (`source='flyer'`) — **default dry-run** | `python -m scraping.flyers.load out/…/matched.json` |
| `eval.py` | precision prezzi / recall / errori pezzatura vs ground truth a mano | `python -m scraping.flyers.eval out/…/extracted.json` |

Dipendenze aggiuntive: `pip install -r scraping/flyers/requirements.txt`
(pypdfium2, Pillow, anthropic — separate dai requirements del progetto).

### Pipeline end-to-end senza chiave API (mock)

```
python -m scraping.flyers.extract --mock
python -m scraping.flyers.match  scraping/flyers/out/mock/<data>/extracted.json
python -m scraping.flyers.load   scraping/flyers/out/mock/<data>/matched.json      # dry-run
python -m scraping.flyers.eval   scraping/flyers/out/mock/<data>/extracted.json
```

## Fonti volantino per catena (verificate il 2026-07-04)

### Lidl — PDF via API pubblica del viewer ✅

- **Overview**: `https://www.lidl.it/c/volantino-lidl/s10018048` (HTML statico,
  contiene i link `/l/it/volantini/<slug>/ar/0` dei volantini correnti).
- **API**: `GET https://endpoints.leaflets.schwarz/v4/flyer?flyer_identifier=<slug>&region_id=0&region_code=0`
  (header `Origin: https://www.lidl.it`). JSON con titolo, validità, elenco
  pagine (dimensioni, keywords, altText) e soprattutto **`pdfUrl` /
  `hiResPdfUrl`**: PDF completo scaricabile (~5-7 MB, 10-30 pagine).
- **Periodicità**: 2-4 volantini/settimana (offerte da lunedì / da giovedì +
  speciali). Formato pagina ~1415×2400.
- **Note**: è l'API ufficiale che il viewer leaflets.schwarz chiama dal
  browser di ogni visitatore; nessuna protezione anti-bot incontrata.

### Aldi — PDF via Publitas ✅

- **Overview**: `https://www.aldi.it/it/volantino-online.html` → link a
  `https://volantino.aldi.it/<slug>` (es. `ALDI_Offerte_da_lunedi_6_Luglio`).
- **Pubblicazione Publitas**: la pagina contiene il link **PDF diretto**
  `https://view.publitas.com/<group>/<pub>/pdfs/<uuid>.pdf?...` e le immagini
  pagina `.../pages/<uuid>-at1600.jpg`.
- **Periodicità**: 2 volantini/settimana (da lunedì e da giovedì).
- **Note**: nessun blocco incontrato con rate 1 req/s.

### Eurospin — feed GIÀ STRUTTURATO via API del viewer ✅ (niente vision)

- **Viewer**: `eurospin.it/volantino/` embedda l'iframe
  `smt-digitalflyer/promotion?code=<code>`; l'app chiama
  `https://digitalflyer.eurospin.it`.
- **Auth**: `POST /oauth/token` (grant `client_credentials`, Basic auth con le
  credenziali del client pubblico embeddate nel bundle JS del viewer —
  le stesse identiche chiamate che il browser di qualunque visitatore esegue).
- **Endpoint**:
  - `GET /api/eurospin/eurospin-italia/stores/eurospin-italia/promotions` —
    promozioni attive (alias, code, date di validità);
  - `GET /api/eurospin/eurospin-italia/promotions/<alias>/stores/eurospin-italia/products?page=N&size=100` —
    prodotti del volantino **già strutturati**: TITLE, MARK (brand), PAGE,
    END-PRICE, INITIAL-PRICE, DISCOUNT-RATE, END-KG-LT-PRICE, CATEGORY,
    descrizione pezzatura. ~200 prodotti per volantino nazionale.
  - `GET /api/eurospin/eurospin-italia/stores` — **1337 punti vendita** con
    indirizzo, città, provincia, CAP e `gpsCoordinates`.
- **Periodicità**: volantino nazionale bisettimanale + "ribassati" mensile.
- **Nota chiave**: per Eurospin la vision NON serve — il feed è la verità.
  È anche un'ottima **ground truth automatica** per calibrare l'estrazione
  vision sulle stesse pagine PDF.

### MD — feed GIÀ STRUTTURATO dal viewer ✅ (niente vision)

- **Flusso** (già usato da `scraping/spiders/md_spider.py`):
  `https://www.mdspa.it/sfogliatore/?id_pv=1` → attributo
  `data-flyer-code` → `https://service-volantino.mdspa.it/<code>` → nel
  HTML `var data = [...]` con i prodotti strutturati (code, name, brand,
  price, priceOff, photos, category, section).
- **Periodicità**: volantino bisettimanale, varianti zonali per punto vendita
  (`id_pv`).

### Penny — HTML server-rendered (copre già lo spider esistente) ⚠

- `https://www.penny.it/volantini` è 404; le offerte volantino vivono come
  **categoria prodotti server-rendered** (`/categorie/volantino-<data>`,
  linkata da `/offerte`). Stessa struttura a tile HTML che
  `scraping/spiders/penny_spider.py` già estrae da `/offerte`.
- Verdetto POC: nessun PDF/immagine pubblico individuato senza browser;
  la strada giusta per Penny è estendere lo spider HTML esistente alla
  categoria volantino, non la vision.

### In's Mercato — non investigata in questo POC

- `insmercato.it` ha un viewer volantino; da investigare in una fase
  successiva (bastano 2 catene funzionanti per il POC, ne abbiamo 4).

## Note legali

I volantini sono **materiale promozionale pubblico**, distribuito dalle
catene proprio per essere consultato e diffuso. La pipeline:

- accede solo a risorse pubbliche, senza autenticazione utente né bypass di
  protezioni (le credenziali OAuth Eurospin sono il client pubblico del
  viewer, servite in chiaro a ogni visitatore del sito);
- rispetta un rate limit di **1 richiesta/secondo** per host;
- usa i dati per confronto prezzi (fatto lecito in UE; cfr. direttiva
  2005/29/CE sulla pubblicità comparativa) senza ripubblicare i PDF;
- conserva i PDF solo come cache di lavorazione in `out/` (non committata).

## Schema dati

`extracted.json` (output di `extract.py`, qualunque modalità):

```json
{
  "chain": "lidl", "source": "vision|structured|mock", "model": "…",
  "usage": {"input_tokens": 0, "output_tokens": 0},
  "items": [{
    "name": "…", "brand": "…|null", "price": 1.99, "original_price": 2.49,
    "unit_size": "500 g", "price_per_unit_claimed": 3.98,
    "promo_from": "2026-07-02", "promo_to": "2026-07-12",
    "requires_card": false, "confidence": 0.93, "page_ref": 3
  }]
}
```

`matched.json`: `{flyer_item, product_id|null, match_score, match_method}` con
method ∈ `ean | alias | brand_name_exact | brand_name_fuzzy | name_fuzzy | null`.

`new_products.json`: unmatched con confidence ≥ 0.7 — candidati nuovi prodotti
(barcode NULL). **Non** inseriti nel DB in questo POC.

## Kill-switch (eval.py)

| Tasso errore prezzi | Verdetto |
|---|---|
| < 5% | **GO** — la strada volantini è percorribile |
| 5-10% | **RIVEDERE** — migliorare prompt/risoluzione, rimisurare |
| > 10% | **STOP** — abbandonare la strada volantini |

Flusso: `eval.py <extracted.json> --init` genera
`ground_truth.template.json`; si compila a mano guardando il PDF (bastano 2-3
pagine, ~30-50 item); `eval.py <extracted.json> <ground_truth.json>` stampa
metriche e verdetto.

## Costi API vision (stima)

Pagina volantino renderizzata a ~1180×2000 px ≈ 3.100 token immagine
(`(w*h)/750`), prompt ~450 token, output ~1.500-2.500 token/pagina
(30-40 item). Con `claude-opus-4-8` ($5/M input, $25/M output):

- **~0,07 $/pagina** → volantino Lidl da 11 pagine ≈ **0,8 $**
- settimana tipo (Lidl 2 volantini ~25 pag + Aldi 2 volantini ~35 pag) ≈
  **4-5 $/settimana**; con Batch API (-50%) ≈ **2-2,5 $/settimana**
- Eurospin e MD costano **0 $** (feed strutturati)

Una volta validata la qualità con eval.py si può testare
`--model claude-haiku-4-5` (≈ 1/5 del costo).
