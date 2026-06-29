# Hardened OpenClaw image for SpesaSmart agents.
# - Non-root user
# - No build tools at runtime
# - Egress controlled at compose level (cap_drop + custom network)
# - Read-only filesystem except /data and /audit

FROM node:22-alpine AS base

# Security: install only what we need, no shell utilities by default
RUN apk add --no-cache \
        ca-certificates \
        tini \
        dumb-init \
        curl \
        jq \
    && addgroup -S openclaw -g 10001 \
    && adduser -S openclaw -G openclaw -u 10001 -h /home/openclaw -s /sbin/nologin

# Install OpenClaw runtime
# NOTE: pin to a specific version once available; auto-update is disabled
ARG OPENCLAW_VERSION=latest
ENV OPENCLAW_VERSION=${OPENCLAW_VERSION}
RUN curl -fsSL https://openclaw.ai/install.sh -o /tmp/install.sh \
    && sha256sum /tmp/install.sh \
    && OPENCLAW_NO_AUTOUPDATE=1 sh /tmp/install.sh \
    && rm /tmp/install.sh

# Skills baked into image: ONLY ours, no community registry
COPY --chown=openclaw:openclaw skills/ /opt/openclaw/skills/

# Strip skill auto-update from config
RUN echo '{"auto_update_skills": false, "registry_enabled": false}' \
    > /etc/openclaw/policy.json \
    && chown openclaw:openclaw /etc/openclaw/policy.json \
    && chmod 0444 /etc/openclaw/policy.json

# Entry-point drops privileges further and validates env
COPY --chown=openclaw:openclaw entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0555 /usr/local/bin/entrypoint.sh

USER openclaw
WORKDIR /home/openclaw

# Persistent state lives only here (mounted volume)
VOLUME ["/data", "/audit"]

# OpenClaw API listens on 127.0.0.1 only; reverse proxy / SSH tunnel for remote
EXPOSE 5234

ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["openclaw", "serve", "--bind", "127.0.0.1:5234", "--policy", "/etc/openclaw/policy.json"]
