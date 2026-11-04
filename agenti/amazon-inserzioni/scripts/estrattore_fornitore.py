from __future__ import annotations
import csv
from pathlib import Path
import yaml
from openpyxl import load_workbook
from modello import Prodotto

_OBBLIGATORI = ("sku", "title", "price", "quantity", "product_type")
_CAMPI_TESTO = ("sku", "title", "brand", "description", "category", "condition", "mpn", "product_type")


def carica_mappatura(percorso_yaml: str) -> dict[str, str]:
    with open(percorso_yaml) as f:
        return yaml.safe_load(f) or {}


def _righe_da_csv(percorso: str):
    with open(percorso, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def _righe_da_xlsx(percorso: str):
    wb = load_workbook(percorso, read_only=True, data_only=True)
    ws = wb.active
    righe = ws.iter_rows(values_only=True)
    intestazioni = next(righe)
    for riga in righe:
        yield dict(zip(intestazioni, riga))


def _valore(riga: dict, mappatura: dict, campo: str):
    colonna = mappatura.get(campo)
    if not colonna:
        return None
    return riga.get(colonna)


def importa(percorso_file: str, mappatura: dict) -> tuple[list[Prodotto], list[tuple[int, str]]]:
    percorso = Path(percorso_file)
    righe = _righe_da_xlsx(percorso_file) if percorso.suffix.lower() == ".xlsx" else _righe_da_csv(percorso_file)

    prodotti: list[Prodotto] = []
    scarti: list[tuple[int, str]] = []
    for indice, riga in enumerate(righe, start=2):  # riga 1 = intestazione
        dati: dict = {}
        for campo in _CAMPI_TESTO:
            v = _valore(riga, mappatura, campo)
            if v is not None and str(v).strip():
                dati[campo] = str(v)

        immagini_raw = _valore(riga, mappatura, "images")
        dati["images"] = [u.strip() for u in str(immagini_raw).split(",") if u.strip()] if immagini_raw else []
        ean_raw = _valore(riga, mappatura, "ean")
        dati["ean"] = str(ean_raw) if ean_raw not in (None, "") else ""

        prezzo_raw = _valore(riga, mappatura, "price")
        quantita_raw = _valore(riga, mappatura, "quantity")

        mancanti = [c for c in _OBBLIGATORI if c not in dati and c not in ("price", "quantity")]
        if prezzo_raw in (None, ""):
            mancanti.append("price")
        if quantita_raw in (None, ""):
            mancanti.append("quantity")
        if mancanti:
            scarti.append((indice, f"campi obbligatori mancanti: {', '.join(mancanti)}"))
            continue

        try:
            dati["price"] = float(prezzo_raw)
            dati["quantity"] = int(quantita_raw)
        except (TypeError, ValueError):
            scarti.append((indice, "prezzo o quantità non numerici"))
            continue

        prodotti.append(Prodotto(**dati))
    return prodotti, scarti
