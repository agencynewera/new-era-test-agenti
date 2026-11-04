from unittest.mock import Mock
import pytest
import corrieri
from spapi_client import SpApiError
from corrieri import genera_opzioni_trasporto, genera_finestre_consegna

_PATH = "/inbound/fba/2024-03-20/inboundPlans/wf1/transportationOptions"


def _client(opzioni):
    client = Mock()
    client.post.return_value = {"operationId": "op1"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"transportationOptions": opzioni}
    return client


def _genera(client, **kwargs):
    parametri = {"placement_option_id": "pl1", "shipment_ids": ["sh1"],
                  "data_pronta": "2026-09-05T08:00Z"}
    parametri.update(kwargs)
    return genera_opzioni_trasporto(client, "wf1", **parametri)


def test_opzione_convenzionata_ha_il_prezzo():
    client = _client([
        {"transportationOptionId": "to1", "shipmentId": "sh1",
         "shippingSolution": "AMAZON_PARTNERED_CARRIER", "shippingMode": "GROUND_SMALL_PARCEL",
         "carrier": {"name": "UPS", "alphaCode": "UPSN"}, "preconditions": [],
         "quote": {"cost": {"amount": 42.5, "code": "EUR"}}},
    ])
    opzioni = _genera(client)
    assert opzioni[0].convenzionato is True
    assert opzioni[0].costo == 42.5
    assert opzioni[0].valuta == "EUR"
    assert opzioni[0].corriere == "UPS"
    assert opzioni[0].shipment_id == "sh1"
    assert opzioni[0].modalita == "GROUND_SMALL_PARCEL"
    assert opzioni[0].richiede_finestra_consegna is False


def test_genera_opzioni_manda_piazzamento_spedizioni_e_data_pronta():
    client = _client([])
    _genera(client, shipment_ids=["sh1", "sh2"])
    assert client.post.call_args.args[0] == _PATH
    corpo = client.post.call_args.kwargs["json_body"]
    assert corpo == {
        "placementOptionId": "pl1",
        "shipmentTransportationConfigurations": [
            {"shipmentId": "sh1", "readyToShipWindow": {"start": "2026-09-05T08:00Z"}},
            {"shipmentId": "sh2", "readyToShipWindow": {"start": "2026-09-05T08:00Z"}},
        ],
    }
    client.get.assert_called_with(_PATH, params={"placementOptionId": "pl1", "pageSize": 20})


def test_genera_opzioni_aggiunge_contatto_e_pallet_quando_ci_sono():
    client = _client([])
    contatto = {"name": "DATO FINTO 6", "phoneNumber": "+390574000000"}
    pallet = [{"quantity": 2, "stackability": "STACKABLE",
                "weight": {"value": 300, "unit": "KG"},
                "dimensions": {"length": 120, "width": 80, "height": 150, "unitOfMeasurement": "CM"}}]
    _genera(client, contatto=contatto, pallet=pallet)
    configurazione = client.post.call_args.kwargs["json_body"]["shipmentTransportationConfigurations"][0]
    assert configurazione["contactInformation"] == contatto
    assert configurazione["pallets"] == pallet


def test_opzione_corriere_proprio_senza_prezzo_e_con_finestra_richiesta():
    client = _client([
        {"transportationOptionId": "to2", "shipmentId": "sh1",
         "shippingSolution": "USE_YOUR_OWN_CARRIER", "shippingMode": "GROUND_SMALL_PARCEL",
         "carrier": {"name": "Poste Italiane"}, "preconditions": ["CONFIRMED_DELIVERY_WINDOW"]},
    ])
    opzioni = _genera(client)
    assert opzioni[0].convenzionato is False
    assert opzioni[0].costo is None
    assert opzioni[0].corriere == "Poste Italiane"
    assert opzioni[0].richiede_finestra_consegna is True


def test_carrier_presente_ma_nullo_non_esplode():
    client = _client([
        {"transportationOptionId": "to3", "shipmentId": "sh1",
         "shippingSolution": "USE_YOUR_OWN_CARRIER", "carrier": None, "preconditions": []},
    ])
    assert _genera(client)[0].corriere == "corriere proprio"


