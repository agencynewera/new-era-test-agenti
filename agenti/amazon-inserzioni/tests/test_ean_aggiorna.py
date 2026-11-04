# tests/test_ean_aggiorna.py
from __future__ import annotations
# Il raccoglitore dei codici di un cliente: legge il report "Tutte le
# inserzioni" di Seller Central e fonde i mercati in una riga per SKU.
import ean_aggiorna as ea

INTESTAZIONE = "item-name\tseller-sku\tproduct-id\tproduct-id-type\tasin1\tstatus\n"


def test_il_report_da_il_codice_solo_quando_e_un_ean_o_un_upc():
    tsv = INTESTAZIONE + (
        "Materasso\tBED-1\t8053307410562\t4\tB0916D7B9S\tActive\n"
        "Cuscino\tBED-2\tB0CM6VH62Z\t1\tB0CM6VH62Z\tActive\n")
    righe = ea.righe_da_report(tsv)
    assert righe[0]["ean"] == "8053307410562"
    assert righe[1]["ean"] == ""          # product-id-type 1 e' un ASIN, non un codice
    assert righe[1]["asin"] == "B0CM6VH62Z"


def test_lo_stesso_sku_su_due_mercati_diventa_una_riga_sola():
    it = [{"sku": "BED-1", "ean": "", "asin": "B0916D7B9S", "stato": "Active", "titolo": "x"}]
    de = [{"sku": "BED-1", "ean": "8053307410562", "asin": "B0916D7B9S", "stato": "Active", "titolo": "x"}]
    fuse = ea.fondi({"IT": it, "DE": de})
    assert len(fuse) == 1
    assert fuse[0]["ean"] == "8053307410562"   # il codice si prende da dove c'e'
    assert fuse[0]["mercati"] == "DE IT"


def test_una_riga_senza_sku_non_entra():
    assert ea.righe_da_report(INTESTAZIONE + "Senza\t\t8053307410562\t4\tB0X\tActive\n") == []


def test_il_documento_del_report_si_scarica_con_agent_nello_user_agent(monkeypatch):
    """Agent Policy Amazon (BSA sez. 19): l'URL del documento non passa da
    spapi_client, e senza header uscirebbe come python-requests."""
    from unittest.mock import Mock, patch
    import spapi_client
    mercato = next(iter(ea.EU))
    client = Mock()
    client.post.return_value = {"reportId": "rep-1"}
    client.get.side_effect = lambda path, **_: (
        {"processingStatus": "DONE", "reportDocumentId": "doc-1"}
        if path.endswith("/reports/rep-1") else {"url": "https://amazon-reports.example/doc-1"})
    monkeypatch.setattr(ea.time, "sleep", lambda _: None)

    with patch("requests.get", return_value=Mock(content=INTESTAZIONE.encode())) as mock_get:
        ea.scarica_report(client, [mercato], avviso=lambda _: None)

    user_agent = mock_get.call_args.kwargs["headers"]["User-Agent"]
    assert user_agent == spapi_client._USER_AGENT
    assert "Agent/" in user_agent
