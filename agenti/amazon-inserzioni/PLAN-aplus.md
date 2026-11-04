# Contenuti A+ (amazon-inserzioni) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Estendere `amazon-inserzioni` per leggere e scrivere i Contenuti A+ (A+ Content
Manager) degli ASIN già gestiti dalla skill, con anteprima modulo per modulo e scrittura
solo dopo un "vai" esplicito — stesso cancello 🟡 del resto della skill, applicato
all'A+ Content Management API (`aplus-content-2020-11-01`) invece della Listings Items
API.

**Architecture:** File piatti in `scripts/` (stessa convenzione, niente sottocartelle),
stesso `SpApiClient` già esistente (stessa auth LWA per cliente, nessuna credenziale
nuova), un nuovo trittico leggi→componi→diff→applica parallelo a quello dei listing,
più un piccolo modulo a parte per l'upload immagini (Uploads API, host diverso da
SP-API per il caricamento fisico del file).

**Tech Stack:** Stesso di `PLAN.md` — Python 3.9 di sistema, `requests`, `pytest`, niente
SDK di terze parti.

**Spec:** `~/.claude/skills/amazon-inserzioni/DESIGN-aplus.md`

## Global Constraints

- 🟡 autonomia: nessuna scrittura su Amazon senza un "vai" esplicito per documento —
  DESIGN-aplus §1
- Un cliente, un marketplace, un ASIN alla volta — DESIGN-aplus §3
- Mai un modulo inventato: un pacchetto senza moduli proposti alza `ValueError`, non si
  scrive mai un documento vuoto — DESIGN-aplus §6
- **Niente `annulla` automatico in v1** — ogni risottomissione passa comunque per una
  nuova approvazione Amazon, non è un rollback istantaneo (DESIGN-aplus §8). Non
  implementare `aplus_annulla.py`.
- Dopo "vai" e scrittura, si dichiara **"sottomesso per approvazione"**, mai "pubblicato"
  — il badge ASIN (`CONTENT_PUBLISHED`) è l'unica fonte di verità e si rilegge in un giro
  successivo (DESIGN-aplus §8)
- Nessuna chiamata di rete nei test: tutto mockato (`unittest.mock`), stesso stile di
  `tests/test_applica.py` e `tests/test_spapi_client.py`
- Import piatti con `sys.path.insert` già configurato in `tests/conftest.py` — nessuna
  modifica lì necessaria
- Credenziali SP-API: stesse di `DESIGN.md` §11, nessuna nuova variabile d'ambiente

---

## File Structure

```
~/.claude/skills/amazon-inserzioni/
  scripts/
    registro.py         ESTESO: + registra_tentativo_aplus / registra_esito_aplus
    aplus_leggi.py       NUOVO: stato attuale di un ASIN (documento, moduli, badge)
    aplus_immagini.py    NUOVO: upload immagine via Uploads API
    aplus_componi.py     NUOVO: pacchetto di modifiche a moduli
    aplus_diff.py         NUOVO: anteprima prima → dopo, modulo per modulo
    aplus_applica.py     NUOVO: scrive, collega ASIN, sottomette, rilegge, registra
  tests/
    test_registro.py     ESTESO
    test_aplus_leggi.py   NUOVO
    test_aplus_immagini.py NUOVO
    test_aplus_componi.py NUOVO
    test_aplus_diff.py    NUOVO
    test_aplus_applica.py NUOVO
  SKILL.md               ESTESO: nuova sezione operativa "Contenuti A+"
```

---

### Task 1: registro.py — registrare i tentativi di scrittura sui Contenuti A+

**Files:**
- Modify: `scripts/registro.py` (aggiungere in fondo, dopo `ultimo_lotto`)
- Test: `tests/test_registro.py` (aggiungere in fondo)

**Interfaces:**
- Consumes: `_percorso(cliente, cartella)`, `_carica(percorso)`, `_salva(percorso, righe)` —
  già definite in `registro.py`
- Produces: `registra_tentativo_aplus(cliente: str, content_reference_key: str, asin: str, moduli: dict, *, origine: str = "contenuti-aplus") -> int`,
  `registra_esito_aplus(cliente: str, indice: int, esito: str, issues: list[dict]) -> None`
  — usate da `aplus_applica.py` (Task 6)

