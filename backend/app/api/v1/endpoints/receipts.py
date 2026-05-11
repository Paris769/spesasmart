"""
Endpoint per il parsing degli scontrini fiscali italiani tramite Claude Vision.

POST /receipts/parse
  - Accetta immagine (JPEG/PNG/WEBP) o PDF dello scontrino
  - Estrae: negozio, data, articoli (nome, prezzo, quantità)
  - Tenta il match degli articoli con i prodotti nel DB
  - Ritorna dati strutturati pronti per la visualizzazione
"""
import base64
import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db

router = APIRouter(prefix="/receipts", tags=["receipts"])

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_SIZE_MB = 10

PARSE_PROMPT = """Analizza questo scontrino fiscale italiano ed estrai le informazioni in formato JSON.

Restituisci SOLO un oggetto JSON valido con questa struttura (nessun testo aggiuntivo):
{
  "store_name": "nome del negozio o insegna",
  "store_address": "indirizzo completo se visibile",
  "store_chain": "catena (es: Esselunga, Conad, Coop, Carrefour, Lidl, Eurospin, ...)",
  "purchase_date": "YYYY-MM-DD o null",
  "total_amount": numero o null,
  "items": [
    {
      "name": "nome prodotto come appare sullo scontrino",
      "quantity": numero (default 1),
      "unit_price": prezzo unitario come numero,
      "total_price": prezzo totale riga come numero,
      "is_discount": true se è una riga di sconto
    }
  ]
}

Regole:
- Includi solo articoli reali (non righe di subtotale, IVA, totale, pagamento)
- I prezzi devono essere numeri decimali (es: 1.99, non "1,99 €")
- Se un campo non è leggibile, usa null
- is_discount = true per righe con importo negativo (sconti, promo)"""


@router.post("/parse")
async def parse_receipt(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="Servizio OCR non configurato (ANTHROPIC_API_KEY mancante)")

    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail=f"Formato non supportato: {content_type}. Usa JPEG, PNG, WEBP o PDF.")

    raw = await file.read()
    if len(raw) > MAX_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"File troppo grande (max {MAX_SIZE_MB} MB)")

    # Per PDF usiamo il media type document, per immagini image
    b64 = base64.standard_b64encode(raw).decode()
    if content_type == "application/pdf":
        media_source = {"type": "base64", "media_type": "application/pdf", "data": b64}
        content_block = {"type": "document", "source": media_source}
    else:
        media_source = {"type": "base64", "media_type": content_type, "data": b64}
        content_block = {"type": "image", "source": media_source}

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": PARSE_PROMPT},
                ],
            }],
        )
        raw_text = message.content[0].text.strip()
        # Rimuove eventuale markdown ```json ... ```
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"Risposta AI non parsificabile: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Errore AI: {e}")

    items = parsed.get("items") or []
    real_items = [it for it in items if not it.get("is_discount")]

    # Match prodotti nel DB (fuzzy sul nome)
    matched = []
    for item in real_items:
        name = (item.get("name") or "").strip()
        if not name:
            continue

        row = await db.execute(
            text("""
                SELECT id, name, brand, image_url, barcode
                FROM products
                WHERE to_tsvector('simple', lower(name)) @@ plainto_tsquery('simple', lower(:q))
                   OR name ILIKE :q_like
                ORDER BY similarity(name, :q) DESC
                LIMIT 1
            """),
            {"q": name, "q_like": f"%{name[:20]}%"},
        )
        match = row.mappings().first()

        matched.append({
            **item,
            "matched_product": dict(match) if match else None,
        })

    return {
        "store_name":    parsed.get("store_name"),
        "store_address": parsed.get("store_address"),
        "store_chain":   parsed.get("store_chain"),
        "purchase_date": parsed.get("purchase_date"),
        "total_amount":  parsed.get("total_amount"),
        "items":         matched,
        "items_count":   len(matched),
    }
