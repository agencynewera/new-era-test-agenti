# tests/test_campi.py
# Il dizionario dei campi in italiano: da "prezzo 13,90" all'attributo Amazon
# COMPLETO da scrivere. Ogni test modella una forma vista dal vivo (Phy to Pet
# e Bedshire, 2026-09-03), non una forma inventata.
import pytest
from letto import StatoAttuale
import campi

IT = "APJ6JRA9NG5V4"
DE = "A1PA6795UKMFR9"


def _stato(**attributi):
    return StatoAttuale(sku="SKU-1", product_type="PET_FOOD", attributi=attributi)


# ------------------------------------------------------------- prezzo ----

def test_prezzo_sostituisce_il_valore_e_conserva_il_resto_dell_offerta():
    stato = _stato(purchasable_offer=[{"currency": "EUR", "audience": "ALL",
                                       "our_price": [{"schedule": [{"value_with_tax": 13.99}]}],
                                       "marketplace_id": IT}])
    attributo, dopo = campi.proponi("prezzo", "13,90", stato, IT)
    assert attributo == "purchasable_offer"
    assert dopo == [{"currency": "EUR", "audience": "ALL",
                     "our_price": [{"schedule": [{"value_with_tax": 13.9}]}],
                     "marketplace_id": IT}]


def test_prezzo_su_inserzione_senza_offerta_costruisce_l_offerta_intera():
    # Gli snack Phy to Pet creati il 01/09 erano Incomplete proprio per questo:
    # list_price c'era, purchasable_offer no.
    stato = _stato(list_price=[{"value_with_tax": 13.9, "currency": "EUR", "marketplace_id": IT}])
    attributo, dopo = campi.proponi("prezzo", 13.9, stato, IT)
    assert dopo == [{"currency": "EUR", "audience": "ALL",
                     "our_price": [{"schedule": [{"value_with_tax": 13.9}]}],
                     "marketplace_id": IT}]


def test_prezzo_su_offerta_scheletro_senza_our_price_la_completa():
    # Forma vista dal vivo su Tecno Cold DE (2026-09-04): alla creazione Amazon
    # accetta l'inserzione ma scarta il prezzo e lascia purchasable_offer con la
    # sola audience. Senza our_price l'inserzione resta Incomplete.
    stato = _stato(purchasable_offer=[{"audience": "ALL", "marketplace_id": DE}],
                   list_price=[{"value_with_tax": 39.9, "currency": "EUR", "marketplace_id": DE}])
    attributo, dopo = campi.proponi("prezzo", "39,90", stato, DE)
    assert attributo == "purchasable_offer"
    assert dopo == [{"audience": "ALL", "marketplace_id": DE, "currency": "EUR",
                     "our_price": [{"schedule": [{"value_with_tax": 39.9}]}]}]


def test_prezzo_non_numerico_alza_valueerror():
    with pytest.raises(ValueError, match="prezzo"):
        campi.proponi("prezzo", "tredici", _stato(), IT)


def test_prezzo_listino_va_in_list_price_con_value_with_tax():
    # Mai list_price.value: errore 99022 (smoke 2026-09-02).
    stato = _stato(list_price=[{"value_with_tax": 13.9, "currency": "EUR", "marketplace_id": IT}])
    attributo, dopo = campi.proponi("prezzo_listino", "15,90", stato, IT)
    assert attributo == "list_price"
    assert dopo == [{"value_with_tax": 15.9, "currency": "EUR", "marketplace_id": IT}]


# ----------------------------------------------------------- quantita ----

def test_quantita_tocca_solo_l_elemento_default_anche_se_non_e_il_primo():
    # Trappola Bedshire 03/09: fulfillment_availability ha un elemento per
    # canale e il DEFAULT (FBM) non e' sempre il primo. Le righe FBA restano.
    fa = [{"fulfillment_channel_code": "AMAZON_EU"},
          {"fulfillment_channel_code": "DEFAULT", "quantity": 1000, "lead_time_to_ship_max_days": 6}]
    attributo, dopo = campi.proponi("quantita", "250", _stato(fulfillment_availability=fa), IT)
    assert attributo == "fulfillment_availability"
    assert dopo == [{"fulfillment_channel_code": "AMAZON_EU"},
                    {"fulfillment_channel_code": "DEFAULT", "quantity": 250, "lead_time_to_ship_max_days": 6}]


def test_quantita_senza_elemento_default_lo_aggiunge_in_coda():
    fa = [{"fulfillment_channel_code": "AMAZON_EU"}]
    _, dopo = campi.proponi("quantita", 10, _stato(fulfillment_availability=fa), IT)
    assert dopo == [{"fulfillment_channel_code": "AMAZON_EU"},
                    {"fulfillment_channel_code": "DEFAULT", "quantity": 10}]