- [ ] **Step 1: Scrivi i test che falliscono**

Aggiungi in fondo a `tests/test_registro.py`:

```python
def test_aplus_tentativo_poi_esito(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    moduli = {"nome": "Storia del marchio", "moduli_prima": [], "moduli_dopo": [{"tipo": "STANDARD_TEXT"}]}
    indice = registro.registra_tentativo_aplus("Bedshire", "amzn1.aplus.cr.abc", "B0EXAMPLE1", moduli)
    riga = registro.ultimo_lotto("Bedshire", "contenuti-aplus")[indice]
    assert riga["content_reference_key"] == "amzn1.aplus.cr.abc"
    assert riga["asin"] == "B0EXAMPLE1"
    assert riga["moduli"] == moduli
    assert riga["esito"] == "in corso"
    registro.registra_esito_aplus("Bedshire", indice, "ACCEPTED", [])
    assert registro.ultimo_lotto("Bedshire", "contenuti-aplus")[indice]["esito"] == "ACCEPTED"


def test_aplus_non_si_mischia_con_gli_aggiornamenti_dei_listing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    registro.registra_tentativo_aplus("Bedshire", "amzn1.aplus.cr.abc", "B0EXAMPLE1", {})
    assert registro.ultimo_lotto("Bedshire", "aggiornamenti") == []
    assert registro.ultimo_lotto("Bedshire", "pubblicazioni") == []
```

- [ ] **Step 2: Esegui i test e verifica che falliscano**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_registro.py -v`
Expected: FAIL con `AttributeError: module 'registro' has no attribute 'registra_tentativo_aplus'`

- [ ] **Step 3: Implementa**

Aggiungi in fondo a `scripts/registro.py`:

```python
def registra_tentativo_aplus(cliente: str, content_reference_key: str, asin: str,
                              moduli: dict, *, origine: str = "contenuti-aplus") -> int:
    percorso = _percorso(cliente, "contenuti-aplus")
    righe = _carica(percorso)
    righe.append({
        "content_reference_key": content_reference_key, "asin": asin, "origine": origine,
        "ora": datetime.now(timezone.utc).isoformat(),
        "moduli": moduli, "esito": "in corso", "issues": [],
    })
    _salva(percorso, righe)
    return len(righe) - 1


def registra_esito_aplus(cliente: str, indice: int, esito: str, issues: list[dict]) -> None:
    percorso = _percorso(cliente, "contenuti-aplus")
    righe = _carica(percorso)
    righe[indice]["esito"] = esito
    righe[indice]["issues"] = issues
    _salva(percorso, righe)
```

- [ ] **Step 4: Esegui i test e verifica che passino**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_registro.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills/amazon-inserzioni
git add scripts/registro.py tests/test_registro.py
git commit -m "aplus: registro dedicato per i tentativi sui Contenuti A+"
```

---

### Task 2: aplus_leggi.py — stato attuale di un ASIN

**Files:**
- Create: `scripts/aplus_leggi.py`
- Test: `tests/test_aplus_leggi.py`

**Interfaces:**
- Consumes: `client.get(path, params=None) -> dict` (già in `spapi_client.SpApiClient`)
- Produces: `StatoAplus` (dataclass: `content_reference_key: str | None`, `asin: str`,
  `nome: str | None`, `stato_documento: str | None`, `moduli: list[dict]`,
  `badge_asin: str | None`), `leggi(client, marketplace_id: str, asin: str) -> StatoAplus`
  — usate da `aplus_componi.py` (Task 4) e `aplus_diff.py` (Task 5)

- [ ] **Step 1: Scrivi i test che falliscono**

Crea `tests/test_aplus_leggi.py`:

