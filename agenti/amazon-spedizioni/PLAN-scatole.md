# Scatole dalla storia — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `giro.py scatole <cliente> [SKU=QTA ...]` legge dai piani SHIPPED del cliente il collo standard di ogni SKU, salva un'istantanea datata, scrive una bozza di `lotto.yaml` già compilata, e `prepara`/`riprendi --lotto` si fermano su una scatola che si scosta dallo storico (salvo `--scatole-nuove`).

**Architecture:** un modulo nuovo e puro nella sua parte di calcolo, `scripts/scatole.py` (lettura storico, istantanea su disco, tabella, bozza YAML, confronto), più un verbo e una guardia in `scripts/giro.py`. Nessun modulo esistente cambia interfaccia; `lotto.da_dizionario` guadagna solo un messaggio parlante sui campi mancanti. I test end-to-end girano su `tests/cliente_finto.py`, che impara a rispondere alla lista dei piani SHIPPED e alle loro scatole con la forma vera di `tests/fixtures/stadio/bedshire_trasporto_prenotato.json`.

**Tech Stack:** Python 3.9 (`python3` di sistema), pytest, PyYAML 6. Import piatti via `tests/conftest.py` (`sys.path.insert` su `scripts/`). Si lancia sempre dalla cartella della skill: `python3 -m pytest tests -q`.

**Spec:** `DESIGN.md` §13 (e §12 per `giro.py`, `lotto.yaml`, il cliente finto).

## Global Constraints

- Sola lettura verso Amazon: il verbo `scatole` non fa nessuna POST e non tocca il registro.
- Il controllo delle scatole gira **prima** di `registro.registra_tentativo`, del file locale (`giri.salva`/`giri.aggiorna`) e di qualunque chiamata ad Amazon.
- `listInboundPlans`: `status=SHIPPED`, `pageSize` **massimo 30**, tutte le pagine (`pagination.nextToken` → `paginationToken`), filtro per `createdAt` su tutte le voci (l'ordine della lista non è garantito). `/boxes`: `pageSize=100`, paginato come `etichette.leggi_cartoni`.
- Fanno standard solo scatole con `contentInformationSource == "BOX_CONTENT_PROVIDED"` e **un solo** elemento in `items`. In ogni piano il collo di riferimento di uno SKU è il mono-SKU con più `pezzi`. Lo standard è il collo di riferimento del piano più recente; le combinazioni diverse (pezzi, misure, peso) dei piani più vecchi sono varianti con `ultima_data`.
- Tolleranze del confronto: più pezzi dello standard → scostamento; stessi pezzi → misure identiche a meno dell'ordine dei lati (terne ordinate, arrotondate al decimo) e peso entro **±10 %**; meno pezzi → parziale (misure identiche, peso non confrontato).
- Il peso di un collo parziale non si stima: nella bozza resta vuoto.
- Il client si costruisce **solo** con `spapi_client.crea_client(cliente)`. Il nome del file dell'istantanea usa `registro._nome_cliente`.
- Stile del repo: docstring che spiegano il *perché*, nomi italiani, niente astrazioni oltre il necessario, testi per Mariano in italiano. Le fixture in `tests/fixtures/stadio/*.json` non si modificano.
- Ogni commit con la suite verde. Messaggi di commit in italiano, prefisso convenzionale, trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

### File Structure

- Modify `tests/cliente_finto.py` — `scatola_finta(...)` (una scatola nella forma vera di Amazon), `ClienteFinto.spedito(plan_id, creato, scatole, leggibile=True)`, `GET /inboundPlans?status=SHIPPED` paginato (max 30), `GET /inboundPlans/{id}/boxes` paginato (100) per i piani spediti, `SpApiError` sui piani illeggibili.
- Create `scripts/scatole.py` — `leggi_storico(client, cliente, adesso=None, mesi=12) -> dict`, `salva_istantanea(istantanea) -> str`, `carica_istantanea(cliente) -> dict | None`, `tabella(istantanea) -> str`, `bozza_lotto(istantanea, quantita) -> str`, `scrivi_bozza(cliente, testo, adesso) -> str`, `Confronto`, `confronta(lotto, istantanea) -> Confronto`.
- Modify `scripts/lotto.py` — `da_dizionario`: campo mancante in una scatola nominato per nome ("scatola 2: manca peso_kg").
- Modify `scripts/giro.py` — verbo `scatole(client, cliente, quantita, adesso=None, mesi=12) -> str`, `_controlla_scatole(lotto, scatole_nuove) -> str`, parametro `scatole_nuove` in `prepara` e `riprendi`, CLI `scatole` + `--scatole-nuove` + `--mesi`.
- Create `tests/test_scatole.py`; modify `tests/test_cliente_finto.py`, `tests/test_lotto.py`, `tests/test_giro.py`.
- Modify `SKILL.md` ("Come si usa", passo 3), `DESIGN.md` §12 (la riga "memoria delle scatole per SKU" rimanda a §13).

---

### Task 1: il cliente finto impara i piani spediti

**Files:**
- Modify: `tests/cliente_finto.py`
- Test: `tests/test_cliente_finto.py`

**Interfaces:**
- Produces: `scatola_finta(sku, pezzi, peso, l, w, h, quantita=1, modello="P1 - B1", asin="B0TEST", altri=())` → dict scatola nella forma di Amazon (`items`, `dimensions`, `weight`, `quantity`, `templateName`, `contentInformationSource`); `ClienteFinto.spedito(plan_id: str, creato: str, scatole: list[dict], leggibile: bool = True) -> None`; `ClienteFinto.spediti: list[dict]`, `ClienteFinto.scatole_spedite: dict[str, list[dict]]`, `ClienteFinto.illeggibili: set[str]`.
- Consumes: `spapi_client.SpApiError`.

- [ ] **Step 1: Scrivi i test che falliscono** (in coda a `tests/test_cliente_finto.py`)

```python
# tests/test_cliente_finto.py (in coda)
import pytest
from cliente_finto import ClienteFinto, scatola_finta
from spapi_client import SpApiError

_B = "/inbound/fba/2024-03-20/inboundPlans"


def test_scatola_finta_ha_la_forma_di_amazon():
    s = scatola_finta("SKU-A", 18, 7, 30, 40, 30, quantita=2, modello="P1 - B3", asin="B0G934VLJR")
    assert s["quantity"] == 2 and s["templateName"] == "P1 - B3"
    assert s["dimensions"] == {"unitOfMeasurement": "CM", "length": 30, "width": 40, "height": 30}
    assert s["weight"] == {"unit": "KG", "value": 7}
    assert s["contentInformationSource"] == "BOX_CONTENT_PROVIDED"
    assert [(i["msku"], i["quantity"], i["asin"]) for i in s["items"]] == [("SKU-A", 18, "B0G934VLJR")]


def test_scatola_finta_mista_porta_piu_righe():
    s = scatola_finta("SKU-A", 10, 5, 30, 40, 30, altri=[("SKU-B", 4)])
    assert [(i["msku"], i["quantity"]) for i in s["items"]] == [("SKU-A", 10), ("SKU-B", 4)]


def test_la_lista_dei_piani_spediti_e_paginata_a_30_e_solo_per_status_shipped():
    c = ClienteFinto()
    for i in range(31):
        c.spedito(f"wfs{i:02d}", "2026-08-01T10:00:00Z", [scatola_finta("SKU-A", 18, 7, 30, 40, 30)])
    prima = c.get(_B, params={"status": "SHIPPED", "pageSize": 30})
    assert len(prima["inboundPlans"]) == 30 and prima["pagination"]["nextToken"]
    assert prima["inboundPlans"][0]["status"] == "SHIPPED" and prima["inboundPlans"][0]["createdAt"] == "2026-08-01T10:00:00Z"
    seconda = c.get(_B, params={"status": "SHIPPED", "pageSize": 30, "paginationToken": prima["pagination"]["nextToken"]})
    assert [p["inboundPlanId"] for p in seconda["inboundPlans"]] == ["wfs30"] and seconda["pagination"] == {}
    assert c.get(_B, params={"status": "ACTIVE", "pageSize": 30}) == {"inboundPlans": [], "pagination": {}}
    with pytest.raises(AssertionError, match="30"):
        c.get(_B, params={"status": "SHIPPED", "pageSize": 100})


def test_le_scatole_di_un_piano_spedito_sono_paginate_a_100():
    c = ClienteFinto()
    c.spedito("wfs1", "2026-08-01T10:00:00Z", [scatola_finta("SKU-A", 18, 7, 30, 40, 30)] * 101)
    prima = c.get(f"{_B}/wfs1/boxes", params={"pageSize": 100})
    assert len(prima["boxes"]) == 100 and prima["pagination"]["nextToken"]
    seconda = c.get(f"{_B}/wfs1/boxes", params={"pageSize": 100, "paginationToken": prima["pagination"]["nextToken"]})
    assert len(seconda["boxes"]) == 1 and seconda["pagination"] == {}


def test_un_piano_spedito_illeggibile_alza_spapierror_sulle_scatole():
    c = ClienteFinto()
    c.spedito("wfs1", "2026-08-01T10:00:00Z", [scatola_finta("SKU-A", 18, 7, 30, 40, 30)], leggibile=False)
    with pytest.raises(SpApiError, match="403"):
        c.get(f"{_B}/wfs1/boxes", params={"pageSize": 100})


def test_i_piani_con_stato_restano_come_prima():
    """La lista spediti non deve rompere il GET del piano nudo dei piani ACTIVE."""
    c = ClienteFinto()
    c.spedito("wfs1", "2026-08-01T10:00:00Z", [scatola_finta("SKU-A", 18, 7, 30, 40, 30)])
    plan_id = c.piano_a(__import__("stadio").SCATOLE_DA_DICHIARARE)
    assert c.get(f"{_B}/{plan_id}")["status"] == "ACTIVE"
```

- [ ] **Step 2: Esegui i test per vedere che falliscono**

Run: `python3 -m pytest tests/test_cliente_finto.py -q`
Expected: FAIL con `ImportError: cannot import name 'scatola_finta'`.

- [ ] **Step 3: Implementa nel cliente finto**

In `tests/cliente_finto.py`, dopo `_B = ...`:

```python
from spapi_client import SpApiError


def scatola_finta(sku, pezzi, peso, l, w, h, quantita=1, modello="P1 - B1", asin="B0TEST", altri=()):
    """Una scatola come la restituisce `GET /inboundPlans/{id}/boxes` di un
    piano spedito (forma di tests/fixtures/stadio/bedshire_trasporto_prenotato.json,
    03/09/2026). `altri` aggiunge righe di altri SKU: una scatola mista."""
    items = [{"msku": sku, "quantity": pezzi, "asin": asin, "fnsku": "X0FINTO", "labelOwner": "NONE",
              "prepInstructions": []}]
    items += [{"msku": s, "quantity": q, "asin": "B0ALTRO", "fnsku": "X1FINTO", "labelOwner": "NONE",
               "prepInstructions": []} for s, q in altri]
    return {"quantity": quantita, "externalContainerIdentifierType": "AMAZON", "templateName": modello,
            "packageId": f"pk-{sku}-{pezzi}", "weight": {"unit": "KG", "value": peso},
            "dimensions": {"unitOfMeasurement": "CM", "length": l, "width": w, "height": h},
            "items": items, "contentInformationSource": "BOX_CONTENT_PROVIDED"}
```

In `ClienteFinto.__init__`, dopo `self._n = 0`:

```python
        self.spediti: list[dict] = []                 # listInboundPlans?status=SHIPPED
        self.scatole_spedite: dict[str, list[dict]] = {}
        self.illeggibili: set[str] = set()
```

Dopo `piano_a`:

```python
    def spedito(self, plan_id: str, creato: str, scatole: list[dict], leggibile: bool = True) -> None:
        """Un piano SHIPPED com'è nella lista di Amazon (forma vera del
        03/09/2026: niente shipments né items nella voce di lista), con le
        sue scatole. `leggibile=False` fa rispondere 403 su /boxes."""
        self.spediti.append({"inboundPlanId": plan_id, "status": "SHIPPED", "createdAt": creato,
                             "lastUpdatedAt": creato, "name": "", "marketplaceIds": ["APJ6JRA9NG5V4"],
                             "sourceAddress": {"name": "Finto", "city": "Prato", "countryCode": "IT"}})
        self.scatole_spedite[plan_id] = list(scatole)
        if not leggibile:
            self.illeggibili.add(plan_id)

    @staticmethod
    def _pagina(elenco, params, chiave, massimo):
        params = params or {}
        da = int(params.get("paginationToken") or 0)
        quanti = min(int(params.get("pageSize") or 10), massimo)
        prossimo = da + quanti
        return {chiave: elenco[da:prossimo],
                "pagination": ({"nextToken": str(prossimo)} if prossimo < len(elenco) else {})}
```

In `get`, subito dopo il ramo delle etichette (`if m: return {"payload": ...}`) e prima dell'`assert path.startswith(_B + "/")`:

```python
        if path == _B:
            assert int((params or {}).get("pageSize") or 10) <= 30, "listInboundPlans: pageSize massimo 30"
            if (params or {}).get("status") != "SHIPPED":
                return {"inboundPlans": [], "pagination": {}}
            return self._pagina(self.spediti, params, "inboundPlans", 30)
```

E dopo `pid = path[len(_B) + 1:].split("/")[0]`, prima di `p = self.piani[pid]`:

```python
        if pid in self.scatole_spedite:
            assert path == f"{_B}/{pid}/boxes", f"di un piano spedito si leggono solo le scatole: {path}"
            if pid in self.illeggibili:
                raise SpApiError(f"403 su {path}: Unauthorized")
            return self._pagina(self.scatole_spedite[pid], params, "boxes", 100)
```

- [ ] **Step 4: Esegui i test**

Run: `python3 -m pytest tests -q`
Expected: tutti PASS (292 + 6).

- [ ] **Step 5: Commit**

```bash
git add tests/cliente_finto.py tests/test_cliente_finto.py
git commit -m "test(cliente_finto): piani SHIPPED con scatole, liste paginate come Amazon"
```

---

### Task 2: `scatole.leggi_storico` e l'istantanea su disco

**Files:**
- Create: `scripts/scatole.py`
- Test: `tests/test_scatole.py`

**Interfaces:**
- Consumes: `ClienteFinto.spedito`, `scatola_finta` (Task 1); `registro._nome_cliente`; `spapi_client.SpApiError`.
- Produces: `leggi_storico(client, cliente: str, adesso: datetime | None = None, mesi: int = 12) -> dict` con chiavi `cliente`, `letto_il` (ISO UTC), `mesi`, `piani_letti`, `piani_saltati: list[str]`, `colli_misti_esclusi: int`, `sku: dict[str, dict]` — ogni SKU: `pezzi`, `lunghezza_cm`, `larghezza_cm`, `altezza_cm`, `peso_kg`, `modello`, `asin`, `ultimo_piano`, `ultima_data` (AAAA-MM-GG), `varianti: list[dict]` (stesse 5 chiavi di misura + `ultima_data`); `salva_istantanea(istantanea: dict) -> str` (percorso); `carica_istantanea(cliente: str) -> dict | None`.

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_scatole.py
from datetime import datetime, timezone
import json
import pytest
import scatole
from cliente_finto import ClienteFinto, scatola_finta

_ADESSO = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def casa(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))


