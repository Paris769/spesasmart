#!/usr/bin/env python3
"""
rotate_db_credentials.py — Rotazione END-TO-END della password DB per SpesaSmart.

A differenza di sync_db_url.py (che PROPAGA una password gia' nota), questo script
GENERA una nuova password e la IMPOSTA via Supabase Management API, poi propaga la
nuova DATABASE_URL a GitHub Actions e Render con deploy. Zero passi manuali ricorrenti.

Flusso:
  1. Genera una password ALFANUMERICA forte (>=32, da `secrets`): intrinsecamente
     URL-safe, nessun problema di percent-encoding nel DSN.
  2. PATCH https://api.supabase.com/v1/projects/{ref}/database/password
     (operationId v1-update-database-password) con header Authorization: Bearer
     <SUPABASE_PAT> e body {"password": "<nuova>"}. E' lo stesso meccanismo del
     bottone "Reset database password" della Dashboard.
  3. Attende con retry+backoff esponenziale che il Session pooler accetti la nuova
     password (la propagazione sul pooler NON e' istantanea). Test con asyncpg a
     parametri SEPARATI e ssl="require". Solo InvalidPasswordError => retry; altri
     errori => ABORT.
  4. Solo dopo il test OK propaga la STESSA connection string IDENTICA a:
       (a) GitHub Actions secret DATABASE_URL  -> `gh secret set` su STDIN
       (b) Render env var DATABASE_URL          -> PUT REST (RENDER_API_KEY + _SERVICE_ID)
       (c) Render deploy                        -> POST + poll fino a "live"
  5. Fail-safe: dopo aver cambiato la password, se QUALSIASI propagazione fallisce,
     scrive la nuova DSN in .db_url.local (mai loggata, chmod 600 su POSIX) cosi' lo
     stato e' recuperabile rilanciando scripts/sync_db_url.py. Il caso peggiore
     "password cambiata ma persa" e' cosi' evitato.
  6. Mai stampare/loggare il segreto. Idempotente: ogni run ruota di nuovo (sicuro).

Uso:
  python scripts/rotate_db_credentials.py                 # rotazione completa + deploy
  python scripts/rotate_db_credentials.py --skip-deploy   # ruota e propaga, niente deploy
  python scripts/rotate_db_credentials.py --dry-run       # non cambia nulla, mostra i passi

Credenziali (env oppure file gitignored, env ha priorita'):
  SUPABASE_PAT       Personal Access Token (Bearer, prefisso sbp_).  file: .supabase.pat
  RENDER_API_KEY     Render API key (Bearer).                        file: .render.key
  RENDER_SERVICE_ID  es. srv-d80gk3faqgkc73a3ul50 (default sotto).
  GITHUB_REPO        default "Paris769/spesasmart".
  GH_TOKEN/GITHUB_TOKEN  usato da `gh` per scrivere il secret (in CI: PAT Secrets:write).

Dipendenze: solo stdlib + asyncpg (gia' usato dal progetto).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import asyncpg

# --- Costanti fisse del progetto SpesaSmart ---------------------------------------
SUPABASE_REF = "xlbfgufgprdarwlpziwl"
SUPABASE_API_BASE = "https://api.supabase.com"
RENDER_API_BASE = "https://api.render.com/v1"
DEFAULT_REPO = "Paris769/spesasmart"
DEFAULT_RENDER_SERVICE_ID = "srv-d80gk3faqgkc73a3ul50"
SECRET_NAME = "DATABASE_URL"
LOCAL_FILE = ".db_url.local"

# Cloudflare davanti ad api.supabase.com blocca i client senza User-Agent "da
# browser" (Error 1010). Inviamo sempre un UA realistico sulle chiamate REST.
HTTP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Parametri del Session pooler (NON cambiano al reset password).
DB_HOST = "aws-1-eu-central-1.pooler.supabase.com"
DB_PORT = 5432
DB_USER = f"postgres.{SUPABASE_REF}"        # ruolo postgres "tenant" sul pooler
DB_NAME = "postgres"

# File gitignored da cui leggere i token se non sono in env.
PAT_FILE = ".supabase.pat"
RENDER_KEY_FILE = ".render.key"

# Retry per la propagazione sul pooler (la nuova password non e' istantanea).
TEST_MAX_TENTATIVI = 6
TEST_BACKOFF_BASE = 2.0       # 1, 2, 4, 8, 16, 32 s

# Poll del deploy Render.
DEPLOY_POLL_TIMEOUT = 600     # 10 minuti
DEPLOY_POLL_INTERVAL = 12     # secondi
DEPLOY_STATI_OK = {"live"}
DEPLOY_STATI_KO = {"build_failed", "update_failed", "pre_deploy_failed",
                   "canceled", "deactivated"}


# --- Lettura credenziali (env con fallback su file gitignored) --------------------
def _leggi_credenziale(env_name: str, file_name: str) -> str | None:
    """Env ha priorita'; in mancanza prova il file gitignored (senza newline)."""
    val = os.environ.get(env_name)
    if val and val.strip():
        return val.strip()
    f = Path(file_name)
    if f.is_file():
        # utf-8-sig: rimuove automaticamente un eventuale BOM (﻿) che
        # PowerShell/Notepad su Windows antepongono salvando in UTF-8 — altrimenti
        # finirebbe negli header HTTP/nel DSN rompendo l'auth.
        txt = f.read_text(encoding="utf-8-sig").strip()
        if txt:
            return txt
    return None


