from __future__ import annotations
import os
import requests
from registro import _nome_cliente
from spapi_client import SpApiError, _USER_AGENT


_BASE = "/inbound/fba/2024-03-20/inboundPlans"


def leggi_cartoni_completi(client, plan_id: str, shipment_id: str) -> list[dict]:
    """I cartoni della spedizione interi (`listShipmentBoxes`): id, contenuto
    (`items`: msku e quantità) e misure. Amazon li assegna dopo
    `setPackingInformation`; forma verificata dal vivo il 2026-08-29 su una
    spedizione reale del cliente.

    Le etichette guardano solo gli id (`leggi_cartoni`), ma la dichiarazione
    dei tracking col corriere proprio ha bisogno del contenuto: il cliente manda
    le lettere di vettura raggruppate per prodotto, e lo SKU è l'unico ponte fra
    il suo elenco e i `boxId` di Amazon."""
    cartoni, token = [], None
    while True:
        params = {"pageSize": 100}
        if token:
            params["paginationToken"] = token
        risposta = client.get(f"{_BASE}/{plan_id}/shipments/{shipment_id}/boxes", params=params)
        cartoni += risposta.get("boxes") or []
        token = (risposta.get("pagination") or {}).get("nextToken")
        if not token:
            return cartoni


def leggi_cartoni(client, plan_id: str, shipment_id: str) -> list[str]:
    """Gli id dei cartoni (`FBA15M3Z3XYSU000001`), che le etichette per cartone
    vogliono uno per uno."""
    return [b["boxId"] for b in leggi_cartoni_completi(client, plan_id, shipment_id)
            if b.get("boxId")]


def formato_cartoni(convenzionato: bool) -> str:
    """Il `PageType` delle etichette per cartone dipende da chi trasporta.

    Col corriere convenzionato Amazon l'etichetta del corriere (con il tracking)
    la emette Amazon insieme a quella FBA, ma solo nei formati `*_Carrier*`:
    misurato dal vivo il 2026-09-03 su una spedizione UPS convenzionata,
    `PackageLabel_A4_4` torna un PDF di 4 KB con la sola etichetta FBA, mentre
    `PackageLabel_Plain_Paper_CarrierBottom` torna il foglio combinato (una
    pagina per cartone, FBA sopra e UPS sotto). Col corriere proprio non c'è
    etichetta del corriere e restano le 4 per foglio A4 che il cliente stampa."""
    return "PackageLabel_Plain_Paper_CarrierBottom" if convenzionato else "PackageLabel_A4_4"


def scarica_etichette(client, shipment_confirmation_id: str, cliente: str,
                       tipo_pagina: str = "PackageLabel_A4_4",
                       tipo_etichetta: str = "UNIQUE",
                       cartoni: list[str] | None = None,
                       numero_pallet: int | None = None,
                       cartella: str | None = None) -> str:
    """Scarica le etichette scatola in PDF e le salva in `cartella` (quella
    PRIME del cliente, da archivio.cartella_spedizione) o, senza, sul Desktop.

    Le etichette NON sono in v2024-03-20: `getLabels` è una delle operazioni
    v0 che Amazon ha esplicitamente conservato, e vuole il
    `shipmentConfirmationId` (stile FBA1234ABCD, generato da
    `confirmPlacementOption`) al posto dello `shipmentId` di v2024-03-20.

    L'URL di download vale pochi secondi: si scarica subito, non si conserva."""
    if not shipment_confirmation_id:
        raise SpApiError(
            "shipment_confirmation_id mancante: le etichette esistono solo "
            "dopo la conferma del piazzamento"
        )
    params = {"PageType": tipo_pagina, "LabelType": tipo_etichetta}
    if tipo_etichetta == "UNIQUE":
        # Etichette per cartone: Amazon ne vuole una per ogni cartone e
        # rifiuta con 400 ("Value null at 'cartonIdList'") se non gliene
        # passi l'elenco. Misurato dal vivo il 2026-08-29.
        if not cartoni:
            raise SpApiError(
                "etichette per cartone richieste senza l'elenco dei cartoni: "
                f"leggilo con leggi_cartoni(client, plan_id, shipment_id) per "
                f"la spedizione {shipment_confirmation_id}"
            )
        params["PackageLabelsToPrint"] = ",".join(cartoni)
    elif tipo_etichetta == "PALLET":
        if not numero_pallet:
            raise SpApiError(
                "etichette pallet richieste senza il numero di pallet: quante "
                "ne servono lo sa solo chi ha bancalizzato la merce"
            )
        params["NumberOfPallets"] = numero_pallet
    risposta = client.get(
        f"/fba/inbound/v0/shipments/{shipment_confirmation_id}/labels",
        params=params,
    )
    url = (risposta.get("payload") or {}).get("DownloadURL")
    if not url:
        raise SpApiError(f"nessun URL etichetta per la spedizione {shipment_confirmation_id}")
    # Questo download non passa da spapi_client: senza try/except un timeout
    # uscirebbe come requests.exceptions grezza, fuori dal contratto SpApiError.
    try:
        scaricato = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=120)
    except requests.exceptions.RequestException as e:
        raise SpApiError(
            f"download etichette {shipment_confirmation_id} non riuscito: {e}. "
            "La spedizione è confermata: mancano solo le etichette, si "
            "riscaricano senza rifare nulla del giro"
        ) from e
    if scaricato.status_code >= 400:
        raise SpApiError(f"download etichette {shipment_confirmation_id} -> {scaricato.status_code}")
    nome = f"Etichette-{_nome_cliente(cliente)}-{shipment_confirmation_id}.pdf"
    percorso = os.path.join(cartella, nome) if cartella else os.path.expanduser(f"~/Desktop/{nome}")
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "wb") as f:
        f.write(scaricato.content)
    return percorso
