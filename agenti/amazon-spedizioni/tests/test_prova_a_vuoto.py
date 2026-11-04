# tests/test_prova_a_vuoto.py
"""Prova a vuoto: l'intera catena su fixture, senza rete.

Il client finto è deliberatamente severo su due cose che un Mock piatto
lascerebbe passare:
  - `attendi_operazione` risponde solo all'operationId che quella POST ha
    davvero restituito: uno stadio che interrogasse l'operazione di un altro
    stadio fallirebbe qui;
  - `get` risponde per percorso: un endpoint sbagliato non trova fixture;
  - `get` conta le letture, così il test può verificare non solo COSA finisce
    nel registro ma QUANDO: fra una spesa reale e la sua traccia non deve
    esserci passata nessuna chiamata di rete.

Il registro è parte della catena, non un accessorio: si chiama negli stessi
punti in cui lo chiama SKILL.md e con gli oggetti veri prodotti dagli stadi
(`RigaSpedizione`, `Spedizione`, `OpzioneTrasporto`), non con dizionari
riscritti a mano. Un flusso che passa dataclass a un registro che sa scrivere
solo dizionari crasherebbe qui, dove prima passava perché la prova a vuoto il
registro non lo toccava affatto.
"""
from unittest.mock import Mock, patch
import pytest
import registro
from modello import RigaSpedizione, Scatola
from piano import crea_piano
from imballaggio import (genera_opzioni_imballaggio, conferma_imballaggio,
                          imposta_informazioni_imballaggio)
from piazzamento import (genera_opzioni_piazzamento, leggi_spedizioni,
                          conferma_piazzamento)
from corrieri import genera_opzioni_trasporto
from conferma import conferma_trasporto
from etichette import scarica_etichette, leggi_cartoni
from anteprima import riepilogo, riepilogo_conferma

_BASE = "/inbound/fba/2024-03-20/inboundPlans/wf1"
CLIENTE = "Phy to Pet"


def _voce_su_disco(riferimento):
    """La voce del registro riletta dal file — non dalla memoria del processo:
    il riferimento porta con sé cliente e giorno del file in cui è nata."""
    data, posizione, _ = riferimento.split("#")
    return registro.ultimo_lotto(CLIENTE, data)[int(posizione)]


