from __future__ import annotations
import os
from datetime import datetime

# La cartella di lavoro dei clienti Performance sul Mac di Mariano, creata
# dall'onboarding (clickup-onboarding-cliente, references/local-folders.md).
_BASE = "~/Desktop/LAVORO/AZIENDE NEW ERA/PERFORMANCE"
_MESI = ["GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO",
         "LUGLIO", "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE"]


def cartella_spedizione(cartella_cliente: str | None, shipment_confirmation_id: str,
                        adesso: datetime) -> str:
    """La cartella dove vanno le etichette di una spedizione:
    `<cliente>/10 LOGISTICA/PRIME/<AAAA>/<MM MESE>/<AAAA-MM-GG> <codice FBA>`,
    la stessa struttura che Mariano tiene a mano, con mese numerato e data ISO
    perché in Finder si ordinino da sole. La crea se manca. La cartella del
    cliente invece deve esistere già (la fa l'onboarding): se manca alza
    ValueError e le etichette restano sul Desktop, non finiscono in una
    cartella nuova che nessuno guarda."""
    if not cartella_cliente:
        raise ValueError("cliente senza `cartella` in clienti.yaml")
    base = os.path.expanduser(_BASE)
    cliente = os.path.join(base, cartella_cliente)
    if not os.path.isdir(cliente):
        raise ValueError(f"cartella cliente {cartella_cliente!r} non trovata in {base}")
    locale = adesso.astimezone()   # la data che legge Mariano, non quella UTC del registro
    percorso = os.path.join(cliente, "10 LOGISTICA", "PRIME", f"{locale:%Y}",
                            f"{locale:%m} {_MESI[locale.month - 1]}",
                            f"{locale:%Y-%m-%d} {shipment_confirmation_id}")
    os.makedirs(percorso, exist_ok=True)
    return percorso
