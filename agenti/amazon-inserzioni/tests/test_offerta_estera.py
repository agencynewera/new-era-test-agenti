"""Offerta su una scheda gia' a catalogo in un altro marketplace (Tecno Cold
IT -> FR/ES, 2026-09-23). Tutto su Mock: nessuna chiamata vera."""
from unittest.mock import Mock, patch
import pytest
from spapi_client import SpApiError
from crea import EsitoCreazione
from offerta_estera import (RigaOfferta, payload_offerta, riga_da_listing, pianifica,
                            pubblica_offerta, pubblica_lotto_offerte)

FR = "A13V1IB3VIYZZH"


def _riga(**override):
    dati = dict(sku="CO2", product_type="SODA_MAKER", asin="B0G81LQKFW", prezzo=24.8, quantita=100)
    dati.update(override)
    return RigaOfferta(**dati)


def test_payload_offerta_ha_solo_asin_condizione_prezzo_disponibilita():
    # Sonda VALIDATION_PREVIEW del 23/09/2026 su FR ed ES: questa forma e' VALID
    # su 6 product type; list_price in LISTING_OFFER_ONLY viene ignorato (90000900),
    # quindi non si manda. Niente EAN: la scheda la sceglie l'ASIN.
    p = payload_offerta(_riga(), FR)
    assert p["productType"] == "SODA_MAKER"
    assert p["requirements"] == "LISTING_OFFER_ONLY"
    assert p["attributes"] == {
        "merchant_suggested_asin": [{"value": "B0G81LQKFW", "marketplace_id": FR}],
        "condition_type": [{"value": "new_new", "marketplace_id": FR}],
        "purchasable_offer": [{"currency": "EUR", "marketplace_id": FR,
                               "our_price": [{"schedule": [{"value_with_tax": 24.8}]}]}],
        "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": 100}],
    }
    assert "list_price" not in p["attributes"]
    assert "externally_assigned_product_identifier" not in p["attributes"]


@pytest.mark.parametrize("override", [
    dict(asin=""), dict(prezzo=None), dict(prezzo=0), dict(prezzo=-1),
    dict(quantita=None), dict(quantita=-1), dict(product_type=""),
])
def test_payload_offerta_rifiuta_dati_mancanti_o_assurdi(override):
    with pytest.raises(ValueError):
        payload_offerta(_riga(**override), FR)


def test_riga_da_listing_prende_prezzo_e_quantita_default_dal_listing_di_origine():
    attributi = {
        "purchasable_offer": [{"currency": "EUR", "marketplace_id": "APJ6JRA9NG5V4",
                               "our_price": [{"schedule": [{"value_with_tax": 29.0}]}]}],
        "fulfillment_availability": [{"fulfillment_channel_code": "AMAZON_EU", "quantity": 3},
                                     {"fulfillment_channel_code": "DEFAULT", "quantity": 44}],
    }
    riga = riga_da_listing("FCZOS3H-400-400-48", "HVAC_AIR_FILTER", attributi, "B0G1MQD9ZT")
    assert riga == RigaOfferta("FCZOS3H-400-400-48", "HVAC_AIR_FILTER", "B0G1MQD9ZT", 29.0, 44)


def test_riga_da_listing_senza_prezzo_o_senza_canale_default_non_inventa():
    with pytest.raises(ValueError):
        riga_da_listing("X", "SODA_MAKER", {"fulfillment_availability": [
            {"fulfillment_channel_code": "DEFAULT", "quantity": 1}]}, "B0X")
    with pytest.raises(ValueError):
        riga_da_listing("X", "SODA_MAKER", {"purchasable_offer": [
            {"our_price": [{"schedule": [{"value_with_tax": 9.9}]}]}]}, "B0X")


def _voce(sku, asin, ean="8050000000001", parentage="child", pt="SODA_MAKER", prezzo=10.0, qta=5):
    return {"sku": sku, "asin": asin, "product_type": pt, "parentage": parentage, "ean": ean,
            "attributi": {
                "purchasable_offer": [{"our_price": [{"schedule": [{"value_with_tax": prezzo}]}]}],
                "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": qta}],
            }}