def test_preventivo_presente_si_prende_anche_se_il_programma_non_e_riconosciuto():
    """Il prezzo non dipende dal programma: scartare un preventivo reale
    perché lo `shippingSolution` non è quello atteso mostrerebbe a Mariano
    come gratuita una spedizione che Amazon ha quotato."""
    client = _client([
        {"transportationOptionId": "to4", "shipmentId": "sh1",
         "shippingSolution": "AMAZON_PARTNERED_CARRIER_LTL_NEW", "carrier": {"name": "DHL"},
         "preconditions": [], "quote": {"cost": {"amount": 88.0, "code": "EUR"}}},
    ])
    opzione = _genera(client)[0]
    assert opzione.convenzionato is False
    assert opzione.costo == 88.0
    assert opzione.valuta == "EUR"


def test_corriere_proprio_con_preventivo_lo_mostra():
    client = _client([
        {"transportationOptionId": "to5", "shipmentId": "sh1",
         "shippingSolution": "USE_YOUR_OWN_CARRIER", "carrier": {"name": "BRT"},
         "preconditions": [], "quote": {"cost": {"amount": 15.9, "code": "EUR"}}},
    ])
    opzione = _genera(client)[0]
    assert opzione.convenzionato is False
    assert opzione.costo == 15.9


def test_entrambe_le_opzioni_insieme_quando_amazon_le_offre():
    client = _client([
        {"transportationOptionId": "to1", "shipmentId": "sh1",
         "shippingSolution": "AMAZON_PARTNERED_CARRIER", "carrier": {"name": "UPS"},
         "preconditions": [], "quote": {"cost": {"amount": 42.5, "code": "EUR"}}},
        {"transportationOptionId": "to2", "shipmentId": "sh1",
         "shippingSolution": "USE_YOUR_OWN_CARRIER", "carrier": {"name": "BRT"},
         "preconditions": ["CONFIRMED_DELIVERY_WINDOW"]},
    ])
    opzioni = _genera(client)
    assert len(opzioni) == 2
    assert [o.convenzionato for o in opzioni] == [True, False]


def test_genera_opzioni_interroga_l_operazione_ricevuta():
    client = _client([])
    client.post.return_value = {"operationId": "op-trasp-3"}
    _genera(client)
    client.attendi_operazione.assert_called_with("op-trasp-3")


def test_senza_spedizioni_si_ferma_prima_di_chiamare_amazon():
    client = _client([])
    with pytest.raises(ValueError, match="nessuna spedizione"):
        _genera(client, shipment_ids=[])
    client.post.assert_not_called()


def test_senza_data_pronta_si_ferma_prima_di_chiamare_amazon():
    client = _client([])
    with pytest.raises(ValueError, match="data di merce pronta"):
        _genera(client, data_pronta="")
    client.post.assert_not_called()


def test_generazione_fallita_alza_errore_col_motivo():
    client = _client([])
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "nessun corriere disponibile per questa destinazione"}],
    }
    with pytest.raises(SpApiError, match="nessun corriere disponibile"):
        _genera(client)


def test_genera_finestre_consegna_torna_le_finestre_offerte():
    client = Mock()
    client.post.return_value = {"operationId": "op-fin"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"deliveryWindowOptions": [
        {"deliveryWindowOptionId": "dw1", "startDate": "2026-09-10T00:00Z",
         "endDate": "2026-09-17T00:00Z", "availabilityType": "AVAILABLE",
         "validUntil": "2026-09-08T00:00Z"},
    ]}
    finestre = genera_finestre_consegna(client, "wf1", "sh1")
    percorso = "/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1/deliveryWindowOptions"
    assert client.post.call_args.args[0] == percorso
    assert client.get.call_args.args[0] == percorso
    assert finestre[0].delivery_window_option_id == "dw1"
    assert finestre[0].inizio == "2026-09-10T00:00Z"
    assert finestre[0].fine == "2026-09-17T00:00Z"
    assert finestre[0].disponibilita == "AVAILABLE"
    client.attendi_operazione.assert_called_with("op-fin")


