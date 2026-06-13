"""
Agente PRODUCT — evoluzione del prodotto (backlog auto-generato).

Trasforma i segnali d'uso (ricerche a zero risultati, copertura, foto) in un
backlog di miglioramenti prioritizzato. Apre/aggiorna una Issue "vivente".
Propone, non esegue: ogni voce è una proposta che un umano accetta (→ diventa
una PR su branch agent/*, mai merge diretto).
"""
from agents.common.state import read_state, write_state
from agents.common.gate import upsert_issue

ISSUE_TITLE = "🧩 [product] Backlog auto-generato (auto)"


def main() -> None:
    m = read_state("metrics.json")
    if not m:
        print("product: metrics.json assente — esegui prima analyst")
        return

    backlog: list[dict] = []

    # 1) Sinonimi/refusi dalle ricerche a zero risultati: candidati a entry in
    #    search_synonyms o a fix di catalogo.
    for z in m.get("zero_result_searches", [])[:10]:
        backlog.append({
            "tipo": "ricerca/catalogo",
            "voce": f"Zero risultati per «{z['q']}» (×{z['n']}): aggiungere sinonimo "
                    f"o verificare se il prodotto manca a catalogo",
            "segnale": z["n"],
        })

    # 2) Foto: se la copertura immagini è bassa, è una leva UX forte.
    if m.get("image_pct", 100) < 80:
        backlog.append({
            "tipo": "qualità dati",
            "voce": f"Copertura foto al {m['image_pct']}%: completare l'arricchimento "
                    f"(workflow images-bulk) e valutare il dump OFF",
            "segnale": round(100 - m["image_pct"]),
        })

    # 3) Copertura prezzi multi-negozio: il valore core dell'app.
    if m.get("coverage_multi_pct", 100) < 50:
        backlog.append({
            "tipo": "copertura",
            "voce": f"Solo {m['coverage_multi_pct']}% dei prodotti ha 2+ negozi: "
                    f"più catene/negozi aumentano il valore del confronto",
            "segnale": round(100 - m["coverage_multi_pct"]),
        })

    backlog.sort(key=lambda x: -x["segnale"])
    write_state("backlog.json", backlog)

    lines = [
        "_Issue generata e aggiornata automaticamente dall'agente Product._",
        "",
        "Backlog prioritizzato dai segnali d'uso reali. Ogni voce è una **proposta**: "
        "se la accetti, l'evoluzione esce come PR su branch `agent/*` (mai merge diretto).",
        "",
    ]
    if backlog:
        for i, b in enumerate(backlog, 1):
            lines.append(f"{i}. **[{b['tipo']}]** {b['voce']}  _(segnale {b['segnale']})_")
    else:
        lines.append("Nessun segnale forte: il prodotto è in buona salute. ✅")

    upsert_issue(ISSUE_TITLE, "\n".join(lines), labels=["agent", "product", "backlog/auto"])


if __name__ == "__main__":
    main()
