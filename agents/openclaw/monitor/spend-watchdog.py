"""
Anthropic spend watchdog.

Every 60 seconds:
    1. Poll Anthropic Admin API for today's cumulative spend
    2. Compare against ANTHROPIC_DAILY_USD_CAP (default $10)
    3. At 50% — WARN to Telegram
    4. At 80% — HIGH to Telegram
    5. At 100% — kill openclaw container, CRIT alert, refuse restart for the day

Also enforces a SECOND cap based on local audit log:
    - if openclaw makes > MAX_REQUESTS_PER_HOUR Anthropic calls, kill it.
    (covers the case where Anthropic billing API is delayed/unavailable)

Why a separate process and not a feature inside openclaw?
    Defense in depth. If openclaw is jailbroken via prompt injection, the
    attacker controls what openclaw thinks its budget is. A separate watchdog
    with its own credentials and the ability to docker-kill openclaw cannot
    be talked out of doing its job.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import docker  # type: ignore[import-not-found]

import alerts

CAP_USD = float(os.environ.get("ANTHROPIC_DAILY_USD_CAP", "10"))
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
TARGET_CONTAINER = os.environ.get("TARGET_CONTAINER", "openclaw")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
MAX_REQUESTS_PER_HOUR = int(os.environ.get("MAX_REQUESTS_PER_HOUR", "1500"))

AUDIT_LIFECYCLE = Path("/audit/lifecycle.jsonl")
AUDIT_ALERTS = Path("/audit/alerts.jsonl")
KILL_FLAG = Path("/audit/kill_today.flag")

_warned_50 = False
_warned_80 = False
_killed_today: str | None = None


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def fetch_spend_usd() -> float | None:
    """Return today's spend in USD, or None if API is unavailable."""
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
    }
    start = f"{today_utc()}T00:00:00Z"
    try:
        r = httpx.get(
            "https://api.anthropic.com/v1/organizations/usage_report/messages",
            params={"starting_at": start, "bucket_width": "1d"},
            headers=headers,
            timeout=10.0,
        )
        if r.status_code == 404:
            # Admin endpoint not enabled on this key — fall back to the
            # per-request usage we logged ourselves.
            return None
        r.raise_for_status()
        data = r.json()
        # API shape: data.data[].results[].uncached_input_tokens, etc.
        # We don't recompute pricing here — we use the `cost.amount` field if
        # present, otherwise return None and rely on the request-rate cap.
        total = 0.0
        for bucket in data.get("data", []):
            for row in bucket.get("results", []):
                total += float(row.get("cost", {}).get("amount", 0))
        return total
    except Exception as exc:  # noqa: BLE001
        alerts.warn(
            "spend-watchdog: Anthropic API unreachable",
            "Falling back to local request-rate cap.",
            error=repr(exc),
        )
        return None


def count_anthropic_calls_last_hour() -> int:
    """Best-effort count of anthropic tool calls from the audit log."""
    if not AUDIT_LIFECYCLE.exists():
        return 0
    cutoff = time.time() - 3600
    n = 0
    try:
        with AUDIT_LIFECYCLE.open("r", encoding="utf-8") as fh:
            for line in fh:
                if '"anthropic' not in line:
                    continue
                # Cheap timestamp check: line starts with `{"ts":"YYYY-MM-DD…"`
                try:
                    ts = line.split('"ts":"', 1)[1][:20]
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    if t >= cutoff:
                        n += 1
                except (IndexError, ValueError):
                    pass
    except OSError:
        pass
    return n


def kill_openclaw(reason: str) -> None:
    global _killed_today
    if _killed_today == today_utc():
        return
    try:
        client = docker.from_env()
        container = client.containers.get(TARGET_CONTAINER)
        container.stop(timeout=5)
        alerts.crit(
            "openclaw killed by watchdog",
            reason,
            container=TARGET_CONTAINER,
            day=today_utc(),
        )
        KILL_FLAG.write_text(f"{today_utc()}\n{reason}\n")
        _killed_today = today_utc()
    except Exception as exc:  # noqa: BLE001
        alerts.crit(
            "watchdog: FAILED to kill openclaw",
            f"Manual intervention required: docker stop {TARGET_CONTAINER}",
            error=repr(exc),
        )


def tick() -> None:
    global _warned_50, _warned_80

    # Hour-based request-rate cap (independent of billing API).
    calls = count_anthropic_calls_last_hour()
    if calls > MAX_REQUESTS_PER_HOUR:
        kill_openclaw(
            f"Request-rate cap: {calls} Anthropic calls in last hour "
            f"> limit {MAX_REQUESTS_PER_HOUR}. Likely a runaway loop."
        )
        return

    # Spend cap (USD).
    spend = fetch_spend_usd()
    if spend is None:
        return  # Already alerted in fetch_spend_usd.

    pct = (spend / CAP_USD) * 100 if CAP_USD > 0 else 0

    if pct >= 100:
        kill_openclaw(
            f"Daily spend cap exceeded: ${spend:.2f} / ${CAP_USD:.2f}"
        )
    elif pct >= 80 and not _warned_80:
        alerts.high(
            "Anthropic spend at 80% of daily cap",
            f"${spend:.2f} / ${CAP_USD:.2f} used today",
            calls_last_hour=calls,
        )
        _warned_80 = True
    elif pct >= 50 and not _warned_50:
        alerts.warn(
            "Anthropic spend at 50% of daily cap",
            f"${spend:.2f} / ${CAP_USD:.2f} used today",
            calls_last_hour=calls,
        )
        _warned_50 = True


def reset_daily_flags() -> None:
    global _warned_50, _warned_80, _killed_today
    _warned_50 = False
    _warned_80 = False
    if _killed_today and _killed_today != today_utc():
        _killed_today = None
        if KILL_FLAG.exists():
            KILL_FLAG.unlink(missing_ok=True)


def main() -> None:
    alerts.info(
        "spend-watchdog started",
        f"Daily cap ${CAP_USD:.2f}, request-rate cap {MAX_REQUESTS_PER_HOUR}/hour.",
        container=TARGET_CONTAINER,
    )
    last_day = today_utc()
    while True:
        if today_utc() != last_day:
            reset_daily_flags()
            last_day = today_utc()
        try:
            tick()
        except Exception as exc:  # noqa: BLE001
            alerts.high("spend-watchdog tick error", error=repr(exc))
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
