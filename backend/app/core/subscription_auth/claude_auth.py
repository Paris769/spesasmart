"""Autonomous Claude subscription auth via OAuth2 PKCE (framework-agnostic).

This module lets ANY local Python app authenticate with the user's Claude
Pro/Max **subscription** (NO paid API cost) and use it through the
``claude-agent-sdk``. The ONLY manual step is a single browser "Authorize"
click; the token is then refreshed automatically on every app start.

How it works
------------
1. Build an OAuth2 PKCE authorize URL and open it in the browser.
2. Capture the authorization code via a localhost callback server (or fall
   back to a manual paste of the code shown by the out-of-band redirect).
3. Exchange the code for an access token (``sk-ant-oat01-...``) + refresh token
   using an ``application/x-www-form-urlencoded`` POST.
4. Persist the refresh token securely (see ``secure_store``); the short-lived
   access token is kept in memory and re-derived via refresh as needed.
5. Inject the access token into the SDK at call time::

       from claude_agent_sdk import ClaudeAgentOptions
       import claude_auth
       opts = ClaudeAgentOptions(env={
           "CLAUDE_CODE_OAUTH_TOKEN": claude_auth.ensure_token(),
           "ANTHROPIC_API_KEY": "",   # guarantees no accidental paid-API use
       })

Why naive approaches fail
-------------------------
* The on-disk ``~/.claude/.credentials.json`` access token expires and is NOT
  auto-refreshed by a standalone/headless ``claude`` spawn (often has an empty
  refreshToken).
* ``claude setup-token`` is interactive (needs a TTY) and can't be driven
  headlessly.
* Setting ``ANTHROPIC_API_KEY`` would switch the SDK to the paid API.

The OAuth constants below match the official Claude Code CLI's public client.
They are env-overridable in case Anthropic rotates them.

``secure_store`` import
-----------------------
By default this imports a sibling ``secure_store`` module (drop both files in
the same directory). To use a package layout instead, set the environment
variable ``CLAUDE_AUTH_STORE_MODULE`` to the importable module path
(e.g. ``myapp.secure_store``).
"""
from __future__ import annotations
import os
import time
import base64
import hashlib
import importlib
import secrets
import socket
import threading
import queue
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


# --- Pluggable secure store (sibling module by default) ---------------------
def _load_store():
    """Import the credential store module (configurable, with fallbacks)."""
    override = os.environ.get("CLAUDE_AUTH_STORE_MODULE")
    candidates = []
    if override:
        candidates.append(override)
    candidates.append("secure_store")
    # Relative import when packaged alongside this module.
    if __package__:
        candidates.insert(0, f"{__package__}.secure_store")
    last_err: Exception | None = None
    for name in candidates:
        try:
            return importlib.import_module(name)
        except Exception as e:  # pragma: no cover - import resolution
            last_err = e
    raise ImportError(
        "Could not import a 'secure_store' module. Place secure_store.py next "
        "to claude_auth.py, or set CLAUDE_AUTH_STORE_MODULE to its import path. "
        f"Last error: {last_err}"
    )


secure_store = _load_store()


# --- Verified OAuth constants (env-overridable) -----------------------------
# Public client_id, identical to the official Claude Code CLI.
CLIENT_ID = os.environ.get("CLAUDE_CODE_OAUTH_CLIENT_ID", "9d1c250a-e61b-44d9-88ed-5944d1962f5e")
AUTHORIZE_URL = "https://platform.claude.com/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
# Out-of-band redirect for the manual-paste fallback.
MANUAL_REDIRECT_URL = "https://platform.claude.com/oauth/code/callback"
# Exactly these 5 scopes. org:create_api_key is intentionally DROPPED to avoid
# consent issues with subscription accounts.
SCOPES = [
    "user:profile",
    "user:inference",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
]
_REFRESH_SKEW = 300  # refresh if < 5 min to expiry

# In-process cache of the live access token (never persisted for the oauth method).
_CACHE: dict = {"access_token": None, "expires_at": None}


class NeedsAuth(Exception):
    """No usable credential — the caller must run the connect flow."""


class OAuthLocalhostFailed(Exception):
    """Localhost-callback capture failed — fall back to manual paste."""


# ---------------- PKCE ----------------

def _b64url(raw: bytes) -> str:
    """URL-safe base64 without padding (PKCE/JWT style)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _pkce() -> tuple[str, str, str]:
    """Generate (code_verifier, code_challenge[S256], state)."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = _b64url(secrets.token_bytes(32))
    return verifier, challenge, state


def _build_authorize_url(challenge: str, state: str, *, port: int | None = None, manual: bool = False) -> str:
    """Construct the authorize URL. ``manual=True`` uses the OOB redirect."""
    redirect = MANUAL_REDIRECT_URL if manual else f"http://localhost:{port}/callback"
    params = {
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect,
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)


# ---------------- localhost callback ----------------

