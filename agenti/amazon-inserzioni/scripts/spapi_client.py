from __future__ import annotations
import logging
import os
import platform
import time
import requests
from urllib.parse import quote

log = logging.getLogger("amazon_inserzioni.spapi")

_ENV_FILE = os.path.expanduser("~/scripts/.env")


def _carica_env_da_file() -> None:
    """Popola le variabili SPAPI mancanti da ~/scripts/.env (mai sovrascrive
    l'ambiente reale). Il file e' condiviso con bot, invii mail e altri
    connettori: le loro chiavi non entrano in questo processo."""
    if not os.path.isfile(_ENV_FILE):
        return
    with open(_ENV_FILE) as f:
        for riga in f:
            riga = riga.strip()
            if not riga or riga.startswith("#") or "=" not in riga:
                continue
            chiave, valore = riga.split("=", 1)
            chiave, valore = chiave.strip(), valore.strip()
            if valore and "SPAPI" in chiave:
                os.environ.setdefault(chiave, valore)


_carica_env_da_file()

_LWA_URL = "https://api.amazon.com/auth/o2/token"
_ENDPOINTS = {
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "na": "https://sellingpartnerapi-na.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",
}

# Agent Policy Amazon (BSA sez. 19, dal 04/03/2026): ogni richiesta HTTP di un
# agente dichiara `Agent/<nome>` nello user agent. Davanti, il formato che SP-API
# chiede già: NomeApp/versione (Language=...; Platform=...).
_USER_AGENT = (
    f"NewEra-amazon-inserzioni/1.0 (Language=Python/{platform.python_version()}; "
    f"Platform={'macOS' if platform.system() == 'Darwin' else platform.system()}) "
    "Agent/NewEra-amazon-inserzioni"
)


class SpApiError(Exception):
    """Errore di dialogo con SP-API. Mai ingoiato: risale sempre al chiamante."""


def percorso_listing(seller_id: str, sku: str) -> str:
    """Path SP-API di un'inserzione, con lo SKU codificato come UN segmento.

    La barra dentro uno SKU (MA1/4SX-R, Tecno Cold 2026-09-03) spezzava il path
    in due segmenti: nessuna rotta combaciava e il gateway rispondeva **403
    Unauthorized**, che sembra un problema di permessi e non lo e' (stesse
    credenziali, stesso token: passa appena si codifica). `safe=''` e'
    obbligatorio -- il default di quote() e' `safe='/'` e lascia passare
    proprio il carattere che rompe.

    Lo SKU vuoto invece si ferma qui: non e' una regola di Amazon, e' il path
    che punterebbe a un'altra rotta, e su una PATCH significherebbe scrivere a
    un indirizzo non scelto. Quasi sempre e' un dato non popolato a monte.
    """
    if not sku.strip():
        raise SpApiError("sku vuoto: non si puo' costruire il path di un'inserzione")
    return f"/listings/2021-08-01/items/{quote(seller_id, safe='')}/{quote(sku, safe='')}"


def _dettaglio(r) -> str:
    try:
        return str(r.json())
    except ValueError:
        return r.text[:200]


