from modifiche import Modifica
from diff import rendi_tabella


def test_rendi_tabella_senza_modifiche():
    testo = rendi_tabella("ABC-1", [])
    assert "nessuna modifica" in testo
    assert "ABC-1" in testo


def test_rendi_tabella_mostra_prima_dopo_e_fonte():
    modifiche = [Modifica(campo="list_price", prima=29.9, dopo=24.9, fonte="manuale")]
    testo = rendi_tabella("ABC-1", modifiche)
    assert "list_price" in testo
    assert "29.9" in testo
    assert "24.9" in testo
    assert "manuale" in testo
