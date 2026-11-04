# tests/test_spapi_client.py
import os
import platform
import re
from urllib.parse import unquote
from unittest.mock import patch, Mock
import pytest
import spapi_client
from spapi_client import SpApiClient, SpApiError


def _resp(status=200, json_body=None):
    m = Mock()
    m.status_code = status
    m.json.return_value = json_body or {}
    m.text = str(json_body or {})
    return m


def test_refresh_token_vuoto_alza_spapierror():
    with pytest.raises(SpApiError, match="refresh_token mancante"):
        SpApiClient("")


def test_regione_sconosciuta_alza_spapierror():
    with pytest.raises(SpApiError, match="regione SP-API sconosciuta"):
        SpApiClient("Atzr|refresh", region="us")


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.post")
def test_access_token_richiesto_una_volta_sola(mock_post):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    c = SpApiClient("Atzr|refresh")
    assert c.access_token == "Atza|tok"
    assert c.access_token == "Atza|tok"
    assert mock_post.call_count == 1


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.put")
@patch("spapi_client.requests.post")
def test_put_manda_il_payload_e_torna_il_corpo_anche_su_invalid(mock_post, mock_put):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_put.return_value = _resp(200, {
        "sku": "ABC", "status": "INVALID",
        "issues": [{"code": "4000001", "message": "attributo mancante", "severity": "ERROR"}],
    })
    c = SpApiClient("Atzr|refresh")
    esito = c.put(
        "/listings/2021-08-01/items/SELLER1/ABC",
        json_body={"productType": "LUGGAGE", "attributes": {}},
        params={"marketplaceIds": "APJ6JRA9NG5V4"},
    )
    assert esito["status"] == "INVALID"
    assert esito["issues"][0]["severity"] == "ERROR"
    chiamata = mock_put.call_args
    assert chiamata.args[0] == "https://sellingpartnerapi-eu.amazon.com/listings/2021-08-01/items/SELLER1/ABC"
    assert chiamata.kwargs["json"] == {"productType": "LUGGAGE", "attributes": {}}
    assert chiamata.kwargs["params"] == {"marketplaceIds": "APJ6JRA9NG5V4"}
    assert chiamata.kwargs["headers"] == {"x-amz-access-token": "Atza|tok",
                                          "User-Agent": spapi_client._USER_AGENT}


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.patch")
@patch("spapi_client.requests.post")
def test_patch_su_429_riprova_con_backoff(mock_post, mock_patch, mock_sleep):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_patch.side_effect = [_resp(429), _resp(200, {"sku": "ABC", "status": "ACCEPTED", "issues": []})]
    c = SpApiClient("Atzr|refresh")
    esito = c.patch("/listings/2021-08-01/items/SELLER1/ABC", json_body={"patches": []})
    assert esito["status"] == "ACCEPTED"
    assert mock_patch.call_count == 2
    assert mock_sleep.call_args_list[0].args == (1,)


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_errore_di_trasporto_alza_spapierror_con_status_code(mock_post, mock_get):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _resp(403, {"errors": [{"message": "Access denied"}]})
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="403"):
        c.get("/listings/2021-08-01/items/SELLER1/ABC")


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_errore_con_corpo_non_json_non_esplode(mock_post, mock_get, mock_sleep):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    errore_html = Mock()
    errore_html.status_code = 503
    errore_html.json.side_effect = ValueError("Expecting value")
    errore_html.text = "<html>Bad Gateway</html>"
    mock_get.return_value = errore_html
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="503"):
        c.get("/qualcosa")


# --- caricamento credenziali da ~/scripts/.env (condiviso con le altre skill Amazon) ---


