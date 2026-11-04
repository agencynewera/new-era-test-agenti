from __future__ import annotations
import logging
import os
import platform
import re
import time
import requests

log = logging.getLogger("amazon_spedizioni.spapi")

_ENV_FILE = os.path.expanduser("~/scripts/.env")


def _valore_senza_commento(valore: str) -> str:
    """Valore di una riga KEY=VALUE ripulito dal commento in coda. Fra
    virgolette è preso alla lettera e può contenere '#'; fuori, il '#' apre un
    commento solo se preceduto da spazio, così una credenziale che lo contiene
    non viene troncata. Senza questo, `ID=abc  # nota` darebbe un client id con
    dentro la nota, e l'unico sintomo sarebbe un 401 invalid_client opaco."""
    valore = valore.strip()
    if valore[:1] in ("'", '"'):
        fine = valore.find(valore[0], 1)
        if fine != -1:
            return valore[1:fine]
    # Solo `spazio + #` apre un commento: un '#' a inizio valore fa parte del
    # valore (il commento di riga intera è già scartato da _carica_env_da_file).
    # Trattandolo come commento si svuota una credenziale legittima, e l'unico
    # sintomo è un errore LWA opaco.
    return re.split(r"\s#", valore, maxsplit=1)[0].strip()


def _carica_env_da_file() -> None:
    """Popola le variabili SPAPI_* mancanti da ~/scripts/.env (mai sovrascrive l'ambiente reale)."""
    if not os.path.isfile(_ENV_FILE):
        return
    with open(_ENV_FILE) as f:
        for riga in f:
            riga = riga.strip()
            if not riga or riga.startswith("#") or "=" not in riga:
                continue
            chiave, valore = riga.split("=", 1)
            valore = _valore_senza_commento(valore)
            if valore:
                os.environ.setdefault(chiave.strip(), valore)


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
    f"NewEra-amazon-spedizioni/1.0 (Language=Python/{platform.python_version()}; "
    f"Platform={'macOS' if platform.system() == 'Darwin' else platform.system()}) "
    "Agent/NewEra-amazon-spedizioni"
)


class SpApiError(Exception):
    """Errore di dialogo con SP-API. Mai ingoiato: risale sempre al chiamante."""


_DETTAGLIO_MAX = 500

def _dettaglio(r) -> str:
    """Corpo della risposta per il messaggio d'errore. Non deve mai alzare a
    sua volta: se la connessione cade mentre si legge il corpo, r.json() e
    r.text alzano una requests.exceptions che sostituirebbe l'errore vero
    (lo status code) con un crash grezzo.
    Troncato: il corpo finisce nelle mail e nello stdout che NOVA legge (DESIGN §17)."""
    try:
        return str(r.json())[:_DETTAGLIO_MAX]
    except ValueError:
        pass
    except requests.exceptions.RequestException as e:
        return f"corpo non leggibile: {e}"
    try:
        return r.text[:_DETTAGLIO_MAX]
    except requests.exceptions.RequestException as e:
        return f"corpo non leggibile: {e}"


def motivo_fallimento(esito: dict) -> str:
    """Motivo letterale di un'operazione FAILED, letto dai suoi
    `operationProblems`. Scarta i messaggi vuoti PRIMA di unirli: unire due
    messaggi vuoti dà `"; "`, che in Python è vero e farebbe passare per
    motivo un separatore, nascondendo un fallimento senza spiegazione."""
    messaggi = [
        (p.get("message") or "").strip()
        for p in (esito.get("operationProblems") or [])
    ]
    return "; ".join(m for m in messaggi if m) or "motivo non specificato"


