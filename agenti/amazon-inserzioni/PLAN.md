# Agente Amazon New Era: creare e aggiornare inserzioni — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un agente Amazon Seller Central che crea inserzioni nuove e aggiorna quelle
già online (prezzo, quantità, contenuto), con anteprima e permesso esplicito prima di
ogni scrittura — l'equivalente Amazon degli Stadi 5/2 eBay, in un solo progetto.

**Architecture:** File piatti in `scripts/` (niente sottocartelle: stessa convenzione
delle skill eBay), un client SP-API condiviso, un modello prodotto canonico, tre
estrattori per popolarlo, una validazione contro lo schema categoria di Amazon, due
flussi (crea/aggiorna) che condividono lo stesso registro JSON e lo stesso cancello di
conferma.

**Tech Stack:** Python 3.9 di sistema (niente venv dedicata), `requests`, `PyYAML`,
`openpyxl`, `pytest` — tutti già installati sulla macchina. Nessuno SDK Amazon di terze
parti: client SP-API scritto a mano, adattato da quello già collaudato in
`marketplace_health.spapi.SpApiClient` (`~/scripts/marketplace-health-monitor/`).

**Spec:** `~/.claude/skills/amazon-inserzioni/DESIGN.md`

## Global Constraints

- 🟡 autonomia: nessuna scrittura su Amazon senza un "vai" esplicito (creazione) o un
  permesso esplicito per singola inserzione (aggiornamento) — DESIGN §3
- Un cliente, un marketplace, alla volta — DESIGN §3
- Mai inventare un attributo mancante: campo non trovato = campo assente, mai un valore
  indovinato — DESIGN §3.1, §9
- **Mai includere in un pacchetto di modifiche un valore "dopo" vuoto/None.** Un campo
  senza contenuto nuovo non si tocca, non si svuota — lezione da `ebay-aggiorna-inserzione`
  (Critical: descrizione vuota che ha cancellato il pubblicato)
- **Un'operazione di patch su un attributo multi-valore porta sempre l'intero array**,
  mai un elemento isolato — stessa lezione, altra causa: la Listings Items API sostituisce
  l'attributo per intero (Critical: specifiche multivalore appiattite)
- **`annulla` ripristina sempre e solo dall'ultima riga di registro per quello SKU**, mai
  ricomponendo da più righe storiche — stessa lezione (Critical: annulla ricostruiva male)
- Scrittura un'inserzione/un aggiornamento alla volta, mai in parallelo; ogni tentativo
  entra nel registro **prima** della chiamata, l'esito **dopo**
- Nessuna chiamata di rete nei test: tutto mockato (`unittest.mock`) o su fixture salvate
  su disco
- Credenziali SP-API lette solo da variabili d'ambiente
  (`SPAPI_LWA_CLIENT_ID`/`SPAPI_LWA_CLIENT_SECRET`) + refresh token per account da
  config locale — mai in chat, mai hardcoded, mai in un file committato
- Import piatti con `sys.path.insert` (vedi `tests/conftest.py`, Task 1) — non un package
  Python con `__init__.py`, per restare sulla stessa convenzione delle skill eBay

---

## File Structure

```
~/.claude/skills/amazon-inserzioni/
  SKILL.md
  scripts/
    spapi_client.py
    catalogo.py
    modello.py
    registro.py
    estrattore_manuale.py
    estrattore_generico.py
    estrattore_fornitore.py
    validazione.py
    crea.py
    letto.py
    modifiche.py
    diff.py
    applica.py
    annulla.py
  tests/
    conftest.py
    test_spapi_client.py
    test_catalogo.py
    test_modello.py
    test_registro.py
    test_estrattore_manuale.py
    test_estrattore_generico.py
    test_estrattore_fornitore.py
    test_validazione.py
    test_crea.py
    test_letto.py
    test_modifiche.py
    test_diff.py
    test_applica.py
    test_annulla.py
    test_prova_a_vuoto.py
    fixtures/
      ldjson_prodotto.html
      opengraph_prodotto.html
      pagina_senza_dati.html
      fornitore_esempio.csv
      mappatura_esempio.yaml
      schema_categoria_test.json
```

Tutti i comandi dei task si eseguono da `~/.claude/skills/amazon-inserzioni/`.

---

### Task 1: `spapi_client.py` — sessione SP-API (auth LWA + retry + get/put/patch)

**Files:**
- Create: `scripts/spapi_client.py`
- Create: `tests/conftest.py`
- Test: `tests/test_spapi_client.py`

**Interfaces:**
- Produces: `SpApiClient(refresh_token: str, region: str = "eu")`; proprietà
  `.access_token -> str`; metodi `.get(path, params=None) -> dict`,
  `.put(path, json_body, params=None) -> dict`, `.patch(path, json_body, params=None) -> dict`;
  eccezione `SpApiError`

Adattato da `marketplace_health.spapi.SpApiClient` (già in produzione su GET/report in
`~/scripts/marketplace-health-monitor/`, verificato con 13 test verdi): stesso auth LWA,
stesso backoff sui 429, stessa lettura degli errori con corpo non-JSON. Aggiunge `PUT` e
`PATCH`, che quel progetto non usava. **Nota per l'implementazione**: `put`/`patch`
tornano il corpo della risposta **anche quando `status_code` è 200 e il campo `status`
nel corpo è `INVALID`** — non è un errore di trasporto, è la Listings Items API che
dichiara un problema di validazione: sta al chiamante (Task 9, 12) leggere `issues`.
Solo `status_code >= 400` alza `SpApiError`.

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
    assert chiamata.kwargs["headers"] == {"x-amz-access-token": "Atza|tok"}


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