class ClienteFinto:
    """Risponde per percorso e tiene traccia delle operazioni interrogate."""

    def __init__(self):
        self.operazioni_aperte = {}
        self.operazioni_interrogate = []
        self.piazzamento_confermato = False
        self.letture = 0
        self._contatore = 0

    def post(self, path, json_body=None, params=None):
        self._contatore += 1
        op_id = f"op-{self._contatore}"
        self.operazioni_aperte[op_id] = path
        if path == f"{_BASE}/placementOptions/pl1/confirmation":
            self.piazzamento_confermato = True
        if path.endswith("/inboundPlans"):
            return {"inboundPlanId": "wf1", "operationId": op_id}
        return {"operationId": op_id}

    def attendi_operazione(self, operation_id, attesa_max_s=300):
        if operation_id not in self.operazioni_aperte:
            raise AssertionError(
                f"interrogata un'operazione mai aperta da una POST: {operation_id}"
            )
        self.operazioni_interrogate.append(operation_id)
        return {"operationId": operation_id, "operationStatus": "SUCCESS"}

    def get(self, path, params=None):
        self.letture += 1
        if path == f"{_BASE}/packingOptions":
            return {"packingOptions": [{"packingOptionId": "po1", "packingGroups": ["pg1"]}]}
        if path == f"{_BASE}/placementOptions":
            return {"placementOptions": [{
                "placementOptionId": "pl1", "shipmentIds": ["sh1"], "status": "OFFERED",
                "fees": [{"type": "FEE", "target": "Placement Services",
                           "description": "commissione di piazzamento",
                           "value": {"amount": 12.0, "code": "EUR"}}],
                "discounts": [],
            }]}
        if path == f"{_BASE}/shipments/sh1":
            spedizione = {"shipmentId": "sh1",
                           "destination": {"destinationType": "AMAZON_WAREHOUSE", "warehouseId": "MXP5"}}
            # shipmentConfirmationId nasce con la conferma del piazzamento
            if self.piazzamento_confermato:
                spedizione["shipmentConfirmationId"] = "FBA1234ABCD"
            return spedizione
        if path == f"{_BASE}/shipments/sh1/items":
            return {"items": [{"msku": "SKU-1", "quantity": 30}]}
        if path == f"{_BASE}/transportationOptions":
            assert params == {"placementOptionId": "pl1", "pageSize": 20}
            return {"transportationOptions": [
                {"transportationOptionId": "to1", "shipmentId": "sh1",
                 "shippingSolution": "AMAZON_PARTNERED_CARRIER",
                 "shippingMode": "GROUND_SMALL_PARCEL", "preconditions": [],
                 "carrier": {"name": "UPS", "alphaCode": "UPSN"},
                 "quote": {"cost": {"amount": 42.5, "code": "EUR"}}},
            ]}
        # non è sotto il piano: i vincoli sono dell'articolo, non del lotto
        if path == "/inbound/fba/2024-03-20/items/prepDetails":
            # Chi prepara ed etichetta lo dichiara Amazon per SKU: forma vera,
            # copiata da una risposta reale del 2026-08-29.
            return {"mskuPrepDetails": [
                {"msku": m, "prepOwnerConstraint": "NONE_ONLY",
                 "labelOwnerConstraint": "NONE_ONLY", "allOwnersConstraint": "MUST_MATCH",
                 "prepCategory": "NONE", "prepTypes": []}
                for m in params["mskus"].split(",")
            ]}
        if path == f"{_BASE}/shipments/sh1/boxes":
            # forma vera di un id cartone, letta dal vivo il 2026-08-29
            return {"boxes": [{"boxId": "FBA1234ABCDU000001"}]}
        if path == "/fba/inbound/v0/shipments/FBA1234ABCD/labels":
            return {"payload": {"DownloadURL": "https://amazon-labels.example/x.pdf"}}
        raise AssertionError(f"GET su un percorso senza fixture: {path}")


