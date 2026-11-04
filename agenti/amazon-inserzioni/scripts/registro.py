from __future__ import annotations
import json
import os
from datetime import datetime, timezone


def slug_cliente(cliente: str) -> str:
    """"Phy to Pet", "PHYTOPET" e "phy-to-pet" sono lo stesso cliente: la
    stessa classe di equivalenza con cui spapi_client risolve le credenziali
    (solo alfanumerici), cosi' il registro segue l'account che si scrive e
    nessun carattere di percorso puo' portare il file fuori dalla cartella."""
    return "".join(ch for ch in cliente.lower() if ch.isascii() and ch.isalnum())


def _percorso(cliente: str, cartella: str) -> str:
    base = os.path.expanduser(f"~/.config/newera-amazon/{cartella}")
    os.makedirs(base, exist_ok=True)
    data = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(base, f"{slug_cliente(cliente)}-{data}.json")


def _carica(percorso: str) -> list[dict]:
    if not os.path.exists(percorso):
        return []
    with open(percorso) as f:
        return json.load(f)


def _salva(percorso: str, righe: list[dict]) -> None:
    with open(percorso, "w") as f:
        json.dump(righe, f, indent=2, ensure_ascii=False)


def registra_tentativo_creazione(cliente: str, sku: str) -> int:
    percorso = _percorso(cliente, "pubblicazioni")
    righe = _carica(percorso)
    righe.append({
        "sku": sku, "ora": datetime.now(timezone.utc).isoformat(),
        "esito": "in corso", "issues": [],
    })
    _salva(percorso, righe)
    return len(righe) - 1


def registra_esito_creazione(cliente: str, indice: int, esito: str, issues: list[dict]) -> None:
    percorso = _percorso(cliente, "pubblicazioni")
    righe = _carica(percorso)
    righe[indice]["esito"] = esito
    righe[indice]["issues"] = issues
    _salva(percorso, righe)


def registra_tentativo_aggiornamento(cliente: str, sku: str, product_type: str,
                                      campi: dict[str, dict], *,
                                      origine: str = "aggiornamento") -> int:
    percorso = _percorso(cliente, "aggiornamenti")
    righe = _carica(percorso)
    righe.append({
        "sku": sku, "product_type": product_type, "origine": origine,
        "ora": datetime.now(timezone.utc).isoformat(),
        "campi": campi, "esito": "in corso", "issues": [],
    })
    _salva(percorso, righe)
    return len(righe) - 1


def registra_esito_aggiornamento(cliente: str, indice: int, esito: str, issues: list[dict]) -> None:
    percorso = _percorso(cliente, "aggiornamenti")
    righe = _carica(percorso)
    righe[indice]["esito"] = esito
    righe[indice]["issues"] = issues
    _salva(percorso, righe)


def ultimo_lotto(cliente: str, cartella: str) -> list[dict]:
    return _carica(_percorso(cliente, cartella))


def registra_tentativo_aplus(cliente: str, content_reference_key: str, asin: str,
                              moduli: dict, *, origine: str = "contenuti-aplus") -> int:
    percorso = _percorso(cliente, "contenuti-aplus")
    righe = _carica(percorso)
    righe.append({
        "content_reference_key": content_reference_key, "asin": asin, "origine": origine,
        "ora": datetime.now(timezone.utc).isoformat(),
        "moduli": moduli, "esito": "in corso", "issues": [],
    })
    _salva(percorso, righe)
    return len(righe) - 1


def registra_esito_aplus(cliente: str, indice: int, esito: str, issues: list[dict]) -> None:
    percorso = _percorso(cliente, "contenuti-aplus")
    righe = _carica(percorso)
    righe[indice]["esito"] = esito
    righe[indice]["issues"] = issues
    _salva(percorso, righe)
