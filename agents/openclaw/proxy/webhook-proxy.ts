/**
 * OpenClaw webhook proxy.
 *
 * Purpose: every webhook that triggers an agent (Sentry, GitHub, internal cron)
 * MUST pass through this proxy. The OpenClaw API is bound to 127.0.0.1 and
 * never directly reachable.
 *
 * Security controls applied here:
 *   1. HMAC signature verification (per source)
 *   2. IP allowlist (optional, env ALLOWED_SOURCE_IPS)
 *   3. Rate limit (default 30 req/min/IP)
 *   4. Payload sanitization — strips known prompt-injection markers from
 *      stack traces, error messages, user-controlled fields
 *   5. Size cap (256 KB) — refuse oversized payloads
 *   6. Audit log line for every accepted/rejected hook
 *
 * This file is INTENTIONALLY conservative: when in doubt, reject (HTTP 4xx)
 * and alert. Better a missed Sentry event than a prompt-injection exploit.
 */

import Fastify from "fastify";
import rateLimit from "@fastify/rate-limit";
import { createHmac, timingSafeEqual } from "node:crypto";
import { request as undiciRequest } from "undici";

const HMAC_SECRET = mustEnv("WEBHOOK_HMAC_SECRET");
const OPENCLAW_URL = mustEnv("OPENCLAW_INTERNAL_URL");
const RATE_PER_MIN = Number(process.env.RATE_LIMIT_PER_MINUTE ?? "30");
const ALLOWED_IPS = (process.env.ALLOWED_SOURCE_IPS ?? "")
  .split(",").map(s => s.trim()).filter(Boolean);
const MAX_BODY_BYTES = 256 * 1024;

const fastify = Fastify({
  logger: { level: "info" },
  bodyLimit: MAX_BODY_BYTES,
  trustProxy: true,
});

await fastify.register(rateLimit, {
  max: RATE_PER_MIN,
  timeWindow: "1 minute",
  keyGenerator: (req) => (req.ip ?? "unknown"),
});

// ──────────────────────────────────────────────────────────────────────────
// Prompt-injection sanitizer.
//
// Stack traces and error messages from Sentry are USER-CONTROLLED data (an
// attacker can craft a request that crashes with a specific error message).
// We strip the most common injection markers BEFORE feeding the payload to
// OpenClaw. We do NOT try to be exhaustive: the real defense is the read-only
// DB user and branch protection.
const INJECTION_MARKERS: RegExp[] = [
  /ignore (all )?previous (instructions|prompts)/gi,
  /system prompt[:]/gi,
  /you (must|should|will) (now|immediately)/gi,
  /\bDROP\s+TABLE\b/gi,
  /\bDELETE\s+FROM\b/gi,
  /\bTRUNCATE\s+TABLE\b/gi,
  /git\s+push\s+--force/gi,
  /rm\s+-rf/gi,
  /chmod\s+\d{3,4}/gi,
  /sudo\s+\w+/gi,
  // Anthropic / OpenAI tag-jail tokens that an attacker may inject
  /<\/?(system|user|assistant|s)>/gi,
  /\[INST\]|\[\/INST\]/g,
];

function sanitize(value: unknown, depth = 0): unknown {
  if (depth > 8) return "[truncated:depth]";
  if (typeof value === "string") {
    let s = value.length > 16_000 ? value.slice(0, 16_000) + "…[truncated]" : value;
    for (const re of INJECTION_MARKERS) s = s.replace(re, "[REDACTED]");
    return s;
  }
  if (Array.isArray(value)) return value.slice(0, 200).map(v => sanitize(v, depth + 1));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    let n = 0;
    for (const [k, v] of Object.entries(value)) {
      if (n++ > 200) { out["__truncated__"] = true; break; }
      out[k] = sanitize(v, depth + 1);
    }
    return out;
  }
  return value;
}

