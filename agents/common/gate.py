"""
Gate di interazione con l'esterno — il CONFINE di sicurezza della rete agenti.

Gli agenti NON pubblicano contenuti, NON spendono denaro, NON mergiano codice e
NON toccano credenziali social/ads/pagamenti. L'unica azione "verso l'esterno"
permessa è aprire/aggiornare una **Issue GitHub** di proposta nel repo del
progetto: un essere umano la legge e decide. Le proposte che implicano azioni
pubbliche/finanziarie/legali sono marcate con l'etichetta `needs-human`.

Richiede `gh` autenticata (in CI: GITHUB_TOKEN con permesso issues:write).
Fallisce in modo silenzioso (gli agenti non devono rompersi se gh non c'è).
"""
import json
import os
import subprocess

REPO = os.getenv("GITHUB_REPO", "Paris769/spesasmart")

# Etichette usate dagli agenti (create idempotentemente).
LABELS = {
    "agent": "5319e7",
    "growth": "0e8a16",
    "product": "1d76db",
    "backlog/auto": "c5def5",
    "needs-human": "d93f0b",
    "channel:social": "fbca04",
}


def _gh(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["gh", *args], input=stdin, text=True, capture_output=True
        )
    except FileNotFoundError:
        print("gate: 'gh' non trovata — salto l'interazione GitHub")
        return subprocess.CompletedProcess(args, 1, "", "gh missing")


def ensure_labels() -> None:
    for name, color in LABELS.items():
        _gh(["label", "create", name, "--color", color, "--force", "--repo", REPO])


def upsert_issue(title: str, body: str, labels: list[str] | None = None) -> str | None:
    """
    Crea o aggiorna (per titolo esatto) una Issue. Idempotente: gli agenti
    girano spesso e NON devono spammare — aggiornano l'issue "vivente".
    """
    ensure_labels()
    found = _gh([
        "issue", "list", "--repo", REPO, "--state", "open",
        "--search", f'in:title "{title}"', "--json", "number,title", "--limit", "30",
    ])
    num = None
    try:
        for it in json.loads(found.stdout or "[]"):
            if it.get("title") == title:
                num = it["number"]
                break
    except (ValueError, KeyError):
        pass

    if num:
        r = _gh(["issue", "edit", str(num), "--repo", REPO, "--body-file", "-"], stdin=body)
        if r.returncode == 0:
            print(f"gate: issue #{num} aggiornata — {title}")
        return str(num)

    args = ["issue", "create", "--repo", REPO, "--title", title, "--body-file", "-"]
    for lab in (labels or []):
        args += ["--label", lab]
    r = _gh(args, stdin=body)
    out = (r.stdout or "").strip()
    print(f"gate: issue creata — {title} ({out or r.stderr.strip()})")
    return out or None