def _free_socket() -> tuple[socket.socket, int]:
    """Bind an ephemeral port on 127.0.0.1 and return (socket, port)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    return s, port


def _make_handler(expected_state: str, result_q: "queue.Queue"):
    """Build a one-shot ``BaseHTTPRequestHandler`` that captures the code."""
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence the default stderr logging
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            code = (qs.get("code") or [None])[0]
            state = (qs.get("state") or [None])[0]
            ok = bool(code) and secrets.compare_digest(state or "", expected_state)
            body = (
                b"<html><body style='font-family:sans-serif;text-align:center;padding-top:60px'>"
                b"<h2>Authentication complete.</h2><p>You can close this tab and return to the app.</p>"
                b"</body></html>"
            ) if ok else (
                b"<html><body style='font-family:sans-serif;text-align:center;padding-top:60px'>"
                b"<h2>Authentication failed.</h2><p>Please retry from the app.</p></body></html>"
            )
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            result_q.put(code if ok else None)

    return _Handler


def run_oauth_login(timeout: int = 180) -> dict:
    """Run the full localhost PKCE flow.

    Opens the browser, captures the code on a localhost callback, and exchanges
    it for tokens. Returns the normalized creds dict. Raises
    :class:`OAuthLocalhostFailed` if the callback cannot be captured (the caller
    should then fall back to :func:`start_manual` + :func:`exchange_manual_code`).
    """
    import webbrowser

    verifier, challenge, state = _pkce()
    try:
        sock, port = _free_socket()
    except Exception as e:
        raise OAuthLocalhostFailed(f"could not open localhost socket: {e}")

    result_q: "queue.Queue" = queue.Queue()
    httpd = HTTPServer(("127.0.0.1", port), _make_handler(state, result_q), bind_and_activate=False)
    httpd.socket = sock
    httpd.server_bind = lambda: None  # already bound above
    httpd.server_activate()
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        redirect_uri = f"http://localhost:{port}/callback"
        url = _build_authorize_url(challenge, state, port=port)
        try:
            webbrowser.open(url)
        except Exception:
            pass
        # Stash the URL so a UI can show a clickable link if the browser
        # didn't open automatically.
        _CACHE["pending_authorize_url"] = url
        try:
            code = result_q.get(timeout=timeout)
        except queue.Empty:
            raise OAuthLocalhostFailed("timed out waiting for the OAuth callback")
        if not code:
            raise OAuthLocalhostFailed("callback returned no valid code (state mismatch?)")
        return _exchange_code(code, verifier, redirect_uri)
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
        _CACHE.pop("pending_authorize_url", None)


# ---------------- token exchange / refresh ----------------

def _post_token(data: dict) -> dict:
    """POST to the token endpoint as application/x-www-form-urlencoded.

    VERIFIED: the form encoding (NOT JSON) returns 200 with access_token,
    refresh_token, expires_in (28800 = 8h) and scope.
    """
    resp = requests.post(
        TOKEN_URL,
        data=data,  # application/x-www-form-urlencoded
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code >= 400:
        # Never log the body (may contain secrets); surface only status + error code.
        try:
            err = resp.json().get("error", "")
        except Exception:
            err = ""
        raise RuntimeError(f"token endpoint {resp.status_code} {err}".strip())
    return resp.json()


def _normalize_and_save(tok: dict, *, method: str) -> dict:
    """Normalize a token response, persist it, and update the in-process cache.

    The rotated refresh_token is saved BEFORE the new access token is handed to
    callers, so a crash mid-flow never strands an unsaved rotation.
    """
    access = tok.get("access_token")
    refresh_tok = tok.get("refresh_token", "")
    expires_in = tok.get("expires_in")
    expires_at = (time.time() + float(expires_in)) if expires_in else None
    scopes = (tok.get("scope") or " ".join(SCOPES)).split()
    creds = {
        "method": method,
        "refresh_token": refresh_tok,
        "expires_at": expires_at,
        "scopes": scopes,
    }
    if method == "setup-token":
        creds["access_token"] = access
    secure_store.save(creds)
    _CACHE["access_token"] = access
    _CACHE["expires_at"] = expires_at
    return creds


def _exchange_code(code: str, verifier: str, redirect_uri: str) -> dict:
    """Exchange an authorization code (localhost flow) for tokens."""
    tok = _post_token({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,  # MUST byte-match the authorize redirect_uri
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    })
    return _normalize_and_save(tok, method="oauth")


def exchange_manual_code(code: str, verifier: str, state: str) -> dict:
    """Exchange a manually pasted code (OOB redirect) for tokens.

    The out-of-band redirect returns the code as ``code#state``; the trailing
    ``#state`` (or ``&state``) fragment is stripped here.
    """
    code = code.strip().split("#")[0].split("&")[0]
    tok = _post_token({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": MANUAL_REDIRECT_URL,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
        "state": state,
    })
    return _normalize_and_save(tok, method="oauth")


def refresh(refresh_token: str) -> dict:
    """Refresh the access token. Clears the store + raises NeedsAuth on invalid_grant."""
    if not refresh_token:
        raise NeedsAuth("no refresh_token available")
    try:
        tok = _post_token({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        })
    except RuntimeError as e:
        if "invalid_grant" in str(e) or "invalid_request" in str(e):
            secure_store.clear()
            raise NeedsAuth("refresh token no longer valid — re-authentication required")
        raise
    # Keep the old refresh_token if the server didn't rotate it.
    if not tok.get("refresh_token"):
        tok["refresh_token"] = refresh_token
    return _normalize_and_save(tok, method="oauth")


# ---------------- public API ----------------

def _shape_ok(token: str | None) -> bool:
    """True if the token looks like a Claude OAuth access token."""
    return bool(token) and token.startswith("sk-ant-oat01")


def ensure_token() -> str:
    """Return a valid access token, refreshing if needed.

    Also injects the token into ``os.environ`` (CLAUDE_CODE_OAUTH_TOKEN) and
    removes ANTHROPIC_API_KEY from the current process. Raises
    :class:`NeedsAuth` if the app is not connected.
    """
    creds = secure_store.load()
    if not creds:
        raise NeedsAuth("Claude credentials absent")

    method = creds.get("method")
    if method == "setup-token":
        tok = creds.get("access_token")
        if not _shape_ok(tok):
            raise NeedsAuth("setup-token access token invalid")
        _apply_env(tok)
        return tok

    # oauth: use the cached access token if still fresh; otherwise refresh.
    now = time.time()
    cached = _CACHE.get("access_token")
    exp = _CACHE.get("expires_at")
    if _shape_ok(cached) and exp and (exp - now) > _REFRESH_SKEW:
        _apply_env(cached)
        return cached

    refresh(creds.get("refresh_token", ""))
    tok = _CACHE.get("access_token")
    if not _shape_ok(tok):
        raise NeedsAuth("refreshed access token invalid")
    _apply_env(tok)
    return tok


def _apply_env(token: str) -> None:
    """Set CLAUDE_CODE_OAUTH_TOKEN and clear ANTHROPIC_API_KEY in this process.

    Note: for spawning the SDK, prefer passing ``env=`` to ClaudeAgentOptions
    rather than relying on these process-global mutations.
    """
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
    os.environ.pop("ANTHROPIC_API_KEY", None)


def sdk_env() -> dict:
    """Convenience: the dict to pass as ``ClaudeAgentOptions(env=...)``.

    ANTHROPIC_API_KEY is set to "" so the spawned CLI can never fall back to the
    paid API; CLAUDE_CODE_OAUTH_TOKEN ranks above on-disk creds in the CLI.
    """
    return {"CLAUDE_CODE_OAUTH_TOKEN": ensure_token(), "ANTHROPIC_API_KEY": ""}


def connect() -> None:
    """Run the autonomous login flow (localhost PKCE).

    Raises :class:`OAuthLocalhostFailed` so a UI can offer the manual fallback.
    """
    run_oauth_login()  # creds saved inside _normalize_and_save


def reconnect() -> None:
    """Clear any stored creds and run :func:`connect` from scratch."""
    secure_store.clear()
    _CACHE["access_token"] = None
    _CACHE["expires_at"] = None
    connect()


def get_status() -> dict:
    """Report connection status without performing any network call.

    Returns a dict ``{"state", "method", "expires_at"}`` where ``state`` is one
    of: ``"absent"``, ``"connected"``, ``"expiring"``, ``"expired"``.
    """
    creds = secure_store.load()
    if not creds:
        return {"state": "absent", "method": None, "expires_at": None}
    method = creds.get("method")
    if method == "setup-token":
        ok = _shape_ok(creds.get("access_token"))
        return {"state": "connected" if ok else "expired", "method": method, "expires_at": None}
    exp = creds.get("expires_at")
    now = time.time()
    if not creds.get("refresh_token"):
        return {"state": "expired", "method": "oauth", "expires_at": exp}
    if exp and (exp - now) < 0 and not _CACHE.get("access_token"):
        # Expired access token but we hold a refresh token -> refreshable.
        return {"state": "expiring", "method": "oauth", "expires_at": exp}
    state = "connected"
    if exp and (exp - now) < 86400:
        state = "expiring"
    return {"state": state, "method": "oauth", "expires_at": exp}


def pending_authorize_url() -> str | None:
    """Return the in-flight authorize URL during :func:`run_oauth_login`, if any."""
    return _CACHE.get("pending_authorize_url")


def start_manual() -> tuple[str, str, str]:
    """Build a manual-redirect (OOB) authorize URL.

    Returns ``(url, verifier, state)``. Open ``url``, click Authorize, copy the
    displayed code, then call :func:`exchange_manual_code(code, verifier, state)`.

    BULLETPROOF FALLBACK: even if the redirect page fails to load, the
    authorization code is present in the browser address bar
    (``.../callback?code=...``); paste that ``code`` value here.
    """
    verifier, challenge, state = _pkce()
    url = _build_authorize_url(challenge, state, manual=True)
    return url, verifier, state
