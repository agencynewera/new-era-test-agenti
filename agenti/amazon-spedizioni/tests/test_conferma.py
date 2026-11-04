from unittest.mock import Mock
import pytest
from modello import OpzioneTrasporto
from spapi_client import SpApiError
from conferma import conferma_trasporto, conferma_finestra_consegna

_PATH_CONFERMA = "/inbound/fba/2024-03-20/inboundPlans/wf1/transportationOptions/confirmation"


def _client():
    client = Mock()
    client.post.return_value = {"operationId": "op3"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    return client


def _opzione(**kwargs):
    parametri = {"transportation_option_id": "to1", "corriere": "UPS", "convenzionato": True,
                  "costo": 42.5, "shipment_id": "sh1"}
    parametri.update(kwargs)
    return OpzioneTrasporto(**parametri)


def test_conferma_trasporto_manda_la_selezione_al_livello_di_piano():
    client = _client()
    conferma_trasporto(client, "wf1", [_opzione()])
    assert client.post.call_args.args[0] == _PATH_CONFERMA
    assert client.post.call_args.kwargs["json_body"] == {
        "transportationSelections": [{"shipmentId": "sh1", "transportationOptionId": "to1"}],
    }
    client.attendi_operazione.assert_called_with("op3")


def test_conferma_trasporto_piu_spedizioni_in_una_sola_chiamata():
    client = _client()
    conferma_trasporto(client, "wf1", [
        _opzione(shipment_id="sh1", transportation_option_id="to1"),
        _opzione(shipment_id="sh2", transportation_option_id="to2"),
    ])
    assert client.post.call_count == 1
    assert client.post.call_args.kwargs["json_body"]["transportationSelections"] == [
        {"shipmentId": "sh1", "transportationOptionId": "to1"},
        {"shipmentId": "sh2", "transportationOptionId": "to2"},
    ]


def test_conferma_trasporto_aggiunge_il_contatto_quando_c_e():
    client = _client()
    contatto = {"name": "DATO FINTO 6", "phoneNumber": "+390574000000"}
    conferma_trasporto(client, "wf1", [_opzione()], contatto=contatto)
    selezione = client.post.call_args.kwargs["json_body"]["transportationSelections"][0]
    assert selezione["contactInformation"] == contatto


def test_conferma_trasporto_self_ship_stesso_flusso():
    client = _client()
    client.post.return_value = {"operationId": "op4"}
    opzione = _opzione(transportation_option_id="to2", corriere="BRT", convenzionato=False,
                        costo=None, richiede_finestra_consegna=True)
    conferma_trasporto(client, "wf1", [opzione])
    assert client.post.call_args.kwargs["json_body"]["transportationSelections"] == [
        {"shipmentId": "sh1", "transportationOptionId": "to2"},
    ]


def test_conferma_trasporto_senza_scelte_si_ferma_prima_di_chiamare_amazon():
    client = _client()
    with pytest.raises(ValueError, match="nessuna opzione"):
        conferma_trasporto(client, "wf1", [])
    client.post.assert_not_called()


def test_conferma_trasporto_opzione_senza_spedizione_si_ferma():
    client = _client()
    with pytest.raises(ValueError, match="to9"):
        conferma_trasporto(client, "wf1", [_opzione(transportation_option_id="to9", shipment_id="")])
    client.post.assert_not_called()


def test_conferma_trasporto_fallita_alza_errore_col_motivo():
    client = _client()
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "finestra di ritiro non più disponibile"}],
    }
    with pytest.raises(SpApiError, match="finestra di ritiro"):
        conferma_trasporto(client, "wf1", [_opzione()])


def test_conferma_finestra_consegna_chiama_l_endpoint_della_finestra_scelta():
    client = _client()
    client.post.return_value = {"operationId": "op-fin-conf"}
    conferma_finestra_consegna(client, "wf1", "sh1", "dw1")
    client.post.assert_called_with(
        "/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1/deliveryWindowOptions/dw1/confirmation"
    )
    client.attendi_operazione.assert_called_with("op-fin-conf")


def test_conferma_finestra_consegna_fallita_alza_errore_col_motivo():
    client = _client()
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "finestra di consegna scaduta"}],
    }
    with pytest.raises(SpApiError, match="finestra di consegna scaduta"):
        conferma_finestra_consegna(client, "wf1", "sh1", "dw1")


@pytest.mark.parametrize("caduta", [
    SpApiError("errore di rete su GET /operations/op3 dopo 5 tentativi: rete assente"),
    SpApiError("operazione op3 non conclusa dopo 300s"),
    RuntimeError("crash inatteso nel polling"),
])
def test_conferma_trasporto_attesa_caduta_dichiara_esito_incerto(caduta):
    """La POST che prenota il corriere è già partita: se cade la rete DURANTE
    l'attesa dell'esito, dire "errore di lettura" farebbe credere che non sia
    successo nulla, e la prenotazione verrebbe rifatta su una spedizione che
    Amazon ha probabilmente già confermato e addebitato."""
    client = _client()
    client.attendi_operazione.side_effect = caduta
    with pytest.raises(SpApiError) as errore:
        conferma_trasporto(client, "wf1", [
            _opzione(shipment_id="sh1", transportation_option_id="to1"),
            _opzione(shipment_id="sh2", transportation_option_id="to2"),
        ])
    messaggio = str(errore.value)
    assert "ESITO INCERTO" in messaggio
    assert "wf1" in messaggio
    for identificativo in ("sh1", "to1", "sh2", "to2"):
        assert identificativo in messaggio
    assert "Seller Central" in messaggio
    assert str(caduta) in messaggio


@pytest.mark.parametrize("chiamata", [
    lambda c: conferma_finestra_consegna(c, "wf1", "sh1", "dw1"),
    lambda c: conferma_trasporto(c, "wf1", [_opzione()]),
])
def test_risposta_senza_operation_id_alza_spapierror(chiamata):
    """`conferma_trasporto` prenota il corriere: se Amazon risponde 2xx con un
    corpo inatteso serve un errore dichiarato, non un KeyError su un'operazione
    che può già essere partita."""
    client = _client()
    client.post.return_value = {"qualcosa": "di inatteso"}
    with pytest.raises(SpApiError, match="operationId"):
        chiamata(client)
    client.attendi_operazione.assert_not_called()