# --- Generazione password forte e URL-safe ----------------------------------------
def genera_password(lunghezza: int = 40) -> str:
    """
    Password SOLO alfanumerica (>=32) da secrets: niente caratteri speciali =>
    nessun percent-encoding necessario nel DSN, nessun apostrofo => nessun rischio
    di quoting lato Postgres. Garantisce almeno una minuscola, una maiuscola e una
    cifra (rispettando eventuali policy di robustezza).
    """
    if lunghezza < 32:
        lunghezza = 32
    alfabeto = string.ascii_letters + string.digits
    while True:
        pwd = "".join(secrets.choice(alfabeto) for _ in range(lunghezza))
        if (any(c.islower() for c in pwd)
                and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd)):
            return pwd


def componi_dsn(password: str) -> str:
    """
    Costruisce la connection string nel formato atteso dal backend (+asyncpg).
    La password e' alfanumerica => puo' essere inserita raw nell'URL senza encoding.
    """
    return (f"postgresql+asyncpg://{DB_USER}:{password}"
            f"@{DB_HOST}:{DB_PORT}/{DB_NAME}")


# --- Supabase Management API: imposta la password del ruolo postgres --------------
def supabase_set_password(pat: str, password: str) -> bool:
    """
    PATCH /v1/projects/{ref}/database/password  body {"password": "..."}.
    Header Authorization: Bearer <PAT>. Non logga mai la password.
    """
    url = f"{SUPABASE_API_BASE}/v1/projects/{SUPABASE_REF}/database/password"
    data = json.dumps({"password": password}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PATCH")
    req.add_header("Authorization", f"Bearer {pat}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", HTTP_UA)  # evita Cloudflare 1010
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        # 401 PAT invalido, 403 permessi, 429 rate limit (60 req/min), 500 fallita.
        print(f"FAIL[Supabase]: HTTP {e.code} sul set password "
              f"(verifica SUPABASE_PAT / rate limit 60 req-min)", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"FAIL[Supabase]: errore di rete ({e.reason})", file=sys.stderr)
        return False
    if resp.status != 200:
        print(f"FAIL[Supabase]: HTTP {resp.status} inatteso", file=sys.stderr)
        return False
    print(f"OK[Supabase]: password del ruolo postgres aggiornata (ref {SUPABASE_REF})")
    return True


# --- Test connessione asyncpg con retry+backoff (attende il pooler) ---------------
async def _prova_connessione(password: str) -> None:
    """
    Connette al Session pooler con parametri SEPARATI (password raw, nessun parsing
    URI) e ssl='require'. Solleva eccezione in caso di errore; non stampa nulla.
    """
    conn = await asyncpg.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=password,    # raw, alfanumerica => nessun encoding
        database=DB_NAME,
        ssl="require",
        timeout=5,
    )
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()


def attendi_pooler(password: str) -> bool:
    """
    Retry+backoff: SOLO InvalidPasswordError (28P01) => il pooler non vede ancora la
    nuova password, riprova. Qualsiasi altro errore => ABORT (problema reale).
    """
    for tentativo in range(1, TEST_MAX_TENTATIVI + 1):
        try:
            asyncio.run(_prova_connessione(password))
            print(f"OK: il pooler accetta la nuova password (tentativo {tentativo})")
            return True
        except asyncpg.InvalidPasswordError:
            # 28P01 — propagazione del pooler non ancora completata: attendi e ritenta.
            if tentativo == TEST_MAX_TENTATIVI:
                print("FAIL: il pooler continua a rifiutare la nuova password (28P01) "
                      "dopo i retry", file=sys.stderr)
                return False
            attesa = TEST_BACKOFF_BASE ** (tentativo - 1)
            print(f"... pooler non ancora pronto (28P01), retry {tentativo}/"
                  f"{TEST_MAX_TENTATIVI} tra {attesa:.0f}s")
            time.sleep(attesa)
        except asyncpg.InvalidAuthorizationSpecificationError as e:
            print(f"FAIL: utente/tenant non valido ({e.sqlstate}) — user atteso "
                  f"{DB_USER} o progetto in pausa", file=sys.stderr)
            return False
        except (asyncio.TimeoutError, TimeoutError):
            print("FAIL: timeout — verifica il Session pooler IPv4 "
                  "(*.pooler.supabase.com)", file=sys.stderr)
            return False
        except OSError as e:
            print(f"FAIL: errore di rete/socket ({type(e).__name__}, errno={e.errno})",
                  file=sys.stderr)
            return False
        except asyncpg.PostgresError as e:
            print(f"FAIL: errore Postgres (SQLSTATE {e.sqlstate}): {type(e).__name__}",
                  file=sys.stderr)
            return False
    return False


