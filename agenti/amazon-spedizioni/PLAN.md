# Agente Amazon New Era: creare spedizioni FBA (Seller Central) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un agente che crea spedizioni FBA su Amazon Seller Central — da SKU/quantità
dettati da Mariano fino alle etichette pronte da stampare — con anteprima di corriere e
prezzo e un "vai" esplicito prima di ogni prenotazione o scrittura.

**Architecture:** File piatti in `scripts/` (niente sottocartelle, stessa convenzione di
`amazon-inserzioni`/eBay), un client SP-API vendorizzato e adattato dal monitor salute
(pattern POST + attesa operazione asincrona, non GET/PUT/PATCH come `amazon-inserzioni`
— qui non si scrive un attributo, si avanza un flusso a stadi: piano → imballaggio →
trasporto → conferma → etichette), un modulo per stadio, un registro JSON condiviso.

**Tech Stack:** Python 3.9 di sistema (niente venv dedicata), `requests`, `pytest` — già
installati sulla macchina. Nessuno SDK Amazon di terze parti.

**Spec:** `~/.claude/skills/amazon-spedizioni/DESIGN.md`

> ⚠️ **Aggiornamento dopo la revisione finale (2026-08-29).** I Task 4-8 di questo piano
> erano stati scritti su una forma dell'API **supposta**, non verificata. Confrontandola
> col modello ufficiale `fulfillmentInbound_2024-03-20.json` sono emerse tre divergenze
> strutturali: mancavano gli stadi `setPackingInformation`, `generatePlacementOptions` e
> `confirmPlacementOption` (le spedizioni nascono lì, non col piano); le etichette non
> sono in v2024-03-20 ma nell'operazione v0 conservata `getLabels`; le operazioni di
> trasporto sono di piano e vogliono un corpo, non di spedizione e senza.
>
> I blocchi di codice dentro i Task 4-8 sono quindi **storici**: raccontano come il piano
> era stato pensato, non cosa gira. La fonte di verità è `scripts/` + `SKILL.md`; le firme
> aggiornate sono nelle righe "Produces" di ogni task, e il flusso reale in DESIGN §5 e
> §10. Il piano resta qui per tracciabilità, non come riferimento di implementazione.

## Global Constraints

- 🟡 autonomia: nessuna **spesa e nessuna scrittura irreversibile** su Amazon senza un
  "vai" esplicito di Mariano — obbligatorio anche quando il corriere proprio non ha un
  costo da mostrare (DESIGN, autonomia). Il flusso Amazon è a stadi: le scritture
  preparatorie (`createInboundPlan`, `confirmPackingOption`, `setPackingInformation`)
  non prenotano nulla e si annullano buttando via il piano (`cancelInboundPlan`); il
  "vai" copre `confirmPlacementOption` (accetta la commissione di piazzamento e fissa la
  divisione per sempre) e `confirmTransportationOptions` (prenota il corriere). La
  sequenza esatta sta in `SKILL.md`, sezione "Dov'è il cancello"
- Un cliente, un marketplace, alla volta — DESIGN §3
- L'agente non decide mai da solo cosa o quanto spedire: SKU e quantità sono sempre
  dettati da Mariano — DESIGN §3
- **Ogni scrittura della Fulfillment Inbound API è asincrona**: una POST torna sempre un
  `operationId` da interrogare con `attendi_operazione` finché non è `SUCCESS` o
  `FAILED` — mai assumere che la risposta della POST sia già lo stato finale
- Un `FAILED` si dichiara col motivo letterale in `operationProblems`, mai si inventa una
  correzione o si ritenta da soli — DESIGN §6
- Nessuna chiamata di rete nei test: tutto mockato (`unittest.mock`) o su fixture inline
- Credenziali SP-API lette solo da variabili d'ambiente
  (`SPAPI_LWA_CLIENT_ID`/`SPAPI_LWA_CLIENT_SECRET`) + refresh token passato dal chiamante
  — mai in chat, mai hardcoded, mai in un file committato — DESIGN §7
- Import piatti con `sys.path.insert` (vedi `tests/conftest.py`, Task 1) — non un package
  Python con `__init__.py`

---

## File Structure

```
~/.claude/skills/amazon-spedizioni/
  SKILL.md
  DESIGN.md · PLAN.md
  scripts/
    spapi_client.py    trasporto SP-API: auth LWA, retry/backoff, get/post,
                        attendi_operazione (polling asincrono generico)
    modello.py          RigaSpedizione, OpzioneImballaggio, OpzioneTrasporto,
                        Spedizione, PianoSpedizione
    registro.py          log JSON di ogni lotto richiesto/esito, per cliente
    piano.py             SKU+quantità → PianoSpedizione (createInboundPlan)
    imballaggio.py        opzioni di imballaggio + conferma + dati scatole
                        (setPackingInformation)
    piazzamento.py         divisione fra centri logistici: opzioni, dettaglio
                        spedizioni, conferma (confirmPlacementOption)
    corrieri.py           opzioni di trasporto di piano + finestre di consegna
    conferma.py            conferma finestra e trasporto scelti — sempre dopo
                        il "vai"
    etichette.py            scarica le etichette di spedizione, salva sul
                        Desktop
    anteprima.py            riepilogo testuale per la chat: pacchi,
                        destinazione, corriere, prezzo
  tests/
    conftest.py
    test_spapi_client.py
    test_modello.py
    test_registro.py
    test_piano.py
    test_imballaggio.py
    test_corrieri.py
    test_conferma.py
    test_etichette.py
    test_anteprima.py
    test_prova_a_vuoto.py
```

Repo git proprio, come `amazon-inserzioni`. Niente venv dedicata: python3 di sistema.

---

### Task 1: `spapi_client.py` — sessione SP-API (auth LWA + retry + get/post + attesa operazione)

**Files:**
- Create: `scripts/spapi_client.py`
- Create: `tests/conftest.py`
- Test: `tests/test_spapi_client.py`

**Interfaces:**
- Produces: `SpApiClient(refresh_token: str, region: str = "eu")`; proprietà
  `.access_token -> str`; metodi `.get(path, params=None) -> dict`,
  `.post(path, json_body=None, params=None) -> dict`,
  `.attendi_operazione(operation_id: str, attesa_max_s: int = 300) -> dict`; eccezione
  `SpApiError`

Vendorizzato da `marketplace_health.spapi.SpApiClient` (già in produzione, 55 test
verdi in `~/scripts/marketplace-health-monitor/`): stesso auth LWA con cache token,
stesso backoff sui 429, stessa lettura di errori con corpo non-JSON. Al posto del ciclo
report (specifico, scarica un documento) aggiunge `attendi_operazione`, generico: la
Fulfillment Inbound API 2024-03-20 usa lo stesso pattern "POST torna un `operationId`,
interroga `GET .../operations/{operationId}` finché `operationStatus` non è `SUCCESS` o
`FAILED`" per **ogni** scrittura (piano, imballaggio, trasporto, conferma).

- [ ] **Step 1: Scrivi `tests/conftest.py`**

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
```

- [ ] **Step 2: Scrivi i test che falliscono**

```python
# tests/test_spapi_client.py
from unittest.mock import patch, Mock
import pytest
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
    assert chiamata.kwargs["headers"] == {"x-amz-access-token": "Atza|tok"}


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
```

- [ ] **Step 2b: Verifica che falliscano**

Run: `python3 -m pytest tests/test_spapi_client.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'spapi_client'`

- [ ] **Step 3: Scrivi `scripts/spapi_client.py`**

```python
from __future__ import annotations
import logging
import os
import time
import requests

log = logging.getLogger("amazon_spedizioni.spapi")

_LWA_URL = "https://api.amazon.com/auth/o2/token"
_ENDPOINTS = {
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "na": "https://sellingpartnerapi-na.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",
}


class SpApiError(Exception):
    """Errore di dialogo con SP-API. Mai ingoiato: risale sempre al chiamante."""


def _dettaglio(r) -> str:
    try:
        return str(r.json())
    except ValueError:
        return r.text[:200]


