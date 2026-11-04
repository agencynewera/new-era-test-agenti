from __future__ import annotations
from dataclasses import dataclass
from aplus_componi import PacchettoAplus
from aplus_leggi import badge_da_relazioni
import registro
from spapi_client import SpApiError


def _issues(risposta: dict) -> list[dict]:
    """A+ Content separa errori e avvisi in due liste ("errors"/"warnings"),
    diversamente dalla Listings Items API che li fonde in "issues" con una
    severità. Si fondono qui per riusare lo stesso schema del resto della
    skill, marcando la provenienza con "severity"."""
    issues = []
    for e in risposta.get("errors", []):
        issues.append({**e, "severity": "ERROR"})
    for w in risposta.get("warnings", []):
        issues.append({**w, "severity": "WARNING"})
    return issues


@dataclass
class EsitoAplus:
    content_reference_key: str
    esito: str  # "ACCEPTED" | "INVALID"
    issues: list[dict]
    badge_asin: str | None


def applica(client, marketplace_id: str, cliente: str, pacchetto: PacchettoAplus, *,
            origine: str = "contenuti-aplus") -> EsitoAplus:
    if not pacchetto.moduli_dopo:
        raise ValueError("nessun modulo da scrivere: uno svuotamento non si propone mai")
    if pacchetto.moduli_dopo == pacchetto.moduli_prima:
        raise ValueError("nessuna modifica da applicare")

    campi_log = {"nome": pacchetto.nome, "moduli_prima": pacchetto.moduli_prima,
                 "moduli_dopo": pacchetto.moduli_dopo}
    indice = registro.registra_tentativo_aplus(cliente, pacchetto.content_reference_key or "",
                                                pacchetto.asin, campi_log, origine=origine)

    corpo = {"contentDocument": {
        "name": pacchetto.nome, "contentType": "EBC", "locale": pacchetto.locale,
        "contentModuleList": pacchetto.moduli_dopo,
    }}
    try:
        if pacchetto.content_reference_key:
            risposta = client.post(f"/aplus/2020-11-01/contentDocuments/{pacchetto.content_reference_key}",
                                    json_body=corpo, params={"marketplaceId": marketplace_id})
            content_reference_key = pacchetto.content_reference_key
        else:
            risposta = client.post("/aplus/2020-11-01/contentDocuments",
                                    json_body=corpo, params={"marketplaceId": marketplace_id})
            content_reference_key = risposta.get("contentReferenceKey", "")

        issues = _issues(risposta)
        esito = "INVALID" if any(i["severity"] == "ERROR" for i in issues) else "ACCEPTED"

        badge_asin = None
        if esito == "ACCEPTED":
            risposta_link = client.post(f"/aplus/2020-11-01/contentDocuments/{content_reference_key}/asins",
                                         json_body={"asinSet": [pacchetto.asin]}, params={"marketplaceId": marketplace_id})
            issues_link = _issues(risposta_link)
            issues += issues_link
            if any(i["severity"] == "ERROR" for i in issues_link):
                esito = "INVALID"
            else:
                risposta_submit = client.post(f"/aplus/2020-11-01/contentDocuments/{content_reference_key}/approvalSubmissions",
                                               json_body={}, params={"marketplaceId": marketplace_id})
                issues_submit = _issues(risposta_submit)
                issues += issues_submit
                if any(i["severity"] == "ERROR" for i in issues_submit):
                    esito = "INVALID"
                else:
                    rilettura = client.get(f"/aplus/2020-11-01/contentDocuments/{content_reference_key}/asins",
                                            params={"marketplaceId": marketplace_id, "asinSet": pacchetto.asin,
                                                    "includedDataSet": "METADATA"})
                    badge_asin = badge_da_relazioni(rilettura, pacchetto.asin)
    except SpApiError as e:
        # Il registro non resta mai su "in corso": una catena caduta a meta'
        # (creato ma non collegato, collegato ma non sottomesso) deve dirlo.
        registro.registra_esito_aplus(cliente, indice, "ERRORE TRASPORTO",
                                      [{"severity": "ERROR", "message": str(e)}])
        raise

    registro.registra_esito_aplus(cliente, indice, esito, issues)
    return EsitoAplus(content_reference_key=content_reference_key, esito=esito,
                       issues=issues, badge_asin=badge_asin)
