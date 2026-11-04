"""Creazione di una scheda su un altro marketplace a partire dagli attributi
del listing di origine (Tecno Cold IT -> FR/ES, 23/09/2026). Tutto su Mock."""
from unittest.mock import Mock, patch
import pytest
from spapi_client import SpApiError
from crea import EsitoCreazione
from scheda_estera import (trasla, costruisci_scheda, pubblica_scheda, pubblica_lotto_schede,
                           ESCLUSI_SEMPRE)

IT, FR = "APJ6JRA9NG5V4", "A13V1IB3VIYZZH"


def _attributi_it():
    return {
        "item_name": [{"value": "Bombola", "language_tag": "it_IT", "marketplace_id": IT}],
        "bullet_point": [{"value": "UNO", "language_tag": "it_IT", "marketplace_id": IT},
                         {"value": "DUE", "language_tag": "it_IT", "marketplace_id": IT}],
        "size": [{"value": "370g - 2 bombole", "language_tag": "it_IT", "marketplace_id": IT}],
        "brand": [{"value": "Tecno Cold", "language_tag": "it_IT", "marketplace_id": IT}],
        "purchasable_offer": [{"currency": "EUR", "marketplace_id": IT, "audience": "ALL",
                               "our_price": [{"schedule": [{"value_with_tax": 78.5}]}]},
                              {"currency": "EUR", "marketplace_id": IT, "audience": "B2B",
                               "our_price": [{"schedule": [{"value_with_tax": 70.0}]}]}],
        "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": 10}],
        "recommended_browse_nodes": [{"value": "14567327031", "marketplace_id": IT}],
        "eprel_registration_number": [{"value": "000000000", "marketplace_id": IT}],
        "merchant_shipping_group": [{"value": "Italia", "marketplace_id": IT}],
        "parentage_level": [{"value": "child", "marketplace_id": IT}],
        "child_parent_sku_relationship": [{"child_relationship_type": "variation", "parent_sku": "R290_PARENT", "marketplace_id": IT}],
        "variation_theme": [{"name": "SIZE/COLOR", "marketplace_id": IT}],
        "main_product_image_locator": [{"media_location": "https://m.media-amazon.com/x.jpg", "marketplace_id": IT}],
        "externally_assigned_product_identifier": [{"type": "ean", "value": "8057998790347", "marketplace_id": IT}],
    }


def test_trasla_cambia_marketplace_e_lingua_ovunque_senza_toccare_i_valori():
    out = trasla(_attributi_it(), FR, "fr_FR")
    assert out["item_name"] == [{"value": "Bombola", "language_tag": "fr_FR", "marketplace_id": FR}]
    assert out["purchasable_offer"][0]["marketplace_id"] == FR
    assert out["main_product_image_locator"][0]["media_location"] == "https://m.media-amazon.com/x.jpg"
    assert _attributi_it()["item_name"][0]["marketplace_id"] == IT  # l'originale non cambia


def test_costruisci_scheda_figlio_sostituisce_i_testi_esclude_il_non_portabile_e_aggancia():
    testi = {"item_name": ["Bouteille"], "bullet_point": ["UN", "DEUX", "TROIS"], "size": ["370 g - 2 bouteilles"]}
    p = costruisci_scheda(_attributi_it(), "REFRIGERATION_APPLIANCE", FR, "fr_FR", testi,
                          asin_suggerito="B0GLQSLSYS", parent_sku="R290_PARENT", tema="SIZE/COLOR")
    a = p["attributes"]
    assert p["productType"] == "REFRIGERATION_APPLIANCE" and p["requirements"] == "LISTING"
    assert a["item_name"] == [{"value": "Bouteille", "language_tag": "fr_FR", "marketplace_id": FR}]
    assert [b["value"] for b in a["bullet_point"]] == ["UN", "DEUX", "TROIS"]
    assert a["size"] == [{"value": "370 g - 2 bouteilles", "language_tag": "fr_FR", "marketplace_id": FR}]
    assert a["brand"][0]["language_tag"] == "fr_FR"
    for k in ESCLUSI_SEMPRE:
        assert k not in a
    assert "recommended_browse_nodes" not in a and "eprel_registration_number" not in a
    assert a["merchant_suggested_asin"] == [{"value": "B0GLQSLSYS", "marketplace_id": FR}]
    assert a["parentage_level"] == [{"value": "child", "marketplace_id": FR}]
    assert a["child_parent_sku_relationship"] == [{"child_relationship_type": "variation", "parent_sku": "R290_PARENT", "marketplace_id": FR}]
    assert a["variation_theme"] == [{"name": "SIZE/COLOR", "marketplace_id": FR}]
    # l'offerta: solo quella per tutti, prezzo e disponibilita' di origine
    assert a["purchasable_offer"] == [{"currency": "EUR", "marketplace_id": FR, "audience": "ALL",
                                       "our_price": [{"schedule": [{"value_with_tax": 78.5}]}]}]
    assert a["fulfillment_availability"] == [{"fulfillment_channel_code": "DEFAULT", "quantity": 10}]
    assert a["externally_assigned_product_identifier"][0]["value"] == "8057998790347"


