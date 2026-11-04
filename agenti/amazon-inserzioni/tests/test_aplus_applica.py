from unittest.mock import Mock
import pytest
from aplus_componi import PacchettoAplus
from aplus_applica import applica, EsitoAplus, _issues


def test_issues_fonde_errors_e_warnings_con_severita():
    risposta = {"errors": [{"code": "CONTENT_FAILED_VALIDATION", "message": "vietato"}],
                "warnings": [{"code": "ASIN_FAILED_VALIDATION", "message": "controlla l'ASIN"}]}
    issues = _issues(risposta)
    assert {"code": "CONTENT_FAILED_VALIDATION", "message": "vietato", "severity": "ERROR"} in issues
    assert {"code": "ASIN_FAILED_VALIDATION", "message": "controlla l'ASIN", "severity": "WARNING"} in issues


def test_applica_niente_modifiche_alza_valueerror():
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key="amzn1.aplus.cr.abc",
                                nome="Scheda", locale="it_IT", stato_documento=None, badge_asin=None,
                                moduli_prima=[{"a": 1}], moduli_dopo=[{"a": 1}])
    with pytest.raises(ValueError, match="nessuna modifica"):
        applica(Mock(), "APJ6JRA9NG5V4", "Bedshire", pacchetto)


def test_applica_svuotamento_alza_valueerror():
    # PacchettoAplus costruito a mano, bypassando componi() (che già impedisce questo
    # caso): moduli_dopo=[] non deve mai passare, altrimenti svuota i Contenuti A+
    # dell'ASIN riportando comunque ACCEPTED.
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key="amzn1.aplus.cr.abc",
                                nome="Scheda", locale="it_IT", stato_documento=None, badge_asin=None,
                                moduli_prima=[{"a": 1}], moduli_dopo=[])
    with pytest.raises(ValueError, match="nessun modulo da scrivere: uno svuotamento non si propone mai"):
        applica(Mock(), "APJ6JRA9NG5V4", "Bedshire", pacchetto)


def test_applica_documento_nuovo_crea_collega_e_sottomette(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None, nome="Scheda", locale="it_IT", stato_documento=None, badge_asin=None,
                                moduli_prima=[], moduli_dopo=[{"contentModuleType": "STANDARD_TEXT"}])
    client = Mock()
    client.post.side_effect = [
        {"contentReferenceKey": "amzn1.aplus.cr.nuovo", "errors": [], "warnings": []},  # createContentDocument
        {"errors": [], "warnings": []},  # postContentDocumentAsinRelations
        {"errors": [], "warnings": []},  # postContentDocumentApprovalSubmission
    ]
    client.get.return_value = {"asinMetadataSet": [{"asin": "B0EXAMPLE1", "badgeSet": ["CONTENT_NOT_PUBLISHED"]}]}

    esito = applica(client, "APJ6JRA9NG5V4", "Bedshire", pacchetto)

    assert esito == EsitoAplus(content_reference_key="amzn1.aplus.cr.nuovo", esito="ACCEPTED",
                                issues=[], badge_asin="CONTENT_NOT_PUBLISHED")
    creazione = client.post.call_args_list[0]
    assert creazione.args[0] == "/aplus/2020-11-01/contentDocuments"
    assert creazione.kwargs["json_body"]["contentDocument"]["contentModuleList"] == pacchetto.moduli_dopo
    assert creazione.kwargs["json_body"]["contentDocument"]["locale"] == "it_IT"
    collegamento = client.post.call_args_list[1]
    assert collegamento.args[0] == "/aplus/2020-11-01/contentDocuments/amzn1.aplus.cr.nuovo/asins"
    assert collegamento.kwargs["json_body"] == {"asinSet": ["B0EXAMPLE1"]}
    sottomissione = client.post.call_args_list[2]
    assert sottomissione.args[0] == "/aplus/2020-11-01/contentDocuments/amzn1.aplus.cr.nuovo/approvalSubmissions"

    import registro
    riga = registro.ultimo_lotto("Bedshire", "contenuti-aplus")[0]
    assert riga["esito"] == "ACCEPTED"
    assert riga["asin"] == "B0EXAMPLE1"


def test_applica_documento_esistente_usa_update_non_create(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key="amzn1.aplus.cr.abc",
                                nome="Scheda", locale="it_IT", stato_documento=None, badge_asin=None,
                                moduli_prima=[{"a": 1}], moduli_dopo=[{"a": 2}])
    client = Mock()
    client.post.side_effect = [
        {"errors": [], "warnings": []},  # updateContentDocument
        {"errors": [], "warnings": []},  # postContentDocumentAsinRelations
        {"errors": [], "warnings": []},  # postContentDocumentApprovalSubmission
    ]
    client.get.return_value = {"asinMetadataSet": [{"asin": "B0EXAMPLE1", "badgeSet": ["CONTENT_PUBLISHED"]}]}

    esito = applica(client, "APJ6JRA9NG5V4", "Bedshire", pacchetto)

    assert esito.content_reference_key == "amzn1.aplus.cr.abc"
    aggiornamento = client.post.call_args_list[0]
    assert aggiornamento.args[0] == "/aplus/2020-11-01/contentDocuments/amzn1.aplus.cr.abc"


def test_applica_rifiutato_non_collega_ne_sottomette(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None, nome="Scheda", locale="it_IT", stato_documento=None, badge_asin=None,
                                moduli_prima=[], moduli_dopo=[{"contentModuleType": "STANDARD_TEXT"}])
    client = Mock()
    client.post.return_value = {
        "contentReferenceKey": "", "warnings": [],
        "errors": [{"code": "CONTENT_FAILED_VALIDATION", "message": "vietato"}],
    }
    esito = applica(client, "APJ6JRA9NG5V4", "Bedshire", pacchetto)
    assert esito.esito == "INVALID"
    assert client.post.call_count == 1  # niente collegamento asin, niente sottomissione
    client.get.assert_not_called()  # niente rilettura badge se non è mai stato sottomesso