def test_genera_finestre_consegna_fallita_alza_errore_col_motivo():
    client = Mock()
    client.post.return_value = {"operationId": "op-fin"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "centro logistico al completo"}],
    }
    with pytest.raises(SpApiError, match="centro logistico al completo"):
        genera_finestre_consegna(client, "wf1", "sh1")


def _pallet(quantita):
    return [{"quantity": quantita, "stackability": "STACKABLE",
              "weight": {"value": 300, "unit": "KG"},
              "dimensions": {"length": 120, "width": 80, "height": 150,
                              "unitOfMeasurement": "CM"}}]


def test_pallet_per_spedizione_quota_a_ciascuna_solo_i_suoi():
    """Lotto diviso in due spedizioni con pallet DIVERSI: quotare a entrambe
    lo stesso carico darebbe a Mariano un preventivo LTL sbagliato. I due
    valori devono essere distinguibili, altrimenti il test passerebbe anche
    se ogni spedizione ricevesse i pallet dell'altra."""
    client = _client([])
    _genera(client, shipment_ids=["sh1", "sh2"],
             pallet={"sh1": _pallet(1), "sh2": _pallet(4)})
    configurazioni = client.post.call_args.kwargs["json_body"]["shipmentTransportationConfigurations"]
    assert configurazioni[0]["pallets"] == _pallet(1)
    assert configurazioni[1]["pallets"] == _pallet(4)


def test_pallet_per_spedizione_omette_chi_non_ne_ha():
    client = _client([])
    _genera(client, shipment_ids=["sh1", "sh2"], pallet={"sh1": _pallet(2)})
    configurazioni = client.post.call_args.kwargs["json_body"]["shipmentTransportationConfigurations"]
    assert configurazioni[0]["pallets"] == _pallet(2)
    assert "pallets" not in configurazioni[1]


def test_pallet_unico_resta_valido_per_tutte_le_spedizioni():
    """Comportamento di default conservato: chi passa una lista sola continua
    a vederla applicata a ogni spedizione."""
    client = _client([])
    _genera(client, shipment_ids=["sh1", "sh2"], pallet=_pallet(3))
    configurazioni = client.post.call_args.kwargs["json_body"]["shipmentTransportationConfigurations"]
    assert [c["pallets"] for c in configurazioni] == [_pallet(3), _pallet(3)]


@pytest.mark.parametrize("chiamata", [
    lambda c: _genera(c),
    lambda c: genera_finestre_consegna(c, "wf1", "sh1"),
])
def test_risposta_senza_operation_id_alza_spapierror(chiamata):
    client = _client([])
    client.post.return_value = {"qualcosa": "di inatteso"}
    with pytest.raises(SpApiError, match="operationId"):
        chiamata(client)
    client.attendi_operazione.assert_not_called()


@pytest.mark.parametrize("data_pronta", ["2026-08-30", "30/08/2026", "2026-08-30T09:00"])
def test_data_pronta_senza_istante_si_ferma_prima_di_amazon(data_pronta):
    """Misurato dal vivo il 2026-08-29: Amazon vuole un istante UTC e respinge
    con 400 la data secca. A quel punto il piano è già creato e le scatole già
    dichiarate: il giro si perde per un formato."""
    client = Mock()
    with pytest.raises(ValueError, match="istante UTC"):
        genera_opzioni_trasporto(client, "wf1", "pl1", ["sh1"], data_pronta)
    client.post.assert_not_called()


def test_data_pronta_con_istante_passa():
    client = Mock()
    client.post.return_value = {"operationId": "op1"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"transportationOptions": []}
    genera_opzioni_trasporto(client, "wf1", "pl1", ["sh1"], "2026-08-30T09:00Z")
    finestra = client.post.call_args.kwargs["json_body"][
        "shipmentTransportationConfigurations"][0]["readyToShipWindow"]
    assert finestra == {"start": "2026-08-30T09:00Z"}


