"""Quanto mandare in FBA per coprire un periodo (DESIGN §19). Sola lettura.

Il ritmo di vendita si ricalcola dalle settimane in cui lo SKU era a stock,
non dal `Units Sold Last 30 Days` del report Restock, che è cieco sugli
esauriti e gonfiato dagli eventi (wiki: Report Restock e consiglio di
riassortimento)."""
from __future__ import annotations
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from spapi_client import SpApiError

_VENDITE = "/sales/v1/orderMetrics"
SETTIMANE = 13
SETTIMANE_RECENTI = 4
PESO_RECENTI = 0.6
# Uno zero in mezzo a settimane da almeno 5 pezzi è un esaurito, non una
# settimana senza domanda (a 5 di media, una settimana a zero per caso è sotto
# l'1%). Sotto, lo zero è plausibile: con 2 si toglieva lo zero di Reladol_300,
# che a 3 pezzi la settimana capita (collaudo del 01/11).
MINIMO_PER_ESAURITO = 5
_ROMA = ZoneInfo("Europe/Rome")
# Una settimana oltre 3 volte la mediana è un evento (B2B, offerta lampo):
# si conta come la mediana, e la riga lo dice.
TETTO_PICCO = 3
# ...ma solo sopra i 10 pezzi: per un articolo da 1 pezzo la settimana, 4 pezzi
# non sono un evento (CALMAPROST_FBA, collaudo del 01/11).
PICCO_MINIMO = 10
SETTIMANE_MINIME = 2


def settimane_vendute(client, sku: str, marketplace_id: str, adesso: datetime,
                      settimane: int = SETTIMANE) -> list[int] | None:
    """Le unità FBA vendute per settimana intera, dalla più vecchia alla più
    recente; la settimana in corso resta fuori perché è parziale. `None` se
    Amazon non risponde: una serie di zeri inventata direbbe 'non vende'."""
    locale = adesso.astimezone(_ROMA)
    lunedi = (locale - timedelta(days=locale.weekday())).date()
    inizio = lunedi - timedelta(weeks=settimane)
    # L'offset di ogni estremo è quello di Roma in quel giorno: estate +02:00, inverno +01:00.
    da, a = (datetime(d.year, d.month, d.day, tzinfo=_ROMA).isoformat() for d in (inizio, lunedi))
    try:
        risposta = client.get(_VENDITE, params={
            "marketplaceIds": marketplace_id,
            "interval": f"{da}--{a}",
            "granularity": "Week", "granularityTimeZone": "Europe/Rome",
            "fulfillmentNetwork": "AFN", "sku": sku})
    except SpApiError:
        return None
    voci = risposta.get("payload")
    if voci is None:
        return None
    return [int(v.get("unitCount") or 0) for v in voci][-settimane:]


@dataclass
class Stima:
    sku: str
    asin: str
    disponibili: int
    in_arrivo: int
    al_giorno: float
    fabbisogno: int
    da_inviare: int
    settimane_valide: int = 0
    note: list[str] = field(default_factory=list)

    @property
    def copertura_giorni(self) -> float:
        if self.al_giorno <= 0:
            return float("inf")
        return (self.disponibili + self.in_arrivo) / self.al_giorno

    @property
    def debole(self) -> bool:
        return any(n.startswith("stima debole") for n in self.note)


def ritmo(serie: list[int]) -> tuple[float, int, list[str]]:
    """Pezzi al giorno dalle settimane a stock: media delle ultime 4 valide
    pesata 0,6 e di tutte le valide 0,4. Torna anche quante settimane sono
    valide e cosa è stato tolto, perché chi legge possa rifare il conto."""
    note = []
    piene = [s for s in serie if s > 0]
    if not piene:
        return 0.0, 0, note
    mediana = statistics.median(piene)
    if mediana >= MINIMO_PER_ESAURITO:
        valide = piene
        tolte = len(serie) - len(piene)
        if tolte:
            note.append(f"{tolte} settimane a zero tolte (esaurito)")
    else:
        # Articolo lento: gli zeri sono vendite vere, si tengono dalla prima
        # settimana venduta in poi (prima, l'articolo forse non c'era).
        primo = next(i for i, s in enumerate(serie) if s > 0)
        valide = serie[primo:]
    tetto = max(mediana * TETTO_PICCO, PICCO_MINIMO)
    picchi = [s for s in valide if s > tetto]
    if picchi:
        note.append(f"picco di {max(picchi)} pz in una settimana contato come {mediana:g}")
        valide = [mediana if s > tetto else s for s in valide]
    recenti = valide[-SETTIMANE_RECENTI:]
    settimanale = (PESO_RECENTI * statistics.fmean(recenti)
                   + (1 - PESO_RECENTI) * statistics.fmean(valide))
    return settimanale / 7, len(valide), note


