from __future__ import annotations
from dataclasses import dataclass
from modifiche import Modifica
from letto import leggi
import registro
from spapi_client import percorso_listing, SpApiError


def costruisci_patch(modifiche: list[Modifica]) -> list[dict]:
    """Un'operazione replace per attributo. Ogni Modifica.dopo deve essere
    già il valore completo da scrivere (modifiche.componi + sostituisci_
    elemento/aggiungi_elemento): qui non si aggrega nulla, si traduce solo
    in JSON Patch. Eccezione: un dopo vuoto con fonte "annulla" è il
    ripristino di un campo aggiunto dal nulla e diventa un op delete —
    per ogni altra fonte resta vietato (guardia anti-svuotamento)."""
    patch = []
    for m in modifiche:
        if m.dopo in (None, "", [], {}):
            if m.fonte != "annulla":
                raise ValueError(f"dopo vuoto su {m.campo} (fonte {m.fonte}): uno svuotamento non si propone mai")
            # Il valore è obbligatorio anche nel delete: Amazon cancella gli
            # elementi elencati, un delete "nudo" è un 400 (smoke 2026-09-02).
            patch.append({"op": "delete", "path": f"/attributes/{m.campo}", "value": m.prima})
        else:
            patch.append({"op": "replace", "path": f"/attributes/{m.campo}", "value": m.dopo})
    return patch


@dataclass
class EsitoVerifica:
    campo: str
    stato: str  # "applicato" | "applicato diversamente" | "non applicato" | "non verificabile: ..."
    atteso: object
    trovato: object
    # Nota: "non applicato" può in realtà significare "non ancora propagato"
    # se la scrittura Amazon è asincrona — punto ancora da verificare dal vivo
    # (DESIGN §9.1); non trattarlo automaticamente come un fallimento.


def applica(client, seller_id: str, marketplace_id: str, cliente: str, sku: str,
            product_type: str, modifiche: list[Modifica], *,
            origine: str = "aggiornamento") -> tuple[str, list[dict], list[EsitoVerifica]]:
    if not modifiche:
        raise ValueError("nessuna modifica da applicare")

    # Un delete passa solo se il value combacia con l'attributo VIVO (Amazon
    # arricchisce, es. language_tag): col valore registrato risponde "errore
    # interno" 4000000. Quindi si rilegge prima (smoke 2026-09-02).
    if any(m.dopo in (None, "", [], {}) for m in modifiche):
        vivo = leggi(client, seller_id, marketplace_id, sku)
        modifiche = [
            Modifica(campo=m.campo, prima=vivo.attributi.get(m.campo, m.prima),
                     dopo=m.dopo, fonte=m.fonte)
            if m.dopo in (None, "", [], {}) else m
            for m in modifiche
        ]

    # La patch si costruisce PRIMA di registrare il tentativo: se la guardia
    # anti-svuotamento la rifiuta, nel registro non resta una riga "in corso"
    # per una scrittura mai partita (collaudo 06/09/2026).
    payload = {"productType": product_type, "patches": costruisci_patch(modifiche)}
    campi_log = {m.campo: {"prima": m.prima, "dopo": m.dopo} for m in modifiche}
    indice = registro.registra_tentativo_aggiornamento(cliente, sku, product_type, campi_log,
                                                        origine=origine)
    try:
        risposta = client.patch(percorso_listing(seller_id, sku),
                                 json_body=payload, params={"marketplaceIds": marketplace_id})
    except SpApiError as e:
        # Il registro non resta mai su "in corso": annulla e le verifiche
        # leggono di li', e un trasporto caduto a meta' deve dirlo.
        registro.registra_esito_aggiornamento(cliente, indice, "ERRORE TRASPORTO",
                                              [{"severity": "ERROR", "message": str(e)}])
        raise
    esito = risposta.get("status", "INVALID")
    issues = risposta.get("issues", [])
    registro.registra_esito_aggiornamento(cliente, indice, esito, issues)

    verifiche: list[EsitoVerifica] = []
    if esito == "ACCEPTED":
        try:
            rilettura = leggi(client, seller_id, marketplace_id, sku)
        except SpApiError as e:
            # Scritto, ACCEPTED e registrato: una rilettura fallita non puo'
            # trasformarlo in "saltato, errore di trasporto" (collaudo 06/09/2026).
            return esito, issues, [EsitoVerifica(campo=m.campo, stato=f"non verificabile: {e}",
                                                 atteso=m.dopo, trovato=None) for m in modifiche]
        for m in modifiche:
            trovato = rilettura.attributi.get(m.campo)
            if trovato == m.dopo:
                stato_v = "applicato"
            elif trovato == m.prima:
                stato_v = "non applicato"
            else:
                stato_v = "applicato diversamente"
            verifiche.append(EsitoVerifica(campo=m.campo, stato=stato_v, atteso=m.dopo, trovato=trovato))
    return esito, issues, verifiche
