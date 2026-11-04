import json
import os
import pytest
import giri


def test_salva_e_carica_sotto_config_newera(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    giri.salva("wf1", {"cliente": "Phy to Pet", "riferimento": "2026-09-02#0#abc"})
    atteso = tmp_path / ".config" / "newera-amazon" / "spedizioni" / "giri" / "wf1.json"
    assert atteso.is_file()
    assert giri.carica("wf1") == {"cliente": "Phy to Pet", "riferimento": "2026-09-02#0#abc"}


def test_carica_di_un_piano_sconosciuto_torna_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert giri.carica("wf-mai-visto") is None


def test_aggiorna_tocca_solo_i_campi_dati(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    giri.salva("wf1", {"cliente": "Phy to Pet", "scelte": {}, "bloccato": None})
    dopo = giri.aggiorna("wf1", bloccato="ESITO INCERTO ...", scelte={"placement_option_id": "pl1"})
    assert dopo["cliente"] == "Phy to Pet"
    assert dopo["bloccato"].startswith("ESITO INCERTO")
    assert giri.carica("wf1")["scelte"] == {"placement_option_id": "pl1"}


def test_scrittura_atomica_non_lascia_file_di_appoggio(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    giri.salva("wf1", {"a": 1})
    cartella = tmp_path / ".config" / "newera-amazon" / "spedizioni" / "giri"
    assert sorted(os.listdir(cartella)) == ["wf1.json"]


def test_plan_id_strano_non_scrive_fuori_dalla_sua_cartella(tmp_path, monkeypatch):
    """Il plan_id arriva dalla riga di comando: un "../" scriverebbe dove
    vuole. Come registro._nome_cliente, ma qui si rifiuta invece di
    sostituire — un id di Amazon non ha mai caratteri strani."""
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="plan_id"):
        giri.salva("../x", {})
    with pytest.raises(ValueError, match="plan_id"):
        giri.carica("wf1/../../fuori")
    giri.salva("wf0dae3b7c-919c-4efb-8761-c93d7126908f", {"cliente": "Phy to Pet"})
    assert giri.carica("wf0dae3b7c-919c-4efb-8761-c93d7126908f") == {"cliente": "Phy to Pet"}


def test_il_lucchetto_del_piano_non_si_prende_due_volte(tmp_path, monkeypatch):
    """Due `conferma --vai` insieme leggerebbero la stessa foto e prenoterebbero
    entrambe: la seconda deve fermarsi subito, non aspettare."""
    monkeypatch.setenv("HOME", str(tmp_path))
    with giri.lucchetto("wf1"):
        with pytest.raises(ValueError, match="in corso"):
            with giri.lucchetto("wf1"):
                pass
    with giri.lucchetto("wf1"):      # rilasciato: si riprende
        pass