def _cliente_ecm():
    """Tre piani: il più recente fa standard, quello di luglio 2025 porta la
    variante 9,4 kg di Reladol_100, quello del 2024 è fuori finestra."""
    c = ClienteFinto()
    c.spedito("wf-ago", "2026-08-12T11:55:22Z", [
        scatola_finta("Reladol_100", 60, 9.0, 30, 40, 31, quantita=2, modello="P1 - B4", asin="B0CB4DCJPZ"),
        scatola_finta("Reladol_100", 10, 2.0, 30, 40, 31),                    # resto: non è una variante
        scatola_finta("ARTINORM_2_FBA", 18, 7, 30, 40, 30, modello="P1 - B1", asin="B0G934VLJR"),
    ])
    c.spedito("wf-lug", "2025-07-28T09:00:00Z", [
        scatola_finta("Reladol_100", 60, 9.4, 30, 40, 31, modello="P1 - B4", asin="B0CB4DCJPZ"),
        scatola_finta("Reladol_100", 10, 2.0, 30, 40, 31, altri=[("ARTINORM_2_FBA", 3)]),   # mista
    ])
    c.spedito("wf-2024", "2024-05-01T09:00:00Z", [
        scatola_finta("Reladol_100", 40, 6.0, 30, 40, 20),
    ])
    return c


def test_lo_standard_e_il_collo_di_riferimento_del_piano_piu_recente():
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    r = ist["sku"]["Reladol_100"]
    assert (r["pezzi"], r["lunghezza_cm"], r["larghezza_cm"], r["altezza_cm"], r["peso_kg"]) == (60, 30, 40, 31, 9.0)
    assert (r["modello"], r["asin"], r["ultimo_piano"], r["ultima_data"]) == ("P1 - B4", "B0CB4DCJPZ", "wf-ago", "2026-08-12")
    assert ist["sku"]["ARTINORM_2_FBA"]["pezzi"] == 18
    assert ist["cliente"] == "ECM" and ist["mesi"] == 12 and ist["letto_il"] == "2026-09-02T20:00:00Z"