def test_flusso_end_to_end_su_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = ClienteFinto()
    righe = [RigaSpedizione(sku="SKU-1", quantita=30)]
    indirizzo = {"name": "Phy to Pet", "addressLine1": "Via Roma 1", "city": "Prato",
                 "postalCode": "59100", "countryCode": "IT"}

    # --- prima del cancello: si può solo preparare e chiedere prezzi ---
    # passo 2: il lotto si registra PRIMA di chiamare Amazon, quando ancora non
    # si sa come verrà diviso.
    rif = registro.registra_tentativo(CLIENTE, righe)

    piano = crea_piano(client, righe, indirizzo, "APJ6JRA9NG5V4")
    assert piano.spedizioni == []

    opzioni_imballaggio = genera_opzioni_imballaggio(client, piano.inbound_plan_id)
    conferma_imballaggio(client, piano.inbound_plan_id, opzioni_imballaggio[0].packing_option_id)
    piano.packing_option_id = opzioni_imballaggio[0].packing_option_id

    imposta_informazioni_imballaggio(client, piano.inbound_plan_id, {
        opzioni_imballaggio[0].packing_group_ids[0]: [
            Scatola(quantita=1, peso_kg=8.0, lunghezza_cm=40, larghezza_cm=30, altezza_cm=25,
                     righe=righe),
        ],
    }, "APJ6JRA9NG5V4")

    opzioni_piazzamento = genera_opzioni_piazzamento(client, piano.inbound_plan_id)
    scelta_piazzamento = opzioni_piazzamento[0]
    assert scelta_piazzamento.commissioni == 12.0
    piano.spedizioni = leggi_spedizioni(client, piano.inbound_plan_id,
                                         scelta_piazzamento.shipment_ids)
    spedizione = piano.spedizioni[0]
    assert spedizione.shipment_confirmation_id is None

    # passo 7: le spedizioni ora esistono — nel registro ci vanno le
    # `Spedizione` vere, con dentro le loro `RigaSpedizione`.
    registro.registra_piano(CLIENTE, rif, piano.inbound_plan_id, piano.spedizioni)

    spedizione.opzioni_trasporto = genera_opzioni_trasporto(
        client, piano.inbound_plan_id, scelta_piazzamento.placement_option_id,
        [spedizione.shipment_id], data_pronta="2026-09-05T08:00Z",
    )
    scelta = spedizione.opzioni_trasporto[0]

    testo_anteprima = riepilogo(piano)
    assert "42.50 EUR" in testo_anteprima
    assert "MXP5" in testo_anteprima

    testo_cancello = riepilogo_conferma(spedizione, scelta)
    assert "42.50 EUR" in testo_cancello
    assert '"vai"' in testo_cancello

    # --- il "vai" di Mariano: qui, nella skill vera, l'agente si ferma.
    # Da questa riga in poi si spende: piazzamento e prenotazione corriere.
    piano.placement_option_id = scelta_piazzamento.placement_option_id
    conferma_piazzamento(client, piano.inbound_plan_id, piano.placement_option_id)
    letture_alla_conferma = client.letture

    # passo 13: la commissione di piazzamento è già spesa, e il trasporto può
    # ancora fallire: la traccia si scrive qui, non alla fine del giro.
    registro.registra_piazzamento_confermato(
        CLIENTE, rif, piano.placement_option_id,
        scelta_piazzamento.commissioni, scelta_piazzamento.valuta)

    # La proprietà che l'ordine dei passi 12-13 garantisce: quando si annota
    # il piazzamento confermato quel dato è GIÀ su disco (lo si rilegge dal
    # file, non dalla memoria del processo) e ci è arrivato senza che nel
    # frattempo sia partita una sola chiamata di rete. Con `leggi_spedizioni`
    # in mezzo, un suo errore lascerebbe la commissione già addebitata senza
    # nessuna riga nel registro.
    speso = _voce_su_disco(rif)["piazzamento_confermato"]
    assert speso["placement_option_id"] == "pl1"
    assert speso["commissioni"] == 12.0
    assert client.letture == letture_alla_conferma

    piano.spedizioni = leggi_spedizioni(client, piano.inbound_plan_id,
                                         scelta_piazzamento.shipment_ids)
    spedizione = piano.spedizioni[0]
    assert spedizione.shipment_confirmation_id == "FBA1234ABCD"

    conferma_trasporto(client, piano.inbound_plan_id, [scelta])
    spedizione.trasporto_confermato = scelta
    registro.registra_trasporto_confermato(CLIENTE, rif, {spedizione.shipment_id: scelta})

    with patch("etichette.requests.get") as mock_download:
        risposta_pdf = Mock()
        risposta_pdf.status_code = 200
        risposta_pdf.content = b"%PDF-1.4 finto"
        mock_download.return_value = risposta_pdf
        cartoni = leggi_cartoni(client, piano.inbound_plan_id, spedizione.shipment_id)
        spedizione.etichette_path = scarica_etichette(
            client, spedizione.shipment_confirmation_id, CLIENTE, cartoni=cartoni)

    assert spedizione.etichette_path.endswith("Etichette-Phy to Pet-FBA1234ABCD.pdf")

    registro.registra_etichette(CLIENTE, rif, spedizione.shipment_id,
                                 spedizione.shipment_confirmation_id,
                                 spedizione.etichette_path)

    registro.registra_esito(CLIENTE, rif, "completata")

    # --- ciò che resta su disco: solo JSON, riletto da un processo che le
    # dataclass non le ha mai viste ---
    voce = _voce_su_disco(rif)
    assert voce["righe"] == [{"sku": "SKU-1", "quantita": 30, "scadenza": None}]
    assert voce["inbound_plan_id"] == "wf1"
    assert voce["spedizioni"][0]["shipment_id"] == "sh1"
    assert voce["spedizioni"][0]["centro_logistico"] == "MXP5"
    assert voce["spedizioni"][0]["righe"] == [{"sku": "SKU-1", "quantita": 30, "scadenza": None}]
    assert voce["piazzamento_confermato"]["placement_option_id"] == "pl1"
    assert voce["piazzamento_confermato"]["commissioni"] == 12.0
    # Il codice FBA e il PDF sono i due riferimenti che servono dopo il giro
    # (ristampe, assistenza): senza questi due, vivrebbero solo nella chat.
    assert voce["spedizioni"][0]["shipment_confirmation_id"] == "FBA1234ABCD"
    assert voce["spedizioni"][0]["etichette_path"].endswith(
        "Etichette-Phy to Pet-FBA1234ABCD.pdf")
    assert voce["piazzamento_confermato"]["valuta"] == "EUR"
    trasporto = voce["spedizioni"][0]["trasporto_confermato"]
    assert trasporto["transportation_option_id"] == "to1"
    assert trasporto["costo"] == 42.5
    assert voce["esito"] == "completata"


