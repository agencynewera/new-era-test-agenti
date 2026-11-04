"""I codici di un cliente Amazon, indicizzati per SKU e per ASIN.

Serve a far parlare la stessa lingua a due marketplace dello stesso cliente:
da un ASIN (che e' cio' che si ricava da un link Amazon) si risale allo SKU con
cui il prodotto e' registrato su Seller Central e al suo EAN.
"""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass

RADICE = os.path.expanduser("~/.config/newera-amazon/ean")


class CodiciMancanti(Exception):
    """I codici di quel cliente non sono ancora stati raccolti."""


@dataclass(frozen=True)
class Voce:
    sku: str
    ean: str


def _preferita(nuova: "Voce", attuale) -> bool:
    if attuale is None:
        return True
    if bool(nuova.ean) != bool(attuale.ean):
        return bool(nuova.ean)
    return nuova.sku < attuale.sku


class Mappa:
    def __init__(self, per_sku: dict, per_asin: dict, generata: str = ""):
        self._sku = per_sku
        self._asin = per_asin
        self.generata = generata

    @classmethod
    def da_righe(cls, righe, generata: str = "") -> "Mappa":
        """I padri di variazione restano fuori: non sono oggetti fisici e un
        codice a barre non ce l'hanno.

        Piu' SKU possono condividere un ASIN (Arcipelago registra "104" e
        "104-FBA" sullo stesso prodotto). Vince chi porta l'EAN, e a parita'
        il primo in ordine alfabetico -- non l'ultimo letto, altrimenti la
        stessa mappa darebbe risposte diverse a seconda di come e' ordinato
        il file da cui nasce."""
        per_sku, per_asin = {}, {}
        for r in righe:
            if r.get("tipo") == "padre di variazione":
                continue
            v = Voce(sku=r["sku"], ean=r.get("ean") or "")
            per_sku[r["sku"]] = v
            if r.get("asin") and _preferita(v, per_asin.get(r["asin"])):
                per_asin[r["asin"]] = v
        return cls(per_sku, per_asin, generata)

    def per_sku(self, sku: str):
        return self._sku.get(sku)

    def per_asin(self, asin: str):
        return self._asin.get(asin)


def nome_file(cliente: str) -> str:
    """Il nome del file dei codici di un cliente: minuscolo, senza accenti di
    percorso. "Tecno Cold" -> tecno-cold.json"""
    piano = re.sub(r"[^a-z0-9]+", "-", cliente.strip().lower()).strip("-")
    return f"{piano}.json"


def carica(cliente: str, radice: str = None) -> Mappa:
    """La mappa dei codici di un cliente, dal file raccolto in precedenza.

    Non va mai a chiedere ad Amazon da sola: il giro completo dura minuti e
    partirebbe a sorpresa in mezzo a un'altra operazione. Se il file non c'e',
    lo dice e si ferma."""
    percorso = os.path.join(radice or RADICE, nome_file(cliente))
    if not os.path.isfile(percorso):
        raise CodiciMancanti(
            f"nessun codice raccolto per {cliente}: lancia "
            f"`python3 scripts/ean_aggiorna.py \"{cliente}\"` (dura qualche minuto)")
    with open(percorso) as f:
        dati = json.load(f)
    return Mappa.da_righe(dati.get("righe") or [], generata=dati.get("generata", ""))