def test_applica_collegamento_asin_fallito_non_sottomette(tmp_path, monkeypatch):
    # Il documento è valido e viene creato, ma il collegamento all'ASIN fallisce:
    # non deve MAI dichiararsi ACCEPTED su un documento mai davvero sottomesso.
    monkeypatch.setenv("HOME", str(tmp_path))
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None, nome="Scheda", locale="it_IT", stato_documento=None, badge_asin=None,
                                moduli_prima=[], moduli_dopo=[{"contentModuleType": "STANDARD_TEXT"}])
    client = Mock()
    client.post.side_effect = [
        {"contentReferenceKey": "amzn1.aplus.cr.nuovo", "errors": [], "warnings": []},  # createContentDocument
        {"errors": [{"code": "ASIN_FAILED_VALIDATION", "message": "asin non valido"}], "warnings": []},  # asins
    ]
    esito = applica(client, "APJ6JRA9NG5V4", "Bedshire", pacchetto)
    assert esito.esito == "INVALID"
    assert {"code": "ASIN_FAILED_VALIDATION", "message": "asin non valido", "severity": "ERROR"} in esito.issues
    assert client.post.call_count == 2  # niente terza chiamata (sottomissione)
    client.get.assert_not_called()  # niente rilettura badge


def test_applica_sottomissione_fallita_non_rilegge_badge(tmp_path, monkeypatch):
    # Documento creato e collegato, ma la sottomissione per approvazione fallisce:
    # niente rilettura badge (non è mai entrato in coda di revisione).
    monkeypatch.setenv("HOME", str(tmp_path))
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None, nome="Scheda", locale="it_IT", stato_documento=None, badge_asin=None,
                                moduli_prima=[], moduli_dopo=[{"contentModuleType": "STANDARD_TEXT"}])
    client = Mock()
    client.post.side_effect = [
        {"contentReferenceKey": "amzn1.aplus.cr.nuovo", "errors": [], "warnings": []},  # createContentDocument
        {"errors": [], "warnings": []},  # asins
        {"errors": [{"code": "APPROVAL_FAILED", "message": "sottomissione respinta"}], "warnings": []},  # approvalSubmissions
    ]
    esito = applica(client, "APJ6JRA9NG5V4", "Bedshire", pacchetto)
    assert esito.esito == "INVALID"
    assert {"code": "APPROVAL_FAILED", "message": "sottomissione respinta", "severity": "ERROR"} in esito.issues
    assert client.post.call_count == 3
    client.get.assert_not_called()  # niente rilettura badge


def test_applica_solo_warning_non_blocca_la_catena(tmp_path, monkeypatch):
    # Un warning sulla create/update non deve mai bloccare link → submit → rilettura:
    # solo un ERROR blocca.
    monkeypatch.setenv("HOME", str(tmp_path))
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None, nome="Scheda", locale="it_IT", stato_documento=None, badge_asin=None,
                                moduli_prima=[], moduli_dopo=[{"contentModuleType": "STANDARD_TEXT"}])
    client = Mock()
    client.post.side_effect = [
        {"contentReferenceKey": "amzn1.aplus.cr.nuovo", "errors": [],
         "warnings": [{"code": "CONTENT_QUALITY_WARNING", "message": "immagine a bassa risoluzione"}]},  # create
        {"errors": [], "warnings": []},  # asins
        {"errors": [], "warnings": []},  # approvalSubmissions
    ]
    client.get.return_value = {"asinMetadataSet": [{"asin": "B0EXAMPLE1", "badgeSet": ["CONTENT_NOT_PUBLISHED"]}]}

    esito = applica(client, "APJ6JRA9NG5V4", "Bedshire", pacchetto)

    assert esito.esito == "ACCEPTED"
    assert {"code": "CONTENT_QUALITY_WARNING", "message": "immagine a bassa risoluzione",
            "severity": "WARNING"} in esito.issues
    assert client.post.call_count == 3  # link e sottomissione avvenuti comunque
    client.get.assert_called_once()  # rilettura badge avvenuta
    assert esito.badge_asin == "CONTENT_NOT_PUBLISHED"


def test_applica_su_errore_di_trasporto_registra_l_esito_e_rialza(tmp_path, monkeypatch):
    # Stessa regola di applica.applica: il registro non resta mai su "in corso"
    # se la catena di scrittura cade a meta' (rete, 500, token scaduto).
    import json, os
    from datetime import datetime, timezone
    from spapi_client import SpApiError
    monkeypatch.setenv("HOME", str(tmp_path))
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None, nome="Scheda", locale="it_IT",
                                stato_documento=None, badge_asin=None,
                                moduli_prima=[], moduli_dopo=[{"contentModuleType": "STANDARD_TEXT"}])
    client = Mock()
    client.post.side_effect = SpApiError("503 su /aplus: service unavailable")

    with pytest.raises(SpApiError, match="503"):
        applica(client, "APJ6JRA9NG5V4", "Bedshire", pacchetto)

    data = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    righe = json.load(open(os.path.join(str(tmp_path), ".config/newera-amazon/contenuti-aplus", f"bedshire-{data}.json")))
    assert righe[0]["esito"] == "ERRORE TRASPORTO"
    assert "503" in righe[0]["issues"][0]["message"]
