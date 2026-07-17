"""
Digest settimanale della spesa abituale (Fase 2).

Per ogni shopping_lists con is_recurring=TRUE e digest_email:
  1. ricalcola il miglior piano (versione semplificata: per ogni voce ancorata
     a un product_id, il miglior prezzo corrente non in quarantena; le voci
     solo testuali vengono segnalate come "da ancorare");
  2. confronta il totale con l'ultima esecuzione salvata in
     optimization_result.digest_history (storico append, max 12 entry);
  3. calcola il promo check (app/services/promo.py) per le voci ancorate;
  4. valuta i price_alerts/watch attivi della stessa email: se il miglior
     prezzo corrente <= threshold_price, li include nel digest e aggiorna
     last_notified_at/last_triggered;
  5. scrive il markdown in state/digests/YYYY-MM-DD/<list_id>.md e, SE tutte
     le env SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/SMTP_FROM sono
     configurate, invia l'email (HTML semplice via smtplib). Lo stato DB
     (last_digest_at, digest_history, alert notificati) viene committato solo
     se l'invio riesce (o se SMTP non e' configurato); un fallimento SMTP
     lascia la lista non committata e fa uscire lo script con codice != 0.

Connessione DB: come gli agenti (env DATABASE_URL), ma via SQLAlchemy async
per poter riusare app/services/promo.py.

Uso:
    python scripts/weekly_digest.py [--dry-run]
--dry-run: calcola e scrive i file markdown ma NON scrive nulla sul DB
(niente last_digest_at, niente digest_history, niente update degli alert)
e non invia email.
"""
import argparse
import asyncio
import datetime
import json
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# per importare app.services.promo (dipende solo da sqlalchemy)
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.freshness import fresh_price_sql  # noqa: E402
from app.services.promo import compute_promo_check  # noqa: E402

MIN_VALID_PRICE = 0.10
MAX_HISTORY_ENTRIES = 12

DIGESTS_DIR = REPO_ROOT / "state" / "digests"

# clausola di freschezza per-catena (soglie in app/core/freshness.py):
# i prezzi is_current ma troppo vecchi non concorrono a minimi/alert.
_FRESH_PARAMS: dict = {}
_FRESH_SQL = fresh_price_sql(_FRESH_PARAMS, price_alias="pr", chain_alias="c")

_BEST_PRICE_SQL = text(f"""
    SELECT pr.price       AS price,
           pr.product_url AS product_url,
           s.name         AS store_name,
           c.name         AS chain_name,
           c.slug         AS chain_slug
    FROM prices pr
    JOIN stores s ON s.id = pr.store_id AND s.is_active = TRUE
    JOIN chains c ON c.id = s.chain_id
    WHERE pr.product_id = :pid
      AND pr.is_current = TRUE
      AND pr.quarantined = FALSE
      AND pr.price >= :min_price
      AND {_FRESH_SQL}
    ORDER BY pr.price ASC
    LIMIT 1
""")


def _db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL non impostata")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def _mask_email(email: str) -> str:
    """Maschera un'email per i log (le run di Actions sono pubbliche): an***@***.it"""
    local, _, domain = email.partition("@")
    tld = domain.rsplit(".", 1)[-1] if "." in domain else "***"
    return f"{local[:2]}***@***.{tld}"


def _smtp_config() -> dict | None:
    """Ritorna la config SMTP solo se TUTTE le variabili sono presenti."""
    keys = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM"]
    values = {k: os.getenv(k) for k in keys}
    if all(values.values()):
        return values
    return None


