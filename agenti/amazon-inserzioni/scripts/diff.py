from __future__ import annotations
from modifiche import Modifica


def rendi_tabella(sku: str, modifiche: list[Modifica]) -> str:
    if not modifiche:
        return f"{sku}: nessuna modifica proposta (tutti i valori sono già identici a quelli online)"
    righe = [sku]
    for m in modifiche:
        righe.append(f"  {m.campo}: {m.prima!r} → {m.dopo!r}  [{m.fonte}]")
    return "\n".join(righe)