log = logging.getLogger("amazon_inserzioni.spapi")

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

    Dal 2 ottobre 2023 SP-API non richiede piu' firma AWS SigV4: basta il
    token LWA nell'header x-amz-access-token. Le scritture (put/patch)
    tornano il corpo della risposta anche a status applicativo INVALID:
    solo status_code >= 400 e' un errore di trasporto.
    """

    _MAX_TENTATIVI = 5

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
        # 60s di margine per non usare un token che scade a meta' giro
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
            elif metodo == "PUT":
                r = requests.put(url, headers=headers, params=params or {}, json=json_body, timeout=60)
            elif metodo == "PATCH":
                r = requests.patch(url, headers=headers, params=params or {}, json=json_body, timeout=60)
            else:
                raise SpApiError(f"metodo non supportato: {metodo}")
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

    def put(self, path: str, json_body: dict, params: dict | None = None) -> dict:
        return self._request("PUT", path, params=params, json_body=json_body)

    def patch(self, path: str, json_body: dict, params: dict | None = None) -> dict:
        return self._request("PATCH", path, params=params, json_body=json_body)
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_spapi_client.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_spapi_client.py scripts/spapi_client.py
git commit -m "feat: client SP-API con auth LWA, retry e get/put/patch"
```

---

### Task 2: `catalogo.py` — schema attributi per categoria

**Files:**
- Create: `scripts/catalogo.py`
- Test: `tests/test_catalogo.py`
- Test fixture: `tests/fixtures/schema_categoria_test.json`

**Interfaces:**
- Consumes: `SpApiClient` (Task 1) — `.get(path, params) -> dict`
- Produces: `CatalogoError`; `cerca_product_type(client, marketplace_id, parola_chiave) -> list[str]`;
  `schema_categoria(client, marketplace_id, seller_id, product_type, cache_dir=None) -> dict`
  (documento JSON Schema con almeno la chiave `"required": list[str]`)

La Product Type Definitions API di Amazon funziona in due passi: prima
`GET /definitions/2020-09-01/productTypes/{productType}` restituisce metadati con un
link **pre-firmato** a un documento JSON Schema separato (`schema.link.resource`), poi
quel link va scaricato con una `GET` semplice (non autenticata con SP-API, è un URL
firmato a parte). Lo schema si mette in cache su disco: cambia raramente e ogni
richiesta in più costa un giro SP-API.

- [ ] **Step 1: Scrivi la fixture dello schema**

```json
// tests/fixtures/schema_categoria_test.json
{
  "$schema": "http://json-schema.org/draft-04/schema#",
  "required": ["item_name", "brand", "colore"],
  "properties": {
    "item_name": {"type": "array"},
    "brand": {"type": "array"},
    "colore": {"type": "array"}
  }
}
```

- [ ] **Step 2: Scrivi i test che falliscono**

```python
# tests/test_catalogo.py
import json
import os
from unittest.mock import Mock, patch
import pytest
from catalogo import cerca_product_type, schema_categoria, CatalogoError


def test_cerca_product_type_estrae_i_nomi():
    client = Mock()
    client.get.return_value = {"productTypes": [{"name": "EXERCISE_MAT"}, {"name": "LUGGAGE"}]}
    risultato = cerca_product_type(client, "APJ6JRA9NG5V4", "tappeto")
    assert risultato == ["EXERCISE_MAT", "LUGGAGE"]
    client.get.assert_called_once_with(
        "/definitions/2020-09-01/productTypes",
        params={"marketplaceIds": "APJ6JRA9NG5V4", "keywords": "tappeto"},
    )


def test_schema_categoria_scarica_definizione_e_schema(tmp_path):
    client = Mock()
    client.get.return_value = {
        "schema": {"link": {"resource": "https://cache.amazon.it/schema/exercise_mat.json", "verb": "GET"}}
    }
    with open(os.path.join(os.path.dirname(__file__), "fixtures", "schema_categoria_test.json")) as f:
        schema_atteso = json.load(f)

    risposta_download = Mock()
    risposta_download.status_code = 200
    risposta_download.json.return_value = schema_atteso

    with patch("catalogo.requests.get", return_value=risposta_download) as mock_get:
        schema = schema_categoria(client, "APJ6JRA9NG5V4", "SELLER1", "EXERCISE_MAT",
                                   cache_dir=str(tmp_path))
    assert schema == schema_atteso
    mock_get.assert_called_once_with("https://cache.amazon.it/schema/exercise_mat.json", timeout=60)
    client.get.assert_called_once_with(
        "/definitions/2020-09-01/productTypes/EXERCISE_MAT",
        params={"marketplaceIds": "APJ6JRA9NG5V4", "sellerId": "SELLER1", "requirements": "LISTING"},
    )


def test_schema_categoria_usa_la_cache_al_secondo_giro(tmp_path):
    client = Mock()
    client.get.return_value = {"schema": {"link": {"resource": "https://x/schema.json"}}}
    risposta_download = Mock()
    risposta_download.status_code = 200
    risposta_download.json.return_value = {"required": ["item_name"]}

    with patch("catalogo.requests.get", return_value=risposta_download):
        schema_categoria(client, "APJ6JRA9NG5V4", "SELLER1", "EXERCISE_MAT", cache_dir=str(tmp_path))
        schema_categoria(client, "APJ6JRA9NG5V4", "SELLER1", "EXERCISE_MAT", cache_dir=str(tmp_path))
    assert client.get.call_count == 1


def test_schema_categoria_senza_link_alza_catalogoerror(tmp_path):
    client = Mock()
    client.get.return_value = {"schema": {}}
    with pytest.raises(CatalogoError, match="nessuno schema disponibile"):
        schema_categoria(client, "APJ6JRA9NG5V4", "SELLER1", "SCONOSCIUTO", cache_dir=str(tmp_path))


def test_schema_categoria_download_fallito_alza_catalogoerror(tmp_path):
    client = Mock()
    client.get.return_value = {"schema": {"link": {"resource": "https://x/schema.json"}}}
    risposta_fallita = Mock()
    risposta_fallita.status_code = 404
    with patch("catalogo.requests.get", return_value=risposta_fallita):
        with pytest.raises(CatalogoError, match="404"):
            schema_categoria(client, "APJ6JRA9NG5V4", "SELLER1", "EXERCISE_MAT", cache_dir=str(tmp_path))
```

- [ ] **Step 2b: Verifica che falliscano**

Run: `python3 -m pytest tests/test_catalogo.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'catalogo'`

- [ ] **Step 3: Scrivi `scripts/catalogo.py`**

```python
from __future__ import annotations
import json
import os
import requests


class CatalogoError(Exception):
    """Errore nel recuperare o leggere lo schema di una categoria Amazon."""


def cerca_product_type(client, marketplace_id: str, parola_chiave: str) -> list[str]:
    body = client.get("/definitions/2020-09-01/productTypes", params={
        "marketplaceIds": marketplace_id, "keywords": parola_chiave,
    })
    return [pt["name"] for pt in body.get("productTypes", [])]


def schema_categoria(client, marketplace_id: str, seller_id: str, product_type: str,
                      cache_dir: str | None = None) -> dict:
    cache_dir = cache_dir or os.path.expanduser("~/.config/newera-amazon/schema")
    cache_path = os.path.join(cache_dir, f"{product_type}-{marketplace_id}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    definizione = client.get(f"/definitions/2020-09-01/productTypes/{product_type}", params={
        "marketplaceIds": marketplace_id, "sellerId": seller_id, "requirements": "LISTING",
    })
    link = definizione.get("schema", {}).get("link", {}).get("resource")
    if not link:
        raise CatalogoError(f"nessuno schema disponibile per {product_type} su {marketplace_id}")

    r = requests.get(link, timeout=60)
    if r.status_code >= 400:
        raise CatalogoError(f"download schema {product_type} -> {r.status_code}")
    schema = r.json()

    os.makedirs(cache_dir, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(schema, f)
    return schema
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_catalogo.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_catalogo.py tests/fixtures/schema_categoria_test.json scripts/catalogo.py
git commit -m "feat: lettura schema attributi per categoria, con cache su disco"
```

---

### Task 3: `modello.py` — Prodotto canonico

**Files:**
- Create: `scripts/modello.py`
- Test: `tests/test_modello.py`

**Interfaces:**
- Produces: `@dataclass Prodotto` con campi `sku, title, price, quantity, product_type`
  (obbligatori) e `ean, brand, description, category, images, condition, bullet_points,
  attributes, weight, dimensions, mpn` (opzionali, stesso contratto di
  `multi-marketplace-uploader/core/model.py` più `product_type`)

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_modello.py
from modello import Prodotto


def test_prodotto_minimo():
    p = Prodotto(sku="ABC-1", title="Tappeto yoga", price=29.9, quantity=10,
                 product_type="EXERCISE_MAT")
    assert p.ean == ""
    assert p.images == []
    assert p.bullet_points == []
    assert p.attributes == {}
    assert p.condition == "New"
    assert p.weight is None


def test_prodotto_completo():
    p = Prodotto(
        sku="ABC-1", title="Tappeto yoga", price=29.9, quantity=10,
        product_type="EXERCISE_MAT", ean="8001234567890", brand="New Era Test",
        description="Tappeto in TPE antiscivolo", category="Sport",
        images=["https://x/1.jpg", "https://x/2.jpg"], condition="New",
        bullet_points=["Antiscivolo", "Ecologico"],
        attributes={"colore": [{"value": "Blu", "marketplace_id": "APJ6JRA9NG5V4"}]},
        weight=1.2, dimensions={"lunghezza": 183, "larghezza": 61}, mpn="TYM-001",
    )
    assert p.brand == "New Era Test"
    assert len(p.images) == 2
    assert p.attributes["colore"][0]["value"] == "Blu"


def test_due_prodotti_con_liste_di_default_non_condividono_stato():
    a = Prodotto(sku="A", title="A", price=1, quantity=1, product_type="X")
    b = Prodotto(sku="B", title="B", price=1, quantity=1, product_type="X")
    a.images.append("https://x/1.jpg")
    assert b.images == []
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_modello.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Scrivi `scripts/modello.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Prodotto:
    sku: str
    title: str
    price: float
    quantity: int
    product_type: str
    ean: str = ""
    brand: str = ""
    description: str = ""
    category: str = ""
    images: list[str] = field(default_factory=list)
    condition: str = "New"
    bullet_points: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    weight: float | None = None
    dimensions: dict | None = None
    mpn: str = ""
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_modello.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_modello.py scripts/modello.py
git commit -m "feat: modello prodotto canonico Prodotto"
```

---

### Task 4: `registro.py` — log JSON di tentativi/esiti

**Files:**
- Create: `scripts/registro.py`
- Test: `tests/test_registro.py`

**Interfaces:**
- Produces: `registra_tentativo_creazione(cliente, sku) -> int`,
  `registra_esito_creazione(cliente, indice, esito, issues) -> None`,
  `registra_tentativo_aggiornamento(cliente, sku, product_type, campi: dict[str, dict]) -> int`,
  `registra_esito_aggiornamento(cliente, indice, esito, issues) -> None`,
  `ultimo_lotto(cliente, cartella: str) -> list[dict]`

`campi` in `registra_tentativo_aggiornamento` è `{nome_campo: {"prima": ..., "dopo": ...}}`
— **solo** i campi toccati in quella scrittura. È l'unica fonte da cui `annulla.py`
(Task 13) ricostruirà il ripristino: niente altrove nel codice deve aggregare più righe
per decidere un "prima".

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_registro.py
import os
import registro


def test_creazione_nessun_file_prima_del_primo_tentativo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert registro.ultimo_lotto("Bedshire", "pubblicazioni") == []


def test_creazione_tentativo_poi_esito(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    indice = registro.registra_tentativo_creazione("Bedshire", "SKU-1")
    assert registro.ultimo_lotto("Bedshire", "pubblicazioni")[indice]["esito"] == "in corso"
    registro.registra_esito_creazione("Bedshire", indice, "ACCEPTED", [])
    riga = registro.ultimo_lotto("Bedshire", "pubblicazioni")[indice]
    assert riga["esito"] == "ACCEPTED"
    assert riga["sku"] == "SKU-1"


def test_due_tentativi_di_creazione_non_si_sovrascrivono(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_creazione("Bedshire", "SKU-1")
    i2 = registro.registra_tentativo_creazione("Bedshire", "SKU-2")
    assert i1 != i2
    registro.registra_esito_creazione("Bedshire", i1, "ACCEPTED", [])
    registro.registra_esito_creazione("Bedshire", i2, "INVALID", [{"message": "manca il colore"}])
    righe = registro.ultimo_lotto("Bedshire", "pubblicazioni")
    assert righe[i1]["esito"] == "ACCEPTED"
    assert righe[i2]["esito"] == "INVALID"


def test_aggiornamento_tentativo_poi_esito(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    campi = {"list_price": {"prima": 29.9, "dopo": 24.9}}
    indice = registro.registra_tentativo_aggiornamento("Bedshire", "SKU-1", "EXERCISE_MAT", campi)
    riga = registro.ultimo_lotto("Bedshire", "aggiornamenti")[indice]
    assert riga["campi"] == campi
    assert riga["product_type"] == "EXERCISE_MAT"
    assert riga["esito"] == "in corso"
    registro.registra_esito_aggiornamento("Bedshire", indice, "ACCEPTED", [])
    assert registro.ultimo_lotto("Bedshire", "aggiornamenti")[indice]["esito"] == "ACCEPTED"


def test_clienti_diversi_hanno_file_diversi(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    registro.registra_tentativo_creazione("Bedshire", "SKU-1")
    assert registro.ultimo_lotto("Tecno Cold", "pubblicazioni") == []
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_registro.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Scrivi `scripts/registro.py`**

```python
from __future__ import annotations
import json
import os
from datetime import datetime, timezone


def _percorso(cliente: str, cartella: str) -> str:
    base = os.path.expanduser(f"~/.config/newera-amazon/{cartella}")
    os.makedirs(base, exist_ok=True)
    data = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(base, f"{cliente}-{data}.json")


def _carica(percorso: str) -> list[dict]:
    if not os.path.exists(percorso):
        return []
    with open(percorso) as f:
        return json.load(f)


def _salva(percorso: str, righe: list[dict]) -> None:
    with open(percorso, "w") as f:
        json.dump(righe, f, indent=2, ensure_ascii=False)


def registra_tentativo_creazione(cliente: str, sku: str) -> int:
    percorso = _percorso(cliente, "pubblicazioni")
    righe = _carica(percorso)
    righe.append({
        "sku": sku, "ora": datetime.now(timezone.utc).isoformat(),
        "esito": "in corso", "issues": [],
    })
    _salva(percorso, righe)
    return len(righe) - 1


def registra_esito_creazione(cliente: str, indice: int, esito: str, issues: list[dict]) -> None:
    percorso = _percorso(cliente, "pubblicazioni")
    righe = _carica(percorso)
    righe[indice]["esito"] = esito
    righe[indice]["issues"] = issues
    _salva(percorso, righe)


def registra_tentativo_aggiornamento(cliente: str, sku: str, product_type: str,
                                      campi: dict[str, dict]) -> int:
    percorso = _percorso(cliente, "aggiornamenti")
    righe = _carica(percorso)
    righe.append({
        "sku": sku, "product_type": product_type,
        "ora": datetime.now(timezone.utc).isoformat(),
        "campi": campi, "esito": "in corso", "issues": [],
    })
    _salva(percorso, righe)
    return len(righe) - 1


def registra_esito_aggiornamento(cliente: str, indice: int, esito: str, issues: list[dict]) -> None:
    percorso = _percorso(cliente, "aggiornamenti")
    righe = _carica(percorso)
    righe[indice]["esito"] = esito
    righe[indice]["issues"] = issues
    _salva(percorso, righe)


def ultimo_lotto(cliente: str, cartella: str) -> list[dict]:
    return _carica(_percorso(cliente, cartella))
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_registro.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_registro.py scripts/registro.py
git commit -m "feat: registro JSON tentativo/esito per creazione e aggiornamento"
```

---

### Task 5: `estrattore_manuale.py` — dati dettati da Mariano

**Files:**
- Create: `scripts/estrattore_manuale.py`
- Test: `tests/test_estrattore_manuale.py`

**Interfaces:**
- Consumes: `Prodotto` (Task 3)
- Produces: `da_dati_manuali(dati: dict) -> Prodotto`

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_estrattore_manuale.py
import pytest
from estrattore_manuale import da_dati_manuali


def test_costruisce_prodotto_dai_dati_minimi():
    p = da_dati_manuali({
        "sku": "ABC-1", "title": "Tappeto yoga", "price": 29.9,
        "quantity": 10, "product_type": "EXERCISE_MAT",
    })
    assert p.sku == "ABC-1"
    assert p.price == 29.9


def test_campo_obbligatorio_mancante_alza_valueerror():
    with pytest.raises(ValueError, match="quantity"):
        da_dati_manuali({"sku": "ABC-1", "title": "Tappeto yoga", "price": 29.9,
                          "product_type": "EXERCISE_MAT"})


def test_campo_sconosciuto_alza_valueerror():
    with pytest.raises(ValueError, match="colour"):
        da_dati_manuali({"sku": "ABC-1", "title": "Tappeto yoga", "price": 29.9,
                          "quantity": 10, "product_type": "EXERCISE_MAT", "colour": "Blu"})
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_estrattore_manuale.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Scrivi `scripts/estrattore_manuale.py`**

```python
from __future__ import annotations
import dataclasses
from modello import Prodotto

_OBBLIGATORI = {"sku", "title", "price", "quantity", "product_type"}


def da_dati_manuali(dati: dict) -> Prodotto:
    """Costruisce un Prodotto da ciò che Mariano ha dettato. Non valida
    contro lo schema categoria (è compito di validazione.py): qui si limita
    a mappare, senza inventare nulla di assente."""
    campi_noti = {f.name for f in dataclasses.fields(Prodotto)}
    sconosciuti = set(dati) - campi_noti
    if sconosciuti:
        raise ValueError(f"campi non riconosciuti: {sorted(sconosciuti)}")
    mancanti = _OBBLIGATORI - set(dati)
    if mancanti:
        raise ValueError(f"campi obbligatori mancanti: {sorted(mancanti)}")
    return Prodotto(**dati)
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_estrattore_manuale.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_estrattore_manuale.py scripts/estrattore_manuale.py
git commit -m "feat: estrattore da dati dettati a mano"
```

---

### Task 6: `estrattore_generico.py` — pagina prodotto → dati strutturati

**Files:**
- Create: `scripts/estrattore_generico.py`
- Test: `tests/test_estrattore_generico.py`
- Fixtures: `tests/fixtures/ldjson_prodotto.html`, `tests/fixtures/opengraph_prodotto.html`,
  `tests/fixtures/pagina_senza_dati.html`

**Interfaces:**
- Consumes: `Prodotto` (Task 3)
- Produces: `EstrazioneFallita`; `estrai_da_link(url: str, *, sessione=None) -> Prodotto`

Legge JSON-LD (`<script type="application/ld+json">` con `@type: Product`) e, in
mancanza, i meta tag Open Graph. `sku`, `quantity` e `product_type` restano vuoti — nessuna
pagina esterna li dichiara in modo affidabile: li completa Mariano o la validazione li
segnala mancanti (DESIGN §3.1 "mai inventare").

- [ ] **Step 1: Scrivi le fixture HTML**

```html
<!-- tests/fixtures/ldjson_prodotto.html -->
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org/","@type":"Product","name":"Tappeto yoga antiscivolo",
 "brand":{"@type":"Brand","name":"New Era Test"},
 "description":"Tappeto in TPE, 183x61cm",
 "image":["https://esempio.it/1.jpg","https://esempio.it/2.jpg"],
 "gtin13":"8001234567890",
 "offers":{"@type":"Offer","price":"29.90","priceCurrency":"EUR"}}
</script>
</head><body></body></html>
```

```html
<!-- tests/fixtures/opengraph_prodotto.html -->
<html><head>
<meta property="og:title" content="Tappeto yoga antiscivolo">
<meta property="og:description" content="Tappeto in TPE, 183x61cm">
<meta property="og:image" content="https://esempio.it/1.jpg">
<meta property="product:price:amount" content="29.90">
</head><body></body></html>
```

```html
<!-- tests/fixtures/pagina_senza_dati.html -->
<html><head><title>Pagina qualsiasi</title></head><body>Niente di strutturato.</body></html>
```

- [ ] **Step 2: Scrivi i test che falliscono**

```python
# tests/test_estrattore_generico.py
import os
from unittest.mock import Mock
import pytest
from estrattore_generico import estrai_da_link, EstrazioneFallita

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _sessione_con(nome_file, status=200):
    with open(os.path.join(_FIXTURES, nome_file), encoding="utf-8") as f:
        html = f.read()
    sess = Mock()
    risposta = Mock()
    risposta.status_code = status
    risposta.text = html
    sess.get.return_value = risposta
    return sess


def test_estrae_da_ldjson():
    p = estrai_da_link("https://esempio.it/prodotto", sessione=_sessione_con("ldjson_prodotto.html"))
    assert p.title == "Tappeto yoga antiscivolo"
    assert p.brand == "New Era Test"
    assert p.price == 29.90
    assert p.ean == "8001234567890"
    assert p.images == ["https://esempio.it/1.jpg", "https://esempio.it/2.jpg"]
    assert p.sku == ""
    assert p.quantity == 0


def test_estrae_da_opengraph_quando_manca_ldjson():
    p = estrai_da_link("https://esempio.it/prodotto", sessione=_sessione_con("opengraph_prodotto.html"))
    assert p.title == "Tappeto yoga antiscivolo"
    assert p.price == 29.90
    assert p.images == ["https://esempio.it/1.jpg"]


def test_pagina_senza_dati_alza_estrazionefallita():
    with pytest.raises(EstrazioneFallita, match="nessun dato strutturato"):
        estrai_da_link("https://esempio.it/prodotto", sessione=_sessione_con("pagina_senza_dati.html"))


def test_pagina_non_raggiungibile_alza_estrazionefallita():
    with pytest.raises(EstrazioneFallita, match="404"):
        estrai_da_link("https://esempio.it/prodotto", sessione=_sessione_con("pagina_senza_dati.html", status=404))
```

- [ ] **Step 2b: Verifica che falliscano**

Run: `python3 -m pytest tests/test_estrattore_generico.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Scrivi `scripts/estrattore_generico.py`**

```python
from __future__ import annotations
import json
import re
import requests
from modello import Prodotto


class EstrazioneFallita(Exception):
    """La pagina non contiene dati strutturati Product utilizzabili."""


_LD_JSON = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_OG_META = re.compile(
    r'<meta[^>]+property=["\']([a-zA-Z:]+)["\'][^>]+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)


def _trova_product_ldjson(html: str) -> dict | None:
    for blocco in _LD_JSON.findall(html):
        try:
            dato = json.loads(blocco.strip())
        except json.JSONDecodeError:
            continue
        candidati = dato if isinstance(dato, list) else [dato]
        for c in candidati:
            if isinstance(c, dict) and c.get("@type") == "Product":
                return c
    return None


def _prezzo(valore) -> float:
    try:
        return float(valore) if valore is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _da_ldjson(dato: dict) -> Prodotto:
    offerte = dato.get("offers") or {}
    if isinstance(offerte, list):
        offerte = offerte[0] if offerte else {}
    immagini = dato.get("image") or []
    if isinstance(immagini, str):
        immagini = [immagini]
    marca = dato.get("brand")
    if isinstance(marca, dict):
        marca = marca.get("name", "")
    return Prodotto(
        sku="", title=dato.get("name", ""), price=_prezzo(offerte.get("price")),
        quantity=0, product_type="",
        ean=dato.get("gtin13") or dato.get("gtin") or "",
        brand=marca or "", description=dato.get("description", ""), images=list(immagini),
    )


def _da_opengraph(html: str) -> Prodotto | None:
    coppie = {chiave: valore for chiave, valore in _OG_META.findall(html)}
    titolo = coppie.get("og:title")
    if not titolo:
        return None
    immagine = coppie.get("og:image")
    return Prodotto(
        sku="", title=titolo, price=_prezzo(coppie.get("product:price:amount")),
        quantity=0, product_type="",
        description=coppie.get("og:description", ""),
        images=[immagine] if immagine else [],
    )


def estrai_da_link(url: str, *, sessione=None) -> Prodotto:
    sess = sessione or requests
    r = sess.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (compatible; NewEraBot/1.0)"})
    if r.status_code >= 400:
        raise EstrazioneFallita(f"{url} -> HTTP {r.status_code}")
    dato = _trova_product_ldjson(r.text)
    if dato:
        return _da_ldjson(dato)
    prodotto = _da_opengraph(r.text)
    if prodotto:
        return prodotto
    raise EstrazioneFallita(f"{url}: nessun dato strutturato Product trovato")
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_estrattore_generico.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_estrattore_generico.py tests/fixtures/ldjson_prodotto.html \
        tests/fixtures/opengraph_prodotto.html tests/fixtures/pagina_senza_dati.html \
        scripts/estrattore_generico.py
git commit -m "feat: estrattore generico da schema.org/Open Graph"
```

---

### Task 7: `estrattore_fornitore.py` — file + mappatura YAML

**Files:**
- Create: `scripts/estrattore_fornitore.py`
- Test: `tests/test_estrattore_fornitore.py`
- Fixtures: `tests/fixtures/fornitore_esempio.csv`, `tests/fixtures/mappatura_esempio.yaml`

**Interfaces:**
- Consumes: `Prodotto` (Task 3)
- Produces: `carica_mappatura(percorso_yaml: str) -> dict[str, str]`;
  `importa(percorso_file: str, mappatura: dict) -> tuple[list[Prodotto], list[tuple[int, str]]]`

Stesso principio di `multi-marketplace-uploader/core/importer.py`: un prodotto incompleto
non blocca gli altri, va negli scarti con motivo e numero di riga.

- [ ] **Step 1: Scrivi le fixture**

```yaml
# tests/fixtures/mappatura_esempio.yaml
sku: "Codice"
title: "Nome Articolo"
brand: "Marca"
price: "Prezzo"
quantity: "Qta"
product_type: "TipoProdotto"
images: "Foto"
ean: "EAN"
```

```csv
# tests/fixtures/fornitore_esempio.csv
Codice,Nome Articolo,Marca,Prezzo,Qta,TipoProdotto,Foto,EAN
SKU-1,Tappeto yoga antiscivolo,New Era Test,29.90,50,EXERCISE_MAT,"https://x/1.jpg,https://x/2.jpg",8001234567890
SKU-2,Borraccia termica,New Era Test,,20,LUGGAGE,https://x/3.jpg,8001234567891
SKU-3,Zaino sportivo,New Era Test,39.90,abc,LUGGAGE,https://x/4.jpg,8001234567892
```

(riga SKU-2: prezzo mancante → scarto; riga SKU-3: quantità non numerica → scarto)

- [ ] **Step 2: Scrivi i test che falliscono**

```python
# tests/test_estrattore_fornitore.py
import os
from estrattore_fornitore import carica_mappatura, importa

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_carica_mappatura():
    m = carica_mappatura(os.path.join(_FIXTURES, "mappatura_esempio.yaml"))
    assert m["sku"] == "Codice"
    assert m["images"] == "Foto"


def test_importa_csv_scarta_le_righe_incomplete():
    mappatura = carica_mappatura(os.path.join(_FIXTURES, "mappatura_esempio.yaml"))
    prodotti, scarti = importa(os.path.join(_FIXTURES, "fornitore_esempio.csv"), mappatura)
    assert len(prodotti) == 1
    assert prodotti[0].sku == "SKU-1"
    assert prodotti[0].price == 29.90
    assert prodotti[0].quantity == 50
    assert prodotti[0].images == ["https://x/1.jpg", "https://x/2.jpg"]
    assert len(scarti) == 2
    numeri_riga = [s[0] for s in scarti]
    assert numeri_riga == [3, 4]
    assert "prezzo" in scarti[0][1] or "obbligatori" in scarti[0][1]


def test_importa_xlsx(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Codice", "Nome Articolo", "Marca", "Prezzo", "Qta", "TipoProdotto", "Foto", "EAN"])
    ws.append(["SKU-9", "Prodotto test", "New Era Test", 19.90, 5, "LUGGAGE", "https://x/9.jpg", "800111"])
    percorso = tmp_path / "fornitore.xlsx"
    wb.save(percorso)

    mappatura = carica_mappatura(os.path.join(_FIXTURES, "mappatura_esempio.yaml"))
    prodotti, scarti = importa(str(percorso), mappatura)
    assert len(prodotti) == 1
    assert prodotti[0].sku == "SKU-9"
    assert scarti == []
```

- [ ] **Step 2b: Verifica che falliscano**

Run: `python3 -m pytest tests/test_estrattore_fornitore.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Scrivi `scripts/estrattore_fornitore.py`**

```python
from __future__ import annotations
import csv
from pathlib import Path
import yaml
from openpyxl import load_workbook
from modello import Prodotto

_OBBLIGATORI = ("sku", "title", "price", "quantity", "product_type")
_CAMPI_TESTO = ("sku", "title", "brand", "description", "category", "condition", "mpn", "product_type")


def carica_mappatura(percorso_yaml: str) -> dict[str, str]:
    with open(percorso_yaml) as f:
        return yaml.safe_load(f) or {}


def _righe_da_csv(percorso: str):
    with open(percorso, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def _righe_da_xlsx(percorso: str):
    wb = load_workbook(percorso, read_only=True, data_only=True)
    ws = wb.active
    righe = ws.iter_rows(values_only=True)
    intestazioni = next(righe)
    for riga in righe:
        yield dict(zip(intestazioni, riga))


def _valore(riga: dict, mappatura: dict, campo: str):
    colonna = mappatura.get(campo)
    if not colonna:
        return None
    return riga.get(colonna)


def importa(percorso_file: str, mappatura: dict) -> tuple[list[Prodotto], list[tuple[int, str]]]:
    percorso = Path(percorso_file)
    righe = _righe_da_xlsx(percorso_file) if percorso.suffix.lower() == ".xlsx" else _righe_da_csv(percorso_file)

    prodotti: list[Prodotto] = []
    scarti: list[tuple[int, str]] = []
    for indice, riga in enumerate(righe, start=2):  # riga 1 = intestazione
        dati: dict = {}
        for campo in _CAMPI_TESTO:
            v = _valore(riga, mappatura, campo)
            if v is not None and str(v).strip():
                dati[campo] = str(v)

        immagini_raw = _valore(riga, mappatura, "images")
        dati["images"] = [u.strip() for u in str(immagini_raw).split(",") if u.strip()] if immagini_raw else []
        ean_raw = _valore(riga, mappatura, "ean")
        dati["ean"] = str(ean_raw) if ean_raw not in (None, "") else ""

        prezzo_raw = _valore(riga, mappatura, "price")
        quantita_raw = _valore(riga, mappatura, "quantity")

        mancanti = [c for c in _OBBLIGATORI if c not in dati and c not in ("price", "quantity")]
        if prezzo_raw in (None, ""):
            mancanti.append("price")
        if quantita_raw in (None, ""):
            mancanti.append("quantity")
        if mancanti:
            scarti.append((indice, f"campi obbligatori mancanti: {', '.join(mancanti)}"))
            continue

        try:
            dati["price"] = float(prezzo_raw)
            dati["quantity"] = int(quantita_raw)
        except (TypeError, ValueError):
            scarti.append((indice, "prezzo o quantità non numerici"))
            continue

        prodotti.append(Prodotto(**dati))
    return prodotti, scarti
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_estrattore_fornitore.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_estrattore_fornitore.py tests/fixtures/fornitore_esempio.csv \
        tests/fixtures/mappatura_esempio.yaml scripts/estrattore_fornitore.py
git commit -m "feat: estrattore da file fornitore con mappatura YAML"
```

---

### Task 8: `validazione.py` — semaforo contro lo schema categoria

**Files:**
- Create: `scripts/validazione.py`
- Test: `tests/test_validazione.py`

**Interfaces:**
- Consumes: `Prodotto` (Task 3), schema da `catalogo.schema_categoria` (Task 2, forma
  `{"required": list[str], ...}`)
- Produces: `@dataclass Rilievo(prodotto, semaforo: str, mancanti_obbligatori: list[str],
  mancanti_opzionali: list[str])`; `valuta(prodotto, schema) -> Rilievo`;
  `valuta_tutti(prodotti, schema) -> list[Rilievo]`

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_validazione.py
from modello import Prodotto
from validazione import valuta, valuta_tutti

_SCHEMA = {"required": ["item_name", "brand", "colore"]}


def _base(**override):
    dati = dict(sku="ABC-1", title="Tappeto yoga", price=29.9, quantity=10,
                product_type="EXERCISE_MAT", images=["https://x/1.jpg", "https://x/2.jpg"],
                bullet_points=["Antiscivolo"], description="Un tappeto",
                attributes={"colore": [{"value": "Blu"}]})
    dati.update(override)
    return Prodotto(**dati)


def test_prodotto_completo_e_verde():
    r = valuta(_base(), _SCHEMA)
    assert r.semaforo == "verde"
    assert r.mancanti_obbligatori == []
    assert r.mancanti_opzionali == []


def test_manca_un_campo_base_e_rosso():
    r = valuta(_base(price=0), _SCHEMA)
    assert r.semaforo == "rosso"
    assert "price" in r.mancanti_obbligatori


def test_manca_un_attributo_di_categoria_e_rosso():
    r = valuta(_base(attributes={}), _SCHEMA)
    assert r.semaforo == "rosso"
    assert "colore" in r.mancanti_obbligatori


def test_manca_solo_qualcosa_di_opzionale_e_giallo():
    r = valuta(_base(bullet_points=[]), _SCHEMA)
    assert r.semaforo == "giallo"
    assert "bullet_points" in r.mancanti_opzionali
    assert r.mancanti_obbligatori == []


def test_valuta_tutti_mappa_ogni_prodotto():
    rilievi = valuta_tutti([_base(), _base(sku="ABC-2", price=0)], _SCHEMA)
    assert [r.semaforo for r in rilievi] == ["verde", "rosso"]
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_validazione.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Scrivi `scripts/validazione.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from modello import Prodotto

_CAMPI_BASE_OBBLIGATORI = ("sku", "title", "price", "quantity", "product_type", "images")


@dataclass
class Rilievo:
    prodotto: Prodotto
    semaforo: str  # "verde" | "giallo" | "rosso"
    mancanti_obbligatori: list[str]
    mancanti_opzionali: list[str]


def valuta(prodotto: Prodotto, schema: dict) -> Rilievo:
    """Mai un valore inventato per un campo mancante: si segnala e basta
    (DESIGN §3.1, §9)."""
    mancanti_base = [c for c in _CAMPI_BASE_OBBLIGATORI if not getattr(prodotto, c)]
    attributi_richiesti = schema.get("required", [])
    mancanti_categoria = [a for a in attributi_richiesti if a not in prodotto.attributes]
    mancanti_obbligatori = mancanti_base + mancanti_categoria

    mancanti_opzionali = []
    if not prodotto.bullet_points:
        mancanti_opzionali.append("bullet_points")
    if not prodotto.description:
        mancanti_opzionali.append("description")
    if len(prodotto.images) < 2:
        mancanti_opzionali.append("almeno una seconda immagine")

    if mancanti_obbligatori:
        semaforo = "rosso"
    elif mancanti_opzionali:
        semaforo = "giallo"
    else:
        semaforo = "verde"
    return Rilievo(prodotto, semaforo, mancanti_obbligatori, mancanti_opzionali)


def valuta_tutti(prodotti: list[Prodotto], schema: dict) -> list[Rilievo]:
    return [valuta(p, schema) for p in prodotti]
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_validazione.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_validazione.py scripts/validazione.py
git commit -m "feat: validazione prodotto contro schema categoria, semaforo"
```

---

### Task 9: `crea.py` — payload e pubblicazione (creazione)

**Files:**
- Create: `scripts/crea.py`
- Test: `tests/test_crea.py`

**Interfaces:**
- Consumes: `Prodotto` (Task 3), `SpApiClient` (Task 1), `registro.*` (Task 4)
- Produces: `costruisci_payload(prodotto, marketplace_id) -> dict`;
  `@dataclass EsitoCreazione(sku, stato, issues)`;
  `pubblica_uno(client, seller_id, marketplace_id, prodotto) -> EsitoCreazione`;
  `pubblica_lotto(client, seller_id, marketplace_id, cliente, prodotti) -> list[EsitoCreazione]`

`pubblica_lotto` non si ferma su un fallimento: registra e continua (Global Constraints,
DESIGN §10).

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_crea.py
import os
from unittest.mock import Mock, patch
import pytest
from modello import Prodotto
from spapi_client import SpApiError
from crea import costruisci_payload, pubblica_uno, pubblica_lotto, EsitoCreazione


def _prodotto(**override):
    dati = dict(sku="ABC-1", title="Tappeto yoga", price=29.9, quantity=10,
                product_type="EXERCISE_MAT", brand="New Era Test",
                description="Un tappeto", bullet_points=["Antiscivolo", "Ecologico"],
                images=["https://x/1.jpg", "https://x/2.jpg"], ean="8001234567890",
                attributes={"colore": [{"value": "Blu", "marketplace_id": "APJ6JRA9NG5V4"}]})
    dati.update(override)
    return Prodotto(**dati)


def test_costruisci_payload_non_emette_campi_vuoti():
    payload = costruisci_payload(_prodotto(brand="", description=""), "APJ6JRA9NG5V4")
    assert "brand" not in payload["attributes"]
    assert "product_description" not in payload["attributes"]
    assert payload["productType"] == "EXERCISE_MAT"


def test_costruisci_payload_bullet_point_come_elenco_di_oggetti():
    payload = costruisci_payload(_prodotto(), "APJ6JRA9NG5V4")
    bp = payload["attributes"]["bullet_point"]
    assert bp == [
        {"value": "Antiscivolo", "marketplace_id": "APJ6JRA9NG5V4"},
        {"value": "Ecologico", "marketplace_id": "APJ6JRA9NG5V4"},
    ]


def test_costruisci_payload_include_gli_attributi_di_categoria():
    payload = costruisci_payload(_prodotto(), "APJ6JRA9NG5V4")
    assert payload["attributes"]["colore"][0]["value"] == "Blu"


def test_pubblica_uno_passa_lo_stato_e_gli_issues():
    client = Mock()
    client.put.return_value = {"sku": "ABC-1", "status": "ACCEPTED", "issues": [
        {"code": "W1", "message": "manca il bullet 5", "severity": "WARNING"}
    ]}
    esito = pubblica_uno(client, "SELLER1", "APJ6JRA9NG5V4", _prodotto())
    assert esito == EsitoCreazione("ABC-1", "ACCEPTED", [
        {"code": "W1", "message": "manca il bullet 5", "severity": "WARNING"}
    ])
    chiamata = client.put.call_args
    assert chiamata.args[0] == "/listings/2021-08-01/items/SELLER1/ABC-1"
    assert chiamata.kwargs["params"] == {"marketplaceIds": "APJ6JRA9NG5V4"}


def test_pubblica_lotto_non_si_ferma_su_un_fallimento(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.put.side_effect = [
        {"sku": "A", "status": "ACCEPTED", "issues": []},
        SpApiError("500 su .../B"),
        {"sku": "C", "status": "ACCEPTED", "issues": []},
    ]
    prodotti = [_prodotto(sku="A"), _prodotto(sku="B"), _prodotto(sku="C")]
    esiti = pubblica_lotto(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire", prodotti)
    assert len(esiti) == 3
    assert esiti[0].stato == "ACCEPTED"
    assert esiti[1].stato == "errore"
    assert esiti[2].stato == "ACCEPTED"
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_crea.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Scrivi `scripts/crea.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from modello import Prodotto
from spapi_client import SpApiError
import registro


def costruisci_payload(prodotto: Prodotto, marketplace_id: str) -> dict:
    """Ogni attributo Amazon è un elenco di oggetti valore, anche quando ce
    n'è uno solo. Non emette mai una chiave per un campo vuoto: farlo
    equivarrebbe a dichiarare un attributo vuoto invece di ometterlo."""
    attributi: dict = {}

    def _imposta(chiave, valore):
        if valore not in (None, "", [], {}):
            attributi[chiave] = valore

    _imposta("item_name", [{"value": prodotto.title, "marketplace_id": marketplace_id}])
    if prodotto.brand:
        _imposta("brand", [{"value": prodotto.brand, "marketplace_id": marketplace_id}])
    _imposta("list_price", [{"value": prodotto.price, "currency": "EUR", "marketplace_id": marketplace_id}])
    if prodotto.description:
        _imposta("product_description", [{"value": prodotto.description, "marketplace_id": marketplace_id}])
    if prodotto.bullet_points:
        attributi["bullet_point"] = [{"value": b, "marketplace_id": marketplace_id} for b in prodotto.bullet_points]
    if prodotto.images:
        attributi["main_product_image_locator"] = [{"media_location": prodotto.images[0], "marketplace_id": marketplace_id}]
        for i, url in enumerate(prodotto.images[1:9], start=2):
            attributi[f"other_product_image_locator_{i - 1}"] = [{"media_location": url, "marketplace_id": marketplace_id}]
    if prodotto.ean:
        _imposta("externally_assigned_product_identifier",
                  [{"type": "EAN", "value": prodotto.ean, "marketplace_id": marketplace_id}])

    attributi.update(prodotto.attributes)  # attributi di categoria, già nel formato Amazon

    return {"productType": prodotto.product_type, "requirements": "LISTING", "attributes": attributi}


@dataclass
class EsitoCreazione:
    sku: str
    stato: str  # "ACCEPTED" | "INVALID" | "errore"
    issues: list[dict]


def pubblica_uno(client, seller_id: str, marketplace_id: str, prodotto: Prodotto) -> EsitoCreazione:
    payload = costruisci_payload(prodotto, marketplace_id)
    risposta = client.put(
        f"/listings/2021-08-01/items/{seller_id}/{prodotto.sku}",
        json_body=payload,
        params={"marketplaceIds": marketplace_id},
    )
    return EsitoCreazione(
        sku=risposta.get("sku", prodotto.sku),
        stato=risposta.get("status", "INVALID"),
        issues=risposta.get("issues", []),
    )


def pubblica_lotto(client, seller_id: str, marketplace_id: str, cliente: str,
                    prodotti: list[Prodotto]) -> list[EsitoCreazione]:
    """Un'inserzione alla volta. Un fallimento non ferma le altre: si
    registra e si continua (DESIGN §10)."""
    esiti = []
    for prodotto in prodotti:
        indice = registro.registra_tentativo_creazione(cliente, prodotto.sku)
        try:
            esito = pubblica_uno(client, seller_id, marketplace_id, prodotto)
        except SpApiError as errore:
            registro.registra_esito_creazione(cliente, indice, "errore", [{"message": str(errore)}])
            esiti.append(EsitoCreazione(prodotto.sku, "errore", [{"message": str(errore)}]))
            continue
        registro.registra_esito_creazione(cliente, indice, esito.stato, esito.issues)
        esiti.append(esito)
    return esiti
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_crea.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_crea.py scripts/crea.py
git commit -m "feat: costruzione payload e pubblicazione lotto (creazione)"
```

---

### Task 10: `letto.py` — stato attuale di un'inserzione

**Files:**
- Create: `scripts/letto.py`
- Test: `tests/test_letto.py`

**Interfaces:**
- Consumes: `SpApiClient` (Task 1)
- Produces: `@dataclass StatoAttuale(sku, product_type, attributi: dict)`;
  `leggi(client, seller_id, marketplace_id, sku) -> StatoAttuale`

Lo stato va letto **per intero**, non solo i campi che si pensa di dover cambiare:
un'operazione di patch su un attributo multi-valore lo sostituisce tutto (Global
Constraints), quindi senza il "prima" completo non si può scrivere in sicurezza.

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_letto.py
from unittest.mock import Mock
from letto import leggi, StatoAttuale


def test_leggi_restituisce_attributi_per_intero():
    client = Mock()
    client.get.return_value = {
        "sku": "ABC-1",
        "summaries": [{"productType": "EXERCISE_MAT", "asin": "B000TEST01"}],
        "attributes": {
            "item_name": [{"value": "Tappeto yoga", "marketplace_id": "APJ6JRA9NG5V4"}],
            "bullet_point": [
                {"value": "Antiscivolo", "marketplace_id": "APJ6JRA9NG5V4"},
                {"value": "Ecologico", "marketplace_id": "APJ6JRA9NG5V4"},
            ],
        },
    }
    stato = leggi(client, "SELLER1", "APJ6JRA9NG5V4", "ABC-1")
    assert stato == StatoAttuale(
        sku="ABC-1", product_type="EXERCISE_MAT",
        attributi=client.get.return_value["attributes"],
    )
    client.get.assert_called_once_with(
        "/listings/2021-08-01/items/SELLER1/ABC-1",
        params={"marketplaceIds": "APJ6JRA9NG5V4", "includedData": "attributes,issues,summaries"},
    )


def test_leggi_senza_summaries_lascia_product_type_vuoto():
    client = Mock()
    client.get.return_value = {"sku": "ABC-1", "attributes": {}}
    stato = leggi(client, "SELLER1", "APJ6JRA9NG5V4", "ABC-1")
    assert stato.product_type == ""
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_letto.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Scrivi `scripts/letto.py`**

```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class StatoAttuale:
    sku: str
    product_type: str
    attributi: dict


def leggi(client, seller_id: str, marketplace_id: str, sku: str) -> StatoAttuale:
    risposta = client.get(
        f"/listings/2021-08-01/items/{seller_id}/{sku}",
        params={"marketplaceIds": marketplace_id, "includedData": "attributes,issues,summaries"},
    )
    riassunti = risposta.get("summaries") or [{}]
    product_type = riassunti[0].get("productType", "")
    return StatoAttuale(sku=sku, product_type=product_type, attributi=risposta.get("attributes", {}))
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_letto.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_letto.py scripts/letto.py
git commit -m "feat: lettura dello stato attuale di un'inserzione"
```

---

### Task 11: `modifiche.py` + `diff.py` — pacchetto di modifiche e anteprima

**Files:**
- Create: `scripts/modifiche.py`, `scripts/diff.py`
- Test: `tests/test_modifiche.py`, `tests/test_diff.py`

**Interfaces:**
- Consumes: `StatoAttuale` (Task 10)
- Produces: `@dataclass Modifica(campo, prima, dopo, fonte)`;
  `componi(stato, proposte: dict, fonte: str) -> list[Modifica]`;
  `sostituisci_elemento(valori_attuali: list, indice: int, nuovo_valore) -> list`;
  `aggiungi_elemento(valori_attuali: list, nuovo_valore) -> list`;
  `rendi_tabella(sku: str, modifiche: list[Modifica]) -> str`

`sostituisci_elemento`/`aggiungi_elemento` sono l'unico modo previsto per costruire il
valore "dopo" di un attributo multi-valore: garantiscono che l'array proposto contenga
sempre tutti gli elementi esistenti, non solo quello cambiato (Global Constraints —
lezione da `ebay-aggiorna-inserzione`).

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_modifiche.py
import pytest
from letto import StatoAttuale
from modifiche import Modifica, componi, sostituisci_elemento, aggiungi_elemento


def test_componi_scarta_valori_identici():
    stato = StatoAttuale(sku="A", product_type="X", attributi={
        "list_price": [{"value": 29.9, "currency": "EUR"}],
    })
    modifiche = componi(stato, {"list_price": [{"value": 29.9, "currency": "EUR"}]}, "manuale")
    assert modifiche == []


def test_componi_include_un_valore_diverso():
    stato = StatoAttuale(sku="A", product_type="X", attributi={
        "list_price": [{"value": 29.9, "currency": "EUR"}],
    })
    modifiche = componi(stato, {"list_price": [{"value": 24.9, "currency": "EUR"}]}, "manuale")
    assert modifiche == [Modifica(
        campo="list_price", prima=[{"value": 29.9, "currency": "EUR"}],
        dopo=[{"value": 24.9, "currency": "EUR"}], fonte="manuale",
    )]


@pytest.mark.parametrize("dopo", [None, "", [], {}])
def test_componi_scarta_sempre_un_dopo_vuoto_anche_se_prima_non_lo_era(dopo):
    stato = StatoAttuale(sku="A", product_type="X", attributi={
        "product_description": [{"value": "Descrizione esistente"}],
    })
    modifiche = componi(stato, {"product_description": dopo}, "manuale")
    assert modifiche == []


def test_componi_include_un_campo_nuovo_non_ancora_presente():
    stato = StatoAttuale(sku="A", product_type="X", attributi={})
    modifiche = componi(stato, {"colore": [{"value": "Blu"}]}, "fornitore")
    assert modifiche[0].prima is None
    assert modifiche[0].dopo == [{"value": "Blu"}]


def test_sostituisci_elemento_non_perde_gli_altri():
    attuali = [{"value": "A"}, {"value": "B"}, {"value": "C"}, {"value": "D"}, {"value": "E"}]
    nuovi = sostituisci_elemento(attuali, 2, {"value": "C-modificato"})
    assert len(nuovi) == 5
    assert nuovi[2] == {"value": "C-modificato"}
    assert nuovi[0] == attuali[0]
    assert nuovi[4] == attuali[4]
    # l'originale non va toccato
    assert attuali[2] == {"value": "C"}


def test_sostituisci_elemento_indice_fuori_range_alza_valueerror():
    with pytest.raises(ValueError, match="indice"):
        sostituisci_elemento([{"value": "A"}], 5, {"value": "X"})


def test_aggiungi_elemento_non_tocca_i_precedenti():
    attuali = [{"value": "A"}, {"value": "B"}]
    nuovi = aggiungi_elemento(attuali, {"value": "C"})
    assert nuovi == [{"value": "A"}, {"value": "B"}, {"value": "C"}]
    assert attuali == [{"value": "A"}, {"value": "B"}]
```

```python
# tests/test_diff.py
from modifiche import Modifica
from diff import rendi_tabella


def test_rendi_tabella_senza_modifiche():
    testo = rendi_tabella("ABC-1", [])
    assert "nessuna modifica" in testo
    assert "ABC-1" in testo


def test_rendi_tabella_mostra_prima_dopo_e_fonte():
    modifiche = [Modifica(campo="list_price", prima=29.9, dopo=24.9, fonte="manuale")]
    testo = rendi_tabella("ABC-1", modifiche)
    assert "list_price" in testo
    assert "29.9" in testo
    assert "24.9" in testo
    assert "manuale" in testo
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_modifiche.py tests/test_diff.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Scrivi `scripts/modifiche.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from letto import StatoAttuale


@dataclass
class Modifica:
    campo: str
    prima: object
    dopo: object
    fonte: str


def componi(stato: StatoAttuale, proposte: dict, fonte: str) -> list[Modifica]:
    """`proposte` è {attributo: nuovo_valore_completo}. Un attributo
    multi-valore deve arrivare già completo — costruito con
    sostituisci_elemento/aggiungi_elemento, mai un singolo elemento isolato.
    Un valore "dopo" vuoto non tocca mai un campo già pubblicato: si scarta
    sempre, non si propone come svuotamento (Global Constraints)."""
    modifiche = []
    for campo, dopo in proposte.items():
        if dopo in (None, "", [], {}):
            continue
        prima = stato.attributi.get(campo)
        if prima == dopo:
            continue
        modifiche.append(Modifica(campo=campo, prima=prima, dopo=dopo, fonte=fonte))
    return modifiche


def sostituisci_elemento(valori_attuali: list, indice: int, nuovo_valore) -> list:
    """L'intero elenco con SOLO l'elemento all'indice indicato cambiato.
    Amazon sostituisce l'attributo per intero: omettere un elemento
    equivale a cancellarlo."""
    if not (0 <= indice < len(valori_attuali)):
        raise ValueError(f"indice {indice} fuori dai {len(valori_attuali)} valori attuali")
    nuovi = list(valori_attuali)
    nuovi[indice] = nuovo_valore
    return nuovi


def aggiungi_elemento(valori_attuali: list, nuovo_valore) -> list:
    return list(valori_attuali) + [nuovo_valore]
```

- [ ] **Step 3b: Scrivi `scripts/diff.py`**

```python
from __future__ import annotations
from modifiche import Modifica


def rendi_tabella(sku: str, modifiche: list[Modifica]) -> str:
    if not modifiche:
        return f"{sku}: nessuna modifica proposta (tutti i valori sono già identici a quelli online)"
    righe = [sku]
    for m in modifiche:
        righe.append(f"  {m.campo}: {m.prima!r} → {m.dopo!r}  [{m.fonte}]")
    return "\n".join(righe)
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_modifiche.py tests/test_diff.py -v`
Expected: 9 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_modifiche.py tests/test_diff.py scripts/modifiche.py scripts/diff.py
git commit -m "feat: pacchetto di modifiche con protezione multi-valore/svuotamento, anteprima"
```

---

### Task 12: `applica.py` — scrittura, rilettura di verifica

**Files:**
- Create: `scripts/applica.py`
- Test: `tests/test_applica.py`

**Interfaces:**
- Consumes: `SpApiClient` (Task 1), `Modifica`/`StatoAttuale` (Task 10-11), `letto.leggi`
  (Task 10), `registro.*` (Task 4)
- Produces: `costruisci_patch(modifiche: list[Modifica]) -> list[dict]`;
  `@dataclass EsitoVerifica(campo, stato, atteso, trovato)`;
  `applica(client, seller_id, marketplace_id, cliente, sku, product_type, modifiche) ->
  tuple[str, list[dict], list[EsitoVerifica]]`

Registra il tentativo **prima** della chiamata (col "prima"/"dopo" completi, per
`annulla.py`), rilegge solo se `status == "ACCEPTED"`, e dichiara per ogni campo
`applicato` / `applicato diversamente` / `non applicato`.

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_applica.py
from unittest.mock import Mock, call
import pytest
from modifiche import Modifica
from applica import costruisci_patch, applica, EsitoVerifica


def test_costruisci_patch_un_replace_per_attributo():
    modifiche = [
        Modifica(campo="list_price", prima=[{"value": 29.9}], dopo=[{"value": 24.9}], fonte="manuale"),
        Modifica(campo="bullet_point", prima=[{"value": "A"}], dopo=[{"value": "A"}, {"value": "B"}], fonte="fornitore"),
    ]
    patch = costruisci_patch(modifiche)
    assert patch == [
        {"op": "replace", "path": "/attributes/list_price", "value": [{"value": 24.9}]},
        {"op": "replace", "path": "/attributes/bullet_point", "value": [{"value": "A"}, {"value": "B"}]},
    ]


def test_applica_niente_modifiche_alza_valueerror():
    with pytest.raises(ValueError, match="nessuna modifica"):
        applica(Mock(), "SELLER1", "APJ6JRA9NG5V4", "Bedshire", "ABC-1", "EXERCISE_MAT", [])


def test_applica_registra_prima_di_scrivere_e_rilegge_se_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.patch.return_value = {"status": "ACCEPTED", "issues": []}
    client.get.return_value = {"attributes": {"list_price": [{"value": 24.9}]}, "summaries": []}
    modifiche = [Modifica(campo="list_price", prima=[{"value": 29.9}], dopo=[{"value": 24.9}], fonte="manuale")]

    esito, issues, verifiche = applica(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire",
                                        "ABC-1", "EXERCISE_MAT", modifiche)

    assert esito == "ACCEPTED"
    assert issues == []
    assert verifiche == [EsitoVerifica(campo="list_price", stato="applicato",
                                        atteso=[{"value": 24.9}], trovato=[{"value": 24.9}])]
    # il tentativo è registrato PRIMA della chiamata patch
    import registro
    riga = registro.ultimo_lotto("Bedshire", "aggiornamenti")[0]
    assert riga["campi"]["list_price"] == {"prima": [{"value": 29.9}], "dopo": [{"value": 24.9}]}
    assert riga["esito"] == "ACCEPTED"


def test_applica_marca_applicato_diversamente_quando_amazon_normalizza(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.patch.return_value = {"status": "ACCEPTED", "issues": []}
    client.get.return_value = {"attributes": {"bullet_point": [{"value": "Antiscivolo (TPE)"}]}, "summaries": []}
    modifiche = [Modifica(campo="bullet_point", prima=[{"value": "vecchio"}],
                           dopo=[{"value": "Antiscivolo"}], fonte="manuale")]
    _, _, verifiche = applica(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire",
                               "ABC-1", "EXERCISE_MAT", modifiche)
    assert verifiche[0].stato == "applicato diversamente"


def test_applica_marca_non_applicato_quando_amazon_non_cambia_nulla(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.patch.return_value = {"status": "ACCEPTED", "issues": []}
    client.get.return_value = {"attributes": {"list_price": [{"value": 29.9}]}, "summaries": []}
    modifiche = [Modifica(campo="list_price", prima=[{"value": 29.9}], dopo=[{"value": 24.9}], fonte="manuale")]
    _, _, verifiche = applica(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire",
                               "ABC-1", "EXERCISE_MAT", modifiche)
    assert verifiche[0].stato == "non applicato"


def test_applica_non_rilegge_se_invalid(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.patch.return_value = {"status": "INVALID", "issues": [{"message": "prezzo negativo"}]}
    modifiche = [Modifica(campo="list_price", prima=[{"value": 29.9}], dopo=[{"value": -1}], fonte="manuale")]
    esito, issues, verifiche = applica(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire",
                                        "ABC-1", "EXERCISE_MAT", modifiche)
    assert esito == "INVALID"
    assert verifiche == []
    client.get.assert_not_called()
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_applica.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Scrivi `scripts/applica.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from modifiche import Modifica
from letto import leggi
import registro


def costruisci_patch(modifiche: list[Modifica]) -> list[dict]:
    """Un'operazione replace per attributo. Ogni Modifica.dopo deve essere
    già il valore completo da scrivere (modifiche.componi + sostituisci_
    elemento/aggiungi_elemento): qui non si aggrega nulla, si traduce solo
    in JSON Patch."""
    return [{"op": "replace", "path": f"/attributes/{m.campo}", "value": m.dopo} for m in modifiche]


@dataclass
class EsitoVerifica:
    campo: str
    stato: str  # "applicato" | "applicato diversamente" | "non applicato"
    atteso: object
    trovato: object


def applica(client, seller_id: str, marketplace_id: str, cliente: str, sku: str,
            product_type: str, modifiche: list[Modifica]) -> tuple[str, list[dict], list[EsitoVerifica]]:
    if not modifiche:
        raise ValueError("nessuna modifica da applicare")

    campi_log = {m.campo: {"prima": m.prima, "dopo": m.dopo} for m in modifiche}
    indice = registro.registra_tentativo_aggiornamento(cliente, sku, product_type, campi_log)

    payload = {"productType": product_type, "patches": costruisci_patch(modifiche)}
    risposta = client.patch(f"/listings/2021-08-01/items/{seller_id}/{sku}",
                             json_body=payload, params={"marketplaceIds": marketplace_id})
    esito = risposta.get("status", "INVALID")
    issues = risposta.get("issues", [])
    registro.registra_esito_aggiornamento(cliente, indice, esito, issues)

    verifiche: list[EsitoVerifica] = []
    if esito == "ACCEPTED":
        rilettura = leggi(client, seller_id, marketplace_id, sku)
        for m in modifiche:
            trovato = rilettura.attributi.get(m.campo)
            if trovato == m.dopo:
                stato_v = "applicato"
            elif trovato == m.prima:
                stato_v = "non applicato"
            else:
                stato_v = "applicato diversamente"
            verifiche.append(EsitoVerifica(campo=m.campo, stato=stato_v, atteso=m.dopo, trovato=trovato))
    return esito, issues, verifiche
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_applica.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_applica.py scripts/applica.py
git commit -m "feat: applicazione modifiche con rilettura di verifica"
```

---

### Task 13: `annulla.py` — ripristino dall'ultima riga di registro

**Files:**
- Create: `scripts/annulla.py`
- Test: `tests/test_annulla.py`

**Interfaces:**
- Consumes: `registro.ultimo_lotto` (Task 4), `Modifica` (Task 11), `applica.applica`
  (Task 12)
- Produces: `annulla_ultimo_lotto(client, seller_id, marketplace_id, cliente) ->
  dict[str, tuple[str, list[dict]]]`

Se uno SKU è stato aggiornato più volte nello stesso giorno, il ripristino usa **solo**
l'ultima riga per quello SKU — non una ricomposizione fra più righe (Global Constraints).

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_annulla.py
from unittest.mock import patch
import registro
from annulla import annulla_ultimo_lotto


def test_annulla_usa_solo_lultima_riga_per_sku(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT", {"list_price": {"prima": [{"value": 29.9}], "dopo": [{"value": 24.9}]}})
    registro.registra_esito_aggiornamento("Bedshire", i1, "ACCEPTED", [])
    i2 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT", {"list_price": {"prima": [{"value": 24.9}], "dopo": [{"value": 19.9}]}})
    registro.registra_esito_aggiornamento("Bedshire", i2, "ACCEPTED", [])

    with patch("annulla.applica") as mock_applica:
        mock_applica.return_value = ("ACCEPTED", [], [])
        annulla_ultimo_lotto(object(), "SELLER1", "APJ6JRA9NG5V4", "Bedshire")

    modifiche_passate = mock_applica.call_args.args[6]
    assert len(modifiche_passate) == 1
    m = modifiche_passate[0]
    # si torna al 'prima' della SECONDA riga (24.9), non della prima (29.9)
    assert m.dopo == [{"value": 24.9}]
    assert m.prima == [{"value": 19.9}]


def test_annulla_ignora_righe_non_accettate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT", {"list_price": {"prima": [{"value": 29.9}], "dopo": [{"value": -1}]}})
    registro.registra_esito_aggiornamento("Bedshire", i1, "INVALID", [{"message": "prezzo negativo"}])

    with patch("annulla.applica") as mock_applica:
        esiti = annulla_ultimo_lotto(object(), "SELLER1", "APJ6JRA9NG5V4", "Bedshire")

    mock_applica.assert_not_called()
    assert esiti == {}


def test_annulla_non_ripropone_uno_svuotamento(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT",
        {"list_price": {"prima": [], "dopo": [{"value": 24.9}]}})  # campo nuovo, prima assente
    registro.registra_esito_aggiornamento("Bedshire", i1, "ACCEPTED", [])

    with patch("annulla.applica") as mock_applica:
        mock_applica.return_value = ("ACCEPTED", [], [])
        annulla_ultimo_lotto(object(), "SELLER1", "APJ6JRA9NG5V4", "Bedshire")

    mock_applica.assert_not_called()  # nessuna modifica: 'prima' era vuoto, non si ripropone uno svuotamento


def test_annulla_passa_dallo_stesso_cancello(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT", {"list_price": {"prima": [{"value": 29.9}], "dopo": [{"value": 24.9}]}})
    registro.registra_esito_aggiornamento("Bedshire", i1, "ACCEPTED", [])

    with patch("annulla.applica") as mock_applica:
        mock_applica.return_value = ("ACCEPTED", [], [])
        esiti = annulla_ultimo_lotto("client-finto", "SELLER1", "APJ6JRA9NG5V4", "Bedshire")

    mock_applica.assert_called_once()
    args = mock_applica.call_args.args
    assert args[:6] == ("client-finto", "SELLER1", "APJ6JRA9NG5V4", "Bedshire", "ABC-1", "EXERCISE_MAT")
    assert esiti == {"ABC-1": ("ACCEPTED", [])}
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest tests/test_annulla.py -v`
Expected: FAIL con `ModuleNotFoundError`

- [ ] **Step 3: Scrivi `scripts/annulla.py`**

```python
from __future__ import annotations
from modifiche import Modifica
import registro
from applica import applica


def annulla_ultimo_lotto(client, seller_id: str, marketplace_id: str, cliente: str
                          ) -> dict[str, tuple[str, list[dict]]]:
    """Ripristina, per ogni SKU, SOLO l'ultima riga di aggiornamento
    registrata oggi per quel SKU — non un valore ricomposto confrontando
    più righe (Global Constraints)."""
    righe = registro.ultimo_lotto(cliente, "aggiornamenti")
    ultima_per_sku: dict[str, dict] = {}
    for riga in righe:
        if riga.get("esito") == "ACCEPTED":
            ultima_per_sku[riga["sku"]] = riga  # l'ultima con quello sku sovrascrive

    esiti: dict[str, tuple[str, list[dict]]] = {}
    for sku, riga in ultima_per_sku.items():
        modifiche = [
            Modifica(campo=campo, prima=v["dopo"], dopo=v["prima"], fonte="annulla")
            for campo, v in riga["campi"].items()
            if v["prima"] not in (None, "", [], {})
        ]
        if not modifiche:
            continue
        esito, issues, _ = applica(client, seller_id, marketplace_id, cliente, sku,
                                    riga["product_type"], modifiche)
        esiti[sku] = (esito, issues)
    return esiti
```

- [ ] **Step 4: Verifica che i test passino**

Run: `python3 -m pytest tests/test_annulla.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_annulla.py scripts/annulla.py
git commit -m "feat: annulla dall'ultima riga di registro per SKU"
```

---

### Task 14: `SKILL.md` + prova a vuoto end-to-end

**Files:**
- Create: `SKILL.md`
- Test: `tests/test_prova_a_vuoto.py`

**Interfaces:**
- Consumes: tutti i moduli dei Task 1-13

La "prova a vuoto" di DESIGN §12: l'intera catena di creazione, dai dati dettati fino al
payload esatto che si manderebbe ad Amazon, senza mai toccare la rete. È il collaudo che
sostituisce la sandbox (probabilmente inesistente per le chiavi di produzione, come su
eBay — da confermare al primo giro reale, Task 15).

- [ ] **Step 1: Scrivi il test end-to-end**

```python
# tests/test_prova_a_vuoto.py
import json
import os
from estrattore_manuale import da_dati_manuali
from validazione import valuta
from crea import costruisci_payload

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_catena_creazione_fino_al_payload_esatto():
    with open(os.path.join(_FIXTURES, "schema_categoria_test.json")) as f:
        schema = json.load(f)

    prodotto = da_dati_manuali({
        "sku": "TEST-001", "title": "Tappeto yoga antiscivolo 183x61cm",
        "price": 29.90, "quantity": 50, "product_type": "EXERCISE_MAT",
        "brand": "New Era Test", "description": "Tappeto in TPE, 183x61cm",
        "bullet_points": ["Antiscivolo", "Ecologico"],
        "images": ["https://esempio.it/1.jpg"],
        "attributes": {"colore": [{"value": "Blu", "marketplace_id": "APJ6JRA9NG5V4"}]},
    })

    rilievo = valuta(prodotto, schema)
    assert rilievo.semaforo == "verde", rilievo.mancanti_obbligatori

    payload = costruisci_payload(prodotto, marketplace_id="APJ6JRA9NG5V4")
    assert payload["productType"] == "EXERCISE_MAT"
    assert payload["attributes"]["item_name"][0]["value"] == "Tappeto yoga antiscivolo 183x61cm"
    assert payload["attributes"]["colore"][0]["value"] == "Blu"
    assert "list_price" in payload["attributes"]
```

- [ ] **Step 2: Verifica che passi (usa solo moduli già scritti)**

Run: `python3 -m pytest tests/test_prova_a_vuoto.py -v`
Expected: 1 PASS

- [ ] **Step 3: Scrivi `SKILL.md`**

```markdown
---
name: amazon-inserzioni
description: >
  Crea inserzioni nuove e aggiorna quelle già online su Amazon Seller Central
  per i clienti New Era, con anteprima e permesso esplicito prima di ogni
  scrittura. Usare quando Mariano chiede di caricare un prodotto su Amazon,
  aggiornare prezzo/quantità/contenuto di un'inserzione Amazon già online, o
  fare per Amazon quello che l'agente eBay fa su eBay. Trigger tipici: "carica
  questo prodotto su Amazon per X", "aggiorna il prezzo su Amazon di X",
  "metti questo su Amazon per X", "cambia la quantità dell'inserzione Amazon
  di X", "pubblica questi prodotti su Amazon per X". NON tocca Vendor Central
  (Biscottini): solo Seller Central. Autonomia gialla — mai una scrittura
  senza un "vai" esplicito (creazione) o un permesso esplicito per singola
  inserzione (aggiornamento). Un cliente, un marketplace, alla volta.
---

# Amazon Inserzioni — crea e aggiorna (Seller Central)

Vedi `DESIGN.md` per l'architettura completa. Riassunto operativo:

## Creare

1. Raccogli i prodotti da una delle tre fonti: `estrattore_fornitore.importa` (file +
   mappatura), `estrattore_generico.estrai_da_link` (un link), `estrattore_manuale.da_dati_manuali`
   (dettatura). Un dominio sconosciuto prova il generico; se fallisce, si passa a mano.
2. `catalogo.schema_categoria` per il `product_type` di destinazione.
3. `validazione.valuta_tutti` → mostra la tabella con semaforo 🟢🟡🔴 in chat.
4. Mariano pota le righe, corregge prezzo/quantità.
5. **"vai"** — parola richiesta, non si deduce da silenzio o approvazioni precedenti.
6. `crea.pubblica_lotto` — un'inserzione alla volta.
7. Mostra sempre gli `issues` tornati da Amazon (errori e avvisi), anche sulle
   pubblicazioni accettate.

## Aggiornare

1. Bersaglio: SKU indicati da Mariano.
2. `letto.leggi` → stato attuale, per intero.
3. Contenuto nuovo da una delle fonti sopra, o dettato a voce.
4. `modifiche.componi` (usando `sostituisci_elemento`/`aggiungi_elemento` per ogni
   attributo multi-valore) → pacchetto di modifiche.
5. `diff.rendi_tabella` → anteprima prima → dopo, per inserzione.
6. **Un "vai" per inserzione.** Mai lotto implicito, mai per iniziativa.
7. `applica.applica` → scrive solo i campi approvati, rilegge, dichiara
   applicato/applicato diversamente/non applicato.
8. `annulla.annulla_ultimo_lotto` su richiesta ("annulla") — passa dallo stesso cancello.

## Regole che non si negoziano

- Mai un attributo mancante inventato.
- Mai un valore "dopo" vuoto in un pacchetto di modifiche.
- Mai un elemento isolato per un attributo multi-valore: sempre l'array intero.
- `annulla` legge solo l'ultima riga di registro per lo SKU.
- Le credenziali SP-API le inserisce e le rigenera Mariano — mai in chat, mai chieste.
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_prova_a_vuoto.py SKILL.md
git commit -m "feat: SKILL.md e prova a vuoto end-to-end (creazione)"
```

---

### Task 15: Smoke test reale — BLOCCATO

**Non eseguibile finché non sono disponibili (DESIGN §13):**

1. **Cliente e categoria pilota** — da Mariano
2. **Credenziali SP-API rigenerate** — `SPAPI_LWA_CLIENT_ID`, `SPAPI_LWA_CLIENT_SECRET`,
   refresh token dell'account del cliente pilota. Azione 🔴: le inserisce Mariano, mai
   in chat, mai in un file committato — in variabili d'ambiente locali o in un file
   `.env` fuori da git (aggiungere a `.gitignore` prima di crearlo)

**Quando sono disponibili, in quest'ordine:**

> **Esito Step 1-2 (2026-09-02, sola lettura, account Phy to Pet):**
> - **Sandbox**: `sandbox.sellingpartnerapi-eu.amazon.com` risponde con le stesse
>   credenziali (1 marketplace statico). È un mock statico: per il dry-run vero si usa
>   `mode=VALIDATION_PREVIEW` (sotto), non la sandbox.
> - **Le 5 ipotesi del DESIGN §14**, verificate su schema reale + doc ufficiale:
>   1. ✅ JSON Patch: "Only top-level listings item attributes can be patched" — la
>      sostituzione dell'attributo intero assunta da `costruisci_patch` non è solo
>      giusta, è l'unica consentita.
>   2. ◐ put/patch rispondono **sincroni** (status ACCEPTED/INVALID/VALID + submissionId
>      + issues); la doc NON garantisce la propagazione immediata al listing — la
>      rilettura di `applica.py` resta da confermare dal vivo allo Step 4.
>   3. ✅ Immagini: `main_product_image_locator` = `{marketplace_id, media_location}` →
>      URL diretto, come assunto in `crea.costruisci_payload`.
>   4. ✅ Rate limit: 5 req/s (burst 10 put, 5 patch/delete) — il backoff di
>      `spapi_client.py` (5 tentativi, raddoppio da 1s) è adeguato.
>   5. ✅ **Il dry-run esiste**: `mode=VALIDATION_PREVIEW` su putListingsItem E
>      patchListingsItem ("validated without persisting"). Da usare come primo colpo
>      dello Step 3, come previsto.
> - **Schema `ANIMAL_NUTRITIONAL_SUPPLEMENT` (IT)**: 123 attributi, 6 obbligatori
>   top-level: brand, bullet_point, country_of_origin, item_name, product_description,
>   supplier_declared_dg_hz_regulation. ⚠️ Gli ultimi due NON sono campi nativi di
>   `Prodotto`: allo Step 3 vanno passati via `prodotto.attributes`.
> - Nessuna ipotesi sbagliata → nessuna correzione ai Task 1-13 richiesta.

- [x] **Step 1: Verifica se esiste una sandbox SP-API funzionante**

Prova le credenziali su un endpoint di sola lettura (`GET /sellers/v1/marketplaceParticipations`)
sia in produzione sia — se esiste per l'app registrata — in ambiente sandbox. Se le
chiavi di produzione non funzionano in sandbox (come su eBay, DESIGN §12.1), documentarlo
qui e trattare ogni scrittura successiva come irreversibile fino a smentita.

- [x] **Step 2: `cerca_product_type` + `schema_categoria` sul prodotto pilota vero**

Verifica dal vivo i cinque punti aperti del DESIGN (§14):
- se il JSON Patch sostituisce l'intero attributo o accetta granularità più fine
  (`costruisci_patch` in `applica.py`, Task 12, assume sostituzione intera)
- se `putListingsItem`/`patchListingsItem` sono sincroni o richiedono un `submissionId`
  (`applica.py`/`crea.py` assumono che la rilettura immediata sia già affidabile)
- il formato reale dell'attributo immagine per quella categoria — URL diretto
  (`media_location`, assunto in `crea.costruisci_payload`) o upload separato
- i limiti di frequenza reali (richieste/secondo) sull'endpoint Listings Items, per
  ricalibrare `_MAX_TENTATIVI`/il backoff di `spapi_client.py` se il default (5
  tentativi, raddoppio da 1s) fosse insufficiente o eccessivo
- se esiste una modalità "solo verifica" (dry-run) sulla Listings Items API: se sì, va
  usata al posto della prima scrittura vera nello Step 3

Se una di queste ipotesi risulta sbagliata, **si corregge il codice dei Task 1-13 prima
di proseguire** — non si aggira nello smoke test.

> **Esito Step 3 dry-run (2026-09-02, pilota B0FDQM7RNG = SKU vivo SPAZZOLINO_BAMBOO,
> TOOTHBRUSH, FBA):** flusso di creazione provato con `mode=VALIDATION_PREVIEW` (nulla
> scritto). Trovato e corretto un bug reale del builder: il prezzo andava in `list_price`
> col campo `value` — sbagliato due volte. Forma giusta (err 99022 + schema + inserzione
> viva): prezzo di vendita in `purchasable_offer.our_price[].schedule[].value_with_tax`
> E `list_price` (RRP) con `value_with_tax`, entrambi presenti. Dopo il fix:
> **status VALID, zero issues**. Nota: gli obbligatori di categoria (unit_count,
> manufacturer, part_number, color, number_of_items, merchant_suggested_asin, EAN/GTIN,
> is_heat_sensitive...) arrivano via `prodotto.attributes` — la validazione li elenca
> con precisione se mancano. La creazione REALE su questo ASIN duplicherebbe l'offerta
> del cliente su se stesso: rimandata a un prodotto davvero nuovo.

- [x] **Step 3: Creazione di un'inserzione vera**

Un prodotto reale del cliente pilota, attraverso il flusso completo (estrazione →
validazione → anteprima → "vai" → `pubblica_uno`). Verificare l'inserzione su Seller
Central, non solo la risposta API.

> **Esito Step 3 reale (2026-09-01, sessione parallela, col "vai" di Mariano):**
> famiglia varianti SNACK di Phy to Pet su Amazon.it, product_type PET_FOOD — padre
> `PTP-SNK-VARIETY-120` + 7 figli, tutti ACCEPTED, ASIN assegnati (B0HHFXY9L2 e
> figli). Senza immagini per scelta di Mariano. Due bug veri emersi e **corretti nel
> codice il 2026-09-02** (101 test): `externally_assigned_product_identifier.type`
> deve essere `"ean"` minuscolo (enum dello schema, uguale in tutte le 6 categorie
> viste — "EAN" fa fallire il matching GTIN e pretende merchant_suggested_asin);
> `validazione.valuta` cercava item_name/brand/bullet_point/product_description in
> `attributes` invece che nei campi base → falsi rossi. Terza lezione: gli
> obbligatori condizionali stanno negli `allOf` dello schema, non in `required`,
> e si vedono solo dalla risposta INVALID → ora `pubblica_uno(anteprima=True)`
> (VALIDATION_PREVIEW) è un passo della procedura prima del "vai". Rilettura
> 2026-09-02: i 7 figli sono **Incomplete** su Seller Central — hanno `list_price`
> ma non `purchasable_offer` (creati col builder prima del fix cb651c1) e nessuna
> immagine. Da completare con un aggiornamento (prezzo di vendita + foto).
> Gocce Oculari rimandato: nessun product_type trovato per la cura oculare animali.

> **Esito Step 4-5 (2026-09-02, dal vivo su SPAZZOLINO_BAMBOO, col "vai" di Mariano):**
> - Aggiornamento: `generic_keyword` assente → valorizzato. ACCEPTED, **rilettura
>   immediata affidabile** (ipotesi §14.2 chiusa: la verifica post-scrittura funziona).
>   Amazon arricchisce i valori (aggiunge `language_tag`): il verificatore lo marca
>   "applicato diversamente" a valore identico — comportamento noto, non un errore.
> - Annulla: trovati e corretti DUE bug che le fixture non potevano vedere:
>   1. la guardia anti-svuotamento rendeva inannullabile un campo aggiunto dal nulla —
>      ora il ripristino ad "assente" diventa `op: delete` (solo fonte "annulla");
>   2. il delete richiede il `value` e deve combaciare con l'attributo VIVO
>      (language_tag incluso): senza value → 400, col valore registrato → "errore
>      interno" 4000000. Ora `applica` rilegge prima di costruire patch con delete.
> - Ripristino verificato: `generic_keyword` di nuovo assente, inserzione com'era.
> - Nota: il ripristino finale è passato da una patch diretta durante la diagnosi; nel
>   registro l'annullamento risulta INVALID (il tentativo fallito) — stato Amazon pulito.

- [x] **Step 4: Aggiornamento reversibile**

Cambiare un solo campo facilmente reversibile (es. quantità) sull'inserzione appena
creata. Verificare con `letto.leggi` che la rilettura di `applica.py` (Task 12) rifletta
lo stato vero.

- [x] **Step 5: `annulla`**

Confermare che il valore torni quello di prima, e che il registro lo tracci.

- [x] **Step 6: Aggiornare la memoria di progetto** (2026-09-02)

Registrare in `project_amazon_inserzioni.md` l'esito dei tre punti del §14 verificati
allo Step 2 — diventano fatti noti per ogni sviluppo Amazon successivo, non solo per
questa skill.