class SpApiClient:
    """Trasporto verso SP-API. Non conosce la semantica dei dati che trasporta.

    Dal 2 ottobre 2023 SP-API non richiede piu' firma AWS SigV4: basta il
    token LWA nell'header x-amz-access-token. Le scritture (put/patch)
    tornano il corpo della risposta anche a status applicativo INVALID:
    solo status_code >= 400 e' un errore di trasporto.
    """

    _MAX_TENTATIVI = 5

    def __init__(self, refresh_token: str, region: str = "eu", client_prefix: str = ""):
        if not refresh_token:
            raise SpApiError(
                "refresh_token mancante: probabile variabile d'ambiente non "
                "impostata nella configurazione dell'account"
            )
        if region not in _ENDPOINTS:
            raise SpApiError(f"regione SP-API sconosciuta: {region}")
        self.refresh_token = refresh_token
        self.base = _ENDPOINTS[region]
        self.client_prefix = client_prefix
        self._token: str | None = None
        self._token_scade: float = 0.0

    @property
    def access_token(self) -> str:
        # Scadenza sull'orologio da parete: time.monotonic() su macOS non
        # avanza nel sonno, e un'ora di stand-by valeva due minuti di orologio
        # (token scaduto creduto buono, 403 a giro iniziato, misurato 06/09/2026).
        if self._token and time.time() < self._token_scade:
            return self._token
        prefisso = f"{self.client_prefix}_" if self.client_prefix else ""
        cid = os.environ.get(f"{prefisso}SPAPI_LWA_CLIENT_ID")
        csec = os.environ.get(f"{prefisso}SPAPI_LWA_CLIENT_SECRET")
        if not cid or not csec:
            raise SpApiError(f"{prefisso}SPAPI_LWA_CLIENT_ID/SECRET mancanti nell'ambiente")
        try:
            r = requests.post(_LWA_URL, data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": cid,
                "client_secret": csec,
            }, headers={"User-Agent": _USER_AGENT}, timeout=30)
        except requests.exceptions.RequestException as e:
            raise SpApiError(f"LWA irraggiungibile: {type(e).__name__}: {e}") from e
        if r.status_code != 200:
            raise SpApiError(f"LWA {r.status_code}: {_dettaglio(r)}")
        body = r.json()
        self._token = body["access_token"]
        # 60s di margine per non usare un token che scade a meta' giro
        self._token_scade = time.time() + int(body.get("expires_in", 3600)) - 60
        return self._token

    def _request(self, metodo: str, path: str, params: dict | None = None,
                 json_body: dict | None = None) -> dict:
        attesa = 1
        rinnovato = False
        for tentativo in range(1, self._MAX_TENTATIVI + 1):
            headers = {"x-amz-access-token": self.access_token, "User-Agent": _USER_AGENT}
            url = f"{self.base}{path}"
            try:
                if metodo == "GET":
                    r = requests.get(url, headers=headers, params=params or {}, timeout=60)
                elif metodo == "PUT":
                    r = requests.put(url, headers=headers, params=params or {}, json=json_body, timeout=60)
                elif metodo == "PATCH":
                    r = requests.patch(url, headers=headers, params=params or {}, json=json_body, timeout=60)
                elif metodo == "POST":
                    r = requests.post(url, headers=headers, params=params or {}, json=json_body, timeout=60)
                elif metodo == "DELETE":
                    r = requests.delete(url, headers=headers, params=params or {}, timeout=60)
                else:
                    raise SpApiError(f"metodo non supportato: {metodo}")
            except requests.exceptions.RequestException as e:
                # Timeout e connessioni cadute non hanno uno status: sfuggivano
                # a ogni "except SpApiError" a valle, lasciando il registro su
                # "in corso" e facendo crollare vai a meta' lotto (06/09/2026).
                raise SpApiError(f"trasporto su {path}: {type(e).__name__}: {e}") from e
            if r.status_code in (401, 403) and not rinnovato:
                # Un token scaduto a giro iniziato risponde 403 "expired": un
                # rinnovo e una ripetizione; se insiste, e' un permesso negato.
                log.warning("%s su %s: rinnovo il token e riprovo", r.status_code, path)
                self._token = None
                rinnovato = True
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if tentativo == self._MAX_TENTATIVI:
                    raise SpApiError(f"{r.status_code} su {path} dopo {tentativo} tentativi: {_dettaglio(r)}")
                log.warning("%s su %s, riprovo tra %ss (tentativo %s)", r.status_code, path, attesa, tentativo)
                time.sleep(attesa)
                attesa *= 2
                continue
            if r.status_code >= 400:
                raise SpApiError(f"{r.status_code} su {path}: {_dettaglio(r)}")
            return r.json()
        raise SpApiError(f"{path} uscita inattesa dal ciclo di retry")

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def put(self, path: str, json_body: dict, params: dict | None = None) -> dict:
        return self._request("PUT", path, params=params, json_body=json_body)

    def patch(self, path: str, json_body: dict, params: dict | None = None) -> dict:
        return self._request("PATCH", path, params=params, json_body=json_body)

    def post(self, path: str, json_body: dict | None = None, params: dict | None = None) -> dict:
        return self._request("POST", path, params=params, json_body=json_body)

    def delete(self, path: str, params: dict | None = None) -> dict:
        return self._request("DELETE", path, params=params)


def crea_client(cliente: str) -> SpApiClient:
    """Factory per-cliente: dal 31/08/2026 ogni cliente ha la sua app LWA in
    ~/scripts/.env sotto `<CLIENTE>_SPAPI_LWA_CLIENT_ID/SECRET/REFRESH_TOKEN`
    (region opzionale, default eu). `cliente` è case-insensitive e ignora
    spazi/trattini (es. "Tecno Cold" -> prefisso TECNOCOLD)."""
    prefisso = "".join(ch for ch in cliente.upper() if ch.isascii() and ch.isalnum())
    refresh_token = os.environ.get(f"{prefisso}_SPAPI_REFRESH_TOKEN")
    if not refresh_token:
        raise SpApiError(f"{prefisso}_SPAPI_REFRESH_TOKEN mancante nell'ambiente")
    region = os.environ.get(f"{prefisso}_SPAPI_REGION", "eu")
    return SpApiClient(refresh_token, region=region, client_prefix=prefisso)


def seller_id_di(cliente: str) -> str:
    """Merchant token del cliente, da `<CLIENTE>_SPAPI_SELLER_ID` in ~/scripts/.env.
    Stessa normalizzazione del prefisso di crea_client. Non arriva dalle API
    (marketplaceParticipations non lo restituisce), quindi va registrato a mano."""
    prefisso = "".join(ch for ch in cliente.upper() if ch.isascii() and ch.isalnum())
    seller_id = os.environ.get(f"{prefisso}_SPAPI_SELLER_ID")
    if not seller_id:
        raise SpApiError(
            f"{prefisso}_SPAPI_SELLER_ID mancante nell'ambiente: e' il merchant token "
            "(Seller Central -> Impostazioni -> Informazioni account), va in ~/scripts/.env"
        )
    return seller_id