def test_pianifica_stesso_asin_poi_ean_e_salta_padri_doppioni_e_assenti():
    voci = [
        _voce("CO2_PARENT", "B0PADRE", ean=None, parentage="parent"),
        _voce("CO2", "B0STESSO"),                       # stesso ASIN a catalogo
        _voce("CO2-12", "B0IT12", ean="8050000000012"),  # ASIN diverso, trovato per EAN
        _voce("MA19", "B0MA19", ean="8050000000675"),    # EAN doppio: non si sceglie da soli
        _voce("R290-2", "B0R2902", ean="8050000000347"),  # non a catalogo
        _voce("NOPREZZO", "B0STESSO2", prezzo=None),     # a catalogo ma senza prezzo di origine
    ]
    per_asin = {"B0STESSO", "B0STESSO2"}
    per_ean = {"8050000000012": ["B0FR12"], "8050000000675": ["B0CO2W2"]}
    righe, saltati = pianifica(voci, per_asin, per_ean, ean_doppi={"8050000000675"})
    # Scheda trovata per EAN sotto un altro ASIN (B0FR12): con l'ASIN locale
    # Amazon risponde 101077 (uno SKU, un ASIN su tutti i marketplace), con
    # quello di origine pretende il titolo (90220). Non e' un'offerta: lotto 2.
    assert [(r.sku, r.asin) for r in righe] == [("CO2", "B0STESSO")]
    assert saltati == {
        "CO2_PARENT": "padre: si crea a parte, senza offerta",
        "CO2-12": "scheda presente solo sotto un altro ASIN (B0FR12): serve il testo, si crea nel lotto 2",
        "MA19": "EAN usato da piu' prodotti su IT e scheda dell'ASIN assente: si crea nel lotto 2",
        "R290-2": "scheda assente a catalogo: si crea nel lotto 2",
        "NOPREZZO": "prezzo di origine (per tutti i clienti) mancante",
    }


def test_pubblica_offerta_anteprima_passa_mode_validation_preview_e_non_registra():
    client = Mock()
    client.put.return_value = {"sku": "CO2", "status": "VALID", "issues": [{"code": "18616", "severity": "WARNING"}]}
    with patch("offerta_estera.registro") as reg:
        esito = pubblica_offerta(client, "SELLER1", FR, _riga(), anteprima=True)
    assert esito == EsitoCreazione("CO2", "VALID", [{"code": "18616", "severity": "WARNING"}])
    chiamata = client.put.call_args
    assert chiamata.args[0] == "/listings/2021-08-01/items/SELLER1/CO2"
    assert chiamata.kwargs["params"] == {"marketplaceIds": FR, "mode": "VALIDATION_PREVIEW"}
    reg.registra_tentativo_creazione.assert_not_called()


def test_pubblica_offerta_codifica_lo_sku_con_la_barra_come_un_solo_segmento():
    client = Mock()
    client.put.return_value = {"sku": "MA1/4SX-R", "status": "ACCEPTED", "issues": []}
    pubblica_offerta(client, "SELLER1", FR, _riga(sku="MA1/4SX-R"))
    assert client.put.call_args.args[0] == "/listings/2021-08-01/items/SELLER1/MA1%2F4SX-R"


def test_pubblica_lotto_registra_ogni_tentativo_e_continua_dopo_un_errore():
    client = Mock()
    client.put.side_effect = [SpApiError("500 su /listings"), {"sku": "CO2W", "status": "ACCEPTED", "issues": []}]
    with patch("offerta_estera.registro") as reg:
        reg.registra_tentativo_creazione.side_effect = [0, 1]
        esiti = pubblica_lotto_offerte(client, "SELLER1", FR, "Tecno Cold",
                                       [_riga(), _riga(sku="CO2W", product_type="KITCHEN", asin="B0GRWJHH31")])
    assert [e.stato for e in esiti] == ["errore", "ACCEPTED"]
    assert esiti[0].issues == [{"message": "500 su /listings"}]
    assert reg.registra_tentativo_creazione.call_args_list[0].args == ("Tecno Cold", "CO2")
    assert reg.registra_esito_creazione.call_args_list[0].args == ("Tecno Cold", 0, "errore", [{"message": "500 su /listings"}])
    assert reg.registra_esito_creazione.call_args_list[1].args == ("Tecno Cold", 1, "ACCEPTED", [])
    assert client.put.call_count == 2


