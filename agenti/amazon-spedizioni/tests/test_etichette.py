import os
from unittest.mock import Mock, patch
import pytest
import requests
from spapi_client import SpApiError
from etichette import scarica_etichette, leggi_cartoni, leggi_cartoni_completi

# Forma vera di un id cartone, letta dal vivo il 2026-08-29.
_CARTONI = ["FBA1234ABCDU000001"]


@patch("etichette.requests.get")
def test_scarica_etichette_salva_il_pdf_sul_desktop(mock_get, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"payload": {"DownloadURL": "https://amazon-labels.example/x.pdf"}}
    risposta_pdf = Mock()
    risposta_pdf.status_code = 200
    risposta_pdf.content = b"%PDF-1.4 finto contenuto"
    mock_get.return_value = risposta_pdf

    percorso = scarica_etichette(client, "FBA1234ABCD", "Phy to Pet", cartoni=_CARTONI)

    assert percorso == str(tmp_path / "Desktop" / "Etichette-Phy to Pet-FBA1234ABCD.pdf")
    assert os.path.exists(percorso)
    with open(percorso, "rb") as f:
        assert f.read() == b"%PDF-1.4 finto contenuto"


@patch("etichette.requests.get")
def test_scarica_etichette_si_presenta_con_agent_nello_user_agent(mock_get, tmp_path, monkeypatch):
    """Agent Policy Amazon (BSA sez. 19): anche il DownloadURL, che non passa
    da spapi_client, è una richiesta ad Amazon e deve dire Agent/<nome>."""
    import spapi_client
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"payload": {"DownloadURL": "https://amazon-labels.example/x.pdf"}}
    mock_get.return_value = Mock(status_code=200, content=b"%PDF")

    scarica_etichette(client, "FBA1234ABCD", "Phy to Pet", cartoni=_CARTONI)

    user_agent = mock_get.call_args.kwargs["headers"]["User-Agent"]
    assert user_agent == spapi_client._USER_AGENT
    assert "Agent/" in user_agent


@patch("etichette.requests.get")
def test_scarica_etichette_usa_l_operazione_v0_col_confirmation_id(mock_get, tmp_path, monkeypatch):
    """getLabels è una delle operazioni v0 conservate: percorso /fba/inbound/v0,
    parametri in PascalCase, e l'identificativo è il shipmentConfirmationId."""
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"payload": {"DownloadURL": "https://amazon-labels.example/x.pdf"}}
    risposta_pdf = Mock()
    risposta_pdf.status_code = 200
    risposta_pdf.content = b"%PDF"
    mock_get.return_value = risposta_pdf

    scarica_etichette(client, "FBA1234ABCD", "Phy to Pet", cartoni=_CARTONI)

    client.get.assert_called_with(
        "/fba/inbound/v0/shipments/FBA1234ABCD/labels",
        params={"PageType": "PackageLabel_A4_4", "LabelType": "UNIQUE",
                "PackageLabelsToPrint": "FBA1234ABCDU000001"},
    )


@patch("etichette.requests.get")
def test_scarica_etichette_accetta_altri_formati_di_pagina(mock_get, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"payload": {"DownloadURL": "https://amazon-labels.example/x.pdf"}}
    risposta_pdf = Mock()
    risposta_pdf.status_code = 200
    risposta_pdf.content = b"%PDF"
    mock_get.return_value = risposta_pdf

    scarica_etichette(client, "FBA1234ABCD", "Phy to Pet",
                       tipo_pagina="PackageLabel_A4_4", tipo_etichetta="PALLET",
                       numero_pallet=2)

    assert client.get.call_args.kwargs["params"] == {
        "PageType": "PackageLabel_A4_4", "LabelType": "PALLET", "NumberOfPallets": 2,
    }


def test_scarica_etichette_senza_confirmation_id_alza_errore():
    client = Mock()
    with pytest.raises(SpApiError, match="shipment_confirmation_id mancante"):
        scarica_etichette(client, None, "Phy to Pet")
    client.get.assert_not_called()