class SpApiClient:
    """Trasporto verso SP-API. Non conosce la semantica dei dati che trasporta.

    Vendorizzato da marketplace_health.spapi.SpApiClient: stesso auth LWA,
    stesso backoff sui 429. `attendi_operazione` è generico — ogni scrittura
    della Fulfillment Inbound API torna un operationId da interrogare finché
    non è SUCCESS o FAILED, stessa forma del ciclo report ma senza documento
    da scaricare alla fine.
    """

    _MAX_TENTATIVI = 5
    _INTERVALLO_POLL_S = 5

    def __init__(self, refresh_token: str, region: str = "eu"):
        if not refresh_token:
            raise SpApiError(
                "refresh_token mancante: probabile variabile d'ambiente non "
                "impostata nella configurazione dell'account"
            )
        if region not in _ENDPOINTS:
            raise SpApiError(f"regione SP-API sconosciuta: {region}")
        self.refresh_token = refresh_token
        self.base = _ENDPOINTS[region]
        self._token: str | None = None
        self._token_scade: float = 0.0

    @property
    def access_token(self) -> str:
        if self._token and time.monotonic() < self._token_scade:
            return self._token
        cid = os.environ.get("SPAPI_LWA_CLIENT_ID")
        csec = os.environ.get("SPAPI_LWA_CLIENT_SECRET")
        if not cid or not csec:
            raise SpApiError("SPAPI_LWA_CLIENT_ID/SECRET mancanti nell'ambiente")
        r = requests.post(_LWA_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": cid,
            "client_secret": csec,
        }, timeout=30)
        if r.status_code != 200:
            raise SpApiError(f"LWA {r.status_code}: {_dettaglio(r)}")
        body = r.json()
        self._token = body["access_token"]
        # 60s di margine per non usare un token che scade a metà giro
        self._token_scade = time.monotonic() + int(body.get("expires_in", 3600)) - 60
        return self._token

    def _request(self, metodo: str, path: str, params: dict | None = None,
                 json_body: dict | None = None) -> dict:
        attesa = 1
        for tentativo in range(1, self._MAX_TENTATIVI + 1):
            headers = {"x-amz-access-token": self.access_token}
            url = f"{self.base}{path}"
            if metodo == "GET":
                r = requests.get(url, headers=headers, params=params or {}, timeout=60)
            else:
                r = requests.post(url, headers=headers, params=params or {}, json=json_body, timeout=60)
            if r.status_code == 429:
                if tentativo == self._MAX_TENTATIVI:
                    raise SpApiError(f"429 su {path}: rate limit dopo {tentativo} tentativi")
                log.warning("429 su %s, riprovo tra %ss (tentativo %s)", path, attesa, tentativo)
                time.sleep(attesa)
                attesa *= 2
                continue
            if r.status_code >= 400:
                raise SpApiError(f"{r.status_code} su {path}: {_dettaglio(r)}")
            return r.json()
        raise SpApiError(f"{path} uscita inattesa dal ciclo di retry")

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, json_body: dict | None = None, params: dict | None = None) -> dict:
        return self._request("POST", path, params=params, json_body=json_body)

    def attendi_operazione(self, operation_id: str, attesa_max_s: int = 300) -> dict:
        """Interroga GET .../operations/{operationId} finché operationStatus
        non è SUCCESS o FAILED. Torna il corpo finale — non alza SpApiError
        su un FAILED, solo su timeout o errore di trasporto (già gestito da
        get()): sta al chiamante decidere cosa fare di un FAILED."""
        atteso = 0
        while atteso < attesa_max_s:
            stato = self.get(f"/inbound/fba/2024-03-20/operations/{operation_id}")
            if stato.get("operationStatus") in ("SUCCESS", "FAILED"):
                return stato
            time.sleep(self._INTERVALLO_POLL_S)
            atteso += self._INTERVALLO_POLL_S
        raise SpApiError(f"operazione {operation_id} non conclusa dopo {attesa_max_s}s")
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest tests/test_spapi_client.py -v`
Expected: PASS — 10 test verdi

- [ ] **Step 5: Commit**

```bash
git add scripts/spapi_client.py tests/conftest.py tests/test_spapi_client.py
git commit -m "feat: client SP-API con auth LWA, retry e attesa operazione asincrona"
```

---

### Task 2: `modello.py` — dati canonici della spedizione

**Files:**
- Create: `scripts/modello.py`
- Test: `tests/test_modello.py`

**Interfaces:**
- Produces: `RigaSpedizione(sku, quantita)`; `OpzioneImballaggio(packing_option_id,
  descrizione)`; `OpzioneTrasporto(transportation_option_id, corriere, convenzionato,
  costo=None, valuta="EUR")`; `Spedizione(shipment_id, centro_logistico, righe=[],
  opzioni_trasporto=[], trasporto_confermato=None, etichette_path=None)`;
  `PianoSpedizione(inbound_plan_id, spedizioni=[], packing_option_id=None)`

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_modello.py
from modello import RigaSpedizione, OpzioneTrasporto, Spedizione, PianoSpedizione


def test_riga_spedizione_campi_minimi():
    r = RigaSpedizione(sku="SKU-1", quantita=30)
    assert r.sku == "SKU-1"
    assert r.quantita == 30


def test_opzione_trasporto_costo_di_default_assente():
    o = OpzioneTrasporto(transportation_option_id="to1", corriere="UPS", convenzionato=True)
    assert o.costo is None
    assert o.valuta == "EUR"


def test_spedizione_liste_di_default_vuote():
    s = Spedizione(shipment_id="sh1", centro_logistico="FC-MXP5")
    assert s.righe == []
    assert s.opzioni_trasporto == []
    assert s.trasporto_confermato is None
    assert s.etichette_path is None


def test_piano_spedizione_liste_di_default_vuote():
    p = PianoSpedizione(inbound_plan_id="wf1")
    assert p.spedizioni == []
    assert p.packing_option_id is None


def test_due_spedizioni_non_condividono_la_stessa_lista_di_righe():
    s1 = Spedizione(shipment_id="sh1", centro_logistico="FC-MXP5")
    s2 = Spedizione(shipment_id="sh2", centro_logistico="FC-BLQ1")
    s1.righe.append(RigaSpedizione(sku="SKU-1", quantita=10))
    assert s2.righe == []
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_modello.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'modello'`

- [ ] **Step 3: Scrivi `scripts/modello.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RigaSpedizione:
    sku: str
    quantita: int


@dataclass
class OpzioneImballaggio:
    packing_option_id: str
    descrizione: str


@dataclass
class OpzioneTrasporto:
    transportation_option_id: str
    corriere: str
    convenzionato: bool
    costo: float | None = None
    valuta: str = "EUR"


@dataclass
class Spedizione:
    shipment_id: str
    centro_logistico: str
    righe: list[RigaSpedizione] = field(default_factory=list)
    opzioni_trasporto: list[OpzioneTrasporto] = field(default_factory=list)
    trasporto_confermato: OpzioneTrasporto | None = None
    etichette_path: str | None = None


@dataclass
class PianoSpedizione:
    inbound_plan_id: str
    spedizioni: list[Spedizione] = field(default_factory=list)
    packing_option_id: str | None = None
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest tests/test_modello.py -v`
Expected: PASS — 5 test verdi

- [ ] **Step 5: Commit**

```bash
git add scripts/modello.py tests/test_modello.py
git commit -m "feat: modello dati canonico della spedizione"
```

---

### Task 3: `registro.py` — log JSON tentativo/esito per cliente

**Files:**
- Create: `scripts/registro.py`
- Test: `tests/test_registro.py`

**Interfaces:**
- Produces: `registra_tentativo(cliente, righe: list[dict]) -> int`;
  `registra_piano(cliente, indice, inbound_plan_id, spedizioni: list[dict]) -> None`;
  `registra_trasporto_confermato(cliente, indice, shipment_id, opzione: dict) -> None`;
  `registra_esito(cliente, indice, esito) -> None`; `ultimo_lotto(cliente) -> list[dict]`

Stessa convenzione di `amazon-inserzioni/scripts/registro.py`: un file JSON per
cliente/giorno sotto `~/.config/newera-amazon/`, mai in git. Chiamato esplicitamente
dall'agente a ogni stadio della procedura (SKILL.md), non incapsulato dentro
`piano.py`/`conferma.py`: qui il flusso è multi-stadio e interattivo (Mariano sceglie
imballaggio e corriere fra le opzioni proposte), a differenza di
`amazon-inserzioni.crea.pubblica_lotto` che è un'unica chiamata per SKU.

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_registro.py
import registro


def test_nessun_file_prima_del_primo_tentativo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert registro.ultimo_lotto("Phy to Pet") == []


