"""Cosa è già stato detto su ogni SKU (DESIGN §14). Uno SKU sotto soglia resta
sotto soglia anche domani: senza questa memoria l'avviso diventerebbe il
bollettino quotidiano che Mariano non vuole."""
from __future__ import annotations
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta

from clienti import _chiave
from registro import _nome_cliente
from stock import CRITICO, OK, RigaStock

GIORNI_SOLLECITO = 7
_BASE = "~/.config/newera-amazon/spedizioni/stock"


@dataclass(frozen=True)
class Avviso:
    sku: str
    zona: str
    motivo: str          # nuovo | peggiorato | sollecito
    riga: RigaStock


def percorso(cliente: str) -> str:
    return os.path.expanduser(f"{_BASE}/{_nome_cliente(cliente)}.json")


def carica(cliente: str) -> dict:
    """Uno stato illeggibile vale come vuoto: si riparte da zero e si riavvisa.
    Un doppione è meglio di un silenzio.

    Come scatole.carica_istantanea, il cliente si riconosce a prescindere da
    maiuscole e spazi: `giro.py stock ecm` a mano deve trovare lo stesso stato
    che il LaunchAgent delle 11:00 salva passando da clienti.elenco(), o il
    cliente riceverebbe avvisi doppi."""
    p = percorso(cliente)
    if not os.path.exists(p):
        cartella = os.path.dirname(p)
        if not os.path.isdir(cartella):
            return {}
        chiave = _chiave(cliente)
        trovato = next((n for n in os.listdir(cartella)
                        if os.path.splitext(n)[1] == ".json"
                        and _chiave(os.path.splitext(n)[0]) == chiave), None)
        if trovato is None:
            return {}
        p = os.path.join(cartella, trovato)
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def salva(cliente: str, stato: dict) -> str:
    """Su file di appoggio e poi sostituzione, come il registro: un errore a
    metà scrittura non lascia uno stato troncato (DESIGN §17)."""
    p = percorso(cliente)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    temporaneo = f"{p}.tmp"
    with open(temporaneo, "w") as f:
        f.write(json.dumps(stato, indent=2, ensure_ascii=False))
    os.replace(temporaneo, p)
    return p


def _voce(riga: RigaStock, zona: str, giorno: date) -> dict:
    return {"zona": zona, "dal": giorno.isoformat(), "avvisato_il": giorno.isoformat(),
            "in_arrivo": riga.in_arrivo}


def da_avvisare(zone: list[tuple[RigaStock, str]], stato: dict, oggi: date,
                giorni_sollecito: int = GIORNI_SOLLECITO) -> tuple[list[Avviso], dict]:
    """Gli avvisi da mandare e lo stato da salvare. L'ordine dei controlli è la
    regola: prima la spedizione partita, poi il peggioramento, poi il sollecito."""
    avvisi, nuovo = [], {}
    for riga, zona in zone:
        prima = stato.get(riga.sku)
        if zona == OK:
            continue                                   # la voce sparisce: il prossimo calo è nuovo
        if prima is None:
            avvisi.append(Avviso(riga.sku, zona, "nuovo", riga))
            nuovo[riga.sku] = _voce(riga, zona, oggi)
            continue
        if riga.in_arrivo > int(prima.get("in_arrivo", 0)):
            nuovo[riga.sku] = _voce(riga, zona, oggi)  # spedizione partita: zitti, timer da capo
            continue
        if zona == CRITICO and prima.get("zona") != CRITICO:
            avvisi.append(Avviso(riga.sku, zona, "peggiorato", riga))
            nuovo[riga.sku] = _voce(riga, zona, oggi)
            continue
        avvisato = date.fromisoformat(prima["avvisato_il"])
        if oggi - avvisato >= timedelta(days=giorni_sollecito):
            avvisi.append(Avviso(riga.sku, zona, "sollecito", riga))
            nuovo[riga.sku] = {**prima, "zona": zona, "avvisato_il": oggi.isoformat(),
                               "in_arrivo": riga.in_arrivo}
            continue
        nuovo[riga.sku] = {**prima, "zona": zona, "in_arrivo": riga.in_arrivo}
    return avvisi, nuovo
