from unittest.mock import Mock
import pytest
import imballaggio
from modello import RigaSpedizione, Scatola
from spapi_client import SpApiError
from imballaggio import (genera_opzioni_imballaggio, conferma_imballaggio,
                          imposta_informazioni_imballaggio)


def _scatola(righe=None):
    return Scatola(quantita=2, peso_kg=6.5, lunghezza_cm=40, larghezza_cm=30, altezza_cm=20,
                    righe=righe or [])


def test_genera_opzioni_imballaggio_torna_le_opzioni_proposte():
    client = Mock()
    client.post.return_value = {"operationId": "op1"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    # packingGroups è una lista di ID (stringhe), non di oggetti — modello
    # fulfillmentInbound_2024-03-20, PackingOption.packingGroups
    client.get.return_value = {"packingOptions": [
        {"packingOptionId": "po1", "packingGroups": ["pg1"]},
        {"packingOptionId": "po2", "packingGroups": ["pg1", "pg2"]},
    ]}
    opzioni = genera_opzioni_imballaggio(client, "wf1")
    assert [o.packing_option_id for o in opzioni] == ["po1", "po2"]
    assert opzioni[1].descrizione == "2 gruppo/i di imballaggio"
    assert opzioni[1].packing_group_ids == ["pg1", "pg2"]
    client.post.assert_called_with("/inbound/fba/2024-03-20/inboundPlans/wf1/packingOptions")


def test_genera_opzioni_imballaggio_interroga_l_operazione_ricevuta():
    client = Mock()
    client.post.return_value = {"operationId": "op-pack-7"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"packingOptions": []}
    genera_opzioni_imballaggio(client, "wf1")
    client.attendi_operazione.assert_called_with("op-pack-7")


def test_genera_opzioni_imballaggio_operazione_fallita():
    client = Mock()
    client.post.return_value = {"operationId": "op1"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "quantità non compatibile con imballaggio standard"}],
    }
    with pytest.raises(SpApiError, match="quantità non compatibile"):
        genera_opzioni_imballaggio(client, "wf1")