```python
from unittest.mock import Mock
from aplus_leggi import leggi, StatoAplus


def test_leggi_asin_senza_documento_esistente():
    client = Mock()
    client.get.return_value = {"contentMetadataRecords": [], "warnings": []}
    stato = leggi(client, "APJ6JRA9NG5V4", "B0EXAMPLE1")
    assert stato == StatoAplus(content_reference_key=None, asin="B0EXAMPLE1",
                                nome=None, stato_documento=None, moduli=[], badge_asin=None)
    # con nessun documento, non deve nemmeno provare a leggerne il contenuto
    assert client.get.call_count == 1


def test_leggi_asin_con_documento_esistente():
    client = Mock()
    client.get.side_effect = [
        {"contentMetadataRecords": [{
            "contentReferenceKey": "amzn1.aplus.cr.abc",
            "contentMetadata": {"name": "Storia del marchio", "status": "APPROVED"},
        }], "warnings": []},
        {"contentDocument": {"contentModuleList": [{"contentModuleType": "STANDARD_TEXT"}]},
         "warnings": []},
        {"asinMetadataSet": [{"asin": "B0EXAMPLE1", "badgeSet": ["CONTENT_PUBLISHED"]}],
         "warnings": []},
    ]
    stato = leggi(client, "APJ6JRA9NG5V4", "B0EXAMPLE1")
    assert stato.content_reference_key == "amzn1.aplus.cr.abc"
    assert stato.nome == "Storia del marchio"
    assert stato.stato_documento == "APPROVED"
    assert stato.moduli == [{"contentModuleType": "STANDARD_TEXT"}]
    assert stato.badge_asin == "CONTENT_PUBLISHED"
    assert client.get.call_args_list[0].args[0] == "/aplus/2020-11-01/contentDocuments"
    assert client.get.call_args_list[0].kwargs["params"] == {
        "marketplaceId": "APJ6JRA9NG5V4", "asin": "B0EXAMPLE1"}
    assert client.get.call_args_list[1].args[0] == "/aplus/2020-11-01/contentDocuments/amzn1.aplus.cr.abc"
    assert client.get.call_args_list[2].args[0] == "/aplus/2020-11-01/contentDocuments/amzn1.aplus.cr.abc/asins"
```

- [ ] **Step 2: Esegui i test e verifica che falliscano**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_aplus_leggi.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'aplus_leggi'`

- [ ] **Step 3: Implementa**

Crea `scripts/aplus_leggi.py`:

```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class StatoAplus:
    content_reference_key: str | None
    asin: str
    nome: str | None
    stato_documento: str | None
    moduli: list[dict]
    badge_asin: str | None  # "CONTENT_PUBLISHED" | "CONTENT_NOT_PUBLISHED" | None


def leggi(client, marketplace_id: str, asin: str) -> StatoAplus:
    """Cerca il documento A+ collegato all'ASIN (searchContentDocuments); se non
    esiste, non prova a leggerne il contenuto — nessun documento è uno stato
    legittimo, non un errore. Se esiste, legge moduli (getContentDocument) e
    badge di pubblicazione dell'ASIN (listContentDocumentAsinRelations)."""
    ricerca = client.get(
        "/aplus/2020-11-01/contentDocuments",
        params={"marketplaceId": marketplace_id, "asin": asin},
    )
    record = (ricerca.get("contentMetadataRecords") or [None])[0]
    if not record:
        return StatoAplus(content_reference_key=None, asin=asin, nome=None,
                           stato_documento=None, moduli=[], badge_asin=None)

    content_reference_key = record["contentReferenceKey"]
    metadata = record.get("contentMetadata", {})

    documento = client.get(
        f"/aplus/2020-11-01/contentDocuments/{content_reference_key}",
        params={"marketplaceId": marketplace_id, "includedDataSet": "CONTENTS"},
    )
    moduli = documento.get("contentDocument", {}).get("contentModuleList", [])

    relazioni = client.get(
        f"/aplus/2020-11-01/contentDocuments/{content_reference_key}/asins",
        params={"marketplaceId": marketplace_id, "asinSet": asin, "includedDataSet": "BADGE_SET"},
    )
    asin_metadata = next(
        (m for m in relazioni.get("asinMetadataSet", []) if m.get("asin") == asin), {})
    badge_set = asin_metadata.get("badgeSet") or []

    return StatoAplus(
        content_reference_key=content_reference_key, asin=asin,
        nome=metadata.get("name"), stato_documento=metadata.get("status"),
        moduli=moduli, badge_asin=badge_set[0] if badge_set else None,
    )
