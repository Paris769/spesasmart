# Inventory catene — spesa online + click & collect Italia

Documento condiviso tra `chain-scout` (scopre) e `chain-analyzer` (classifica).
Ogni catena ha una riga con schema fisso. Gli agenti modificano SOLO via PR
review-needed.

**Legenda `status`**:
- `in-scope`         → presente in `_CHAINS_SEED`, spider attivo o in roadmap
- `pending-analysis` → trovata da scout, da classificare
- `ready-for-spider` → classificata, scrapability nota, in attesa builder
- `defer-anti-bot`   → tecnologicamente possibile ma bloccata da WAF/captcha
- `defer-login-required` → prezzi nascosti dietro account
- `out-of-scope`     → no spesa online né click & collect
- `dead-link`        → homepage non raggiungibile

**Legenda `scrapability`**: `easy` | `medium` | `hard` | `blocked`

**Legenda `integration_type`**: `api-hybris` (SAP OCC) · `api-ebsn`
(Digitelematica) · `api-esselunga` · `api-sfcc` · `api-commercetools` ·
`api-shopify-storefront` · `ssr-next` (Next.js) · `html-magento` ·
`html-woocommerce` · `html-custom`

---

## 1. Catene IN SCOPE (in `_CHAINS_SEED` con spider attivo o roadmap)

| Slug | Nome | Gruppo | Copertura | Shop URL | Platform | Integration | Scrap. | Anti-bot | Click&Collect | Status spider |
|---|---|---|---|---|---|---|---|---|---|---|
| esselunga | Esselunga | Esselunga SpA | Lomb/Tosc/Emil/Ven/Piem/Laz | spesa.esselunga.it | proprietary | api-esselunga | easy | NONE | ✅ | 🟢 funzionante (7395 prezzi) |
| conad | Conad | Conad cons. | Nazionale | conad.it/spesa-online | SAP Hybris | api-hybris | medium | SOFT | ✅ | 🟡 scheletro |
| carrefour | Carrefour | Carrefour Italia | Nazionale | carrefour.it/spesa-online | SAP Hybris | api-hybris | medium | NONE | ✅ | 🟡 scheletro |
| coop | Coop | Coop Italia (consorzio) | Nazionale (frammentata) | cooponline.it | SFCC | api-sfcc | medium | NONE | ✅ | 🔴 da fare |
| pam | Pam Panorama | Gruppo Pam | Nord+Centro | pampanorama.it/spesa-online | proprietary | html-custom | medium | NONE | ✅ | 🔴 da fare |
| famila | Famila | Selex | Nazionale | cosicomodo.it/famila | CosìComodo (SAP CC) | api-hybris | easy | NONE | ✅ | 🟢 attivo (in corso run) |
| iper | Iper La grande i | Finiper | Nord-Ovest | iperdrive.it | EBSN/Digitelematica | api-ebsn | medium | SOFT (Gigya) | ✅ | 🔴 da fare (auth Gigya) |
| u2 | U2 Supermercato | Finiper Unes | Lombardia | u2supermercato.it | EBSN/Digitelematica | api-ebsn | medium | SOFT (Gigya) | ✅ | 🔴 da fare (stesso stack di iper) |
| crai | Crai | Crai Secom | Nazionale | craispesaonline.it | proprietary | html-custom | medium | NONE | ✅ | 🔴 da fare |
| bennet | Bennet | Bennet SpA | Nord | bennet.com/spesa-online | proprietary | html-custom | medium | NONE | ✅ | 🔴 da fare |
| tigros | Tigros | Tigros SpA | Lomb/Piem | spesaonline.tigros.it | proprietary | html-custom | medium | NONE | ✅ | 🔴 da fare |
| il-gigante | Il Gigante | Il Gigante SpA | Lomb/Piem | ilgigante.net/spesa-online | proprietary | html-custom | medium | NONE | ✅ | 🔴 da fare |

---

## 2. Candidate da analizzare (pending-analysis)

Aggiunte da `chain-scout`, in attesa che `chain-analyzer` le classifichi.

