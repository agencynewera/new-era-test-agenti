import os
from unittest.mock import Mock
import pytest
from estrattore_generico import estrai_da_link, EstrazioneFallita

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _sessione_con(nome_file, status=200):
    with open(os.path.join(_FIXTURES, nome_file), encoding="utf-8") as f:
        html = f.read()
    sess = Mock()
    risposta = Mock()
    risposta.status_code = status
    risposta.text = html
    sess.get.return_value = risposta
    return sess


def test_estrae_da_ldjson():
    p = estrai_da_link("https://esempio.it/prodotto", sessione=_sessione_con("ldjson_prodotto.html"))
    assert p.title == "Tappeto yoga antiscivolo"
    assert p.brand == "New Era Test"
    assert p.price == 29.90
    assert p.ean == "8001234567890"
    assert p.images == ["https://esempio.it/1.jpg", "https://esempio.it/2.jpg"]
    assert p.sku == ""
    assert p.quantity == 0


def test_estrae_da_opengraph_quando_manca_ldjson():
    p = estrai_da_link("https://esempio.it/prodotto", sessione=_sessione_con("opengraph_prodotto.html"))
    assert p.title == "Tappeto yoga antiscivolo"
    assert p.price == 29.90
    assert p.images == ["https://esempio.it/1.jpg"]


def test_pagina_senza_dati_alza_estrazionefallita():
    with pytest.raises(EstrazioneFallita, match="nessun dato strutturato"):
        estrai_da_link("https://esempio.it/prodotto", sessione=_sessione_con("pagina_senza_dati.html"))


def test_pagina_non_raggiungibile_alza_estrazionefallita():
    with pytest.raises(EstrazioneFallita, match="404"):
        estrai_da_link("https://esempio.it/prodotto", sessione=_sessione_con("pagina_senza_dati.html", status=404))