async def _compute_plan(db, items: list[dict]) -> dict:
    """Piano semplificato: per ogni voce ancorata il miglior prezzo corrente."""
    per_item: list[dict] = []
    total = 0.0
    chain_stats: dict[str, dict] = {}

    for it in items:
        qty = float(it["quantity"] or 1)
        entry = {"query": it["product_name"], "quantity": qty}
        if it["product_id"] is None:
            entry["status"] = "da_ancorare"
            per_item.append(entry)
            continue

        row = (await db.execute(_BEST_PRICE_SQL, {
            "pid": str(it["product_id"]), "min_price": MIN_VALID_PRICE,
            **_FRESH_PARAMS,
        })).mappings().first()
        if not row:
            entry["status"] = "non_trovato"
            per_item.append(entry)
            continue

        price = float(row["price"])
        subtotal = round(price * qty, 2)
        entry.update({
            "status": "ok",
            "product_id": str(it["product_id"]),
            "resolved_name": it.get("resolved_name"),
            "price": price,
            "subtotal": subtotal,
            "chain_name": row["chain_name"],
            "store_name": row["store_name"],
            "product_url": row["product_url"],
        })
        per_item.append(entry)
        total = round(total + subtotal, 2)

        st = chain_stats.setdefault(row["chain_name"], {"covered": 0, "total": 0.0})
        st["covered"] += 1
        st["total"] = round(st["total"] + subtotal, 2)

    best_chain = None
    if chain_stats:
        best_chain = sorted(
            chain_stats.items(), key=lambda kv: (-kv[1]["covered"], kv[1]["total"])
        )[0][0]

    return {
        "computed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total": total,
        "best_chain": best_chain,
        "per_item": per_item,
    }


async def _triggered_alerts(db, email: str) -> list[dict]:
    """Watch attivi dell'email il cui miglior prezzo corrente e' <= soglia."""
    rows = (await db.execute(
        text(f"""
            SELECT a.id::text AS id, a.product_id, a.threshold_price,
                   p.name AS product_name,
                   (
                       SELECT MIN(pr.price)
                       FROM prices pr
                       JOIN stores s ON s.id = pr.store_id AND s.is_active = TRUE
                       JOIN chains c ON c.id = s.chain_id
                       WHERE pr.product_id = a.product_id
                         AND pr.is_current = TRUE
                         AND pr.quarantined = FALSE
                         AND pr.price >= :min_price
                         AND {_FRESH_SQL}
                   ) AS current_min_price
            FROM price_alerts a
            JOIN products p ON p.id = a.product_id
            WHERE a.is_active = TRUE
              AND a.threshold_price IS NOT NULL
              AND lower(a.email) = :email
        """),
        {"email": email, "min_price": MIN_VALID_PRICE, **_FRESH_PARAMS},
    )).mappings().all()

    triggered = []
    for r in rows:
        if r["current_min_price"] is not None and float(r["current_min_price"]) <= float(r["threshold_price"]):
            triggered.append({
                "id": r["id"],
                "product_name": r["product_name"],
                "threshold_price": float(r["threshold_price"]),
                "current_min_price": float(r["current_min_price"]),
            })
    return triggered


def _render_markdown(lst: dict, plan: dict, prev_total: float | None,
                     promo_checks: list[dict], alerts: list[dict]) -> str:
    lines = [f"# SpesaSmart — Digest: {lst['name']}", ""]
    lines.append(f"_Calcolato il {plan['computed_at'][:16].replace('T', ' ')} UTC_")
    lines.append("")

    lines.append("## Il tuo piano spesa")
    lines.append("")
    lines.append("| Voce | Qta | Prezzo | Subtotale | Dove |")
    lines.append("|---|---:|---:|---:|---|")
    for item in plan["per_item"]:
        if item.get("status") == "ok":
            dove = f"{item['chain_name']} — {item['store_name']}"
            lines.append(
                f"| {item['query']} | {item['quantity']:g} | €{item['price']:.2f} "
                f"| €{item['subtotal']:.2f} | {dove} |"
            )
        elif item.get("status") == "da_ancorare":
            lines.append(f"| {item['query']} | {item['quantity']:g} | — | — | _da ancorare a un prodotto_ |")
        else:
            lines.append(f"| {item['query']} | {item['quantity']:g} | — | — | _nessun prezzo trovato_ |")
    lines.append("")
    lines.append(f"**Totale stimato: €{plan['total']:.2f}**"
                 + (f" — catena migliore: **{plan['best_chain']}**" if plan["best_chain"] else ""))
    lines.append("")

    if prev_total is not None:
        delta = round(plan["total"] - prev_total, 2)
        if delta > 0:
            lines.append(f"📈 Rispetto all'ultimo digest il totale e' salito di €{delta:.2f} (era €{prev_total:.2f}).")
        elif delta < 0:
            lines.append(f"📉 Rispetto all'ultimo digest risparmi €{-delta:.2f} (era €{prev_total:.2f}).")
        else:
            lines.append(f"Totale invariato rispetto all'ultimo digest (€{prev_total:.2f}).")
        lines.append("")

    if promo_checks:
        lines.append("## Promo check")
        lines.append("")
        verdict_label = {
            "true_promo": "✅ vera promo",
            "weak_promo": "🤏 sconto debole",
            "fake_promo": "⚠️ promo gonfiata",
            "insufficient_history": "❓ storico insufficiente",
        }
        for pc in promo_checks:
            label = verdict_label.get(pc["verdict"], pc["verdict"])
            median = f"€{pc['median_60d']:.2f}" if pc.get("median_60d") is not None else "n/d"
            lines.append(
                f"- **{pc['product_name']}** da {pc['chain_name']}: €{pc['current_price']:.2f} "
                f"(mediana 60gg {median}) → {label}"
            )
        lines.append("")

    if alerts:
        lines.append("## 🔔 Avvisi prezzo scattati")
        lines.append("")
        for a in alerts:
            lines.append(
                f"- **{a['product_name']}**: ora a €{a['current_min_price']:.2f} "
                f"(soglia €{a['threshold_price']:.2f})"
            )
        lines.append("")

    lines.append("---")
    lines.append("_SpesaSmart — confronto prezzi supermercati italiani_")
    return "\n".join(lines) + "\n"


