"""
Agente REVENUE — monetizzazione e ricavi.

Analizza i click d'acquisto (click_log) per capire dove l'app genera già valore
(traffico verso i retailer) e propone come trasformarlo in ricavo: a quali
programmi di affiliazione iscriversi, dove attivare la monetizzazione. Apre una
Issue di proposta marcata `needs-human`: l'iscrizione ai programmi richiede dati
personali/fiscali/bancari e la decisione resta SEMPRE umana. L'agente non si
iscrive, non pubblica, non gestisce pagamenti.
"""
import asyncio

from agents.common.db import connect
from agents.common.state import write_state
from agents.common.gate import upsert_issue

ISSUE_TITLE = "💶 [revenue] Monetizzazione & ricavi (auto)"


async def main() -> None:
    conn = await connect()
    try:
        tot = await conn.fetchval(
            "SELECT count(*) FROM click_log WHERE created_at > now() - interval '30 days'"
        ) or 0
        per_chain = await conn.fetch(
            """
            SELECT chain_slug, count(*) AS n
            FROM click_log
            WHERE created_at > now() - interval '30 days'
            GROUP BY chain_slug ORDER BY n DESC
            """
        )
    finally:
        await conn.close()

    # Stima ricavi: commissione media affiliazione grocery ~2-5% su un carrello
    # medio ~50€ → ~1-2,5€ per acquisto. Tasso di conversione click→acquisto
    # prudenziale ~5-10%. Range volutamente cauto.
    clicks = int(tot)
    low = round(clicks * 0.05 * 1.0, 1)
    high = round(clicks * 0.10 * 2.5, 1)

    revenue = {
        "clicks_30d": clicks,
        "per_chain": [{"chain": r["chain_slug"], "clicks": int(r["n"])} for r in per_chain],
        "stima_mensile_eur": {"min": low, "max": high},
    }
    write_state("revenue.json", revenue)

    lines = [
        "_Issue generata e aggiornata automaticamente dall'agente Revenue._",
        "",
        "## 📈 Segnale di valore (ultimi 30 giorni)",
        f"- Click \"Acquista\" verso i retailer: **{clicks}**",
    ]
    if per_chain:
        lines += [f"  - {r['chain_slug'] or 'n/d'}: {int(r['n'])}" for r in per_chain]
    lines += [
        f"- Ricavo potenziale stimato (affiliazione, prudenziale): "
        f"**€{low}–{high}/mese** _(cresce col traffico)_",
        "",
        "## ✅ Attivazione (richiede TE — `needs-human`)",
        "L'infrastruttura è pronta: i pulsanti d'acquisto passano già da `/go` "
        "(tracciato). Per incassare basta iscriversi a un programma e impostare gli "
        "env `AFFIL_<CATENA>` / `AFFIL_DEFAULT` su Render. Ordine consigliato:",
        "1. **Reti di affiliazione** (Awin / TradeDoubler / Sovrn): iscrizione publisher, "
        "poi attiva i programmi delle catene disponibili. _(serve P.IVA)_",
        "2. **Amazon Associates** per i prodotti linkabili ad Amazon (immediato).",
        "3. **Display ads** (AdSense) come ricavo passivo sul traffico.",
        "4. **Premium** (alert illimitati, ottimizzazione multi-negozio, no-ads) con Stripe.",
        "",
        "> ⚠️ L'iscrizione ai programmi e l'impostazione dei pagamenti spettano a te: "
        "l'agente prepara e propone, non crea account né gestisce denaro.",
    ]
    upsert_issue(ISSUE_TITLE, "\n".join(lines), labels=["agent", "needs-human"])


if __name__ == "__main__":
    asyncio.run(main())