def test_le_varianti_vengono_dai_colli_di_riferimento_dei_piani_vecchi_con_la_loro_data():
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    assert ist["sku"]["Reladol_100"]["varianti"] == [
        {"pezzi": 60, "lunghezza_cm": 30, "larghezza_cm": 40, "altezza_cm": 31, "peso_kg": 9.4, "ultima_data": "2025-07-28"}]
    assert ist["sku"]["ARTINORM_2_FBA"]["varianti"] == []


def test_i_resti_e_i_colli_misti_non_fanno_standard_ne_variante():
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    pesi = [v["peso_kg"] for v in ist["sku"]["Reladol_100"]["varianti"]] + [ist["sku"]["Reladol_100"]["peso_kg"]]
    assert 2.0 not in pesi
    assert ist["colli_misti_esclusi"] == 1


def test_la_finestra_esclude_i_piani_vecchi_e_conta_quelli_letti():
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    assert ist["piani_letti"] == 2                       # wf-2024 fuori dai 12 mesi
    assert all(v["peso_kg"] != 6.0 for v in ist["sku"]["Reladol_100"]["varianti"])
    largo = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO, mesi=36)
    assert largo["piani_letti"] == 3 and largo["sku"]["Reladol_100"]["varianti"][-1]["pezzi"] == 40


def test_la_lista_si_legge_a_pagine_da_30_e_il_filtro_vale_su_tutte():
    """L'ordine della lista non è garantito: il piano recente sta in fondo,
    dopo 30 vecchi, e deve fare standard lo stesso."""
    c = ClienteFinto()
    for i in range(30):
        c.spedito(f"wf-vecchio{i}", "2024-01-01T00:00:00Z", [scatola_finta("SKU-A", 5, 1, 10, 10, 10)])
    c.spedito("wf-nuovo", "2026-08-01T00:00:00Z", [scatola_finta("SKU-A", 12, 3, 20, 20, 20)])
    ist = scatole.leggi_storico(c, "X", _ADESSO)
    assert ist["sku"]["SKU-A"]["pezzi"] == 12 and ist["piani_letti"] == 1
    liste = [(p, q) for p, q in c.get_fatte if p.endswith("/inboundPlans")]
    assert len(liste) == 2 and all(q["pageSize"] == 30 and q["status"] == "SHIPPED" for _, q in liste)


def test_le_scatole_si_leggono_a_pagine_da_100():
    c = ClienteFinto()
    c.spedito("wf1", "2026-08-01T00:00:00Z", [scatola_finta("SKU-A", 5, 1, 10, 10, 10)] * 100
              + [scatola_finta("SKU-B", 7, 2, 10, 10, 10)])
    ist = scatole.leggi_storico(c, "X", _ADESSO)
    assert set(ist["sku"]) == {"SKU-A", "SKU-B"}


def test_un_piano_illeggibile_viene_saltato_e_nominato():
    c = ClienteFinto()
    c.spedito("wf-ok", "2026-08-01T00:00:00Z", [scatola_finta("SKU-A", 5, 1, 10, 10, 10)])
    c.spedito("wf-403", "2026-08-15T00:00:00Z", [scatola_finta("SKU-A", 9, 9, 10, 10, 10)], leggibile=False)
    ist = scatole.leggi_storico(c, "X", _ADESSO)
    assert ist["sku"]["SKU-A"]["pezzi"] == 5 and ist["piani_letti"] == 1
    assert len(ist["piani_saltati"]) == 1 and ist["piani_saltati"][0].startswith("wf-403")


def test_senza_piani_nella_finestra_l_istantanea_e_vuota():
    c = ClienteFinto()
    ist = scatole.leggi_storico(c, "X", _ADESSO)
    assert ist["sku"] == {} and ist["piani_letti"] == 0


def test_istantanea_salvata_e_riletta_col_nome_file_del_registro(tmp_path):
    ist = scatole.leggi_storico(_cliente_ecm(), "Puraluna (Bee Your Home)", _ADESSO)
    percorso = scatole.salva_istantanea(ist)
    assert percorso == str(tmp_path / ".config/newera-amazon/spedizioni/scatole/Puraluna _Bee Your Home_.json")
    assert json.load(open(percorso))["sku"]["Reladol_100"]["pezzi"] == 60
    assert scatole.carica_istantanea("Puraluna (Bee Your Home)") == ist
    assert scatole.carica_istantanea("Mai letto") is None
```

- [ ] **Step 2: Esegui i test per vedere che falliscono**

Run: `python3 -m pytest tests/test_scatole.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'scatole'`.

- [ ] **Step 3: Scrivi `scripts/scatole.py`**

```python
# scripts/scatole.py
"""Scatole dalla storia (DESIGN §13): il collo standard di ogni SKU letto
dai piani SHIPPED del cliente, l'istantanea su disco, la tabella per Mariano,
la bozza di lotto.yaml e il confronto con un lotto dettato a mano."""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from registro import _nome_cliente
from spapi_client import SpApiError

_BASE = "/inbound/fba/2024-03-20/inboundPlans"
_MISURE = ("pezzi", "lunghezza_cm", "larghezza_cm", "altezza_cm", "peso_kg")
TOLLERANZA_PESO = 0.10


def _istante(testo: str) -> datetime:
    return datetime.fromisoformat(testo.replace("Z", "+00:00"))


def _piani_spediti(client, adesso: datetime, mesi: int) -> list[dict]:
    """`listInboundPlans?status=SHIPPED`, tutte le pagine (max 30 per pagina).
    Senza `status` Amazon torna solo gli ACTIVE, che sono bozze vuote; l'ordine
    della lista non è garantito, quindi il filtro per data vale su ogni voce."""
    soglia = adesso - timedelta(days=30 * mesi)
    piani, token = [], None
    while True:
        params = {"status": "SHIPPED", "pageSize": 30}
        if token:
            params["paginationToken"] = token
        risposta = client.get(_BASE, params=params)
        piani += [p for p in (risposta.get("inboundPlans") or [])
                  if p.get("createdAt") and _istante(p["createdAt"]) >= soglia]
        token = (risposta.get("pagination") or {}).get("nextToken")
        if not token:
            return sorted(piani, key=lambda p: p["createdAt"], reverse=True)


def _scatole_del_piano(client, plan_id: str) -> list[dict]:
    scatole, token = [], None
    while True:
        params = {"pageSize": 100}
        if token:
            params["paginationToken"] = token
        risposta = client.get(f"{_BASE}/{plan_id}/boxes", params=params)
        scatole += risposta.get("boxes") or []
        token = (risposta.get("pagination") or {}).get("nextToken")
        if not token:
            return scatole


def _riferimenti(scatole: list[dict]) -> tuple[dict[str, dict], int]:
    """Per ogni SKU il collo di riferimento del piano: il mono-SKU con più
    pezzi. I mono-SKU più piccoli dello stesso piano sono resti (130 pz =
    2×60 + 1×10), non un imballo diverso. Le scatole con più SKU si contano
    e basta: non dicono quanto pesa un collo di uno SKU solo."""
    riferimenti, misti = {}, 0
    for b in scatole:
        items = b.get("items") or []
        if b.get("contentInformationSource") != "BOX_CONTENT_PROVIDED" or not items:
            continue
        if len(items) > 1:
            misti += int(b.get("quantity") or 1)
            continue
        d, w, it = b.get("dimensions") or {}, b.get("weight") or {}, items[0]
        if not d or not w:
            continue
        collo = {"pezzi": int(it["quantity"]), "lunghezza_cm": float(d["length"]),
                 "larghezza_cm": float(d["width"]), "altezza_cm": float(d["height"]),
                 "peso_kg": float(w["value"]), "modello": b.get("templateName") or None,
                 "asin": it.get("asin")}
        if it["msku"] not in riferimenti or collo["pezzi"] > riferimenti[it["msku"]]["pezzi"]:
            riferimenti[it["msku"]] = collo
    return riferimenti, misti


def _combo(collo: dict) -> tuple:
    return tuple(collo[k] for k in _MISURE)


