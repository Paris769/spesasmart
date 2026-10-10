"""
Backend di inferenza dell'assistente basato sull'ABBONAMENTO Claude dell'utente.

===========================  AVVERTENZA  ===========================
Questo percorso usa l'ABBONAMENTO Claude Pro/Max PERSONALE dell'utente
(OAuth2 PKCE, token in keyring), non l'API a pagamento. Di conseguenza:

  * e' legittimo SOLO per uso PERSONALE e LOCALE (l'app girante sul PC
    dell'utente, con l'utente che la usa);
  * il token e' una CREDENZIALE PERSONALE: non va usato per servire
    utenti pubblici, non va condiviso, non va messo in un deploy
    multi-utente (Render/Vercel) ne' in un container pubblico;
  * se SpesaSmart viene esposta a utenti terzi, questo percorso NON e'
    quello corretto: serve l'API a pagamento (ANTHROPIC_API_KEY), che
    l'endpoint continua a preferire quando la chiave e' presente;
  * i limiti dell'abbonamento sono condivisi con tutto il resto che
    l'utente fa con Claude: esaurirli qui significa restare senza
    Claude altrove. Per questo il rate limit resta attivo anche qui
    (vedi assistant.py: non serve a contenere i COSTI - non ce ne sono -
    ma a non bruciare la quota dell'abbonamento).
====================================================================

COME FUNZIONA
`claude-agent-sdk` 0.2.165 non parla direttamente con l'API: lancia come
SOTTOPROCESSO la CLI `claude`, che a sua volta chiama il modello usando il
token dell'abbonamento passato in `env`. I nostri tool vengono esposti al
modello come un server MCP IN-PROCESS (`create_sdk_mcp_server`): le funzioni
girano nel NOSTRO processo Python, quindi hanno accesso diretto alla
AsyncSession di SQLAlchemy esattamente come nel percorso API. Niente HTTP,
niente duplicazione della logica dei tool: `assistant_tools.TOOLS` /
`assistant_tools.run_tool` sono gli stessi.

Dettagli dell'API usata (verificata sul pacchetto installato, 0.2.165):
  tool(name, description, input_schema) -> decoratore -> SdkMcpTool
  create_sdk_mcp_server(name, version, tools=[...]) -> McpSdkServerConfig
  ClaudeAgentOptions(mcp_servers={...}, allowed_tools=[...], tools=[],
                     system_prompt=str, env=..., model=..., effort=...,
                     max_turns=..., permission_mode=..., cwd=...,
                     setting_sources=[], strict_mcp_config=True)
  ClaudeSDKClient(options) -> async with ... -> query(prompt) / receive_response()

I tool MCP sono visti dal modello con il nome `mcp__spesasmart__<tool>`.

DIFFERENZE RISPETTO AL PERCORSO API (volute, documentate)
  1. Lo storico non viaggia come turni separati: la CLI riceve UN prompt, e
     i turni precedenti vengono riassunti dentro il prompt come trascrizione.
  2. Il "replay" dei blocchi thinking lo gestisce la CLI: noi non ricostruiamo
     la conversazione.
  3. Il loop di tool use lo guida la CLI (`max_turns`), non noi; l'ultima
     iterazione non viene forzata con `tool_choice: none` (la CLI non lo
     espone), quindi il limite e' solo `max_turns`.
  4. Non c'e' cache_control da gestire (nessun costo da ottimizzare) ne'
     conteggio token per fatturazione: logghiamo quello che la CLI riporta.
  5. Gli argomenti dei tool sono validati con jsonschema PRIMA del nostro
     handler. Per non perdere la rete di sicurezza che inietta lat/lng/email
     dal contesto della richiesta, i campi che sappiamo riempire vengono
     TOLTI da `required` nello schema esposto al modello (vedi _relaxed_schema).

ISOLAMENTO DEL SOTTOPROCESSO (non e' un dettaglio)
  * `tools=[]`          -> nessun tool built-in della CLI (niente Bash, Read,
                           Write, WebFetch...): il modello vede SOLO i nostri 6.
  * `setting_sources=[]`-> nessun settings.json, nessun hook, nessun CLAUDE.md.
  * `strict_mcp_config` -> solo il nostro server MCP, non quelli dell'utente.
  * `cwd`               -> una cartella temporanea dedicata, MAI il repo
                           (che sta in OneDrive).
  * `permission_mode="bypassPermissions"` -> senza tool built-in non c'e' nulla
    di pericoloso da autorizzare, e la CLI non puo' chiedere conferme a una
    richiesta HTTP. Il gate sulle SCRITTURE resta il nostro (allow_write).
  * NIENTE `--bare`: in quella modalita' la CLI legge l'autenticazione SOLO da
    ANTHROPIC_API_KEY/apiKeyHelper e ignora OAuth, cioe' romperebbe proprio
    l'uso con abbonamento.

PROMPT INJECTION: identica al percorso API. I valori che vengono dallo
scraping passano da assistant_tools (_safe_text) e viaggiano solo come
contenuto di un tool_result; il system prompt lo dichiara al modello. In piu'
qui il messaggio utente e lo storico vengono ripuliti dai caratteri di
controllo prima di finire nel prompt (vedi _prompt_safe).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import assistant_tools as at

logger = logging.getLogger("spesasmart.assistant_sdk")

# ───────────────────────────── configurazione ─────────────────────────────

# Nome del server MCP in-process: entra nel nome che il modello vede
# (`mcp__spesasmart__get_last_purchase`).
MCP_SERVER_NAME = "spesasmart"

# Modello: qui si passa alla CLI, che accetta sia gli alias ("sonnet", "opus",
# "haiku") sia gli id completi. L'alias e' piu' robusto fra le versioni della
# CLI, quindi e' il default; si forza con ASSISTANT_SUBSCRIPTION_MODEL.
SUB_MODEL = os.getenv("ASSISTANT_SUBSCRIPTION_MODEL", "sonnet").strip() or "sonnet"

# effort: come nel percorso API, il compito e' instradare tool e riassumere.
# Stringa vuota = non passare il flag (lascia decidere alla CLI).
SUB_EFFORT = os.getenv("ASSISTANT_SUBSCRIPTION_EFFORT", "low").strip()
_VALID_EFFORT = {"low", "medium", "high", "xhigh", "max"}

MAX_HISTORY_TURNS = 10          # come assistant.py
MAX_HISTORY_CHARS = 2000
MAX_TOOL_RESULT_CHARS = 12000   # tetto sul JSON dato al modello
MAX_STDERR_LOG_CHARS = 500

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, "")), 1)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(float(os.getenv(name, "")), 1.0)
    except (TypeError, ValueError):
        return default


# Turni massimi del loop di tool use lato CLI. Piu' generoso di
# MAX_TOOL_ITERATIONS del percorso API (6) perche' qui ogni turno non costa
# denaro; il tetto serve solo a non avvitarsi.
SUB_MAX_TURNS = _env_int("ASSISTANT_SUBSCRIPTION_MAX_TURNS", 10)

# Timeout complessivo. Piu' alto dei 45s del percorso API: qui si paga lo
# spawn della CLI (node, ~1-3s) e non c'e' fretta di costo. Resta sotto i 90s
# di timeout del client (frontend/lib/api.ts: ASSISTANT_TIMEOUT_MS).
SUB_TOTAL_TIMEOUT_S = _env_float("ASSISTANT_SUBSCRIPTION_TIMEOUT_S", 75.0)


# ───────────────────────────── errori ─────────────────────────────

class SubscriptionError(RuntimeError):
    """Base: il percorso abbonamento non ha prodotto nulla di utile."""


class SubscriptionUnavailable(SubscriptionError):
    """Il percorso abbonamento non e' installato/utilizzabile su questa
    macchina (manca claude-agent-sdk, manca la CLI `claude`, manca
    `requests`/`keyring` per il modulo OAuth...)."""


class SubscriptionNeedsAuth(SubscriptionError):
    """L'abbonamento non e' (piu') collegato: serve il click su "Authorize".
    L'endpoint traduce questo caso in 503 `assistant_needs_auth`."""


# ───────────────────────────── stato / disponibilita' ─────────────────────────────

def _claude_auth():
    """Import LAZY del modulo OAuth della skill.

    Lazy e non in testa al file perche' `claude_auth` importa `requests` e
    `keyring`, che NON sono in backend/requirements.txt: in produzione
    (Render) l'import fallirebbe e non deve impedire l'avvio dell'app ne'
    rompere il percorso API.
    """
    try:
        from app.core.subscription_auth import claude_auth  # type: ignore
    except Exception as exc:  # ImportError, ma anche errori di init del keyring
        raise SubscriptionUnavailable(
            f"modulo subscription_auth non utilizzabile: {type(exc).__name__}: {exc}"
        ) from exc
    return claude_auth


def _sdk():
    """Import LAZY di claude-agent-sdk (anch'esso fuori da requirements.txt)."""
    try:
        import claude_agent_sdk  # type: ignore
    except Exception as exc:
        raise SubscriptionUnavailable(
            f"claude-agent-sdk non disponibile: {type(exc).__name__}: {exc}"
        ) from exc
    return claude_agent_sdk


def is_installed() -> bool:
    """True se i pezzi del percorso abbonamento sono importabili.

    Serve all'endpoint per distinguere "non c'e' nulla di configurato"
    (`assistant_unavailable`) da "c'e' tutto, manca solo l'autorizzazione"
    (`assistant_needs_auth`). Non fa I/O di rete.
    """
    try:
        _sdk()
        _claude_auth()
    except SubscriptionUnavailable:
        return False
    return True


def _status_blocking() -> dict:
    return _claude_auth().get_status()


async def subscription_status() -> dict:
    """Stato del collegamento all'abbonamento, senza bloccare l'event loop.

    `get_status()` legge il keyring (I/O sincrono, su Windows passa per il
    Credential Manager): va in un thread. Non fa chiamate di rete.

    Ritorna sempre un dict con almeno `state`; in caso di modulo assente
    ritorna {"state": "unavailable"} invece di sollevare, cosi' il chiamante
    ha un solo ramo da gestire.
    """
    try:
        return await asyncio.to_thread(_status_blocking)
    except SubscriptionUnavailable as exc:
        logger.debug("assistant_sdk: percorso abbonamento non installato (%s)", exc)
        return {"state": "unavailable", "method": None, "expires_at": None}
    except Exception as exc:
        logger.warning(
            "assistant_sdk: get_status() fallito (%s: %s)", type(exc).__name__, exc
        )
        return {"state": "error", "method": None, "expires_at": None}


#: Stati in cui si puo' provare a chiamare il modello: "expiring" va bene,
#: `ensure_token()` fa il refresh da solo.
USABLE_STATES = frozenset({"connected", "expiring"})


def is_usable(status: Mapping[str, Any]) -> bool:
    return str((status or {}).get("state") or "") in USABLE_STATES


async def _sdk_env() -> dict:
    """`{CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY: ""}` per il sottoprocesso.

    In un thread: `sdk_env()` -> `ensure_token()` puo' fare una POST di refresh
    con `requests` (bloccante).

    NB: `ensure_token()` tocca anche os.environ del NOSTRO processo (mette
    CLAUDE_CODE_OAUTH_TOKEN e rimuove ANTHROPIC_API_KEY). Non e' un problema
    per il percorso API, che usa `settings.ANTHROPIC_API_KEY` (letto da
    pydantic-settings all'avvio) e lo passa esplicitamente al client.
    """
    claude_auth = _claude_auth()
    try:
        return await asyncio.to_thread(claude_auth.sdk_env)
    except claude_auth.NeedsAuth as exc:
        raise SubscriptionNeedsAuth(str(exc) or "abbonamento non collegato") from exc


# ───────────────────────────── contesto della richiesta ─────────────────────────────

def _tools_with_property(prop: str) -> frozenset[str]:
    """Quali tool accettano un certo argomento, letto dai loro schemi.

    Derivato da `at.TOOLS` invece di una lista scritta a mano: se un tool nuovo
    nasce con lat/lng o email, l'iniezione del contesto lo copre da sola.
    """
    out = set()
    for spec in at.TOOLS:
        props = (spec.get("input_schema") or {}).get("properties") or {}
        if prop in props:
            out.add(spec["name"])
    return frozenset(out)


_GEO_TOOLS = _tools_with_property("lat")
_EMAIL_TOOLS = _tools_with_property("email")

# Campi che sappiamo riempire noi dal contesto della richiesta: quando li
# abbiamo, li togliamo da `required` nello schema esposto al modello, cosi'
# una dimenticanza del modello non diventa un errore di validazione.
_CONTEXT_FILLABLE = ("lat", "lng", "radius_km", "email")

# Quali tool "risolvono" un needs appeso (gemello di _NEEDS_RESOLVED_BY in
# assistant.py: serve a non restituire al frontend un needs gia' superato).
_NEEDS_RESOLVED_BY = {
    "email": set(_EMAIL_TOOLS),
    "no_purchases": {"get_last_purchase"},
    "location": {"build_store_plan", "rebuild_with_offers", "list_offers_nearby"},
}


def _prompt_safe(value: Any, limit: int = MAX_HISTORY_CHARS) -> str:
    """Ripulisce un testo prima di metterlo nel prompt: via i caratteri di
    controllo (incluse le sequenze ANSI), una riga sola, lunghezza limitata."""
    s = _CONTROL_CHARS.sub(" ", str(value or ""))
    s = s.replace("\r", "\n")
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    return s[:limit]


def _tool_result_json(view: Any) -> str:
    try:
        payload = json.dumps(view, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = json.dumps({"error": "risultato non serializzabile"}, ensure_ascii=False)
    if len(payload) > MAX_TOOL_RESULT_CHARS:
        payload = payload[:MAX_TOOL_RESULT_CHARS] + "\n[...risultato troncato...]"
    return payload


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _Context:
    """Il contesto della richiesta (email/posizione/gate di scrittura),
    normalizzato una volta sola."""

    def __init__(self, raw: Optional[Mapping[str, Any]]) -> None:
        raw = raw or {}
        self.email: Optional[str] = (str(raw.get("email")).strip() or None) if raw.get("email") else None
        self.lat = _num(raw.get("lat"))
        self.lng = _num(raw.get("lng"))
        self.radius_km = _num(raw.get("radius_km"))
        self.allow_write = bool(raw.get("allow_write"))
        self.system_prompt = str(raw.get("system_prompt") or "").strip()

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lng is not None

    def fillable(self) -> set[str]:
        """I campi che sappiamo riempire noi in questa richiesta."""
        out: set[str] = set()
        if self.has_location:
            out.update({"lat", "lng"})
            if self.radius_km is not None:
                out.add("radius_km")
        if self.email:
            out.add("email")
        return out

    def fill_args(self, tool_name: str, args: Any) -> dict:
        """Gemello di `_fill_context_args` in assistant.py."""
        out = dict(args) if isinstance(args, dict) else {}
        if tool_name in _GEO_TOOLS and self.has_location:
            if out.get("lat") is None or out.get("lng") is None:
                out["lat"], out["lng"] = self.lat, self.lng
            if out.get("radius_km") is None and self.radius_km is not None:
                out["radius_km"] = self.radius_km
        if tool_name in _EMAIL_TOOLS and self.email and not out.get("email"):
            out["email"] = self.email
        return out

    def lines(self) -> list[str]:
        ctx: list[str] = []
        if self.email:
            ctx.append(f"email utente: {self.email}")
        else:
            ctx.append("email utente: NON disponibile (chiedila se ti serve)")
        if self.has_location:
            ctx.append(f"posizione utente: lat={self.lat}, lng={self.lng}")
            ctx.append(f"raggio di ricerca: {self.radius_km or at.DEFAULT_RADIUS_KM} km")
        else:
            ctx.append("posizione utente: NON disponibile (chiedila se ti serve)")
        if not self.allow_write:
            ctx.append(
                "l'utente NON ha chiesto di salvare liste: non usare save_recurring_list"
            )
        return ctx


# ───────────────────────────── sessione: tool + stato ─────────────────────────────

# Appendice al system prompt: solo le differenze di questo percorso. Il system
# prompt "vero" arriva dall'endpoint (context["system_prompt"]) per non
# duplicarlo e per non creare un import circolare con assistant.py.
_PROMPT_APPENDIX = """
NOTE TECNICHE DI QUESTO CANALE
- I tool ti arrivano con il prefisso `mcp__spesasmart__`: `mcp__spesasmart__get_last_purchase` e' il tool get_last_purchase, e cosi' per gli altri. Le regole sui tool valgono identiche.
- Non hai nessun altro strumento oltre a questi: non leggi file, non esegui comandi, non navighi il web. Se ti serve un dato che nessun tool fornisce, dillo all'utente.
- Chiudi SEMPRE con un messaggio di testo in italiano per l'utente: e' l'unica cosa che l'utente legge.
"""


class _Session:
    """Stato di UNA richiesta: tiene i tool MCP (closure sul db e sul
    contesto), accumula actions/payload/needs come `_ChatState` nel percorso
    API."""

    def __init__(self, db: AsyncSession, ctx: _Context) -> None:
        self.db = db
        self.ctx = ctx
        self.reply: str = ""
        self.actions: list[dict] = []
        self.client_payloads: dict[str, Any] = {}
        self.needs: Optional[dict] = None
        self.called: set[str] = set()
        self.tool_calls: int = 0
        self.timed_out: bool = False
        self.stop_reason: Optional[str] = None
        self.usage: Optional[dict] = None
        # La AsyncSession di SQLAlchemy non e' utilizzabile da piu' task
        # contemporaneamente: se il modello chiede due tool in parallelo, il
        # server MCP li eseguirebbe davvero in parallelo. Il lock li serializza
        # (nel percorso API la serializzazione e' implicita nel loop).
        self._lock = asyncio.Lock()

    # -- gestione needs (identica al percorso API) --
    def record_needs(self, needs: Optional[dict]) -> None:
        if needs and not self.needs:
            self.needs = needs

    def resolve_needs(self, tool_name: str) -> None:
        if not self.needs:
            return
        if tool_name in _NEEDS_RESOLVED_BY.get(self.needs.get("type", ""), set()):
            self.needs = None

    # -- schema esposto al modello --
    def _relaxed_schema(self, spec: Mapping[str, Any]) -> dict:
        """Lo schema del tool, con i campi che sappiamo riempire noi tolti da
        `required`.

        Perche': il server MCP valida gli argomenti con jsonschema PRIMA di
        chiamare il nostro handler. Senza questo, un modello che dimentica
        lat/lng riceverebbe un errore di validazione invece dell'iniezione dal
        contesto che il percorso API gli regala. Se il contesto NON ha quel
        campo, `required` resta com'e': cosi' il modello e' spinto a chiederlo
        all'utente invece di inventarlo.
        """
        schema = dict(spec.get("input_schema") or {})
        required = [r for r in (schema.get("required") or [])]
        fillable = self.ctx.fillable()
        kept = [r for r in required if not (r in _CONTEXT_FILLABLE and r in fillable)]
        if kept != required:
            schema["required"] = kept
        return schema

    def _blocked_write_outcome(self) -> dict:
        return {
            "ok": False,
            "summary": "Salvataggio non autorizzato dall'utente",
            "model_view": {
                "error": "salvataggio bloccato",
                "hint": (
                    "l'utente non ha chiesto di salvare: chiedigli conferma "
                    "prima di riprovare"
                ),
            },
            "client_key": None,
            "client": None,
            "needs": None,
        }

    def _handler_for(self, tool_name: str):
        """La closure che il server MCP chiama. Non duplica logica: delega a
        `at.run_tool`, esattamente come fa il loop del percorso API."""

        async def handler(args: Any) -> dict:
            self.tool_calls += 1
            filled = self.ctx.fill_args(tool_name, args)

            if tool_name in at.WRITE_TOOLS and not self.ctx.allow_write:
                # Gate di codice sulle scritture: il prompt lo dice, ma non ci
                # fidiamo del solo prompt (doppia difesa, come nel percorso API).
                outcome = self._blocked_write_outcome()
            elif self.needs is not None:
                # Un needs irrisolvibile (email/posizione mancanti, storico
                # vuoto) non migliora con altri tool: smettiamo di lavorare e
                # diciamo al modello di chiudere chiedendo all'utente.
                outcome = {
                    "ok": False,
                    "summary": f"{tool_name} non eseguito: manca un dato all'utente",
                    "model_view": {
                        "error": "informazione mancante",
                        "hint": self.needs.get("message") or "chiedi il dato all'utente",
                    },
                    "client_key": None,
                    "client": None,
                    "needs": None,
                }
            else:
                async with self._lock:
                    outcome = await at.run_tool(self.db, tool_name, filled)

            self.called.add(tool_name)
            ok = bool(outcome.get("ok"))
            self.actions.append({
                "tool": tool_name,
                "summary": outcome.get("summary") or tool_name,
                "ok": ok,
            })
            if ok:
                self.resolve_needs(tool_name)
            self.record_needs(outcome.get("needs"))
            if outcome.get("client_key") and outcome.get("client") is not None:
                self.client_payloads[outcome["client_key"]] = outcome["client"]

            return {
                "content": [
                    {"type": "text", "text": _tool_result_json(outcome.get("model_view") or {})}
                ],
                "is_error": not ok,
            }

        handler.__name__ = f"sdk_tool_{tool_name}"
        return handler

    def mcp_server(self) -> Any:
        """Il server MCP in-process con i nostri 6 tool.

        Costruito PER RICHIESTA: gli handler sono closure su `self.db` e sul
        contesto, quindi niente contextvar e niente stato condiviso fra
        richieste concorrenti. La creazione e' in-process e costa nulla.
        """
        sdk = _sdk()
        tools = [
            sdk.tool(
                spec["name"],
                spec["description"],
                self._relaxed_schema(spec),
            )(self._handler_for(spec["name"]))
            for spec in at.TOOLS
        ]
        return sdk.create_sdk_mcp_server(
            name=MCP_SERVER_NAME, version="1.0.0", tools=tools
        )

    def allowed_tool_names(self) -> list[str]:
        return [f"mcp__{MCP_SERVER_NAME}__{spec['name']}" for spec in at.TOOLS]


# ───────────────────────────── prompt ─────────────────────────────

def _history_block(history: Optional[Sequence[Any]]) -> str:
    """Lo storico come trascrizione dentro il prompt.

    La CLI riceve UN messaggio utente: i turni precedenti non possono essere
    passati come turni veri (lo farebbe `resume`, ma vorrebbe una sessione
    persistente lato CLI e noi siamo stateless). Trascrizione etichettata:
    il modello la capisce e i marcatori di ruolo finti vengono neutralizzati
    da `_prompt_safe` + prefisso ">".
    """
    turns = [h for h in (history or []) if h is not None]
    cleaned: list[str] = []
    for h in turns[-(MAX_HISTORY_TURNS * 2):]:
        role = getattr(h, "role", None) or (h.get("role") if isinstance(h, Mapping) else None)
        content = getattr(h, "content", None) or (h.get("content") if isinstance(h, Mapping) else None)
        text = _prompt_safe(content)
        if not text:
            continue
        label = "UTENTE" if role == "user" else "ASSISTENTE"
        body = "\n".join(f"> {line}" for line in text.split("\n"))
        cleaned.append(f"{label}:\n{body}")
    if not cleaned:
        return ""
    return "CONVERSAZIONE PRECEDENTE (dal piu' vecchio al piu' recente):\n" + "\n".join(cleaned)


def _build_prompt(message: str, history: Optional[Sequence[Any]], ctx: _Context) -> str:
    parts = [f"[contesto: {'; '.join(ctx.lines())}]"]
    block = _history_block(history)
    if block:
        parts.append(block)
    parts.append("RICHIESTA ATTUALE DELL'UTENTE:\n" + _prompt_safe(message, limit=4000))
    return "\n\n".join(parts)


def _build_system_prompt(ctx: _Context) -> str:
    base = ctx.system_prompt or (
        "Sei l'assistente di SpesaSmart, un comparatore di prezzi dei "
        "supermercati italiani. Parli italiano, sei concreto e brevissimo. "
        "Agisci solo tramite i tool: non inventare mai email, coordinate, "
        "prezzi, prodotti o negozi."
    )
    return base.rstrip() + "\n" + _PROMPT_APPENDIX


# ───────────────────────────── esecuzione ─────────────────────────────

_WORKDIR_NAME = "spesasmart-assistant-sdk"


def _workdir() -> str:
    """Cartella di lavoro del sottoprocesso: MAI il repo.

    Il repo sta in OneDrive e contiene .claude/, CLAUDE.md e settings di
    progetto. Con `setting_sources=[]` non verrebbero letti comunque, ma una
    cwd dedicata evita anche che la CLI scriva qualcosa dentro la cartella
    sincronizzata.
    """
    path = Path(tempfile.gettempdir()) / _WORKDIR_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
        return str(path)
    except OSError:
        return tempfile.gettempdir()


def _log_stderr(line: str) -> None:
    text = (line or "").strip()
    if text:
        logger.debug("assistant_sdk[cli]: %s", text[:MAX_STDERR_LOG_CHARS])


def _build_options(session: _Session, env: dict) -> Any:
    sdk = _sdk()
    kwargs: dict[str, Any] = {
        "env": env,
        "system_prompt": _build_system_prompt(session.ctx),
        "mcp_servers": {MCP_SERVER_NAME: session.mcp_server()},
        "allowed_tools": session.allowed_tool_names(),
        # Nessun tool built-in della CLI: il modello vede SOLO i nostri.
        "tools": [],
        # Solo il nostro server MCP, non quelli configurati dall'utente.
        "strict_mcp_config": True,
        # Nessun settings.json / hook / CLAUDE.md dal filesystem.
        "setting_sources": [],
        # Niente tool built-in => niente da autorizzare; e una richiesta HTTP
        # non puo' rispondere a un prompt di permesso.
        "permission_mode": "bypassPermissions",
        "max_turns": SUB_MAX_TURNS,
        "model": SUB_MODEL,
        "cwd": _workdir(),
        "include_partial_messages": False,
        "stderr": _log_stderr,
    }
    if SUB_EFFORT in _VALID_EFFORT:
        kwargs["effort"] = SUB_EFFORT
    return sdk.ClaudeAgentOptions(**kwargs)


async def _drive(session: _Session, options: Any, prompt: str) -> None:
    """Guida una conversazione con la CLI fino al ResultMessage.

    `ClaudeSDKClient` + `async with` (e non `query()`): il context manager
    garantisce il `disconnect()` - e quindi la chiusura del sottoprocesso -
    anche quando il task viene cancellato dal timeout. Con un async generator
    la chiusura dipenderebbe dalla finalizzazione, cioe' potrebbe restare in
    giro un processo `claude` per richiesta andata in timeout.
    """
    sdk = _sdk()
    async with sdk.ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, sdk.AssistantMessage):
                _absorb_assistant(session, sdk, msg)
                if msg.error:
                    _raise_for_model_error(str(msg.error))
            elif isinstance(msg, sdk.ResultMessage):
                _absorb_result(session, msg)
            elif isinstance(msg, sdk.RateLimitEvent):
                logger.warning(
                    "assistant_sdk: limite dell'abbonamento segnalato dalla CLI (%s)",
                    getattr(msg, "rate_limit_info", None),
                )

            if session.needs:
                # Come nel percorso API: un needs non si risolve con altre
                # iterazioni. Fermiamo la conversazione invece di consumare
                # altri turni dell'abbonamento.
                with contextlib.suppress(Exception):
                    await client.interrupt()
                return


def _absorb_assistant(session: _Session, sdk: Any, msg: Any) -> None:
    texts = [
        b.text
        for b in (msg.content or [])
        if isinstance(b, sdk.TextBlock) and getattr(b, "text", None)
    ]
    text = " ".join(t.strip() for t in texts).strip()
    if text:
        session.reply = text


def _absorb_result(session: _Session, msg: Any) -> None:
    session.stop_reason = getattr(msg, "subtype", None) or getattr(msg, "stop_reason", None)
    session.usage = getattr(msg, "usage", None)
    result = (getattr(msg, "result", None) or "").strip()
    if result and not getattr(msg, "is_error", False):
        session.reply = result
    status = getattr(msg, "api_error_status", None)
    if status in (401, 403):
        raise SubscriptionNeedsAuth(
            f"la CLI ha ricevuto HTTP {status}: token dell'abbonamento rifiutato"
        )
    if getattr(msg, "is_error", False):
        logger.warning(
            "assistant_sdk: la CLI ha chiuso in errore (subtype=%s, errors=%s)",
            session.stop_reason, (getattr(msg, "errors", None) or [])[:3],
        )


def _raise_for_model_error(error: str) -> None:
    if error == "authentication_failed":
        raise SubscriptionNeedsAuth("autenticazione rifiutata: riautorizza l'abbonamento")
    if error == "billing_error":
        # Succede se per qualche motivo la CLI e' finita sull'API a pagamento.
        raise SubscriptionUnavailable("errore di fatturazione riportato dalla CLI")
    logger.warning("assistant_sdk: errore riportato dal modello (%s)", error)


async def run_subscription_chat(
    db: AsyncSession,
    message: str,
    history: Optional[Sequence[Any]] = None,
    context: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Esegue una conversazione dell'assistente sull'ABBONAMENTO dell'utente.

    Stessa semantica del loop di tool use del percorso API, stessa forma della
    risposta dell'endpoint:

        {reply, actions, engine, plan?, rebuild?, last_purchase?, saved_list?, needs?}

    `context` (tutte le chiavi opzionali):
        email, lat, lng, radius_km  -> contesto iniettato negli argomenti dei tool
        allow_write (bool)          -> gate sul solo tool che scrive
        system_prompt (str)         -> il system prompt dell'endpoint (passato
                                       dall'esterno per non duplicarlo e non
                                       creare import circolari)

    `reply` puo' essere vuota (timeout, max_turns, la CLI non ha prodotto
    testo): il chiamante applica il proprio messaggio di cortesia, cosi' il
    testo di fallback vive in un posto solo (assistant.py::_fallback_reply).
    Se c'e' un `needs`, `reply` e' il suo messaggio canonico.

    Solleva:
        SubscriptionNeedsAuth  -> abbonamento da (ri)autorizzare   -> 503 assistant_needs_auth
        SubscriptionUnavailable-> pezzi mancanti / CLI assente     -> 503 assistant_unavailable
    Non solleva per errori dei tool (diventano dati) ne' per timeout (ritorna
    quello che ha raccolto).
    """
    ctx = _Context(context)
    session = _Session(db, ctx)

    sdk = _sdk()                                # puo' sollevare Unavailable
    try:
        env = await _sdk_env()                  # puo' sollevare NeedsAuth
        options = _build_options(session, env)
        prompt = _build_prompt(message, history, ctx)
    except SubscriptionError:
        raise
    except Exception as exc:
        # Es. la POST di refresh del token che non passa (rete giu'): nessuna
        # chiamata al modello e' ancora partita, quindi e' un 503 pieno.
        logger.warning(
            "assistant_sdk: preparazione fallita (%s: %s)", type(exc).__name__, exc
        )
        raise SubscriptionUnavailable(f"{type(exc).__name__}: {exc}") from exc

    try:
        await asyncio.wait_for(
            _drive(session, options, prompt), timeout=SUB_TOTAL_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        session.timed_out = True
        logger.warning(
            "assistant_sdk: timeout dopo %.0fs (%d tool chiamati)",
            SUB_TOTAL_TIMEOUT_S, session.tool_calls,
        )
    except sdk.CLINotFoundError as exc:
        raise SubscriptionUnavailable(
            "la CLI `claude` non e' installata o non e' nel PATH "
            "(npm i -g @anthropic-ai/claude-code): " + str(exc)
        ) from exc
    except SubscriptionError:
        raise
    except sdk.ClaudeSDKError as exc:
        # ProcessError / CLIConnectionError / CLIJSONDecodeError: se non
        # abbiamo raccolto nulla il chiamante deve poter rispondere 503.
        logger.warning(
            "assistant_sdk: errore della CLI (%s: %s)", type(exc).__name__, exc
        )
        if not session.reply and not session.actions:
            raise SubscriptionUnavailable(f"{type(exc).__name__}: {exc}") from exc
    except Exception as exc:
        logger.warning(
            "assistant_sdk: errore inatteso (%s: %s)", type(exc).__name__, exc
        )
        if not session.reply and not session.actions:
            raise SubscriptionUnavailable(f"{type(exc).__name__}: {exc}") from exc

    reply = session.reply.strip()
    if not reply and session.needs and session.needs.get("message"):
        reply = session.needs["message"]

    logger.info(
        "assistant_sdk: fine — tool=%s, stop=%s, timeout=%s, usage=%s",
        sorted(session.called) or "-", session.stop_reason, session.timed_out,
        _usage_digest(session.usage),
    )

    out: dict[str, Any] = {
        "reply": reply,
        "actions": session.actions,
        "engine": "subscription",
    }
    out.update(session.client_payloads)   # plan / rebuild / last_purchase / saved_list
    if session.needs:
        out["needs"] = session.needs
    return out


def _usage_digest(usage: Any) -> str:
    """Solo i token, per i log. Niente costo: sull'abbonamento non ce n'e'."""
    if not isinstance(usage, Mapping):
        return "-"
    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    return f"in/out={inp}/{out}"
