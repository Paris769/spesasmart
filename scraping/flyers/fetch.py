"""
POC volantini discount — download dei volantini correnti.

Fonti (validate il 2026-07-04, dettagli e note legali in README.md):
  lidl      overview HTML → slug volantini → API pubblica
            endpoints.leaflets.schwarz/v4/flyer → download PDF
  aldi      volantino-online.html → pubblicazioni Publitas
            (volantino.aldi.it) → link PDF diretto nella pagina
  eurospin  API del viewer ufficiale digitalflyer.eurospin.it
            (OAuth client_credentials con credenziali pubbliche del viewer)
            → prodotti GIÀ STRUTTURATI in JSON: niente vision necessaria
  md        sfogliatore mdspa.it → flyer code → service-volantino.mdspa.it
            → `var data = [...]` con prodotti GIÀ STRUTTURATI

Output: scraping/flyers/out/<chain>/<YYYY-MM-DD>/
  manifest.json               — indice di ciò che è stato scaricato
  <slug>.pdf                  — volantino PDF (lidl, aldi)
  products_<alias>.json       — feed strutturato (eurospin, md)

Uso:
    python -m scraping.flyers.fetch --chain lidl
    python -m scraping.flyers.fetch --chain all --limit 2
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import re
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("flyers.fetch")

OUT_DIR = Path(__file__).resolve().parent / "out"
RATE = 1.0  # max 1 richiesta/secondo, come da policy del progetto

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9",
}

# ── Lidl ─────────────────────────────────────────────────────────────────────
LIDL_OVERVIEW = "https://www.lidl.it/c/volantino-lidl/s10018048"
LIDL_API = "https://endpoints.leaflets.schwarz/v4/flyer"
_LIDL_SLUG_RE = re.compile(r"/l/it/volantini/([a-z0-9-]+)/")

# ── Aldi ─────────────────────────────────────────────────────────────────────
ALDI_OVERVIEW = "https://www.aldi.it/it/volantino-online.html"
_ALDI_PUB_RE = re.compile(r"https://volantino\.aldi\.it/([A-Za-z0-9_%-]+)")
_PUBLITAS_PDF_RE = re.compile(
    r"https://view\.publitas\.com/\d+/\d+/pdfs/[a-f0-9-]+\.pdf[^\"'\\ ]*"
)

# ── Eurospin ─────────────────────────────────────────────────────────────────
# Credenziali OAuth del viewer PUBBLICO smt-digitalflyer: sono embeddate nel
# bundle JS servito a ogni visitatore di eurospin.it/volantino/ — la pipeline
# esegue le stesse identiche chiamate del browser di un utente qualsiasi.
EUROSPIN_API = "https://digitalflyer.eurospin.it"
EUROSPIN_API_PATH = "api/eurospin/eurospin-italia"
EUROSPIN_CLIENT_CREDS = "850bdb5c-a86d-40b2-a8fb-a7bb61823a24:Interlacedit0"
EUROSPIN_NATIONAL_STORE = "eurospin-italia"

# ── MD ───────────────────────────────────────────────────────────────────────
MD_SFOGLIATORE = "https://www.mdspa.it/sfogliatore/?id_pv={pv_id}"
MD_VIEWER = "https://service-volantino.mdspa.it/{code}"
MD_DEFAULT_PV = "1"
_MD_DATA_RE = re.compile(r"var\s+data\s*=\s*(\[.*?\]);\s*\r?\n", re.S)
_MD_FLYER_CODE_RE = re.compile(r'data-flyer-code="([^"]+)"')


class Fetcher:
    def __init__(self, client: httpx.AsyncClient, out_root: Path, limit: int = 3):
        self.client = client
        self.out_root = out_root
        self.limit = limit
        self._t_last = 0.0

    async def _throttle(self) -> None:
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._t_last
        if elapsed < RATE:
            await asyncio.sleep(RATE - elapsed)
        self._t_last = loop.time()

    async def _get(self, url: str, **kwargs) -> httpx.Response | None:
        await self._throttle()
        # header estratti UNA volta fuori dal loop: kwargs.pop dentro il retry
        # li perderebbe dal secondo tentativo in poi (es. Authorization Eurospin)
        headers = {**HEADERS, **kwargs.pop("headers", {})}
        for attempt in range(3):
            try:
                r = await self.client.get(
                    url, headers=headers,
                    timeout=60, follow_redirects=True, **kwargs,
                )
                if r.status_code == 200:
                    return r
                log.warning("HTTP %s GET %s (tentativo %d)", r.status_code, url, attempt + 1)
                if r.status_code in (403, 404):
                    return None
            except httpx.RequestError as exc:
                log.warning("Tentativo %d errore GET %s: %s", attempt + 1, url, exc)
            await asyncio.sleep(2 ** attempt)
        return None

    def _chain_dir(self, chain: str) -> Path:
        d = self.out_root / chain / date.today().isoformat()
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _write_manifest(chain_dir: Path, chain: str, items: list[dict]) -> None:
        manifest = {
            "chain": chain,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
        }
        (chain_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("%s: manifest con %d elementi → %s", chain, len(items), chain_dir)

    # ── Lidl: PDF via API leaflets.schwarz ───────────────────────────────────

    async def fetch_lidl(self) -> int:
        chain_dir = self._chain_dir("lidl")
        r = await self._get(LIDL_OVERVIEW)
        if not r:
            log.error("lidl: overview non raggiungibile")
            return 0
        slugs = list(dict.fromkeys(_LIDL_SLUG_RE.findall(r.text)))[: self.limit]
        log.info("lidl: %d volantini correnti: %s", len(slugs), slugs)

        items: list[dict] = []
        for slug in slugs:
            meta = await self._get(
                LIDL_API,
                params={"flyer_identifier": slug, "region_id": 0, "region_code": 0},
                headers={"Origin": "https://www.lidl.it",
                         "Referer": "https://www.lidl.it/",
                         "Accept": "application/json"},
            )
            if not meta:
                log.warning("lidl: API flyer fallita per %s", slug)
                continue
            flyer = (meta.json() or {}).get("flyer") or {}
            pdf_url = flyer.get("hiResPdfUrl") or flyer.get("pdfUrl")
            pages = flyer.get("pages") or []
            entry = {
                "type": "pdf",
                "slug": slug,
                "title": flyer.get("title"),
                "pages": len(pages),
                "pdf_url": pdf_url,
                "path": None,
            }
            if pdf_url:
                pdf = await self._get(pdf_url)
                if pdf:
                    path = chain_dir / f"{slug[:80]}.pdf"
                    path.write_bytes(pdf.content)
                    entry["path"] = path.name
                    log.info("lidl: %s → %s (%.1f MB, %d pagine)",
                             slug, path.name, len(pdf.content) / 1e6, len(pages))
            (chain_dir / f"{slug[:80]}.meta.json").write_text(
                json.dumps(meta.json(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            items.append(entry)

        self._write_manifest(chain_dir, "lidl", items)
        return len([i for i in items if i.get("path")])

    # ── Aldi: PDF via Publitas ───────────────────────────────────────────────

    async def fetch_aldi(self) -> int:
        chain_dir = self._chain_dir("aldi")
        r = await self._get(ALDI_OVERVIEW)
        if not r:
            log.error("aldi: pagina volantino-online non raggiungibile")
            return 0
        pubs = [p for p in dict.fromkeys(_ALDI_PUB_RE.findall(r.text))
                if "brochure" not in p.lower()][: self.limit]
        log.info("aldi: %d pubblicazioni correnti: %s", len(pubs), pubs)

        items: list[dict] = []
        for pub in pubs:
            page = await self._get(f"https://volantino.aldi.it/{pub}")
            if not page:
                log.warning("aldi: pubblicazione %s non raggiungibile", pub)
                continue
            html = unescape(page.text)
            m = _PUBLITAS_PDF_RE.search(html)
            entry = {"type": "pdf", "slug": pub, "pdf_url": m.group(0) if m else None,
                     "path": None}
            if m:
                pdf = await self._get(m.group(0))
                if pdf:
                    path = chain_dir / f"{pub[:80]}.pdf"
                    path.write_bytes(pdf.content)
                    entry["path"] = path.name
                    log.info("aldi: %s → %s (%.1f MB)",
                             pub, path.name, len(pdf.content) / 1e6)
            else:
                log.warning("aldi: nessun link PDF Publitas in %s", pub)
            items.append(entry)

        self._write_manifest(chain_dir, "aldi", items)
        return len([i for i in items if i.get("path")])

    # ── Eurospin: feed strutturato via API del viewer ────────────────────────

    async def _eurospin_token(self) -> str | None:
        await self._throttle()
        basic = base64.b64encode(EUROSPIN_CLIENT_CREDS.encode()).decode()
        try:
            r = await self.client.post(
                f"{EUROSPIN_API}/oauth/token",
                headers={**HEADERS,
                         "Authorization": f"Basic {basic}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                content="grant_type=client_credentials&scope=read write",
                timeout=30,
            )
            if r.status_code == 200:
                return r.json().get("access_token")
            log.error("eurospin: oauth/token HTTP %s", r.status_code)
        except httpx.RequestError as exc:
            log.error("eurospin: oauth/token errore: %s", exc)
        return None

    async def fetch_eurospin(self) -> int:
        chain_dir = self._chain_dir("eurospin")
        token = await self._eurospin_token()
        if not token:
            return 0
        auth = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        r = await self._get(
            f"{EUROSPIN_API}/{EUROSPIN_API_PATH}/stores/{EUROSPIN_NATIONAL_STORE}/promotions",
            headers=auth,
        )
        if not r:
            log.error("eurospin: lista promozioni non disponibile")
            return 0
        promos = r.json() or []
        log.info("eurospin: %d promozioni attive: %s",
                 len(promos), [p.get("alias") for p in promos])

        items: list[dict] = []
        for promo in promos[: self.limit]:
            alias = promo.get("alias")
            if not alias:
                continue
            products: list[dict] = []
            page = 0
            while True:
                pr = await self._get(
                    f"{EUROSPIN_API}/{EUROSPIN_API_PATH}/promotions/{alias}"
                    f"/stores/{EUROSPIN_NATIONAL_STORE}/products",
                    params={"page": page, "size": 100},
                    headers=auth,
                )
                if not pr:
                    break
                data = pr.json() or {}
                products.extend(data.get("elements") or [])
                if data.get("last", True):
                    break
                page += 1
            path = chain_dir / f"products_{alias[:70]}.json"
            path.write_text(
                json.dumps({"promotion": promo, "products": products},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info("eurospin: %s → %d prodotti strutturati", alias, len(products))
            items.append({
                "type": "structured",
                "slug": alias,
                "code": promo.get("code"),
                "start_date": promo.get("startDate"),
                "end_date": promo.get("endDate"),
                "products": len(products),
                "path": path.name,
            })

        self._write_manifest(chain_dir, "eurospin", items)
        return len(items)

    # ── MD: feed strutturato dal viewer service-volantino ────────────────────

    async def fetch_md(self, pv_id: str = MD_DEFAULT_PV) -> int:
        chain_dir = self._chain_dir("md")
        r = await self._get(MD_SFOGLIATORE.format(pv_id=pv_id))
        if not r:
            log.error("md: sfogliatore non raggiungibile")
            return 0
        m = _MD_FLYER_CODE_RE.search(r.text)
        if not m:
            log.error("md: data-flyer-code non trovato nello sfogliatore")
            return 0
        code = m.group(1)
        log.info("md: flyer corrente pv=%s → code=%s", pv_id, code)

        viewer = await self._get(MD_VIEWER.format(code=code))
        if not viewer:
            log.error("md: viewer %s non raggiungibile", code)
            return 0
        dm = _MD_DATA_RE.search(viewer.text)
        if not dm:
            log.error("md: 'var data = [...]' non trovato nel viewer")
            return 0
        products = json.loads(dm.group(1))
        path = chain_dir / f"products_{code[:70]}.json"
        path.write_text(
            json.dumps({"flyer_code": code, "pv_id": pv_id, "products": products},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("md: %s → %d prodotti strutturati", code, len(products))
        self._write_manifest(chain_dir, "md", [{
            "type": "structured",
            "slug": code,
            "products": len(products),
            "path": path.name,
        }])
        return 1


CHAINS = ("lidl", "aldi", "eurospin", "md")


async def main(args: argparse.Namespace) -> None:
    chains = CHAINS if args.chain == "all" else (args.chain,)
    out_root = Path(args.out) if args.out else OUT_DIR
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(client, out_root, limit=args.limit)
        for chain in chains:
            log.info("=== fetch %s ===", chain)
            try:
                n = await getattr(fetcher, f"fetch_{chain}")()
                log.info("=== %s completato: %d elementi ===", chain, n)
            except Exception:
                log.exception("fetch %s fallito", chain)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scarica i volantini discount correnti")
    parser.add_argument("--chain", choices=[*CHAINS, "all"], default="all")
    parser.add_argument("--limit", type=int, default=3,
                        help="Max volantini/promozioni per catena (default: 3)")
    parser.add_argument("--out", default=None,
                        help="Directory di output (default: scraping/flyers/out)")
    asyncio.run(main(parser.parse_args()))
