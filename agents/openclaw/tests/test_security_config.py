"""
Verifica statica della configurazione di sicurezza.

Questi test girano in CI e bloccano il merge se qualcuno indebolisce
inavvertitamente il setup hardened. Niente di esoterico: sono assert
testuali sui file di config.

Esegui con:    python -m pytest agents/openclaw/tests/ -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── Dockerfile ──────────────────────────────────────────────────────────────

def test_dockerfile_runs_as_non_root() -> None:
    content = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "USER openclaw" in content, "container must run as non-root user"
    assert re.search(r"adduser .* -u 10001", content), "uid 10001 expected"


def test_dockerfile_registry_disabled() -> None:
    content = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert '"registry_enabled": false' in content
    assert '"auto_update_skills": false' in content


# ── docker-compose ──────────────────────────────────────────────────────────

def test_compose_caps_dropped() -> None:
    content = (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'cap_drop: ["ALL"]' in content, "must drop all Linux capabilities"
    assert "no-new-privileges:true" in content


def test_compose_openclaw_bound_localhost_only() -> None:
    content = (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:5234:5234"' in content, (
        "openclaw API must be bound to localhost only — never expose to internet"
    )


def test_compose_read_only_filesystem() -> None:
    content = (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    # Both openclaw and webhook-proxy must be read_only
    assert content.count("read_only: true") >= 2


def test_compose_spend_cap_present() -> None:
    content = (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "ANTHROPIC_DAILY_USD_CAP" in content
    assert "spend-watchdog" in content, "spend watchdog sidecar is mandatory"


# ── entrypoint ──────────────────────────────────────────────────────────────

def test_entrypoint_refuses_root() -> None:
    content = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert 'id -u' in content and 'refusing to run as root' in content


def test_entrypoint_validates_fine_grained_pat() -> None:
    content = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "github_pat_*" in content, "must reject classic PATs (ghp_*)"


def test_entrypoint_validates_ro_db_user() -> None:
    content = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "agent_ro" in content, (
        "entrypoint must check that DATABASE_URL points to agent_ro"
    )


# ── Postgres role ───────────────────────────────────────────────────────────

def test_db_role_is_strictly_read_only() -> None:
    sql = (ROOT / "db" / "001_readonly_agent_user.sql").read_text(encoding="utf-8")
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql
    assert "GRANT SELECT" in sql
    # Must NOT contain any write grant
    assert re.search(r"GRANT (INSERT|UPDATE|DELETE|TRUNCATE)", sql) is None
    assert "REVOKE INSERT, UPDATE, DELETE, TRUNCATE" in sql
    assert "statement_timeout" in sql, "must cap query duration"


# ── Webhook proxy ───────────────────────────────────────────────────────────

def test_proxy_has_hmac_and_sanitizer() -> None:
    content = (ROOT / "proxy" / "webhook-proxy.ts").read_text(encoding="utf-8")
    assert "timingSafeEqual" in content, "HMAC compare must be constant-time"
    assert "INJECTION_MARKERS" in content
    assert "rateLimit" in content
    assert "MAX_BODY_BYTES" in content


def test_proxy_rejects_unknown_sources() -> None:
    content = (ROOT / "proxy" / "webhook-proxy.ts").read_text(encoding="utf-8")
    assert "allowedSources" in content


# ── Monitor ─────────────────────────────────────────────────────────────────

def test_watchdog_can_kill_container() -> None:
    content = (ROOT / "monitor" / "spend-watchdog.py").read_text(encoding="utf-8")
    assert "docker.from_env()" in content
    assert "container.stop" in content
    assert "MAX_REQUESTS_PER_HOUR" in content, (
        "must have rate-based kill switch independent of billing API"
    )


def test_audit_anomaly_covers_critical_rules() -> None:
    content = (ROOT / "monitor" / "audit-anomaly.py").read_text(encoding="utf-8")
    # Each rule id must be referenced
    for rule in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"):
        assert rule in content, f"rule {rule} missing from anomaly detector"


def test_alerts_module_has_severity_levels() -> None:
    content = (ROOT / "monitor" / "alerts.py").read_text(encoding="utf-8")
    for level in ("info", "warn", "high", "crit"):
        assert f"def {level}(" in content


# ── Egress allowlist ────────────────────────────────────────────────────────

def test_egress_allowlist_does_not_contain_wildcards() -> None:
    content = (ROOT / "policy" / "egress-allowlist.txt").read_text(encoding="utf-8")
    lines = [
        line.split("#", 1)[0].strip()
        for line in content.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    for host in lines:
        assert "*" not in host, f"wildcard in allowlist: {host}"
        assert "/" not in host, f"path in allowlist (only hostnames allowed): {host}"


def test_egress_allowlist_no_dangerous_hosts() -> None:
    content = (ROOT / "policy" / "egress-allowlist.txt").read_text(encoding="utf-8")
    forbidden = [
        "raw.githubusercontent.com",   # arbitrary code download
        "pastebin.com",
        "transfer.sh",
        "0x0.st",
        "ngrok.io",
        "0.0.0.0",
        "169.254.169.254",             # cloud metadata
    ]
    for host in forbidden:
        assert host not in content, f"dangerous host present in allowlist: {host}"


# ── Branch protection ──────────────────────────────────────────────────────

def test_branch_protection_enforces_admins() -> None:
    content = (ROOT / "policy" / "branch-protection.sh").read_text(encoding="utf-8")
    assert "enforce_admins=true" in content
    assert "allow_force_pushes=false" in content
    assert "allow_deletions=false" in content


# ── Doctor skill safety ─────────────────────────────────────────────────────

def test_doctor_skill_has_hard_constraints() -> None:
    content = (ROOT / "skills" / "spesasmart-doctor" / "SKILL.md").read_text(encoding="utf-8")
    assert "rm -rf" in content
    assert "git push --force" in content
    assert "Mai su `main`" in content
    assert "auto-merge" in content.lower()


# ── No secrets accidentally committed ──────────────────────────────────────

@pytest.mark.parametrize("pattern", [
    r"sk-ant-[a-zA-Z0-9_-]{40,}",
    r"github_pat_[A-Za-z0-9_]{60,}",
    r"ghp_[A-Za-z0-9]{36,}",
])
def test_no_real_secrets_in_repo(pattern: str) -> None:
    rx = re.compile(pattern)
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(p in path.parts for p in ("node_modules", "dist", "__pycache__", ".git")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = rx.search(text)
        if match:
            # Allow obvious placeholders
            if any(p in match.group(0).lower() for p in ("changeme", "example", "xxxx")):
                continue
            pytest.fail(f"possible real secret in {path}: {match.group(0)[:20]}…")
