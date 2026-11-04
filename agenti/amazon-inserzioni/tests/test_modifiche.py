import pytest
from letto import StatoAttuale
from modifiche import Modifica, componi, sostituisci_elemento, aggiungi_elemento


def test_componi_scarta_valori_identici():
    stato = StatoAttuale(sku="A", product_type="X", attributi={
        "list_price": [{"value": 29.9, "currency": "EUR"}],
    })
    modifiche = componi(stato, {"list_price": [{"value": 29.9, "currency": "EUR"}]}, "manuale")
    assert modifiche == []


def test_componi_include_un_valore_diverso():
    stato = StatoAttuale(sku="A", product_type="X", attributi={
        "list_price": [{"value": 29.9, "currency": "EUR"}],
    })
    modifiche = componi(stato, {"list_price": [{"value": 24.9, "currency": "EUR"}]}, "manuale")
    assert modifiche == [Modifica(
        campo="list_price", prima=[{"value": 29.9, "currency": "EUR"}],
        dopo=[{"value": 24.9, "currency": "EUR"}], fonte="manuale",
    )]


@pytest.mark.parametrize("dopo", [None, "", [], {}])
def test_componi_scarta_sempre_un_dopo_vuoto_anche_se_prima_non_lo_era(dopo):
    stato = StatoAttuale(sku="A", product_type="X", attributi={
        "product_description": [{"value": "Descrizione esistente"}],
    })
    modifiche = componi(stato, {"product_description": dopo}, "manuale")
    assert modifiche == []


def test_componi_include_un_campo_nuovo_non_ancora_presente():
    stato = StatoAttuale(sku="A", product_type="X", attributi={})
    modifiche = componi(stato, {"colore": [{"value": "Blu"}]}, "fornitore")
    assert modifiche[0].prima is None
    assert modifiche[0].dopo == [{"value": "Blu"}]


def test_sostituisci_elemento_non_perde_gli_altri():
    attuali = [{"value": "A"}, {"value": "B"}, {"value": "C"}, {"value": "D"}, {"value": "E"}]
    nuovi = sostituisci_elemento(attuali, 2, {"value": "C-modificato"})
    assert len(nuovi) == 5
    assert nuovi[2] == {"value": "C-modificato"}
    assert nuovi[0] == attuali[0]
    assert nuovi[4] == attuali[4]
    # l'originale non va toccato
    assert attuali[2] == {"value": "C"}


def test_sostituisci_elemento_indice_fuori_range_alza_valueerror():
    with pytest.raises(ValueError, match="indice"):
        sostituisci_elemento([{"value": "A"}], 5, {"value": "X"})


def test_aggiungi_elemento_non_tocca_i_precedenti():
    attuali = [{"value": "A"}, {"value": "B"}]
    nuovi = aggiungi_elemento(attuali, {"value": "C"})
    assert nuovi == [{"value": "A"}, {"value": "B"}, {"value": "C"}]
    assert attuali == [{"value": "A"}, {"value": "B"}]