class SpApiClient:
    """Trasporto verso SP-API. Non conosce la semantica dei dati che trasporta.

    Vendorizzato da marketplace_health.spapi.SpApiClient: stesso auth LWA,
    stesso backoff sui 429. `attendi_operazione` è generico — ogni scrittura
    della Fulfillment Inbound API torna un operationId da interrogare finché
    non è SUCCESS o FAILED, stessa forma del ciclo report ma senza documento
    da scaricare alla fine.
    """

    # Invii massimi della stessa richiesta, il primo compreso. Su
    # confirmTransportationOptions non c'è chiave di idempotenza: alzarlo
    # aumenta l'esposizione alla doppia prenotazione (DESIGN §6).
    _MAX_TENTATIVI = 5
    _INTERVALLO_POLL_S = 5

    def __init__(self, refresh_token: str, region: str = "eu",
                 client_prefix: str = ""):
        if not refresh_token:
            raise SpApiError(
                "refresh_token mancante: probabile variabile d'ambiente non "
                "impostata nella configurazione dell'account"
            )
        if region not in _ENDPOINTS:
            raise SpApiError(f"regione SP-API sconosciuta: {region}")
        self.refresh_token = refresh_token
        self.base = _ENDPOINTS[region]
        # Prefisso `<CLIENTE>` delle variabili LWA in ~/scripts/.env: ogni
        # cliente ha la sua app, e un refresh token vale solo con l'app che lo
        # ha emesso (altrimenti LWA risponde `unauthorized_client`).
        self.client_prefix = client_prefix
        self._token: str | None = None
        self._token_scade: float = 0.0
        # Path già annunciati nel log: il rate limit di un'operazione non
        # cambia dentro un giro, e il polling ripete lo stesso path ogni 5s.
        self._limiti_visti: set[str] = set()

    @property
    def access_token(self) -> str:
        if self._token and time.time() < self._token_scade:
            return self._token
        prefisso = f"{self.client_prefix}_" if self.client_prefix else ""
        cid = os.environ.get(f"{prefisso}SPAPI_LWA_CLIENT_ID")
        csec = os.environ.get(f"{prefisso}SPAPI_LWA_CLIENT_SECRET")
        if not cid or not csec:
            raise SpApiError(f"{prefisso}SPAPI_LWA_CLIENT_ID/SECRET mancanti nell'ambiente")
        r = requests.post(_LWA_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": cid,
            "client_secret": csec,
        }, headers={"User-Agent": _USER_AGENT}, timeout=30)
        if r.status_code != 200:
            raise SpApiError(f"LWA {r.status_code}: {_dettaglio(r)}")
        try:
            body = r.json()
            self._token = body["access_token"]
            scadenza = int(body.get("expires_in", 3600))
        except (ValueError, TypeError, KeyError) as e:
            # Un 200 illeggibile qui uscirebbe come ValueError/KeyError grezzo:
            # non è SpApiError e sfuggirebbe al contratto d'errore del flusso.
            raise SpApiError(f"risposta LWA illeggibile: {_dettaglio(r)}") from e
        # 60s di margine per non usare un token che scade a metà giro
        # orologio da parete, non monotonic: su macOS il monotonic si ferma nel sonno (DESIGN §17)
        self._token_scade = time.time() + scadenza - 60
        return self._token

    def _corpo_inatteso(self, metodo: str, path: str, motivo: str) -> SpApiError:
        """Errore per un 2xx che non si riesce a leggere. Su una POST il
        messaggio deve dichiarare l'esito incerto: le due chiamate che spendono
        (conferma piazzamento, conferma trasporto) passano di qui, e Amazon può
        aver già elaborato l'operazione. Su una GET no: rileggere è innocuo."""
        messaggio = f"risposta 2xx illeggibile da {metodo} {path}: {motivo}"
        if metodo != "GET":
            messaggio += (
                ". ESITO INCERTO: la richiesta è stata accettata e può essere "
                "già stata elaborata da Amazon — verifica su Seller Central "
                "com'è finita prima di rifare qualunque cosa."
            )
        return SpApiError(messaggio)

    def _request(self, metodo: str, path: str, params: dict | None = None,
                 json_body: dict | None = None) -> dict:
        attesa = 1
        for tentativo in range(1, self._MAX_TENTATIVI + 1):
            try:
                headers = {"x-amz-access-token": self.access_token, "User-Agent": _USER_AGENT}
            except requests.exceptions.RequestException as e:
                raise SpApiError(
                    f"errore di rete sul token LWA prima di {metodo} {path}: {e}. "
                    "Nessuna richiesta è arrivata ad Amazon: nulla è stato speso."
                ) from e
            url = f"{self.base}{path}"
            try:
                if metodo == "GET":
                    r = requests.get(url, headers=headers, params=params or {}, timeout=60)
                elif metodo == "PUT":
                    r = requests.put(url, headers=headers, params=params or {}, json=json_body, timeout=60)
                else:
                    r = requests.post(url, headers=headers, params=params or {}, json=json_body, timeout=60)
            except requests.exceptions.RequestException as e:
                # Senza questo la requests.exceptions.* uscirebbe grezza: sfugge al
                # contratto SpApiError, l'orchestratore non la intercetta, il giro
                # crasha e nel registro non finisce niente.
                if metodo != "GET":
                    # Il socket può scadere DOPO che Amazon ha elaborato la
                    # scrittura (conferma piazzamento e trasporto: lì si spende;
                    # dichiarazione dei tracking: lì la spedizione passa a SHIPPED).
                    # Ritentare da soli duplicherebbe un'operazione a pagamento.
                    raise SpApiError(
                        f"errore di rete su {metodo} {path}: {e}. ESITO INCERTO: la "
                        "richiesta potrebbe essere già stata elaborata da Amazon. "
                        "NON viene ritentata — verifica su Seller Central com'è "
                        "finita prima di riprovare."
                    ) from e
                if tentativo == self._MAX_TENTATIVI:
                    raise SpApiError(
                        f"errore di rete su GET {path} dopo {tentativo} tentativi: {e}"
                    ) from e
                # Una GET non spende e non modifica: ritentarla è sicuro.
                log.warning("errore di rete su %s, riprovo tra %ss (tentativo %s): %s",
                            path, attesa, tentativo, e)
                time.sleep(attesa)
                attesa *= 2
                continue
            # `x-amzn-RateLimit-Limit` è l'unico posto in cui Amazon dichiara
            # la velocità di ricarica della quota di quell'operazione per
            # questa coppia app+cliente. Arriva solo su 20x/400/404 — sul 429,
            # cioè proprio quando servirebbe, non c'è — e può mancare del tutto.
            limite = r.headers.get("x-amzn-RateLimit-Limit")
            if limite and path not in self._limiti_visti:
                self._limiti_visti.add(path)
                # warning e non info: senza basicConfig il root logger sta a
                # WARNING, e una riga info non comparirebbe nei log del giro.
                log.warning("rate limit %s req/s su %s", limite, path)
            if r.status_code == 429:
                if tentativo == self._MAX_TENTATIVI:
                    raise SpApiError(f"429 su {path}: rate limit dopo {tentativo} tentativi")
                log.warning("429 su %s, riprovo tra %ss (tentativo %s)", path, attesa, tentativo)
                time.sleep(attesa)
                attesa *= 2
                continue
            if r.status_code >= 400:
                messaggio = f"{r.status_code} su {path}: {_dettaglio(r)}"
                if r.status_code >= 500 and metodo != "GET":
                    # Amazon documenta il 503 come sovraccarico temporaneo: è
                    # proprio il caso in cui l'operazione può essere stata presa
                    # in carico e la risposta persa. Un 4xx invece è un rifiuto.
                    messaggio += (
                        ". ESITO INCERTO: errore lato server su una richiesta che "
                        "può essere già stata elaborata — verifica su Seller Central "
                        "com'è finita prima di rifare qualunque cosa."
                    )
                raise SpApiError(messaggio)
            try:
                corpo = r.json()
            except ValueError as e:
                raise self._corpo_inatteso(
                    metodo, path, f"JSON non valido ({e}); inizio del corpo: {r.text[:200]}"
                ) from e
            except requests.exceptions.RequestException as e:
                # La connessione può cadere mentre si legge il corpo, non solo
                # mentre si manda la richiesta: qui non è un ValueError.
                raise self._corpo_inatteso(
                    metodo, path, f"corpo non ricevuto per intero ({e})"
                ) from e
            if not isinstance(corpo, dict):
                # JSON valido ma di forma inattesa: passandolo oltre scoppierebbe
                # più in là con un TypeError/AttributeError fuori contratto.
                raise self._corpo_inatteso(
                    metodo, path,
                    f"atteso un oggetto JSON, ricevuto {type(corpo).__name__}"
                )
            return corpo
        raise SpApiError(f"{path} uscita inattesa dal ciclo di retry")

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, json_body: dict | None = None, params: dict | None = None) -> dict:
        return self._request("POST", path, params=params, json_body=json_body)

    def put(self, path: str, json_body: dict | None = None, params: dict | None = None) -> dict:
        """`updateShipmentTrackingDetails` è l'unica operazione del flusso che
        vuole una PUT. Vale per lei tutto ciò che vale per la POST: nessun
        ritentativo automatico e messaggio di esito incerto, perché anche qui
        una risposta persa non significa che Amazon non abbia accettato."""
        return self._request("PUT", path, params=params, json_body=json_body)

    def attendi_operazione(self, operation_id: str, attesa_max_s: int = 300) -> dict:
        """Interroga GET .../operations/{operationId} finché operationStatus
        non è SUCCESS o FAILED. Torna il corpo finale — non alza SpApiError
        su un FAILED, solo su timeout o errore di trasporto (già gestito da
        get()): sta al chiamante decidere cosa fare di un FAILED.

        Il tetto è tenuto anche sull'orologio, non solo sulle pause dichiarate:
        i ritentativi interni a get() (429 e buchi di rete) mangiano tempo che
        il contatore non vede, e senza il controllo sull'orologio l'attesa reale
        supera di molto `attesa_max_s` continuando a dichiararlo. Il messaggio
        riporta il tempo davvero trascorso."""
        inizio = time.monotonic()
        atteso = 0
        while atteso < attesa_max_s and time.monotonic() - inizio < attesa_max_s:
            stato = self.get(f"/inbound/fba/2024-03-20/operations/{operation_id}")
            if stato.get("operationStatus") in ("SUCCESS", "FAILED"):
                return stato
            time.sleep(self._INTERVALLO_POLL_S)
            atteso += self._INTERVALLO_POLL_S
        raise SpApiError(
            f"operazione {operation_id} non conclusa dopo "
            f"{round(time.monotonic() - inizio)}s (tetto {attesa_max_s}s)"
        )


def crea_client(cliente: str) -> SpApiClient:
    """Factory per-cliente: dal 31/08/2026 ogni cliente ha la sua app LWA in
    ~/scripts/.env sotto `<CLIENTE>_SPAPI_LWA_CLIENT_ID/SECRET/REFRESH_TOKEN`
    (region opzionale, default eu). `cliente` è case-insensitive e ignora
    spazi/trattini (es. "Phy to Pet" -> prefisso PHYTOPET). Stessa convenzione
    di amazon-inserzioni."""
    prefisso = "".join(ch for ch in cliente.upper() if ch.isalnum())
    refresh_token = os.environ.get(f"{prefisso}_SPAPI_REFRESH_TOKEN")
    if not refresh_token:
        raise SpApiError(f"{prefisso}_SPAPI_REFRESH_TOKEN mancante nell'ambiente")
    region = os.environ.get(f"{prefisso}_SPAPI_REGION", "eu")
    return SpApiClient(refresh_token, region=region, client_prefix=prefisso)