def leggi_storico(client, cliente: str, adesso: datetime | None = None, mesi: int = 12) -> dict:
    """L'istantanea: per ogni SKU lo standard (il collo di riferimento del
    piano più recente) e le varianti (combinazioni diverse nei piani più
    vecchi, con l'ultima data in cui sono comparse). Un piano le cui scatole
    non si leggono viene saltato e nominato: lo standard deve uscire anche
    da uno storico con buchi."""
    adesso = adesso or datetime.now(timezone.utc)
    piani = _piani_spediti(client, adesso, mesi)
    sku: dict[str, dict] = {}
    misti, saltati = 0, []
    for p in piani:
        pid = p["inboundPlanId"]
        try:
            scatole = _scatole_del_piano(client, pid)
        except SpApiError as e:
            saltati.append(f"{pid}: {e}")
            continue
        riferimenti, m = _riferimenti(scatole)
        misti += m
        data = p["createdAt"][:10]
        for s, collo in riferimenti.items():
            if s not in sku:
                sku[s] = {**collo, "ultimo_piano": pid, "ultima_data": data, "varianti": []}
                continue
            std = sku[s]
            if _combo(collo) == _combo(std) or any(_combo(collo) == _combo(v) for v in std["varianti"]):
                continue
            std["varianti"].append({**{k: collo[k] for k in _MISURE}, "ultima_data": data})
    return {"cliente": cliente, "letto_il": adesso.strftime("%Y-%m-%dT%H:%M:%SZ"), "mesi": mesi,
            "piani_letti": len(piani) - len(saltati), "piani_saltati": saltati,
            "colli_misti_esclusi": misti, "sku": sku}


def _percorso_istantanea(cliente: str) -> str:
    return os.path.expanduser(
        f"~/.config/newera-amazon/spedizioni/scatole/{_nome_cliente(cliente)}.json")


def salva_istantanea(istantanea: dict) -> str:
    percorso = _percorso_istantanea(istantanea["cliente"])
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    temporaneo = f"{percorso}.tmp"
    with open(temporaneo, "w") as f:
        f.write(json.dumps(istantanea, indent=2, ensure_ascii=False))
    os.replace(temporaneo, percorso)
    return percorso


def carica_istantanea(cliente: str) -> dict | None:
    percorso = _percorso_istantanea(cliente)
    if not os.path.isfile(percorso):
        return None
    with open(percorso) as f:
        return json.load(f)
```

- [ ] **Step 4: Esegui i test**

Run: `python3 -m pytest tests -q`
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/scatole.py tests/test_scatole.py
git commit -m "feat(scatole): lo standard per SKU dai piani SHIPPED, con varianti e istantanea su disco"
```

---

### Task 3: tabella e bozza di `lotto.yaml`

**Files:**
- Modify: `scripts/scatole.py`
- Modify: `scripts/lotto.py` (`da_dizionario`, ciclo delle scatole)
- Test: `tests/test_scatole.py`, `tests/test_lotto.py`

**Interfaces:**
- Consumes: l'istantanea di Task 2; `lotto.carica`.
- Produces: `tabella(istantanea: dict) -> str`; `bozza_lotto(istantanea: dict, quantita: dict[str, int]) -> str` (testo YAML, `ValueError` su SKU senza storico); `scrivi_bozza(cliente: str, testo: str, adesso: datetime) -> str` (percorso `~/Desktop/Lotto-<cliente>-<AAAA-MM-GG>.yaml`, mai sovrascrive: `-2`, `-3`...).

- [ ] **Step 1: Scrivi i test che falliscono** (in coda a `tests/test_scatole.py` e a `tests/test_lotto.py`)

```python
# tests/test_scatole.py (in coda)
import yaml
import lotto as lotto_mod


def test_la_tabella_dice_standard_varianti_misti_e_piani_letti():
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    t = scatole.tabella(ist)
    assert "ECM" in t and "2 piani" in t and "12 mesi" in t
    assert "Reladol_100 (B0CB4DCJPZ): 60 pz/collo, 30×40×31 cm, 9 kg, modello P1 - B4, ultimo piano 2026-08-12" in t
    assert "9.4 kg fino al 2025-07-28" in t
    assert "1 colli misti esclusi" in t


def test_la_tabella_vuota_suggerisce_di_allargare_la_finestra():
    ist = scatole.leggi_storico(ClienteFinto(), "X", _ADESSO)
    assert "--mesi" in scatole.tabella(ist)


def test_la_tabella_nomina_i_piani_saltati():
    c = ClienteFinto()
    c.spedito("wf-403", "2026-08-15T00:00:00Z", [scatola_finta("SKU-A", 9, 9, 10, 10, 10)], leggibile=False)
    assert "wf-403" in scatole.tabella(scatole.leggi_storico(c, "X", _ADESSO))


def test_la_bozza_e_yaml_valido_con_colli_pieni_e_un_parziale_da_pesare():
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    testo = scatole.bozza_lotto(ist, {"Reladol_100": 130, "ARTINORM_2_FBA": 18})
    dati = yaml.safe_load(testo)
    assert dati["cliente"] == "ECM"
    assert dati["righe"] == [{"sku": "Reladol_100", "quantita": 130}, {"sku": "ARTINORM_2_FBA", "quantita": 18}]
    assert dati["scatole"][0] == {"quantita": 2, "peso_kg": 9.0, "lunghezza_cm": 30, "larghezza_cm": 40,
                                  "altezza_cm": 31, "righe": [{"sku": "Reladol_100", "quantita": 60}]}
    parziale = dati["scatole"][1]
    assert parziale["quantita"] == 1 and parziale["peso_kg"] is None
    assert parziale["righe"] == [{"sku": "Reladol_100", "quantita": 10}]
    assert dati["scatole"][2]["righe"] == [{"sku": "ARTINORM_2_FBA", "quantita": 18}]
    assert dati["data_pronta"] is None
    assert "da pesare" in testo and "standard: P1 - B4" in testo and "2026-09-08T08:00Z" in testo


def test_la_bozza_senza_resto_non_ha_il_collo_parziale():
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    dati = yaml.safe_load(scatole.bozza_lotto(ist, {"Reladol_100": 120}))
    assert len(dati["scatole"]) == 1 and dati["scatole"][0]["quantita"] == 2


def test_la_bozza_incompleta_e_rifiutata_da_lotto_carica_finche_non_e_completa(tmp_path):
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    f = tmp_path / "bozza.yaml"
    f.write_text(scatole.bozza_lotto(ist, {"Reladol_100": 130}))
    with pytest.raises(ValueError, match="scatola 2: manca peso_kg"):
        lotto_mod.carica(str(f))
    f.write_text(f.read_text().replace("peso_kg: ,", "peso_kg: 1.8,"))
    with pytest.raises(ValueError, match="data_pronta"):
        lotto_mod.carica(str(f))
    f.write_text(f.read_text().replace("data_pronta:", "data_pronta: 2026-09-08T08:00Z"))
    l = lotto_mod.carica(str(f))
    assert sum(s.quantita * s.righe[0].quantita for s in l.scatole) == 130


def test_uno_sku_senza_storico_ferma_la_bozza_e_nomina_quelli_noti():
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    with pytest.raises(ValueError, match=r"nessuno storico per NUOVO_SKU.*ARTINORM_2_FBA, Reladol_100"):
        scatole.bozza_lotto(ist, {"NUOVO_SKU": 5, "Reladol_100": 60})


def test_scrivi_bozza_sul_desktop_senza_sovrascrivere(tmp_path):
    p1 = scatole.scrivi_bozza("ECM", "cliente: ECM\n", _ADESSO)
    p2 = scatole.scrivi_bozza("ECM", "cliente: ECM\n", _ADESSO)
    assert p1 == str(tmp_path / "Desktop/Lotto-ECM-2026-09-02.yaml")
    assert p2 == str(tmp_path / "Desktop/Lotto-ECM-2026-09-02-2.yaml")
    assert open(p1).read() == "cliente: ECM\n"
```

```python
# tests/test_lotto.py (in coda)
def test_un_campo_mancante_in_una_scatola_viene_nominato():
    """La bozza di `scatole.bozza_lotto` lascia vuoto il peso del collo
    parziale: l'errore deve dire quale campo manca, non `float() argument`."""
    dati = __import__("yaml").safe_load(_YAML)
    dati["scatole"][0]["peso_kg"] = None
    with pytest.raises(ValueError, match="scatola 1: manca peso_kg"):
        lotto.da_dizionario(dati)
```

- [ ] **Step 2: Esegui i test per vedere che falliscono**

Run: `python3 -m pytest tests/test_scatole.py tests/test_lotto.py -q`
Expected: FAIL — `AttributeError: module 'scatole' has no attribute 'tabella'` e, per il test di lotto, `Failed: DID NOT RAISE` oppure il messaggio `float() argument`.

- [ ] **Step 3: Implementa**

In `scripts/lotto.py`, dentro il ciclo `for i, s in enumerate(dati.get("scatole") or [], start=1):`, come prima riga del `try`:

