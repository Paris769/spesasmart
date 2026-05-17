#!/usr/bin/env bash
# Apply GitHub branch-protection rules so the agent token CANNOT push to main.
#
# What this script enforces on `main`:
#   - No direct pushes (everything via PR)
#   - At least 1 reviewer required
#   - Stale reviews dismissed on new commits
#   - Status checks `test-frontend`, `test-backend` must pass
#   - Force-push and deletion forbidden
#   - Admins are NOT bypassed (you too need to PR)
#
# What it enforces on `agent/*`:
#   - No protection — agents create/push freely there
#   - But the only way to merge into `main` is via PR, which goes through
#     the protected `main` rules above.
#
# Requires: gh auth login as a repo admin

set -euo pipefail

REPO="${REPO:-Paris769/spesasmart}"
BRANCH="${BRANCH:-main}"

echo "→ Applying branch protection on $REPO:$BRANCH"

gh api -X PUT "/repos/$REPO/branches/$BRANCH/protection" \
    -H "Accept: application/vnd.github+json" \
    -f required_status_checks[strict]=true \
    -f 'required_status_checks[contexts][]=test-frontend' \
    -f 'required_status_checks[contexts][]=test-backend' \
    -f enforce_admins=true \
    -f 'required_pull_request_reviews[required_approving_review_count]=1' \
    -f 'required_pull_request_reviews[dismiss_stale_reviews]=true' \
    -f 'required_pull_request_reviews[require_code_owner_reviews]=false' \
    -f restrictions= \
    -f required_linear_history=true \
    -f allow_force_pushes=false \
    -f allow_deletions=false \
    -f required_conversation_resolution=true \
    > /tmp/branch-protection.json

echo "✓ main is protected:"
jq -r '
  "  - PR review required: " + (.required_pull_request_reviews.required_approving_review_count | tostring),
  "  - Status checks: " + (.required_status_checks.contexts | join(", ")),
  "  - Force-push allowed: " + (.allow_force_pushes.enabled | tostring),
  "  - Admins enforced: " + (.enforce_admins.enabled | tostring)
' /tmp/branch-protection.json

cat <<'EOF'

NEXT STEPS (manual, in the GitHub UI):
  1. Settings → Developer settings → Personal access tokens → Fine-grained
  2. Create a new token named "openclaw-agent"
  3. Repository access: only `Paris769/spesasmart`
  4. Permissions:
       - Contents: Read and Write
       - Pull requests: Read and Write
       - Metadata: Read
       (everything else: NONE)
  5. Expiration: 90 days max (and set a calendar reminder to rotate)
  6. Copy the token into Doppler as GITHUB_AGENT_TOKEN

Then create a GitHub Ruleset to forbid the agent token from pushing
anywhere except `agent/*`:

  Settings → Rules → Rulesets → New ruleset
    Name: "agent-token-confined"
    Target: this repository
    Branch targeting: include "**"  exclude "agent/**"
    Bypass: nobody
    Rules: Restrict pushes → bypass actors: (you, but NOT the token)

This double layer (branch protection + ruleset) means even if the agent
goes haywire, it physically cannot push to `main`.
EOF