```

- [ ] **Step 4: Esegui i test e verifica che passino**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_aplus_leggi.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills/amazon-inserzioni
git add scripts/aplus_leggi.py tests/test_aplus_leggi.py
git commit -m "aplus: leggere il documento A+ e il badge di un ASIN"
```

---

### Task 3: aplus_immagini.py — caricare un'immagine per i moduli A+

**Files:**
- Create: `scripts/aplus_immagini.py`
- Test: `tests/test_aplus_immagini.py`

**Interfaces:**
- Consumes: `client.post(path, json_body=None, params=None) -> dict` (già in
  `spapi_client.SpApiClient`); `spapi_client.SpApiError`
- Produces: `carica_immagine(client, marketplace_id: str, percorso_file: str, content_type: str) -> str`
  (restituisce l'`uploadDestinationId` da mettere in `image.uploadDestinationId` di un
  modulo) — usata da `aplus_componi.py` (Task 4) quando Mariano fornisce un'immagine
  nuova

- [ ] **Step 1: Scrivi i test che falliscono**

Crea `tests/test_aplus_immagini.py`:

```python
from unittest.mock import Mock, patch
from aplus_immagini import carica_immagine
from spapi_client import SpApiError
import pytest


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
    # l'upload vero va all'host S3 restituito, coi parametri della policy come campi form
    url_chiamata, dati_form = mock_s3_post.call_args.args[0], mock_s3_post.call_args.kwargs["data"]
    assert url_chiamata == "https://aplus-media.s3.amazonaws.com/"
    assert dati_form == {"policy": "xyz", "key": "sc/f3af8f70.jpg", "x-amz-signature": "abc"}
    assert mock_s3_post.call_args.kwargs["files"]["file"] == b"contenuto finto jpeg"


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
```

- [ ] **Step 2: Esegui i test e verifica che falliscano**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_aplus_immagini.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'aplus_immagini'`

- [ ] **Step 3: Implementa**

Crea `scripts/aplus_immagini.py`:

```python
from __future__ import annotations
import hashlib
import base64
from urllib.parse import urlsplit, parse_qsl
import requests
from spapi_client import SpApiError

_RESOURCE_APLUS = "aplus/2020-11-01/contentDocuments"


def carica_immagine(client, marketplace_id: str, percorso_file: str, content_type: str) -> str:
    """Carica un'immagine per i Contenuti A+ in due passi: 1) createUploadDestination
    ForResource (via SP-API, stesso client autenticato) per ottenere una destinazione;
    2) upload del file vero al risultato — non allo stesso host SP-API. La destinazione
    per il bucket aplus-media torna come policy S3 firmata nella query string dell'url,
    da rispedire come campi form (mai come query string, altrimenti Amazon risponde
    "Conflicting query string parameters"); se un giorno Amazon tornasse un url +
    headers da usare con un PUT diretto (come per altre risorse Uploads API), si sceglie
    in base a cosa è stato effettivamente restituito, non per assunzione."""
    with open(percorso_file, "rb") as f:
        dati = f.read()
    md5 = base64.b64encode(hashlib.md5(dati).digest()).decode()

    risposta = client.post(
        f"/uploads/2020-11-01/uploadDestinations/{_RESOURCE_APLUS}",
        params={"marketplaceIds": marketplace_id, "contentMD5": md5, "contentType": content_type},
    )
    destinazione = risposta["payload"]
    _carica_su_destinazione(destinazione["url"], dati, destinazione.get("headers"))
    return destinazione["uploadDestinationId"]


def _carica_su_destinazione(url: str, dati: bytes, headers: dict | None) -> None:
    scomposto = urlsplit(url)
    campi_policy = dict(parse_qsl(scomposto.query))
    base_url = f"{scomposto.scheme}://{scomposto.netloc}{scomposto.path}"
    if campi_policy:
        r = requests.post(base_url, data=campi_policy, files={"file": dati}, timeout=60)
    else:
        r = requests.put(url, data=dati, headers=headers or {}, timeout=60)
    if r.status_code >= 300:
        raise SpApiError(f"upload immagine A+ fallito ({r.status_code}): {r.text[:300]}")
