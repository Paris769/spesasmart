"""
Agente MARKETING — prepara BOZZE di contenuti (mai pubblica).

Trasforma i dati reali in materiale di marketing pronto da rivedere: i prodotti
con il maggior risparmio possibile diventano "ganci" ("risparmia €X su Y"), le
ricerche più frequenti diventano idee SEO/blog. Scrive le bozze in
agents/drafts/ e apre una Issue `needs-human, channel:social`: la PUBBLICAZIONE
è sempre un atto umano. L'agente non posta, non spende in ads, non firma nulla.
"""
import asyncio
import datetime

from agents.common.db import connect
from agents.common.state import read_state, write_state
from agents.common.gate import upsert_issue
from pathlib import Path

ISSUE_TITLE = "📣 [marketing] Bozze contenuti (auto)"
DRAFTS_DIR = Path(__file__).resolve().parent / "drafts"


async def main() -> None:
    conn = await connect()
    try:
        # "Ganci" di risparmio: prodotti con la maggior forbice di prezzo tra
        # negozi (il messaggio più efficace: "potevi risparmiare €X").
        hooks = await conn.fetch(
            """
            WITH spread AS (
                SELECT p.name,
                       min(pr.price) AS lo, max(pr.price) AS hi,
                       count(DISTINCT pr.store_id) AS sc
                FROM products p
                JOIN prices pr ON pr.product_id = p.id AND pr.is_current = TRUE
                GROUP BY p.id, p.name
                HAVING count(DISTINCT pr.store_id) >= 2 AND min(pr.price) > 0
            )
            SELECT name, lo, hi, (hi - lo) AS save, sc
            FROM spread ORDER BY (hi - lo) DESC LIMIT 8
            """
        )
    finally:
        await conn.close()

    m = read_state("metrics.json") or {}
    top = [t["q"] for t in m.get("top_searches", [])[:8]]

    # --- Bozza social ---
    social_lines = ["# Bozze post social (DA RIVEDERE PRIMA DI PUBBLICARE)", ""]
    for h in hooks[:5]:
        save = float(h["save"])
        if save < 0.2:
            continue
        social_lines.append(
            f"- 💸 Lo sapevi? Su **{h['name'][:60]}** puoi risparmiare fino a "
            f"**€{save:.2f}** scegliendo il supermercato giusto ({int(h['sc'])} a "
            f"confronto). Cerca su SpesaSmart 👉 [link] #spesa #risparmio"
        )
    # --- Idee SEO/blog dalle ricerche reali ---
    seo_lines = ["", "# Idee articoli SEO (dalle ricerche reali)", ""]
    for q in top:
        seo_lines.append(f"- \"Dove costa meno {q}? Confronto prezzi supermercati 2026\"")
    if not top:
        seo_lines.append("- (telemetria ricerche ancora giovane: torneranno idee con più dati)")

    # --- ASO (store description) ---
    aso = [
        "",
        "# Descrizione store / ASO (bozza)",
        "",
        "SpesaSmart — Confronta i prezzi della spesa e risparmia. Cerca un "
        "prodotto e scopri dove costa meno tra i supermercati vicino a te. "
        "Lista della spesa ottimizzata, assistente d'acquisto, alert prezzi. "
        "Parole chiave: confronto prezzi, spesa, supermercati, offerte, risparmio.",
    ]

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    content = "\n".join(social_lines + seo_lines + aso)
    (DRAFTS_DIR / "marketing.md").write_text(content, encoding="utf-8")
    write_state("marketing.json", {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "hooks": [{"name": h["name"], "save": float(h["save"])} for h in hooks],
        "seo_ideas": top,
    })

    body = (
        "_Issue generata dall'agente Marketing. Le bozze NON sono pubblicate._\n\n"
        "Materiale pronto da rivedere e — se ti convince — pubblicare TU "
        "(social, blog/SEO, store). File completo: `agents/drafts/marketing.md`.\n\n"
        + content
        + "\n\n> ⚠️ `needs-human`: nessun post è stato pubblicato. Niente budget "
          "ads, niente account social toccati. Pubblichi tu ciò che approvi."
    )
    upsert_issue(ISSUE_TITLE, body, labels=["agent", "needs-human", "channel:social"])


if __name__ == "__main__":
    asyncio.run(main())