def test_scarica_etichette_senza_url_alza_errore():
    client = Mock()
    client.get.return_value = {"payload": {}}
    with pytest.raises(SpApiError, match="nessun URL etichetta"):
        scarica_etichette(client, "FBA1234ABCD", "Phy to Pet", cartoni=_CARTONI)


@patch("etichette.requests.get")
def test_scarica_etichette_errore_di_rete_resta_dentro_il_contratto(mock_get, tmp_path, monkeypatch):
    """Il download non passa da spapi_client: senza try/except un timeout esce
    come requests.exceptions grezza, fuori dal contratto SpApiError. È l'ultimo
    passo, a spedizione già pagata: il messaggio deve dirlo, così nessuno rifà
    il giro per riavere le etichette."""
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"payload": {"DownloadURL": "https://amazon-labels.example/x.pdf"}}
    mock_get.side_effect = requests.exceptions.Timeout("Read timed out")
    with pytest.raises(SpApiError) as errore:
        scarica_etichette(client, "FBA1234ABCD", "Phy to Pet", cartoni=_CARTONI)
    messaggio = str(errore.value)
    assert "FBA1234ABCD" in messaggio
    assert "confermata" in messaggio
    assert "Read timed out" in messaggio


@patch("etichette.requests.get")
def test_scarica_etichette_download_fallito_alza_errore(mock_get, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"payload": {"DownloadURL": "https://amazon-labels.example/x.pdf"}}
    risposta_errore = Mock()
    risposta_errore.status_code = 403
    mock_get.return_value = risposta_errore
    with pytest.raises(SpApiError, match="403"):
        scarica_etichette(client, "FBA1234ABCD", "Phy to Pet", cartoni=_CARTONI)


def test_etichette_per_cartone_senza_elenco_si_ferma_prima_di_amazon():
    """Misurato dal vivo il 2026-08-29: LabelType=UNIQUE senza l'elenco dei
    cartoni viene respinto dallo schema Amazon con "Value null at
    'cartonIdList'". Meglio fermarsi qui, dicendo dove prendere l'elenco."""
    client = Mock()
    with pytest.raises(SpApiError, match="leggi_cartoni"):
        scarica_etichette(client, "FBA1234ABCD", "Phy to Pet")
    client.get.assert_not_called()


def test_etichette_pallet_senza_numero_si_ferma():
    client = Mock()
    with pytest.raises(SpApiError, match="numero di pallet"):
        scarica_etichette(client, "FBA1234ABCD", "Phy to Pet", tipo_etichetta="PALLET")
    client.get.assert_not_called()


def test_leggi_cartoni_segue_la_paginazione():
    """Una spedizione con molti cartoni arriva a pagine: fermarsi alla prima
    stamperebbe le etichette solo di una parte delle scatole."""
    client = Mock()
    client.get.side_effect = [
        {"boxes": [{"boxId": "FBA1U000001"}], "pagination": {"nextToken": "t2"}},
        {"boxes": [{"boxId": "FBA1U000002"}]},
    ]
    assert leggi_cartoni(client, "wf1", "sh1") == ["FBA1U000001", "FBA1U000002"]
    assert client.get.call_args_list[1].kwargs["params"]["paginationToken"] == "t2"


@patch("etichette.requests.get")
def test_anche_le_etichette_pallet_escono_quattro_per_foglio(mock_get, tmp_path, monkeypatch):
    """Un bancale vuole 4 etichette, una per fianco: 4 per foglio vuol dire un
    foglio solo da tagliare in quattro. Deciso da Mariano il 03/09/2026 dopo
    aver visto entrambi i formati stampati."""
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"payload": {"DownloadURL": "https://amazon-labels.example/x.pdf"}}
    risposta_pdf = Mock()
    risposta_pdf.status_code = 200
    risposta_pdf.content = b"%PDF"
    mock_get.return_value = risposta_pdf

    scarica_etichette(client, "FBA1234ABCD", "Arcipelago",
                       tipo_etichetta="PALLET", numero_pallet=1)

    assert client.get.call_args.kwargs["params"]["PageType"] == "PackageLabel_A4_4"