def test_carica_env_riempie_le_variabili_mancanti(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# commento da ignorare\n"
        "\n"
        "SPAPI_LWA_CLIENT_ID=finto-id\n"
        "SPAPI_REGION=eu\n"
        "RIGA_SENZA_UGUALE\n"
    )
    monkeypatch.setattr(spapi_client, "_ENV_FILE", str(env))
    monkeypatch.delenv("SPAPI_LWA_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPAPI_REGION", raising=False)

    spapi_client._carica_env_da_file()

    assert os.environ["SPAPI_LWA_CLIENT_ID"] == "finto-id"
    assert os.environ["SPAPI_REGION"] == "eu"


def test_carica_env_non_sovrascrive_l_ambiente_reale(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SPAPI_LWA_CLIENT_ID=quello-del-file\n")
    monkeypatch.setattr(spapi_client, "_ENV_FILE", str(env))
    monkeypatch.setenv("SPAPI_LWA_CLIENT_ID", "quello-gia-nell-ambiente")

    spapi_client._carica_env_da_file()

    assert os.environ["SPAPI_LWA_CLIENT_ID"] == "quello-gia-nell-ambiente"


def test_carica_env_ignora_i_valori_vuoti(tmp_path, monkeypatch):
    """Un .env appena creato dal template ha le chiavi ma non i valori: non
    deve fissare stringhe vuote che poi mascherano l'ambiente reale."""
    env = tmp_path / ".env"
    env.write_text("SPAPI_LWA_CLIENT_SECRET=\n")
    monkeypatch.setattr(spapi_client, "_ENV_FILE", str(env))
    monkeypatch.delenv("SPAPI_LWA_CLIENT_SECRET", raising=False)

    spapi_client._carica_env_da_file()

    assert "SPAPI_LWA_CLIENT_SECRET" not in os.environ


def test_carica_env_senza_file_non_esplode(tmp_path, monkeypatch):
    monkeypatch.setattr(spapi_client, "_ENV_FILE", str(tmp_path / "non-esiste.env"))
    spapi_client._carica_env_da_file()  # non deve alzare


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.post")
def test_post_manda_il_payload_e_torna_il_corpo(mock_post):
    mock_post.side_effect = [
        _resp(200, {"access_token": "Atza|tok", "expires_in": 3600}),
        _resp(202, {"reportId": "123"}),
    ]
    c = SpApiClient("Atzr|refresh")
    esito = c.post("/reports/2021-06-30/reports", json_body={"reportType": "GET_MERCHANT_LISTINGS_ALL_DATA"})
    assert esito == {"reportId": "123"}


# --- credenziali per-cliente (dal 31/08/2026, vedi reference_spapi_credenziali_ruoli) ---


def test_crea_client_usa_il_refresh_token_del_prefisso_cliente(monkeypatch):
    monkeypatch.setenv("TECNOCOLD_SPAPI_REFRESH_TOKEN", "Atzr|tecnocold")
    monkeypatch.setenv("TECNOCOLD_SPAPI_REGION", "eu")
    c = spapi_client.crea_client("Tecno Cold")
    assert c.refresh_token == "Atzr|tecnocold"
    assert c.client_prefix == "TECNOCOLD"
    assert c.base == "https://sellingpartnerapi-eu.amazon.com"


def test_crea_client_senza_refresh_token_alza_spapierror(monkeypatch):
    monkeypatch.delenv("RIVEXTAUTO_SPAPI_REFRESH_TOKEN", raising=False)
    with pytest.raises(SpApiError, match="RIVEXTAUTO_SPAPI_REFRESH_TOKEN mancante"):
        spapi_client.crea_client("Rivextauto")


def test_seller_id_di_usa_il_prefisso_cliente(monkeypatch):
    monkeypatch.setenv("TECNOCOLD_SPAPI_SELLER_ID", "A1TECNOCOLD123")
    assert spapi_client.seller_id_di("Tecno Cold") == "A1TECNOCOLD123"


def test_seller_id_di_mancante_alza_spapierror_con_istruzioni(monkeypatch):
    monkeypatch.delenv("RIVEXTAUTO_SPAPI_SELLER_ID", raising=False)
    with pytest.raises(SpApiError, match="RIVEXTAUTO_SPAPI_SELLER_ID mancante"):
        spapi_client.seller_id_di("Rivextauto")


@patch("spapi_client.requests.post")
def test_access_token_di_un_client_con_prefisso_usa_le_credenziali_del_prefisso(mock_post, monkeypatch):
    monkeypatch.setenv("TECNOCOLD_SPAPI_LWA_CLIENT_ID", "cid-tecnocold")
    monkeypatch.setenv("TECNOCOLD_SPAPI_LWA_CLIENT_SECRET", "csec-tecnocold")
    monkeypatch.delenv("SPAPI_LWA_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPAPI_LWA_CLIENT_SECRET", raising=False)
    mock_post.return_value = _resp(200, {"access_token": "Atza|tecnocold", "expires_in": 3600})

    c = SpApiClient("Atzr|refresh", client_prefix="TECNOCOLD")
    assert c.access_token == "Atza|tecnocold"
    assert mock_post.call_args.kwargs["data"]["client_id"] == "cid-tecnocold"


def test_access_token_senza_prefisso_resta_compatibile_con_le_vecchie_credenziali_globali(monkeypatch):
    monkeypatch.setenv("SPAPI_LWA_CLIENT_ID", "cid-globale")
    monkeypatch.setenv("SPAPI_LWA_CLIENT_SECRET", "csec-globale")
    with patch("spapi_client.requests.post") as mock_post:
        mock_post.return_value = _resp(200, {"access_token": "Atza|globale", "expires_in": 3600})
        c = SpApiClient("Atzr|refresh")
        assert c.access_token == "Atza|globale"


# --- percorso_listing: lo SKU e' UN segmento del path, non una struttura ---

def test_percorso_listing_codifica_la_barra_dello_sku():
    # Dal vivo 2026-09-03 (Tecno Cold, MA1/4SX-R): con la barra grezza il path
    # acquista un segmento, non combacia con nessuna rotta e il gateway Amazon
    # risponde 403 Unauthorized. Sembra un problema di permessi e non lo e':
    # stesse credenziali, stesso token, passa appena si codifica.
    assert spapi_client.percorso_listing("SELLER1", "MA1/4SX-R") == \
        "/listings/2021-08-01/items/SELLER1/MA1%2F4SX-R"


@pytest.mark.parametrize("sku", ["ABC-1", "MA1/4SX-R", "R290-2+R", "R290 12", "50%-OFF", "a#b", "a?b"])
def test_percorso_listing_lo_sku_sopravvive_al_round_trip(sku):
    # L'invariante che conta: qualunque carattere contenga, lo SKU che Amazon
    # legge deve essere quello che gli abbiamo dato. Non fissa la forma sul filo
    # (%2B o + letterale sono equivalenti in un path segment), fissa il senso.
    segmento = spapi_client.percorso_listing("SELLER1", sku).rsplit("/", 1)[-1]
    assert unquote(segmento) == sku


def test_percorso_listing_sku_vuoto_alza_spapierror():
    # Lo SKU vuoto non e' una regola di Amazon: e' la nostra struttura che si
    # rompe (il path finirebbe su .../items/SELLER1/, un'altra rotta). Su una
    # PATCH vorrebbe dire scrivere a un indirizzo non scelto: fermarsi qui.
    with pytest.raises(SpApiError, match="sku vuoto"):
        spapi_client.percorso_listing("SELLER1", "   ")


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.delete")
@patch("spapi_client.requests.post")
def test_delete_manda_i_parametri_e_torna_il_corpo(mock_post, mock_delete):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_delete.return_value = _resp(200, {
        "sku": "ABC", "status": "ACCEPTED", "submissionId": "sub1", "issues": [],
    })
    c = SpApiClient("Atzr|refresh")
    esito = c.delete(
        "/listings/2021-08-01/items/SELLER1/ABC",
        params={"marketplaceIds": "APJ6JRA9NG5V4"},
    )
    assert esito["status"] == "ACCEPTED"
    assert esito["submissionId"] == "sub1"
    chiamata = mock_delete.call_args
    assert chiamata.args[0] == "https://sellingpartnerapi-eu.amazon.com/listings/2021-08-01/items/SELLER1/ABC"
    assert chiamata.kwargs["params"] == {"marketplaceIds": "APJ6JRA9NG5V4"}
    assert chiamata.kwargs["headers"] == {"x-amz-access-token": "Atza|tok",
                                          "User-Agent": spapi_client._USER_AGENT}


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.delete")
@patch("spapi_client.requests.post")
def test_delete_alza_spapierror_su_403(mock_post, mock_delete):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_delete.return_value = _resp(403, {"errors": [{"message": "Unauthorized"}]})
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="403"):
        c.delete("/listings/2021-08-01/items/SELLER1/ABC",
                 params={"marketplaceIds": "APJ6JRA9NG5V4"})