def test_quantita_negativa_o_decimale_alza_valueerror():
    with pytest.raises(ValueError, match="quantita"):
        campi.proponi("quantita", "-3", _stato(), IT)
    with pytest.raises(ValueError, match="quantita"):
        campi.proponi("quantita", "2,5", _stato(), IT)


def test_tempo_di_gestione_scrive_lead_time_sull_elemento_default():
    fa = [{"fulfillment_channel_code": "DEFAULT", "quantity": 1000, "lead_time_to_ship_max_days": 6}]
    _, dopo = campi.proponi("tempo_di_gestione", "2", _stato(fulfillment_availability=fa), IT)
    assert dopo == [{"fulfillment_channel_code": "DEFAULT", "quantity": 1000, "lead_time_to_ship_max_days": 2}]


# ------------------------------------------------------------- testi ----

def test_titolo_conserva_language_tag_e_marketplace_dell_elemento_vivo():
    stato = _stato(item_name=[{"value": "Vecchio", "language_tag": "it_IT", "marketplace_id": IT}])
    attributo, dopo = campi.proponi("titolo", "Nuovo titolo", stato, IT)
    assert attributo == "item_name"
    assert dopo == [{"value": "Nuovo titolo", "language_tag": "it_IT", "marketplace_id": IT}]


def test_titolo_senza_elemento_vivo_deduce_la_lingua_dal_marketplace():
    _, dopo = campi.proponi("titolo", "Nuovo", _stato(), "A1PA6795UKMFR9")  # DE
    assert dopo == [{"value": "Nuovo", "language_tag": "de_DE", "marketplace_id": "A1PA6795UKMFR9"}]


def test_titolo_su_marketplace_sconosciuto_senza_elemento_vivo_alza_valueerror():
    # Mai indovinare la lingua: meglio fermarsi che scrivere un language_tag a caso.
    with pytest.raises(ValueError, match="lingua"):
        campi.proponi("titolo", "Nuovo", _stato(), "MARKETPLACE_IGNOTO")


def test_colore_scrive_l_attributo_color_conservando_la_lingua():
    stato = _stato(color=[{"value": "Nero", "language_tag": "it_IT", "marketplace_id": IT}])
    attributo, dopo = campi.proponi("Colore", "Rosso-Nero", stato, IT)
    assert attributo == "color"
    assert dopo == [{"value": "Rosso-Nero", "language_tag": "it_IT", "marketplace_id": IT}]
    assert campi.valore_attuale("colore", stato) == "Nero"


def test_titolo_vuoto_alza_valueerror():
    with pytest.raises(ValueError, match="titolo"):
        campi.proponi("titolo", "   ", _stato(), IT)


def test_bullet_riscrive_l_array_intero_un_elemento_per_punto():
    stato = _stato(bullet_point=[{"value": "A", "language_tag": "it_IT", "marketplace_id": IT}])
    attributo, dopo = campi.proponi("bullet", ["Primo", "Secondo"], stato, IT)
    assert attributo == "bullet_point"
    assert dopo == [{"value": "Primo", "language_tag": "it_IT", "marketplace_id": IT},
                    {"value": "Secondo", "language_tag": "it_IT", "marketplace_id": IT}]


def test_bullet_da_stringa_si_divide_sulla_barra_verticale():
    # E' la forma con cui i bullet entrano da una cella di tabella.
    _, dopo = campi.proponi("bullet", "Primo | Secondo|Terzo", _stato(), IT)
    assert [b["value"] for b in dopo] == ["Primo", "Secondo", "Terzo"]


def test_descrizione_e_parole_chiave_sono_testi_a_elemento_singolo():
    _, d = campi.proponi("descrizione", "Testo lungo", _stato(), IT)
    assert d == [{"value": "Testo lungo", "language_tag": "it_IT", "marketplace_id": IT}]
    attributo, k = campi.proponi("parole_chiave", "snack cane gatto", _stato(), IT)
    assert attributo == "generic_keyword"
    assert k == [{"value": "snack cane gatto", "language_tag": "it_IT", "marketplace_id": IT}]


# ----------------------------------------------------------- immagini ----

def test_immagine_principale_e_slot_secondari_usano_media_location():
    # Forma verificata dal vivo sui 49 slot di Phy to Pet (03/09).
    url = "https://agencynewera.it/amz-tmp/x-1.jpg"
    attributo, dopo = campi.proponi("immagine_principale", url, _stato(), IT)
    assert attributo == "main_product_image_locator"
    assert dopo == [{"media_location": url, "marketplace_id": IT}]
    attributo2, _ = campi.proponi("immagine_2", url, _stato(), IT)
    assert attributo2 == "other_product_image_locator_1"
    attributo8, _ = campi.proponi("immagine_8", url, _stato(), IT)
    assert attributo8 == "other_product_image_locator_7"
    attributo9, _ = campi.proponi("immagine_9", url, _stato(), IT)
    assert attributo9 == "other_product_image_locator_8"


