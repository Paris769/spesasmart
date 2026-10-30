"""
Ricostruzione della spesa con le offerte: POST /api/v1/rebuild/from-items.

"Fammi una spesa con i prodotti della mia ultima spesa, ma scegliendo quelli in
offerta e mantenendo il piu' possibile le marche che compro": questo endpoint
e' l'ingresso HTTP del motore in app/services/offers_rebuild.py. Lo chiamano
l'assistente AI e lo storico spese, che gli passano le voci gia' estratte
(nome, marca, product_id quando l'hanno).

CONTRATTO
---------
Richiesta:
    {
      "items": [{"name": str, "brand": str|null,
                 "product_id": uuid|null, "quantity": float}],   # max 60
      "lat": float, "lng": float,
      "radius_km": float = 10,          # 0.5 - 50
      "chain_slug": str|null,           # limita la spesa a una sola catena
      "keep_brand": bool = true         # false = accetta il cambio marca se
                                        #         il risparmio e' sensibile
    }

Risposta:
    {
      "items": [{
        "query_name": str,
        "original": {"product_id", "name", "brand", "price",
                     "origine", "origine_label", "sconto_affermabile"},
        "chosen":   {"product_id", "name", "brand", "image_url", "price",
                     "original_price", "discount_pct", "price_per_unit",
                     "promo_label", "promo_expires", "origine",
                     "origine_label", "sconto_affermabile", "chain_slug",
                     "chain_name", "store_id", "store_name",
                     "distance_km"} | null,
        "match_kind": "same_product_on_offer" | "same_brand_on_offer"
                    | "similar_on_offer" | "no_offer_same_product"
                    | "not_found",
        "saving_vs_original": float|null,
        "brand_kept": bool,
        "promo_verdict": "true_promo"|"weak_promo"|"fake_promo"
                       |"insufficient_history"|null,
        "note": str            # frase breve in italiano, pronta da mostrare
      }],
      "summary": {"items_total", "on_offer_count", "brand_kept_count",
                  "brand_changed_count", "not_found_count",
                  "total_estimated", "total_without_offers",
                  "estimated_saving", "avviso"}
    }

`distance_km` e' null per gli store online nazionali e per i volantini: non
hanno una distanza (vedi core/geo_coverage).

`origine` dichiara da dove viene il prezzo ("online", "negozio", "volantino",
"scaduta", "segnalato", "sconosciuta") e `origine_label` e' il testo pronto da
mostrare, con la scadenza quando l'offerta e' ancora in corso (vedi
core/price_origin). `sconto_affermabile` risponde a una domanda DIVERSA: se
su quella riga si puo' affermare un risparmio, cioe' se mostrare il prezzo
barrato, la percentuale di sconto e qualunque frase di risparmio. E' true
quando la riga non e' dimostrabilmente scaduta E porta una prova di sconto
(barrato piu' alto del prezzo, o una sorgente che per natura E' un volantino),
quindi anche su uno sconto di catalogo senza scadenza — la' l'etichetta dice
che il termine non e' indicato, e non inventa una data. E' false su una riga
con la scadenza GIA' PASSATA, che e' il caso in cui il piano prometteva un
prezzo non piu' ottenibile. Il campo si chiamava `promozione_viva` fino al
giro scorso, quando fondeva le due domande in una.
`promo_verdict` e' null anche quando lo sconto non e' affermabile: un verdetto
certifica uno sconto, e dove non c'e' niente da certificare non si pubblica un
valore speciale (prima era "promo_non_viva", che nessuna riga produceva).
Nelle voci "not_found" `original` ha comunque le chiavi
`origine`, `origine_label` e `sconto_affermabile`, valorizzate a null: senza
prezzo non c'e' provenienza, ma il contratto resta quello dichiarato qui.
`summary.avviso` e' valorizzato solo quando il totale somma prezzi online e
prezzi di negozio/volantino, cioe' quando non e' un importo pagabile in una
volta sola; e' null altrimenti.

Cache-Control: no-store. La risposta dipende dalla spesa passata dell'utente e
dalla sua posizione: non va messa in nessuna cache condivisa.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.offers_rebuild import MAX_ITEMS, rebuild_with_offers

router = APIRouter(prefix="/rebuild", tags=["rebuild"])


class RebuildItem(BaseModel):
    name: str
    brand: Optional[str] = None
    product_id: Optional[str] = None
    quantity: float = 1.0


class RebuildRequest(BaseModel):
    items: list[RebuildItem]
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    radius_km: float = Field(10.0, ge=0.5, le=50)
    chain_slug: Optional[str] = None
    keep_brand: bool = True


@router.post("/from-items")
async def rebuild_from_items(
    body: RebuildRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Sostituisce ogni voce della spesa con la migliore offerta compatibile."""
    # Dipende dai dati della spesa dell'utente: mai in cache.
    response.headers["Cache-Control"] = "no-store"

    items = [it for it in body.items if (it.name or "").strip()]
    if not items:
        raise HTTPException(status_code=400, detail="Fornire almeno una voce valida")
    if len(items) > MAX_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"Massimo {MAX_ITEMS} voci per richiesta (ricevute {len(items)})",
        )

    payload: list[dict] = []
    for it in items:
        pid: Optional[str] = None
        if it.product_id:
            try:
                pid = str(UUID(str(it.product_id)))
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=422,
                    detail=f"product_id non valido per la voce '{it.name}'",
                )
        payload.append({
            "name": it.name.strip(),
            "brand": (it.brand or "").strip() or None,
            "product_id": pid,
            # Quantita' negative o nulle non hanno senso in una spesa: si
            # normalizzano a 1 invece di rifiutare l'intera richiesta.
            "quantity": it.quantity if it.quantity and it.quantity > 0 else 1.0,
        })

    chain = (body.chain_slug or "").strip().lower() or None

    return await rebuild_with_offers(
        db,
        payload,
        lat=body.lat,
        lng=body.lng,
        radius_km=body.radius_km,
        chain_slug=chain,
        keep_brand=body.keep_brand,
    )
