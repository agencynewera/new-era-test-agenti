from unittest.mock import patch
from letto import StatoAttuale
from unifica import pianifica, esegui


def _figlio(sku, product_type="CO2_CARTRIDGE"):
    return StatoAttuale(sku=sku, product_type=product_type, attributi={})


def test_pianifica_include_padre_e_figli_nuovi():
    stato_padre = StatoAttuale(sku="CO2_NUOVA_PARENT", product_type="CO2_CARTRIDGE", attributi={})
    attributi_padre = {"parentage_level": [{"value": "parent"}],
                        "variation_theme": [{"name": "SIZE"}]}
    stati_figli = {"CO2W-1.2KG-2": _figlio("CO2W-1.2KG-2"), "CO2W-1.2KG-3": _figlio("CO2W-1.2KG-3")}
    attributi_figli = {
        sku: {"parentage_level": [{"value": "child"}],
              "child_parent_sku_relationship": [{"child_relationship_type": "variation",
                                                  "parent_sku": "CO2_NUOVA_PARENT"}]}
        for sku in stati_figli
    }

    piano = pianifica(stato_padre, attributi_padre, stati_figli, attributi_figli)

    assert piano.padre[0] == "CO2_CARTRIDGE"
    assert {m.campo for m in piano.padre[1]} == {"parentage_level", "variation_theme"}
    assert set(piano.figli.keys()) == {"CO2W-1.2KG-2", "CO2W-1.2KG-3"}
    for sku, (product_type, mod) in piano.figli.items():
        assert product_type == "CO2_CARTRIDGE"
        assert {m.campo for m in mod} == {"parentage_level", "child_parent_sku_relationship"}


def test_pianifica_salta_uno_sku_gia_configurato_come_voluto():
    attributi_gia_presenti = {
        "parentage_level": [{"value": "child"}],
        "child_parent_sku_relationship": [{"child_relationship_type": "variation",
                                            "parent_sku": "CO2_NUOVA_PARENT"}],
    }
    stato_gia_fatto = StatoAttuale(sku="CO2W-1.2KG-4", product_type="CO2_CARTRIDGE",
                                    attributi=attributi_gia_presenti)
    piano = pianifica(None, {}, {"CO2W-1.2KG-4": stato_gia_fatto},
                       {"CO2W-1.2KG-4": attributi_gia_presenti})
    assert piano.figli == {}
    assert piano.vuoto()


def test_pianifica_senza_padre_da_scrivere_lo_lascia_a_none():
    piano = pianifica(None, {}, {}, {})
    assert piano.padre is None
    assert piano.vuoto()


def test_pianifica_figlio_senza_attributi_proposti_alza_valueerror():
    stati_figli = {"CO2W-1.2KG-2": _figlio("CO2W-1.2KG-2")}
    try:
        pianifica(None, {}, stati_figli, {})
        assert False, "doveva alzare ValueError"
    except ValueError as e:
        assert "CO2W-1.2KG-2" in str(e)


def test_esegui_scrive_padre_e_ogni_figlio_con_origine_unificazione():
    stato_padre = StatoAttuale(sku="CO2_NUOVA_PARENT", product_type="CO2_CARTRIDGE", attributi={})
    attributi_padre = {"parentage_level": [{"value": "parent"}]}
    stati_figli = {"CO2W-1.2KG-2": _figlio("CO2W-1.2KG-2")}
    attributi_figli = {"CO2W-1.2KG-2": {"parentage_level": [{"value": "child"}]}}
    piano = pianifica(stato_padre, attributi_padre, stati_figli, attributi_figli)

    with patch("unifica.applica") as mock_applica:
        mock_applica.return_value = ("ACCEPTED", [], [])
        esiti = esegui("client-finto", "SELLER1", "A1PA6795UKMFR9", "Tecno Cold", piano,
                        sku_padre="CO2_NUOVA_PARENT")

    assert mock_applica.call_count == 2
    origini = {c.kwargs["origine"] for c in mock_applica.call_args_list}
    assert origini == {"unificazione"}
    skus_chiamati = {c.args[4] for c in mock_applica.call_args_list}
    assert skus_chiamati == {"CO2_NUOVA_PARENT", "CO2W-1.2KG-2"}
    assert esiti == {"CO2_NUOVA_PARENT": ("ACCEPTED", []), "CO2W-1.2KG-2": ("ACCEPTED", [])}


def test_esegui_con_piano_padre_ma_senza_sku_padre_alza_valueerror():
    stato_padre = StatoAttuale(sku="CO2_NUOVA_PARENT", product_type="CO2_CARTRIDGE", attributi={})
    piano = pianifica(stato_padre, {"parentage_level": [{"value": "parent"}]}, {}, {})
    try:
        esegui("client-finto", "SELLER1", "A1PA6795UKMFR9", "Tecno Cold", piano)
        assert False, "doveva alzare ValueError"
    except ValueError as e:
        assert "sku_padre" in str(e)