def test_immagine_campione_usa_swatch_product_image_locator():
    # La miniatura del colore nella pagina viene da qui (Rivextauto Zenit, 24/10).
    url = "https://agencynewera.it/amz-tmp/x-swch.jpg"
    attributo, dopo = campi.proponi("immagine campione", url, _stato(), IT)
    assert attributo == "swatch_product_image_locator"
    assert dopo == [{"media_location": url, "marketplace_id": IT}]
    stato = _stato(swatch_product_image_locator=[{"media_location": "https://x/s.jpg"}])
    assert campi.valore_attuale("immagine_campione", stato) == "https://x/s.jpg"


def test_immagine_senza_url_http_alza_valueerror():
    # La Listings API accetta solo URL pubblici, mai un percorso su disco.
    with pytest.raises(ValueError, match="URL"):
        campi.proponi("immagine_principale", "/Users/x/foto.jpg", _stato(), IT)


# --------------------------------------------------------------- GPSR ----

def test_gpsr_produttore_e_responsabile_ue_nella_forma_letta_dal_vivo():
    # Forma vista sugli SKU GERMISTOP di ECM (01/11/2026): e-mail o URL.
    attributo, dopo = campi.proponi("gpsr produttore", "cliente@example.com", _stato(), IT)
    assert attributo == "gpsr_manufacturer_reference"
    assert dopo == [{"gpsr_manufacturer_email_address": "cliente@example.com", "marketplace_id": IT}]
    attributo, dopo = campi.proponi("gpsr responsabile ue", "www.cerichem.com", _stato(), IT)
    assert attributo == "dsa_responsible_party_address"
    assert dopo == [{"value": "www.cerichem.com", "marketplace_id": IT}]
    stato = _stato(dsa_responsible_party_address=[{"value": "cliente@example.com", "marketplace_id": IT}],
                   gpsr_manufacturer_reference=[{"gpsr_manufacturer_email_address": "a@b.it"}])
    assert campi.valore_attuale("gpsr_responsabile_ue", stato) == "cliente@example.com"
    assert campi.valore_attuale("gpsr_produttore", stato) == "a@b.it"


def test_gpsr_contatto_che_non_e_ne_email_ne_url_alza_valueerror():
    with pytest.raises(ValueError, match="e-mail o"):
        campi.proponi("gpsr_produttore", "European Home Shopping", _stato(), IT)


def test_gpsr_attestazione_accetta_si_no_e_scrive_un_booleano():
    attributo, dopo = campi.proponi("gpsr attestazione", "No", _stato(), IT)
    assert attributo == "gpsr_safety_attestation"
    assert dopo == [{"value": False, "marketplace_id": IT}]
    assert campi.proponi("gpsr_attestazione", "sì", _stato(), IT)[1][0]["value"] is True
    with pytest.raises(ValueError):
        campi.proponi("gpsr_attestazione", "forse", _stato(), IT)


def test_avvertenze_scrivono_safety_warning_con_lingua():
    attributo, dopo = campi.proponi("avvertenze", "Non ingerire.", _stato(), IT)
    assert attributo == "safety_warning"
    assert dopo == [{"value": "Non ingerire.", "language_tag": "it_IT", "marketplace_id": IT}]


# ------------------------------------------------------------ nomi ----

def test_campo_sconosciuto_alza_valueerror_con_l_elenco_dei_nomi_validi():
    with pytest.raises(ValueError) as e:
        campi.proponi("materiale", "lana", _stato(), IT)
    assert "colore" in str(e.value) and "prezzo" in str(e.value) and "quantita" in str(e.value)


def test_nomi_accettano_accenti_maiuscole_e_spazi():
    # "Quantità", "tempo di gestione", "PREZZO" sono tutti nomi leciti in chat.
    assert campi.normalizza_nome("Quantità") == "quantita"
    assert campi.normalizza_nome("tempo di gestione") == "tempo_di_gestione"
    assert campi.normalizza_nome("PREZZO") == "prezzo"


# ---------------------------------------------------- valore_attuale ----
# L'inverso di proponi: da un'inserzione viva al valore del campo in italiano.

