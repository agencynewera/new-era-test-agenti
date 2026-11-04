"""Etichette prodotto (FNSKU) di un piano FBA, 21 per foglio A4 (63,5 × 38,1 mm,
griglia Avery L7160): il formato che ECM stampa da Seller Central.

Non esiste via SP-API (getLabels fa solo cartoni e pallet): i FNSKU si leggono
dagli articoli del piano, il Code128 lo disegna python-barcode, il PDF lo
stampa Chrome headless. Collaudato dal vivo il 03/09/2026 su ECM (144
etichette, 7 fogli; i 4 codici ridecodificati esattamente).

    python3 etichette_fnsku.py <cliente> <plan_id> [--ordine SKU,SKU,...] [--per-foglio 21|24]

24 per foglio = griglia Avery L7159 (64 × 33,9 mm, 3 × 8), chiesta da Bedshire il 07/09/2026.

Stampare al 100%, mai "adatta alla pagina". Chrome va lanciato col sandbox
Bash disattivato, altrimenti il PDF non arriva sul disco. Il PDF va nella
cartella PRIME delle etichette cartone del piano (vedi `destinazione`).
"""
from __future__ import annotations
import argparse
import html
import io
import os
import subprocess
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import barcode
from barcode.writer import SVGWriter
import giri
from registro import _nome_cliente
import spapi_client

_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_CSS = """
@page { size: A4; margin: 0; }
html, body { margin: 0; padding: 0; }
body { font-family: Helvetica, Arial, sans-serif; color: #000; }
.pagina { position: relative; width: 210mm; height: 297mm; page-break-after: always; overflow: hidden; }
.pagina:last-child { page-break-after: auto; }
.et { position: absolute; width: %(larghezza)smm; height: %(altezza)smm; box-sizing: border-box; padding: 1.5mm 2.5mm 1mm; text-align: center; overflow: hidden; }
.et svg { display: block; margin: 0 auto; height: %(barcode_mm)smm; width: auto; }
.fnsku { font-size: 9pt; font-weight: bold; letter-spacing: 0.5px; margin: 0.6mm 0 0.8mm; }
.titolo { font-size: 6.5pt; line-height: 1.2; max-height: %(titolo_em)sem; overflow: hidden; }
.cond { font-size: 6.5pt; margin-top: 0.8mm; }
"""


def _code128_svg(testo: str) -> str:
    out = io.BytesIO()
    barcode.Code128(testo, writer=SVGWriter()).write(out, options={
        "module_width": 0.3, "module_height": 11, "quiet_zone": 2,
        "write_text": False, "font_size": 0, "text_distance": 0})
    s = out.getvalue().decode()
    return s[s.index("<svg"):]


def etichette_del_piano(client, seller_id: str, marketplace_id: str, plan_id: str,
                        ordine: list[str] | None = None) -> list[tuple[str, str]]:
    """[(fnsku, titolo)] ripetuti per quantità, nell'ordine di applicazione."""
    items = client.get(f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/items",
                       params={"pageSize": 100}).get("items") or []
    per_sku = {i["msku"]: i for i in items}
    mancanti = [s for s in (ordine or []) if s not in per_sku]
    if mancanti:
        raise ValueError(f"SKU non nel piano: {', '.join(mancanti)}")
    etichette = []
    for sku in (ordine or list(per_sku)):
        it = per_sku[sku]
        r = client.get(f"/listings/2021-08-01/items/{seller_id}/{quote(sku, safe='')}",
                       params={"marketplaceIds": marketplace_id, "includedData": "summaries"})
        titolo = (r.get("summaries") or [{}])[0].get("itemName", "")
        etichette += [(it["fnsku"], titolo)] * int(it["quantity"])
    return etichette


# Griglie A4 note: misure in mm, margini dal bordo foglio, passo fra etichette.
_GRIGLIE = {
    21: dict(colonne=3, larghezza=63.5, altezza=38.1, sx=7.2, alto=15.15, passo_x=66.0, passo_y=38.1,
             barcode_mm=12, titolo_em=2.4, titolo_car=90),   # Avery L7160, 3 x 7
    24: dict(colonne=3, larghezza=64.0, altezza=33.9, sx=7.2, alto=12.9, passo_x=66.0, passo_y=33.9,
             barcode_mm=10, titolo_em=1.2, titolo_car=48),   # Avery L7159, 3 x 8
}


