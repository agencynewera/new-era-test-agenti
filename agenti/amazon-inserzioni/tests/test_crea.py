import os
from unittest.mock import Mock, patch
import pytest
from modello import Prodotto
from spapi_client import SpApiError
from crea import costruisci_payload, pubblica_uno, pubblica_lotto, EsitoCreazione


def _prodotto(**override):
    dati = dict(sku="ABC-1", title="Tappeto yoga", price=29.9, quantity=10,
                product_type="EXERCISE_MAT", brand="New Era Test",
                description="Un tappeto", bullet_points=["Antiscivolo", "Ecologico"],
                images=["https://x/1.jpg", "https://x/2.jpg"], ean="8001234567890",
                attributes={"colore": [{"value": "Blu", "marketplace_id": "APJ6JRA9NG5V4"}]})
    dati.update(override)
    return Prodotto(**dati)


def test_costruisci_payload_non_emette_campi_vuoti():
    payload = costruisci_payload(_prodotto(brand="", description=""), "APJ6JRA9NG5V4")
    assert "brand" not in payload["attributes"]
    assert "product_description" not in payload["attributes"]
    assert payload["productType"] == "EXERCISE_MAT"


def test_costruisci_payload_bullet_point_come_elenco_di_oggetti():
    payload = costruisci_payload(_prodotto(), "APJ6JRA9NG5V4")
    bp = payload["attributes"]["bullet_point"]
    assert bp == [
        {"value": "Antiscivolo", "marketplace_id": "APJ6JRA9NG5V4"},
        {"value": "Ecologico", "marketplace_id": "APJ6JRA9NG5V4"},
    ]


def test_costruisci_payload_prezzo_in_purchasable_offer_e_list_price_con_value_with_tax():
    # Verificato dal vivo (smoke 2026-09-02): il prezzo di vendita va in
    # purchasable_offer.our_price; list_price (RRP) vuole value_with_tax, mai
    # "value" (err 99022), e Amazon lo pretende accanto all'offerta.
    payload = costruisci_payload(_prodotto(), "APJ6JRA9NG5V4")
    assert payload["attributes"]["purchasable_offer"] == [{
        "currency": "EUR",
        "marketplace_id": "APJ6JRA9NG5V4",
        "our_price": [{"schedule": [{"value_with_tax": 29.9}]}],
    }]
    assert payload["attributes"]["list_price"] == [{
        "value_with_tax": 29.9, "currency": "EUR", "marketplace_id": "APJ6JRA9NG5V4",
    }]


def test_costruisci_payload_include_gli_attributi_di_categoria():
    payload = costruisci_payload(_prodotto(), "APJ6JRA9NG5V4")
    assert payload["attributes"]["colore"][0]["value"] == "Blu"


def test_costruisci_payload_include_la_quantita():
    payload = costruisci_payload(_prodotto(quantity=10), "APJ6JRA9NG5V4")
    assert payload["attributes"]["fulfillment_availability"] == [
        {"fulfillment_channel_code": "DEFAULT", "quantity": 10}
    ]


def test_costruisci_payload_include_la_condizione():
    payload = costruisci_payload(_prodotto(), "APJ6JRA9NG5V4")
    assert payload["attributes"]["condition_type"] == [
        {"value": "new_new", "marketplace_id": "APJ6JRA9NG5V4"}
    ]


def test_pubblica_uno_passa_lo_stato_e_gli_issues():
    client = Mock()
    client.put.return_value = {"sku": "ABC-1", "status": "ACCEPTED", "issues": [
        {"code": "W1", "message": "manca il bullet 5", "severity": "WARNING"}
    ]}
    esito = pubblica_uno(client, "SELLER1", "APJ6JRA9NG5V4", _prodotto())
    assert esito == EsitoCreazione("ABC-1", "ACCEPTED", [
        {"code": "W1", "message": "manca il bullet 5", "severity": "WARNING"}
    ])
    chiamata = client.put.call_args
    assert chiamata.args[0] == "/listings/2021-08-01/items/SELLER1/ABC-1"
    assert chiamata.kwargs["params"] == {"marketplaceIds": "APJ6JRA9NG5V4"}


# --- sku padre di una relazione di variazione (senza_offerta) ---


def test_costruisci_payload_senza_offerta_non_ha_prezzo_quantita_condizione():
    payload = costruisci_payload(_prodotto(price=0.0, quantity=0), "APJ6JRA9NG5V4", senza_offerta=True)
    assert "purchasable_offer" not in payload["attributes"]
    assert "fulfillment_availability" not in payload["attributes"]
    assert "condition_type" not in payload["attributes"]
    assert payload["requirements"] == "LISTING_PRODUCT_ONLY"