| Slug | Nome | Gruppo | Homepage | Regioni segnalate | Discovered | Egress required |
|---|---|---|---|---|---|---|
| despar | Despar / Eurospar / Interspar | Aspiag Service / Maiora | Nord-Est / Sud | despar.it | NE+S | 2026-05-12 (seed) | despar.it |
| iperal | Iperal | Iperal SpA | Lombardia | iperal.it | Lomb | 2026-05-12 (seed) | iperal.it |
| naturasi | NaturaSì | EcorNaturaSì | Nazionale (bio) | naturasi.it | Naz | 2026-05-12 (seed) | naturasi.it |
| cortilia | Cortilia | Cortilia SpA | Nord (online-only) | cortilia.it | Nord | 2026-05-12 (seed) | cortilia.it |
| eataly | Eataly | Eataly SpA | Naz/Internaz (gourmet) | eataly.com | Naz | 2026-05-12 (seed) | eataly.com |
| unicoop-firenze | Unicoop Firenze | Coop (cooperativa autonoma) | Toscana | unicoopfirenze.it | Tosc | 2026-05-12 (seed) | unicoopfirenze.it |
| nova-coop | Nova Coop | Coop (cooperativa autonoma) | Piemonte | novacoop.it | Piem | 2026-05-12 (seed) | novacoop.it |
| coop-alleanza | Coop Alleanza 3.0 | Coop | Adriatica/Centro | coopalleanza3-0.it | Centro/Adr | 2026-05-12 (seed) | coopalleanza3-0.it |
| coop-lombardia | Coop Lombardia | Coop | Lombardia | e-coop.it | Lomb | 2026-05-12 (seed) | e-coop.it |
| coop-liguria | Coop Liguria | Coop | Liguria | coopliguria.coop | Lig | 2026-05-12 (seed) | coopliguria.coop |
| dok | DOK Supermercati | Megamark (Selex) | Sud | cosicomodo.it/dok | Sud | 2026-05-12 (seed) | cosicomodo.it |
| sole365 | Sole 365 | Sole 365 SpA | Sud | sole365.it | Sud | 2026-05-12 (seed) | sole365.it |
| emisfero | Emisfero | Unicomm (Selex) | Veneto/FVG | cosicomodo.it/emisfero | NE | 2026-05-12 (seed) | cosicomodo.it |
| mercato | Mercatò | Maxi Di (Selex) | Piemonte | cosicomodo.it/mercato | Piem | 2026-05-12 (seed) | cosicomodo.it |
| galassia | Galassia | Maxi Di (Selex) | Veneto/FVG | cosicomodo.it/galassia | NE | 2026-05-12 (seed) | cosicomodo.it |
| italmark | Italmark | Italmark SpA (Selex) | Brescia/BG | cosicomodo.it/italmark | Lomb | 2026-05-12 (seed) | cosicomodo.it |
| pan | Pan | Pan (Selex) | Veneto | cosicomodo.it/pan | Ven | 2026-05-12 (seed) | cosicomodo.it |
| emi | Emi | Emi (Selex) | Piem/Lig | cosicomodo.it/emi | NW | 2026-05-12 (seed) | cosicomodo.it |
| ilgigante-selex | Il Gigante (Selex) | Selex | Lomb/Piem | cosicomodo.it/ilgigante | NW | 2026-05-12 (seed) | cosicomodo.it |
| aeo | A&O Selex | Selex | Naz (regionale) | cosicomodo.it/aeo | Vari | 2026-05-12 (seed) | cosicomodo.it |
| cadoro | Cadoro Spesa Online | Cadoro SpA | Veneto | cadoro.com | Ven | 2026-05-12 (seed) | cadoro.com |
| tigreamico | Tigre Amico / Tigre | Gruppo Gabrielli | Centro/Sud | tigreamico.com | Centro | 2026-05-12 (seed) | tigreamico.com |
| basko | Basko | SoGeGross | Liguria/Piem | basko.it | Lig/Piem | 2026-05-12 (seed) | basko.it |
| ipersoap | Ipersoap | Ipersoap SpA | Centro/Sud (drugstore) | ipersoap.it | Centro/Sud | 2026-05-12 (seed) | ipersoap.it |
| sigma | Sigma | Sigma Italia (consorzio) | Naz (frammentata) | sigmaonline.it | Naz | 2026-05-12 (seed) | sigmaonline.it |
| sma | SMA / Auchan | (era Auchan, ora Conad) | Naz | conad.it | — | 2026-05-12 (seed) | — (rimanda a Conad) |
| simply | Simply Market | (era Auchan, ora Carrefour) | Naz | carrefour.it | — | 2026-05-12 (seed) | — (rimanda a Carrefour) |

---

## 3. Catene classificate (ready-for-spider / defer / out-of-scope)

_(vuota — verrà popolata dall'analyzer mano a mano che lavora sulle candidate)_

| Slug | Status | Scrapability | Anti-bot | Note |
|---|---|---|---|---|

---

## 4. Catene FUORI scope (no spesa online consumer)

Confermate prive di carrello + prezzi online al 2026-05-12. Restano qui
documentate per evitare che `chain-scout` le re-segnali ogni settimana.

| Slug | Nome | Motivo esclusione |
|---|---|---|
| lidl | Lidl Italia | Solo volantino digitale, no carrello |
| eurospin | Eurospin | Solo volantino, no e-commerce |
| md | MD | Solo volantino |
| aldi | Aldi Italia | Solo volantino |
| penny | Penny Market | Solo volantino |
| dpiu | Dpiù | Solo volantino |
| todis | Todis | Solo volantino |
| ins | INS Mercato | Solo volantino |

---

## 5. Marketplace di personal shopping (caso a parte)

Non sono catene proprietarie ma aggregatori che acquistano per conto
dell'utente. Espongono prezzi propri (markup variabile). Vanno trattati
come **catena virtuale** se mai integrati, con `integration_type:
marketplace-personal-shopper`.

| Slug | Nome | Note |
|---|---|---|
| everli | Everli | Personal shopper su catene partner |
| glovo-market | Glovo Market | Delivery ultra-rapido |
| just-eat-grocery | Just Eat Grocery | Beta in alcune città |

**Decisione corrente**: NON includere finché non si valuta l'impatto su
trasparenza prezzi (i loro listini sono spesso +10-30% sul retail).
