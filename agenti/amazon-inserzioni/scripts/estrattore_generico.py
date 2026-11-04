from __future__ import annotations
import json
import re
import requests
from modello import Prodotto


class EstrazioneFallita(Exception):
    """La pagina non contiene dati strutturati Product utilizzabili."""


_LD_JSON = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_OG_META = re.compile(
    r'<meta[^>]+property=["\']([a-zA-Z:]+)["\'][^>]+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)


def _trova_product_ldjson(html: str) -> dict | None:
    for blocco in _LD_JSON.findall(html):
        try:
            dato = json.loads(blocco.strip())
        except json.JSONDecodeError:
            continue
        candidati = dato if isinstance(dato, list) else [dato]
        for c in candidati:
            if isinstance(c, dict) and c.get("@type") == "Product":
                return c
    return None


def _prezzo(valore) -> float:
    try:
        return float(valore) if valore is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _da_ldjson(dato: dict) -> Prodotto:
    offerte = dato.get("offers") or {}
    if isinstance(offerte, list):
        offerte = offerte[0] if offerte else {}
    immagini = dato.get("image") or []
    if isinstance(immagini, str):
        immagini = [immagini]
    marca = dato.get("brand")
    if isinstance(marca, dict):
        marca = marca.get("name", "")
    return Prodotto(
        sku="", title=dato.get("name", ""), price=_prezzo(offerte.get("price")),
        quantity=0, product_type="",
        ean=dato.get("gtin13") or dato.get("gtin") or "",
        brand=marca or "", description=dato.get("description", ""), images=list(immagini),
    )


def _da_opengraph(html: str) -> Prodotto | None:
    coppie = {chiave: valore for chiave, valore in _OG_META.findall(html)}
    titolo = coppie.get("og:title")
    if not titolo:
        return None
    immagine = coppie.get("og:image")
    return Prodotto(
        sku="", title=titolo, price=_prezzo(coppie.get("product:price:amount")),
        quantity=0, product_type="",
        description=coppie.get("og:description", ""),
        images=[immagine] if immagine else [],
    )


def estrai_da_link(url: str, *, sessione=None) -> Prodotto:
    sess = sessione or requests
    r = sess.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (compatible; NewEraBot/1.0)"})
    if r.status_code >= 400:
        raise EstrazioneFallita(f"{url} -> HTTP {r.status_code}")
    dato = _trova_product_ldjson(r.text)
    if dato:
        return _da_ldjson(dato)
    prodotto = _da_opengraph(r.text)
    if prodotto:
        return prodotto
    raise EstrazioneFallita(f"{url}: nessun dato strutturato Product trovato")
