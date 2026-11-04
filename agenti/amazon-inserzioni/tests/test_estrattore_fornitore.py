import os
from estrattore_fornitore import carica_mappatura, importa

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_carica_mappatura():
    m = carica_mappatura(os.path.join(_FIXTURES, "mappatura_esempio.yaml"))
    assert m["sku"] == "Codice"
    assert m["images"] == "Foto"


def test_importa_csv_scarta_le_righe_incomplete():
    mappatura = carica_mappatura(os.path.join(_FIXTURES, "mappatura_esempio.yaml"))
    prodotti, scarti = importa(os.path.join(_FIXTURES, "fornitore_esempio.csv"), mappatura)
    assert len(prodotti) == 1
    assert prodotti[0].sku == "SKU-1"
    assert prodotti[0].price == 29.90
    assert prodotti[0].quantity == 50
    assert prodotti[0].images == ["https://x/1.jpg", "https://x/2.jpg"]
    assert len(scarti) == 2
    numeri_riga = [s[0] for s in scarti]
    assert numeri_riga == [3, 4]
    assert "prezzo" in scarti[0][1] or "obbligatori" in scarti[0][1]


def test_importa_xlsx(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Codice", "Nome Articolo", "Marca", "Prezzo", "Qta", "TipoProdotto", "Foto", "EAN"])
    ws.append(["SKU-9", "Prodotto test", "New Era Test", 19.90, 5, "LUGGAGE", "https://x/9.jpg", "800111"])
    percorso = tmp_path / "fornitore.xlsx"
    wb.save(percorso)

    mappatura = carica_mappatura(os.path.join(_FIXTURES, "mappatura_esempio.yaml"))
    prodotti, scarti = importa(str(percorso), mappatura)
    assert len(prodotti) == 1
    assert prodotti[0].sku == "SKU-9"
    assert scarti == []
