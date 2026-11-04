"""Il foglio per chi decide: riepilogo, una riga per articolo col semaforo,
e i verdi in un foglio «Da comprare». Brand New Era, sul Desktop, mai sopra
un file dello stesso giorno."""
from __future__ import annotations
import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

import margine

VIOLA, BLU = "6027F1", "1C1348"
COLORI = {"verde": "C6EFCE", "giallo": "FFEB9C", "rosso": "FFC7CE", "grigio": "E7E6E6"}
ORDINE = {"verde": 0, "giallo": 1, "rosso": 2, "grigio": 3}

TITOLI = ["Riga", "Codice", "Descrizione fornitore", "U.M.", "Costo €", "EAN", "ASIN", "Prodotto su Amazon",
          "Marca su Amazon", "Abbinamento", "Pezzi nella scheda", "Prezzo IVA incl. €", "Buy box", "Offerte",
          "Offerte FBA", "Amazon vende", "Classifica", "Categoria", "Vendite", "Commissione €",
          "Logistica / spedizione €", "Resi €", "Merce €", "Margine €", "Margine %", "ROI %", "Pareggio IVA incl. €",
          "Restrizioni", "Prova d'offerta", "Semaforo", "Motivi"]
LARGHEZZE = [6, 11, 38, 6, 9, 15, 12, 45, 16, 12, 8, 10, 7, 8, 8, 8, 10, 18, 14, 11, 11, 8, 9, 10, 9, 8, 11, 22, 26, 10, 60]


def _valori(riga) -> list:
    r, abb, off, comm, restr, conto = riga["r"], riga["abb"], riga["off"], riga["comm"], riga["restr"], riga["conto"]
    s = abb.scheda
    prova = riga["prova"] or {}
    confidenza = abb.confidenza + (" · confermato" if riga["sicuro"] and not abb.sicuro else "")
    return [
        r.riga_file, r.codice, r.descrizione, r.um, round(r.costo, 4), r.ean or None,
        s.asin if s else None, s.titolo if s else None, s.marca if s else None, confidenza or None,
        s.pezzi if s else None, off.prezzo if off else None,
        ("sì" if off.da_buy_box else "no") if off and off.prezzo else None,
        off.n_offerte if off else None, off.n_fba if off else None,
        ("sì" if off.amazon_vende else "no") if off else None,
        s.classifica if s else None, s.categoria if s else None,
        margine.fascia(s.classifica) if s else None,
        round(comm.commissione, 2) if comm and comm.commissione is not None else None,
        conto.logistica if conto else None, conto.resi if conto else None, conto.merce if conto else None,
        conto.margine if conto else None, conto.margine_pct if conto else None,
        conto.roi if conto else None, conto.pareggio if conto else None,
        (", ".join(sorted(set(restr.blocchi))) or "nessuna") if restr else None,
        (prova.get("stato", "") + (": " + "; ".join(prova.get("errori") or []) if prova.get("errori") else ""))
        if prova else None,
        riga["esito"].colore.upper(), "; ".join(riga["esito"].motivi) or None,
    ]


def _tabella(ws, righe, riga_inizio: int):
    for j, t in enumerate(TITOLI, 1):
        c = ws.cell(riga_inizio, j, t)
        c.font = Font(name="Nunito", bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=VIOLA)
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, riga in enumerate(righe, riga_inizio + 1):
        for j, v in enumerate(_valori(riga), 1):
            ws.cell(i, j, v).font = Font(name="Nunito")
        ws.cell(i, 30).fill = PatternFill("solid", fgColor=COLORI[riga["esito"].colore])
        ws.cell(i, 30).font = Font(name="Nunito", bold=True)
    for j, w in enumerate(LARGHEZZE, 1):
        ws.column_dimensions[ws.cell(1, j).column_letter].width = w
    ws.freeze_panes = ws.cell(riga_inizio + 1, 4)
    ws.auto_filter.ref = f"A{riga_inizio}:{ws.cell(riga_inizio, len(TITOLI)).column_letter}{riga_inizio + len(righe)}"


