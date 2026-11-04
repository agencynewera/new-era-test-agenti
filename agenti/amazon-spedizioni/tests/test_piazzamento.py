from unittest.mock import Mock
import pytest
from spapi_client import SpApiError
from piazzamento import (genera_opzioni_piazzamento, leggi_spedizioni,
                          conferma_piazzamento)


def test_genera_opzioni_piazzamento_torna_spedizioni_e_commissioni():
    client = Mock()
    client.post.return_value = {"operationId": "op-piaz"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"placementOptions": [
        {"placementOptionId": "pl1", "shipmentIds": ["sh1"], "status": "OFFERED",
         "fees": [{"type": "FEE", "target": "Placement Services", "description": "commissione",
                    "value": {"amount": 12.34, "code": "EUR"}}],
         "discounts": []},
        {"placementOptionId": "pl2", "shipmentIds": ["sh2", "sh3"], "status": "OFFERED",
         "fees": [], "discounts": []},
    ]}
    opzioni = genera_opzioni_piazzamento(client, "wf1")
    assert client.post.call_args.args[0] == "/inbound/fba/2024-03-20/inboundPlans/wf1/placementOptions"
    assert client.post.call_args.kwargs.get("json_body") == {}
    assert [o.placement_option_id for o in opzioni] == ["pl1", "pl2"]
    assert opzioni[0].shipment_ids == ["sh1"]
    assert opzioni[0].commissioni == 12.34
    assert opzioni[0].valuta == "EUR"
    assert opzioni[1].shipment_ids == ["sh2", "sh3"]
    assert opzioni[1].commissioni is None


def test_genera_opzioni_piazzamento_somma_piu_commissioni():
    client = Mock()
    client.post.return_value = {"operationId": "op-piaz"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"placementOptions": [
        {"placementOptionId": "pl1", "shipmentIds": ["sh1"],
         "fees": [{"value": {"amount": 10.0, "code": "EUR"}},
                   {"value": {"amount": 5.5, "code": "EUR"}}]},
    ]}
    assert genera_opzioni_piazzamento(client, "wf1")[0].commissioni == 15.5


def test_genera_opzioni_piazzamento_interroga_l_operazione_ricevuta():
    client = Mock()
    client.post.return_value = {"operationId": "op-piaz-9"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"placementOptions": []}
    genera_opzioni_piazzamento(client, "wf1")
    client.attendi_operazione.assert_called_with("op-piaz-9")


def test_genera_opzioni_piazzamento_fallita_alza_errore_col_motivo():
    client = Mock()
    client.post.return_value = {"operationId": "op-piaz"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "informazioni di imballaggio mancanti"}],
    }
    with pytest.raises(SpApiError, match="informazioni di imballaggio mancanti"):
        genera_opzioni_piazzamento(client, "wf1")


def test_leggi_spedizioni_legge_destinazione_contenuto_e_confirmation_id():
    client = Mock()
    client.get.side_effect = [
        {"shipmentId": "sh1", "shipmentConfirmationId": "FBA1234ABCD",
         "destination": {"destinationType": "AMAZON_WAREHOUSE", "warehouseId": "MXP5"},
         "source": {"sourceType": "SELLER_PROVIDED"}, "placementOptionId": "pl1"},
        {"items": [{"msku": "SKU-1", "quantity": 30, "asin": "B01", "fnsku": "X1",
                     "labelOwner": "SELLER", "prepInstructions": []}]},
    ]
    spedizioni = leggi_spedizioni(client, "wf1", ["sh1"])
    assert len(spedizioni) == 1
    s = spedizioni[0]
    assert s.shipment_id == "sh1"
    assert s.centro_logistico == "MXP5"
    assert s.shipment_confirmation_id == "FBA1234ABCD"
    assert [(r.sku, r.quantita) for r in s.righe] == [("SKU-1", 30)]
    assert client.get.call_args_list[0].args[0] == "/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1"
    assert client.get.call_args_list[1].args[0] == "/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1/items"


def test_leggi_spedizioni_centro_non_assegnato_lo_dichiara():
    """Con destinazione AMAZON_OPTIMIZED il warehouseId è vuoto finché Amazon
    non assegna: si dichiara, non si inventa un centro."""
    client = Mock()
    client.get.side_effect = [
        {"shipmentId": "sh1", "destination": {"destinationType": "AMAZON_OPTIMIZED"}},
        {"items": []},
    ]
    s = leggi_spedizioni(client, "wf1", ["sh1"])[0]
    assert s.centro_logistico == "centro non ancora assegnato"
    assert s.shipment_confirmation_id is None


def test_leggi_spedizioni_piu_spedizioni_le_legge_tutte():
    client = Mock()
    client.get.side_effect = [
        {"shipmentId": "sh1", "destination": {"warehouseId": "MXP5"}},
        {"items": [{"msku": "SKU-1", "quantity": 30}]},
        {"shipmentId": "sh2", "destination": {"warehouseId": "BLQ1"}},
        {"items": [{"msku": "SKU-2", "quantity": 10}]},
    ]
    spedizioni = leggi_spedizioni(client, "wf1", ["sh1", "sh2"])
    assert [s.centro_logistico for s in spedizioni] == ["MXP5", "BLQ1"]
    assert [s.righe[0].sku for s in spedizioni] == ["SKU-1", "SKU-2"]