# --- trasporto, token e orologio: collaudo di sicurezza 06/09/2026 ---
import requests


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_eccezione_di_rete_di_requests_diventa_spapierror(mock_post, mock_get):
    # Timeout e connessioni cadute non hanno uno status: sfuggivano a ogni
    # "except SpApiError" a valle, lasciando il registro su "in corso" e
    # facendo crollare vai a meta' lotto.
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.side_effect = requests.exceptions.ReadTimeout("lento")
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="ReadTimeout"):
        c.get("/listings/2021-08-01/items/SELLER1/ABC")


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.post")
def test_eccezione_di_rete_sul_token_lwa_diventa_spapierror(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError("dns")
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="LWA"):
        c.access_token


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_su_5xx_riprova_con_backoff_e_poi_passa(mock_post, mock_get, mock_sleep):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.side_effect = [_resp(503, {"errors": [{"message": "Service Unavailable"}]}), _resp(200, {"ok": 1})]
    c = SpApiClient("Atzr|refresh")
    assert c.get("/qualcosa") == {"ok": 1}
    assert mock_get.call_count == 2
    assert mock_sleep.call_args_list[0].args == (1,)


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_su_5xx_persistente_alza_dopo_i_tentativi(mock_post, mock_get, mock_sleep):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _resp(502, {"errors": [{"message": "Bad Gateway"}]})
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="502"):
        c.get("/qualcosa")
    assert mock_get.call_count == SpApiClient._MAX_TENTATIVI


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_su_403_rinnova_il_token_e_riprova_una_volta(mock_post, mock_get):
    # Un token scaduto a giro iniziato (sonno del Mac) risponde 403 "expired":
    # un rinnovo e una ripetizione, non uno SKU saltato.
    mock_post.side_effect = [_resp(200, {"access_token": "Atza|vecchio", "expires_in": 3600}),
                             _resp(200, {"access_token": "Atza|nuovo", "expires_in": 3600})]
    mock_get.side_effect = [_resp(403, {"errors": [{"message": "The access token you provided has expired."}]}),
                            _resp(200, {"ok": 1})]
    c = SpApiClient("Atzr|refresh")
    assert c.get("/qualcosa") == {"ok": 1}
    assert mock_post.call_count == 2
    assert mock_get.call_args_list[1].kwargs["headers"] == {"x-amz-access-token": "Atza|nuovo",
                                                            "User-Agent": spapi_client._USER_AGENT}


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_su_403_persistente_alza_dopo_un_solo_rinnovo(mock_post, mock_get):
    # Se il 403 resta anche col token nuovo e' un permesso negato davvero.
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _resp(403, {"errors": [{"message": "Unauthorized"}]})
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="403"):
        c.get("/qualcosa")
    assert mock_get.call_count == 2
    assert mock_post.call_count == 2


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.post")
def test_access_token_rinnovato_anche_se_il_mac_ha_dormito(mock_post, monkeypatch):
    # time.monotonic() su macOS non avanza nel sonno: un'ora di stand-by
    # valeva due minuti di orologio e il token scaduto veniva creduto buono.
    # La scadenza si misura sull'orologio da parete, l'unico che vede la
    # stessa ora di Amazon.
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    orologio = iter([1000.0, 5000.0, 5000.0])
    monkeypatch.setattr(spapi_client.time, "time", lambda: next(orologio))
    c = SpApiClient("Atzr|refresh")
    c.access_token
    c.access_token
    assert mock_post.call_count == 2


