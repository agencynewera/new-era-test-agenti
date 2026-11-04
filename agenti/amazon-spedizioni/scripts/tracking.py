from __future__ import annotations
from collections import Counter
import yaml
from piano import id_operazione
from spapi_client import SpApiError, motivo_fallimento

_BASE = "/inbound/fba/2024-03-20/inboundPlans"


class _SoloTesto(yaml.SafeLoader):
    """Loader che tiene i numeri com'erano scritti. Serve perché PyYAML legge
    `0123` come l'ottale 83 e `0922943001` come stringa (le cifre 9 non sono
    ottali): due lettere di vettura dello stesso corriere finirebbero una
    intera e una storpiata, e la seconda non esisterebbe da nessuna parte."""


_SoloTesto.add_constructor("tag:yaml.org,2002:int", lambda caricatore, nodo: nodo.value)


def carica_file(percorso: str) -> dict[str, list[str]]:
    """Le lettere di vettura da un file, `SKU: [codice, codice, ...]` — la forma
    comoda quando i codici sono tanti e arrivano incollati da una mail."""
    with open(percorso) as f:
        try:
            dati = yaml.load(f, Loader=_SoloTesto)
        except yaml.YAMLError as e:
            # Un YAMLError grezzo non è ValueError: sfuggirebbe al contratto
            # d'errore di giro.main e uscirebbe come traceback.
            raise ValueError(f"{percorso} non è un YAML valido: {e}") from e
    if not dati:
        raise ValueError(f"nessuna lettera di vettura in {percorso}")
    if not isinstance(dati, dict):
        raise ValueError(
            f"{percorso}: atteso un elenco di codici per SKU (`SKU:` e sotto i "
            f"codici), trovato {type(dati).__name__}"
        )
    for sku, codici in dati.items():
        if not isinstance(codici, list):
            raise ValueError(
                f"{percorso}: i codici di {sku} devono stare in un elenco, uno per "
                f"riga preceduto da '-', anche quando è uno solo"
            )
    return {str(sku): [str(c) for c in codici] for sku, codici in dati.items()}


def abbina(cartoni: list[dict], codici: dict[str, list[str]]) -> dict[str, str]:
    """Da «i codici del prodotto X» (come li manda il cliente) a «il codice del
    cartone Y» (come li vuole Amazon). Torna {boxId: trackingId}.

    Il ponte è lo SKU, perché il cliente non conosce i `boxId`. Regge solo con
    cartoni mono-SKU: dentro un gruppo di cartoni identici l'abbinamento è
    arbitrario e senza conseguenze, ma con un cartone misto non c'è modo di
    sapere quale codice gli tocca, e indovinare manderebbe il trasportatore a
    citare il numero sbagliato.

    Ogni controllo qui dentro esiste per fermare il giro PRIMA della scrittura:
    Amazon accetta senza discutere anche un abbinamento sbagliato."""
    if not cartoni:
        raise ValueError("nessun cartone nella spedizione: niente da tracciare")
    sku_del_cartone: dict[str, str] = {}
    for c in cartoni:
        box = c.get("boxId")
        items = c.get("items") or []
        if not items:
            raise ValueError(
                f"Amazon non dichiara il contenuto del cartone {box}: senza SKU "
                "non so quale lettera di vettura gli tocca"
            )
        if len(items) > 1:
            raise ValueError(
                f"il cartone {box} è misto ({', '.join(i['msku'] for i in items)}): "
                "i codici arrivano raggruppati per prodotto e non bastano a dire "
                "quale va su questo cartone — dichiarali a mano su Seller Central"
            )
        sku_del_cartone[box] = items[0]["msku"]

    nei_cartoni = set(sku_del_cartone.values())
    ignoti = sorted(set(codici) - nei_cartoni)
    if ignoti:
        raise ValueError(
            f"{', '.join(ignoti)} non sta in nessun cartone di questa spedizione "
            f"(ci sono {', '.join(sorted(nei_cartoni))}): probabilmente i codici "
            "sono di un'altra spedizione"
        )
    mancanti = sorted(nei_cartoni - set(codici))
    if mancanti:
        raise ValueError(
            f"nessuna lettera di vettura per {', '.join(mancanti)}, che occupa "
            f"dei cartoni di questa spedizione: la mail del cliente è incompleta"
        )

    assegnato: dict[str, str] = {}
    for sku in sorted(nei_cartoni):
        suoi = [b for b, s in sku_del_cartone.items() if s == sku]
        if len(codici[sku]) != len(suoi):
            raise ValueError(
                f"{sku}: {len(codici[sku])} lettere di vettura per {len(suoi)} "
                "cartoni. La mail del cliente è incompleta o di un altro lotto — "
                "fermo qui, non tiro a indovinare quale codice va su quale cartone"
            )
        for box, codice in zip(suoi, codici[sku]):
            assegnato[box] = str(codice).strip()
    # Nell'ordine dei cartoni di Amazon, non degli SKU: è l'ordine in cui
    # l'anteprima si legge accanto alla pila dei colli, ed è quello delle
    # etichette già stampate.
    mappa = {box: assegnato[box] for box in sku_del_cartone}

    vuoti = [b for b, t in mappa.items() if not t]
    if vuoti:
        raise ValueError(f"lettera di vettura vuota per i cartoni {', '.join(sorted(vuoti))}")
    conta = Counter(mappa.values())
    ripetuti = sorted(t for t, n in conta.items() if n > 1)
    if ripetuti:
        raise ValueError(
            f"la stessa lettera di vettura su più cartoni: {', '.join(ripetuti)}. "
            "Quasi sempre è un copia-incolla sbagliato, e Amazon la accetterebbe "
            "senza dire niente"
        )
    return mappa


