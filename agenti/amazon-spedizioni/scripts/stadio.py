from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime

_BASE = "/inbound/fba/2024-03-20/inboundPlans"

IMBALLAGGIO_DA_CONFERMARE = "IMBALLAGGIO_DA_CONFERMARE"
SCATOLE_DA_DICHIARARE = "SCATOLE_DA_DICHIARARE"
PIAZZAMENTO_DA_GENERARE = "PIAZZAMENTO_DA_GENERARE"
PIAZZAMENTO_DA_SCEGLIERE = "PIAZZAMENTO_DA_SCEGLIERE"
TRASPORTO_DA_SCEGLIERE = "TRASPORTO_DA_SCEGLIERE"
TRASPORTO_PRENOTATO = "TRASPORTO_PRENOTATO"
COMPLETATO = "COMPLETATO"


@dataclass
class Fotografia:
    """Ciò che Amazon dice di un piano, letto in un colpo solo. Lo stadio si
    deduce da qui e mai dal file locale: Amazon è l'unico che sa cosa è stato
    davvero accettato, confermato o prenotato (DESIGN §12)."""
    piano: dict
    packing_options: list[dict] = field(default_factory=list)
    placement_options: list[dict] = field(default_factory=list)
    boxes: list[dict] = field(default_factory=list)
    spedizioni: dict[str, dict] = field(default_factory=dict)


def leggi(client, plan_id: str) -> Fotografia:
    piano = client.get(f"{_BASE}/{plan_id}")
    packing = client.get(f"{_BASE}/{plan_id}/packingOptions").get("packingOptions") or []
    placement = client.get(f"{_BASE}/{plan_id}/placementOptions").get("placementOptions") or []
    boxes = _tutte_le_scatole(client, plan_id)
    ids: list[str] = []
    for o in placement:
        ids += o.get("shipmentIds") or []
    ids += [s["shipmentId"] for s in (piano.get("shipments") or [])]
    spedizioni = {sid: client.get(f"{_BASE}/{plan_id}/shipments/{sid}") for sid in dict.fromkeys(ids)}
    return Fotografia(piano, packing, placement, boxes, spedizioni)


def _tutte_le_scatole(client, plan_id: str) -> list[dict]:
    """`boxes` a pagine da 100 (il massimo): la Fotografia dice di essere
    l'elenco delle scatole, quindi le porta tutte, non la prima pagina."""
    scatole, token = [], None
    while True:
        params = {"pageSize": 100}
        if token:
            params["paginationToken"] = token
        risposta = client.get(f"{_BASE}/{plan_id}/boxes", params=params)
        scatole += risposta.get("boxes") or []
        token = (risposta.get("pagination") or {}).get("nextToken")
        if not token:
            return scatole


def scaduta(expiration: str | None, adesso: datetime) -> bool:
    if not expiration:
        return False
    return datetime.fromisoformat(expiration.replace("Z", "+00:00")) <= adesso


def piazzamento_accettato(foto: Fotografia) -> dict | None:
    return next((o for o in foto.placement_options if o.get("status") == "ACCEPTED"), None)


def piazzamenti_offerti_validi(foto: Fotografia, adesso: datetime) -> list[dict]:
    return [o for o in foto.placement_options
            if o.get("status") == "OFFERED" and not scaduta(o.get("expiration"), adesso)]


def gruppi_imballaggio_accettati(foto: Fotografia) -> list[str]:
    for o in foto.packing_options:
        if o.get("status") == "ACCEPTED":
            return list(o.get("packingGroups") or [])
    return []


def riconosci(foto: Fotografia, adesso: datetime, etichette_scaricate: bool = False) -> str:
    """Un solo posto in cui si decide "a che punto è il piano". Le opzioni
    scadute contano come mai generate: rigenerarle non costa niente."""
    if not gruppi_imballaggio_accettati(foto):
        return IMBALLAGGIO_DA_CONFERMARE
    if not foto.boxes:
        return SCATOLE_DA_DICHIARARE
    accettato = piazzamento_accettato(foto)
    if accettato is None:
        return PIAZZAMENTO_DA_SCEGLIERE if piazzamenti_offerti_validi(foto, adesso) else PIAZZAMENTO_DA_GENERARE
    prenotate = [(foto.spedizioni.get(sid) or {}).get("selectedTransportationOptionId")
                 for sid in (accettato.get("shipmentIds") or [])]
    if not prenotate or not all(prenotate):
        return TRASPORTO_DA_SCEGLIERE
    return COMPLETATO if etichette_scaricate else TRASPORTO_PRENOTATO
