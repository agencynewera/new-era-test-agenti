"""Margine e semaforo di una riga. Conti puri, nessuna chiamata.

margine = prezzo senza IVA − commissione − logistica (FBA) o spedizione (FBM)
          − merce × pezzi della scheda − trasporto − resi
Niente pubblicità: nel reselling non si fanno campagne (regola Cambioni del
26/09/2026); chi la vuole la toglie dal margine guardando la colonna."""
from __future__ import annotations
from dataclasses import dataclass, field

# Fasce di vendita dalla classifica della categoria principale, quelle usate
# sul reselling Cambioni il 20/10/2026. Sono fasce, non vendite: senza uno
# strumento esterno le vendite al mese non si conoscono.
FASCE = ((20_000, "vende bene"), (100_000, "vende"), (250_000, "vende poco"))
MOLTE_OFFERTE = 10
MARGINE_AL_LIMITE = 5        # punti sopra la soglia entro cui il margine è «al limite»


@dataclass
class Parametri:
    fba: bool = False
    soglia: float = 15.0          # margine minimo %
    spedizione: float = 4.50      # FBM, € a collo (stima finché il cliente non dà il suo)
    trasporto: float = 0.0        # dal fornitore a noi, € a pezzo
    iva: float = 22.0
    resi: float = 5.0             # % del prezzo senza IVA


@dataclass
class Conto:
    prezzo_netto: float
    merce: float
    logistica: float
    resi: float
    margine: float
    margine_pct: float
    roi: float | None
    pareggio: float | None        # prezzo IVA inclusa a margine zero


def fascia(classifica: int | None) -> str:
    if classifica is None:
        return "senza classifica"
    for tetto, nome in FASCE:
        if classifica <= tetto:
            return nome
    return "quasi ferma"


def calcola(prezzo: float, commissione: float, costo: float, pezzi: int, p: Parametri,
            logistica_fba: float | None = None) -> Conto:
    netto = prezzo / (1 + p.iva / 100)
    merce = costo * pezzi
    logistica = (logistica_fba or 0.0) if p.fba else p.spedizione
    resi = netto * p.resi / 100
    margine = netto - commissione - logistica - merce - p.trasporto - resi
    spesa = merce + p.trasporto
    # Pareggio con la commissione in percentuale costante sul prezzo lordo.
    quota_comm = commissione / prezzo if prezzo else 0
    divisore = (1 - p.resi / 100) / (1 + p.iva / 100) - quota_comm
    pareggio = round((merce + p.trasporto + logistica) / divisore, 2) if divisore > 0 else None
    return Conto(round(netto, 2), round(merce, 2), round(logistica, 2), round(resi, 2),
                 round(margine, 2), round(100 * margine / netto, 1) if netto else 0.0,
                 round(100 * margine / spesa, 1) if spesa else None, pareggio)


@dataclass
class Esito:
    colore: str                   # verde, giallo, rosso, grigio (non trovato o non calcolabile)
    motivi: list[str] = field(default_factory=list)


def semaforo(*, trovato: bool, sicuro: bool, conto: Conto | None, classifica: int | None,
             n_offerte: int, amazon_vende: bool, blocchi: list[str], approvazione: bool,
             prova: str | None, a_pezzo: bool, p: Parametri) -> Esito:
    """Verde solo se tutto torna: abbinamento per EAN (o confermato), margine
    almeno 5 punti sopra la soglia, classifica ≤100.000, nessuna restrizione,
    prova d'offerta VALID, Amazon non vende, meno di 10 offerte."""
    if not trovato:
        return Esito("grigio", ["non trovato su Amazon"])
    if not a_pezzo:
        return Esito("grigio", ["costo per unità di misura: serve il prezzo a confezione"])
    if conto is None:
        return Esito("grigio", ["prezzo o commissione non disponibili"])
    rossi, gialli = [], []
    if conto.margine_pct < p.soglia:
        rossi.append(f"margine {conto.margine_pct:g}% sotto la soglia del {p.soglia:g}%")
    elif conto.margine_pct < p.soglia + MARGINE_AL_LIMITE:
        gialli.append(f"margine al limite ({conto.margine_pct:g}%)")
    nome_fascia = fascia(classifica)
    if nome_fascia in ("quasi ferma", "senza classifica"):
        rossi.append(f"vendite: {nome_fascia}")
    elif nome_fascia == "vende poco":
        gialli.append("vendite: vende poco")
    if blocchi and not approvazione:
        rossi.append("non vendibile: " + ", ".join(sorted(set(blocchi))))
    elif approvazione:
        gialli.append("serve l'approvazione della marca o della categoria")
    if prova == "INVALID":
        rossi.append("offerta rifiutata alla prova (scheda con restrizioni)")
    elif prova is None and not rossi:
        gialli.append("prova d'offerta non fatta")
    if not sicuro:
        gialli.append("abbinamento da confermare")
    if amazon_vende:
        gialli.append("Amazon vende")
    if n_offerte >= MOLTE_OFFERTE:
        gialli.append(f"{n_offerte} offerte")
    if rossi:
        return Esito("rosso", rossi + gialli)
    if gialli:
        return Esito("giallo", gialli)
    return Esito("verde", [])