// ──────────────────────────────────────────────────────────────────────────
// HMAC verification (per source).
//
// Sentry signs payloads with HMAC-SHA256 in `Sentry-Hook-Signature`.
// GitHub signs with `X-Hub-Signature-256`.  We accept either header.
function verifyHmac(rawBody: Buffer, headerValue: string | undefined): boolean {
  if (!headerValue) return false;
  // Header may be "sha256=…" (GitHub) or plain hex (Sentry).
  const provided = headerValue.startsWith("sha256=") ? headerValue.slice(7) : headerValue;
  const expected = createHmac("sha256", HMAC_SECRET).update(rawBody).digest("hex");
  if (provided.length !== expected.length) return false;
  try {
    return timingSafeEqual(Buffer.from(provided, "hex"), Buffer.from(expected, "hex"));
  } catch {
    return false;
  }
}

function ipAllowed(ip: string | undefined): boolean {
  if (ALLOWED_IPS.length === 0) return true;
  return !!ip && ALLOWED_IPS.includes(ip);
}

// ──────────────────────────────────────────────────────────────────────────

fastify.get("/health", async () => ({ ok: true }));

// Reject anything that isn't a known hook path.
fastify.addHook("onRequest", async (req, reply) => {
  if (!req.url.startsWith("/hooks/") && req.url !== "/health") {
    reply.code(404).send({ error: "not_found" });
  }
});

// Capture raw body for HMAC.
fastify.addContentTypeParser(
  "application/json",
  { parseAs: "buffer" },
  (_req, body: Buffer, done) => {
    try {
      const json = body.length === 0 ? {} : JSON.parse(body.toString("utf8"));
      done(null, { __raw: body, __parsed: json });
    } catch (err) {
      done(err as Error);
    }
  }
);

interface HookBody { __raw: Buffer; __parsed: unknown; }

fastify.post<{ Params: { source: string }; Body: HookBody }>(
  "/hooks/:source",
  async (req, reply) => {
    const { source } = req.params;
    const allowedSources = new Set(["sentry", "github", "cron", "internal"]);
    if (!allowedSources.has(source)) {
      reply.code(404).send({ error: "unknown_source" });
      return;
    }

    if (!ipAllowed(req.ip)) {
      audit("reject_ip", { ip: req.ip, source });
      reply.code(403).send({ error: "ip_not_allowed" });
      return;
    }

    const headerSig =
      (req.headers["x-hub-signature-256"] as string | undefined) ??
      (req.headers["sentry-hook-signature"] as string | undefined) ??
      (req.headers["x-openclaw-signature"] as string | undefined);

    if (!verifyHmac(req.body.__raw, headerSig)) {
      audit("reject_hmac", { ip: req.ip, source });
      reply.code(401).send({ error: "bad_signature" });
      return;
    }

    const sanitized = sanitize(req.body.__parsed);
    audit("accept", { ip: req.ip, source, bytes: req.body.__raw.length });

    // Forward to OpenClaw internal API. Note: we strip ALL incoming headers
    // except a curated set, and add our own source tag.
    const upstream = await undiciRequest(`${OPENCLAW_URL}/v1/hooks/${source}`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-openclaw-source": source,
        "x-openclaw-proxy": "1",
      },
      body: JSON.stringify({ source, payload: sanitized }),
    });

    reply.code(upstream.statusCode).send(await upstream.body.text());
  }
);

function audit(event: string, details: Record<string, unknown>): void {
  const line = JSON.stringify({
    ts: new Date().toISOString(),
    event,
    ...details,
  });
  // stdout is captured by docker's json-file driver
  process.stdout.write(line + "\n");
}

function mustEnv(name: string): string {
  const v = process.env[name];
  if (!v) {
    console.error(`FATAL: env ${name} missing`);
    process.exit(2);
  }
  return v;
}

const port = Number(process.env.PORT ?? "8443");
fastify.listen({ host: "0.0.0.0", port }).catch((err) => {
  fastify.log.error(err);
  process.exit(1);
});