def test_costruisci_payload_senza_offerta_include_comunque_titolo_e_attributi_di_categoria():
    padre = _prodotto(
        price=0.0, quantity=0,
        attributes={
            "parentage_level": [{"value": "parent", "marketplace_id": "APJ6JRA9NG5V4"}],
            "variation_theme": [{"name": "SIZE"}],
        },
    )
    payload = costruisci_payload(padre, "APJ6JRA9NG5V4", senza_offerta=True)
    assert payload["attributes"]["item_name"][0]["value"] == "Tappeto yoga"
    assert payload["attributes"]["parentage_level"][0]["value"] == "parent"
    assert payload["attributes"]["variation_theme"] == [{"name": "SIZE"}]


def test_costruisci_payload_senza_senza_offerta_resta_come_prima():
    payload = costruisci_payload(_prodotto(), "APJ6JRA9NG5V4")
    assert payload["requirements"] == "LISTING"
    assert "fulfillment_availability" in payload["attributes"]


def test_pubblica_uno_senza_offerta_passa_il_flag_a_costruisci_payload():
    client = Mock()
    client.put.return_value = {"sku": "PARENT-1", "status": "ACCEPTED", "issues": []}
    pubblica_uno(client, "SELLER1", "APJ6JRA9NG5V4", _prodotto(sku="PARENT-1", price=0.0, quantity=0),
                 senza_offerta=True)
    payload_inviato = client.put.call_args.kwargs["json_body"]
    assert payload_inviato["requirements"] == "LISTING_PRODUCT_ONLY"
    assert "fulfillment_availability" not in payload_inviato["attributes"]


def test_pubblica_lotto_non_si_ferma_su_un_fallimento(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.put.side_effect = [
        {"sku": "A", "status": "ACCEPTED", "issues": []},
        SpApiError("500 su .../B"),
        {"sku": "C", "status": "ACCEPTED", "issues": []},
    ]
    prodotti = [_prodotto(sku="A"), _prodotto(sku="B"), _prodotto(sku="C")]
    esiti = pubblica_lotto(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire", prodotti)
    assert len(esiti) == 3
    assert esiti[0].stato == "ACCEPTED"
    assert esiti[1].stato == "errore"
    assert esiti[2].stato == "ACCEPTED"


# --- forma dell'identificativo e anteprima di validazione (smoke 2026-09-01/02) ---


def test_costruisci_payload_ean_col_tipo_minuscolo_dello_schema():
    # L'enum dello schema è ["ean","gtin","minsan_code","upc"]: "EAN" maiuscolo
    # fa fallire il matching GTIN e fa scattare merchant_suggested_asin.
    payload = costruisci_payload(_prodotto(), "APJ6JRA9NG5V4")
    assert payload["attributes"]["externally_assigned_product_identifier"] == [
        {"type": "ean", "value": "8001234567890", "marketplace_id": "APJ6JRA9NG5V4"}
    ]


def test_pubblica_uno_in_anteprima_chiede_validation_preview_e_non_scrive():
    client = Mock()
    client.put.return_value = {"sku": "ABC-1", "status": "VALID", "issues": []}
    esito = pubblica_uno(client, "SELLER1", "APJ6JRA9NG5V4", _prodotto(), anteprima=True)
    assert esito.stato == "VALID"
    chiamata = client.put.call_args
    assert chiamata.kwargs["params"] == {"marketplaceIds": "APJ6JRA9NG5V4", "mode": "VALIDATION_PREVIEW"}


def test_pubblica_uno_senza_anteprima_non_manda_mode():
    client = Mock()
    client.put.return_value = {"sku": "ABC-1", "status": "ACCEPTED", "issues": []}
    pubblica_uno(client, "SELLER1", "APJ6JRA9NG5V4", _prodotto())
    assert "mode" not in client.put.call_args.kwargs["params"]


def test_pubblica_uno_codifica_la_barra_nello_sku():
    # Sulla PUT di creazione la stessa barra dava 403 (Tecno Cold, 2026-09-03).
    client = Mock()
    client.put.return_value = {"sku": "MA1/4SX-R", "status": "ACCEPTED", "issues": []}
    pubblica_uno(client, "SELLER1", "APJ6JRA9NG5V4", _prodotto(sku="MA1/4SX-R"))
    assert client.put.call_args.args[0] == "/listings/2021-08-01/items/SELLER1/MA1%2F4SX-R"
