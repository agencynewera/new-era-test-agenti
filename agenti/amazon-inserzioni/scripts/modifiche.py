from __future__ import annotations
from dataclasses import dataclass
from letto import StatoAttuale


@dataclass
class Modifica:
    campo: str
    prima: object
    dopo: object
    fonte: str


def componi(stato: StatoAttuale, proposte: dict, fonte: str) -> list[Modifica]:
    """`proposte` è {attributo: nuovo_valore_completo}. Un attributo
    multi-valore deve arrivare già completo — costruito con
    sostituisci_elemento/aggiungi_elemento, mai un singolo elemento isolato.
    Un valore "dopo" vuoto non tocca mai un campo già pubblicato: si scarta
    sempre, non si propone come svuotamento (Global Constraints)."""
    modifiche = []
    for campo, dopo in proposte.items():
        if dopo in (None, "", [], {}):
            continue
        prima = stato.attributi.get(campo)
        if prima == dopo:
            continue
        modifiche.append(Modifica(campo=campo, prima=prima, dopo=dopo, fonte=fonte))
    return modifiche


def sostituisci_elemento(valori_attuali: list, indice: int, nuovo_valore) -> list:
    """L'intero elenco con SOLO l'elemento all'indice indicato cambiato.
    Amazon sostituisce l'attributo per intero: omettere un elemento
    equivale a cancellarlo."""
    if not (0 <= indice < len(valori_attuali)):
        raise ValueError(f"indice {indice} fuori dai {len(valori_attuali)} valori attuali")
    nuovi = list(valori_attuali)
    nuovi[indice] = nuovo_valore
    return nuovi


def aggiungi_elemento(valori_attuali: list, nuovo_valore) -> list:
    return list(valori_attuali) + [nuovo_valore]
