from datetime import date

import pytest

import stato_stock
import stock

OGGI = date(2026, 9, 5)


def _riga(sku="Reladol_300", disponibili=3, in_arrivo=22, venduti=15, consigliati=35):
    return stock.RigaStock(sku=sku, asin="B0TEST", disponibili=disponibili, in_arrivo=in_arrivo,
                           venduti_30gg=venduti, consigliati=consigliati, entro="09/12/2026",
                           alert_amazon=None)


def test_avvisa_una_volta_sola_quando_entra_in_zona():
    avvisi, nuovo = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], {}, OGGI)
    assert [(a.sku, a.motivo) for a in avvisi] == [("Reladol_300", "nuovo")]
    domani, _ = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], nuovo, date(2026, 9, 6))
    assert domani == []


def test_parla_di_nuovo_se_peggiora():
    _, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], {}, OGGI)
    peggio = _riga(disponibili=0, in_arrivo=0, consigliati=35)
    avvisi, stato2 = stato_stock.da_avvisare([(peggio, stock.CRITICO)], stato, date(2026, 9, 6))
    assert [(a.sku, a.motivo) for a in avvisi] == [("Reladol_300", "peggiorato")]
    ancora, _ = stato_stock.da_avvisare([(peggio, stock.CRITICO)], stato2, date(2026, 9, 7))
    assert ancora == []


def test_la_spedizione_partita_azzera_senza_avvisare():
    """Inbound cresciuto = qualcuno ha spedito. Nessuna mail, e il timer del
    sollecito riparte da oggi: la voce non si cancella, o domani sarebbe 'nuovo'."""
    _, stato = stato_stock.da_avvisare([(_riga(in_arrivo=22), stock.RIASSORTIRE)], {}, OGGI)
    avvisi, dopo = stato_stock.da_avvisare(
        [(_riga(in_arrivo=66), stock.RIASSORTIRE)], stato, date(2026, 9, 8))
    assert avvisi == []
    assert dopo["Reladol_300"]["in_arrivo"] == 66
    assert dopo["Reladol_300"]["dal"] == "2026-09-08"


def test_sollecita_dopo_sette_giorni_di_immobilita():
    _, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], {}, OGGI)
    zitto, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], stato, date(2026, 9, 11))
    assert zitto == []
    avvisi, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], stato, date(2026, 9, 12))
    assert [(a.sku, a.motivo) for a in avvisi] == [("Reladol_300", "sollecito")]
    ancora, _ = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], stato, date(2026, 9, 13))
    assert ancora == []


def test_tornare_a_posto_cancella_la_voce():
    _, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], {}, OGGI)
    avvisi, dopo = stato_stock.da_avvisare(
        [(_riga(disponibili=300, consigliati=0), stock.OK)], stato, date(2026, 9, 20))
    assert avvisi == [] and dopo == {}


def test_stato_illeggibile_riparte_da_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    percorso = stato_stock.percorso("ECM")
    import os
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    open(percorso, "w").write("{ rotto")
    assert stato_stock.carica("ECM") == {}


def test_salva_e_rilegge(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], {}, OGGI)
    stato_stock.salva("ECM", stato)
    assert stato_stock.carica("ECM") == stato


def test_carica_ritrova_lo_stato_a_prescindere_da_maiuscole_e_spazi(tmp_path, monkeypatch):
    """Task 11: come scatole.carica_istantanea — altrimenti `giro.py stock ecm`
    a mano scrive un file diverso da quello che il LaunchAgent legge passando
    da clienti.elenco(), e il cliente riceve avvisi doppi."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], {}, OGGI)
    stato_stock.salva("ECM", stato)
    assert stato_stock.carica("ecm") == stato


def test_una_scrittura_fallita_non_distrugge_lo_stato_precedente(tmp_path, monkeypatch):
    """Uno stato troncato si legge come vuoto, e il giro riavviserebbe tutto."""
    monkeypatch.setenv("HOME", str(tmp_path))
    stato_stock.salva("ECM", {"Reladol_300": {"zona": "critico"}})

    def esplode(*a, **k):
        raise RuntimeError("disco pieno")

    monkeypatch.setattr(stato_stock.json, "dumps", esplode)
    with pytest.raises(RuntimeError):
        stato_stock.salva("ECM", {"Altro": {}})
    assert stato_stock.carica("ECM") == {"Reladol_300": {"zona": "critico"}}
