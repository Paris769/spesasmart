"""
Orchestratore della rete agenti SpesaSmart.

Esegue gli agenti nell'ordine giusto: Analyst (calcola i KPI) → Growth e
Product (leggono i KPI e aprono le Issue di proposta). Il Guardian (ops/
self-healing) gira separatamente col suo workflow.

I segreti negli env sono SOLO: DATABASE_URL (lettura) e GITHUB_TOKEN (issues).
Nessuna credenziale social/ads/pagamento: per design, gli agenti non possono
compiere azioni pubbliche o finanziarie — solo proporre.
"""
import asyncio

from agents import analyst, growth, product, revenue, marketing


async def main() -> None:
    print("=== Orchestratore rete agenti ===")
    await analyst.main()      # async: tocca il DB → metrics.json
    growth.main()             # sync: legge metrics.json, apre issue
    product.main()
    await revenue.main()      # async: legge click_log → proposta monetizzazione
    await marketing.main()    # async: bozze contenuti (draft-only) → proposta
    print("=== Fine ===")


if __name__ == "__main__":
    asyncio.run(main())