def test_col_corriere_convenzionato_il_foglio_porta_anche_l_etichetta_del_corriere():
    """Misurato dal vivo il 2026-09-03 su una spedizione UPS convenzionata Amazon:
    `PackageLabel_A4_4` torna un PDF con la sola etichetta FBA, senza quella
    del corriere. `PackageLabel_Plain_Paper_CarrierBottom` torna il foglio
    combinato, FBA sopra e corriere col tracking sotto — l'unico che il cliente
    può attaccare e spedire. Col corriere proprio l'etichetta del corriere non
    esiste e restano le 4 per foglio che il cliente vuole."""
    from etichette import formato_cartoni
    assert formato_cartoni(convenzionato=True) == "PackageLabel_Plain_Paper_CarrierBottom"
    assert formato_cartoni(convenzionato=False) == "PackageLabel_A4_4"


@patch("etichette.requests.get")
def test_scarica_etichette_salva_nella_cartella_indicata(mock_get, tmp_path, monkeypatch):
    """Con `cartella` il PDF va lì (la cartella PRIME del cliente), non sul Desktop."""
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"payload": {"DownloadURL": "https://amazon-labels.example/x.pdf"}}
    mock_get.return_value = Mock(status_code=200, content=b"%PDF")
    destinazione = tmp_path / "PRIME" / "2026-09-07 FBA1234ABCD"
    destinazione.mkdir(parents=True)

    percorso = scarica_etichette(client, "FBA1234ABCD", "Phy to Pet", cartoni=_CARTONI,
                                 cartella=str(destinazione))

    assert percorso == str(destinazione / "Etichette-Phy to Pet-FBA1234ABCD.pdf")
    assert os.path.exists(percorso)
    assert not (tmp_path / "Desktop").exists()


@patch("etichette.requests.get")
def test_un_nome_cliente_con_percorso_dentro_resta_sul_desktop(mock_get, tmp_path, monkeypatch):
    """Collaudo del 06/09: "Phy to Pet/../../" portava il PDF in $HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"payload": {"DownloadURL": "https://amazon-labels.example/x.pdf"}}
    mock_get.return_value = Mock(status_code=200, content=b"%PDF-1.4")
    percorso = scarica_etichette(client, "FBA1234ABCD", "Phy to Pet/../../", cartoni=_CARTONI)
    assert os.path.dirname(percorso) == str(tmp_path / "Desktop")
    assert os.path.exists(percorso)


def test_leggi_cartoni_completi_tiene_contenuto_e_quantita():
    """Per dichiarare i tracking non bastano gli id: serve sapere che cosa c'è
    dentro ogni cartone, perché il cliente manda le lettere di vettura
    raggruppate per prodotto e non per cartone."""
    client = Mock()
    client.get.return_value = {"boxes": [
        {"boxId": "FBA15MBX78RC000001", "items": [{"msku": "51NERO_FBA", "quantity": 12}]},
        {"boxId": "FBA15MBX78RC000002", "items": [{"msku": "51NERO_FBA", "quantity": 12}]},
    ]}
    cartoni = leggi_cartoni_completi(client, "wf1", "sh1")
    assert [c["boxId"] for c in cartoni] == ["FBA15MBX78RC000001", "FBA15MBX78RC000002"]
    assert cartoni[0]["items"] == [{"msku": "51NERO_FBA", "quantity": 12}]


def test_leggi_cartoni_completi_segue_la_paginazione():
    client = Mock()
    client.get.side_effect = [
        {"boxes": [{"boxId": "FBA1U000001"}], "pagination": {"nextToken": "t2"}},
        {"boxes": [{"boxId": "FBA1U000002"}]},
    ]
    assert [c["boxId"] for c in leggi_cartoni_completi(client, "wf1", "sh1")] == [
        "FBA1U000001", "FBA1U000002",
    ]
