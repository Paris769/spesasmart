#!/usr/bin/env python3
"""
sync_db_url.py — Propagazione "enter-once" di DATABASE_URL per SpesaSmart.

Flusso:
  1. Legge la connection string da .db_url.local (gitignored) oppure da env DATABASE_URL.
  2. Testa la connessione con asyncpg (fail-fast, timeout 5s). Il segreto e' passato
     ad asyncpg via parametri SEPARATI (host/port/user/password/database): nessun
     parsing URI, quindi nessun problema di percent-encoding (#, @, :, /, &).
     Il segreto NON viene MAI stampato.
  3. Se il test passa, propaga la stringa IDENTICA a:
       (a) GitHub Actions secret DATABASE_URL  -> via `gh secret set` su STDIN
       (b) Render env var DATABASE_URL          -> via PUT REST (solo se in env
           ci sono RENDER_API_KEY e RENDER_SERVICE_ID)
  4. Stampa solo esiti OK/FAIL, mai il valore del segreto.

Uso:
  python scripts/sync_db_url.py
  python scripts/sync_db_url.py --no-render     # salta Render anche se le env ci sono
  python scripts/sync_db_url.py --deploy        # dopo Render, scatena un deploy

Env opzionali:
  GITHUB_REPO        default "Paris769/spesasmart"
  RENDER_API_KEY     se presente (+RENDER_SERVICE_ID) abilita la propagazione Render
  RENDER_SERVICE_ID  es. srv-xxxxxxxxxxxx
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

import asyncpg

# --- Costanti progetto SpesaSmart -------------------------------------------------
DEFAULT_REPO = "Paris769/spesasmart"
SECRET_NAME = "DATABASE_URL"
LOCAL_FILE = ".db_url.local"
RENDER_API_BASE = "https://api.render.com/v1"


# --- Lettura della connection string ---------------------------------------------
def leggi_dsn() -> str:
    """
    Sorgente unica del segreto: prima .db_url.local (senza newline finale),
    poi fallback su env DATABASE_URL. Ritorna la stringa esatta, senza modificarla.
    """
    f = Path(LOCAL_FILE)
    if f.is_file():
        # utf-8-sig: rimuove un eventuale BOM (﻿) aggiunto da PowerShell/Notepad,
        # che altrimenti sporcherebbe il DSN.
        dsn = f.read_text(encoding="utf-8-sig").strip()
        if dsn:
            return dsn
    env = os.environ.get("DATABASE_URL")
    if env:
        return env.strip()
    print(f"FAIL: nessuna sorgente trovata ({LOCAL_FILE} mancante e DATABASE_URL non in env)",
          file=sys.stderr)
    sys.exit(1)


def to_postgresql(dsn: str) -> str:
    """
    Normalizza lo schema per asyncpg/psql: rimuove il +asyncpg che SQLAlchemy usa,
    cosi' come fanno gli scraper con .replace('postgresql+asyncpg://','postgresql://').
    NON tocca user/password/host.
    """
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


# --- Test connessione fail-fast (parametri SEPARATI, niente parsing URI) ----------
async def _test_connessione(dsn: str) -> None:
    """
    Scompone il DSN nei singoli campi e li passa ad asyncpg come keyword arguments:
    cosi' la password e' trattata come byte grezzi (nessun percent-encoding richiesto).
    Solleva eccezione in caso di errore; non stampa nulla.
    """
    pg = to_postgresql(dsn)
    parsed = urllib.parse.urlparse(pg)
    user = urllib.parse.unquote(parsed.username or "")
    password = urllib.parse.unquote(parsed.password or "")
    host = parsed.hostname
    port = parsed.port or 5432
    database = (parsed.path or "/postgres").lstrip("/") or "postgres"

    conn = await asyncpg.connect(
        host=host,
        port=port,
        user=user,
        password=password,   # raw, nessun encoding
        database=database,
        ssl="require",       # Supabase richiede TLS
        timeout=5,           # fail-fast su rete/IPv6/host irraggiungibile
    )
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()


def test_connessione(dsn: str) -> bool:
    """Wrapper sincrono: classifica gli errori in modo leggibile, mai stampa il segreto."""
    try:
        asyncio.run(_test_connessione(dsn))
        print("OK: connessione DB riuscita (SELECT 1)")
        return True
    except asyncpg.InvalidPasswordError:
        # 28P01 — sottoclasse di InvalidAuthorizationSpecificationError: va catturata PRIMA
        print("FAIL: password errata (28P01) — il valore in .db_url.local non combacia col DB",
              file=sys.stderr)
    except asyncpg.InvalidAuthorizationSpecificationError as e:
        # 28000 — Supabase: 'Tenant or user not found' (user/ref errato o progetto in pausa)
        print(f"FAIL: utente/tenant non valido ({e.sqlstate}) — controlla user=postgres.<ref> "
              f"o progetto in pausa", file=sys.stderr)
    except (asyncio.TimeoutError, TimeoutError):
        print("FAIL: timeout — usa il Session pooler IPv4 (*.pooler.supabase.com), non il "
              "direct host IPv6", file=sys.stderr)
    except OSError as e:
        print(f"FAIL: errore di rete/socket ({type(e).__name__}, errno={e.errno})", file=sys.stderr)
    except asyncpg.PostgresError as e:
        print(f"FAIL: errore Postgres (SQLSTATE {e.sqlstate}): {type(e).__name__}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: errore inatteso: {type(e).__name__}", file=sys.stderr)
    return False


# --- Propagazione GitHub ----------------------------------------------------------
def push_github(dsn: str, repo: str) -> bool:
    """
    gh secret set DATABASE_URL --repo <repo>  leggendo il valore da STDIN:
    il segreto non compare mai negli argomenti del processo ne' nella shell history.
    """
    try:
        proc = subprocess.run(
            ["gh", "secret", "set", SECRET_NAME, "--repo", repo],
            input=dsn,            # passato su STDIN, non in argv
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        print("FAIL[GitHub]: 'gh' CLI non trovata nel PATH", file=sys.stderr)
        return False
    if proc.returncode != 0:
        print(f"FAIL[GitHub]: gh secret set ha restituito {proc.returncode}: {proc.stderr.strip()}",
              file=sys.stderr)
        return False
    print(f"OK[GitHub]: secret {SECRET_NAME} aggiornato su {repo}")
    return True


# --- Propagazione Render ----------------------------------------------------------
def _render_request(method: str, path: str, api_key: str, body: dict | None = None):
    url = f"{RENDER_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Accept", "application/json")
    req.add_header(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req, timeout=15)


def push_render(dsn: str, api_key: str, service_id: str) -> bool:
    """
    PUT /v1/services/{id}/env-vars/DATABASE_URL  con body {"value": <dsn>}.
    Idempotente (add-or-update). Il JSON e' costruito con json.dumps -> quoting sicuro.
    """
    body = {"value": dsn}
    try:
        resp = _render_request(
            "PUT", f"/services/{service_id}/env-vars/{SECRET_NAME}", api_key, body
        )
    except urllib.error.HTTPError as e:
        print(f"FAIL[Render]: HTTP {e.code} sull'update env var (verifica RENDER_SERVICE_ID/API key)",
              file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"FAIL[Render]: errore di rete ({e.reason})", file=sys.stderr)
        return False
    if resp.status != 200:
        print(f"FAIL[Render]: HTTP {resp.status}", file=sys.stderr)
        return False
    print(f"OK[Render]: env var {SECRET_NAME} aggiornata sul servizio {service_id}")
    return True


def trigger_render_deploy(api_key: str, service_id: str) -> bool:
    """POST /v1/services/{id}/deploys — l'update env var NON ridepoya da solo."""
    try:
        resp = _render_request(
            "POST", f"/services/{service_id}/deploys", api_key, {"clearCache": "do_not_clear"}
        )
    except urllib.error.HTTPError as e:
        print(f"FAIL[Render-deploy]: HTTP {e.code}", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"FAIL[Render-deploy]: errore di rete ({e.reason})", file=sys.stderr)
        return False
    if resp.status not in (200, 201):
        print(f"FAIL[Render-deploy]: HTTP {resp.status}", file=sys.stderr)
        return False
    print(f"OK[Render-deploy]: deploy avviato sul servizio {service_id}")
    return True


