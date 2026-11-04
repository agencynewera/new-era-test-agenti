from __future__ import annotations
import registro
from spapi_client import percorso_listing, SpApiError


def cancella(client, seller_id: str, marketplace_id: str, cliente: str,
             sku: str) -> tuple[str, list[dict]]:
    """Cancella l'inserzione `sku` su `marketplace_id`. Ritira SOLO la nostra
    offerta: l'ASIN a catalogo resta, insieme a recensioni e cronologia.

    Azione ROSSA (CLAUDE.md §5): irreversibile dal nostro lato — non esiste un
    "annulla" che la rimetta, va ricreata da zero con crea.pubblica_uno. Va
    chiamata solo dopo un ordine esplicito di Mariano per quello SKU, e dopo
    aver letto cosa si sta cancellando: uno SKU padre con figli ancora
    agganciati lascia la famiglia senza padre.

    Lascia traccia nel registro degli aggiornamenti con origine
    "cancellazione", come ogni altra scrittura della skill: `annulla` la
    ignora (non ha campi da ripristinare), ma resta la prova di cosa e
    quando."""
    percorso = percorso_listing(seller_id, sku)  # alza SpApiError su sku vuoto
    indice = registro.registra_tentativo_aggiornamento(
        cliente, sku, "", {}, origine="cancellazione")
    try:
        risposta = client.delete(percorso, params={"marketplaceIds": marketplace_id})
    except SpApiError as e:
        # Stessa disciplina di applica: il registro non resta mai su "in corso".
        registro.registra_esito_aggiornamento(
            cliente, indice, "ERRORE TRASPORTO", [{"severity": "ERROR", "message": str(e)}])
        raise
    esito = risposta.get("status", "INVALID")
    issues = risposta.get("issues", [])
    registro.registra_esito_aggiornamento(cliente, indice, esito, issues)
    return esito, issues
