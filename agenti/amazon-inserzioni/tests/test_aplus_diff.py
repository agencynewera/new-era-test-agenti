from aplus_componi import PacchettoAplus
from aplus_diff import rendi_tabella


def test_rendi_tabella_nessuna_modifica():
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key="amzn1.aplus.cr.abc",
                                nome="Scheda", locale="it_IT", stato_documento=None, badge_asin=None,
                                moduli_prima=[{"a": 1}], moduli_dopo=[{"a": 1}])
    assert rendi_tabella(pacchetto) == "B0EXAMPLE1: nessuna modifica proposta (i moduli sono già identici a quelli online)"


def test_rendi_tabella_documento_nuovo():
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None,
                                nome="Scheda", locale="it_IT", stato_documento=None, badge_asin=None,
                                moduli_prima=[], moduli_dopo=[{"a": 1}])
    tabella = rendi_tabella(pacchetto)
    assert "B0EXAMPLE1" in tabella
    assert "(nuovo)" in tabella
    assert "modulo 1: None → {'a': 1}" in tabella
    # documento nuovo: nessuno stato attuale da mostrare, la riga non compare
    assert "stato attuale" not in tabella


def test_rendi_tabella_modulo_modificato():
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key="amzn1.aplus.cr.abc",
                                nome="Scheda", locale="it_IT", stato_documento=None, badge_asin=None,
                                moduli_prima=[{"a": 1}], moduli_dopo=[{"a": 2}])
    tabella = rendi_tabella(pacchetto)
    assert "modulo 1: {'a': 1} → {'a': 2}" in tabella


def test_rendi_tabella_mostra_stato_attuale_quando_presente():
    # Un documento già pubblicato o in revisione: la riga "stato attuale" deve
    # sempre apparire, è il dato che cambia la decisione di Mariano sul "vai".
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key="amzn1.aplus.cr.abc",
                                nome="Scheda", locale="it_IT",
                                stato_documento="CONTENT_PUBLISHED", badge_asin="CONTENT_PUBLISHED",
                                moduli_prima=[{"a": 1}], moduli_dopo=[{"a": 2}])
    tabella = rendi_tabella(pacchetto)
    assert "stato attuale: CONTENT_PUBLISHED" in tabella
    assert "badge ASIN: CONTENT_PUBLISHED" in tabella


def test_rendi_tabella_documento_nuovo_stato_attuale_nessuno():
    # Se per qualche motivo la riga viene comunque richiesta su un pacchetto senza
    # stato/badge, deve leggersi "nessuno", mai un None crudo.
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key="amzn1.aplus.cr.abc",
                                nome="Scheda", locale="it_IT",
                                stato_documento="SUBMITTED", badge_asin=None,
                                moduli_prima=[{"a": 1}], moduli_dopo=[{"a": 2}])
    tabella = rendi_tabella(pacchetto)
    assert "stato attuale: SUBMITTED" in tabella
    assert "badge ASIN: nessuno" in tabella
