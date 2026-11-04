from unittest.mock import Mock
import pytest
from aplus_leggi import leggi, StatoAplus, badge_da_relazioni


def test_leggi_asin_senza_documento_esistente():
    client = Mock()
    client.get.return_value = {"contentMetadataRecords": [], "warnings": []}
    stato = leggi(client, "APJ6JRA9NG5V4", "B0EXAMPLE1")
    assert stato == StatoAplus(content_reference_key=None, asin="B0EXAMPLE1", nome=None,
                                stato_documento=None, locale=None, moduli=[], badge_asin=None)
    assert stato.locale is None
    # con nessun documento, non deve nemmeno provare a leggerne il contenuto
    assert client.get.call_count == 1


def test_leggi_asin_con_documento_esistente():
    client = Mock()
    client.get.side_effect = [
        {"contentMetadataRecords": [{
            "contentReferenceKey": "amzn1.aplus.cr.abc",
            "contentMetadata": {"name": "Storia del marchio", "status": "APPROVED"},
        }], "warnings": []},
        {"contentDocument": {"contentModuleList": [{"contentModuleType": "STANDARD_TEXT"}],
                             "locale": "it_IT"},
         "warnings": []},
        {"asinMetadataSet": [{"asin": "B0EXAMPLE1", "badgeSet": ["CONTENT_PUBLISHED"]}],
         "warnings": []},
    ]
    stato = leggi(client, "APJ6JRA9NG5V4", "B0EXAMPLE1")
    assert stato.content_reference_key == "amzn1.aplus.cr.abc"
    assert stato.nome == "Storia del marchio"
    assert stato.stato_documento == "APPROVED"
    assert stato.locale == "it_IT"
    assert stato.moduli == [{"contentModuleType": "STANDARD_TEXT"}]
    assert stato.badge_asin == "CONTENT_PUBLISHED"
    assert client.get.call_args_list[0].args[0] == "/aplus/2020-11-01/contentDocuments"
    assert client.get.call_args_list[0].kwargs["params"] == {
        "marketplaceId": "APJ6JRA9NG5V4", "asin": "B0EXAMPLE1"}
    assert client.get.call_args_list[1].args[0] == "/aplus/2020-11-01/contentDocuments/amzn1.aplus.cr.abc"
    assert client.get.call_args_list[2].args[0] == "/aplus/2020-11-01/contentDocuments/amzn1.aplus.cr.abc/asins"


def test_leggi_piu_di_un_documento_si_ferma_e_chiede():
    client = Mock()
    client.get.return_value = {"contentMetadataRecords": [
        {"contentReferenceKey": "amzn1.aplus.cr.abc", "contentMetadata": {"name": "Approvato", "status": "APPROVED"}},
        {"contentReferenceKey": "amzn1.aplus.cr.def", "contentMetadata": {"name": "Bozza", "status": "DRAFT"}},
    ], "warnings": []}
    with pytest.raises(ValueError, match="B0EXAMPLE1"):
        leggi(client, "APJ6JRA9NG5V4", "B0EXAMPLE1")


def test_badge_da_relazioni_trova_il_badge_dell_asin():
    relazioni = {"asinMetadataSet": [
        {"asin": "B0ALTRO", "badgeSet": ["CONTENT_PUBLISHED"]},
        {"asin": "B0EXAMPLE1", "badgeSet": ["CONTENT_NOT_PUBLISHED"]},
    ]}
    assert badge_da_relazioni(relazioni, "B0EXAMPLE1") == "CONTENT_NOT_PUBLISHED"


def test_badge_da_relazioni_asin_assente_torna_none():
    relazioni = {"asinMetadataSet": [{"asin": "B0ALTRO", "badgeSet": ["CONTENT_PUBLISHED"]}]}
    assert badge_da_relazioni(relazioni, "B0EXAMPLE1") is None


def test_badge_da_relazioni_badge_set_vuoto_torna_none():
    relazioni = {"asinMetadataSet": [{"asin": "B0EXAMPLE1", "badgeSet": []}]}
    assert badge_da_relazioni(relazioni, "B0EXAMPLE1") is None
