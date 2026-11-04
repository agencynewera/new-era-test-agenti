from __future__ import annotations
from modello import OpzioneImballaggio, Scatola
from piano import id_operazione, leggi_vincoli_prep
from spapi_client import SpApiError, motivo_fallimento

_BASE = "/inbound/fba/2024-03-20/inboundPlans"


def _path_opzioni_imballaggio(plan_id: str) -> str:
    return f"{_BASE}/{plan_id}/packingOptions"


def genera_opzioni_imballaggio(client, plan_id: str) -> list[OpzioneImballaggio]:
    """Chiede ad Amazon come impacchettare il piano (POST asincrona), poi
    legge le opzioni proposte."""
    risposta = client.post(_path_opzioni_imballaggio(plan_id))
    esito = client.attendi_operazione(id_operazione(risposta, "generatePackingOptions"))
    if esito["operationStatus"] != "SUCCESS":
        raise SpApiError(f"opzioni imballaggio non generate: {motivo_fallimento(esito)}")
    elenco = client.get(_path_opzioni_imballaggio(plan_id))
    opzioni = []
    for o in elenco.get("packingOptions", []):
        gruppi = list(o.get("packingGroups") or [])
        opzioni.append(OpzioneImballaggio(
            packing_option_id=o["packingOptionId"],
            descrizione=f"{len(gruppi)} gruppo/i di imballaggio",
            packing_group_ids=gruppi,
        ))
    return opzioni


def conferma_imballaggio(client, plan_id: str, packing_option_id: str) -> None:
    risposta = client.post(f"{_path_opzioni_imballaggio(plan_id)}/{packing_option_id}/confirmation")
    esito = client.attendi_operazione(id_operazione(risposta, "confirmPackingOption"))
    if esito["operationStatus"] != "SUCCESS":
        raise SpApiError(f"imballaggio non confermato: {motivo_fallimento(esito)}")


def imposta_informazioni_imballaggio(client, plan_id: str,
                                      scatole_per_gruppo: dict[str, list[Scatola]],
                                      marketplace_id: str) -> None:
    """`setPackingInformation`: dice ad Amazon quante scatole ci sono per
    ogni gruppo di imballaggio, quanto pesano e quanto misurano — i dati che
    Amazon non può sapere da solo e che detta Mariano.

    Va chiamata dopo `conferma_imballaggio` e prima di generare le opzioni di
    piazzamento: senza queste misure Amazon non sa dividere il piano fra
    centri logistici né quotare un corriere."""
    if not scatole_per_gruppo:
        raise ValueError("nessun gruppo di imballaggio: dati scatole mancanti")
    # Gli stessi owner dichiarati nel piano devono tornare identici qui dentro,
    # per ogni articolo di ogni scatola: Amazon confronta le due dichiarazioni e
    # rifiuta con "did not contain expected items and/or quantities" se non
    # combaciano. Misurato dal vivo il 2026-08-29.
    mskus = sorted({r.sku for scatole in scatole_per_gruppo.values()
                     for s in scatole for r in s.righe})
    vincoli = leggi_vincoli_prep(client, mskus, marketplace_id) if mskus else {}
    raggruppamenti = []
    for gruppo_id, scatole in scatole_per_gruppo.items():
        if not scatole:
            raise ValueError(f"nessuna scatola dichiarata per il gruppo {gruppo_id}")
        raggruppamenti.append({
            "packingGroupId": gruppo_id,
            "boxes": [_scatola_a_box(s, gruppo_id, posizione, vincoli)
                       for posizione, s in enumerate(scatole, start=1)],
        })
    risposta = client.post(f"{_BASE}/{plan_id}/packingInformation",
                            json_body={"packageGroupings": raggruppamenti})
    esito = client.attendi_operazione(id_operazione(risposta, "setPackingInformation"))
    if esito["operationStatus"] != "SUCCESS":
        raise SpApiError(f"informazioni di imballaggio non accettate: {motivo_fallimento(esito)}")


def _scatola_a_box(s: Scatola, gruppo_id: str, posizione: int,
                    vincoli: dict[str, tuple]) -> dict:
    # Peso e misure li dà il venditore, Amazon non li conosce (DESIGN §5): un
    # valore nullo o negativo si ferma qui, come SKU e quantità in crea_piano,
    # invece di arrivare ad Amazon.
    for campo in ("quantita", "peso_kg", "lunghezza_cm", "larghezza_cm", "altezza_cm"):
        valore = getattr(s, campo)
        if valore <= 0:
            raise ValueError(
                f"{campo} non valido ({valore}) per la scatola {posizione} "
                f"del gruppo {gruppo_id}"
            )
    box = {
        "contentInformationSource": "BOX_CONTENT_PROVIDED" if s.righe else "MANUAL_PROCESS",
        "dimensions": {
            "length": s.lunghezza_cm, "width": s.larghezza_cm, "height": s.altezza_cm,
            "unitOfMeasurement": "CM",
        },
        "weight": {"value": s.peso_kg, "unit": "KG"},
        "quantity": s.quantita,
    }
    if s.righe:
        box["items"] = []
        for r in s.righe:
            prep_owner, label_owner = vincoli.get(r.sku, (None, None))
            if not prep_owner or not label_owner:
                raise SpApiError(
                    f"Amazon non dichiara i vincoli di preparazione per {r.sku}: "
                    f"non posso descrivere il contenuto della scatola {posizione} "
                    f"del gruppo {gruppo_id} senza inventarli"
                )
            item = {"msku": r.sku, "quantity": r.quantita,
                    "prepOwner": prep_owner, "labelOwner": label_owner}
            if r.scadenza:
                item["expiration"] = r.scadenza
            box["items"].append(item)
    return box


def leggi_articoli_gruppo(client, plan_id: str, gruppo_id: str) -> dict[str, int]:
    """Gli msku di un gruppo di imballaggio con i loro pezzi
    (`listPackingGroupItems`). Serve ad assegnare le scatole dettate da
    Mariano al gruppo giusto quando Amazon ne fa più d'uno, e a dire quanti
    pezzi vanno in ciascuno quando Amazon divide lo stesso SKU."""
    risposta = client.get(f"{_BASE}/{plan_id}/packingGroups/{gruppo_id}/items")
    articoli: dict[str, int] = {}
    for i in risposta.get("items") or []:
        if i.get("msku"):
            articoli[i["msku"]] = articoli.get(i["msku"], 0) + i.get("quantity", 0)
    return articoli