```python
            mancanti = [c for c in ("quantita", "peso_kg", "lunghezza_cm", "larghezza_cm", "altezza_cm")
                        if s.get(c) in (None, "")]
            if mancanti:
                raise ValueError(f"manca {', '.join(mancanti)}")
```

(L'`except` esistente lo riavvolge in `scatola {i}: manca peso_kg`.)

In coda a `scripts/scatole.py`:

```python
def _misure(c: dict) -> str:
    return f"{c['lunghezza_cm']:g}×{c['larghezza_cm']:g}×{c['altezza_cm']:g}"


def tabella(istantanea: dict) -> str:
    """La tabella dei colli standard, nella forma della scheda cliente in
    ~/nova/CLIENTI/: SKU, ASIN, pezzi per collo, misure, peso, modello,
    ultimo piano, e le varianti viste prima."""
    righe = [f"Colli standard di {istantanea['cliente']} — letti {istantanea['piani_letti']} piani "
             f"SHIPPED negli ultimi {istantanea['mesi']} mesi (istantanea del {istantanea['letto_il'][:10]})"]
    if not istantanea["sku"]:
        righe.append("  nessun collo mono-SKU trovato: prova con --mesi più alto")
    for s, c in sorted(istantanea["sku"].items()):
        varianti = "; ".join(f"{v['pezzi']} pz {_misure(v)} cm {v['peso_kg']:g} kg fino al {v['ultima_data']}"
                             for v in c["varianti"])
        righe.append(f"  {s} ({c['asin'] or 'ASIN ?'}): {c['pezzi']} pz/collo, {_misure(c)} cm, "
                     f"{c['peso_kg']:g} kg, modello {c['modello'] or '—'}, ultimo piano {c['ultima_data']}"
                     + (f" | prima: {varianti}" if varianti else ""))
    if istantanea["colli_misti_esclusi"]:
        righe.append(f"  {istantanea['colli_misti_esclusi']} colli misti esclusi dallo standard")
    righe += [f"  piano non letto: {s}" for s in istantanea["piani_saltati"]]
    return "\n".join(righe)


def bozza_lotto(istantanea: dict, quantita: dict[str, int]) -> str:
    """Il lotto.yaml già compilato, come TESTO: yaml.safe_dump non scrive
    commenti, e qui i commenti sono il punto (da dove viene ogni collo, cosa
    manca). Colli pieni dallo standard; il resto in un collo parziale con le
    misure dello standard e il peso vuoto — la tara non è proporzionale, lo
    pesa chi imballa. `lotto.carica` rifiuta la bozza finché non è completa."""
    ignoti = sorted(set(quantita) - set(istantanea["sku"]))
    if ignoti:
        raise ValueError(
            f"nessuno storico per {', '.join(ignoti)}: SKU noti "
            f"{', '.join(sorted(istantanea['sku'])) or 'nessuno'}. La scatola di un articolo "
            "mai spedito la detti tu nel lotto.yaml")
    righe = [f"cliente: {istantanea['cliente']}", "righe:"]
    righe += [f"  - {{sku: {s}, quantita: {q}}}" for s, q in quantita.items()]
    righe.append("scatole:")
    for s, q in quantita.items():
        c = istantanea["sku"][s]
        pieni, resto = divmod(q, c["pezzi"])
        misure = (f"lunghezza_cm: {c['lunghezza_cm']:g}, larghezza_cm: {c['larghezza_cm']:g}, "
                  f"altezza_cm: {c['altezza_cm']:g}")
        if pieni:
            righe.append(f"  - {{quantita: {pieni}, peso_kg: {c['peso_kg']:g}, {misure},")
            righe.append(f"     righe: [{{sku: {s}, quantita: {c['pezzi']}}}]}}"
                         f"   # standard: {c['modello'] or 'senza modello'}, ultimo piano {c['ultima_data']}")
        if resto:
            righe.append(f"  - {{quantita: 1, peso_kg: , {misure},")
            righe.append(f"     righe: [{{sku: {s}, quantita: {resto}}}]}}   # parziale: {resto} pz, da pesare")
    righe.append("data_pronta:   # istante UTC in cui la merce è pronta al ritiro, es. 2026-09-08T08:00Z")
    return "\n".join(righe) + "\n"


def scrivi_bozza(cliente: str, testo: str, adesso: datetime) -> str:
    """Sul Desktop come ogni consegna; mai sopra una bozza dello stesso giorno,
    che Mariano potrebbe aver già completato a mano."""
    base = os.path.expanduser(f"~/Desktop/Lotto-{cliente}-{adesso:%Y-%m-%d}")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    percorso, n = f"{base}.yaml", 1
    while os.path.exists(percorso):
        n += 1
        percorso = f"{base}-{n}.yaml"
    with open(percorso, "w") as f:
        f.write(testo)
    return percorso
```

- [ ] **Step 4: Esegui i test**

Run: `python3 -m pytest tests -q`
Expected: tutti PASS. Se `yaml.safe_load` rifiuta `peso_kg: ,` dentro la mappa a flusso, sostituire con `peso_kg: null,` sia in `bozza_lotto` sia nel test (`replace("peso_kg: null,", ...)`): il valore letto resta `None`.

- [ ] **Step 5: Commit**

```bash
git add scripts/scatole.py scripts/lotto.py tests/test_scatole.py tests/test_lotto.py
git commit -m "feat(scatole): tabella dei colli standard e bozza di lotto.yaml con i parziali da pesare"
```

---

### Task 4: `scatole.confronta`

**Files:**
- Modify: `scripts/scatole.py`
- Test: `tests/test_scatole.py`

**Interfaces:**
- Consumes: `lotto.Lotto` (`scatole: list[modello.Scatola]`, ogni `Scatola` ha `quantita`, `peso_kg`, `lunghezza_cm`, `larghezza_cm`, `altezza_cm`, `righe: list[RigaSpedizione(sku, quantita)]`); l'istantanea.
- Produces: `@dataclass Confronto(scostamenti: list[str], note: list[str])`; `confronta(lotto, istantanea: dict) -> Confronto`.

- [ ] **Step 1: Scrivi i test che falliscono** (in coda a `tests/test_scatole.py`)

```python
# tests/test_scatole.py (in coda)
def _lotto(scatole):
    """Un lotto le cui righe sono esattamente il contenuto delle scatole."""
    totali = {}
    for b in scatole:
        for r in b["righe"]:
            totali[r["sku"]] = totali.get(r["sku"], 0) + b["quantita"] * r["quantita"]
    return lotto_mod.da_dizionario({"cliente": "ECM", "righe": [{"sku": s, "quantita": q} for s, q in totali.items()],
                                    "scatole": scatole, "data_pronta": "2026-09-08T08:00Z"})


def _scatola(sku, pezzi, peso, l, w, h, quantita=1, altri=()):
    return {"quantita": quantita, "peso_kg": peso, "lunghezza_cm": l, "larghezza_cm": w, "altezza_cm": h,
            "righe": [{"sku": sku, "quantita": pezzi}] + [{"sku": s, "quantita": q} for s, q in altri]}


@pytest.fixture
def istantanea():
    return scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)     # Reladol_100: 60 pz 30×40×31 9 kg


def test_una_scatola_uguale_allo_standard_non_da_scostamenti(istantanea):
    esito = scatole.confronta(_lotto([_scatola("Reladol_100", 60, 9.0, 30, 40, 31, quantita=2)]), istantanea)
    assert esito.scostamenti == [] and esito.note == []


def test_le_misure_si_confrontano_a_prescindere_dall_ordine_dei_lati(istantanea):
    esito = scatole.confronta(_lotto([_scatola("Reladol_100", 60, 9.0, 40, 31, 30)]), istantanea)
    assert esito.scostamenti == []


def test_il_peso_entro_il_dieci_per_cento_passa_oltre_no(istantanea):
    assert scatole.confronta(_lotto([_scatola("Reladol_100", 60, 9.4, 30, 40, 31)]), istantanea).scostamenti == []
    esito = scatole.confronta(_lotto([_scatola("Reladol_100", 60, 2.22, 30, 40, 31)]), istantanea)
    assert len(esito.scostamenti) == 1
    assert "scatola 1 (Reladol_100): 2.22 kg dichiarati" in esito.scostamenti[0]
    assert "9 kg" in esito.scostamenti[0] and "wf-ago" in esito.scostamenti[0] and "2026-08-12" in esito.scostamenti[0]


def test_misure_diverse_sono_uno_scostamento(istantanea):
    esito = scatole.confronta(_lotto([_scatola("Reladol_100", 60, 9.0, 30, 40, 20)]), istantanea)
    assert len(esito.scostamenti) == 1 and "misure 30×40×20" in esito.scostamenti[0] and "30×40×31" in esito.scostamenti[0]


def test_piu_pezzi_dello_standard_sono_uno_scostamento(istantanea):
    esito = scatole.confronta(_lotto([_scatola("Reladol_100", 72, 9.0, 30, 40, 31)]), istantanea)
    assert len(esito.scostamenti) == 1 and "72 pz per collo" in esito.scostamenti[0] and "60 pz" in esito.scostamenti[0]


def test_meno_pezzi_e_un_parziale_con_le_misure_dello_standard_e_il_peso_libero(istantanea):
    esito = scatole.confronta(_lotto([_scatola("Reladol_100", 10, 1.8, 30, 40, 31)]), istantanea)
    assert esito.scostamenti == [] and esito.note == ["scatola 1 (Reladol_100): collo parziale da 10 pz, peso non confrontato"]
    esito = scatole.confronta(_lotto([_scatola("Reladol_100", 10, 1.8, 20, 20, 20)]), istantanea)
    assert len(esito.scostamenti) == 1 and "misure" in esito.scostamenti[0]


def test_scatole_miste_e_sku_senza_storico_sono_note_non_scostamenti(istantanea):
    esito = scatole.confronta(_lotto([_scatola("Reladol_100", 10, 3, 30, 40, 31, altri=[("ARTINORM_2_FBA", 2)]),
                                      _scatola("NUOVO", 5, 1, 10, 10, 10)]), istantanea)
    assert esito.scostamenti == []
    assert esito.note == ["scatola 1: mista (Reladol_100, ARTINORM_2_FBA), non confrontata",
                          "scatola 2 (NUOVO): nessuno storico, non confrontata"]
```

- [ ] **Step 2: Esegui i test per vedere che falliscono**

Run: `python3 -m pytest tests/test_scatole.py -q -k "confront or scostament or parziale or lati or pezzi or miste"`
Expected: FAIL con `AttributeError: module 'scatole' has no attribute 'confronta'`.

- [ ] **Step 3: Implementa** (in coda a `scripts/scatole.py`)

```python
@dataclass
class Confronto:
    """Scostamenti fermano `prepara`; le note si stampano e basta."""
    scostamenti: list[str] = field(default_factory=list)
    note: list[str] = field(default_factory=list)


def _lati(l, w, h) -> tuple:
    return tuple(sorted(round(float(x), 1) for x in (l, w, h)))


def confronta(lotto, istantanea: dict) -> Confronto:
    """DESIGN §13: per ogni scatola mono-SKU del lotto, contro lo standard del
    suo SKU. Più pezzi dello standard: scostamento. Stessi pezzi: misure
    identiche (a meno dell'ordine dei lati) e peso entro ±10 %. Meno pezzi:
    collo parziale, come quello della bozza — misure identiche, peso non
    confrontato perché la tara non è proporzionale."""
    esito = Confronto()
    for i, s in enumerate(lotto.scatole, start=1):
        if len(s.righe) != 1:
            esito.note.append(f"scatola {i}: mista ({', '.join(r.sku for r in s.righe)}), non confrontata")
            continue
        r = s.righe[0]
        std = istantanea["sku"].get(r.sku)
        if std is None:
            esito.note.append(f"scatola {i} ({r.sku}): nessuno storico, non confrontata")
            continue
        storico = (f"lo storico dice {std['pezzi']} pz {_misure(std)} cm {std['peso_kg']:g} kg "
                   f"(piano {std['ultimo_piano']}, {std['ultima_data']})")
        if r.quantita > std["pezzi"]:
            esito.scostamenti.append(f"scatola {i} ({r.sku}): {r.quantita} pz per collo, {storico}")
        elif _lati(s.lunghezza_cm, s.larghezza_cm, s.altezza_cm) != _lati(std["lunghezza_cm"], std["larghezza_cm"], std["altezza_cm"]):
            esito.scostamenti.append(
                f"scatola {i} ({r.sku}): misure {s.lunghezza_cm:g}×{s.larghezza_cm:g}×{s.altezza_cm:g} cm, {storico}")
        elif r.quantita < std["pezzi"]:
            esito.note.append(f"scatola {i} ({r.sku}): collo parziale da {r.quantita} pz, peso non confrontato")
        elif abs(s.peso_kg - std["peso_kg"]) > TOLLERANZA_PESO * std["peso_kg"]:
            esito.scostamenti.append(f"scatola {i} ({r.sku}): {s.peso_kg:g} kg dichiarati, {storico}")
    return esito
```

- [ ] **Step 4: Esegui i test**

Run: `python3 -m pytest tests -q`
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/scatole.py tests/test_scatole.py
git commit -m "feat(scatole): confronto di un lotto con lo storico — pezzi, misure, peso entro il 10 %, parziali"
```

---

### Task 5: il verbo `giro.py scatole`

**Files:**
- Modify: `scripts/giro.py` (import, funzione `scatole`, `_quantita`, CLI)
- Test: `tests/test_giro.py`

**Interfaces:**
- Consumes: `scatole.leggi_storico`, `salva_istantanea`, `tabella`, `bozza_lotto`, `scrivi_bozza`; `giro._coppie`.
- Produces: `giro.scatole(client, cliente: str, quantita: dict[str, int], adesso: datetime | None = None, mesi: int = 12) -> str`; CLI `giro.py scatole <cliente> [SKU=QTA ...] [--mesi N]`.

- [ ] **Step 1: Scrivi i test che falliscono** (in coda a `tests/test_giro.py`)

```python
# tests/test_giro.py (in coda)
import scatole as scatole_mod
from cliente_finto import scatola_finta


def _spedito_ecm(c):
    c.spedito("wf-ago", "2026-08-12T11:55:22Z", [
        scatola_finta("Reladol_100", 60, 9.0, 30, 40, 31, quantita=2, modello="P1 - B4", asin="B0CB4DCJPZ")])
    return c


def test_scatole_stampa_la_tabella_e_salva_l_istantanea(tmp_path):
    c = _spedito_ecm(ClienteFinto())
    testo = giro.scatole(c, "ECM", {}, adesso=_ADESSO)
    assert "Reladol_100 (B0CB4DCJPZ): 60 pz/collo" in testo
    assert "Istantanea: " in testo and scatole_mod.carica_istantanea("ECM")["sku"]["Reladol_100"]["pezzi"] == 60
    assert c.post_fatte == []
    assert not (tmp_path / "Desktop").exists()                 # senza quantità niente bozza


def test_scatole_con_le_quantita_scrive_la_bozza_sul_desktop(tmp_path):
    c = _spedito_ecm(ClienteFinto())
    testo = giro.scatole(c, "ECM", {"Reladol_100": 130}, adesso=_ADESSO)
    percorso = tmp_path / "Desktop/Lotto-ECM-2026-09-02.yaml"
    assert f"Bozza del lotto: {percorso}" in testo and "da pesare" in testo
    assert "quantita: 130" in percorso.read_text()


def test_cli_scatole(monkeypatch, capsys, tmp_path):
    c = _spedito_ecm(ClienteFinto())
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: c)
    assert giro.main(["scatole", "ECM", "Reladol_100=130", "--mesi", "6"]) == 0
    out = capsys.readouterr().out
    assert "60 pz/collo" in out and "6 mesi" in out and "Lotto-ECM-2026" in out
    assert giro.main(["scatole", "ECM", "Reladol_100=tanti"]) == 1
    assert "quantità" in capsys.readouterr().err