def leggi_spedizione(client, plan_id: str, shipment_id: str) -> dict:
    """`getShipment` grezza. È da qui che si leggono le lettere di vettura, non
    dall'endpoint `/trackingDetails`, che in GET risponde 403 (misurato il
    07/09/2026): quel path accetta solo la PUT.

    Torna il corpo intero perché una lettura sola deve bastare: il codice FBA e
    lo stato servono all'anteprima quanto i tracking."""
    return client.get(f"{_BASE}/{plan_id}/shipments/{shipment_id}")


def tracking_dichiarati(spedizione: dict) -> dict[str, str]:
    """Le lettere di vettura già note ad Amazon, {boxId: trackingId}. Prima che
    qualcuno dichiari i codici, Amazon elenca i cartoni col solo `boxId` —
    quelli non contano come tracking presenti."""
    dettagli = spedizione.get("trackingDetails") or {}
    spd = dettagli.get("spdTrackingDetail")
    if spd is None:
        if dettagli.get("ltlTrackingDetail"):
            # Il trasporto a pallet ha un'altra forma (freightBillNumber, bol) e
            # non l'abbiamo mai vista dal vivo: scriverci dentro codici SPD
            # sarebbe inventare un comportamento.
            raise SpApiError(
                f"la spedizione {spedizione.get('shipmentConfirmationId') or ''} viaggia "
                "a pallet (ltlTrackingDetail): questo passo dichiara solo i pacchi "
                "(SPD). Dichiarala a mano su Seller Central e segnalalo, così la "
                "aggiungiamo su un caso vero"
            )
        return {}
    return {i["boxId"]: i["trackingId"]
            for i in (spd.get("spdTrackingItems") or [])
            if i.get("boxId") and i.get("trackingId")}


def leggi_tracking(client, plan_id: str, shipment_id: str) -> dict[str, str]:
    """Le lettere di vettura già note ad Amazon. Scorciatoia per chi la
    spedizione non ce l'ha già in mano — la rilettura di verifica dopo la PUT."""
    return tracking_dichiarati(leggi_spedizione(client, plan_id, shipment_id))