def test_ogni_stadio_interroga_la_propria_operazione(tmp_path, monkeypatch):
    """Ogni POST asincrona apre un operationId e deve interrogare QUELLO:
    un id trascinato da uno stadio precedente passerebbe inosservato con un
    `attendi_operazione` a risposta fissa."""
    monkeypatch.setenv("HOME", str(tmp_path))
    client = ClienteFinto()
    righe = [RigaSpedizione(sku="SKU-1", quantita=30)]
    indirizzo = {"name": "Phy to Pet", "addressLine1": "Via Roma 1", "city": "Prato",
                 "postalCode": "59100", "countryCode": "IT"}

    piano = crea_piano(client, righe, indirizzo, "APJ6JRA9NG5V4")
    opzioni_imballaggio = genera_opzioni_imballaggio(client, piano.inbound_plan_id)
    conferma_imballaggio(client, piano.inbound_plan_id, opzioni_imballaggio[0].packing_option_id)
    imposta_informazioni_imballaggio(client, piano.inbound_plan_id, {
        "pg1": [Scatola(quantita=1, peso_kg=8.0, lunghezza_cm=40, larghezza_cm=30, altezza_cm=25)],
    }, "APJ6JRA9NG5V4")
    opzioni_piazzamento = genera_opzioni_piazzamento(client, piano.inbound_plan_id)
    spedizioni = leggi_spedizioni(client, piano.inbound_plan_id, opzioni_piazzamento[0].shipment_ids)
    opzioni_trasporto = genera_opzioni_trasporto(
        client, piano.inbound_plan_id, opzioni_piazzamento[0].placement_option_id,
        [spedizioni[0].shipment_id], data_pronta="2026-09-05T08:00Z")
    conferma_piazzamento(client, piano.inbound_plan_id, opzioni_piazzamento[0].placement_option_id)
    conferma_trasporto(client, piano.inbound_plan_id, [opzioni_trasporto[0]])

    # Otto POST asincrone, otto operazioni distinte, interrogate in ordine.
    assert client.operazioni_interrogate == [f"op-{n}" for n in range(1, 9)]
    assert len(set(client.operazioni_interrogate)) == 8
    percorsi = [client.operazioni_aperte[op] for op in client.operazioni_interrogate]
    assert percorsi == [
        "/inbound/fba/2024-03-20/inboundPlans",
        f"{_BASE}/packingOptions",
        f"{_BASE}/packingOptions/po1/confirmation",
        f"{_BASE}/packingInformation",
        f"{_BASE}/placementOptions",
        f"{_BASE}/transportationOptions",
        f"{_BASE}/placementOptions/pl1/confirmation",
        f"{_BASE}/transportationOptions/confirmation",
    ]


def test_stadio_che_interroga_un_operazione_mai_aperta_viene_scoperto():
    """Guardia sul client finto: se un modulo interrogasse un id inventato
    (o trascinato), la prova a vuoto lo direbbe invece di passare."""
    client = ClienteFinto()
    with pytest.raises(AssertionError, match="mai aperta"):
        client.attendi_operazione("op-inventato")
