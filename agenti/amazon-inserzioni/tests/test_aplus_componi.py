import pytest
from aplus_leggi import StatoAplus
from aplus_componi import componi, PacchettoAplus


def test_componi_documento_nuovo():
    stato = StatoAplus(content_reference_key=None, asin="B0EXAMPLE1", nome=None,
                        stato_documento=None, locale=None, moduli=[], badge_asin=None)
    moduli_nuovi = [{"contentModuleType": "STANDARD_TEXT", "standardText": {"headline": {"value": "Titolo"}}}]
    pacchetto = componi(stato, moduli_nuovi, "Scheda tecnica", locale="it_IT")
    assert pacchetto == PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None,
                                        nome="Scheda tecnica", locale="it_IT",
                                        stato_documento=None, badge_asin=None,
                                        moduli_prima=[], moduli_dopo=moduli_nuovi)
    assert pacchetto.locale == "it_IT"
    assert pacchetto.stato_documento is None
    assert pacchetto.badge_asin is None


def test_componi_documento_esistente_porta_sempre_la_lista_intera():
    stato = StatoAplus(content_reference_key="amzn1.aplus.cr.abc", asin="B0EXAMPLE1",
                        nome="Scheda tecnica", stato_documento="APPROVED", locale="it_IT",
                        moduli=[{"contentModuleType": "STANDARD_TEXT"}], badge_asin="CONTENT_PUBLISHED")
    moduli_nuovi = [{"contentModuleType": "STANDARD_TEXT"}, {"contentModuleType": "STANDARD_COMPANY_LOGO"}]
    pacchetto = componi(stato, moduli_nuovi, "Scheda tecnica")
    assert pacchetto.content_reference_key == "amzn1.aplus.cr.abc"
    assert pacchetto.moduli_prima == [{"contentModuleType": "STANDARD_TEXT"}]
    assert pacchetto.moduli_dopo == moduli_nuovi
    assert pacchetto.locale == "it_IT"
    assert pacchetto.stato_documento == "APPROVED"
    assert pacchetto.badge_asin == "CONTENT_PUBLISHED"


def test_componi_documento_esistente_il_locale_vivo_vince_su_quello_passato():
    # Il locale di un documento già esistente non si cambia mai per iniziativa
    # del chiamante, nemmeno se ne passa uno diverso esplicitamente.
    stato = StatoAplus(content_reference_key="amzn1.aplus.cr.abc", asin="B0EXAMPLE1",
                        nome="Scheda tecnica", stato_documento="APPROVED", locale="it_IT",
                        moduli=[{"contentModuleType": "STANDARD_TEXT"}], badge_asin="CONTENT_PUBLISHED")
    moduli_nuovi = [{"contentModuleType": "STANDARD_TEXT"}]
    pacchetto = componi(stato, moduli_nuovi, "Scheda tecnica", locale="de_DE")
    assert pacchetto.locale == "it_IT"


def test_componi_senza_moduli_alza_valueerror():
    stato = StatoAplus(content_reference_key=None, asin="B0EXAMPLE1", nome=None,
                        stato_documento=None, locale=None, moduli=[], badge_asin=None)
    with pytest.raises(ValueError, match="nessun modulo"):
        componi(stato, [], "Scheda tecnica", locale="it_IT")


def test_componi_documento_nuovo_senza_locale_alza_valueerror():
    # Documento nuovo (nessun locale vivo) e nessun locale passato dal chiamante:
    # non si può indovinare dal marketplace, si ferma e chiede.
    stato = StatoAplus(content_reference_key=None, asin="B0EXAMPLE1", nome=None,
                        stato_documento=None, locale=None, moduli=[], badge_asin=None)
    moduli_nuovi = [{"contentModuleType": "STANDARD_TEXT"}]
    with pytest.raises(ValueError, match="locale obbligatorio"):
        componi(stato, moduli_nuovi, "Scheda tecnica")