def _titolo_breve(titolo: str, massimo: int) -> str:
    """Inizio + "…" + fine, come le etichette di Amazon: la variante (taglia,
    colore) sta quasi sempre in coda al titolo, e tagliando solo in fondo le
    varianti di un foglio misto escono identiche (Galeone, 18/09/2026)."""
    if len(titolo) <= massimo:
        return titolo
    meta = (massimo - 1) // 2
    return titolo[:meta].rstrip() + "…" + titolo[-meta:].lstrip()


def html_fogli(etichette: list[tuple[str, str]], per_foglio: int = 21) -> str:
    g = _GRIGLIE[per_foglio]
    svg_di = {f: _code128_svg(f) for f, _ in etichette}
    pagine = []
    for p in range(0, len(etichette), per_foglio):
        div = []
        for k, (fnsku, titolo) in enumerate(etichette[p:p + per_foglio]):
            left = g["sx"] + (k % g["colonne"]) * g["passo_x"]
            top = g["alto"] + (k // g["colonne"]) * g["passo_y"]
            div.append(f'<div class="et" style="left:{left}mm;top:{top}mm">{svg_di[fnsku]}'
                       f'<div class="fnsku">{fnsku}</div><div class="titolo">{html.escape(_titolo_breve(titolo, g["titolo_car"]))}</div>'
                       f'<div class="cond">Nuovo</div></div>')
        pagine.append('<div class="pagina">' + "".join(div) + "</div>")
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{_CSS % g}</style></head>"
            f"<body>{''.join(pagine)}</body></html>")


def destinazione(cliente: str, plan_id: str, suffisso: str) -> str:
    """Il percorso del PDF senza estensione. Dall'11/09/2026 nella stessa
    cartella PRIME delle etichette cartone del piano (`giro.py conferma --vai`
    la scrive nel file locale del piano): Mariano le riprende da lì. Se il
    piano non ha ancora etichette, o la cartella è sparita, resta il Desktop."""
    nome = f"Etichette-FNSKU-{_nome_cliente(cliente)}-{plan_id[:10]}{suffisso}"
    locale = giri.carica(plan_id) or {}
    for percorsi in (locale.get("etichette") or {}).values():
        for percorso in percorsi:
            cartella = os.path.dirname(percorso)
            if os.path.isdir(cartella):
                return os.path.join(cartella, nome)
    return os.path.expanduser(f"~/Desktop/{nome}")


_AMBIENTE_CHROME = ("PATH", "HOME", "TMPDIR", "USER", "LANG")


def _stampa_pdf(base: str) -> None:
    """Chrome headless stampa `base.html` in `base.pdf`, con un ambiente di sola
    lista bianca: `spapi_client` carica in os.environ i segreti LWA di tutti i
    clienti, e un sottoprocesso li erediterebbe in blocco (DESIGN §17)."""
    ambiente = {k: os.environ[k] for k in _AMBIENTE_CHROME if k in os.environ}
    subprocess.run([_CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={base}.pdf", f"file://{base}.html"],
                   check=True, capture_output=True, env=ambiente)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cliente"); p.add_argument("plan_id")
    p.add_argument("--ordine", help="SKU nell'ordine di applicazione, separati da virgola")
    p.add_argument("--marketplace", default="APJ6JRA9NG5V4")
    p.add_argument("--per-foglio", type=int, choices=sorted(_GRIGLIE), default=21,
                   help="etichette per foglio A4: 21 (Avery L7160) o 24 (Avery L7159)")
    a = p.parse_args(argv)
    client = spapi_client.crea_client(a.cliente)
    prefisso = "".join(ch for ch in a.cliente.upper() if ch.isalnum())
    seller = os.environ.get(f"{prefisso}_SPAPI_SELLER_ID")
    if not seller:
        print(f"ERRORE: {prefisso}_SPAPI_SELLER_ID mancante", file=sys.stderr); return 1
    ordine = [s.strip() for s in a.ordine.split(",")] if a.ordine else None
    etichette = etichette_del_piano(client, seller, a.marketplace, a.plan_id, ordine)
    suffisso = "" if a.per_foglio == 21 else f"-x{a.per_foglio}"
    base = destinazione(a.cliente, a.plan_id, suffisso)
    with open(base + ".html", "w") as f:
        f.write(html_fogli(etichette, a.per_foglio))
    _stampa_pdf(base)
    os.remove(base + ".html")
    fogli = -(-len(etichette) // a.per_foglio)
    print(f"{len(etichette)} etichette, {fogli} fogli A4 x {a.per_foglio}: {base}.pdf")
    return 0


if __name__ == "__main__":
    sys.exit(main())
