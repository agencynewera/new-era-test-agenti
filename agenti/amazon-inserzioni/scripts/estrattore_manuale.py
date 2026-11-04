from __future__ import annotations
import dataclasses
from modello import Prodotto

_OBBLIGATORI = {"sku", "title", "price", "quantity", "product_type"}


def da_dati_manuali(dati: dict) -> Prodotto:
    """Costruisce un Prodotto da ciò che Mariano ha dettato. Non valida
    contro lo schema categoria (è compito di validazione.py): qui si limita
    a mappare, senza inventare nulla di assente."""
    campi_noti = {f.name for f in dataclasses.fields(Prodotto)}
    sconosciuti = set(dati) - campi_noti
    if sconosciuti:
        raise ValueError(f"campi non riconosciuti: {sorted(sconosciuti)}")
    mancanti = _OBBLIGATORI - set(dati)
    if mancanti:
        raise ValueError(f"campi obbligatori mancanti: {sorted(mancanti)}")
    return Prodotto(**dati)
