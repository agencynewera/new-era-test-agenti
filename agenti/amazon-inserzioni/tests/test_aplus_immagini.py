from unittest.mock import Mock, patch
from aplus_immagini import carica_immagine
from spapi_client import SpApiError
import pytest
import hashlib
import base64


def test_carica_immagine_ritorna_l_upload_destination_id(tmp_path):
    file_immagine = tmp_path / "foto.jpg"
    file_immagine.write_bytes(b"contenuto finto jpeg")

    client = Mock()
    client.post.return_value = {"payload": {
        "uploadDestinationId": "aplus-media/sc/f3af8f70.jpg",
        "url": "https://aplus-media.s3.amazonaws.com/?policy=xyz&key=sc%2Ff3af8f70.jpg&x-amz-signature=abc",
        "headers": None,
    }}

    with patch("aplus_immagini.requests.post") as mock_s3_post:
        mock_s3_post.return_value = Mock(status_code=204, text="")
        upload_destination_id = carica_immagine(client, "APJ6JRA9NG5V4", str(file_immagine), "image/jpeg")

    assert upload_destination_id == "aplus-media/sc/f3af8f70.jpg"
    # la richiesta a SP-API passa dal client autenticato, sul resource fisso per l'A+
    assert client.post.call_args.args[0] == "/uploads/2020-11-01/uploadDestinations/aplus/2020-11-01/contentDocuments"
    assert client.post.call_args.kwargs["params"]["marketplaceIds"] == "APJ6JRA9NG5V4"
    assert client.post.call_args.kwargs["params"]["contentType"] == "image/jpeg"
    # verifica che il contentMD5 sia calcolato come base64(md5), non hex
    atteso_md5 = base64.b64encode(hashlib.md5(b"contenuto finto jpeg").digest()).decode()
    assert client.post.call_args.kwargs["params"]["contentMD5"] == atteso_md5
    # l'upload vero va all'host S3 restituito, coi parametri della policy come campi form
    url_chiamata, dati_form = mock_s3_post.call_args.args[0], mock_s3_post.call_args.kwargs["data"]
    assert url_chiamata == "https://aplus-media.s3.amazonaws.com/"
    assert dati_form == {"policy": "xyz", "key": "sc/f3af8f70.jpg", "x-amz-signature": "abc"}
    assert mock_s3_post.call_args.kwargs["files"]["file"] == b"contenuto finto jpeg"


def test_carica_immagine_post_con_agent_nello_user_agent(tmp_path):
    """Agent Policy Amazon (BSA sez. 19): l'upload va a S3 di Amazon senza
    passare da spapi_client. La policy S3 firma i campi del form, non gli
    header HTTP: lo User-Agent si può aggiungere senza invalidarla."""
    import spapi_client
    file_immagine = tmp_path / "foto.jpg"
    file_immagine.write_bytes(b"x")
    client = Mock()
    client.post.return_value = {"payload": {
        "uploadDestinationId": "aplus-media/sc/f3af8f70.jpg",
        "url": "https://aplus-media.s3.amazonaws.com/?policy=xyz",
        "headers": None,
    }}
    with patch("aplus_immagini.requests.post") as mock_s3_post:
        mock_s3_post.return_value = Mock(status_code=204, text="")
        carica_immagine(client, "APJ6JRA9NG5V4", str(file_immagine), "image/jpeg")

    user_agent = mock_s3_post.call_args.kwargs["headers"]["User-Agent"]
    assert user_agent == spapi_client._USER_AGENT
    assert "Agent/" in user_agent


def test_carica_immagine_put_aggiunge_lo_user_agent_senza_toccare_gli_header_di_amazon(tmp_path):
    import spapi_client
    file_immagine = tmp_path / "foto.jpg"
    file_immagine.write_bytes(b"x")
    client = Mock()
    headers_destinazione = {"Content-MD5": "abc123==", "x-amz-server-side-encryption": "AES256"}
    client.post.return_value = {"payload": {
        "uploadDestinationId": "aplus-media/sc/f3af8f70.jpg",
        "url": "https://esempio-bucket.s3.amazonaws.com/qualche/chiave.jpg",
        "headers": headers_destinazione,
    }}
    with patch("aplus_immagini.requests.put") as mock_s3_put:
        mock_s3_put.return_value = Mock(status_code=200, text="")
        carica_immagine(client, "APJ6JRA9NG5V4", str(file_immagine), "image/jpeg")

    assert mock_s3_put.call_args.kwargs["headers"] == {
        "Content-MD5": "abc123==", "x-amz-server-side-encryption": "AES256",
        "User-Agent": spapi_client._USER_AGENT}
    # il dizionario restituito da Amazon non si modifica sul posto
    assert headers_destinazione == {"Content-MD5": "abc123==", "x-amz-server-side-encryption": "AES256"}


def test_carica_immagine_upload_fallito_alza_spapierror(tmp_path):
    file_immagine = tmp_path / "foto.jpg"
    file_immagine.write_bytes(b"x")
    client = Mock()
    client.post.return_value = {"payload": {
        "uploadDestinationId": "aplus-media/sc/f3af8f70.jpg",
        "url": "https://aplus-media.s3.amazonaws.com/?policy=xyz",
        "headers": None,
    }}
    with patch("aplus_immagini.requests.post") as mock_s3_post:
        mock_s3_post.return_value = Mock(status_code=403, text="Forbidden")
        with pytest.raises(SpApiError, match="403"):
            carica_immagine(client, "APJ6JRA9NG5V4", str(file_immagine), "image/jpeg")


def test_carica_immagine_con_put_headers(tmp_path):
    file_immagine = tmp_path / "foto.jpg"
    file_immagine.write_bytes(b"contenuto finto jpeg")

    client = Mock()
    headers_destinazione = {"Content-MD5": "abc123==", "x-amz-server-side-encryption": "AES256"}
    client.post.return_value = {"payload": {
        "uploadDestinationId": "aplus-media/sc/f3af8f70.jpg",
        "url": "https://esempio-bucket.s3.amazonaws.com/qualche/chiave.jpg",
        "headers": headers_destinazione,
    }}

    with patch("aplus_immagini.requests.put") as mock_s3_put:
        mock_s3_put.return_value = Mock(status_code=200, text="")
        upload_destination_id = carica_immagine(client, "APJ6JRA9NG5V4", str(file_immagine), "image/jpeg")

    assert upload_destination_id == "aplus-media/sc/f3af8f70.jpg"
    # quando non c'è policy nella query string, usa PUT con gli header
    assert mock_s3_put.call_args.args[0] == "https://esempio-bucket.s3.amazonaws.com/qualche/chiave.jpg"
    assert mock_s3_put.call_args.kwargs["data"] == b"contenuto finto jpeg"
    assert headers_destinazione.items() <= mock_s3_put.call_args.kwargs["headers"].items()
