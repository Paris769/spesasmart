"""
Telegram alert dispatcher — shared module used by spend-watchdog.py and
audit-anomaly.py.

Severity levels (the icon is what you'll see in Telegram):
    INFO    ℹ️   non-urgent, daily summary
    WARN    ⚠️   needs attention this hour (e.g. spend > 50% of cap)
    HIGH    🚨   needs attention now (e.g. egress denied, PR on main)
    CRIT    🔥   kill-switch fired, immediate intervention

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from env (loaded by Doppler).
Never logs the bot token. Never includes secrets in message bodies.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Literal

import httpx

Severity = Literal["INFO", "WARN", "HIGH", "CRIT"]

_ICONS: dict[Severity, str] = {
    "INFO": "ℹ️",
    "WARN": "⚠️",
    "HIGH": "🚨",
    "CRIT": "🔥",
}

_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
_TELEGRAM_API = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"

# Stdout audit-trail is written even if Telegram is unreachable, so we never
# silently swallow a security event.
_AUDIT_PATH = "/audit/alerts.jsonl"


def send(severity: Severity, title: str, body: str = "", **fields: object) -> None:
    """Send an alert. Never raises — failure to alert must not crash the caller."""
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "severity": severity,
        "title": title,
        "body": body,
        "fields": fields,
    }
    _write_audit(record)

    if not (_BOT_TOKEN and _CHAT_ID):
        print(f"[alerts] no telegram config; record={record}", file=sys.stderr)
        return

    text = _format_message(severity, title, body, fields)
    try:
        # Short timeout: if Telegram is down we don't want to block the
        # monitor for 30 seconds; the audit log still has the record.
        httpx.post(
            _TELEGRAM_API,
            data={
                "chat_id": _CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": "true",
            },
            timeout=5.0,
        )
    except Exception as exc:  # noqa: BLE001 — alerts must never raise
        print(f"[alerts] telegram failed: {exc!r}", file=sys.stderr)


def _format_message(severity: Severity, title: str, body: str, fields: dict[str, object]) -> str:
    icon = _ICONS.get(severity, "•")
    parts = [f"{icon} *{severity}* — {_escape(title)}"]
    if body:
        parts.append("")
        parts.append(_escape(body))
    if fields:
        parts.append("")
        for k, v in fields.items():
            parts.append(f"`{k}`: `{_escape(str(v))}`")
    return "\n".join(parts)


def _escape(s: str) -> str:
    # Markdown v1 escape: just neutralize the few characters that break parsing.
    return (
        s.replace("\\", "\\\\")
         .replace("`", "'")
         .replace("*", "·")
         .replace("_", "‗")
    )


def _write_audit(record: dict[str, object]) -> None:
    try:
        with open(_AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # Audit volume not mounted in some test contexts — degrade gracefully.
        print(f"[alerts] audit write failed: {record}", file=sys.stderr)


# Convenience wrappers — read like English at the call site.
def info(title: str, body: str = "", **fields: object) -> None:
    send("INFO", title, body, **fields)


def warn(title: str, body: str = "", **fields: object) -> None:
    send("WARN", title, body, **fields)


def high(title: str, body: str = "", **fields: object) -> None:
    send("HIGH", title, body, **fields)


def crit(title: str, body: str = "", **fields: object) -> None:
    send("CRIT", title, body, **fields)