def test_leggi_opzioni_trasporto_senza_generare_e_con_scadenza():
    """`conferma` deve poter rileggere i preventivi già generati e sapere
    se sono scaduti, senza chiedere ad Amazon di rifarli."""
    class Client:
        def __init__(self):
            self.post_fatte = 0
        def post(self, *a, **k):
            self.post_fatte += 1
            return {"operationId": "op"}
        def get(self, path, params=None):
            assert path.endswith("/inboundPlans/wf1/transportationOptions")
            assert params == {"placementOptionId": "pl1", "pageSize": 20}
            return {"transportationOptions": [
                {"transportationOptionId": "to1", "shipmentId": "sh1",
                 "shippingSolution": "AMAZON_PARTNERED_CARRIER", "shippingMode": "GROUND_SMALL_PARCEL",
                 "carrier": {"name": "UPS"}, "preconditions": [],
                 "quote": {"cost": {"amount": 7.9, "code": "EUR"}, "expiration": "2026-09-03T10:00:00.000Z"}},
                {"transportationOptionId": "to2", "shipmentId": "sh1",
                 "shippingSolution": "USE_YOUR_OWN_CARRIER", "shippingMode": "FREIGHT_LTL",
                 "preconditions": ["CONFIRMED_DELIVERY_WINDOW"]},
            ]}
    c = Client()
    opzioni = corrieri.leggi_opzioni_trasporto(c, "wf1", "pl1")
    assert c.post_fatte == 0
    assert [o.transportation_option_id for o in opzioni] == ["to1", "to2"]
    assert opzioni[0].scadenza == "2026-09-03T10:00:00.000Z"
    assert opzioni[1].scadenza is None and opzioni[1].richiede_finestra_consegna


def test_leggi_finestre_consegna_non_genera_niente():
    """Serve alla validazione di `giro.conferma`, che gira PRIMA del "vai":
    deve poter controllare un id di finestra senza fare nessuna POST."""
    client = Mock()
    client.get.return_value = {"deliveryWindowOptions": [
        {"deliveryWindowOptionId": "dw1", "startDate": "2026-09-10T00:00Z",
         "endDate": "2026-09-17T00:00Z", "availabilityType": "AVAILABLE"},
    ]}
    finestre = corrieri.leggi_finestre_consegna(client, "wf1", "sh1")
    assert client.post.call_count == 0
    assert client.get.call_args.args[0] == "/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1/deliveryWindowOptions"
    assert [f.delivery_window_option_id for f in finestre] == ["dw1"]
    assert finestre[0].inizio == "2026-09-10T00:00Z"


def test_leggi_opzioni_trasporto_segue_la_paginazione_a_pagine_da_venti():
    """Dal vivo (02/09, Phy to Pet) listTransportationOptions torna 10
    opzioni per pagina se non si chiede altro, accetta al massimo pageSize=20
    e un piano da 20 pezzi ne aveva 274: l'unica convenzionata UPS era alla
    pagina 14 e il menù non la mostrava."""
    class Client:
        def __init__(self):
            self.chiamate = []
        def get(self, path, params=None):
            self.chiamate.append(dict(params))
            assert params["pageSize"] == 20
            if params.get("paginationToken") is None:
                return {"transportationOptions": [
                    {"transportationOptionId": "to-1", "shipmentId": "sh1",
                     "shippingSolution": "USE_YOUR_OWN_CARRIER", "shippingMode": "GROUND_SMALL_PARCEL",
                     "carrier": {"name": "CIIQ", "alphaCode": "CIIQ"}, "preconditions": []}],
                        "pagination": {"nextToken": "tok-2"}}
            assert params["paginationToken"] == "tok-2"
            return {"transportationOptions": [
                {"transportationOptionId": "to-ups", "shipmentId": "sh1",
                 "shippingSolution": "AMAZON_PARTNERED_CARRIER", "shippingMode": "GROUND_SMALL_PARCEL",
                 "carrier": {"name": "UPS", "alphaCode": "UPSN"}, "preconditions": [],
                 "quote": {"cost": {"amount": 7.9, "code": "EUR"}}}],
                    "pagination": {}}
    c = Client()
    opzioni = corrieri.leggi_opzioni_trasporto(c, "wf1", "pl1")
    assert [o.transportation_option_id for o in opzioni] == ["to-1", "to-ups"]
    assert len(c.chiamate) == 2 and c.chiamate[0]["placementOptionId"] == "pl1"
    assert opzioni[1].convenzionato and opzioni[1].costo == 7.9 and opzioni[1].scadenza is None
