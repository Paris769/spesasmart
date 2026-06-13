"""
Agente GROWTH — espansione e copertura.

Legge metrics.json e individua i gap di crescita: catene con shop online ma zero
prezzi (catene da aggiungere), copertura bassa, e i prodotti più cercati che NON
diamo (dalle ricerche a zero risultati = domanda non soddisfatta). Apre/aggiorna
una Issue di proposta. NON crea account né scrape: le nuove catene richiedono
decisioni umane → etichetta `needs-human`.
"""
from agents.common.state import read_state
from agents.common.gate import upsert_issue

ISSUE_TITLE = "🌱 [growth] Espansione & copertura (auto)"


def main() -> None:
    m = read_state("metrics.json")
    if not m:
        print("growth: metrics.json assente — esegui prima analyst")
        return

    # Catene "dormienti": hanno uno shop online ma 0 prezzi nel DB → candidate
    # all'integrazione (nuovo scraper). Richiede decisione/eventuali account = umano.
    dormienti = [
        c["slug"] for c in m["chains"]
        if c.get("has_online_shop") and not (c.get("prezzi") or 0)
    ]
    # Catene attive ma "vecchie" (dati > 48h): segnale per Ops/Guardian.
    stantie = [
        f"{c['slug']} ({c['eta_ore']}h)"
        for c in m["chains"]
        if (c.get("prezzi") or 0) > 0 and (c.get("eta_ore") or 0) > 48
    ]
    zero = m.get("zero_result_searches", [])[:15]

    lines = [
        "_Issue generata e aggiornata automaticamente dall'agente Growth._",
        "",
        "## 📊 Stato",
        f"- Prodotti: **{m['products_total']:,}** · foto **{m['image_pct']}%** · "
        f"copertura 2+ negozi **{m['coverage_multi_pct']}%**",
        f"- Prezzi correnti: **{m['prices_current']:,}**",
        "",
        "## 🧭 Catene da integrare (shop online, 0 prezzi)",
    ]
    if dormienti:
        lines += [f"- **{c}** — valutare nuovo scraper / accesso" for c in dormienti]
        lines.append("")
        lines.append("> ⚠️ `needs-human`: nuove catene possono richiedere account/credenziali "
                     "o accordi. Decisione umana richiesta prima di procedere.")
    else:
        lines.append("- Nessuna: tutte le catene con shop online hanno già prezzi. ✅")

    if stantie:
        lines += ["", "## ⏳ Catene con dati vecchi (>48h)", *[f"- {s}" for s in stantie]]

    lines += ["", "## 🔎 Domanda non soddisfatta (ricerche a zero risultati, 30gg)"]
    if zero:
        lines += [f"- `{z['q']}` × {z['n']}" for z in zero]
        lines.append("")
        lines.append("→ Prodotti/categorie più richiesti che NON copriamo: priorità per "
                     "catalogo e nuove catene.")
    else:
        lines.append("- Nessuna ricerca a zero risultati registrata (telemetria giovane).")

    upsert_issue(ISSUE_TITLE, "\n".join(lines), labels=["agent", "growth"])


if __name__ == "__main__":
    main()
