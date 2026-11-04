from __future__ import annotations
from modello import OpzionePiazzamento, RigaSpedizione, Spedizione
from piano import id_operazione
from spapi_client import SpApiError, motivo_fallimento

_BASE = "/inbound/fba/2024-03-20/inboundPlans"


def _path_piazzamento(plan_id: str) -> str:
    return f"{_BASE}/{plan_id}/placementOptions"


def genera_opzioni_piazzamento(client, plan_id: str) -> list[OpzionePiazzamento]:
    """`generatePlacementOptions` + `listPlacementOptions`: è qui che il piano
    prende forma di spedizioni vere — ogni opzione dice in quante spedizioni
    Amazon dividerebbe il lotto (`shipmentIds`) e quanto costa quella
    divisione in commissioni di piazzamento (`fees`)."""
    risposta = client.post(_path_piazzamento(plan_id), json_body={})
    esito = client.attendi_operazione(id_operazione(risposta, "generatePlacementOptions"))
    if esito["operationStatus"] != "SUCCESS":
        raise SpApiError(f"opzioni di piazzamento non generate: {motivo_fallimento(esito)}")
    elenco = client.get(_path_piazzamento(plan_id))
    return [opzione_da_amazon(o) for o in elenco.get("placementOptions", [])]


def opzione_da_amazon(o: dict) -> OpzionePiazzamento:
    """Una `placementOption` grezza come la manda Amazon. Sta qui e non in
    `giro.py` perché la stessa opzione arriva per due strade — appena generata
    o riletta dalla fotografia dello stadio — e due letture separate
    divergerebbero su quanto costa una divisione."""
    commissioni = [f for f in (o.get("fees") or []) if (f.get("value") or {}).get("amount") is not None]
    return OpzionePiazzamento(
        placement_option_id=o["placementOptionId"],
        shipment_ids=list(o.get("shipmentIds") or []),
        commissioni=sum(f["value"]["amount"] for f in commissioni) if commissioni else None,
        valuta=commissioni[0]["value"].get("code", "EUR") if commissioni else "EUR",
    )


def leggi_spedizioni(client, plan_id: str, shipment_ids: list[str]) -> list[Spedizione]:
    """Dettaglio di ogni spedizione di un'opzione di piazzamento: destinazione,
    contenuto e — quando il piazzamento è già confermato — il
    `shipmentConfirmationId` che serve alle etichette.

    Il centro logistico resta ignoto finché Amazon non lo assegna (le
    destinazioni `AMAZON_OPTIMIZED` hanno `warehouseId` vuoto): in quel caso
    si dichiara, non si inventa."""
    spedizioni = []
    for sid in shipment_ids:
        dettaglio = client.get(f"{_BASE}/{plan_id}/shipments/{sid}")
        contenuto = client.get(f"{_BASE}/{plan_id}/shipments/{sid}/items")
        spedizioni.append(Spedizione(
            shipment_id=dettaglio["shipmentId"],
            centro_logistico=(dettaglio.get("destination") or {}).get("warehouseId")
                              or "centro non ancora assegnato",
            righe=[RigaSpedizione(sku=i["msku"], quantita=i["quantity"])
                    for i in contenuto.get("items", [])],
            shipment_confirmation_id=dettaglio.get("shipmentConfirmationId"),
            amazon_reference_id=dettaglio.get("amazonReferenceId") or None,
        ))
    return spedizioni


def conferma_piazzamento(client, plan_id: str, placement_option_id: str) -> None:
    """`confirmPlacementOption`. Da qui Amazon genera i
    `shipmentConfirmationId` e la divisione non è più modificabile: si può
    solo annullare l'intero piano (`cancelInboundPlan`). Comporta la
    commissione di piazzamento quotata nell'opzione, quindi va chiamata
    dopo il "vai" di Mariano, non prima — vedi SKILL.md."""
    risposta = client.post(f"{_path_piazzamento(plan_id)}/{placement_option_id}/confirmation")
    operazione = id_operazione(risposta, "confirmPlacementOption")
    # Da qui la POST è partita: la commissione può essere già stata addebitata.
    # Se l'attesa cade, l'errore che risale parla di un problema di lettura, e
    # chi legge penserebbe che non sia successo nulla — rifacendo il giro e
    # pagando due volte una commissione irreversibile.
    try:
        esito = client.attendi_operazione(operazione)
    except Exception as errore:
        raise SpApiError(
            f"ESITO INCERTO sulla conferma del piazzamento {placement_option_id} "
            f"(piano {plan_id}, operazione {operazione}): {errore}. La richiesta è "
            f"partita: la commissione di piazzamento può essere già stata "
            f"addebitata e le spedizioni già create. Verifica su Seller Central "
            f"com'è finita prima di rifare qualunque cosa, non ritentare"
        ) from errore
    if esito["operationStatus"] != "SUCCESS":
        raise SpApiError(f"piazzamento non confermato: {motivo_fallimento(esito)}")
