# tests/test_spapi_client.py
import itertools
import logging
import os
import platform
import re
from unittest.mock import patch, Mock
import pytest
import requests
import spapi_client
from spapi_client import (
    SpApiClient,
    SpApiError,
    motivo_fallimento,
    _carica_env_da_file,
)


def _resp(status=200, json_body=None, headers=None):
    m = Mock()
    m.status_code = status
    m.json.return_value = json_body or {}
    m.text = str(json_body or {})
    # Un dict vero, non un Mock: `headers.get(...)` su un Mock torna un Mock,
    # che è sempre vero, e farebbe passare per rate limit un oggetto finto.
    m.headers = headers or {}
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
@patch("spapi_client.requests.post")
def test_post_manda_il_payload_e_torna_il_corpo(mock_post):
    mock_post.side_effect = [
        _resp(200, {"access_token": "Atza|tok", "expires_in": 3600}),
        _resp(200, {"inboundPlanId": "wf1", "operationId": "op1"}),
    ]
    c = SpApiClient("Atzr|refresh")
    esito = c.post("/inbound/fba/2024-03-20/inboundPlans", json_body={"name": "Spedizione test"})
    assert esito == {"inboundPlanId": "wf1", "operationId": "op1"}
    chiamata = mock_post.call_args
    assert chiamata.args[0] == "https://sellingpartnerapi-eu.amazon.com/inbound/fba/2024-03-20/inboundPlans"
    assert chiamata.kwargs["json"] == {"name": "Spedizione test"}
    assert chiamata.kwargs["headers"] == {"x-amz-access-token": "Atza|tok",
                                          "User-Agent": spapi_client._USER_AGENT}


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_get_su_429_riprova_con_backoff(mock_post, mock_get, mock_sleep):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.side_effect = [_resp(429), _resp(200, {"ok": True})]
    c = SpApiClient("Atzr|refresh")
    esito = c.get("/inbound/fba/2024-03-20/inboundPlans/wf1")
    assert esito == {"ok": True}
    assert mock_get.call_count == 2
    assert mock_sleep.call_args_list[0].args == (1,)


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.post")
def test_post_in_timeout_alza_spapierror_con_esito_incerto(mock_post, mock_sleep):
    """Un timeout su una POST è il caso grave: il socket può scadere DOPO che
    Amazon ha già elaborato la richiesta, quindi i soldi sono spesi. Deve
    arrivare SpApiError — non il Timeout grezzo, che sfuggirebbe
    all'orchestratore e farebbe crashare il giro senza scrivere nel registro —
    e il messaggio deve dichiarare l'esito incerto. Nessun ritentativo
    automatico: riprovare duplicherebbe un'operazione a pagamento."""
    mock_post.side_effect = [
        _resp(200, {"access_token": "Atza|tok", "expires_in": 3600}),
        requests.exceptions.Timeout("Read timed out"),
    ]
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError) as errore:
        c.post("/inbound/fba/2024-03-20/inboundPlans/wf1/placementOptions/po1/confirmation")
    messaggio = str(errore.value)
    assert "INCERTO" in messaggio.upper()
    assert "Seller Central" in messaggio
    assert "/placementOptions/po1/confirmation" in messaggio
    # 1 chiamata al token LWA + 1 sola POST verso SP-API: mai ritentata
    assert mock_post.call_count == 2
    mock_sleep.assert_not_called()


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.post")
def test_errore_di_rete_sul_token_lwa_alza_spapierror(mock_post, mock_sleep):
    """Il token si prende dentro il ciclo di _request: anche lì un errore di
    rete deve diventare SpApiError. Qui però l'esito NON è incerto — la
    richiesta ad Amazon non è mai partita, quindi non c'è nulla da verificare."""
    mock_post.side_effect = requests.exceptions.ConnectionError("DNS fallito")
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="token LWA") as errore:
        c.post("/inbound/fba/2024-03-20/inboundPlans", json_body={"name": "x"})
    assert "Nessuna richiesta" in str(errore.value)
    assert "INCERTO" not in str(errore.value).upper()
    assert mock_post.call_count == 1


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_get_su_errore_di_rete_riprova_con_backoff(mock_post, mock_get, mock_sleep):
    """Una GET non spende e non modifica nulla: ritentarla è sicuro, con lo
    stesso backoff dei 429."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.side_effect = [
        requests.exceptions.ConnectionError("Name or service not known"),
        _resp(200, {"ok": True}),
    ]
    c = SpApiClient("Atzr|refresh")
    assert c.get("/inbound/fba/2024-03-20/inboundPlans/wf1") == {"ok": True}
    assert mock_get.call_count == 2
    assert mock_sleep.call_args_list[0].args == (1,)


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_get_con_rete_sempre_giu_alza_spapierror(mock_post, mock_get, mock_sleep):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.side_effect = requests.exceptions.ConnectionError("rete assente")
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="errore di rete"):
        c.get("/qualcosa")
    assert mock_get.call_count == 5


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_attendi_operazione_regge_un_errore_di_rete_nel_polling(mock_post, mock_get, mock_sleep):
    """Il polling passa da self.get: un buco di rete a metà attesa non deve
    far crashare un giro in cui i soldi sono già stati spesi."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.side_effect = [
        requests.exceptions.ConnectionError("connessione interrotta"),
        _resp(200, {"operationId": "op1", "operationStatus": "SUCCESS"}),
    ]
    c = SpApiClient("Atzr|refresh")
    assert c.attendi_operazione("op1")["operationStatus"] == "SUCCESS"


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_errore_di_trasporto_alza_spapierror_con_status_code(mock_post, mock_get):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _resp(403, {"errors": [{"message": "Access denied"}]})
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="403"):
        c.get("/inbound/fba/2024-03-20/inboundPlans/wf1")


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_errore_con_corpo_non_json_non_esplode(mock_post, mock_get):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    errore_html = Mock()
    errore_html.status_code = 503
    errore_html.json.side_effect = ValueError("Expecting value")
    errore_html.text = "<html>Bad Gateway</html>"
    mock_get.return_value = errore_html
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="503"):
        c.get("/qualcosa")


