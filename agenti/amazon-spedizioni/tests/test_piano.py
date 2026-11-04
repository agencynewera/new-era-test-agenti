from unittest.mock import Mock
import pytest
from modello import RigaSpedizione
from spapi_client import SpApiError
from piano import crea_piano


def _righe():
    return [RigaSpedizione(sku="SKU-1", quantita=30), RigaSpedizione(sku="SKU-2", quantita=10)]


def _indirizzo():
    return {"name": "Phy to Pet", "addressLine1": "Via Roma 1", "city": "Prato",
            "postalCode": "59100", "countryCode": "IT"}


def _prep(*mskus, vincolo="NONE_ONLY"):
    """Forma vera di prepDetails, copiata da una risposta reale del 2026-08-29."""
    return {"mskuPrepDetails": [
        {"msku": m, "prepOwnerConstraint": vincolo, "labelOwnerConstraint": vincolo,
         "allOwnersConstraint": "MUST_MATCH", "prepCategory": "NONE", "prepTypes": []}
        for m in mskus
    ]}


def _client_ok(prep=None):
    client = Mock()
    client.get.return_value = prep if prep is not None else _prep("SKU-1", "SKU-2")
    client.post.return_value = {"inboundPlanId": "wf1", "operationId": "op1"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    return client


def test_nessuna_riga_alza_valueerror():
    with pytest.raises(ValueError, match="nessuna riga"):
        crea_piano(Mock(), [], _indirizzo(), "APJ6JRA9NG5V4")


def test_crea_piano_manda_items_e_indirizzo():
    client = _client_ok()
    piano = crea_piano(client, _righe(), _indirizzo(), "APJ6JRA9NG5V4")
    corpo_inviato = client.post.call_args.kwargs["json_body"]
    assert corpo_inviato["sourceAddress"] == _indirizzo()
    assert corpo_inviato["destinationMarketplaces"] == ["APJ6JRA9NG5V4"]
    assert corpo_inviato["items"] == [
        {"msku": "SKU-1", "quantity": 30, "prepOwner": "NONE", "labelOwner": "NONE"},
        {"msku": "SKU-2", "quantity": 10, "prepOwner": "NONE", "labelOwner": "NONE"},
    ]
    assert piano.inbound_plan_id == "wf1"


def test_gli_owner_vengono_da_amazon_non_da_noi():
    """Misurato dal vivo il 2026-08-29: con owner fissi a SELLER, Amazon
    risponde 400 "does not require labelOwner but SELLER was assigned.
    Accepted values: [NONE]". Chi prepara ed etichetta lo decide Amazon per
    SKU, e va chiesto a prepDetails."""
    client = _client_ok(_prep("SKU-1", "SKU-2", vincolo="SELLER_ONLY"))
    crea_piano(client, _righe(), _indirizzo(), "APJ6JRA9NG5V4")
    items = client.post.call_args.kwargs["json_body"]["items"]
    assert [i["prepOwner"] for i in items] == ["SELLER", "SELLER"]
    assert [i["labelOwner"] for i in items] == ["SELLER", "SELLER"]
    # e il vincolo è stato chiesto per gli SKU del lotto, non indovinato
    assert client.get.call_args.kwargs["params"]["mskus"] == "SKU-1,SKU-2"


def test_articolo_senza_vincolo_dichiarato_non_viene_indovinato():
    """prepCategory UNKNOWN: Amazon non ha classificato l'articolo. Tirare a
    indovinare non crea un piano sbagliato (createInboundPlan respinge con
    400 senza creare niente) ma fa perdere il giro con un messaggio oscuro."""
    client = _client_ok({"mskuPrepDetails": [
        {"msku": "SKU-1", "prepCategory": "UNKNOWN", "prepTypes": []},
    ]})
    with pytest.raises(SpApiError, match="SKU-1"):
        crea_piano(client, [RigaSpedizione(sku="SKU-1", quantita=1)],
                   _indirizzo(), "APJ6JRA9NG5V4")
    client.post.assert_not_called()


def test_crea_piano_non_inventa_spedizioni():
    """Le spedizioni nascono col piazzamento, non con il piano: un piano
    appena creato non ha ancora shipmentId né centri logistici."""
    client = _client_ok()
    piano = crea_piano(client, _righe(), _indirizzo(), "APJ6JRA9NG5V4")
    assert piano.spedizioni == []
    assert piano.placement_option_id is None
    # l'unica lettura ammessa qui è quella dei vincoli di preparazione: il
    # piano non va a cercare spedizioni, perché non ne esistono ancora
    letti = [c.args[0] for c in client.get.call_args_list]
    assert letti == ["/inbound/fba/2024-03-20/items/prepDetails"]


def test_crea_piano_interroga_l_operazione_ricevuta():
    client = _client_ok()
    client.post.return_value = {"inboundPlanId": "wf1", "operationId": "op-piano-42"}
    crea_piano(client, _righe(), _indirizzo(), "APJ6JRA9NG5V4")
    client.attendi_operazione.assert_called_with("op-piano-42")


def test_crea_piano_operazione_fallita_alza_spapierror_con_motivo():
    client = _client_ok()
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "indirizzo di origine non valido"}],
    }
    with pytest.raises(SpApiError, match="indirizzo di origine non valido"):
        crea_piano(client, _righe(), _indirizzo(), "APJ6JRA9NG5V4")