def test_conferma_piazzamento_chiama_l_endpoint_dell_opzione_scelta():
    client = Mock()
    client.post.return_value = {"operationId": "op-piaz-conf"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    conferma_piazzamento(client, "wf1", "pl1")
    client.post.assert_called_with(
        "/inbound/fba/2024-03-20/inboundPlans/wf1/placementOptions/pl1/confirmation"
    )
    client.attendi_operazione.assert_called_with("op-piaz-conf")


def test_conferma_piazzamento_fallita_alza_errore_col_motivo():
    client = Mock()
    client.post.return_value = {"operationId": "op-piaz-conf"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "opzione di piazzamento scaduta"}],
    }
    with pytest.raises(SpApiError, match="opzione di piazzamento scaduta"):
        conferma_piazzamento(client, "wf1", "pl1")


@pytest.mark.parametrize("caduta", [
    SpApiError("errore di rete su GET /operations/op-piaz-conf dopo 5 tentativi: rete assente"),
    SpApiError("operazione op-piaz-conf non conclusa dopo 300s"),
    RuntimeError("crash inatteso nel polling"),
])
def test_conferma_piazzamento_attesa_caduta_dichiara_esito_incerto(caduta):
    """La POST è già partita: la commissione di piazzamento può essere stata
    addebitata. Se cade la rete DURANTE l'attesa dell'esito, il messaggio che
    arriva a Mariano non può parlare di un banale errore di lettura — chi legge
    penserebbe che non sia successo nulla e rifarebbe il giro, pagando due
    volte una commissione irreversibile."""
    client = Mock()
    client.post.return_value = {"operationId": "op-piaz-conf"}
    client.attendi_operazione.side_effect = caduta
    with pytest.raises(SpApiError) as errore:
        conferma_piazzamento(client, "wf1", "pl1")
    messaggio = str(errore.value)
    assert "ESITO INCERTO" in messaggio
    assert "wf1" in messaggio and "pl1" in messaggio
    assert "Seller Central" in messaggio
    assert str(caduta) in messaggio


@pytest.mark.parametrize("chiamata", [
    lambda c: genera_opzioni_piazzamento(c, "wf1"),
    lambda c: conferma_piazzamento(c, "wf1", "pl1"),
])
def test_risposta_senza_operation_id_alza_spapierror(chiamata):
    """`conferma_piazzamento` è oltre il cancello del "vai": una risposta
    inattesa deve restare dentro il contratto d'errore, non un KeyError."""
    client = Mock()
    client.post.return_value = {"qualcosa": "di inatteso"}
    with pytest.raises(SpApiError, match="operationId"):
        chiamata(client)
    client.attendi_operazione.assert_not_called()


def test_opzione_da_amazon_somma_le_commissioni_e_regge_quelle_senza_importo():
    """La stessa lettura serve a `genera_opzioni_piazzamento` e a `giro.py`,
    che le opzioni le ha già in mano dalla fotografia dello stadio: una sola
    copia, o le due interpretazioni divergono su quanto costa una divisione."""
    from piazzamento import opzione_da_amazon
    o = opzione_da_amazon({"placementOptionId": "pl1", "shipmentIds": ["sh1", "sh2"],
                           "fees": [{"value": {"amount": 10.0, "code": "EUR"}},
                                     {"value": {"amount": 2.5, "code": "EUR"}},
                                     {"type": "FEE", "value": {}}]})
    assert o.placement_option_id == "pl1" and o.shipment_ids == ["sh1", "sh2"]
    assert o.commissioni == 12.5 and o.valuta == "EUR"
    senza = opzione_da_amazon({"placementOptionId": "pl2", "fees": [{"type": "FEE"}]})
    assert senza.commissioni is None and senza.valuta == "EUR" and senza.shipment_ids == []


def test_leggi_spedizioni_riporta_il_codice_asn():
    """`amazonReferenceId` è il codice che il trasportatore deve dare ad Amazon
    per consegnare (Mariano lo chiama ASN). Amazon lo genera col piazzamento
    confermato: senza leggerlo qui resterebbe solo dentro Seller Central."""
    client = Mock()
    client.get.side_effect = [
        {"shipmentId": "sh1", "destination": {"warehouseId": "XLI8"},
         "shipmentConfirmationId": "FBA15M1QXGH7", "amazonReferenceId": "4KK9J93M"},
        {"items": [{"msku": "138_FBA", "quantity": 108}]},
    ]
    spedizione = leggi_spedizioni(client, "wf1", ["sh1"])[0]
    assert spedizione.amazon_reference_id == "4KK9J93M"


def test_leggi_spedizioni_senza_asn_non_inventa():
    """Prima della conferma del piazzamento il codice non esiste ancora: resta
    None, così l'anteprima può dire che non c'è invece di stampare una stringa
    vuota che sembrerebbe un codice."""
    client = Mock()
    client.get.side_effect = [
        {"shipmentId": "sh1", "destination": {"warehouseId": ""}},
        {"items": []},
    ]
    assert leggi_spedizioni(client, "wf1", ["sh1"])[0].amazon_reference_id is None