def _corpo_illeggibile():
    """2xx con un corpo che non è JSON: r.json() alza ValueError, che non è
    SpApiError e sfuggirebbe al contratto d'errore."""
    m = Mock()
    m.status_code = 200
    m.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
    m.text = "<html>502 Bad Gateway</html>"
    return m


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.post")
def test_post_con_2xx_illeggibile_alza_spapierror_con_esito_incerto(mock_post, mock_sleep):
    """Successo HTTP ma corpo non decodificabile: sulle due chiamate che
    spendono significa soldi impegnati e crash grezzo, senza traccia. Deve
    uscire SpApiError, e il messaggio deve dichiarare l'esito incerto."""
    mock_post.side_effect = [
        _resp(200, {"access_token": "Atza|tok", "expires_in": 3600}),
        _corpo_illeggibile(),
    ]
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError) as errore:
        c.post("/inbound/fba/2024-03-20/inboundPlans/wf1/placementOptions/po1/confirmation")
    messaggio = str(errore.value)
    assert "ESITO INCERTO" in messaggio
    assert "Seller Central" in messaggio
    assert "/placementOptions/po1/confirmation" in messaggio
    # mai ritentata da sola: duplicherebbe un'operazione a pagamento
    assert mock_post.call_count == 2


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_get_con_2xx_illeggibile_alza_spapierror_senza_esito_incerto(mock_post, mock_get, mock_sleep):
    """Rileggere è innocuo: una GET illeggibile è un errore, non un allarme."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _corpo_illeggibile()
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError) as errore:
        c.get("/inbound/fba/2024-03-20/inboundPlans/wf1")
    assert "INCERTO" not in str(errore.value).upper()


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_corpo_json_di_tipo_inatteso_alza_spapierror(mock_post, mock_get, mock_sleep):
    """JSON valido ma lista invece di oggetto: senza controllo il corpo passa
    e scoppia più in là con un TypeError/AttributeError fuori contratto."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _resp(200, [{"operationStatus": "SUCCESS"}])
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError):
        c.attendi_operazione("op1")


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.post")
def test_risposta_lwa_illeggibile_alza_spapierror(mock_post):
    """Anche il token si prende dentro _request: un 200 senza access_token
    leggibile deve restare SpApiError, non un ValueError/KeyError grezzo."""
    mock_post.return_value = _corpo_illeggibile()
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="LWA"):
        c.post("/inbound/fba/2024-03-20/inboundPlans", json_body={"name": "x"})


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.post")
def test_post_con_corpo_troncato_a_meta_lettura_alza_spapierror(mock_post, mock_sleep):
    """La connessione può cadere mentre si LEGGE il corpo, non solo mentre si
    manda la richiesta: lì r.json() alza una requests.exceptions, non un
    ValueError. Su una POST che spende deve comunque uscire SpApiError con
    l'esito incerto, non un crash grezzo."""
    troncata = Mock()
    troncata.status_code = 200
    troncata.json.side_effect = requests.exceptions.ChunkedEncodingError("connessione chiusa")
    mock_post.side_effect = [
        _resp(200, {"access_token": "Atza|tok", "expires_in": 3600}),
        troncata,
    ]
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError) as errore:
        c.post("/inbound/fba/2024-03-20/inboundPlans/wf1/transportationOptions/confirmation")
    assert "ESITO INCERTO" in str(errore.value)
    assert mock_post.call_count == 2


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.post")
def test_errore_con_corpo_illeggibile_non_sostituisce_l_errore_vero(mock_post, mock_sleep):
    """Il dettaglio del corpo serve al messaggio, ma non deve alzare a sua
    volta: se la connessione cade mentre lo si legge, un crash grezzo
    nasconderebbe lo status code, che è l'informazione utile."""
    rotta = Mock()
    rotta.status_code = 503
    rotta.json.side_effect = requests.exceptions.ChunkedEncodingError("connessione chiusa")
    type(rotta).text = property(
        lambda self: (_ for _ in ()).throw(requests.exceptions.ConnectionError("corpo perso"))
    )
    mock_post.side_effect = [
        _resp(200, {"access_token": "Atza|tok", "expires_in": 3600}),
        rotta,
    ]
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="503"):
        c.post("/inbound/fba/2024-03-20/inboundPlans", json_body={"name": "x"})