```

- [ ] **Step 4: Esegui i test e verifica che passino**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_aplus_immagini.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills/amazon-inserzioni
git add scripts/aplus_immagini.py tests/test_aplus_immagini.py
git commit -m "aplus: upload immagini via Uploads API"
```

---

### Task 4: aplus_componi.py — pacchetto di modifiche a moduli

**Files:**
- Create: `scripts/aplus_componi.py`
- Test: `tests/test_aplus_componi.py`

**Interfaces:**
- Consumes: `StatoAplus` (da `aplus_leggi.py`, Task 2)
- Produces: `PacchettoAplus` (dataclass: `asin: str`, `content_reference_key: str | None`,
  `nome: str`, `moduli_prima: list[dict]`, `moduli_dopo: list[dict]`),
  `componi(stato: StatoAplus, moduli_nuovi: list[dict], nome: str) -> PacchettoAplus`
  — usate da `aplus_diff.py` (Task 5) e `aplus_applica.py` (Task 6)

- [ ] **Step 1: Scrivi i test che falliscono**

Crea `tests/test_aplus_componi.py`:

```python
import pytest
from aplus_leggi import StatoAplus
from aplus_componi import componi, PacchettoAplus


def test_componi_documento_nuovo():
    stato = StatoAplus(content_reference_key=None, asin="B0EXAMPLE1", nome=None,
                        stato_documento=None, moduli=[], badge_asin=None)
    moduli_nuovi = [{"contentModuleType": "STANDARD_TEXT", "standardText": {"headline": {"value": "Titolo"}}}]
    pacchetto = componi(stato, moduli_nuovi, "Scheda tecnica")
    assert pacchetto == PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None,
                                        nome="Scheda tecnica", moduli_prima=[], moduli_dopo=moduli_nuovi)


def test_componi_documento_esistente_porta_sempre_la_lista_intera():
    stato = StatoAplus(content_reference_key="amzn1.aplus.cr.abc", asin="B0EXAMPLE1",
                        nome="Scheda tecnica", stato_documento="APPROVED",
                        moduli=[{"contentModuleType": "STANDARD_TEXT"}], badge_asin="CONTENT_PUBLISHED")
    moduli_nuovi = [{"contentModuleType": "STANDARD_TEXT"}, {"contentModuleType": "STANDARD_COMPANY_LOGO"}]
    pacchetto = componi(stato, moduli_nuovi, "Scheda tecnica")
    assert pacchetto.content_reference_key == "amzn1.aplus.cr.abc"
    assert pacchetto.moduli_prima == [{"contentModuleType": "STANDARD_TEXT"}]
    assert pacchetto.moduli_dopo == moduli_nuovi


def test_componi_senza_moduli_alza_valueerror():
    stato = StatoAplus(content_reference_key=None, asin="B0EXAMPLE1", nome=None,
                        stato_documento=None, moduli=[], badge_asin=None)
    with pytest.raises(ValueError, match="nessun modulo"):
        componi(stato, [], "Scheda tecnica")
```

- [ ] **Step 2: Esegui i test e verifica che falliscano**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_aplus_componi.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'aplus_componi'`

- [ ] **Step 3: Implementa**

Crea `scripts/aplus_componi.py`:

```python
from __future__ import annotations
from dataclasses import dataclass
from aplus_leggi import StatoAplus


@dataclass
class PacchettoAplus:
    asin: str
    content_reference_key: str | None
    nome: str
    moduli_prima: list[dict]
    moduli_dopo: list[dict]


def componi(stato: StatoAplus, moduli_nuovi: list[dict], nome: str) -> PacchettoAplus:
    """`moduli_nuovi` è SEMPRE la lista completa dei moduli desiderata, mai un
    modulo isolato da aggiungere ai vecchi: come per gli attributi multi-valore
    dei listing, qui l'intero contentModuleList è l'unità che si sostituisce."""
    if not moduli_nuovi:
        raise ValueError("nessun modulo proposto: un documento A+ non può restare vuoto")
    return PacchettoAplus(
        asin=stato.asin,
        content_reference_key=stato.content_reference_key,
        nome=nome,
        moduli_prima=stato.moduli,
        moduli_dopo=moduli_nuovi,
    )