def scrivi(cliente: str, nome_listino: str, righe, p, adesso, totale_listino: int, da: int,
           cartella: str = "~/Desktop") -> str:
    ordinate = sorted(righe, key=lambda x: (ORDINE[x["esito"].colore],
                                            -(x["conto"].margine if x["conto"] else -1e9)))
    wb = Workbook()
    ws = wb.active
    ws.title = "Riepilogo"
    ws["B2"] = f"Controllo listino su Amazon — {cliente}"
    ws["B2"].font = Font(name="Nunito", size=16, bold=True, color=BLU)
    ws["B3"] = (f"Listino «{nome_listino}» · articoli {da}-{da + len(righe) - 1} di {totale_listino} · "
                f"dati letti da Amazon il {adesso:%d/%m/%Y %H:%M}")
    ws["B3"].font = Font(name="Nunito", size=10, color=BLU)
    conta = {k: sum(1 for r in righe if r["esito"].colore == k) for k in ORDINE}
    voci = [("Verdi: da comprare", conta["verde"]), ("Gialli: da guardare", conta["giallo"]),
            ("Rossi: non conviene o non vendibile", conta["rosso"]),
            ("Grigi: non trovati o non calcolabili", conta["grigio"]), ("", None),
            ("Logistica", "FBA (logistica Amazon)" if p.fba else f"spedizione propria, {p.spedizione:.2f} € a collo"),
            ("Margine minimo", f"{p.soglia:g}%"), ("Trasporto dal fornitore", f"{p.trasporto:.2f} € a pezzo"),
            ("IVA", f"{p.iva:g}%"), ("Resi", f"{p.resi:g}% del prezzo senza IVA"), ("Pubblicità", "esclusa")]
    for i, (k, v) in enumerate(voci, 5):
        ws.cell(i, 2, k).font = Font(name="Nunito", bold=bool(v is not None and i < 9))
        ws.cell(i, 3, v).font = Font(name="Nunito")
        if i < 9:
            ws.cell(i, 2).fill = PatternFill("solid", fgColor=COLORI[list(ORDINE)[i - 5]])
    note = [
        "Come leggere",
        "Prezzo = buy box IVA inclusa (prezzo + spedizione); senza buy box, l'offerta più bassa.",
        "Commissione dalla stima di Amazon sul prezzo; «commissione stimata al 15%» = riga già in perdita, non chiesta ad Amazon.",
        "Vendite = fascia dalla classifica della categoria principale (≤20.000 vende bene, ≤100.000 vende, ≤250.000 poco). Non sono vendite al mese.",
        "Abbinamento: EAN = sicuro; alta/media/bassa = trovato per descrizione, da confermare prima di comprare.",
        "Prova d'offerta = validazione di Amazon senza scrivere: vede i blocchi su schede generiche che le restrizioni non mostrano.",
        "Verde = EAN o confermato, margine ≥ soglia + 5 punti, classifica ≤100.000, nessun blocco, prova valida, Amazon non vende, meno di 10 offerte.",
    ]
    for i, t in enumerate(note, 18):
        ws.cell(i, 2, t).font = Font(name="Nunito", bold=(i == 18), color=BLU)
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 40

    tutti = wb.create_sheet("Controllo")
    _tabella(tutti, ordinate, 1)
    verdi = wb.create_sheet("Da comprare")
    _tabella(verdi, [r for r in ordinate if r["esito"].colore == "verde"], 1)

    pulito = "".join(c if c.isalnum() else "-" for c in cliente)
    base = os.path.expanduser(f"{cartella}/Controllo-listino-{pulito}-{adesso:%Y-%m-%d}")
    percorso, n = f"{base}.xlsx", 1
    while os.path.exists(percorso):
        n += 1
        percorso = f"{base}-{n}.xlsx"
    wb.save(percorso)
    return percorso