def test_cerca_per_identificativi_chiede_20_per_pagina_e_segue_il_token():
    # Il 23/09/2026 il default (10 per pagina) ha fatto risultare "assenti" ASIN
    # presenti: 20 identificativi -> 10 risultati e nessuna pagina letta dopo.
    from offerta_estera import cerca_per_identificativi
    client = Mock()
    client.get.side_effect = [
        {"items": [{"asin": "B0A", "identifiers": [], "relationships": []}],
         "pagination": {"nextToken": "T2"}},
        {"items": [{"asin": "B0B", "identifiers": [], "relationships": []}]},
        {"items": [{"asin": "B0C", "identifiers": [], "relationships": []}]},
    ]
    ids = [f"B0{i:08d}" for i in range(25)]
    out = cerca_per_identificativi(client, FR, ids, "ASIN", pausa=0)
    assert set(out) == {"B0A", "B0B", "B0C"}
    p1, p2, p3 = [c.kwargs["params"] for c in client.get.call_args_list]
    assert p1["pageSize"] == 20 and p1["identifiersType"] == "ASIN" and p1["marketplaceIds"] == FR
    assert len(p1["identifiers"].split(",")) == 20 and "pageToken" not in p1
    assert p2["pageToken"] == "T2" and p2["identifiers"] == p1["identifiers"]
    assert len(p3["identifiers"].split(",")) == 5 and "pageToken" not in p3


def test_ean_delle_voci_di_catalogo_mappa_ogni_ean_ai_suoi_asin():
    from offerta_estera import ean_delle_voci
    voci = {
        "B0X": {"identifiers": [{"marketplaceId": FR, "identifiers": [
            {"identifierType": "EAN", "identifier": "8050000000001"}, {"identifierType": "UPC", "identifier": "1"}]}]},
        "B0Y": {"identifiers": [{"marketplaceId": FR, "identifiers": [{"identifierType": "EAN", "identifier": "8050000000001"}]}]},
        "B0Z": {"identifiers": []},
    }
    assert ean_delle_voci(voci) == {"8050000000001": ["B0X", "B0Y"]}


def test_riga_da_listing_prende_il_prezzo_per_tutti_non_quello_b2b():
    # Su IT Tecno Cold ha due purchasable_offer: audience ALL (29,00) e audience
    # B2B (26,10 con sconti quantita'). Il 23/09 la prima versione prendeva
    # l'ultimo valore trovato, cioe' il B2B: il prezzo da copiare e' quello ALL.
    attributi = {
        "purchasable_offer": [
            {"currency": "EUR", "audience": "ALL", "our_price": [{"schedule": [{"value_with_tax": 29.0}]}]},
            {"currency": "EUR", "audience": "B2B", "our_price": [{"schedule": [{"value_with_tax": 26.1}]}],
             "quantity_discount_plan": [{"schedule": [{"discount_type": "percent", "levels": [{"lower_bound": 5, "value": 5.0}]}]}]},
        ],
        "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": 44}],
    }
    assert riga_da_listing("F", "HVAC_AIR_FILTER", attributi, "B0X").prezzo == 29.0
    # invertito l'ordine, stesso risultato
    attributi["purchasable_offer"].reverse()
    assert riga_da_listing("F", "HVAC_AIR_FILTER", attributi, "B0X").prezzo == 29.0


def test_riga_da_listing_con_solo_b2b_non_inventa_un_prezzo_al_pubblico():
    attributi = {
        "purchasable_offer": [{"currency": "EUR", "audience": "B2B", "our_price": [{"schedule": [{"value_with_tax": 26.1}]}]}],
        "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": 1}],
    }
    with pytest.raises(ValueError):
        riga_da_listing("F", "HVAC_AIR_FILTER", attributi, "B0X")


SE = "A2NODRKZP88ZB9"


def test_payload_offerta_usa_la_valuta_del_marketplace_di_destinazione():
    # Tecno Cold IT -> SE (14/10/2026): Amazon.se vende in corone, non in euro.
    p = payload_offerta(_riga(prezzo=277.0), SE)
    assert p["attributes"]["purchasable_offer"][0]["currency"] == "SEK"


def test_payload_offerta_su_marketplace_senza_valuta_nota_si_ferma():
    with pytest.raises(ValueError):
        payload_offerta(_riga(), "XXXXXXXX")


def test_converti_riga_porta_il_prezzo_in_corone_intere_al_cambio_dato():
    from offerta_estera import converti_riga
    r = converti_riga(_riga(prezzo=24.8), 11.1580)
    assert r.prezzo == 277.0        # 24,80 x 11,158 = 276,72 -> 277 kr
    assert r.quantita == 100 and r.asin == "B0G81LQKFW"