# --- Fail-safe: salva la nuova DSN in locale per il recupero ----------------------
def salva_dsn_locale(dsn: str) -> None:
    """
    Scrive la nuova connection string in .db_url.local (gitignored), permessi 600 su
    POSIX. NON logga il contenuto. Serve a recuperare lo stato (rilancia sync_db_url.py)
    se la propagazione fallisce DOPO aver gia' cambiato la password sul DB.
    """
    p = Path(LOCAL_FILE)
    p.write_text(dsn, encoding="utf-8")  # senza newline finale
    try:
        os.chmod(p, 0o600)               # no-op/ininfluente su Windows
    except OSError:
        pass
    print(f"INFO: nuova connection string salvata in {LOCAL_FILE} "
          f"(recupero possibile con scripts/sync_db_url.py)")


# --- Propagazione GitHub ----------------------------------------------------------
def push_github(dsn: str, repo: str) -> bool:
    """`gh secret set DATABASE_URL --repo <repo>` con il valore su STDIN (mai in argv)."""
    try:
        proc = subprocess.run(
            ["gh", "secret", "set", SECRET_NAME, "--repo", repo],
            input=dsn,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        print("FAIL[GitHub]: 'gh' CLI non trovata nel PATH", file=sys.stderr)
        return False
    if proc.returncode != 0:
        print(f"FAIL[GitHub]: gh secret set ha restituito {proc.returncode}: "
              f"{proc.stderr.strip()}", file=sys.stderr)
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
    req.add_header("User-Agent", HTTP_UA)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req, timeout=20)


def push_render(dsn: str, api_key: str, service_id: str) -> bool:
    """PUT /v1/services/{id}/env-vars/DATABASE_URL body {"value": <dsn>} (idempotente)."""
    try:
        resp = _render_request(
            "PUT", f"/services/{service_id}/env-vars/{SECRET_NAME}", api_key, {"value": dsn}
        )
    except urllib.error.HTTPError as e:
        print(f"FAIL[Render]: HTTP {e.code} sull'update env var "
              f"(verifica RENDER_SERVICE_ID/API key)", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"FAIL[Render]: errore di rete ({e.reason})", file=sys.stderr)
        return False
    if resp.status != 200:
        print(f"FAIL[Render]: HTTP {resp.status}", file=sys.stderr)
        return False
    print(f"OK[Render]: env var {SECRET_NAME} aggiornata sul servizio {service_id}")
    return True


def trigger_render_deploy(api_key: str, service_id: str) -> str | None:
    """POST /v1/services/{id}/deploys — ritorna il deploy id (dep-...) o None."""
    try:
        resp = _render_request(
            "POST", f"/services/{service_id}/deploys", api_key,
            {"clearCache": "do_not_clear"},
        )
        payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"FAIL[Render-deploy]: HTTP {e.code}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        print(f"FAIL[Render-deploy]: errore di rete ({e.reason})", file=sys.stderr)
        return None
    except (ValueError, KeyError):
        print("FAIL[Render-deploy]: risposta non interpretabile", file=sys.stderr)
        return None
    if resp.status not in (200, 201, 202):
        print(f"FAIL[Render-deploy]: HTTP {resp.status}", file=sys.stderr)
        return None
    dep_id = payload.get("id")
    print(f"OK[Render-deploy]: deploy avviato ({dep_id}) sul servizio {service_id}")
    return dep_id


