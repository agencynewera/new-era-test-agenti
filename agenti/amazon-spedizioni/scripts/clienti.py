from __future__ import annotations
import os
import yaml

_FILE_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clienti.yaml")

# Campi senza i quali createInboundPlan risponde 400 (misurato il 31/08/2026:
# phoneNumber vuoto). Controllarli qui evita un giro perso contro Amazon.
_CAMPI_INDIRIZZO = ("name", "addressLine1", "city", "postalCode", "countryCode", "phoneNumber")


def _chiave(nome: str) -> str:
    return "".join(ch for ch in nome.upper() if ch.isalnum())


def carica_cliente(nome: str, percorso: str = _FILE_DEFAULT) -> dict:
    """Configurazione locale di un cliente: `marketplace_id`, `indirizzo_origine`
    (forma `sourceAddress` di createInboundPlan), `contatto` (forma
    `contactInformation` di generateTransportationOptions) e `cartella` (il
    nome della sua cartella in AZIENDE NEW ERA/PERFORMANCE, dove finiscono le
    etichette; None se non configurata). Il nome è case-insensitive e ignora
    spazi/trattini, come il prefisso di crea_client."""
    with open(percorso) as f:
        tutti = yaml.safe_load(f) or {}
    voce = next((v for k, v in tutti.items() if _chiave(k) == _chiave(nome)), None)
    if voce is None:
        raise ValueError(f"cliente {nome!r} assente in {os.path.basename(percorso)} ({percorso})")
    indirizzo = voce.get("indirizzo_origine") or {}
    mancanti = [c for c in _CAMPI_INDIRIZZO if not str(indirizzo.get(c) or "").strip()]
    if mancanti:
        raise ValueError(
            f"indirizzo_origine incompleto per {nome}: manca {', '.join(mancanti)}"
        )
    cartella = voce.get("cartella")
    if cartella is not None:
        cartella = str(cartella)
        # Diventa un pezzo di percorso: un "../" porterebbe le etichette fuori
        # dalle cartelle clienti. Solo il nome della cartella, senza barre.
        if "/" in cartella or cartella in (".", ".."):
            raise ValueError(f"cartella non valida per {nome}: {cartella!r} (solo il nome, senza barre)")
    return {
        "marketplace_id": voce["marketplace_id"],
        "indirizzo_origine": {k: str(v) for k, v in indirizzo.items()},
        "contatto": {k: str(v) for k, v in (voce.get("contatto") or {}).items()},
        "cartella": cartella,
    }


def elenco(percorso: str = _FILE_DEFAULT) -> list[str]:
    """I clienti configurati, nell'ordine del file. Il giro dello stock parte
    da qui e non dalle credenziali in ~/scripts/.env: senza marketplace_id non
    si sa che report chiedere."""
    with open(percorso) as f:
        return list((yaml.safe_load(f) or {}).keys())
