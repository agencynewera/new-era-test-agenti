from unittest.mock import Mock
from letto import leggi, StatoAttuale


def test_leggi_restituisce_attributi_per_intero():
    client = Mock()
    client.get.return_value = {
        "sku": "ABC-1",
        "summaries": [{"productType": "EXERCISE_MAT", "asin": "B000TEST01"}],
        "attributes": {
            "item_name": [{"value": "Tappeto yoga", "marketplace_id": "APJ6JRA9NG5V4"}],
            "bullet_point": [
                {"value": "Antiscivolo", "marketplace_id": "APJ6JRA9NG5V4"},
                {"value": "Ecologico", "marketplace_id": "APJ6JRA9NG5V4"},
            ],
        },
    }
    stato = leggi(client, "SELLER1", "APJ6JRA9NG5V4", "ABC-1")
    assert stato == StatoAttuale(
        sku="ABC-1", product_type="EXERCISE_MAT",
        attributi=client.get.return_value["attributes"],
    )
    client.get.assert_called_once_with(
        "/listings/2021-08-01/items/SELLER1/ABC-1",
        params={"marketplaceIds": "APJ6JRA9NG5V4", "includedData": "attributes,issues,summaries"},
    )


def test_leggi_senza_summaries_lascia_product_type_vuoto():
    client = Mock()
    client.get.return_value = {"sku": "ABC-1", "attributes": {}}
    stato = leggi(client, "SELLER1", "APJ6JRA9NG5V4", "ABC-1")
    assert stato.product_type == ""


def test_leggi_codifica_la_barra_nello_sku():
    # MA1/4SX-R (Tecno Cold, 2026-09-03): lo SKU grezzo spezzava il path in due
    # segmenti e Amazon rispondeva 403. Deve partire come %2F.
    client = Mock()
    client.get.return_value = {"sku": "MA1/4SX-R", "attributes": {}}
    leggi(client, "SELLER1", "APJ6JRA9NG5V4", "MA1/4SX-R")
    assert client.get.call_args.args[0] == "/listings/2021-08-01/items/SELLER1/MA1%2F4SX-R"
