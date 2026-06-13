"""
Layer di affiliazione + sicurezza per i redirect d'acquisto in uscita.

L'app guadagna instradando i click "Acquista" attraverso /go, che:
  1. verifica che la destinazione sia un sito retailer noto (anti open-redirect),
  2. applica il tag di affiliazione SE configurato (env), altrimenti lascia il
     link invariato — così funziona già oggi e diventa remunerativo appena
     l'utente si iscrive a un programma e imposta gli env.

Configurazione (env, opzionale, da impostare dopo l'iscrizione ai programmi):
  AFFIL_DEFAULT          template/param applicato a tutte le catene senza override
  AFFIL_<SLUG>           override per singola catena (es. AFFIL_ESSELUNGA)
Formato del valore:
  - "{url}"  → il valore è un wrapper di rete; {url} viene sostituito con la
               destinazione URL-encoded (es. https://network/redir?url={url}&id=123)
  - altrimenti viene appeso come query string (es. "utm_source=spesasmart&aff=123")
"""
import os
import urllib.parse

# Domini retailer ammessi come destinazione (registrable domain). Tutto il resto
# è rifiutato: previene che /go diventi un open-redirect per phishing.
ALLOWED_DOMAINS = (
    "esselunga.it",
    "carrefour.it",
    "conad.it",
    "cosicomodo.it",
    "pampanorama.it",
    "cooponline.it",
    "coop.it",
    "eurospin.it",
    "iper.it",
    "everli.com",
)


def is_allowed_host(host: str | None) -> bool:
    h = (host or "").lower()
    return any(h == d or h.endswith("." + d) for d in ALLOWED_DOMAINS)


def apply_affiliate(url: str, chain_slug: str | None) -> str:
    """Applica il tag di affiliazione se configurato; altrimenti ritorna url."""
    tag = (
        os.getenv(f"AFFIL_{(chain_slug or '').upper()}")
        or os.getenv("AFFIL_DEFAULT")
    )
    if not tag:
        return url
    if "{url}" in tag:
        return tag.replace("{url}", urllib.parse.quote(url, safe=""))
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{tag}"