```

- [ ] **Step 2: Esegui i test per vedere che falliscono**

Run: `python3 -m pytest tests/test_giro.py -q -k scatole`
Expected: FAIL con `AttributeError: module 'giro' has no attribute 'scatole'`.

- [ ] **Step 3: Implementa** in `scripts/giro.py`

Import, accanto a `import registro`:

```python
import scatole as scatole_mod
```

Prima della sezione `# --- CLI ---`:

```python
# --------------------------------------------------------------- scatole ----

def scatole(client, cliente: str, quantita: dict[str, int], adesso: datetime | None = None,
            mesi: int = 12) -> str:
    """DESIGN §13: i colli standard del cliente dai piani spediti, l'istantanea
    per il controllo di `prepara`, e con le quantità la bozza del lotto. Sola
    lettura: nessun registro, nessuna POST."""
    adesso = adesso or datetime.now(timezone.utc)
    istantanea = scatole_mod.leggi_storico(client, cliente, adesso, mesi)
    percorso = scatole_mod.salva_istantanea(istantanea)
    testo = scatole_mod.tabella(istantanea) + f"\nIstantanea: {percorso}"
    if quantita:
        bozza = scatole_mod.scrivi_bozza(cliente, scatole_mod.bozza_lotto(istantanea, quantita), adesso)
        testo += (f"\nBozza del lotto: {bozza} — completa il peso dei colli parziali (da pesare) e "
                  f"data_pronta, poi giro.py prepara")
    return testo
```

