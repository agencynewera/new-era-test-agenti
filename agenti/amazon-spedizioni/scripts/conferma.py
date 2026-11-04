from __future__ import annotations
from modello import OpzioneTrasporto
from piano import id_operazione
from spapi_client import SpApiError, motivo_fallimento

_BASE = "/inbound/fba/2024-03-20/inboundPlans"


def conferma_finestra_consegna(client, plan_id: str, shipment_id: str,
                                delivery_window_option_id: str) -> None:
    """`confirmDeliveryWindowOptions`. Va chiamata, per ogni spedizione che lo
    richiede, PRIMA di `conferma_trasporto`: è la condizione che Amazon
    impone al corriere proprio (`CONFIRMED_DELIVERY_WINDOW`), e senza di essa
    la prenotazione del corriere proprio non passa."""
    risposta = client.post(
        f"{_BASE}/{plan_id}/shipments/{shipment_id}"
        f"/deliveryWindowOptions/{delivery_window_option_id}/confirmation"
    )
    esito = client.attendi_operazione(id_operazione(risposta, "confirmDeliveryWindowOptions"))
    if esito["operationStatus"] != "SUCCESS":
        raise SpApiError(f"finestra di consegna non confermata: {motivo_fallimento(esito)}")


def conferma_trasporto(client, plan_id: str, scelte: list[OpzioneTrasporto],
                        contatto: dict | None = None) -> None:
    """`confirmTransportationOptions`: prenota i corrieri scelti — convenzionato
    Amazon (spesa reale) o proprio (self-ship). È un'operazione di piano: si
    confermano insieme tutte le spedizioni, una scelta per spedizione.

    È l'unica chiamata di questo flusso che impegna denaro verso un corriere,
    ed è irreversibile fuori dalla finestra di annullamento. Va chiamata SOLO
    dopo che Mariano ha visto il riepilogo di `anteprima.riepilogo_conferma`
    per ogni spedizione e ha detto "vai": il cancello di conferma vive
    nell'orchestrazione (SKILL.md), non qui.

    Richiede che il piazzamento sia già confermato, e che le spedizioni con
    precondizione `CONFIRMED_DELIVERY_WINDOW` abbiano già la loro finestra."""
    if not scelte:
        raise ValueError("nessuna opzione di trasporto scelta da confermare")
    selezioni = []
    for o in scelte:
        if not o.shipment_id:
            raise ValueError(
                f"opzione {o.transportation_option_id} senza shipment_id: "
                "non si sa a quale spedizione applicarla"
            )
        selezione = {"shipmentId": o.shipment_id,
                     "transportationOptionId": o.transportation_option_id}
        if contatto:
            selezione["contactInformation"] = contatto
        selezioni.append(selezione)
    risposta = client.post(f"{_BASE}/{plan_id}/transportationOptions/confirmation",
                            json_body={"transportationSelections": selezioni})
    operazione = id_operazione(risposta, "confirmTransportationOptions")
    # Da qui la POST è partita: il corriere può essere già prenotato e
    # addebitato. Se l'attesa cade, un messaggio che parla di errore di lettura
    # farebbe credere che non sia successo nulla, e la prenotazione verrebbe
    # rifatta su spedizioni che Amazon ha già confermato.
    try:
        esito = client.attendi_operazione(operazione)
    except Exception as errore:
        prenotazioni = ", ".join(
            f"{s['shipmentId']}→{s['transportationOptionId']}" for s in selezioni
        )
        raise SpApiError(
            f"ESITO INCERTO sulla conferma del trasporto del piano {plan_id} "
            f"({prenotazioni}, operazione {operazione}): {errore}. La richiesta è "
            f"partita: i corrieri possono essere già prenotati e addebitati. "
            f"Verifica su Seller Central com'è finita prima di rifare qualunque "
            f"cosa, non ritentare"
        ) from errore
    if esito["operationStatus"] != "SUCCESS":
        raise SpApiError(f"trasporto non confermato: {motivo_fallimento(esito)}")