```

- [ ] **Step 4: Esegui i test e verifica che passino**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_aplus_componi.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills/amazon-inserzioni
git add scripts/aplus_componi.py tests/test_aplus_componi.py
git commit -m "aplus: pacchetto di modifiche a moduli"
```

---

### Task 5: aplus_diff.py — anteprima prima → dopo

**Files:**
- Create: `scripts/aplus_diff.py`
- Test: `tests/test_aplus_diff.py`

**Interfaces:**
- Consumes: `PacchettoAplus` (da `aplus_componi.py`, Task 4)
- Produces: `rendi_tabella(pacchetto: PacchettoAplus) -> str` — usata in chat prima del
  "vai", stesso ruolo di `diff.rendi_tabella` per i listing

- [ ] **Step 1: Scrivi i test che falliscono**

Crea `tests/test_aplus_diff.py`:

```python
from aplus_componi import PacchettoAplus
from aplus_diff import rendi_tabella


def test_rendi_tabella_nessuna_modifica():
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key="amzn1.aplus.cr.abc",
                                nome="Scheda", moduli_prima=[{"a": 1}], moduli_dopo=[{"a": 1}])
    assert rendi_tabella(pacchetto) == "B0EXAMPLE1: nessuna modifica proposta (i moduli sono già identici a quelli online)"


def test_rendi_tabella_documento_nuovo():
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None,
                                nome="Scheda", moduli_prima=[], moduli_dopo=[{"a": 1}])
    tabella = rendi_tabella(pacchetto)
    assert "B0EXAMPLE1" in tabella
    assert "(nuovo)" in tabella
    assert "modulo 1: None → {'a': 1}" in tabella


def test_rendi_tabella_modulo_modificato():
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key="amzn1.aplus.cr.abc",
                                nome="Scheda", moduli_prima=[{"a": 1}], moduli_dopo=[{"a": 2}])
    tabella = rendi_tabella(pacchetto)
    assert "modulo 1: {'a': 1} → {'a': 2}" in tabella
```

- [ ] **Step 2: Esegui i test e verifica che falliscano**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_aplus_diff.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'aplus_diff'`

- [ ] **Step 3: Implementa**

Crea `scripts/aplus_diff.py`:

```python
from __future__ import annotations
from aplus_componi import PacchettoAplus


def rendi_tabella(pacchetto: PacchettoAplus) -> str:
    if pacchetto.moduli_prima == pacchetto.moduli_dopo:
        return f"{pacchetto.asin}: nessuna modifica proposta (i moduli sono già identici a quelli online)"
    riferimento = pacchetto.content_reference_key or "(nuovo)"
    righe = [f"{pacchetto.asin} — documento {riferimento} — {pacchetto.nome}"]
    n = max(len(pacchetto.moduli_prima), len(pacchetto.moduli_dopo))
    for i in range(n):
        prima = pacchetto.moduli_prima[i] if i < len(pacchetto.moduli_prima) else None
        dopo = pacchetto.moduli_dopo[i] if i < len(pacchetto.moduli_dopo) else None
        righe.append(f"  modulo {i + 1}: {prima!r} → {dopo!r}")
    return "\n".join(righe)
```

- [ ] **Step 4: Esegui i test e verifica che passino**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_aplus_diff.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/.claude/skills/amazon-inserzioni
git add scripts/aplus_diff.py tests/test_aplus_diff.py
git commit -m "aplus: anteprima modulo per modulo"
```

---

### Task 6: aplus_applica.py — scrivere, collegare l'ASIN, sottomettere, rileggere

**Files:**
- Create: `scripts/aplus_applica.py`
- Test: `tests/test_aplus_applica.py`

**Interfaces:**
- Consumes: `PacchettoAplus` (Task 4), `client.post`/`client.get` (SpApiClient),
  `registro.registra_tentativo_aplus`/`registra_esito_aplus` (Task 1)
- Produces: `EsitoAplus` (dataclass: `content_reference_key: str`, `esito: str`,
  `issues: list[dict]`, `badge_asin: str | None`),
  `applica(client, marketplace_id: str, cliente: str, pacchetto: PacchettoAplus, *, origine: str = "contenuti-aplus") -> EsitoAplus`

- [ ] **Step 1: Scrivi i test che falliscono**

Crea `tests/test_aplus_applica.py`:

```python
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
                                nome="Scheda", moduli_prima=[{"a": 1}], moduli_dopo=[{"a": 1}])
    with pytest.raises(ValueError, match="nessuna modifica"):
        applica(Mock(), "APJ6JRA9NG5V4", "Bedshire", pacchetto)


def test_applica_documento_nuovo_crea_collega_e_sottomette(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None, nome="Scheda",
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
                                nome="Scheda", moduli_prima=[{"a": 1}], moduli_dopo=[{"a": 2}])
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
    pacchetto = PacchettoAplus(asin="B0EXAMPLE1", content_reference_key=None, nome="Scheda",
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
```

- [ ] **Step 2: Esegui i test e verifica che falliscano**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_aplus_applica.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'aplus_applica'`

- [ ] **Step 3: Implementa**

Crea `scripts/aplus_applica.py`:

```python
from __future__ import annotations
from dataclasses import dataclass
from aplus_componi import PacchettoAplus
import registro


def _issues(risposta: dict) -> list[dict]:
    """A+ Content separa errori e avvisi in due liste ("errors"/"warnings"),
    diversamente dalla Listings Items API che li fonde in "issues" con una
    severità. Si fondono qui per riusare lo stesso schema del resto della
    skill, marcando la provenienza con "severity"."""
    issues = []
    for e in risposta.get("errors", []):
        issues.append({**e, "severity": "ERROR"})
    for w in risposta.get("warnings", []):
        issues.append({**w, "severity": "WARNING"})
    return issues


@dataclass
class EsitoAplus:
    content_reference_key: str
    esito: str  # "ACCEPTED" | "INVALID"
    issues: list[dict]
    badge_asin: str | None


def applica(client, marketplace_id: str, cliente: str, pacchetto: PacchettoAplus, *,
            origine: str = "contenuti-aplus") -> EsitoAplus:
    if pacchetto.moduli_dopo == pacchetto.moduli_prima:
        raise ValueError("nessuna modifica da applicare")

    campi_log = {"nome": pacchetto.nome, "moduli_prima": pacchetto.moduli_prima,
                 "moduli_dopo": pacchetto.moduli_dopo}
    indice = registro.registra_tentativo_aplus(cliente, pacchetto.content_reference_key or "",
                                                pacchetto.asin, campi_log, origine=origine)

    corpo = {"contentDocument": {
        "name": pacchetto.nome, "contentType": "EBC", "locale": "it_IT",
        "contentModuleList": pacchetto.moduli_dopo,
    }}
    if pacchetto.content_reference_key:
        risposta = client.post(f"/aplus/2020-11-01/contentDocuments/{pacchetto.content_reference_key}",
                                json_body=corpo, params={"marketplaceId": marketplace_id})
        content_reference_key = pacchetto.content_reference_key
    else:
        risposta = client.post("/aplus/2020-11-01/contentDocuments",
                                json_body=corpo, params={"marketplaceId": marketplace_id})
        content_reference_key = risposta.get("contentReferenceKey", "")

    issues = _issues(risposta)
    esito = "INVALID" if any(i["severity"] == "ERROR" for i in issues) else "ACCEPTED"

    badge_asin = None
    if esito == "ACCEPTED":
        client.post(f"/aplus/2020-11-01/contentDocuments/{content_reference_key}/asins",
                     json_body={"asinSet": [pacchetto.asin]}, params={"marketplaceId": marketplace_id})
        client.post(f"/aplus/2020-11-01/contentDocuments/{content_reference_key}/approvalSubmissions",
                     json_body={}, params={"marketplaceId": marketplace_id})
        rilettura = client.get(f"/aplus/2020-11-01/contentDocuments/{content_reference_key}/asins",
                                params={"marketplaceId": marketplace_id, "asinSet": pacchetto.asin,
                                        "includedDataSet": "BADGE_SET"})
        asin_metadata = next(
            (m for m in rilettura.get("asinMetadataSet", []) if m.get("asin") == pacchetto.asin), {})
        badge_set = asin_metadata.get("badgeSet") or []
        badge_asin = badge_set[0] if badge_set else None

    registro.registra_esito_aplus(cliente, indice, esito, issues)
    return EsitoAplus(content_reference_key=content_reference_key, esito=esito,
                       issues=issues, badge_asin=badge_asin)