def test_carica_env_prende_solo_le_chiavi_spapi(tmp_path, monkeypatch):
    # ~/scripts/.env e' condiviso con bot, invii mail e altri connettori: qui
    # servono solo le credenziali SP-API, il resto non entra nel processo.
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=segreto-altrui\nPHYTOPET_SPAPI_SELLER_ID=A1PHYTOPET\n")
    monkeypatch.setattr(spapi_client, "_ENV_FILE", str(env))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PHYTOPET_SPAPI_SELLER_ID", raising=False)

    spapi_client._carica_env_da_file()

    assert os.environ["PHYTOPET_SPAPI_SELLER_ID"] == "A1PHYTOPET"
    assert "TELEGRAM_BOT_TOKEN" not in os.environ


def test_crea_client_normalizza_gli_accenti_come_il_registro(monkeypatch):
    # Stessa classe di equivalenza di registro.slug_cliente: solo ASCII
    # alfanumerico, cosi' credenziali, registro e id lotto concordano.
    monkeypatch.setenv("CAFNOIR_SPAPI_REFRESH_TOKEN", "Atzr|caf")
    assert spapi_client.crea_client("Café Noir").client_prefix == "CAFNOIR"
    monkeypatch.setenv("CAFNOIR_SPAPI_SELLER_ID", "A1CAF")
    assert spapi_client.seller_id_di("Café Noir") == "A1CAF"


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.delete")
@patch("spapi_client.requests.patch")
@patch("spapi_client.requests.put")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_ogni_richiesta_dichiara_l_agente_nello_user_agent(mock_post, mock_get, mock_put,
                                                          mock_patch, mock_delete):
    """Agent Policy Amazon (BSA sez. 19, dal 04/03/2026): ogni richiesta HTTP di un
    agente porta `Agent/<nome>` nello user agent. La prima parte è il formato che
    SP-API chiede già (`NomeApp/versione (Language=...; Platform=...)`). Vale anche
    per il token LWA: è un servizio Amazon anche quello."""
    mock_post.side_effect = [
        _resp(200, {"access_token": "Atza|tok", "expires_in": 3600}),   # LWA
        _resp(200, {"ok": True}),
    ]
    for finto in (mock_get, mock_put, mock_patch, mock_delete):
        finto.return_value = _resp(200, {"ok": True})
    c = SpApiClient("Atzr|refresh")
    percorso = "/listings/2021-08-01/items/A1SELLER/SKU1"
    c.get(percorso)
    c.put(percorso, json_body={})
    c.patch(percorso, json_body={})
    c.post("/aplus/2020-11-01/contentDocuments", json_body={})
    c.delete(percorso)
    atteso = re.compile(r"NewEra-amazon-inserzioni/\S+ \(Language=Python/"
                        + re.escape(platform.python_version())
                        + r"; Platform=\S+\) Agent/NewEra-amazon-inserzioni")
    chiamate = (mock_post.call_args_list + mock_get.call_args_list + mock_put.call_args_list
                + mock_patch.call_args_list + mock_delete.call_args_list)
    assert len(chiamate) == 6   # token LWA, POST, GET, PUT, PATCH, DELETE
    for chiamata in chiamate:
        ua = (chiamata.kwargs.get("headers") or {}).get("User-Agent", "")
        assert atteso.fullmatch(ua), f"{chiamata.args[0]}: user agent {ua!r}"
