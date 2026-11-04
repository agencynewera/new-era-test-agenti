import pytest
from datetime import datetime, timezone

import stima
from stock import RigaStock
from spapi_client import SpApiError

ADESSO = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)   # mercoledì


def _riga(sku="SKU-1", disponibili=0, in_arrivo=0, venduti=0, in_lavorazione=0, in_working=0):
    return RigaStock(sku, "B0TEST", disponibili, in_arrivo, venduti, 0, None, None, in_lavorazione, in_working)


def test_ritmo_costante():
    al_giorno, valide, note = stima.ritmo([14] * 13)
    assert al_giorno == pytest.approx(2.0)
    assert (valide, note) == (13, [])


def test_gli_zeri_di_un_articolo_che_vende_sono_esauriti():
    """Bedshire PILLOW-FBA, 13/09: esaurito, il report diceva 0. Le settimane
    a zero fra settimane piene non abbassano il ritmo."""
    al_giorno, valide, note = stima.ritmo([21, 21, 0, 0, 0, 21, 21])
    assert al_giorno == pytest.approx(3.0)
    assert valide == 4
    assert note == ["3 settimane a zero tolte (esaurito)"]


def test_lo_zero_di_un_articolo_da_tre_a_settimana_resta():
    """Reladol_300, collaudo del 01/11: 3 pz di media, una settimana a zero
    capita senza essere un esaurito."""
    _, valide, note = stima.ritmo([3, 6, 1, 5, 6, 2, 0, 4, 2, 1, 7, 2, 3])
    assert (valide, note) == (13, [])


def test_gli_zeri_di_un_articolo_lento_sono_vendite_vere():
    al_giorno, valide, _ = stima.ritmo([0, 0, 1, 0, 1, 0, 0, 1])
    assert valide == 6   # dalla prima settimana venduta
    assert al_giorno == pytest.approx(0.5 / 7)   # 3 pezzi in 6 settimane


def test_un_picco_conta_come_la_mediana():
    """ED-OTZI-8I3E: 80 pezzi in una settimana di evento non si ripetono."""
    al_giorno, _, note = stima.ritmo([7, 7, 80, 7, 7])
    assert al_giorno == pytest.approx(1.0)
    assert note == ["picco di 80 pz in una settimana contato come 7"]


def test_quattro_pezzi_di_un_articolo_lento_non_sono_un_picco():
    _, _, note = stima.ritmo([1, 0, 1, 4, 1, 1])
    assert note == []


def test_mai_venduto_e_zero():
    assert stima.ritmo([0, 0, 0]) == (0.0, 0, [])


def test_da_inviare_toglie_magazzino_e_arrivi():
    s = stima.stima_sku(_riga(disponibili=20, in_arrivo=10), [14] * 13, giorni=60, transito=10, scorta=14)
    assert s.fabbisogno == 168          # 2/gg × 84 giorni
    assert s.da_inviare == 138
    assert s.note == []


def test_in_lavorazione_conta_working_no():
    """FC transfer e FC Processing sono già in Amazon; i piani in WORKING
    possono non partire mai (wiki, Report Restock)."""
    s = stima.stima_sku(_riga(disponibili=20, in_lavorazione=5, in_arrivo=30, in_working=20),
                        [14] * 13, giorni=60, transito=10, scorta=14)
    assert (s.disponibili, s.in_arrivo) == (25, 10)
    assert s.da_inviare == 168 - 25 - 10
    assert s.note == ["20 pz in piani WORKING non contati: se partono, toglili dal da inviare"]


def test_coperto_non_manda_niente():
    s = stima.stima_sku(_riga(disponibili=500), [14] * 13, giorni=30, transito=10, scorta=14)
    assert s.da_inviare == 0


def test_vendite_non_lette_ripiega_sul_report_e_lo_dice():
    s = stima.stima_sku(_riga(venduti=30), None, giorni=30, transito=0, scorta=0)
    assert s.fabbisogno == 30
    assert s.debole


def test_una_settimana_sola_e_stima_debole():
    s = stima.stima_sku(_riga(), [0, 0, 0, 14], giorni=30, transito=0, scorta=0)
    assert s.debole


class _Vendite:
    def __init__(self, payload=None, errore=False):
        self.payload, self.errore, self.chieste = payload, errore, []

    def get(self, path, params=None):
        self.chieste.append((path, params))
        if self.errore:
            raise SpApiError("500")
        return {"payload": self.payload}


def test_chiede_settimane_intere_fba_dello_sku():
    c = _Vendite([{"unitCount": 3}, {"unitCount": 5}])
    assert stima.settimane_vendute(c, "SKU-1", "APJ6JRA9NG5V4", ADESSO) == [3, 5]
    path, params = c.chieste[0]
    assert path == "/sales/v1/orderMetrics"
    assert params["interval"] == "2026-08-03T00:00:00+02:00--2026-11-02T00:00:00+01:00"
    assert (params["granularity"], params["fulfillmentNetwork"], params["sku"]) == ("Week", "AFN", "SKU-1")


def test_vendite_illeggibili_non_diventano_zeri():
    assert stima.settimane_vendute(_Vendite(errore=True), "S", "M", ADESSO) is None


def test_periodo_da_data_o_giorni():
    assert stima.giorni_periodo(ADESSO, None, "2026-12-04") == 30
    assert stima.giorni_periodo(ADESSO, 45, None) == 45
    with pytest.raises(ValueError):
        stima.giorni_periodo(ADESSO, None, None)
    with pytest.raises(ValueError):
        stima.giorni_periodo(ADESSO, None, "2026-11-01")