```

- [ ] **Step 4: Esegui i test e verifica che passino**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest tests/test_aplus_applica.py -v`
Expected: PASS

- [ ] **Step 5: Esegui l'intera suite per verificare che nulla si sia rotto**

Run: `cd ~/.claude/skills/amazon-inserzioni && python3 -m pytest -v`
Expected: PASS, incluso tutto ciò che già esisteva per i listing

- [ ] **Step 6: Commit**

```bash
cd ~/.claude/skills/amazon-inserzioni
git add scripts/aplus_applica.py tests/test_aplus_applica.py
git commit -m "aplus: scrivere, collegare l'ASIN, sottomettere per approvazione"
```

---

### Task 7: SKILL.md — documentare il nuovo flusso e i limiti noti

**Files:**
- Modify: `SKILL.md`

**Interfaces:**
- Consumes: nessuna (solo documentazione)
- Produces: nessuna (nessun altro task dipende da questo)

- [ ] **Step 1: Aggiungi in fondo a `SKILL.md`** (dopo "## Regole che non si negoziano"),
  una nuova sezione:

```markdown
## Contenuti A+ (A+ Content)

Vedi `DESIGN-aplus.md` per l'architettura completa. Stesso cancello 🟡, stesso client
SP-API per cliente — nessuna credenziale nuova.

1. Bersaglio: ASIN (+ cliente, marketplace).
2. `aplus_leggi.leggi` → documento esistente (se c'è), moduli, badge di pubblicazione.
3. Contenuto nuovo: testo dettato da Mariano e/o immagini da file locale —
   `aplus_immagini.carica_immagine` per ogni immagine nuova, un'immagine alla volta.
4. `aplus_componi.componi` → pacchetto con la lista COMPLETA dei moduli desiderata
   (mai un modulo isolato aggiunto ai vecchi).
5. `aplus_diff.rendi_tabella` → anteprima modulo per modulo, prima → dopo.
6. **Un "vai" per documento.**
7. `aplus_applica.applica` → crea o aggiorna il documento, lo collega all'ASIN,
   lo sottomette per approvazione, rilegge il badge, registra.
8. **Dopo "vai" si dichiara "sottomesso per approvazione", mai "pubblicato"** — il
   badge `CONTENT_PUBLISHED` è l'unica prova che Amazon ha davvero pubblicato, e va
   verificato in un giro successivo, non subito.

**Fuori scope, non per scelta ma perché oggi non esiste un'API pubblica (verificato
2026-09-02):** Brand Story, video prodotto, pagina Store. Vanno segnalati a Mariano
come "solo modifica manuale da Seller Central" se richiesti — non è un compito
rimandato, è un limite della piattaforma.

**Niente `annulla` automatico**: risottomettere una versione precedente è una nuova
richiesta di approvazione, non un rollback istantaneo — si ripresenta a mano
consultando `registro.ultimo_lotto(cliente, "contenuti-aplus")`.
```

- [ ] **Step 2: Rileggi `SKILL.md` per intero e verifica che la nuova sezione non
  contraddica nulla di quanto già scritto sopra** (in particolare l'elenco "Fuori
  scope" non deve confliggere con l'intestazione dello YAML frontmatter, che resta
  quella originale — questa estensione non cambia il trigger della skill)

- [ ] **Step 3: Commit**

```bash
cd ~/.claude/skills/amazon-inserzioni
git add SKILL.md
git commit -m "aplus: documentare il flusso Contenuti A+ e i limiti Brand Story/Video/Store"
```

---

## Dopo il piano — collaudo dal vivo (non un task di questo piano)

Prima di dichiarare la skill pronta: una lettura di prova (`aplus_leggi.leggi`) su un
cliente/ASIN pilota per confermare che il ruolo SP-API "Product Listing"/"Brand
Analytics" sia già autorizzato (DESIGN-aplus §11), poi una singola scrittura reale con
"vai" di Mariano, verificando in un giro successivo il badge ASIN — stesso schema di
collaudo già usato per `amazon-inserzioni` sui listing (Phy to Pet, 2026-09-02).
