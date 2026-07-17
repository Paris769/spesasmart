"""
Agente AI (Fase 2): parsing LLM della richiesta di spesa in linguaggio naturale.

POST /api/v1/agent/parse trasforma un prompt libero ("colazione per 4 per una
settimana") in una lista spesa concreta [{query, quantity}] usando Claude Haiku
(claude-haiku-4-5, il piu' recente ed economico). Se ANTHROPIC_API_KEY manca o
la chiamata fallisce, rispondiamo 503 {"detail": "llm_unavailable"}: il
frontend fa fallback al parser locale.

Privacy: il prompt utente NON viene mai loggato per intero — troncato a 50
caratteri nei log.
"""
import json
import logging
import re
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger("spesasmart.agent_ai")

MODEL_ID = "claude-haiku-4-5"
MAX_PROMPT_CHARS = 500
LLM_TIMEOUT_S = 10.0
MAX_TOKENS = 800  # contenuto: una lista spesa JSON non supera mai poche centinaia di token

# Rate limit in-memory (pattern di watches.py): ogni chiamata costa denaro
# (API Anthropic), quindi l'endpoint pubblico va protetto dal denial of wallet.
# Finestra scorrevole per IP + tetto globale sul processo. NB: e' PER-PROCESSO
# (con piu' worker/istanze il tetto effettivo si moltiplica) e si azzera al
# riavvio: basta comunque a fermare gli abusi.
_RATE_WINDOW_S = 60
_RATE_MAX_REQUESTS = 10        # per IP al minuto
_GLOBAL_WINDOW_S = 3600
_GLOBAL_MAX_REQUESTS = 300     # su tutto il processo all'ora
_rate_buckets: dict[str, list[float]] = {}
_global_bucket: list[float] = []


def _client_ip(request: Request) -> str:
    # Su Render il backend sta dietro un proxy: il client reale e' il primo
    # IP di X-Forwarded-For, se presente.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    _global_bucket[:] = [t for t in _global_bucket if now - t < _GLOBAL_WINDOW_S]
    if len(_global_bucket) >= _GLOBAL_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="Troppe richieste, riprova piu' tardi")
    bucket = [t for t in _rate_buckets.get(ip, []) if now - t < _RATE_WINDOW_S]
    if len(bucket) >= _RATE_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="Troppe richieste, riprova piu' tardi")
    bucket.append(now)
    _rate_buckets[ip] = bucket
    _global_bucket.append(now)
    # pulizia opportunistica per non far crescere il dict all'infinito
    if len(_rate_buckets) > 5000:
        for k in [k for k, v in _rate_buckets.items() if not v or now - v[-1] > _RATE_WINDOW_S]:
            _rate_buckets.pop(k, None)

SYSTEM_PROMPT = """Sei l'assistente spesa di SpesaSmart, un comparatore prezzi di supermercati italiani.

Trasforma la richiesta dell'utente in una lista della spesa concreta per un supermercato italiano.

Regole:
- Ogni voce e' un prodotto generico cercabile in un supermercato italiano (es. "latte", "pasta", "petto di pollo", "passata di pomodoro"). Niente marche a meno che l'utente non le chieda esplicitamente.
- Quantita' sensate e proporzionate: se la richiesta menziona persone e/o durata, moltiplica le quantita' (es. "colazione per 4 persone per una settimana" -> latte x4, biscotti x2, ...). Le quantita' sono numeri di confezioni/unita' da acquistare (interi, minimo 1).
- Massimo 25 voci.
- Se la richiesta non c'entra nulla con la spesa alimentare/casa, restituisci {"items": []}.

Rispondi SOLO con JSON valido, senza testo prima o dopo, nel formato:
{"items": [{"query": "latte", "quantity": 4}, {"query": "biscotti", "quantity": 2}]}"""


class ParseRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)


def _log_safe(prompt: str) -> str:
    """Tronca il prompt a 50 caratteri per i log (privacy)."""
    p = prompt.replace("\n", " ")
    return p[:50] + ("…" if len(p) > 50 else "")


def _extract_json(text_out: str) -> dict:
    """Estrae l'oggetto JSON dalla risposta, tollerando eventuali code fence."""
    cleaned = text_out.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # ultimo tentativo: primo blocco {...} nel testo
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _normalize_items(payload: dict) -> list[dict]:
    items = []
    for it in (payload.get("items") or [])[:25]:
        if not isinstance(it, dict):
            continue
        query = str(it.get("query") or "").strip()
        if len(query) < 2:
            continue
        try:
            qty = float(it.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1
        items.append({"query": query[:100], "quantity": max(round(qty, 2), 1)})
    return items


@router.post("/parse")
async def parse_shopping_prompt(body: ParseRequest, request: Request):
    """Parsing LLM del prompt di spesa.

    Rate limit in-memory PER-PROCESSO (10/min per IP + 300/h globali):
    con piu' worker o istanze il tetto effettivo si moltiplica e si azzera
    a ogni riavvio; oltre il limite risponde 429.
    """
    _check_rate_limit(_client_ip(request))

    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="llm_unavailable")

    try:
        import anthropic
    except ImportError:
        logger.error("agent_ai: pacchetto anthropic non installato")
        raise HTTPException(status_code=503, detail="llm_unavailable")

    prompt = body.prompt.strip()
    try:
        client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=LLM_TIMEOUT_S,
            max_retries=0,  # il frontend ha gia' un fallback locale: meglio fallire in fretta
        )
        response = await client.messages.create(
            model=MODEL_ID,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text_out = next((b.text for b in response.content if b.type == "text"), "")
        items = _normalize_items(_extract_json(text_out))
    except HTTPException:
        raise
    except Exception as exc:  # timeout, errori API, JSON malformato, ...
        logger.warning(
            "agent_ai: parse fallito (%s: %s) — prompt=%r",
            type(exc).__name__, exc, _log_safe(prompt),
        )
        raise HTTPException(status_code=503, detail="llm_unavailable")

    logger.info("agent_ai: parse ok, %d voci — prompt=%r", len(items), _log_safe(prompt))
    return {"items": items, "source": "llm"}