def _markdown_to_html(md: str) -> str:
    """Conversione minimale markdown -> HTML (niente dipendenze extra)."""
    import html as html_mod
    out = ["<html><body style='font-family:sans-serif;max-width:640px'>"]
    in_table = False
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [html_mod.escape(c.strip()) for c in stripped.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} and c for c in cells):
                continue  # riga separatore
            if not in_table:
                out.append("<table border='1' cellpadding='6' cellspacing='0'>")
                in_table = True
            out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if stripped.startswith("# "):
            out.append(f"<h1>{html_mod.escape(stripped[2:])}</h1>")
        elif stripped.startswith("## "):
            out.append(f"<h2>{html_mod.escape(stripped[3:])}</h2>")
        elif stripped.startswith("- "):
            out.append(f"<p>• {html_mod.escape(stripped[2:])}</p>")
        elif stripped == "---":
            out.append("<hr>")
        elif stripped:
            out.append(f"<p>{html_mod.escape(stripped)}</p>")
    if in_table:
        out.append("</table>")
    out.append("</body></html>")
    # grassetti markdown **x**
    html_out = "\n".join(out)
    while "**" in html_out:
        html_out = html_out.replace("**", "<b>", 1).replace("**", "</b>", 1)
    return html_out


def _send_email(smtp: dict, to_addr: str, subject: str, md_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp["SMTP_FROM"]
    msg["To"] = to_addr
    msg.attach(MIMEText(md_body, "plain", "utf-8"))
    msg.attach(MIMEText(_markdown_to_html(md_body), "html", "utf-8"))

    port = int(smtp["SMTP_PORT"])
    if port == 465:
        server = smtplib.SMTP_SSL(smtp["SMTP_HOST"], port, timeout=30)
    else:
        server = smtplib.SMTP(smtp["SMTP_HOST"], port, timeout=30)
        server.starttls()
    try:
        server.login(smtp["SMTP_USER"], smtp["SMTP_PASS"])
        server.sendmail(smtp["SMTP_FROM"], [to_addr], msg.as_string())
    finally:
        server.quit()


async def main(dry_run: bool) -> None:
    engine = create_async_engine(
        _db_url(),
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0},  # compatibile con pgbouncer/pooler
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    smtp = _smtp_config()
    today = datetime.date.today().isoformat()
    out_dir = DIGESTS_DIR / today
    out_dir.mkdir(parents=True, exist_ok=True)

    n_lists = 0
    n_emails = 0
    n_email_failures = 0
    notified_alert_ids: set[str] = set()
    alerts_by_email: dict[str, list[dict]] = {}

    async with session_factory() as db:
        lists = (await db.execute(text("""
            SELECT id::text AS id, name, digest_email, optimization_result
            FROM shopping_lists
            WHERE is_recurring = TRUE
              AND digest_email IS NOT NULL AND digest_email <> ''
            ORDER BY created_at
        """))).mappings().all()

        for lst in lists:
            email = lst["digest_email"].strip().lower()
            items = (await db.execute(text("""
                SELECT li.product_id, li.product_name, li.quantity,
                       p.name AS resolved_name
                FROM list_items li
                LEFT JOIN products p ON p.id = li.product_id
                WHERE li.list_id = :lid
                ORDER BY li.sort_order
            """), {"lid": lst["id"]})).mappings().all()
            items = [dict(i) for i in items]
            if not items:
                continue

            plan = await _compute_plan(db, items)

            # storico dal precedente digest
            opt = lst["optimization_result"] or {}
            if not isinstance(opt, dict):
                opt = {}
            history = opt.get("digest_history") or []
            prev_total = None
            if history and isinstance(history[-1], dict) and "total" in history[-1]:
                try:
                    prev_total = float(history[-1]["total"])
                except (TypeError, ValueError):
                    prev_total = None

            # promo check per le voci ancorate
            promo_checks: list[dict] = []
            for it in items:
                if it["product_id"] is None:
                    continue
                for pc in await compute_promo_check(db, str(it["product_id"])):
                    pc["product_name"] = it.get("resolved_name") or it["product_name"]
                    promo_checks.append(pc)

            # alert della stessa email (calcolati una sola volta per email)
            if email not in alerts_by_email:
                alerts_by_email[email] = await _triggered_alerts(db, email)
            alerts = alerts_by_email[email]

            md = _render_markdown(dict(lst), plan, prev_total, promo_checks, alerts)
            out_path = out_dir / f"{lst['id']}.md"
            out_path.write_text(md, encoding="utf-8")
            n_lists += 1
            print(f"digest: lista {lst['id']} ({lst['name']!r}) -> {out_path}")

            # invio email PRIMA del commit: lo stato (last_digest_at, storico,
            # alert notificati) viene committato solo se l'invio riesce, oppure
            # se SMTP non e' configurato (il markdown e' il deliverable).
            email_ok = True
            if smtp and not dry_run:
                try:
                    _send_email(smtp, email, f"SpesaSmart — digest: {lst['name']}", md)
                    n_emails += 1
                    print(f"digest: lista {lst['id']}: email inviata a {_mask_email(email)}")
                except Exception as exc:
                    email_ok = False
                    n_email_failures += 1
                    err = str(exc).replace(email, _mask_email(email))
                    print(f"digest: lista {lst['id']}: invio email a {_mask_email(email)} fallito: {err}")

            if not dry_run:
                if not email_ok:
                    # stato NON committato: al prossimo run il digest per
                    # questa lista viene ricalcolato e reinviato
                    await db.rollback()
                    continue
                # append allo storico (max 12 entry) + last_digest_at
                history = (history + [plan])[-MAX_HISTORY_ENTRIES:]
                opt["digest_history"] = history
                await db.execute(
                    text("""
                        UPDATE shopping_lists
                        SET optimization_result = CAST(:opt AS jsonb),
                            last_digest_at = NOW(),
                            updated_at = NOW()
                        WHERE id = :lid
                    """),
                    {"opt": json.dumps(opt, ensure_ascii=False, default=str), "lid": lst["id"]},
                )
                # aggiorna gli alert notificati (una sola volta anche se
                # l'email ha piu' liste)
                for a in alerts:
                    if a["id"] in notified_alert_ids:
                        continue
                    await db.execute(
                        text("""
                            UPDATE price_alerts
                            SET last_notified_at = NOW(), last_triggered = NOW()
                            WHERE id = :aid
                        """),
                        {"aid": a["id"]},
                    )
                    notified_alert_ids.add(a["id"])
                await db.commit()

    await engine.dispose()
    mode = "DRY-RUN, nessuna scrittura DB" if dry_run else "scritture DB effettuate"
    smtp_msg = f"{n_emails} email inviate" if smtp else "SMTP non configurato: solo file"
    print(f"digest: {n_lists} liste elaborate ({mode}; {smtp_msg})")
    if n_email_failures:
        print(f"digest: {n_email_failures} invii email falliti: stato non committato "
              "per quelle liste, verranno ritentate al prossimo run")
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Digest settimanale spesa abituale")
    parser.add_argument("--dry-run", action="store_true",
                        help="non scrive sul DB e non invia email (solo file markdown)")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