Accanto a `_coppie`:

```python
def _quantita(valori: list[str] | None) -> dict[str, int]:
    quantita = {}
    for sku, q in _coppie(valori, "quantita").items():
        try:
            quantita[sku] = int(q)
        except ValueError:
            raise ValueError(f"{sku}={q}: {q!r} non è una quantità") from None
        if quantita[sku] < 1:
            raise ValueError(f"{sku}={q}: la quantità deve essere almeno 1")
    return quantita
```

In `main`, dopo il parser `d` (asn):

```python
    e = sub.add_parser("scatole", help="colli standard per SKU dai piani spediti; con SKU=QTA scrive la bozza del lotto")
    e.add_argument("cliente")
    e.add_argument("quantita", nargs="*", metavar="SKU=QTA")
    e.add_argument("--mesi", type=int, default=12, help="quanti mesi di piani spediti leggere (12)")
```

Nel `try` di `main`, subito dopo il ramo `prepara` e prima di `locale = giri.carica(args.plan_id)`:

```python
        if args.verbo == "scatole":
            print(scatole(spapi_client.crea_client(args.cliente), args.cliente, _quantita(args.quantita),
                          mesi=args.mesi))
            return 0
```

`_coppie` si aspetta `SHIPMENT_ID=ID`: il messaggio d'errore per `--quantita` dirà `--quantita vuole SHIPMENT_ID=ID` — accettabile, ma se si preferisce, in `_coppie` sostituire `SHIPMENT_ID=ID` con `CHIAVE=VALORE`.

- [ ] **Step 4: Esegui i test**

Run: `python3 -m pytest tests -q`
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/giro.py tests/test_giro.py
git commit -m "feat(giro): verbo scatole — colli standard dallo storico, istantanea e bozza del lotto"
```

---

### Task 6: il controllo in `prepara` e `riprendi --lotto`, `--scatole-nuove`, documenti

**Files:**
- Modify: `scripts/giro.py` (`prepara`, `riprendi`, `_controlla_scatole`, CLI)
- Modify: `SKILL.md`, `DESIGN.md` §12
- Test: `tests/test_giro.py`

**Interfaces:**
- Consumes: `scatole.carica_istantanea`, `scatole.confronta`, `Confronto`.
- Produces: `giro._controlla_scatole(lotto, scatole_nuove: bool) -> str` (testo da anteporre all'output; `ValueError` sugli scostamenti senza flag); `prepara(client, lotto, adesso=None, scatole_nuove=False)`; `riprendi(client, plan_id, lotto=None, packing_option_id=None, verificato=False, adesso=None, scatole_nuove=False)`; CLI `--scatole-nuove` su `prepara` e `riprendi`.

- [ ] **Step 1: Scrivi i test che falliscono** (in coda a `tests/test_giro.py`)

```python
# tests/test_giro.py (in coda)
def _istantanea_phytopet(peso=12.0, pezzi=30):
    """Lo standard di SKU-1 nel formato di scatole.leggi_storico; _lotto()
    dichiara 30 pz 40×30×25 8 kg."""
    scatole_mod.salva_istantanea({
        "cliente": "Phy to Pet", "letto_il": "2026-09-01T10:00:00Z", "mesi": 12, "piani_letti": 1,
        "piani_saltati": [], "colli_misti_esclusi": 0,
        "sku": {"SKU-1": {"pezzi": pezzi, "lunghezza_cm": 40, "larghezza_cm": 30, "altezza_cm": 25,
                          "peso_kg": peso, "modello": "P1 - B1", "asin": "B0TEST",
                          "ultimo_piano": "wf-old", "ultima_data": "2026-07-01", "varianti": []}}})


def test_prepara_si_ferma_su_uno_scostamento_senza_scrivere_niente():
    c = ClienteFinto()
    _istantanea_phytopet(peso=12.0)                                      # 8 kg dichiarati contro 12: -33 %
    with pytest.raises(ValueError, match=r"(?s)scatole diverse dallo storico.*SKU-1.*8 kg dichiarati.*12 kg.*--scatole-nuove"):
        giro.prepara(c, _lotto(), adesso=_ADESSO)
    assert c.post_fatte == [] and c.get_fatte == []
    assert registro.ultimo_lotto("Phy to Pet") == []
    assert giri.carica("wf1") is None


def test_prepara_con_scatole_nuove_procede_e_stampa_il_confronto():
    c = ClienteFinto()
    _istantanea_phytopet(peso=12.0)
    plan_id, testo = giro.prepara(c, _lotto(), adesso=_ADESSO, scatole_nuove=True)
    assert plan_id == "wf1" and "8 kg dichiarati" in testo and "UPS" in testo


def test_prepara_con_scatole_come_lo_storico_lo_dice():
    c = ClienteFinto()
    _istantanea_phytopet(peso=8.0)
    _, testo = giro.prepara(c, _lotto(), adesso=_ADESSO)
    assert "come nello storico del 2026-09-01" in testo


def test_prepara_senza_istantanea_lo_dice_e_procede():
    c = ClienteFinto()
    _, testo = giro.prepara(c, _lotto(), adesso=_ADESSO)
    assert "nessuno storico letto per Phy to Pet" in testo and "giro.py scatole" in testo and "UPS" in testo


def test_riprendi_con_lotto_passa_dal_controllo_prima_di_scrivere_il_file():
    c = ClienteFinto()
    _istantanea_phytopet(peso=12.0)
    plan_id = c.piano_a(stadio.SCATOLE_DA_DICHIARARE)
    with pytest.raises(ValueError, match="scatole diverse"):
        giro.riprendi(c, plan_id, lotto=_lotto(), adesso=_ADESSO)
    assert giri.carica(plan_id) is None and registro.ultimo_lotto("Phy to Pet") == []
    testo = giro.riprendi(c, plan_id, lotto=_lotto(), adesso=_ADESSO, scatole_nuove=True)
    assert "8 kg dichiarati" in testo and giri.carica(plan_id)["lotto"]["scatole"][0]["peso_kg"] == 8


def test_cli_scatole_nuove(monkeypatch, tmp_path):
    import yaml
    chiamate = {}
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: ClienteFinto())
    monkeypatch.setattr(giro, "prepara", lambda client, lotto, **k: chiamate.update(k) or ("wf1", "ok"))
    f = tmp_path / "lotto.yaml"
    f.write_text(yaml.safe_dump(lotto_mod.a_dizionario(_lotto())))
    assert giro.main(["prepara", str(f), "--scatole-nuove"]) == 0
    assert chiamate["scatole_nuove"] is True
    monkeypatch.setattr(giro, "riprendi", lambda client, plan_id, **k: chiamate.update(k) or "ok")
    assert giro.main(["riprendi", "wf1", "--lotto", str(f), "--scatole-nuove"]) == 0
    assert chiamate["scatole_nuove"] is True
```

- [ ] **Step 2: Esegui i test per vedere che falliscono**

Run: `python3 -m pytest tests/test_giro.py -q -k "scostamento or scatole_nuove or come_lo_storico or senza_istantanea or passa_dal_controllo"`
Expected: FAIL — `DID NOT RAISE`, `TypeError: prepara() got an unexpected keyword argument 'scatole_nuove'`, e per il CLI `SystemExit: 2`.

- [ ] **Step 3: Implementa** in `scripts/giro.py`

Dopo `_nuovo_locale`:

```python
def _controlla_scatole(lotto: lotto_mod.Lotto, scatole_nuove: bool) -> str:
    """DESIGN §13: PRIMA di registrare il tentativo, del file locale e di ogni
    chiamata ad Amazon, perché una scatola dichiarata non si corregge — resta
    una bozza da abbandonare. Senza istantanea si procede: il controllo è un
    aiuto, non un prerequisito, e un cliente nuovo non ha storia."""
    istantanea = scatole_mod.carica_istantanea(lotto.cliente)
    if istantanea is None:
        return (f"nessuno storico letto per {lotto.cliente}: `giro.py scatole \"{lotto.cliente}\"` "
                "per averlo. Scatole non controllate.\n")
    esito = scatole_mod.confronta(lotto, istantanea)
    if esito.scostamenti and not scatole_nuove:
        raise ValueError(
            "scatole diverse dallo storico del cliente:\n  " + "\n  ".join(esito.scostamenti)
            + "\nSe l'imballo è cambiato davvero, ripeti con --scatole-nuove")
    righe = esito.scostamenti + esito.note
    if not righe:
        return f"Scatole come nello storico del {istantanea['letto_il'][:10]}.\n"
    return (f"Scatole confrontate con lo storico del {istantanea['letto_il'][:10]}:\n  "
            + "\n  ".join(righe) + "\n")