def test_tentativo_poi_piano_poi_esito(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    righe = [{"sku": "SKU-1", "quantita": 30}]
    indice = registro.registra_tentativo("Phy to Pet", righe)
    riga = registro.ultimo_lotto("Phy to Pet")[indice]
    assert riga["righe"] == righe
    assert riga["esito"] == "in corso"
    assert riga["inbound_plan_id"] is None

    spedizioni = [{"shipment_id": "sh1", "centro_logistico": "FC-MXP5"}]
    registro.registra_piano("Phy to Pet", indice, "wf1", spedizioni)
    riga = registro.ultimo_lotto("Phy to Pet")[indice]
    assert riga["inbound_plan_id"] == "wf1"
    assert riga["spedizioni"] == spedizioni

    registro.registra_esito("Phy to Pet", indice, "completata")
    assert registro.ultimo_lotto("Phy to Pet")[indice]["esito"] == "completata"


def test_trasporto_confermato_aggiorna_la_spedizione_giusta(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    indice = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    registro.registra_piano("Phy to Pet", indice, "wf1", [
        {"shipment_id": "sh1", "centro_logistico": "FC-MXP5"},
        {"shipment_id": "sh2", "centro_logistico": "FC-BLQ1"},
    ])
    opzione = {"corriere": "UPS", "convenzionato": True, "costo": 42.5}
    registro.registra_trasporto_confermato("Phy to Pet", indice, "sh2", opzione)
    spedizioni = registro.ultimo_lotto("Phy to Pet")[indice]["spedizioni"]
    assert spedizioni[0].get("trasporto_confermato") is None
    assert spedizioni[1]["trasporto_confermato"] == opzione


def test_due_tentativi_non_si_sovrascrivono(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    i2 = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-2", "quantita": 5}])
    assert i1 != i2
    registro.registra_esito("Phy to Pet", i1, "completata")
    righe = registro.ultimo_lotto("Phy to Pet")
    assert righe[i1]["esito"] == "completata"
    assert righe[i2]["esito"] == "in corso"


def test_clienti_diversi_hanno_file_diversi(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    assert registro.ultimo_lotto("Bedshire") == []
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_registro.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'registro'`

- [ ] **Step 3: Scrivi `scripts/registro.py`**

```python
from __future__ import annotations
import json
import os
from datetime import datetime, timezone


def _percorso(cliente: str) -> str:
    base = os.path.expanduser("~/.config/newera-amazon/spedizioni")
    os.makedirs(base, exist_ok=True)
    data = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(base, f"{cliente}-{data}.json")


def _carica(percorso: str) -> list[dict]:
    if not os.path.exists(percorso):
        return []
    with open(percorso) as f:
        return json.load(f)


def _salva(percorso: str, log: list[dict]) -> None:
    with open(percorso, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def registra_tentativo(cliente: str, righe: list[dict]) -> int:
    """righe: [{"sku":..., "quantita":...}, ...] — il lotto richiesto da
    Mariano, prima di sapere come Amazon lo dividerà in spedizioni."""
    percorso = _percorso(cliente)
    log = _carica(percorso)
    log.append({
        "righe": righe, "ora": datetime.now(timezone.utc).isoformat(),
        "esito": "in corso", "inbound_plan_id": None, "spedizioni": [],
    })
    _salva(percorso, log)
    return len(log) - 1


def registra_piano(cliente: str, indice: int, inbound_plan_id: str, spedizioni: list[dict]) -> None:
    percorso = _percorso(cliente)
    log = _carica(percorso)
    log[indice]["inbound_plan_id"] = inbound_plan_id
    log[indice]["spedizioni"] = spedizioni
    _salva(percorso, log)


def registra_trasporto_confermato(cliente: str, indice: int, shipment_id: str, opzione: dict) -> None:
    percorso = _percorso(cliente)
    log = _carica(percorso)
    for s in log[indice]["spedizioni"]:
        if s["shipment_id"] == shipment_id:
            s["trasporto_confermato"] = opzione
    _salva(percorso, log)


def registra_esito(cliente: str, indice: int, esito: str) -> None:
    percorso = _percorso(cliente)
    log = _carica(percorso)
    log[indice]["esito"] = esito
    _salva(percorso, log)


def ultimo_lotto(cliente: str) -> list[dict]:
    return _carica(_percorso(cliente))
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest tests/test_registro.py -v`
Expected: PASS — 5 test verdi

- [ ] **Step 5: Commit**

```bash
git add scripts/registro.py tests/test_registro.py
git commit -m "feat: registro JSON tentativo/piano/esito per cliente"
```

---

### Task 4: `piano.py` — crea il piano di spedizione

**Files:**
- Create: `scripts/piano.py`
- Test: `tests/test_piano.py`

**Interfaces:**
- Consumes: `SpApiClient.post`, `SpApiClient.attendi_operazione`, `SpApiClient.get`
  (Task 1); `RigaSpedizione`, `PianoSpedizione`, `Spedizione` (Task 2); `SpApiError`
  (Task 1)
- Produces: `crea_piano(client, righe: list[RigaSpedizione], indirizzo_origine: dict,
  marketplace_id: str) -> PianoSpedizione`

> **Firma finale (post-revisione)**: invariata, ma il `PianoSpedizione` torna **senza
> spedizioni** — un `InboundPlan` appena creato non ha `shipments`, quelle nascono in
> `piazzamento.py`. In più `crea_piano` valida `quantita >= 1` e SKU non vuoto e si ferma
> prima di chiamare Amazon, nominando la riga (DESIGN §6).

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_piano.py
from unittest.mock import Mock
import pytest
from modello import RigaSpedizione
from spapi_client import SpApiError
from piano import crea_piano


def _righe():
    return [RigaSpedizione(sku="SKU-1", quantita=30), RigaSpedizione(sku="SKU-2", quantita=10)]


def _indirizzo():
    return {"name": "Phy to Pet", "addressLine1": "Via Roma 1", "city": "Prato",
            "postalCode": "59100", "countryCode": "IT"}


def test_nessuna_riga_alza_valueerror():
    with pytest.raises(ValueError, match="nessuna riga"):
        crea_piano(Mock(), [], _indirizzo(), "APJ6JRA9NG5V4")


def test_crea_piano_manda_items_e_indirizzo():
    client = Mock()
    client.post.return_value = {"inboundPlanId": "wf1", "operationId": "op1"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"inboundPlanId": "wf1", "shipments": [
        {"shipmentId": "sh1", "destinationFacilityId": "FC-MXP5",
         "items": [{"msku": "SKU-1", "quantity": 30}, {"msku": "SKU-2", "quantity": 10}]},
    ]}
    piano = crea_piano(client, _righe(), _indirizzo(), "APJ6JRA9NG5V4")
    corpo_inviato = client.post.call_args.kwargs["json_body"]
    assert corpo_inviato["sourceAddress"] == _indirizzo()
    assert corpo_inviato["destinationMarketplaces"] == ["APJ6JRA9NG5V4"]
    assert corpo_inviato["items"] == [
        {"msku": "SKU-1", "quantity": 30, "prepOwner": "SELLER", "labelOwner": "SELLER"},
        {"msku": "SKU-2", "quantity": 10, "prepOwner": "SELLER", "labelOwner": "SELLER"},
    ]
    assert piano.inbound_plan_id == "wf1"


def test_crea_piano_puo_dividersi_in_piu_spedizioni():
    client = Mock()
    client.post.return_value = {"inboundPlanId": "wf1", "operationId": "op1"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"inboundPlanId": "wf1", "shipments": [
        {"shipmentId": "sh1", "destinationFacilityId": "FC-MXP5",
         "items": [{"msku": "SKU-1", "quantity": 30}]},
        {"shipmentId": "sh2", "destinationFacilityId": "FC-BLQ1",
         "items": [{"msku": "SKU-2", "quantity": 10}]},
    ]}
    piano = crea_piano(client, _righe(), _indirizzo(), "APJ6JRA9NG5V4")
    assert len(piano.spedizioni) == 2
    assert piano.spedizioni[0].centro_logistico == "FC-MXP5"
    assert piano.spedizioni[1].centro_logistico == "FC-BLQ1"
    assert piano.spedizioni[0].righe[0].sku == "SKU-1"


def test_crea_piano_operazione_fallita_alza_spapierror_con_motivo():
    client = Mock()
    client.post.return_value = {"inboundPlanId": "wf1", "operationId": "op1"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "indirizzo di origine non valido"}],
    }
    with pytest.raises(SpApiError, match="indirizzo di origine non valido"):
        crea_piano(client, _righe(), _indirizzo(), "APJ6JRA9NG5V4")
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_piano.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'piano'`

- [ ] **Step 3: Scrivi `scripts/piano.py`**

```python
from __future__ import annotations
from modello import RigaSpedizione, PianoSpedizione, Spedizione
from spapi_client import SpApiError

_PATH_PIANI = "/inbound/fba/2024-03-20/inboundPlans"


def crea_piano(client, righe: list[RigaSpedizione], indirizzo_origine: dict,
                marketplace_id: str) -> PianoSpedizione:
    """Crea un piano di spedizione FBA. La POST è asincrona: torna
    inboundPlanId subito, ma il contenuto (come Amazon lo divide in
    spedizioni) è pronto solo a operazione SUCCESS."""
    if not righe:
        raise ValueError("nessuna riga di spedizione: SKU/quantità mancanti")
    corpo = {
        "name": f"Spedizione {righe[0].sku}",
        "sourceAddress": indirizzo_origine,
        "destinationMarketplaces": [marketplace_id],
        "items": [
            {"msku": r.sku, "quantity": r.quantita, "prepOwner": "SELLER", "labelOwner": "SELLER"}
            for r in righe
        ],
    }
    risposta = client.post(_PATH_PIANI, json_body=corpo)
    plan_id = risposta["inboundPlanId"]
    esito = client.attendi_operazione(risposta["operationId"])
    if esito["operationStatus"] != "SUCCESS":
        problemi = "; ".join(p.get("message", "") for p in esito.get("operationProblems", []))
        raise SpApiError(f"piano spedizione non creato: {problemi or 'motivo non specificato'}")
    dettaglio = client.get(f"{_PATH_PIANI}/{plan_id}")
    spedizioni = [
        Spedizione(
            shipment_id=s["shipmentId"],
            centro_logistico=s["destinationFacilityId"],
            righe=[RigaSpedizione(sku=i["msku"], quantita=i["quantity"]) for i in s["items"]],
        )
        for s in dettaglio.get("shipments", [])
    ]
    return PianoSpedizione(inbound_plan_id=plan_id, spedizioni=spedizioni)
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest tests/test_piano.py -v`
Expected: PASS — 4 test verdi

- [ ] **Step 5: Commit**

```bash
git add scripts/piano.py tests/test_piano.py
git commit -m "feat: creazione piano di spedizione FBA"
```

---

### Task 5: `imballaggio.py` — opzioni di imballaggio + conferma

**Files:**
- Create: `scripts/imballaggio.py`
- Test: `tests/test_imballaggio.py`

**Interfaces:**
- Consumes: `SpApiClient.post/get/attendi_operazione` (Task 1); `OpzioneImballaggio`
  (Task 2); `SpApiError` (Task 1)
- Produces: `genera_opzioni_imballaggio(client, plan_id: str) ->
  list[OpzioneImballaggio]`; `conferma_imballaggio(client, plan_id: str,
  packing_option_id: str) -> None`

> **Firma finale (post-revisione)**: aggiunta
> `imposta_informazioni_imballaggio(client, plan_id: str, scatole_per_gruppo:
> dict[str, list[Scatola]]) -> None` (`setPackingInformation`) — quante scatole per
> gruppo, peso e misure. Stadio obbligato: senza, Amazon non genera né piazzamenti né
> preventivi. `OpzioneImballaggio` porta ora `packing_group_ids`, e `packingGroups` è una
> lista di ID (stringhe), non di oggetti.

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_imballaggio.py
from unittest.mock import Mock
import pytest
from spapi_client import SpApiError
from imballaggio import genera_opzioni_imballaggio, conferma_imballaggio


def test_genera_opzioni_imballaggio_torna_le_opzioni_proposte():
    client = Mock()
    client.post.return_value = {"operationId": "op1"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"packingOptions": [
        {"packingOptionId": "po1", "packingGroups": [{"packingGroupId": "g1"}]},
        {"packingOptionId": "po2", "packingGroups": [{"packingGroupId": "g1"}, {"packingGroupId": "g2"}]},
    ]}
    opzioni = genera_opzioni_imballaggio(client, "wf1")
    assert [o.packing_option_id for o in opzioni] == ["po1", "po2"]
    assert opzioni[1].descrizione == "2 gruppo/i di imballaggio"
    client.post.assert_called_with("/inbound/fba/2024-03-20/inboundPlans/wf1/packingOptions")


def test_genera_opzioni_imballaggio_operazione_fallita():
    client = Mock()
    client.post.return_value = {"operationId": "op1"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "quantità non compatibile con imballaggio standard"}],
    }
    with pytest.raises(SpApiError, match="quantità non compatibile"):
        genera_opzioni_imballaggio(client, "wf1")


def test_conferma_imballaggio_chiama_l_endpoint_dell_opzione_scelta():
    client = Mock()
    client.post.return_value = {"operationId": "op2"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    conferma_imballaggio(client, "wf1", "po1")
    client.post.assert_called_with("/inbound/fba/2024-03-20/inboundPlans/wf1/packingOptions/po1/confirmation")


def test_conferma_imballaggio_fallita_alza_errore():
    client = Mock()
    client.post.return_value = {"operationId": "op2"}
    client.attendi_operazione.return_value = {"operationStatus": "FAILED", "operationProblems": []}
    with pytest.raises(SpApiError, match="motivo non specificato"):
        conferma_imballaggio(client, "wf1", "po1")
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_imballaggio.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'imballaggio'`

- [ ] **Step 3: Scrivi `scripts/imballaggio.py`**

```python
from __future__ import annotations
from modello import OpzioneImballaggio
from spapi_client import SpApiError


def _path_opzioni_imballaggio(plan_id: str) -> str:
    return f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/packingOptions"


def genera_opzioni_imballaggio(client, plan_id: str) -> list[OpzioneImballaggio]:
    """Chiede ad Amazon come impacchettare il piano (POST asincrona), poi
    legge le opzioni proposte."""
    risposta = client.post(_path_opzioni_imballaggio(plan_id))
    esito = client.attendi_operazione(risposta["operationId"])
    if esito["operationStatus"] != "SUCCESS":
        problemi = "; ".join(p.get("message", "") for p in esito.get("operationProblems", []))
        raise SpApiError(f"opzioni imballaggio non generate: {problemi or 'motivo non specificato'}")
    elenco = client.get(_path_opzioni_imballaggio(plan_id))
    return [
        OpzioneImballaggio(
            packing_option_id=o["packingOptionId"],
            descrizione=f"{len(o.get('packingGroups', []))} gruppo/i di imballaggio",
        )
        for o in elenco.get("packingOptions", [])
    ]


def conferma_imballaggio(client, plan_id: str, packing_option_id: str) -> None:
    risposta = client.post(f"{_path_opzioni_imballaggio(plan_id)}/{packing_option_id}/confirmation")
    esito = client.attendi_operazione(risposta["operationId"])
    if esito["operationStatus"] != "SUCCESS":
        problemi = "; ".join(p.get("message", "") for p in esito.get("operationProblems", []))
        raise SpApiError(f"imballaggio non confermato: {problemi or 'motivo non specificato'}")
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest tests/test_imballaggio.py -v`
Expected: PASS — 4 test verdi

- [ ] **Step 5: Commit**

```bash
git add scripts/imballaggio.py tests/test_imballaggio.py
git commit -m "feat: opzioni di imballaggio proposte da Amazon + conferma"
```

---

### Task 6: `corrieri.py` — opzioni di trasporto (convenzionato + proprio)

**Files:**
- Create: `scripts/corrieri.py`
- Test: `tests/test_corrieri.py`

**Interfaces:**
- Consumes: `SpApiClient.post/get/attendi_operazione` (Task 1); `OpzioneTrasporto`
  (Task 2); `SpApiError` (Task 1)
- Produces: `genera_opzioni_trasporto(client, plan_id: str, shipment_id: str) ->
  list[OpzioneTrasporto]`

> **Firma finale (post-revisione)**:
> `genera_opzioni_trasporto(client, plan_id, placement_option_id, shipment_ids,
> data_pronta, contatto=None, pallet=None) -> list[OpzioneTrasporto]`.
> `generateTransportationOptions` è di **piano**, non di spedizione, e vuole un corpo:
> `placementOptionId` + una configurazione per spedizione con la data di merce pronta
> (`readyToShipWindow.start`) e, per i bancali, `pallets` — senza i quali Amazon non
> genera le opzioni LTL. Il preventivo si legge da `quote.cost.amount` / `quote.cost.code`.
> Aggiunta `genera_finestre_consegna(client, plan_id, shipment_id) ->
> list[FinestraConsegna]`: le opzioni con corriere proprio hanno la precondizione
> `CONFIRMED_DELIVERY_WINDOW` e senza finestra confermata non si prenotano.

Mariano usa entrambe le modalità di spedizione a seconda del costo (deciso il
2026-08-29): questa funzione deve tornare **entrambe** le opzioni quando Amazon le offre
— mai scegliere lei stessa. Il prezzo si riporta **sempre quando Amazon lo quota**, anche
su un'opzione non convenzionata: `convenzionato` etichetta il tipo di opzione, non decide
se un costo esiste.

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_corrieri.py
from unittest.mock import Mock
import pytest
from spapi_client import SpApiError
from corrieri import genera_opzioni_trasporto


def test_opzione_convenzionata_ha_il_prezzo():
    client = Mock()
    client.post.return_value = {"operationId": "op1"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"transportationOptions": [
        {"transportationOptionId": "to1", "shippingSolution": "AMAZON_PARTNERED_CARRIER",
         "carrier": {"name": "UPS"}, "quote": {"amount": 42.5, "currencyCode": "EUR"}},
    ]}
    opzioni = genera_opzioni_trasporto(client, "wf1", "sh1")
    assert opzioni[0].convenzionato is True
    assert opzioni[0].costo == 42.5
    assert opzioni[0].corriere == "UPS"
    client.post.assert_called_with(
        "/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1/transportationOptions"
    )


def test_opzione_corriere_proprio_senza_prezzo():
    client = Mock()
    client.post.return_value = {"operationId": "op1"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"transportationOptions": [
        {"transportationOptionId": "to2", "shippingSolution": "USE_YOUR_OWN_CARRIER",
         "carrier": {"name": "corriere proprio"}},
    ]}
    opzioni = genera_opzioni_trasporto(client, "wf1", "sh1")
    assert opzioni[0].convenzionato is False
    assert opzioni[0].costo is None


def test_entrambe_le_opzioni_insieme_quando_amazon_le_offre():
    client = Mock()
    client.post.return_value = {"operationId": "op1"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.return_value = {"transportationOptions": [
        {"transportationOptionId": "to1", "shippingSolution": "AMAZON_PARTNERED_CARRIER",
         "carrier": {"name": "UPS"}, "quote": {"amount": 42.5, "currencyCode": "EUR"}},
        {"transportationOptionId": "to2", "shippingSolution": "USE_YOUR_OWN_CARRIER",
         "carrier": {"name": "corriere proprio"}},
    ]}
    opzioni = genera_opzioni_trasporto(client, "wf1", "sh1")
    assert len(opzioni) == 2
    assert [o.convenzionato for o in opzioni] == [True, False]


def test_generazione_fallita_alza_errore_col_motivo():
    client = Mock()
    client.post.return_value = {"operationId": "op1"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "nessun corriere disponibile per questa destinazione"}],
    }
    with pytest.raises(SpApiError, match="nessun corriere disponibile"):
        genera_opzioni_trasporto(client, "wf1", "sh1")
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_corrieri.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'corrieri'`

- [ ] **Step 3: Scrivi `scripts/corrieri.py`**

```python
from __future__ import annotations
from modello import OpzioneTrasporto
from spapi_client import SpApiError


def _path_opzioni_trasporto(plan_id: str, shipment_id: str) -> str:
    return f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/shipments/{shipment_id}/transportationOptions"


def genera_opzioni_trasporto(client, plan_id: str, shipment_id: str) -> list[OpzioneTrasporto]:
    """Torna tutte le opzioni che Amazon offre per questa spedizione — sia
    il corriere convenzionato (con prezzo) sia il proprio (solo etichetta),
    quando entrambi disponibili. Non sceglie: la scelta è di Mariano."""
    risposta = client.post(_path_opzioni_trasporto(plan_id, shipment_id))
    esito = client.attendi_operazione(risposta["operationId"])
    if esito["operationStatus"] != "SUCCESS":
        problemi = "; ".join(p.get("message", "") for p in esito.get("operationProblems", []))
        raise SpApiError(f"opzioni di trasporto non generate: {problemi or 'motivo non specificato'}")
    elenco = client.get(_path_opzioni_trasporto(plan_id, shipment_id))
    opzioni = []
    for o in elenco.get("transportationOptions", []):
        convenzionato = o.get("shippingSolution") == "AMAZON_PARTNERED_CARRIER"
        quota = o.get("quote") or {}
        opzioni.append(OpzioneTrasporto(
            transportation_option_id=o["transportationOptionId"],
            corriere=o.get("carrier", {}).get("name", "corriere proprio"),
            convenzionato=convenzionato,
            costo=quota.get("amount") if convenzionato else None,
            valuta=quota.get("currencyCode", "EUR"),
        ))
    return opzioni
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest tests/test_corrieri.py -v`
Expected: PASS — 4 test verdi

- [ ] **Step 5: Commit**

```bash
git add scripts/corrieri.py tests/test_corrieri.py
git commit -m "feat: opzioni di trasporto, convenzionato e proprio"
```

---

### Task 7: `conferma.py` — prenota il trasporto scelto

**Files:**
- Create: `scripts/conferma.py`
- Test: `tests/test_conferma.py`

**Interfaces:**
- Consumes: `SpApiClient.post/attendi_operazione` (Task 1); `OpzioneTrasporto` (Task 2);
  `SpApiError` (Task 1)
- Produces: `conferma_trasporto(client, plan_id: str, shipment_id: str, opzione:
  OpzioneTrasporto) -> None`

> **Firma finale (post-revisione)**:
> `conferma_trasporto(client, plan_id, scelte: list[OpzioneTrasporto], contatto=None)
> -> None`. `confirmTransportationOptions` è di **piano**: una chiamata sola con
> `transportationSelections`, una scelta per spedizione (ecco perché `OpzioneTrasporto`
> porta ora `shipment_id`). Aggiunta
> `conferma_finestra_consegna(client, plan_id, shipment_id, delivery_window_option_id)
> -> None`, da chiamare **prima** per ogni spedizione con precondizione
> `CONFIRMED_DELIVERY_WINDOW`: è ciò che rende possibile il corriere proprio.

Il cancello di conferma (prezzo mostrato + "vai" esplicito di Mariano, DESIGN
autonomia) vive nell'orchestrazione (SKILL.md), **prima** di chiamare questa funzione:
qui si assume che il permesso ci sia già stato. Il testo da mostrare al cancello è
quello di `anteprima.riepilogo_conferma`, non il menù di `anteprima.riepilogo`.

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_conferma.py
from unittest.mock import Mock
import pytest
from modello import OpzioneTrasporto
from spapi_client import SpApiError
from conferma import conferma_trasporto


def test_conferma_trasporto_chiama_l_endpoint_dell_opzione_scelta():
    client = Mock()
    client.post.return_value = {"operationId": "op3"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    opzione = OpzioneTrasporto(transportation_option_id="to1", corriere="UPS", convenzionato=True, costo=42.5)
    conferma_trasporto(client, "wf1", "sh1", opzione)
    client.post.assert_called_with(
        "/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1/transportationOptions/to1/confirmation"
    )


def test_conferma_trasporto_self_ship_stesso_flusso():
    client = Mock()
    client.post.return_value = {"operationId": "op4"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    opzione = OpzioneTrasporto(transportation_option_id="to2", corriere="corriere proprio", convenzionato=False)
    conferma_trasporto(client, "wf1", "sh1", opzione)  # non solleva


def test_conferma_trasporto_fallita_alza_errore_col_motivo():
    client = Mock()
    client.post.return_value = {"operationId": "op3"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "finestra di ritiro non più disponibile"}],
    }
    opzione = OpzioneTrasporto(transportation_option_id="to1", corriere="UPS", convenzionato=True, costo=42.5)
    with pytest.raises(SpApiError, match="finestra di ritiro"):
        conferma_trasporto(client, "wf1", "sh1", opzione)
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_conferma.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'conferma'`

- [ ] **Step 3: Scrivi `scripts/conferma.py`**

```python
from __future__ import annotations
from spapi_client import SpApiError


def _path_conferma_trasporto(plan_id: str, shipment_id: str, transportation_option_id: str) -> str:
    return (f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/shipments/{shipment_id}"
            f"/transportationOptions/{transportation_option_id}/confirmation")


def conferma_trasporto(client, plan_id: str, shipment_id: str, opzione) -> None:
    """Prenota il corriere scelto — convenzionato Amazon (spesa reale) o
    proprio (self-ship). Va chiamata SOLO dopo che Mariano ha visto il
    prezzo, se c'è, e ha detto 'vai': il cancello di conferma vive
    nell'orchestrazione (SKILL.md), non qui."""
    risposta = client.post(_path_conferma_trasporto(plan_id, shipment_id, opzione.transportation_option_id))
    esito = client.attendi_operazione(risposta["operationId"])
    if esito["operationStatus"] != "SUCCESS":
        problemi = "; ".join(p.get("message", "") for p in esito.get("operationProblems", []))
        raise SpApiError(f"trasporto non confermato: {problemi or 'motivo non specificato'}")
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest tests/test_conferma.py -v`
Expected: PASS — 3 test verdi

- [ ] **Step 5: Commit**

```bash
git add scripts/conferma.py tests/test_conferma.py
git commit -m "feat: conferma/prenotazione del trasporto scelto"
```

---

### Task 8: `etichette.py` — scarica le etichette, salva sul Desktop

**Files:**
- Create: `scripts/etichette.py`
- Test: `tests/test_etichette.py`

**Interfaces:**
- Consumes: `SpApiClient.get` (Task 1); `SpApiError` (Task 1)
- Produces: `scarica_etichette(client, plan_id: str, shipment_id: str, cliente: str) ->
  str` (percorso del PDF salvato)

> **Firma finale (post-revisione)**:
> `scarica_etichette(client, shipment_confirmation_id, cliente,
> tipo_pagina="PackageLabel_Plain_Paper", tipo_etichetta="UNIQUE") -> str`.
> `getLabels` **non esiste in v2024-03-20**: è una delle operazioni v0 che Amazon ha
> conservato — `GET /fba/inbound/v0/shipments/{shipmentConfirmationId}/labels`, parametri
> `PageType`/`LabelType` in PascalCase, risposta in `payload.DownloadURL`. Niente
> `plan_id`, e l'identificativo è lo `shipmentConfirmationId` (stile `FBA1234ABCD`) che
> nasce con `confirmPlacementOption`, non lo `shipmentId` di v2024-03-20.

Stesso pattern del download report SP-API già collaudato nel monitor: l'endpoint torna
un URL temporaneo (valido pochi secondi), si scarica il contenuto con una richiesta
separata.

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_etichette.py
import os
from unittest.mock import Mock, patch
import pytest
from spapi_client import SpApiError
from etichette import scarica_etichette


@patch("etichette.requests.get")
def test_scarica_etichette_salva_il_pdf_sul_desktop(mock_get, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"downloadURL": "https://amazon-labels.example/x.pdf"}
    risposta_pdf = Mock()
    risposta_pdf.status_code = 200
    risposta_pdf.content = b"%PDF-1.4 finto contenuto"
    mock_get.return_value = risposta_pdf

    percorso = scarica_etichette(client, "wf1", "sh1", "Phy to Pet")

    assert percorso == str(tmp_path / "Desktop" / "Etichette-Phy to Pet-sh1.pdf")
    assert os.path.exists(percorso)
    with open(percorso, "rb") as f:
        assert f.read() == b"%PDF-1.4 finto contenuto"
    client.get.assert_called_with(
        "/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1/labels",
        params={"pageType": "PackageLabel_Plain_Paper", "labelType": "UNIQUE"},
    )


def test_scarica_etichette_senza_url_alza_errore():
    client = Mock()
    client.get.return_value = {}
    with pytest.raises(SpApiError, match="nessun URL etichetta"):
        scarica_etichette(client, "wf1", "sh1", "Phy to Pet")


@patch("etichette.requests.get")
def test_scarica_etichette_download_fallito_alza_errore(mock_get, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"downloadURL": "https://amazon-labels.example/x.pdf"}
    risposta_errore = Mock()
    risposta_errore.status_code = 403
    mock_get.return_value = risposta_errore
    with pytest.raises(SpApiError, match="403"):
        scarica_etichette(client, "wf1", "sh1", "Phy to Pet")
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_etichette.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'etichette'`

- [ ] **Step 3: Scrivi `scripts/etichette.py`**

```python
from __future__ import annotations
import os
import requests
from spapi_client import SpApiError


def scarica_etichette(client, plan_id: str, shipment_id: str, cliente: str) -> str:
    """Le etichette sono un PDF ospitato su un URL temporaneo, stesso
    pattern del download report SP-API. Salva sul Desktop, come gli altri
    PDF prodotti dalle skill New Era."""
    risposta = client.get(
        f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/shipments/{shipment_id}/labels",
        params={"pageType": "PackageLabel_Plain_Paper", "labelType": "UNIQUE"},
    )
    url = risposta.get("downloadURL")
    if not url:
        raise SpApiError(f"nessun URL etichetta per la spedizione {shipment_id}")
    scaricato = requests.get(url, timeout=120)
    if scaricato.status_code >= 400:
        raise SpApiError(f"download etichette {shipment_id} -> {scaricato.status_code}")
    percorso = os.path.expanduser(f"~/Desktop/Etichette-{cliente}-{shipment_id}.pdf")
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "wb") as f:
        f.write(scaricato.content)
    return percorso
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest tests/test_etichette.py -v`
Expected: PASS — 3 test verdi

- [ ] **Step 5: Commit**

```bash
git add scripts/etichette.py tests/test_etichette.py
git commit -m "feat: download etichette di spedizione sul Desktop"
```

---

### Task 9: `anteprima.py` — riepilogo per la chat

**Files:**
- Create: `scripts/anteprima.py`
- Test: `tests/test_anteprima.py`

**Interfaces:**
- Consumes: `PianoSpedizione`, `Spedizione`, `RigaSpedizione`, `OpzioneTrasporto`
  (Task 2)
- Produces: `riepilogo(piano: PianoSpedizione) -> str`

> **Firma finale (post-revisione)**: aggiunta
> `riepilogo_conferma(spedizione: Spedizione, opzione: OpzioneTrasporto) -> str` — il
> testo del cancello, che nomina una spedizione, un corriere e un prezzo soli, così che
> il "vai" sia legato a una scelta e non a un elenco. `riepilogo` resta il menù da
> sfogliare. Quando Amazon non quota un prezzo il testo dice "nessun costo quotato da
> Amazon": l'assenza di preventivo non si racconta come gratuità.

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_anteprima.py
from modello import PianoSpedizione, Spedizione, RigaSpedizione, OpzioneTrasporto
from anteprima import riepilogo


def test_riepilogo_mostra_pezzi_e_destinazione():
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[
        Spedizione(shipment_id="sh1", centro_logistico="FC-MXP5",
                   righe=[RigaSpedizione(sku="SKU-1", quantita=30), RigaSpedizione(sku="SKU-2", quantita=10)]),
    ])
    testo = riepilogo(piano)
    assert "FC-MXP5" in testo
    assert "40 pezzi" in testo
    assert "2 SKU" in testo


def test_riepilogo_senza_opzioni_di_trasporto_lo_dice():
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[
        Spedizione(shipment_id="sh1", centro_logistico="FC-MXP5", righe=[RigaSpedizione(sku="SKU-1", quantita=30)]),
    ])
    assert "nessuna opzione di trasporto" in riepilogo(piano)


def test_riepilogo_mostra_il_prezzo_del_convenzionato():
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[
        Spedizione(shipment_id="sh1", centro_logistico="FC-MXP5",
                   righe=[RigaSpedizione(sku="SKU-1", quantita=30)],
                   opzioni_trasporto=[OpzioneTrasporto("to1", "UPS", True, 42.5, "EUR")]),
    ])
    testo = riepilogo(piano)
    assert "42.50 EUR" in testo
    assert "convenzionato Amazon" in testo


def test_riepilogo_corriere_proprio_dice_solo_etichetta():
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[
        Spedizione(shipment_id="sh1", centro_logistico="FC-MXP5",
                   righe=[RigaSpedizione(sku="SKU-1", quantita=30)],
                   opzioni_trasporto=[OpzioneTrasporto("to2", "corriere proprio", False)]),
    ])
    testo = riepilogo(piano)
    assert "solo etichetta" in testo
    assert "corriere proprio" in testo


