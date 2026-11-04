import pytest
from estrattore_manuale import da_dati_manuali


def test_costruisce_prodotto_dai_dati_minimi():
    p = da_dati_manuali({
        "sku": "ABC-1", "title": "Tappeto yoga", "price": 29.9,
        "quantity": 10, "product_type": "EXERCISE_MAT",
    })
    assert p.sku == "ABC-1"
    assert p.price == 29.9


def test_campo_obbligatorio_mancante_alza_valueerror():
    with pytest.raises(ValueError, match="quantity"):
        da_dati_manuali({"sku": "ABC-1", "title": "Tappeto yoga", "price": 29.9,
                          "product_type": "EXERCISE_MAT"})


def test_campo_sconosciuto_alza_valueerror():
    with pytest.raises(ValueError, match="colour"):
        da_dati_manuali({"sku": "ABC-1", "title": "Tappeto yoga", "price": 29.9,
                          "quantity": 10, "product_type": "EXERCISE_MAT", "colour": "Blu"})