```

`prepara`:

```python
def prepara(client, lotto: lotto_mod.Lotto, adesso: datetime | None = None,
            scatole_nuove: bool = False) -> tuple[str, str]:
    """Passi 2-10 di SKILL.md: dal lotto al menù. Le scatole si controllano
    contro lo storico prima di tutto; il lotto si registra PRIMA di creare il
    piano; il file locale si scrive appena Amazon torna il plan_id, così anche
    un crash subito dopo lascia un piano riprendibile."""
    nota = _controlla_scatole(lotto, scatole_nuove)
    cfg = clienti.carica_cliente(lotto.cliente)
    ...                                            # invariato fino al return
    return plan_id, nota + riprendi(client, plan_id, adesso=adesso)
```

`riprendi`: firma con `scatole_nuove: bool = False` in coda; nel ramo `locale is None`, prima di `riferimento = registro.registra_tentativo(...)`: `avviso = _controlla_scatole(lotto, scatole_nuove)`; nel ramo `else`, dopo `_controlla_blocco` e prima di `giri.aggiorna(...)` dentro `if lotto is not None:`: `avviso = _controlla_scatole(lotto, scatole_nuove)` e poi `avviso += (f"Lotto aggiornato dal file: ...")` (l'attuale assegnazione diventa `+=`).

CLI, in `main`: `a.add_argument("--scatole-nuove", action="store_true", help="accetta scatole diverse dallo storico del cliente (imballo cambiato)")` e lo stesso su `b`; nel ramo `prepara`: `prepara(spapi_client.crea_client(lotto.cliente), lotto, scatole_nuove=args.scatole_nuove)`; nel ramo `riprendi`: aggiungere `scatole_nuove=args.scatole_nuove`.

- [ ] **Step 4: Esegui i test**

Run: `python3 -m pytest tests -q`
Expected: tutti PASS. I test esistenti di `prepara` vedono in testa la riga "nessuno storico letto…" (HOME finto, nessuna istantanea): le loro asserzioni usano `in`, restano verdi.

- [ ] **Step 5: Documenti**

`SKILL.md`, blocco "Come si usa": aggiungere prima di `prepara`:

```
    python3 giro.py scatole <cliente> [SKU=QTA ...]         # colli standard dallo storico + bozza lotto, lettura
```

e `[--scatole-nuove]` in coda alle righe di `prepara` e `riprendi`. Dopo il blocco, un paragrafo:

```
`scatole` legge dai piani SHIPPED del cliente il collo standard di ogni SKU (pezzi, misure,
peso, modello Amazon), salva un'istantanea datata e, con le quantità, scrive sul Desktop
`Lotto-<cliente>-<data>.yaml` già compilato: colli pieni dallo storico, il resto in un collo
parziale col peso vuoto da pesare, `data_pronta` vuota. `prepara` e `riprendi --lotto`
confrontano ogni scatola mono-SKU con l'istantanea (pezzi, misure, peso entro ±10 %) e si
fermano sullo scostamento **prima** di toccare Amazon: `--scatole-nuove` fa passare un
imballo cambiato davvero. Senza istantanea lo dicono e procedono. Vedi `DESIGN.md` §13.
```

Nel passo 3 della Procedura, sostituire le tre righe "Misure e pesi delle scatole di lotti passati si rileggono dai piani **SHIPPED** …" con: "Misure e pesi delle scatole di lotti passati li legge `giro.py scatole <cliente>` dai piani **SHIPPED** (il default di `listInboundPlans` mostra solo gli ACTIVE, bozze vuote)."

`DESIGN.md` §12, riga "**Fuori da questa v1.** … memoria delle scatole per SKU (punto 2 della roadmap), …": sostituire "memoria delle scatole per SKU (punto 2 della roadmap)" con "memoria delle scatole per SKU (punto 2 della roadmap, poi fatta in §13)".

- [ ] **Step 6: Commit**

```bash
git add scripts/giro.py tests/test_giro.py SKILL.md DESIGN.md
git commit -m "feat(giro): prepara e riprendi --lotto si fermano su una scatola diversa dallo storico (--scatole-nuove)"
```

---

### Task 7: collaudo dal vivo (sola lettura)

**Files:** nessuno da modificare; output sul Desktop e in `~/.config/newera-amazon/spedizioni/scatole/`.

**Interfaces:** consuma il CLI dei Task 5-6. Credenziali per cliente in `~/scripts/.env` (vedi `DESIGN.md` §7).

> **Esito del collaudo, 2026-09-05** (sola lettura, nessun piano creato).

- [x] **Step 1: ECM riproduce la tabella scritta a mano**

`python3 giro.py scatole ECM` → 36 piani SHIPPED in 12 mesi, 11 SKU, nessun piano saltato, nessun collo misto. I quattro SKU della scheda coincidono: ARTINORM_2_FBA 18 pz 30×40×30 7 kg; Reladol_300 22 pz 38×51×47 9,5 kg; Reladol_500 22 pz 38×51×47 14 kg; Reladol_100 60 pz 30×40×31 9 kg con varianti 30 pz/4,7 kg (fino al 2025-11-11) e 60 pz/9,4 kg (fino al 2025-11-07). C'è anche lo storico ARTINOMR_FBA 22 pz 30×40×30 5 kg. Il "modello" segue l'ultimo piano: oggi "P1 - B1…B4", non più i nomi vecchi ("ARTINORM 22 PZ" resta su ARTINOMR_FBA, ultimo piano 04/08). **Rettifica alla scheda cliente**: la nota del 03/09 diceva che con 12 mesi le varianti di Reladol_100 non si vedono e serviva `--mesi 15` — falso, si vedono uguali; i 15 mesi aggiungono solo 6 SKU fuori finestra e una variante vecchia di ARTINOMR_FBA (8 pz, 21×31×22 cm, 2 kg, fino al 2025-06-30).

- [x] **Step 2: Arcipelago fa emergere il peso di 138_FBA**

`python3 giro.py scatole Arcipelago` → 11 piani SHIPPED, un solo SKU: `138_FBA` 3 pz 32×42.7×25.3 cm **2.22 kg**, come atteso. Il modello però è "P1 - B1", non "PALLET 36 PACCHI" come diceva il piano: il template è cambiato con l'ultimo piano spedito (03/09). Il peso resta quello dichiarato dal cliente — domanda aperta per Guiducci, non un bug.

- [x] **Step 3: la bozza e il controllo, senza creare piani**

`python3 giro.py scatole ECM Reladol_100=130` → `~/Desktop/Lotto-ECM-2026-09-05.yaml` con 2 colli pieni da 60 pz (peso 9, misure dallo standard, commento "standard: P1 - B4") e 1 parziale da 10 pz con `peso_kg:` vuoto e commento "parziale: 10 pz, da pesare". `lotto.carica` sulla bozza alza `ValueError: scatola 2: manca peso_kg`. Nessun `prepara` lanciato.

- [x] **Step 4: annotare l'esito** — fatto in `~/nova/CLIENTI/ecm.md` (con la rettifica dello Step 1) e `~/nova/CLIENTI/arcipelago-gin.md`, e in memoria di progetto (`project_amazon_spedizioni.md`).

---

## Self-review (fatto scrivendo il piano)

- **Copertura della spec §13**: sorgente e finestra (Task 2), collo di riferimento/varianti/misti (Task 2), istantanea (Task 2), verbo e tabella (Task 3, 5), bozza con parziale vuoto e SKU ignoto (Task 3), controllo con le tre regole e la flag, prima di ogni scrittura, senza istantanea (Task 4, 6), errori: piano illeggibile saltato e finestra vuota (Task 2, 3), collaudo ECM/Arcipelago (Task 7), documenti (Task 6).
- **Nomi coerenti tra i task**: `leggi_storico`, `salva_istantanea`, `carica_istantanea`, `tabella`, `bozza_lotto`, `scrivi_bozza`, `Confronto`, `confronta`, `scatola_finta`, `ClienteFinto.spedito`, `giro.scatole`, `giro._controlla_scatole`, `scatole_nuove`.
- **Deviazione dalla spec, dichiarata**: `confronta` torna un `Confronto(scostamenti, note)` invece di `list[str]`, perché le note (miste, senza storico, parziali) non devono fermare `prepara` e la spec le distingue.