def test_riepilogo_piu_spedizioni_le_mostra_tutte():
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[
        Spedizione(shipment_id="sh1", centro_logistico="FC-MXP5", righe=[RigaSpedizione(sku="SKU-1", quantita=30)]),
        Spedizione(shipment_id="sh2", centro_logistico="FC-BLQ1", righe=[RigaSpedizione(sku="SKU-2", quantita=10)]),
    ])
    testo = riepilogo(piano)
    assert "sh1" in testo and "sh2" in testo
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_anteprima.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'anteprima'`

- [ ] **Step 3: Scrivi `scripts/anteprima.py`**

```python
from __future__ import annotations
from modello import PianoSpedizione


def riepilogo(piano: PianoSpedizione) -> str:
    righe_testo = [f"Piano spedizione {piano.inbound_plan_id} — {len(piano.spedizioni)} spedizione/i:"]
    for s in piano.spedizioni:
        pezzi = sum(r.quantita for r in s.righe)
        righe_testo.append(f"\n- {s.shipment_id} → {s.centro_logistico}: {pezzi} pezzi ({len(s.righe)} SKU)")
        if not s.opzioni_trasporto:
            righe_testo.append("  nessuna opzione di trasporto ancora generata")
        for o in s.opzioni_trasporto:
            prezzo = f"{o.costo:.2f} {o.valuta}" if o.costo is not None else "prezzo non disponibile, solo etichetta"
            tipo = "convenzionato Amazon" if o.convenzionato else "corriere proprio"
            righe_testo.append(f"  · {o.corriere} ({tipo}) — {prezzo}")
    return "\n".join(righe_testo)
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest tests/test_anteprima.py -v`
Expected: PASS — 5 test verdi

- [ ] **Step 5: Commit**

```bash
git add scripts/anteprima.py tests/test_anteprima.py
git commit -m "feat: riepilogo spedizione per l'anteprima in chat"
```

---

### Task 10: `SKILL.md` + prova a vuoto end-to-end

**Files:**
- Create: `SKILL.md`
- Test: `tests/test_prova_a_vuoto.py`

**Interfaces:**
- Consumes: tutti i moduli dei Task 1-9

Chiude il piano con un test che percorre l'intera catena su fixture — piano →
imballaggio → piazzamento → trasporto → anteprima → conferma → etichette — mostrando
esattamente cosa succederebbe con Amazon vero, senza fare nessuna chiamata di rete reale
(DESIGN §8: "prova a vuoto").

> **Post-revisione**: il client finto non è più un `Mock` piatto. Risponde per percorso
> (un endpoint sbagliato non trova fixture) e `attendi_operazione` risponde **solo**
> all'`operationId` che quella POST ha davvero aperto, così che uno stadio che
> interrogasse l'operazione di un altro venga scoperto invece di passare inosservato.

Nota: a differenza dei Task 1-9, qui non c'è produzione nuova da guidare con un test
rosso — tutti i moduli esistono già. Questo è un test di integrazione: si scrive, si fa
girare, e deve passare subito perché verifica che i pezzi già collaudati singolarmente
si compongano correttamente end-to-end.

- [ ] **Step 1: Scrivi `tests/test_prova_a_vuoto.py`**

> **Post-revisione**: il test ricopiato qui sotto è quello della prima stesura ed è
> **superato**. Quello vero non usa un `Mock` piatto (client finto che risponde per
> percorso, vedi sopra), percorre gli stadi mancanti — informazioni di imballaggio,
> piazzamento, conferma del piazzamento — e chiama `registro.*` negli stessi punti di
> `SKILL.md` con gli oggetti veri degli stadi. Era proprio l'assenza di quelle chiamate a
> nascondere il fatto che il registro non sapesse scrivere le dataclass: non un difetto
> vecchio, ma un difetto che nessun test poteva vedere, perché la prova a vuoto il
> registro non lo toccava affatto. Verifica anche l'ordine, non solo il contenuto: la
> commissione di piazzamento dev'essere su disco prima della rilettura delle spedizioni.
> Leggere `tests/test_prova_a_vuoto.py`, non questo blocco.

```python
# tests/test_prova_a_vuoto.py
from unittest.mock import Mock, patch
from modello import RigaSpedizione
from piano import crea_piano
from imballaggio import genera_opzioni_imballaggio, conferma_imballaggio
from corrieri import genera_opzioni_trasporto
from conferma import conferma_trasporto
from etichette import scarica_etichette
from anteprima import riepilogo


