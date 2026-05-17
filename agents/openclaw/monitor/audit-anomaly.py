"""
Audit-log anomaly detector.

Watches /audit/*.jsonl in real time. Raises an alert when any of these
"shouldn't happen, ever" conditions occurs.

Rules implemented:
    R1  Write attempt on the read-only DB     → HIGH (likely compromise)
    R2  Egress to host outside allowlist      → HIGH
    R3  PR opened on a branch != agent/*      → CRIT (token misused)
    R4  More than N tool calls in 60s         → WARN (loop)
    R5  Lifecycle restart loop (>3 / 10min)   → HIGH
    R6  Skill loaded from outside /opt/openclaw/skills → CRIT
    R7  HMAC reject burst (>20 / minute)      → WARN  (someone scanning)
    R8  Secret-shaped string in tool output   → HIGH  (potential leak)

Each rule has a cooldown to avoid alert flooding.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterator

import alerts

AUDIT_DIR = Path("/audit")
LIFECYCLE = AUDIT_DIR / "lifecycle.jsonl"
TOOL_CALLS = AUDIT_DIR / "tool_calls.jsonl"
PROXY_LOG = AUDIT_DIR / "proxy.jsonl"

POLL_SECONDS = 5
COOLDOWN_SECONDS = 600        # one alert per rule per 10 minutes max
TOOL_RATE_WINDOW = 60
TOOL_RATE_THRESHOLD = 100

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[a-zA-Z0-9_-]{40,}"),       # Anthropic
    re.compile(r"github_pat_[A-Za-z0-9_]{60,}"),    # GitHub fine-grained PAT
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),            # GitHub classic PAT
    re.compile(r"xoxb-[A-Za-z0-9-]{30,}"),          # Slack bot token
    re.compile(r"AKIA[0-9A-Z]{16}"),                # AWS access key id
]

_last_alert: dict[str, float] = {}
_recent_tool_calls: deque[float] = deque()
_recent_restarts: deque[float] = deque()
_recent_hmac_rejects: deque[float] = deque()


def cooldown_ok(rule_id: str) -> bool:
    now = time.time()
    if now - _last_alert.get(rule_id, 0) < COOLDOWN_SECONDS:
        return False
    _last_alert[rule_id] = now
    return True


def tail(path: Path) -> Iterator[dict[str, Any]]:
    """Generator that yields parsed JSON lines as they are appended."""
    last_size = 0
    while True:
        if not path.exists():
            yield from ()
            time.sleep(POLL_SECONDS)
            continue
        size = path.stat().st_size
        if size < last_size:
            last_size = 0   # log rotated
        if size > last_size:
            with path.open("r", encoding="utf-8") as fh:
                fh.seek(last_size)
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
                last_size = fh.tell()
        time.sleep(POLL_SECONDS)


def check_lifecycle(record: dict[str, Any]) -> None:
    if record.get("event") == "startup":
        now = time.time()
        _recent_restarts.append(now)
        while _recent_restarts and now - _recent_restarts[0] > 600:
            _recent_restarts.popleft()
        if len(_recent_restarts) > 3 and cooldown_ok("R5"):
            alerts.high(
                "openclaw restart loop",
                f"{len(_recent_restarts)} restarts in the last 10 minutes — "
                "container is crashing repeatedly.",
            )


def check_tool_call(record: dict[str, Any]) -> None:
    tool = record.get("tool", "")
    args = record.get("args", {})
    output = record.get("output", "")

    # R1: write attempt on read-only DB
    if tool in {"db_query", "postgres"} and isinstance(args, dict):
        sql = str(args.get("sql", "")).lower()
        if any(kw in sql for kw in ("insert ", "update ", "delete ", "truncate ", "drop ", "alter ")):
            if cooldown_ok("R1"):
                alerts.high(
                    "DB write attempted by agent",
                    "agent_ro is read-only; this should never happen.",
                    sql=sql[:200],
                )

    # R2: egress to non-allowlisted host
    if tool in {"web_fetch", "browser_navigate", "http_request"}:
        url = str(args.get("url", "") if isinstance(args, dict) else "")
        denied = bool(record.get("denied")) or "egress-deny" in str(output)
        if denied and cooldown_ok("R2"):
            alerts.high(
                "Egress denied to non-allowlisted host",
                "A skill tried to reach a host outside the allowlist.",
                url=url[:200],
            )

    # R3: PR opened on a branch != agent/*
    if tool in {"github_open_pr", "gh_pr_create"}:
        head = str(args.get("head", "") if isinstance(args, dict) else "")
        if head and not head.startswith("agent/") and cooldown_ok("R3"):
            alerts.crit(
                "PR opened on non-agent branch",
                "Token misuse — agent should only push to agent/* branches.",
                head=head,
            )

    # R4: tool-call rate (loop detection)
    now = time.time()
    _recent_tool_calls.append(now)
    while _recent_tool_calls and now - _recent_tool_calls[0] > TOOL_RATE_WINDOW:
        _recent_tool_calls.popleft()
    if len(_recent_tool_calls) > TOOL_RATE_THRESHOLD and cooldown_ok("R4"):
        alerts.warn(
            "Tool-call rate spike",
            f"{len(_recent_tool_calls)} tool calls in last {TOOL_RATE_WINDOW}s — possible loop.",
        )

    # R6: skill loaded from outside the baked-in path
    if tool == "load_skill":
        path = str(args.get("path", "") if isinstance(args, dict) else "")
        if path and not path.startswith("/opt/openclaw/skills/") and cooldown_ok("R6"):
            alerts.crit(
                "Skill loaded from unexpected path",
                "Only skills baked into the image at /opt/openclaw/skills are allowed.",
                path=path,
            )

    # R8: secret-shaped string in any tool output
    out_str = output if isinstance(output, str) else json.dumps(output, default=str)[:8_000]
    for pat in SECRET_PATTERNS:
        m = pat.search(out_str)
        if m and cooldown_ok(f"R8::{pat.pattern[:20]}"):
            alerts.high(
                "Secret-shaped value detected in tool output",
                "Possible credential leak. Inspect the audit record immediately.",
                tool=tool,
                pattern=pat.pattern,
                sample=m.group(0)[:8] + "…",
            )
            break


def check_proxy(record: dict[str, Any]) -> None:
    # R7: HMAC reject burst
    if record.get("event") == "reject_hmac":
        now = time.time()
        _recent_hmac_rejects.append(now)
        while _recent_hmac_rejects and now - _recent_hmac_rejects[0] > 60:
            _recent_hmac_rejects.popleft()
        if len(_recent_hmac_rejects) > 20 and cooldown_ok("R7"):
            alerts.warn(
                "HMAC reject burst",
                f"{len(_recent_hmac_rejects)} bad signatures in 60s — scan attempt or "
                "rotated secret out of sync.",
            )


def main() -> None:
    alerts.info(
        "audit-anomaly started",
        "Watching /audit/*.jsonl for security events.",
    )
    # Run three tailers in a round-robin (cheap; this isn't latency critical).
    iters = {
        "lifecycle": tail(LIFECYCLE),
        "tool":      tail(TOOL_CALLS),
        "proxy":     tail(PROXY_LOG),
    }
    while True:
        any_record = False
        for name, it in iters.items():
            try:
                rec = next(it)
            except StopIteration:
                continue
            any_record = True
            try:
                if name == "lifecycle":
                    check_lifecycle(rec)
                elif name == "tool":
                    check_tool_call(rec)
                elif name == "proxy":
                    check_proxy(rec)
            except Exception as exc:  # noqa: BLE001
                alerts.warn("audit-anomaly: check failed", error=repr(exc), rule=name)
        if not any_record:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