class _OrologioFinto:
    """Orologio che avanza solo quando si dorme: rende deterministico quanto
    costano davvero i ritentativi dentro il ciclo di polling."""

    def __init__(self):
        self.trascorso = 0.0

    def ora(self):
        return self.trascorso

    def dormi(self, secondi):
        self.trascorso += secondi


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_attendi_operazione_non_mente_sul_tempo_atteso(mock_post, mock_get):
    """I ritentativi interni al polling (429 e buchi di rete) allungano
    l'attesa reale ben oltre `attesa_max_s`: il messaggio di timeout non può
    continuare a dichiarare il tetto come se fosse il tempo trascorso."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    in_corso = _resp(200, {"operationId": "op1", "operationStatus": "IN_PROGRESS"})
    caduta = requests.exceptions.ConnectionError("connessione persa")
    mock_get.side_effect = itertools.cycle([caduta, caduta, caduta, caduta, in_corso])
    orologio = _OrologioFinto()
    with patch("spapi_client.time.sleep", orologio.dormi), \
            patch("spapi_client.time.monotonic", orologio.ora):
        c = SpApiClient("Atzr|refresh")
        with pytest.raises(SpApiError, match="non conclusa") as errore:
            c.attendi_operazione("op1", attesa_max_s=10)
    assert orologio.trascorso > 10, "l'attesa reale deve aver superato il tetto"
    assert f"{int(orologio.trascorso)}s" in str(errore.value)


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_attendi_operazione_ritenta_finche_non_success(mock_post, mock_get, mock_sleep):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.side_effect = [
        _resp(200, {"operationId": "op1", "operationStatus": "IN_PROGRESS"}),
        _resp(200, {"operationId": "op1", "operationStatus": "SUCCESS"}),
    ]
    c = SpApiClient("Atzr|refresh")
    esito = c.attendi_operazione("op1")
    assert esito["operationStatus"] == "SUCCESS"
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0].args[0] == "https://sellingpartnerapi-eu.amazon.com/inbound/fba/2024-03-20/operations/op1"


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_attendi_operazione_torna_failed_senza_alzare_errore(mock_post, mock_get):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _resp(200, {"operationId": "op1", "operationStatus": "FAILED",
                                         "operationProblems": [{"message": "SKU non trovato"}]})
    c = SpApiClient("Atzr|refresh")
    esito = c.attendi_operazione("op1")
    assert esito["operationStatus"] == "FAILED"
    assert esito["operationProblems"][0]["message"] == "SKU non trovato"


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_attendi_operazione_alza_errore_dopo_timeout(mock_post, mock_get, mock_sleep):
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _resp(200, {"operationId": "op1", "operationStatus": "IN_PROGRESS"})
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="non conclusa"):
        c.attendi_operazione("op1", attesa_max_s=10)


def test_motivo_fallimento_unisce_i_messaggi_presenti():
    esito = {"operationProblems": [{"message": "SKU non trovato"}, {"message": "quantità zero"}]}
    assert motivo_fallimento(esito) == "SKU non trovato; quantità zero"


def test_motivo_fallimento_senza_problemi_ha_il_fallback():
    assert motivo_fallimento({"operationStatus": "FAILED"}) == "motivo non specificato"
    assert motivo_fallimento({"operationProblems": []}) == "motivo non specificato"


def test_motivo_fallimento_messaggi_vuoti_non_diventano_un_separatore():
    """Unire due messaggi vuoti dà '; ', che è vero: senza scartarli prima,
    il fallback non scatterebbe e il motivo sarebbe un punto e virgola."""
    esito = {"operationProblems": [{"message": ""}, {"message": "   "}, {"code": "X"}]}
    assert motivo_fallimento(esito) == "motivo non specificato"


def test_motivo_fallimento_scarta_i_vuoti_e_tiene_i_pieni():
    esito = {"operationProblems": [{"message": ""}, {"message": "indirizzo non valido"}]}
    assert motivo_fallimento(esito) == "indirizzo non valido"


def test_motivo_fallimento_regge_operationproblems_a_none():
    assert motivo_fallimento({"operationProblems": None}) == "motivo non specificato"


def _leggi_env(tmp_path, contenuto, chiave):
    """Fa girare _carica_env_da_file su un .env finto e torna il valore letto."""
    percorso = tmp_path / ".env"
    percorso.write_text(contenuto)
    with patch.dict("os.environ", {}, clear=False):
        os.environ.pop(chiave, None)
        with patch("spapi_client._ENV_FILE", str(percorso)):
            _carica_env_da_file()
        return os.environ.get(chiave)


def test_env_scarta_il_commento_in_coda_alla_riga(tmp_path):
    """Un commento dopo il valore finirebbe dentro la credenziale: l'unico
    sintomo sarebbe un 401 invalid_client opaco, senza indizi sulla causa."""
    letto = _leggi_env(
        tmp_path,
        "SPAPI_TEST_CLIENT_ID=abc123  # id Phy to Pet\n",
        "SPAPI_TEST_CLIENT_ID",
    )
    assert letto == "abc123"


def test_env_valore_fra_virgolette_puo_contenere_il_cancelletto(tmp_path):
    letto = _leggi_env(
        tmp_path,
        'SPAPI_TEST_SECRET="ab#cd"  # segreto del client\n',
        "SPAPI_TEST_SECRET",
    )
    assert letto == "ab#cd"


def test_env_valore_che_inizia_per_cancelletto_non_viene_svuotato(tmp_path):
    """Una riga di valore che comincia con '#' non è un commento: il commento
    di riga intera è già scartato prima. Troncarla qui svuota una credenziale
    legittima, e l'unico sintomo è un errore LWA opaco."""
    letto = _leggi_env(
        tmp_path,
        "SPAPI_TEST_SECRET=#abc123\n",
        "SPAPI_TEST_SECRET",
    )
    assert letto == "#abc123"