def test_flusso_end_to_end_su_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.post.side_effect = [
        {"inboundPlanId": "wf1", "operationId": "op-piano"},
        {"operationId": "op-pack"},
        {"operationId": "op-pack-conf"},
        {"operationId": "op-trasp"},
        {"operationId": "op-trasp-conf"},
    ]
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    client.get.side_effect = [
        {"inboundPlanId": "wf1", "shipments": [
            {"shipmentId": "sh1", "destinationFacilityId": "FC-MXP5",
             "items": [{"msku": "SKU-1", "quantity": 30}]},
        ]},
        {"packingOptions": [{"packingOptionId": "po1", "packingGroups": [{"packingGroupId": "g1"}]}]},
        {"transportationOptions": [
            {"transportationOptionId": "to1", "shippingSolution": "AMAZON_PARTNERED_CARRIER",
             "carrier": {"name": "UPS"}, "quote": {"amount": 42.5, "currencyCode": "EUR"}},
        ]},
        {"downloadURL": "https://amazon-labels.example/x.pdf"},  # consumato da scarica_etichette, in fondo
    ]
    righe = [RigaSpedizione(sku="SKU-1", quantita=30)]
    indirizzo = {"name": "Phy to Pet", "addressLine1": "Via Roma 1", "city": "Prato",
                 "postalCode": "59100", "countryCode": "IT"}

    piano = crea_piano(client, righe, indirizzo, "APJ6JRA9NG5V4")
    opzioni_imballaggio = genera_opzioni_imballaggio(client, piano.inbound_plan_id)
    conferma_imballaggio(client, piano.inbound_plan_id, opzioni_imballaggio[0].packing_option_id)

    spedizione = piano.spedizioni[0]
    spedizione.opzioni_trasporto = genera_opzioni_trasporto(client, piano.inbound_plan_id, spedizione.shipment_id)
    scelta = spedizione.opzioni_trasporto[0]

    testo_anteprima = riepilogo(piano)
    assert "42.50 EUR" in testo_anteprima
    assert "FC-MXP5" in testo_anteprima

    # a questo punto, nella skill vera, l'agente si ferma e mostra
    # testo_anteprima aspettando il "vai" esplicito di Mariano — qui
    # simulato, l'orchestrazione reale vive in SKILL.md
    conferma_trasporto(client, piano.inbound_plan_id, spedizione.shipment_id, scelta)

    with patch("etichette.requests.get") as mock_download:
        risposta_pdf = Mock()
        risposta_pdf.status_code = 200
        risposta_pdf.content = b"%PDF-1.4 finto"
        mock_download.return_value = risposta_pdf
        percorso = scarica_etichette(client, piano.inbound_plan_id, spedizione.shipment_id, "Phy to Pet")

    assert percorso.endswith("Etichette-Phy to Pet-sh1.pdf")
