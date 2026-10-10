"""Secure persistence for Claude OAuth credentials (framework-agnostic).

Priority: OS keyring (Windows Credential Manager via DPAPI, macOS Keychain,
Secret Service on Linux) -> DPAPI-encrypted file fallback.

The file fallback lives under %LOCALAPPDATA% (Windows) or ~/.local/share
(other OSes) and is DPAPI-encrypted on Windows. It REFUSES to write inside an
OneDrive- (or other cloud-) synced tree so a synced/backup copy is never a
plaintext or recoverable credential.

Stored payload (dict), persisted as JSON:
  - method:        "oauth" | "setup-token"
  - refresh_token: str        (oauth only; "" for setup-token)
  - access_token:  str | None (setup-token stores the long-lived token here;
                               oauth omits it and re-derives via refresh)
  - expires_at:    float | None (epoch seconds; None = no expiry tracking)
  - scopes:        list[str]

Configuration
-------------
Change APP_NAME / SERVICE / ACCOUNT below to namespace credentials per app, or
override them at runtime via the environment variables documented near each
constant. Different apps SHOULD use distinct SERVICE values so their tokens do
not collide in the OS keyring.
"""
from __future__ import annotations
import os
import io
import json
from pathlib import Path

# --- Configurable identity (override via env if you prefer) -----------------
# A short slug used for the on-disk fallback directory name.
APP_NAME = os.environ.get("CLAUDE_AUTH_APP_NAME", "claude-autogen")
# The keyring "service" name. Distinct per app to avoid credential collisions.
SERVICE = os.environ.get("CLAUDE_AUTH_KEYRING_SERVICE", f"{APP_NAME}/claude-oauth")
# The keyring "account"/username within the service.
ACCOUNT = os.environ.get("CLAUDE_AUTH_KEYRING_ACCOUNT", "default")
# DPAPI description tag (Windows only; cosmetic, shown in Credential Manager).
_DPAPI_DESC = f"{APP_NAME}-claude-oauth"


def _fallback_path() -> Path:
    """Compute the encrypted-file fallback path, refusing cloud-synced trees."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        # Non-Windows / no LOCALAPPDATA: use XDG-ish data dir under the home.
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    p = Path(base) / APP_NAME / "claude_oauth.bin"
    low = str(p).lower()
    # Refuse known cloud-sync roots: a synced credential file defeats the point.
    for marker in ("onedrive", "dropbox", "google drive", "googledrive", "icloud"):
        if marker in low:
            raise RuntimeError(
                f"Refusing to store credentials in a cloud-synced path: {p}. "
                "Set LOCALAPPDATA (or XDG_DATA_HOME) to a non-synced directory."
            )
    return p


def _dpapi_encrypt(data: bytes) -> bytes:
    """DPAPI-encrypt (Windows). Falls back to a PLAIN: marker if unavailable."""
    try:
        import win32crypt  # type: ignore
        return win32crypt.CryptProtectData(data, _DPAPI_DESC, None, None, None, 0)
    except Exception:
        # No DPAPI (non-Windows or pywin32 missing): store as-is. The file is
        # still mode 0600 and outside any cloud-synced tree.
        return b"PLAIN:" + data


def _dpapi_decrypt(blob: bytes) -> bytes:
    """Reverse of :func:`_dpapi_encrypt`."""
    if blob.startswith(b"PLAIN:"):
        return blob[len(b"PLAIN:"):]
    import win32crypt  # type: ignore
    _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data


def save(creds: dict) -> None:
    """Persist the credentials dict (keyring first, encrypted file fallback)."""
    payload = json.dumps(creds, separators=(",", ":"))
    # 1) Try the OS keyring.
    try:
        import keyring  # type: ignore
        keyring.set_password(SERVICE, ACCOUNT, payload)
        # Remove any stale file fallback so there's a single source of truth.
        try:
            _fallback_path().unlink(missing_ok=True)
        except Exception:
            pass
        return
    except Exception:
        pass
    # 2) Encrypted file fallback.
    path = _fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except Exception:
        pass
    blob = _dpapi_encrypt(payload.encode("utf-8"))
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with io.open(fd, "wb") as f:  # fd is owned/closed by this context manager
        f.write(blob)


def load() -> dict | None:
    """Return the stored credentials dict, or None if nothing is stored."""
    # 1) keyring
    try:
        import keyring  # type: ignore
        raw = keyring.get_password(SERVICE, ACCOUNT)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    # 2) encrypted file fallback
    try:
        path = _fallback_path()
        if path.exists():
            blob = path.read_bytes()
            return json.loads(_dpapi_decrypt(blob).decode("utf-8"))
    except Exception:
        pass
    return None


def clear() -> None:
    """Delete the stored credentials from both keyring and file fallback."""
    try:
        import keyring  # type: ignore
        keyring.delete_password(SERVICE, ACCOUNT)
    except Exception:
        pass
    try:
        _fallback_path().unlink(missing_ok=True)
    except Exception:
        pass
