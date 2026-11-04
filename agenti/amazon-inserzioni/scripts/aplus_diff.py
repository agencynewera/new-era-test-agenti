from __future__ import annotations
from aplus_componi import PacchettoAplus


def rendi_tabella(pacchetto: PacchettoAplus) -> str:
    if pacchetto.moduli_prima == pacchetto.moduli_dopo:
        return f"{pacchetto.asin}: nessuna modifica proposta (i moduli sono già identici a quelli online)"
    riferimento = pacchetto.content_reference_key or "(nuovo)"
    righe = [f"{pacchetto.asin} — documento {riferimento} — {pacchetto.nome}"]
    if pacchetto.stato_documento or pacchetto.badge_asin:
        righe.append(f"  stato attuale: {pacchetto.stato_documento or 'nessuno'} — "
                      f"badge ASIN: {pacchetto.badge_asin or 'nessuno'}")
    n = max(len(pacchetto.moduli_prima), len(pacchetto.moduli_dopo))
    for i in range(n):
        prima = pacchetto.moduli_prima[i] if i < len(pacchetto.moduli_prima) else None
        dopo = pacchetto.moduli_dopo[i] if i < len(pacchetto.moduli_dopo) else None
        righe.append(f"  modulo {i + 1}: {prima!r} → {dopo!r}")
    return "\n".join(righe)