```

- [ ] **Step 2: Verifica che passi subito**

Run: `python3 -m pytest tests/test_prova_a_vuoto.py -v`
Expected: PASS — 1 test verde (integra i Task 1-9, già tutti collaudati singolarmente)

- [ ] **Step 3: Scrivi `SKILL.md`**

> **Post-revisione**: la procedura ricopiata qui sotto è quella della prima stesura ed è
> **superata**. Il `SKILL.md` vero contiene gli stadi mancanti (piazzamento, informazioni
> di imballaggio, finestra di consegna), le chiamate a `registro.*` inline al punto in cui
> vanno fatte davvero — `registra_tentativo` prima di creare il piano, non alla fine — e
> dice esplicitamente su quale testo si chiede il "vai" e quali campi l'orchestratore
> riassegna dopo ogni stadio. Leggere `SKILL.md`, non questo blocco.

```markdown
---
name: amazon-spedizioni
description: Crea spedizioni verso la logistica Amazon (FBA) per i clienti Seller Central di New Era - dal piano spedizione fino alle etichette da stampare, con anteprima di corriere e prezzo e conferma esplicita prima di ogni prenotazione. Usare quando Mariano chiede di spedire prodotto in FBA, creare una spedizione Amazon, mandare stock al magazzino Amazon per un cliente. Trigger tipici: "spedisci questi articoli ad Amazon per X", "crea una spedizione FBA per X", "manda queste unità al magazzino Amazon di X", "spedizione logistica Amazon per X". NON è amazon-inserzioni (quella crea/aggiorna le inserzioni, non spedisce stock) né il monitor salute account (quello legge, non spedisce). NON tocca Vendor Central. Autonomia gialla - corriere e prezzo sempre mostrati prima, mai una prenotazione senza un "vai" esplicito. Un cliente, un marketplace, alla volta.
---

