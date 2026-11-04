from __future__ import annotations
from modello import RigaSpedizione, PianoSpedizione
from spapi_client import SpApiError, motivo_fallimento

_PATH_PIANI = "/inbound/fba/2024-03-20/inboundPlans"
_PATH_PREP = "/inbound/fba/2024-03-20/items/prepDetails"

# Amazon risponde col vincolo, non con il valore da mandare.
_OWNER_DA_VINCOLO = {"NONE_ONLY": "NONE", "SELLER_ONLY": "SELLER", "AMAZON_ONLY": "AMAZON"}


def leggi_vincoli_prep(client, mskus: list[str], marketplace_id: str) -> dict[str, tuple]:
    """Chi prepara ed etichetta ogni articolo lo decide **Amazon**, per SKU, e
    lo dichiara in `prepDetails`. Mandare un valore diverso fa fallire
    createInboundPlan con 400 (`does not require labelOwner but SELLER was
    assigned. Accepted values: [NONE]`) — misurato dal vivo il 2026-08-29.

    Torna {msku: (prep_owner, label_owner)}, con None dove Amazon non dichiara
    un vincolo."""
    risposta = client.get(_PATH_PREP, params={"marketplaceId": marketplace_id,
                                              "mskus": ",".join(mskus)})
    return {
        d.get("msku"): (_OWNER_DA_VINCOLO.get(d.get("prepOwnerConstraint")),
                        _OWNER_DA_VINCOLO.get(d.get("labelOwnerConstraint")))
        for d in (risposta.get("mskuPrepDetails") or [])
    }


def id_operazione(risposta: dict, operazione: str) -> str:
    """L'operationId con cui seguire una POST asincrona. Se Amazon risponde
    2xx ma senza, alza SpApiError e non KeyError: l'errore resta dentro il
    contratto del flusso, e dice che l'operazione può essere partita lo
    stesso. Vive qui perché la usano tutti gli stadi, dal piano alla
    prenotazione del corriere."""
    operation_id = (risposta or {}).get("operationId")
    if not operation_id:
        raise SpApiError(
            f"{operazione}: Amazon ha risposto senza operationId "
            f"(campi ricevuti: {sorted((risposta or {}).keys())}). La richiesta "
            "è stata accettata: l'operazione può essere partita lo stesso, "
            "verifica su Seller Central prima di ritentare"
        )
    return operation_id


def crea_piano(client, righe: list[RigaSpedizione], indirizzo_origine: dict,
                marketplace_id: str) -> PianoSpedizione:
    """Crea un piano di spedizione FBA (`createInboundPlan`). La POST è
    asincrona: torna inboundPlanId subito, ma il piano è utilizzabile solo a
    operazione SUCCESS.

    Non torna spedizioni: in v2024-03-20 le spedizioni nascono più avanti,
    con le opzioni di piazzamento (`piazzamento.py`), non qui — un piano
    appena creato non ha ancora né centri logistici né shipmentId."""
    if not righe:
        raise ValueError("nessuna riga di spedizione: SKU/quantità mancanti")
    for posizione, r in enumerate(righe, start=1):
        if not (r.sku or "").strip():
            raise ValueError(f"SKU mancante nella riga {posizione}")
        if r.quantita < 1:
            raise ValueError(
                f"quantità non valida per {r.sku} (riga {posizione}): {r.quantita}"
            )
    vincoli = leggi_vincoli_prep(client, [r.sku for r in righe], marketplace_id)
    items = []
    for r in righe:
        prep_owner, label_owner = vincoli.get(r.sku, (None, None))
        if not prep_owner or not label_owner:
            # Amazon non ha classificato l'articolo (prepCategory UNKNOWN).
            # Indovinare qui non costa un piano sbagliato — createInboundPlan
            # respinge con 400 senza creare niente — ma costa un giro perso e
            # un messaggio oscuro. Meglio dire subito qual è lo SKU.
            raise SpApiError(
                f"Amazon non dichiara i vincoli di preparazione per {r.sku} su "
                f"{marketplace_id}: controlla i requisiti dell'articolo in Seller "
                "Central (prepCategory risulta UNKNOWN). Non li indovino."
            )
        item = {"msku": r.sku, "quantity": r.quantita,
                "prepOwner": prep_owner, "labelOwner": label_owner}
        if r.scadenza:
            item["expiration"] = r.scadenza
        items.append(item)
    corpo = {
        "name": f"Spedizione {righe[0].sku}",
        "sourceAddress": indirizzo_origine,
        "destinationMarketplaces": [marketplace_id],
        "items": items,
    }
    risposta = client.post(_PATH_PIANI, json_body=corpo)
    plan_id = risposta["inboundPlanId"]
    # Da qui in poi il piano ESISTE su Amazon: qualunque cosa vada storta,
    # il plan_id deve arrivare a Mariano dentro l'errore. Perderlo significa
    # che un secondo giro sullo stesso lotto crea un secondo piano, e nulla
    # impedisce che entrambi arrivino a piazzamento e trasporto confermati.
    try:
        esito = client.attendi_operazione(id_operazione(risposta, "createInboundPlan"))
    except Exception as errore:
        raise SpApiError(
            f"piano {plan_id} creato su Amazon ma esito non verificato: {errore}. "
            f"Il piano esiste: annullalo su Seller Central (cancelInboundPlan) "
            f"o riprendi {plan_id}, non crearne un altro per lo stesso lotto"
        ) from errore
    if esito["operationStatus"] != "SUCCESS":
        raise SpApiError(
            f"piano {plan_id} creato su Amazon ma non utilizzabile: "
            f"{motivo_fallimento(esito)}. Il piano esiste: annullalo su Seller "
            f"Central (cancelInboundPlan), non crearne un altro per lo stesso lotto"
        )
    return PianoSpedizione(inbound_plan_id=plan_id)
