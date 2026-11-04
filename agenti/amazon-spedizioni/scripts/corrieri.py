from __future__ import annotations
import re
from modello import FinestraConsegna, OpzioneTrasporto
from piano import id_operazione
from spapi_client import SpApiError, motivo_fallimento

_BASE = "/inbound/fba/2024-03-20/inboundPlans"


def _path_opzioni_trasporto(plan_id: str) -> str:
    return f"{_BASE}/{plan_id}/transportationOptions"


def _path_finestre(plan_id: str, shipment_id: str) -> str:
    return f"{_BASE}/{plan_id}/shipments/{shipment_id}/deliveryWindowOptions"


def genera_opzioni_trasporto(client, plan_id: str, placement_option_id: str,
                              shipment_ids: list[str], data_pronta: str,
                              contatto: dict | None = None,
                              pallet: list[dict] | dict[str, list[dict]] | None = None
                              ) -> list[OpzioneTrasporto]:
    """`generateTransportationOptions` + `listTransportationOptions`. Sono
    operazioni di piano, non di singola spedizione: una sola chiamata quota
    tutte le spedizioni dell'opzione di piazzamento scelta.

    Amazon quota solo ciò che sa: senza `data_pronta` non genera nulla, e
    senza `pallet` non genera le opzioni LTL.

    `data_pronta` è l'ISTANTE in cui la merce è pronta al ritiro, non il
    giorno: `2026-08-30T09:00Z`. Una data secca viene respinta con 400
    ("DateTime value '2026-08-30' is not in a valid ISO 8601 format") —
    misurato dal vivo il 2026-08-29.

    L'indirizzo di partenza non si ripete qui: è quello del piano, dato a
    `crea_piano`.

    `pallet` come lista vale per tutte le spedizioni (il caso di default, un
    lotto solo); se il lotto è diviso, un dizionario `{shipment_id: [pallet]}`
    quota a ciascuna spedizione i suoi, che è l'unico modo di non far pagare
    il pallet intero a ognuna.

    Non sceglie: torna tutte le opzioni, convenzionate Amazon e proprie."""
    if not shipment_ids:
        raise ValueError("nessuna spedizione per cui chiedere il trasporto")
    if not data_pronta:
        raise ValueError("data di merce pronta mancante: Amazon non quota senza")
    # Amazon vuole un istante, non un giorno. La differenza costa un 400 dopo
    # aver già creato il piano e dichiarato le scatole: meglio fermarsi qui.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{3})?)?Z", data_pronta):
        raise ValueError(
            f"data di merce pronta in formato non valido: {data_pronta!r}. "
            "Amazon vuole un istante UTC, non un giorno — es. '2026-08-30T09:00Z'"
        )
    configurazioni = []
    for sid in shipment_ids:
        configurazione = {"shipmentId": sid, "readyToShipWindow": {"start": data_pronta}}
        if contatto:
            configurazione["contactInformation"] = contatto
        if pallet:
            suoi_pallet = pallet.get(sid) if isinstance(pallet, dict) else pallet
            if suoi_pallet:
                configurazione["pallets"] = suoi_pallet
        configurazioni.append(configurazione)
    corpo = {
        "placementOptionId": placement_option_id,
        "shipmentTransportationConfigurations": configurazioni,
    }
    risposta = client.post(_path_opzioni_trasporto(plan_id), json_body=corpo)
    esito = client.attendi_operazione(id_operazione(risposta, "generateTransportationOptions"))
    if esito["operationStatus"] != "SUCCESS":
        raise SpApiError(f"opzioni di trasporto non generate: {motivo_fallimento(esito)}")
    return leggi_opzioni_trasporto(client, plan_id, placement_option_id)


def leggi_opzioni_trasporto(client, plan_id: str, placement_option_id: str) -> list[OpzioneTrasporto]:
    """`listTransportationOptions`: i preventivi già generati per un'opzione
    di piazzamento. Non genera niente — serve a `conferma` per rileggere ciò
    che Mariano ha visto nel menù e controllare che non sia scaduto."""
    # Misurato dal vivo il 02/09/2026: senza pageSize Amazon torna 10 opzioni,
    # ne accetta al massimo 20 per pagina, e un piano da 20 pezzi ne aveva 274
    # (273 corrieri propri + 1 UPS convenzionata, in fondo). Leggere una pagina
    # sola nascondeva proprio l'opzione col prezzo.
    grezze, token = [], None
    while True:
        params = {"placementOptionId": placement_option_id, "pageSize": 20}
        if token:
            params["paginationToken"] = token
        elenco = client.get(_path_opzioni_trasporto(plan_id), params=params)
        grezze += elenco.get("transportationOptions") or []
        token = (elenco.get("pagination") or {}).get("nextToken")
        if not token:
            break
    opzioni = []
    for o in grezze:
        # Il prezzo si prende sempre che ci sia, qualunque sia il programma di
        # spedizione: `convenzionato` etichetta il tipo di opzione, non decide
        # se un prezzo esiste. Un preventivo scartato sarebbe una spesa
        # mostrata a Mariano come gratuita.
        quota = o.get("quote") or {}
        costo = quota.get("cost") or {}
        opzioni.append(OpzioneTrasporto(
            transportation_option_id=o["transportationOptionId"],
            corriere=(o.get("carrier") or {}).get("name") or "corriere proprio",
            convenzionato=o.get("shippingSolution") == "AMAZON_PARTNERED_CARRIER",
            costo=costo.get("amount"),
            valuta=costo.get("code") or "EUR",
            shipment_id=o.get("shipmentId", ""),
            modalita=o.get("shippingMode", ""),
            richiede_finestra_consegna="CONFIRMED_DELIVERY_WINDOW" in (o.get("preconditions") or []),
            scadenza=quota.get("expiration"),
        ))
    return opzioni


def genera_finestre_consegna(client, plan_id: str, shipment_id: str) -> list[FinestraConsegna]:
    """`generateDeliveryWindowOptions` + `listDeliveryWindowOptions`. Serve a
    ogni spedizione la cui opzione di trasporto ha la precondizione
    `CONFIRMED_DELIVERY_WINDOW` — in pratica tutte quelle con corriere
    proprio: senza una finestra confermata Amazon rifiuta la prenotazione."""
    risposta = client.post(_path_finestre(plan_id, shipment_id))
    esito = client.attendi_operazione(id_operazione(risposta, "generateDeliveryWindowOptions"))
    if esito["operationStatus"] != "SUCCESS":
        raise SpApiError(f"finestre di consegna non generate: {motivo_fallimento(esito)}")
    return leggi_finestre_consegna(client, plan_id, shipment_id)


def leggi_finestre_consegna(client, plan_id: str, shipment_id: str) -> list[FinestraConsegna]:
    """`listDeliveryWindowOptions`: le finestre già generate per una
    spedizione. Non genera niente, come `leggi_opzioni_trasporto` — serve a
    `giro.conferma` per controllare l'id scelto da Mariano PRIMA di pagare la
    commissione di piazzamento, senza fare nessuna POST."""
    elenco = client.get(_path_finestre(plan_id, shipment_id))
    return [
        FinestraConsegna(
            delivery_window_option_id=f["deliveryWindowOptionId"],
            inizio=f["startDate"],
            fine=f["endDate"],
            disponibilita=f.get("availabilityType", ""),
        )
        for f in elenco.get("deliveryWindowOptions", [])
    ]