def test_valore_attuale_prezzo_quantita_tempo_dal_vivo():
    stato = _stato(purchasable_offer=[{"currency": "EUR", "our_price": [{"schedule": [{"value_with_tax": 13.99}]}]}],
                   list_price=[{"value_with_tax": 13.9, "currency": "EUR"}],
                   fulfillment_availability=[{"fulfillment_channel_code": "AMAZON_EU"},
                                             {"fulfillment_channel_code": "DEFAULT", "quantity": 100, "lead_time_to_ship_max_days": 2}])
    assert campi.valore_attuale("prezzo", stato) == 13.99
    assert campi.valore_attuale("prezzo listino", stato) == 13.9
    assert campi.valore_attuale("Quantità", stato) == 100
    assert campi.valore_attuale("tempo di gestione", stato) == 2


def test_valore_attuale_testi_bullet_e_immagini():
    stato = _stato(item_name=[{"value": "Titolo", "language_tag": "it_IT"}],
                   bullet_point=[{"value": "A"}, {"value": "B"}],
                   main_product_image_locator=[{"media_location": "https://x/1.jpg"}],
                   other_product_image_locator_1=[{"media_location": "https://x/2.jpg"}])
    assert campi.valore_attuale("titolo", stato) == "Titolo"
    assert campi.valore_attuale("bullet", stato) == ["A", "B"]
    assert campi.valore_attuale("immagine_principale", stato) == "https://x/1.jpg"
    assert campi.valore_attuale("immagine_2", stato) == "https://x/2.jpg"


def test_valore_attuale_assente_e_none_e_campo_sconosciuto_alza():
    assert campi.valore_attuale("prezzo", _stato()) is None
    assert campi.valore_attuale("tempo di gestione", _stato(fulfillment_availability=[{"fulfillment_channel_code": "DEFAULT", "quantity": 5}])) is None
    with pytest.raises(ValueError, match="sconosciuto"):
        campi.valore_attuale("materiale", _stato())


def test_interpreta_normalizza_il_valore_come_proponi():
    # Serve ai filtri "dove": "13,90" deve confrontarsi con 13.9, non con la stringa.
    assert campi.interpreta("prezzo", "13,90") == 13.9
    assert campi.interpreta("quantita", "5") == 5
    assert campi.interpreta("bullet", "A | B") == ["A", "B"]


# ------------------------------------------------ misure e peso confezione ----
# Senza, createInboundPlan FBA manda il piano in errore ("dimensions/weight need
# to be provided in the manufacturer's original packaging", Galeone 18/09/2026).
# Unità dall'enum dello schema UNDERPANTS: centimeters, kilograms.

def test_misure_confezione_da_testo_lxpxa_in_centimetri():
    attributo, dopo = campi.proponi("misure confezione", "21x8x8", _stato(), IT)
    assert attributo == "item_package_dimensions"
    assert dopo == [{"length": {"unit": "centimeters", "value": 21.0},
                     "width": {"unit": "centimeters", "value": 8.0},
                     "height": {"unit": "centimeters", "value": 8.0},
                     "marketplace_id": IT}]


def test_misure_confezione_accettano_virgola_spazi_e_per():
    assert campi.interpreta("misure_confezione", "21,5 × 8 X 8") == (21.5, 8.0, 8.0)


def test_misure_confezione_senza_tre_numeri_positivi_alza_valueerror():
    for sbagliato in ("21x8", "21x8x0", "21xAx8"):
        with pytest.raises(ValueError, match="misure_confezione"):
            campi.interpreta("misure_confezione", sbagliato)


def test_peso_confezione_in_chilogrammi():
    attributo, dopo = campi.proponi("peso confezione", "0,2", _stato(), IT)
    assert attributo == "item_package_weight"
    assert dopo == [{"unit": "kilograms", "value": 0.2, "marketplace_id": IT}]


def test_peso_confezione_zero_o_negativo_alza_valueerror():
    for sbagliato in ("0", "-1"):
        with pytest.raises(ValueError, match="peso_confezione"):
            campi.interpreta("peso_confezione", sbagliato)


def test_valore_attuale_misure_e_peso_nella_forma_letta_dal_vivo():
    # Forma di SEMPLICI-ASSORTITI-M-FBA (Galeone, 18/09): valori stringa, unità "CM".
    stato = _stato(item_package_dimensions=[{"height": {"unit": "CM", "value": "8.0"},
                                             "length": {"unit": "CM", "value": "21.0"},
                                             "width": {"unit": "CM", "value": "8.0"}}],
                   item_package_weight=[{"unit": "kilograms", "value": "0.2"}])
    assert campi.valore_attuale("misure confezione", stato) == (21.0, 8.0, 8.0)
    assert campi.valore_attuale("peso confezione", stato) == 0.2
    assert campi.valore_attuale("misure confezione", _stato()) is None
    assert campi.valore_attuale("peso confezione", _stato()) is None
