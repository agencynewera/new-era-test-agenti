# tests/test_ean_catalogo.py
from __future__ import annotations
# La mappa dei codici di un cliente Amazon: da uno SKU o da un ASIN al codice
# EAN e allo SKU con cui il prodotto e' registrato su Seller Central. Serve a
# far parlare la stessa lingua a due marketplace dello stesso cliente.
import ean_catalogo as ec


def _riga(sku, ean="", asin="", tipo="prodotto"):
    return {"sku": sku, "ean": ean, "asin": asin, "tipo": tipo}


def test_l_asin_porta_allo_sku_amazon_e_al_suo_ean():
    m = ec.Mappa.da_righe([_riga("05-Y0CN-G67Q", ean="8050506899686", asin="B0C9D6G8WB")])
    v = m.per_asin("B0C9D6G8WB")
    assert v.sku == "05-Y0CN-G67Q"
    assert v.ean == "8050506899686"


def test_lo_sku_amazon_porta_al_suo_ean():
    m = ec.Mappa.da_righe([_riga("TC-VENT-450", ean="8012345678905")])
    assert m.per_sku("TC-VENT-450").ean == "8012345678905"


def test_una_chiave_che_non_c_e_non_restituisce_niente():
    m = ec.Mappa.da_righe([_riga("TC-VENT-450", ean="8012345678905")])
    assert m.per_sku("MAI-VISTO") is None
    assert m.per_asin("B0MAIVISTO") is None


def test_fra_due_sku_sullo_stesso_asin_vince_quello_che_ha_il_codice():
    # Arcipelago registra "104" e "104-FBA" sullo stesso ASIN: uno dei due puo'
    # non portare l'EAN, e sarebbe il peggiore da consegnare.
    righe = [_riga("104-FBA", asin="B0FJSLCF34"),
             _riga("104", ean="8000086001047", asin="B0FJSLCF34")]
    for ordine in (righe, list(reversed(righe))):
        assert ec.Mappa.da_righe(ordine).per_asin("B0FJSLCF34").sku == "104"


def test_a_parita_di_codice_la_scelta_non_dipende_dall_ordine_di_lettura():
    righe = [_riga("104", ean="8000086001047", asin="B0FJSLCF34"),
             _riga("104-FBA", ean="8000086001047", asin="B0FJSLCF34")]
    assert (ec.Mappa.da_righe(righe).per_asin("B0FJSLCF34").sku
            == ec.Mappa.da_righe(list(reversed(righe))).per_asin("B0FJSLCF34").sku)


def test_un_padre_di_variazione_non_e_un_prodotto_da_agganciare():
    # Un padre non e' un oggetto fisico: un EAN non ce l'ha e non deve essere
    # proposto come sorgente per una variante eBay.
    m = ec.Mappa.da_righe([_riga("X0-010Z-E9UX", asin="B0PADRE0001", tipo="padre di variazione")])
    assert m.per_asin("B0PADRE0001") is None


def test_la_mappa_di_un_cliente_si_legge_dal_file_dei_codici(tmp_path):
    (tmp_path / "tecno-cold.json").write_text(
        '{"generata": "2026-09-05T10:00:00", "righe": ['
        '{"sku": "TC-VENT-450", "ean": "8012345678905", "asin": "B0TC1", "tipo": "prodotto"}]}')
    m = ec.carica("Tecno Cold", radice=str(tmp_path))
    assert m.per_asin("B0TC1").ean == "8012345678905"
    assert m.generata == "2026-09-05T10:00:00"


def test_senza_il_file_dei_codici_dice_come_generarlo(tmp_path):
    try:
        ec.carica("Tecno Cold", radice=str(tmp_path))
    except ec.CodiciMancanti as e:
        assert "ean_aggiorna" in str(e) and "Tecno Cold" in str(e)
    else:
        raise AssertionError("doveva rifiutarsi di procedere senza i codici")