# Spedizioni Amazon FBA — Seller Central

Vedi `DESIGN.md` per l'architettura completa e `PLAN.md` per i task di implementazione.

## Procedura

1. Mariano detta SKU + quantità da spedire (mai dedotti da soli).
2. `piano.crea_piano(client, righe, indirizzo_origine, marketplace_id)` — crea il piano
   su Amazon, che decide destinazione/i e come si divide in una o più spedizioni.
   `indirizzo_origine` e `marketplace_id` vengono dalla configurazione locale del
   cliente, non si inventano.
3. `imballaggio.genera_opzioni_imballaggio` → mostra le opzioni a Mariano →
   `imballaggio.conferma_imballaggio` sull'opzione scelta.
4. Per ogni spedizione del piano: `corrieri.genera_opzioni_trasporto` — mostra sia il
   corriere convenzionato Amazon (con prezzo) sia l'opzione corriere proprio (solo
   etichetta), quando entrambe disponibili.
5. `anteprima.riepilogo(piano)` — mostra tutto in chat: pacchi, destinazione, corriere/i,
   prezzo.
6. **"vai" esplicito di Mariano** — obbligatorio, non si deduce, anche quando il costo è
   assente (corriere proprio).
7. `conferma.conferma_trasporto` — prenota (convenzionato) o marca self-ship (proprio).
8. `etichette.scarica_etichette` — salva il PDF sul Desktop.
9. `registro.registra_tentativo` / `registra_piano` / `registra_trasporto_confermato` /
   `registra_esito` — un tentativo per lotto, chiamato esplicitamente a ogni stadio.