@pytest.mark.parametrize("quantita", [0, -5])
def test_quantita_non_valida_si_ferma_prima_di_chiamare_amazon(quantita):
    client = Mock()
    righe = [RigaSpedizione(sku="SKU-1", quantita=30), RigaSpedizione(sku="SKU-2", quantita=quantita)]
    with pytest.raises(ValueError, match="SKU-2"):
        crea_piano(client, righe, _indirizzo(), "APJ6JRA9NG5V4")
    client.post.assert_not_called()


def test_sku_vuoto_si_ferma_prima_di_chiamare_amazon():
    client = Mock()
    righe = [RigaSpedizione(sku="  ", quantita=30)]
    with pytest.raises(ValueError, match="riga 1"):
        crea_piano(client, righe, _indirizzo(), "APJ6JRA9NG5V4")
    client.post.assert_not_called()


@pytest.mark.parametrize("guasto", [
    SpApiError("operazione op1 non conclusa dopo 300s"),
    ConnectionError("connessione caduta"),
])
def test_attesa_fallita_non_perde_il_plan_id(guasto):
    """Quando la POST è già passata, il piano ESISTE su Amazon: se l'attesa
    dell'operazione salta, il plan_id deve arrivare a Mariano nell'errore,
    altrimenti un secondo giro creerebbe un secondo piano sulla stessa merce."""
    client = _client_ok()
    client.post.return_value = {"inboundPlanId": "wf-42", "operationId": "op1"}
    client.attendi_operazione.side_effect = guasto
    with pytest.raises(SpApiError, match="wf-42") as errore:
        crea_piano(client, _righe(), _indirizzo(), "APJ6JRA9NG5V4")
    testo = str(errore.value)
    assert "creato su Amazon" in testo
    assert "annulla" in testo.lower()


def test_operazione_fallita_dice_che_il_piano_esiste_comunque():
    client = _client_ok()
    client.post.return_value = {"inboundPlanId": "wf-42", "operationId": "op1"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "indirizzo di origine non valido"}],
    }
    with pytest.raises(SpApiError, match="indirizzo di origine non valido") as errore:
        crea_piano(client, _righe(), _indirizzo(), "APJ6JRA9NG5V4")
    testo = str(errore.value)
    assert "wf-42" in testo
    assert "creato su Amazon" in testo


def test_risposta_senza_operation_id_alza_spapierror_col_plan_id():
    """Una 2xx con corpo inatteso non deve diventare un KeyError: il piano è
    già nato su Amazon e il suo identificativo va detto lo stesso."""
    client = _client_ok()
    client.post.return_value = {"inboundPlanId": "wf-42"}
    with pytest.raises(SpApiError, match="operationId") as errore:
        crea_piano(client, _righe(), _indirizzo(), "APJ6JRA9NG5V4")
    assert "wf-42" in str(errore.value)
    client.attendi_operazione.assert_not_called()


def test_scadenza_va_in_expiration_solo_per_le_righe_che_la_dichiarano():
    """Misurato dal vivo il 2026-09-03 su ECM: per un articolo con
    `is_expiration_dated_product` Amazon crea il piano e poi lo manda in
    ERRORED con "Expiration date required". La data la dà Mariano per riga;
    le righe senza non devono mandare il campo."""
    client = _client_ok()
    righe = [RigaSpedizione(sku="SKU-1", quantita=30, scadenza="2028-03-14"),
             RigaSpedizione(sku="SKU-2", quantita=10)]
    crea_piano(client, righe, _indirizzo(), "APJ6JRA9NG5V4")
    items = client.post.call_args.kwargs["json_body"]["items"]
    assert items[0]["expiration"] == "2028-03-14"
    assert "expiration" not in items[1]