def stima_sku(riga, serie: list[int] | None, giorni: int, transito: int, scorta: int) -> Stima:
    """fabbisogno = ritmo × (periodo + transito + scorta); da inviare = fabbisogno
    meno ciò che è già in magazzino o in arrivo. Il collo pieno lo mette dopo
    chi conosce il modello d'imballaggio."""
    note = []
    if serie is None:
        al_giorno, valide = riga.venduti_30gg / 30, 0
        note.append("stima debole: vendite settimanali non lette, uso il venduto a 30 giorni del report")
    else:
        al_giorno, valide, note = ritmo(serie)
        if 0 < valide < SETTIMANE_MINIME:
            note.append(f"stima debole: solo {valide} settimana di vendite")
    # In magazzino conta anche ciò che Amazon sta smistando; in arrivo solo ciò
    # che è partito: i piani in WORKING possono non partire mai (wiki, Report Restock).
    magazzino = riga.disponibili + riga.in_lavorazione
    in_arrivo = max(0, riga.in_arrivo - riga.in_working)
    if riga.in_working:
        note.append(f"{riga.in_working} pz in piani WORKING non contati: se partono, toglili dal da inviare")
    fabbisogno = round(al_giorno * (giorni + transito + scorta))
    da_inviare = max(0, fabbisogno - magazzino - in_arrivo)
    return Stima(riga.sku, riga.asin, magazzino, in_arrivo, al_giorno,
                 fabbisogno, da_inviare, valide, note)


def giorni_periodo(adesso: datetime, giorni: int | None, fino: str | None) -> int:
    if (giorni is None) == (fino is None):
        raise ValueError("indica il periodo: --giorni N oppure --fino AAAA-MM-GG")
    if fino is not None:
        fine = datetime.strptime(fino, "%Y-%m-%d").date()
        giorni = (fine - adesso.date()).days
    if giorni < 1:
        raise ValueError("il periodo da coprire deve essere di almeno un giorno")
    return giorni


VIOLA, BLU = "6027F1", "1C1348"


def scrivi_excel(cliente: str, stime: list[Stima], colli: dict[str, str], consigli: dict[str, int],
                 intestazione: str, adesso: datetime, cartella: str = "~/Desktop") -> str:
    """Il foglio per chi decide: una riga per SKU, prima quelli da mandare.
    Sul Desktop come ogni consegna, mai sopra un file dello stesso giorno."""
    import os
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "Stima"
    ws["A1"] = f"Stima rifornimento FBA — {cliente}"
    ws["A1"].font = Font(name="Nunito", size=16, bold=True, color=BLU)
    ws["A2"] = intestazione
    ws["A2"].font = Font(name="Nunito", size=10, color=BLU)
    titoli = ["SKU", "ASIN", "Disponibili", "In arrivo", "Pz al giorno", "Copertura (gg)",
              "Fabbisogno", "Da inviare", "Colli", "Consiglio Amazon", "Settimane valide", "Note"]
    ws.append([])
    ws.append(titoli)
    for cella in ws[4]:
        cella.font = Font(name="Nunito", bold=True, color="FFFFFF")
        cella.fill = PatternFill("solid", fgColor=VIOLA)
        cella.alignment = Alignment(wrap_text=True, vertical="center")
    for s in sorted(stime, key=lambda s: (-s.da_inviare, s.sku)):
        copertura = None if s.copertura_giorni == float("inf") else round(s.copertura_giorni)
        ws.append([s.sku, s.asin, s.disponibili, s.in_arrivo, round(s.al_giorno, 2), copertura,
                   s.fabbisogno, s.da_inviare, colli.get(s.sku, ""), consigli.get(s.sku),
                   s.settimane_valide, "; ".join(s.note)])
        for cella in ws[ws.max_row]:
            cella.font = Font(name="Nunito", bold=cella.column == 8 and s.da_inviare > 0)
    for lettera, larghezza in zip("ABCDEFGHIJKL", (22, 13, 11, 10, 11, 13, 11, 11, 30, 12, 11, 60)):
        ws.column_dimensions[lettera].width = larghezza
    ws.freeze_panes = "A5"
    nome = "".join(c if c.isalnum() else "-" for c in cliente)
    base = os.path.expanduser(f"{cartella}/Stima-FBA-{nome}-{adesso:%Y-%m-%d}")
    percorso, n = f"{base}.xlsx", 1
    while os.path.exists(percorso):
        n += 1
        percorso = f"{base}-{n}.xlsx"
    wb.save(percorso)
    return percorso