## Cosa non fa

Non decide da sola cosa o quanto spedire; non prenota corrieri non convenzionati (li
chiama New Era); non traccia la spedizione dopo la creazione; non tocca Vendor Central.
Vedi `DESIGN.md` §3, §11.

## Credenziali

`SPAPI_LWA_CLIENT_ID` / `SPAPI_LWA_CLIENT_SECRET` in ambiente + refresh token
dell'account Phy to Pet da configurazione locale mai committata. Le inserisce e le
rigenera solo Mariano — vedi `DESIGN.md` §7.
```

- [ ] **Step 4: Verifica che il test end-to-end passi**

Run: `python3 -m pytest tests/ -v`
Expected: PASS — tutti i test dei Task 1-10 verdi (44 test totali: 10+5+5+4+4+4+3+3+5+1)

- [ ] **Step 5: Commit**

```bash
git add SKILL.md tests/test_prova_a_vuoto.py
git commit -m "feat: SKILL.md e prova a vuoto end-to-end"
```

---

### Task 11: Collaudo dal vivo — **in corso** (orchestratore: vedi `PLAN-orchestratore.md`)

> **Stato al 2026-09-02.** Step 1-3 fatti (29/08 su Phy to Pet fino al cancello, 31/08 su
> Bedshire oltre il cancello: piano `wf017a1157`, spedizione `FBA15M9GM6FJ` verso TRN3,
> corriere proprio LTL su 2 pallet, commissione di piazzamento 0 EUR, etichette cartone
> scaricate). Step 5 fatto solo nel ramo **senza** finestra di consegna (l'opzione LTL
> del corriere proprio non la richiedeva: `conferma_finestra_consegna` resta mai
> esercitata dal vivo). **Restano**: Step 4 (corriere convenzionato Amazon, l'unico che
> spende davvero — su Phy to Pet era quotato UPS 7,90 EUR), il ramo con finestra di
> consegna, le etichette pallet, e la verifica a occhio dell'etichetta (Step 6).
> Dal 02/09 le credenziali si prendono con `crea_client(cliente)` e indirizzo/marketplace/
> contatto da `clienti.yaml` (`clienti.carica_cliente`): prima si passavano a mano ed è
> costato tre tentativi falliti il 31/08 (app LWA sbagliata, marketplace inventato,
> telefono mancante).

**Precondizioni** (DESIGN §9, nessuna delle due esiste oggi):

1. App SP-API creata da Mariano con il ruolo che copre la Fulfillment Inbound API
   (verificato in fase di richiesta app, non solo Listings/Pricing/Inventory)
2. Credenziali rigenerate per l'account Seller di Phy to Pet: `SPAPI_LWA_CLIENT_ID`,
   `SPAPI_LWA_CLIENT_SECRET`, refresh token — in un file locale mai committato

Quando entrambe sono disponibili, l'esecutore di questo task:

- [ ] **Step 1: Configura le credenziali nell'ambiente locale** (mai in chat, mai in un
  file del repo — segue la stessa convenzione già in uso per `amazon-inserzioni`)

- [ ] **Step 2: Un lotto vero e piccolo** — 1 SKU, quantità minima sensata (es. 5-10
  pezzi) — su Phy to Pet: `crea_piano` → `imballaggio` → `piazzamento`, verificando a
  occhio che le spedizioni compaiano davvero solo con `listPlacementOptions`
  (`placementOptions[].shipmentIds`) e che `getShipment` torni `destination.warehouseId`
  e, dopo la conferma, `shipmentConfirmationId`

- [ ] **Step 3: Confrontare le risposte vere con le fixture dei test** — le forme sono
  già state verificate sul modello ufficiale (DESIGN §10): qui si controlla solo che il
  vivo corrisponda. Punti da guardare: `packingOptions[].packingGroups` (lista di ID),
  `transportationOptions[].quote.cost.amount/code`, `shippingSolution`
  (`AMAZON_PARTNERED_CARRIER` / `USE_YOUR_OWN_CARRIER`), `preconditions`
  (`CONFIRMED_DELIVERY_WINDOW` sul corriere proprio). Se il vivo diverge, correggere il
  modulo con un test di fixture aggiornato per ciascuna correzione

- [ ] **Step 4: Corriere convenzionato con importo minimo verificabile** — confermare il
  trasporto, controllare che l'addebito segnalato da Amazon corrisponda al prezzo
  mostrato in anteprima prima del "vai"

- [ ] **Step 5: Corriere proprio** — l'altra modalità che Mariano usa davvero: verificare
  che l'opzione `USE_YOUR_OWN_CARRIER` arrivi con `CONFIRMED_DELIVERY_WINDOW`, che
  `conferma_finestra_consegna` passi, e che senza finestra la prenotazione venga
  effettivamente rifiutata (è il motivo per cui quello stadio esiste)

- [ ] **Step 6: Etichetta generata** — verificarla a occhio (leggibile, SKU/spedizione
  corretti) prima di considerare il collaudo riuscito

- [ ] **Step 6: Se qualcosa nella forma reale diverge dalle fixture assunte**, aggiornare
  il modulo coinvolto e il suo test, ricommittare con messaggio che cita cosa è cambiato
  e perché (non silenziosamente — anche gli altri due progetti gemelli condividono lo
  stesso account, un errore di forma qui vale la pena segnalarlo anche a loro)

- [x] **Step 7 (02/09): `python3 giro.py riprendi wf0dae3b7c-919c-4efb-8761-c93d7126908f --lotto lotto.yaml`**
  su Phy to Pet, col lotto da 20 DENTIFRICIO_SPRAY_DUE in 2 scatole (10×25×15 cm, 0,8 kg) e
  data pronta a scelta di Mariano. Atteso: stadio PIAZZAMENTO_DA_GENERARE riconosciuto,
  piazzamenti e preventivi rigenerati, menù con UPS convenzionato e corriere proprio,
  **nessuna POST di conferma**. È il piano che serve allo Step 4 (corriere convenzionato).
  **Esito 02/09 sera → 03/09 00:02**: fatto due volte. Stadio riconosciuto PIAZZAMENTO_DA_GENERARE,
  piazzamenti rigenerati (TRN3 e FCO8, commissione 0), preventivi letti; `conferma` senza `--vai`
  ha stampato il cancello con UPS 7,90 EUR senza scrivere. Scoperta: `listTransportationOptions`
  pagina a 10 (max 20) e ne aveva 274 → l'UPS era invisibile; corretto in `dff35cf`. Le quote
  UPS non hanno `expiration`; nessuna opzione (nemmeno propria) chiede `CONFIRMED_DELIVERY_WINDOW`
  su questo account. Resta lo Step 4 col "vai" di Mariano.

**Non eseguire questo task finché Mariano non conferma che le due precondizioni sono
soddisfatte.**
