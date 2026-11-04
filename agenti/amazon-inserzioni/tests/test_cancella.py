from unittest.mock import Mock
import pytest
import registro
from spapi_client import SpApiError
from cancella import cancella


def _client(risposta=None, errore=None):
    c = Mock()
    if errore is not None:
        c.delete.side_effect = errore
    else:
        c.delete.return_value = risposta or {
            "sku": "ABC-1", "status": "ACCEPTED", "submissionId": "sub1", "issues": [],
        }
    return c


def test_cancella_chiama_delete_sul_percorso_dello_sku(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = _client()

    esito, issues = cancella(c, "SELLER1", "APJ6JRA9NG5V4", "Tecno Cold", "ABC-1")

    assert esito == "ACCEPTED"
    assert issues == []
    c.delete.assert_called_once_with("/listings/2021-08-01/items/SELLER1/ABC-1",
                                     params={"marketplaceIds": "APJ6JRA9NG5V4"})


def test_cancella_codifica_lo_sku_con_la_barra(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = _client()

    cancella(c, "SELLER1", "APJ6JRA9NG5V4", "Tecno Cold", "MA1/4SX-R")

    assert c.delete.call_args.args[0] == "/listings/2021-08-01/items/SELLER1/MA1%2F4SX-R"


def test_cancella_lascia_traccia_nel_registro(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    cancella(_client(), "SELLER1", "APJ6JRA9NG5V4", "Tecno Cold", "ABC-1")

    righe = registro.ultimo_lotto("Tecno Cold", "aggiornamenti")
    assert len(righe) == 1
    assert righe[0]["sku"] == "ABC-1"
    assert righe[0]["origine"] == "cancellazione"
    assert righe[0]["esito"] == "ACCEPTED"


def test_cancella_registra_anche_lesito_negativo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = _client({"sku": "ABC-1", "status": "INVALID",
                 "issues": [{"code": "8541", "severity": "ERROR", "message": "no"}]})

    esito, issues = cancella(c, "SELLER1", "APJ6JRA9NG5V4", "Tecno Cold", "ABC-1")

    assert esito == "INVALID"
    assert issues[0]["code"] == "8541"
    assert registro.ultimo_lotto("Tecno Cold", "aggiornamenti")[0]["esito"] == "INVALID"


def test_cancella_non_lascia_il_registro_in_corso_se_il_trasporto_cade(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = _client(errore=SpApiError("503 su /listings"))

    with pytest.raises(SpApiError):
        cancella(c, "SELLER1", "APJ6JRA9NG5V4", "Tecno Cold", "ABC-1")

    riga = registro.ultimo_lotto("Tecno Cold", "aggiornamenti")[0]
    assert riga["esito"] == "ERRORE TRASPORTO"
    assert "503" in riga["issues"][0]["message"]


def test_cancella_rifiuta_uno_sku_vuoto(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = _client()

    with pytest.raises(SpApiError):
        cancella(c, "SELLER1", "APJ6JRA9NG5V4", "Tecno Cold", "")

    c.delete.assert_not_called()