def poll_render_deploy(api_key: str, service_id: str, deploy_id: str) -> bool:
    """GET /v1/services/{id}/deploys/{depId} fino a stato terminale (live / fallito)."""
    scadenza = time.monotonic() + DEPLOY_POLL_TIMEOUT
    while time.monotonic() < scadenza:
        try:
            resp = _render_request(
                "GET", f"/services/{service_id}/deploys/{deploy_id}", api_key
            )
            stato = json.loads(resp.read().decode("utf-8")).get("status", "")
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
            print(f"... poll deploy: errore transitorio ({type(e).__name__}), riprovo",
                  file=sys.stderr)
            time.sleep(DEPLOY_POLL_INTERVAL)
            continue
        print(f"... deploy status={stato}")
        if stato in DEPLOY_STATI_OK:
            print("OK[Render-deploy]: deploy LIVE")
            return True
        if stato in DEPLOY_STATI_KO:
            print(f"FAIL[Render-deploy]: stato terminale di fallimento '{stato}'",
                  file=sys.stderr)
            return False
        time.sleep(DEPLOY_POLL_INTERVAL)
    print("FAIL[Render-deploy]: timeout di polling raggiunto", file=sys.stderr)
    return False


# --- Main -------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ruota la password DB (Supabase Management API) e propaga "
                    "DATABASE_URL a GitHub e Render con deploy."
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="non cambia nulla: mostra solo i passi che eseguirebbe")
    ap.add_argument("--skip-deploy", action="store_true",
                    help="propaga senza far ripartire il deploy Render")
    args = ap.parse_args()

    repo = os.environ.get("GITHUB_REPO", DEFAULT_REPO)
    render_sid = os.environ.get("RENDER_SERVICE_ID", DEFAULT_RENDER_SERVICE_ID)

    # Credenziali necessarie (fail-fast PRIMA di toccare il DB).
    pat = _leggi_credenziale("SUPABASE_PAT", PAT_FILE)
    if not pat:
        print(f"FAIL: SUPABASE_PAT non in env ne' in {PAT_FILE} "
              f"(crea un PAT su https://supabase.com/dashboard/account/tokens)",
              file=sys.stderr)
        return 1
    render_key = _leggi_credenziale("RENDER_API_KEY", RENDER_KEY_FILE)

    if args.dry_run:
        print("DRY-RUN: passi previsti:")
        print(f"  1. genera password alfanumerica (>=32) via secrets")
        print(f"  2. PATCH {SUPABASE_API_BASE}/v1/projects/{SUPABASE_REF}/database/password")
        print(f"  3. attendi pooler {DB_HOST}:{DB_PORT} (user {DB_USER}) con backoff")
        print(f"  4. gh secret set {SECRET_NAME} --repo {repo}")
        if render_key:
            print(f"  5. Render PUT env-var + " +
                  ("(deploy SALTATO)" if args.skip_deploy else f"POST deploy su {render_sid}"))
        else:
            print("  5. Render SALTATO (RENDER_API_KEY assente)")
        print("DRY-RUN: nessuna modifica effettuata.")
        return 0

    # 1) Genera la nuova password e la DSN corrispondente.
    nuova_pwd = genera_password()
    nuova_dsn = componi_dsn(nuova_pwd)

    # 2) Imposta la password su Supabase (da qui il DB e' cambiato: attiva fail-safe).
    if not supabase_set_password(pat, nuova_pwd):
        print("ABORT: impossibile impostare la password su Supabase, nulla e' cambiato.",
              file=sys.stderr)
        return 1

    # 3) Attendi che il pooler accetti la nuova password (retry+backoff).
    if not attendi_pooler(nuova_pwd):
        # Password GIA' cambiata sul DB: salva per recupero, poi esci.
        salva_dsn_locale(nuova_dsn)
        print("ABORT: il pooler non accetta la nuova password. La nuova DSN e' in "
              f"{LOCAL_FILE}: una volta propagata, rilancia scripts/sync_db_url.py.",
              file=sys.stderr)
        return 1

    # 4) Propagazione. Da qui ogni fallimento e' RECUPERABILE: salva subito la DSN.
    ok = True

    if not push_github(nuova_dsn, repo):
        ok = False

    if render_key:
        if not push_render(nuova_dsn, render_key, render_sid):
            ok = False
        elif not args.skip_deploy:
            dep_id = trigger_render_deploy(render_key, render_sid)
            if dep_id is None:
                ok = False
            elif not poll_render_deploy(render_key, render_sid, dep_id):
                ok = False
    else:
        print("SKIP[Render]: RENDER_API_KEY assente (env o .render.key) — "
              "propaga manualmente in dashboard il valore di .db_url.local")
        ok = False  # la propagazione non e' completa senza Render

    if not ok:
        # Stato del DB gia' ruotato ma propagazione incompleta: salva per recupero.
        salva_dsn_locale(nuova_dsn)
        print("DONE-WITH-ERRORS: password ruotata ma propagazione incompleta. "
              f"La nuova DSN e' in {LOCAL_FILE}: rilancia scripts/sync_db_url.py "
              "per completare.", file=sys.stderr)
        return 1

    print("DONE: rotazione e propagazione completate, deploy LIVE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