def test_env_cancelletto_attaccato_al_valore_non_e_un_commento(tmp_path):
    """Senza spazio davanti il '#' fa parte del valore: una password che lo
    contiene non va troncata."""
    letto = _leggi_env(
        tmp_path,
        "SPAPI_TEST_SECRET=pa#ssword\n",
        "SPAPI_TEST_SECRET",
    )
    assert letto == "pa#ssword"


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.post")
def test_post_su_5xx_dichiara_l_esito_incerto(mock_post, mock_sleep):
    """Amazon documenta il 503 come sovraccarico temporaneo: la richiesta può
    essere già stata presa in carico e la risposta persa. Sulle due chiamate
    che spendono, un 503 zitto farebbe rifare la prenotazione."""
    mock_post.side_effect = [
        _resp(200, {"access_token": "Atza|tok", "expires_in": 3600}),
        _resp(503, {"errors": [{"message": "Service Unavailable"}]}),
    ]
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError) as errore:
        c.post("/inbound/fba/2024-03-20/inboundPlans/wf1/transportationOptions/confirmation")
    messaggio = str(errore.value)
    assert "503" in messaggio
    assert "ESITO INCERTO" in messaggio
    assert "Seller Central" in messaggio


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_get_su_5xx_non_dichiara_esito_incerto(mock_post, mock_get, mock_sleep):
    """Una GET non spende e non modifica: allarmare qui insegnerebbe a
    ignorare l'allarme quando conta."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _resp(503, {"errors": [{"message": "Service Unavailable"}]})
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError) as errore:
        c.get("/inbound/fba/2024-03-20/inboundPlans/wf1")
    assert "INCERTO" not in str(errore.value).upper()


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.post")
def test_post_su_4xx_non_dichiara_esito_incerto(mock_post, mock_sleep):
    """Un 4xx è un rifiuto: Amazon ha scartato la richiesta, non c'è niente da
    verificare su Seller Central."""
    mock_post.side_effect = [
        _resp(200, {"access_token": "Atza|tok", "expires_in": 3600}),
        _resp(400, {"errors": [{"message": "Invalid placementOptionId"}]}),
    ]
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError) as errore:
        c.post("/inbound/fba/2024-03-20/inboundPlans/wf1/placementOptions/po1/confirmation")
    messaggio = str(errore.value)
    assert "400" in messaggio
    assert "INCERTO" not in messaggio.upper()


_CONFERMA_TRASPORTO = (
    "/inbound/fba/2024-03-20/inboundPlans/wf1/transportationOptions/confirmation"
)


def _invii(mock_post, path):
    """Le POST davvero partite verso `path`, escluse quelle al token LWA."""
    return [c for c in mock_post.call_args_list if c.args and c.args[0].endswith(path)]


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.post")
def test_post_su_429_riprova_anche_quando_spende(mock_post, mock_sleep):
    """Il 429 è ritentabile per documentazione Amazon: la strozzatura scatta
    prima che l'operazione sia presa in carico. È una decisione deliberata
    (DESIGN §6, rischio residuo accettato) proprio perché questa POST prenota
    il corriere: senza questo test si può togliere il ritentativo — rendendo
    la skill fragile al normale rate limiting — senza che nulla protesti."""
    mock_post.side_effect = [
        _resp(200, {"access_token": "Atza|tok", "expires_in": 3600}),
        _resp(429), _resp(429), _resp(200, {"operationId": "op1"}),
    ]
    c = SpApiClient("Atzr|refresh")
    assert c.post(_CONFERMA_TRASPORTO) == {"operationId": "op1"}
    assert len(_invii(mock_post, _CONFERMA_TRASPORTO)) == 3  # 1 + 2 ritentativi


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.post")
def test_post_su_429_si_ferma_a_cinque_invii(mock_post, mock_sleep):
    """L'altra direzione dello stesso rischio: non c'è chiave di idempotenza su
    confirmTransportationOptions, quindi ogni invio in più è esposizione in più
    alla doppia prenotazione. Il tetto è cinque e va inchiodato."""
    mock_post.side_effect = itertools.chain(
        [_resp(200, {"access_token": "Atza|tok", "expires_in": 3600})],
        itertools.repeat(_resp(429)),
    )
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError, match="rate limit dopo 5 tentativi"):
        c.post(_CONFERMA_TRASPORTO)
    assert len(_invii(mock_post, _CONFERMA_TRASPORTO)) == 5


# --- credenziali per cliente (dal 31/08/2026 ~/scripts/.env ha `<CLIENTE>_SPAPI_*`) ---

def test_crea_client_usa_il_refresh_token_del_prefisso_cliente(monkeypatch):
    monkeypatch.setenv("PHYTOPET_SPAPI_REFRESH_TOKEN", "Atzr|phytopet")
    monkeypatch.setenv("PHYTOPET_SPAPI_REGION", "eu")
    c = spapi_client.crea_client("Phy to Pet")
    assert c.refresh_token == "Atzr|phytopet"
    assert c.client_prefix == "PHYTOPET"
    assert c.base == "https://sellingpartnerapi-eu.amazon.com"


def test_crea_client_senza_refresh_token_alza_spapierror(monkeypatch):
    monkeypatch.delenv("RIVEXTAUTO_SPAPI_REFRESH_TOKEN", raising=False)
    with pytest.raises(SpApiError, match="RIVEXTAUTO_SPAPI_REFRESH_TOKEN mancante"):
        spapi_client.crea_client("Rivextauto")


@patch("spapi_client.requests.post")
def test_access_token_usa_le_credenziali_lwa_del_prefisso_cliente(mock_post, monkeypatch):
    """Ogni cliente ha la SUA app LWA: il refresh token vale solo con il client
    id/secret che lo ha emesso. Accoppiarlo con le credenziali globali darebbe
    `unauthorized_client`, che sembra un token scaduto ma è un accoppiamento sbagliato."""
    monkeypatch.setenv("PHYTOPET_SPAPI_LWA_CLIENT_ID", "cid-phytopet")
    monkeypatch.setenv("PHYTOPET_SPAPI_LWA_CLIENT_SECRET", "csec-phytopet")
    monkeypatch.setenv("SPAPI_LWA_CLIENT_ID", "cid-globale")
    monkeypatch.setenv("SPAPI_LWA_CLIENT_SECRET", "csec-globale")
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    c = SpApiClient("Atzr|refresh", client_prefix="PHYTOPET")
    assert c.access_token == "Atza|tok"
    assert mock_post.call_args.kwargs["data"]["client_id"] == "cid-phytopet"
    assert mock_post.call_args.kwargs["data"]["client_secret"] == "csec-phytopet"


def test_access_token_senza_credenziali_del_prefisso_nomina_il_prefisso(monkeypatch):
    monkeypatch.delenv("PHYTOPET_SPAPI_LWA_CLIENT_ID", raising=False)
    monkeypatch.delenv("PHYTOPET_SPAPI_LWA_CLIENT_SECRET", raising=False)
    c = SpApiClient("Atzr|refresh", client_prefix="PHYTOPET")
    with pytest.raises(SpApiError, match="PHYTOPET_SPAPI_LWA_CLIENT_ID/SECRET mancanti"):
        c.access_token


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_il_rate_limit_dellheader_finisce_nel_log(mock_post, mock_get, caplog):
    """`x-amzn-RateLimit-Limit` è l'unico posto in cui Amazon dice a che
    velocità ricarica la quota di QUELLA operazione per QUESTO cliente: senza
    leggerlo la soglia si scopre solo prendendo un 429."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _resp(200, {"ok": True},
                                 headers={"x-amzn-RateLimit-Limit": "0.0222"})
    c = SpApiClient("Atzr|refresh")
    with caplog.at_level(logging.WARNING, logger="amazon_spedizioni.spapi"):
        c.get("/reports/2021-06-30/reports")
    assert "0.0222" in caplog.text
    assert "/reports/2021-06-30/reports" in caplog.text


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_il_rate_limit_si_logga_una_volta_sola_per_path(mock_post, mock_get, caplog):
    """Il polling di `attendi_operazione` ripete lo stesso path ogni 5s: una
    riga per chiamata seppellirebbe il log sotto la stessa identica frase."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _resp(200, {"ok": True},
                                 headers={"x-amzn-RateLimit-Limit": "2.0"})
    c = SpApiClient("Atzr|refresh")
    with caplog.at_level(logging.WARNING, logger="amazon_spedizioni.spapi"):
        c.get("/inbound/fba/2024-03-20/operations/op1")
        c.get("/inbound/fba/2024-03-20/operations/op1")
    assert caplog.text.count("2.0") == 1


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_senza_header_del_rate_limit_non_si_logga_niente(mock_post, mock_get, caplog):
    """Amazon dichiara che l'header può mancare (e sul 429 manca sempre). Una
    riga che annuncia un limite `None` sarebbe rumore travestito da dato."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_get.return_value = _resp(200, {"ok": True})
    c = SpApiClient("Atzr|refresh")
    with caplog.at_level(logging.WARNING, logger="amazon_spedizioni.spapi"):
        c.get("/inbound/fba/2024-03-20/inboundPlans")
    assert caplog.text == ""


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.post")
def test_access_token_rinnovato_anche_se_il_mac_ha_dormito(mock_post):
    """Su macOS time.monotonic() si ferma nel sonno: un'ora dormita vale due
    minuti, e il token scaduto viene creduto buono (403 a giro iniziato,
    misurato il 06/09/2026). La scadenza si conta sull'orologio da parete."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    parete, fermo = _OrologioFinto(), _OrologioFinto()
    parete.trascorso = 1_700_000_000.0
    with patch("spapi_client.time.time", parete.ora), patch("spapi_client.time.monotonic", fermo.ora):
        c = SpApiClient("Atzr|refresh")
        assert c.access_token == "Atza|tok"
        parete.dormi(2 * 3600)                     # due ore di sonno: il monotonic non le vede
        c.access_token
    assert mock_post.call_count == 2, "dopo due ore di parete il token va richiesto di nuovo"


def test_il_corpo_dell_errore_e_troncato():
    """Finisce nelle mail e nello stdout che NOVA legge: cinque KB di JSON
    di Amazon non sono un dettaglio, sono un muro (DESIGN §17)."""
    from spapi_client import _dettaglio
    r = Mock()
    r.json.return_value = {"errors": [{"message": "x" * 5000}]}
    assert len(_dettaglio(r)) <= 500


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.get")
@patch("spapi_client.requests.post")
def test_ogni_richiesta_dichiara_l_agente_nello_user_agent(mock_post, mock_get):
    """Agent Policy Amazon (BSA sez. 19, dal 04/03/2026): ogni richiesta HTTP di un
    agente porta `Agent/<nome>` nello user agent. La prima parte è il formato che
    SP-API chiede già (`NomeApp/versione (Language=...; Platform=...)`). Vale anche
    per il token LWA: è un servizio Amazon anche quello."""
    mock_post.side_effect = [
        _resp(200, {"access_token": "Atza|tok", "expires_in": 3600}),   # LWA
        _resp(200, {"operationId": "op1"}),
    ]
    mock_get.return_value = _resp(200, {"operationStatus": "SUCCESS"})
    c = SpApiClient("Atzr|refresh")
    c.get("/inbound/fba/2024-03-20/operations/op1")
    c.post("/inbound/fba/2024-03-20/inboundPlans", json_body={})
    atteso = re.compile(r"NewEra-amazon-spedizioni/\S+ \(Language=Python/"
                        + re.escape(platform.python_version())
                        + r"; Platform=\S+\) Agent/NewEra-amazon-spedizioni")
    chiamate = mock_post.call_args_list + mock_get.call_args_list
    assert len(chiamate) == 3   # token LWA, POST, GET
    for chiamata in chiamate:
        ua = (chiamata.kwargs.get("headers") or {}).get("User-Agent", "")
        assert atteso.fullmatch(ua), f"{chiamata.args[0]}: user agent {ua!r}"


_PATH_TRACKING = (
    "/inbound/fba/2024-03-20/inboundPlans/wf5545452f/shipments/FBA15MBX78RC/trackingDetails"
)
_CORPO_TRACKING = {
    "trackingDetails": {
        "spdTrackingDetail": {
            "spdTrackingItems": [{"boxId": "FBA15MBX78RC000001", "trackingId": "0922943001"}]
        }
    }
}


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.requests.put")
@patch("spapi_client.requests.post")
def test_put_manda_il_payload_e_torna_il_corpo(mock_post, mock_put):
    """`updateShipmentTrackingDetails` è l'unica chiamata del flusso che vuole
    una PUT: senza, `_request` la manderebbe in POST e Amazon risponderebbe 404
    (verificato dal vivo su ECM l'11/09/2026)."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_put.return_value = _resp(200, {"operationId": "op9"})
    c = SpApiClient("Atzr|refresh")
    assert c.put(_PATH_TRACKING, json_body=_CORPO_TRACKING) == {"operationId": "op9"}
    assert mock_put.call_args.args[0] == f"https://sellingpartnerapi-eu.amazon.com{_PATH_TRACKING}"
    assert mock_put.call_args.kwargs["json"] == _CORPO_TRACKING
    # La POST resta quella del token LWA: la scrittura non deve passare di lì.
    assert mock_post.call_count == 1


@patch.dict("os.environ", {"SPAPI_LWA_CLIENT_ID": "cid", "SPAPI_LWA_CLIENT_SECRET": "csec"})
@patch("spapi_client.time.sleep")
@patch("spapi_client.requests.put")
@patch("spapi_client.requests.post")
def test_put_in_timeout_dichiara_l_esito_incerto_e_nomina_la_put(mock_post, mock_put, mock_sleep):
    """Stessa regola della POST: il socket può scadere dopo che Amazon ha già
    accettato la dichiarazione. Il messaggio deve dire PUT, non POST, o manda a
    cercare su Seller Central una chiamata che non è stata fatta."""
    mock_post.return_value = _resp(200, {"access_token": "Atza|tok", "expires_in": 3600})
    mock_put.side_effect = requests.exceptions.Timeout("Read timed out")
    c = SpApiClient("Atzr|refresh")
    with pytest.raises(SpApiError) as errore:
        c.put(_PATH_TRACKING, json_body=_CORPO_TRACKING)
    messaggio = str(errore.value)
    assert "INCERTO" in messaggio.upper()
    assert "PUT" in messaggio and "POST" not in messaggio
    assert mock_put.call_count == 1
    mock_sleep.assert_not_called()
