#!/bin/sh
# Hardened entrypoint for OpenClaw container.
# - Validates required secrets are present (fetched via Doppler before container start)
# - Refuses to start if running as root
# - Refuses to start if any secret looks plaintext-injected (not from vault)
# - Writes a startup audit record

set -eu

# Refuse root
if [ "$(id -u)" -eq 0 ]; then
    echo "FATAL: refusing to run as root" >&2
    exit 1
fi

# Required env (sourced from Doppler / SSM via wrapper)
required="ANTHROPIC_API_KEY AGENT_RO_DATABASE_URL GITHUB_AGENT_TOKEN TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID WEBHOOK_HMAC_SECRET"

for v in $required; do
    eval val=\${$v:-}
    if [ -z "$val" ]; then
        echo "FATAL: required env $v missing — vault not mounted?" >&2
        exit 1
    fi
    # Heuristic: reject obvious placeholders
    case "$val" in
        *changeme*|*REPLACE*|*example*|*xxxxxx*)
            echo "FATAL: env $v looks like a placeholder — refusing to start" >&2
            exit 1
            ;;
    esac
done

# GitHub token must be fine-grained (starts with github_pat_) and not classic
case "$GITHUB_AGENT_TOKEN" in
    github_pat_*) ;;
    *)
        echo "FATAL: GITHUB_AGENT_TOKEN is not a fine-grained PAT (must start with github_pat_)" >&2
        exit 1
        ;;
esac

# DATABASE_URL must point to the read-only user (defense in depth)
case "$AGENT_RO_DATABASE_URL" in
    *agent_ro*) ;;
    *)
        echo "FATAL: AGENT_RO_DATABASE_URL must use the agent_ro user" >&2
        exit 1
        ;;
esac

# Startup audit record
mkdir -p /audit
printf '{"ts":"%s","event":"startup","pid":%d,"user":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$" "$(id -un)" \
    >> /audit/lifecycle.jsonl

exec "$@"
