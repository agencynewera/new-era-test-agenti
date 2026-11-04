"""Lettura del listino di un fornitore (Excel o CSV) in righe uniformi.

Le colonne si riconoscono dall'intestazione, che può stare in una delle prime
righe del file. Il listino Cambioni del 25/09/2026 ha codice e descrizione
nella stessa cella, separati da un a capo, e una riga vuota dopo ogni
articolo: si leggono anche così."""
from __future__ import annotations
import csv
import os
import re
from dataclasses import dataclass

# Parole dell'intestazione → campo. L'ordine conta: «codice a barre» è un EAN
# prima di essere un codice, «prezzo netto» un costo prima di un prezzo.
_SINONIMI = [
    ("ean", ("ean", "gtin", "barcode", "codice a barre", "cod. a barre", "upc")),
    ("marca", ("marca", "brand", "produttore", "marchio")),
    ("um", ("u.m.", "u.m", "um", "unità di misura", "unita di misura", "unità")),
    ("costo", ("prezzo netto", "costo", "netto", "prezzo", "listino", "importo")),
    ("descrizione", ("descrizione", "prodotto", "nome", "articolo desc")),
    ("codice", ("codice", "cod.", "cod ", "articolo", "sku", "riferimento", "rif.")),
]
# Già senza punteggiatura finale: il listino Cambioni scrive N, N. N, e N?
_UM_PEZZO = {"", "N", "NR", "PZ", "PEZZI", "CAD", "CONF"}


@dataclass
class RigaListino:
    riga_file: int
    codice: str
    descrizione: str
    costo: float
    ean: str = ""
    marca: str = ""
    um: str = ""

    @property
    def a_pezzo(self) -> bool:
        """Il listino a KG, LT, MT o MQ dà il prezzo per unità di misura, non
        per confezione: il margine non si calcola finché non si sa il formato
        (trappola del listino Cambioni, 25/09/2026)."""
        return self.um.strip().upper().rstrip(".,?") in _UM_PEZZO


def normalizza_ean(valore) -> str:
    """Solo cifre; Excel toglie gli zeri iniziali, quindi 12 cifre (UPC o EAN
    mozzato) diventano 13 con lo zero davanti. Sotto le 8 cifre non è un EAN."""
    if valore is None:
        return ""
    if isinstance(valore, float) and valore.is_integer():
        valore = int(valore)
    cifre = re.sub(r"\D", "", str(valore))
    if len(cifre) in (11, 12):
        cifre = cifre.zfill(13)
    return cifre if len(cifre) in (8, 13, 14) else ""


def normalizza_costo(valore) -> float | None:
    if valore is None or valore == "":
        return None
    if isinstance(valore, (int, float)):
        return float(valore)
    testo = str(valore).replace("€", "").replace(" ", "").strip()
    if "," in testo and "." in testo:
        testo = testo.replace(".", "")
    testo = testo.replace(",", ".")
    try:
        return float(testo)
    except ValueError:
        return None


def _righe_grezze(percorso: str) -> list[list]:
    est = os.path.splitext(percorso)[1].lower()
    if est in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(percorso, read_only=True, data_only=True)
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    if est == ".xls":
        import xlrd
        foglio = xlrd.open_workbook(percorso).sheet_by_index(0)
        return [foglio.row_values(i) for i in range(foglio.nrows)]
    if est in (".csv", ".txt"):
        with open(percorso, newline="", encoding="utf-8-sig") as f:
            campione = f.read(4096)
            f.seek(0)
            # Il Sniffer si arrende sulle righe vuote ";;;": vince il separatore più frequente.
            separatore = max(";,\t", key=campione.count)
            return [r for r in csv.reader(f, delimiter=separatore)]
    raise ValueError(f"formato non letto: {est} (servono .xlsx, .xls o .csv; un PDF va prima convertito)")


def _campo_di(intestazione: str) -> str | None:
    testo = intestazione.strip().lower()
    for campo, parole in _SINONIMI:
        if any(p in testo for p in parole):
            return campo
    return None


def riconosci_colonne(righe: list[list], prime: int = 15) -> tuple[int, dict[str, int], bool]:
    """(indice della riga d'intestazione, {campo: colonna}, codice e descrizione
    nella stessa cella). Serve almeno descrizione e costo."""
    for i, riga in enumerate(righe[:prime]):
        colonne: dict[str, int] = {}
        unita = False
        for j, cella in enumerate(riga):
            if cella is None or str(cella).strip() == "":
                continue
            testo = str(cella)
            if "\n" in testo and _campo_di(testo.split("\n")[0]) == "codice" \
                    and _campo_di(testo.split("\n")[1]) == "descrizione":
                colonne.setdefault("codice", j)
                colonne.setdefault("descrizione", j)
                unita = True
                continue
            campo = _campo_di(testo)
            if campo and campo not in colonne:
                colonne[campo] = j
        if "descrizione" in colonne and "costo" in colonne:
            return i, colonne, unita
    raise ValueError("intestazione non riconosciuta: servono almeno una colonna descrizione e una di costo")


def leggi_listino(percorso: str) -> list[RigaListino]:
    righe = _righe_grezze(percorso)
    testa, col, unita = riconosci_colonne(righe)
    out = []
    for n, riga in enumerate(righe[testa + 1:], start=testa + 2):
        def cella(campo):
            j = col.get(campo)
            return riga[j] if j is not None and j < len(riga) else None
        costo = normalizza_costo(cella("costo"))
        if unita:
            testo = str(cella("descrizione") or "")
            codice, _, descrizione = testo.partition("\n")
            if not descrizione:
                codice, descrizione = "", codice
        else:
            descrizione = str(cella("descrizione") or "")
            codice = cella("codice")
            codice = "" if codice is None else str(int(codice) if isinstance(codice, float) and codice.is_integer() else codice)
        descrizione = " ".join(descrizione.split())
        if not descrizione or costo is None or costo <= 0:
            continue
        out.append(RigaListino(n, codice.strip(), descrizione, costo,
                               normalizza_ean(cella("ean")), str(cella("marca") or "").strip(),
                               str(cella("um") or "").strip()))
    return out