# --- Main -------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Propaga DATABASE_URL a GitHub e Render (enter-once).")
    ap.add_argument("--no-render", action="store_true", help="non propagare a Render")
    ap.add_argument("--no-github", action="store_true", help="non propagare a GitHub")
    ap.add_argument("--deploy", action="store_true", help="dopo Render, scatena un deploy")
    args = ap.parse_args()

    repo = os.environ.get("GITHUB_REPO", DEFAULT_REPO)
    dsn = leggi_dsn()

    # 1) TEST FAIL-FAST: niente propagazione se il DB non risponde.
    if not test_connessione(dsn):
        print("ABORT: test connessione fallito, nessuna propagazione eseguita.", file=sys.stderr)
        return 1

    ok = True

    # 2a) GitHub
    if not args.no_github:
        ok = push_github(dsn, repo) and ok

    # 2b) Render (solo se le credenziali sono in env)
    render_key = os.environ.get("RENDER_API_KEY")
    render_sid = os.environ.get("RENDER_SERVICE_ID")
    if not args.no_render:
        if render_key and render_sid:
            ok = push_render(dsn, render_key, render_sid) and ok
            if ok and args.deploy:
                ok = trigger_render_deploy(render_key, render_sid) and ok
        else:
            print("SKIP[Render]: RENDER_API_KEY/RENDER_SERVICE_ID non in env "
                  "(propaga manualmente in dashboard, valore identico a .db_url.local)")

    if ok:
        print("DONE: propagazione completata.")
        return 0
    print("DONE-WITH-ERRORS: alcune propagazioni sono fallite (vedi sopra).", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