def da_dichiarare(mappa: dict[str, str], gia_tracciati: dict[str, str]) -> dict[str, str]:
    """Cosa mandare davvero ad Amazon quando una parte dei cartoni ha GIÀ una
    lettera di vettura.

    `mappa`: {boxId: trackingId} che abbiamo appena abbinato, tutti i cartoni.
    `gia_tracciati`: {boxId: trackingId} che Amazon ha già, da `leggi_tracking`.
    Torna il sottoinsieme di `mappa` da dichiarare; `raise ValueError(...)` ferma
    il giro prima di qualunque scrittura, e il messaggio arriva tale e quale a
    Mariano.

    Perché la domanda esiste: dei cartoni già tracciati vogliono dire quasi
    sempre una di due cose — o la spedizione è quella sbagliata, o la
    dichiarazione è già stata fatta e questa è una ripetizione. Ma capita anche
    che una PUT sia andata a metà e ne manchi qualcuno.

    Si ferma sempre, anche per un solo cartone (deciso da Mariano il
    14/09/2026). L'alternativa — dichiarare solo i mancanti — completerebbe da
    sola una PUT andata a metà, ma sulla spedizione sbagliata scriverebbe
    lettere di vettura errate sui cartoni ancora liberi e la manderebbe in
    SHIPPED; e non sappiamo se un tracking sbagliato si corregge, non l'abbiamo
    mai provato. Fermarsi costa solo il fastidio di togliere dalla riga di
    comando i codici già dichiarati."""
    if gia_tracciati:
        elenco = ", ".join(f"{box} ({codice})" for box, codice in sorted(gia_tracciati.items()))
        raise ValueError(
            f"{len(gia_tracciati)} cartoni su {len(mappa)} hanno già una lettera di "
            f"vettura su Amazon: {elenco}. Nessuna scrittura — di solito vuol dire "
            "che la mail è di un'altra spedizione. Se invece una dichiarazione era "
            "andata a metà, rilancia togliendo i codici già fatti"
        )
    return mappa


def dichiara(client, plan_id: str, shipment_id: str, mappa: dict[str, str]) -> None:
    """`updateShipmentTrackingDetails`: PUT, asincrona come ogni scrittura della
    Fulfillment Inbound API.

    Non è una scrittura tecnica: dichiarare le lettere di vettura fa passare la
    spedizione da READY_TO_SHIP a SHIPPED. È il modo in cui, col corriere
    proprio, si dice ad Amazon che la merce è partita — quindi va chiamata
    quando i colli sono davvero usciti, non appena arrivano i codici. Il
    cancello 🟡 vive nell'orchestrazione (giro.py), non qui."""
    if not mappa:
        raise ValueError("nessuna lettera di vettura da dichiarare")
    corpo = {"trackingDetails": {"spdTrackingDetail": {"spdTrackingItems": [
        {"boxId": box, "trackingId": codice} for box, codice in mappa.items()
    ]}}}
    risposta = client.put(f"{_BASE}/{plan_id}/shipments/{shipment_id}/trackingDetails",
                           json_body=corpo)
    operazione = id_operazione(risposta, "updateShipmentTrackingDetails")
    # Da qui la PUT è partita. Se l'attesa cade, un messaggio che parla di
    # errore di lettura farebbe ridichiarare i codici su una spedizione che
    # Amazon ha già accettato e messo in SHIPPED.
    try:
        esito = client.attendi_operazione(operazione)
    except Exception as errore:
        raise SpApiError(
            f"ESITO INCERTO sulla dichiarazione delle lettere di vettura di "
            f"{shipment_id} (operazione {operazione}): {errore}. La richiesta è "
            f"partita: i codici possono essere già stati accettati e la spedizione "
            f"già passata a SHIPPED. Rileggi la spedizione su Seller Central prima "
            f"di rifare qualunque cosa"
        ) from errore
    if esito["operationStatus"] != "SUCCESS":
        raise SpApiError(f"lettere di vettura non accettate: {motivo_fallimento(esito)}")
