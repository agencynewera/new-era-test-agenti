import os
from datetime import datetime, timezone
import pytest
import archivio

_ADESSO = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)


def _base(tmp_path):
    return tmp_path / "Desktop" / "LAVORO" / "AZIENDE NEW ERA" / "PERFORMANCE"


def test_la_cartella_della_spedizione_sta_in_prime_per_anno_mese_e_codice_fba(tmp_path, monkeypatch):
    """Stessa struttura che Mariano usa a mano in 10 LOGISTICA/PRIME, con mese
    numerato e data ISO perché in Finder si ordinino da sole."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (_base(tmp_path) / "ECM" / "10 LOGISTICA" / "PRIME").mkdir(parents=True)

    cartella = archivio.cartella_spedizione("ECM", "FBA1234ABCD", _ADESSO)

    assert cartella == str(_base(tmp_path) / "ECM" / "10 LOGISTICA" / "PRIME"
                           / "2026" / "09 SETTEMBRE" / "2026-09-07 FBA1234ABCD")
    assert os.path.isdir(cartella)


def test_prime_mancante_dentro_una_cartella_cliente_esistente_viene_creata(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (_base(tmp_path) / "ECM").mkdir(parents=True)
    cartella = archivio.cartella_spedizione("ECM", "FBA1234ABCD", _ADESSO)
    assert os.path.isdir(cartella)


def test_la_cartella_cliente_non_si_crea_mai(tmp_path, monkeypatch):
    """Le cartelle cliente le fa l'onboarding: crearne una qui vorrebbe dire
    archiviare in un posto che nessuno guarda."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _base(tmp_path).mkdir(parents=True)
    with pytest.raises(ValueError, match="ECM.*non trovata"):
        archivio.cartella_spedizione("ECM", "FBA1234ABCD", _ADESSO)
    assert not (_base(tmp_path) / "ECM").exists()


def test_cliente_senza_cartella_configurata(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="clienti.yaml"):
        archivio.cartella_spedizione(None, "FBA1234ABCD", _ADESSO)