def test_conferma_imballaggio_chiama_l_endpoint_dell_opzione_scelta():
    client = Mock()
    client.post.return_value = {"operationId": "op2"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    conferma_imballaggio(client, "wf1", "po1")
    client.post.assert_called_with("/inbound/fba/2024-03-20/inboundPlans/wf1/packingOptions/po1/confirmation")
    client.attendi_operazione.assert_called_with("op2")


def test_conferma_imballaggio_fallita_alza_errore():
    client = Mock()
    client.post.return_value = {"operationId": "op2"}
    client.attendi_operazione.return_value = {"operationStatus": "FAILED", "operationProblems": []}
    with pytest.raises(SpApiError, match="motivo non specificato"):
        conferma_imballaggio(client, "wf1", "po1")


def test_conferma_imballaggio_fallita_con_messaggi_vuoti_dice_motivo_non_specificato():
    client = Mock()
    client.post.return_value = {"operationId": "op2"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": ""}, {"message": ""}],
    }
    with pytest.raises(SpApiError, match="motivo non specificato"):
        conferma_imballaggio(client, "wf1", "po1")


def test_imposta_informazioni_manda_scatole_misure_e_peso():
    client = Mock()
    client.post.return_value = {"operationId": "op-info"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    imposta_informazioni_imballaggio(client, "wf1", {"pg1": [_scatola()]}, "APJ6JRA9NG5V4")
    assert client.post.call_args.args[0] == "/inbound/fba/2024-03-20/inboundPlans/wf1/packingInformation"
    corpo = client.post.call_args.kwargs["json_body"]
    assert corpo == {"packageGroupings": [{
        "packingGroupId": "pg1",
        "boxes": [{
            "contentInformationSource": "MANUAL_PROCESS",
            "dimensions": {"length": 40, "width": 30, "height": 20, "unitOfMeasurement": "CM"},
            "weight": {"value": 6.5, "unit": "KG"},
            "quantity": 2,
        }],
    }]}
    client.attendi_operazione.assert_called_with("op-info")


def test_imposta_informazioni_con_contenuto_dichiarato_lo_manda():
    """Gli owner del contenuto devono ripetere quelli dichiarati nel piano:
    Amazon confronta le due dichiarazioni e rifiuta se non combaciano."""
    client = Mock()
    client.get.return_value = {"mskuPrepDetails": [
        {"msku": "SKU-1", "prepOwnerConstraint": "NONE_ONLY",
         "labelOwnerConstraint": "NONE_ONLY", "prepCategory": "NONE"}]}
    client.post.return_value = {"operationId": "op-info"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    scatola = _scatola(righe=[RigaSpedizione(sku="SKU-1", quantita=15)])
    imposta_informazioni_imballaggio(client, "wf1", {"pg1": [scatola]}, "APJ6JRA9NG5V4")
    box = client.post.call_args.kwargs["json_body"]["packageGroupings"][0]["boxes"][0]
    assert box["contentInformationSource"] == "BOX_CONTENT_PROVIDED"
    assert box["items"] == [
        {"msku": "SKU-1", "quantity": 15, "prepOwner": "NONE", "labelOwner": "NONE"},
    ]


def test_imposta_informazioni_senza_gruppi_si_ferma_prima_di_chiamare_amazon():
    client = Mock()
    with pytest.raises(ValueError, match="nessun gruppo"):
        imposta_informazioni_imballaggio(client, "wf1", {}, "APJ6JRA9NG5V4")
    client.post.assert_not_called()


def test_imposta_informazioni_gruppo_senza_scatole_si_ferma():
    client = Mock()
    with pytest.raises(ValueError, match="pg1"):
        imposta_informazioni_imballaggio(client, "wf1", {"pg1": []}, "APJ6JRA9NG5V4")
    client.post.assert_not_called()


def test_imposta_informazioni_fallita_alza_errore_col_motivo():
    client = Mock()
    client.post.return_value = {"operationId": "op-info"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "peso scatola oltre il limite"}],
    }
    with pytest.raises(SpApiError, match="peso scatola oltre il limite"):
        imposta_informazioni_imballaggio(client, "wf1", {"pg1": [_scatola()]}, "APJ6JRA9NG5V4")


@pytest.mark.parametrize("campo, valore", [
    ("quantita", 0),
    ("peso_kg", 0),
    ("peso_kg", -1.5),
    ("lunghezza_cm", 0),
    ("larghezza_cm", -30),
    ("altezza_cm", 0),
])
def test_scatola_con_misure_non_valide_si_ferma_prima_di_chiamare_amazon(campo, valore):
    """Peso e misure valgono quanto SKU e quantità: una scatola che pesa 0 o
    misura meno di zero si ferma qui, non arriva ad Amazon."""
    client = Mock()
    scatola = _scatola()
    setattr(scatola, campo, valore)
    with pytest.raises(ValueError, match=campo):
        imposta_informazioni_imballaggio(client, "wf1", {"pg1": [scatola]}, "APJ6JRA9NG5V4")
    client.post.assert_not_called()


def test_scatola_non_valida_nomina_la_scatola_e_il_gruppo():
    client = Mock()
    scatole = [_scatola(), _scatola()]
    scatole[1].peso_kg = -2
    with pytest.raises(ValueError, match="scatola 2 del gruppo pg1"):
        imposta_informazioni_imballaggio(client, "wf1", {"pg1": scatole}, "APJ6JRA9NG5V4")
    client.post.assert_not_called()


@pytest.mark.parametrize("chiamata", [
    lambda c: genera_opzioni_imballaggio(c, "wf1"),
    lambda c: conferma_imballaggio(c, "wf1", "po1"),
    lambda c: imposta_informazioni_imballaggio(c, "wf1", {"pg1": [_scatola()]}, "APJ6JRA9NG5V4"),
])
def test_risposta_senza_operation_id_alza_spapierror(chiamata):
    client = Mock()
    client.post.return_value = {"qualcosa": "di inatteso"}
    with pytest.raises(SpApiError, match="operationId"):
        chiamata(client)
    client.attendi_operazione.assert_not_called()


def test_leggi_articoli_gruppo_torna_gli_msku_del_gruppo_con_le_quantita():
    class Client:
        def get(self, path, params=None):
            assert path == "/inbound/fba/2024-03-20/inboundPlans/wf1/packingGroups/pg1/items"
            # forma vera, tests/fixtures/stadio/phytopet_piazzamento_scaduto.json
            return {"items": [{"labelOwner": "NONE", "msku": "DENTIFRICIO_SPRAY_DUE", "quantity": 20,
                               "fnsku": "B0F9XFJZ4Y", "asin": "B0F9XFJZ4Y", "prepInstructions": []}]}
    assert imballaggio.leggi_articoli_gruppo(Client(), "wf1", "pg1") == {"DENTIFRICIO_SPRAY_DUE": 20}


def test_leggi_articoli_gruppo_somma_lo_stesso_msku_su_piu_righe():
    """Uno SKU con scadenza può tornare su due righe (una per data): i pezzi
    del gruppo sono la somma, non l'ultima riga letta."""
    class Client:
        def get(self, path, params=None):
            return {"items": [{"msku": "Reladol_100", "quantity": 40, "expiration": "2027-06-30"},
                              {"msku": "Reladol_100", "quantity": 21, "expiration": "2027-09-30"}]}
    assert imballaggio.leggi_articoli_gruppo(Client(), "wf1", "pg1") == {"Reladol_100": 61}


def test_contenuto_scatola_ripete_la_scadenza_dichiarata_nel_piano():
    """Stessa regola degli owner: il contenuto dichiarato deve ripetere gli
    articoli del piano, scadenza compresa."""
    client = Mock()
    client.get.return_value = {"mskuPrepDetails": [
        {"msku": "SKU-1", "prepOwnerConstraint": "SELLER_ONLY",
         "labelOwnerConstraint": "SELLER_ONLY", "prepCategory": "LIQUID"}]}
    client.post.return_value = {"operationId": "op-info"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    scatola = _scatola(righe=[RigaSpedizione(sku="SKU-1", quantita=15, scadenza="2028-03-14")])
    imposta_informazioni_imballaggio(client, "wf1", {"pg1": [scatola]}, "APJ6JRA9NG5V4")
    box = client.post.call_args.kwargs["json_body"]["packageGroupings"][0]["boxes"][0]
    assert box["items"] == [{"msku": "SKU-1", "quantity": 15, "prepOwner": "SELLER",
                             "labelOwner": "SELLER", "expiration": "2028-03-14"}]