def test_costruisci_scheda_padre_senza_offerta_e_senza_asin_suggerito():
    p = costruisci_scheda(_attributi_it(), "REFRIGERATION_APPLIANCE", FR, "fr_FR",
                          {"item_name": ["Bouteille"], "bullet_point": ["UN"], "size": ["s"]},
                          padre=True, tema="SIZE/COLOR")
    a = p["attributes"]
    assert p["requirements"] == "LISTING_PRODUCT_ONLY"
    for k in ("purchasable_offer", "fulfillment_availability", "merchant_suggested_asin",
              "child_parent_sku_relationship", "externally_assigned_product_identifier", "condition_type"):
        assert k not in a
    assert a["parentage_level"] == [{"value": "parent", "marketplace_id": FR}]
    assert a["variation_theme"] == [{"name": "SIZE/COLOR", "marketplace_id": FR}]


def test_costruisci_scheda_rifiuta_testo_mancante_per_un_campo_di_testo_presente():
    # item_name c'e' su IT: senza traduzione non si pubblica un titolo italiano su FR.
    with pytest.raises(ValueError, match="item_name"):
        costruisci_scheda(_attributi_it(), "REFRIGERATION_APPLIANCE", FR, "fr_FR",
                          {"bullet_point": ["UN"]}, asin_suggerito="B0X", parent_sku="P", tema="SIZE/COLOR")


def test_costruisci_scheda_figlio_senza_asin_suggerito_o_senza_padre_e_un_errore():
    with pytest.raises(ValueError):
        costruisci_scheda(_attributi_it(), "REFRIGERATION_APPLIANCE", FR, "fr_FR",
                          {"item_name": ["B"], "bullet_point": ["U"], "size": ["s"]}, parent_sku="P", tema="SIZE/COLOR")
    with pytest.raises(ValueError):
        costruisci_scheda(_attributi_it(), "REFRIGERATION_APPLIANCE", FR, "fr_FR",
                          {"item_name": ["B"], "bullet_point": ["U"], "size": ["s"]}, asin_suggerito="B0X", tema="SIZE/COLOR")


def test_pubblica_scheda_anteprima_e_lotto_registrano_come_le_creazioni():
    client = Mock()
    client.put.return_value = {"sku": "R290-2", "status": "VALID", "issues": []}
    payload = {"productType": "REFRIGERATION_APPLIANCE", "requirements": "LISTING", "attributes": {}}
    with patch("scheda_estera.registro") as reg:
        e = pubblica_scheda(client, "SELLER1", FR, "R290-2", payload, anteprima=True)
    assert e == EsitoCreazione("R290-2", "VALID", [])
    assert client.put.call_args.kwargs["params"] == {"marketplaceIds": FR, "mode": "VALIDATION_PREVIEW"}
    reg.registra_tentativo_creazione.assert_not_called()

    client.put.side_effect = [SpApiError("timeout"), {"sku": "R290-3", "status": "ACCEPTED", "issues": []}]
    with patch("scheda_estera.registro") as reg:
        reg.registra_tentativo_creazione.side_effect = [0, 1]
        esiti = pubblica_lotto_schede(client, "SELLER1", FR, "Tecno Cold",
                                      [("R290-2", payload), ("R290-3", payload)])
    assert [x.stato for x in esiti] == ["errore", "ACCEPTED"]
    assert reg.registra_esito_creazione.call_args_list[0].args[2] == "errore"
    assert reg.registra_esito_creazione.call_args_list[1].args[2] == "ACCEPTED"


def test_patch_ops_una_replace_per_attributo_nell_ordine_dato_e_mai_un_valore_vuoto():
    from scheda_estera import patch_ops
    ops = patch_ops({"parentage_level": [{"value": "child", "marketplace_id": FR}],
                     "size": [{"value": "1 kg - 3 bouteilles", "language_tag": "fr_FR", "marketplace_id": FR}]})
    assert ops == [
        {"op": "replace", "path": "/attributes/parentage_level", "value": [{"value": "child", "marketplace_id": FR}]},
        {"op": "replace", "path": "/attributes/size", "value": [{"value": "1 kg - 3 bouteilles", "language_tag": "fr_FR", "marketplace_id": FR}]},
    ]
    with pytest.raises(ValueError):
        patch_ops({"size": []})
