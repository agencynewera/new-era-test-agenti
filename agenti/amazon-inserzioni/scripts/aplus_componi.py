from __future__ import annotations
from dataclasses import dataclass
from aplus_leggi import StatoAplus


@dataclass
class PacchettoAplus:
    asin: str
    content_reference_key: str | None
    nome: str
    locale: str
    stato_documento: str | None
    badge_asin: str | None
    moduli_prima: list[dict]
    moduli_dopo: list[dict]


def componi(stato: StatoAplus, moduli_nuovi: list[dict], nome: str,
            locale: str | None = None) -> PacchettoAplus:
    """`moduli_nuovi` è SEMPRE la lista completa dei moduli desiderata, mai un
    modulo isolato da aggiungere ai vecchi: come per gli attributi multi-valore
    dei listing, qui l'intero contentModuleList è l'unità che si sostituisce.

    Il locale di un documento già esistente non si cambia mai per iniziativa:
    vince sempre `stato.locale`, anche se il chiamante ne passa uno diverso.
    Solo per un documento nuovo (`stato.locale is None`) il locale del
    chiamante è quello che conta — ed è obbligatorio, perché non c'è nessun
    valore vivo da cui dedurlo."""
    if not moduli_nuovi:
        raise ValueError("nessun modulo proposto: un documento A+ non può restare vuoto")
    if stato.locale is not None:
        locale_effettivo = stato.locale
    elif locale is not None:
        locale_effettivo = locale
    else:
        raise ValueError("locale obbligatorio per un documento nuovo: non si può indovinare dal marketplace")
    return PacchettoAplus(
        asin=stato.asin,
        content_reference_key=stato.content_reference_key,
        nome=nome,
        locale=locale_effettivo,
        stato_documento=stato.stato_documento,
        badge_asin=stato.badge_asin,
        moduli_prima=stato.moduli,
        moduli_dopo=moduli_nuovi,
    )
