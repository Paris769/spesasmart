"""
Redirect d'acquisto tracciato (monetizzazione).

Tutti i pulsanti "Acquista"/"Apri" passano da qui: registriamo il click
(prova del valore generato ai partner affiliati + dati per ottimizzare i ricavi)
e reindirizziamo al retailer, applicando il tag di affiliazione se configurato.
La destinazione è validata contro un'allowlist (anti open-redirect/phishing).
"""
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.api.v1.endpoints.affiliate import apply_affiliate, is_allowed_host

router = APIRouter(prefix="/go", tags=["go"])


def _looks_uuid(s: str | None) -> bool:
    return bool(s) and len(s) == 36 and s.count("-") == 4


@router.get("")
async def go(
    u: str = Query(..., description="URL di destinazione (retailer)"),
    chain: str | None = Query(None),
    pid: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not is_allowed_host(parsed.hostname):
        return JSONResponse({"detail": "destinazione non consentita"}, status_code=400)

    # Tracciamento click (fire-and-forget): non deve mai bloccare il redirect.
    try:
        await db.execute(
            text(
                "INSERT INTO click_log (chain_slug, product_id, target_host) "
                "VALUES (:c, :p, :h)"
            ),
            {"c": chain, "p": pid if _looks_uuid(pid) else None, "h": parsed.hostname},
        )
        await db.commit()
    except Exception:
        pass

    return RedirectResponse(apply_affiliate(u, chain), status_code=302)
