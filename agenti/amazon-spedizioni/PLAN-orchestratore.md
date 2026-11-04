# Orchestratore `giro.py` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** un comando solo (`prepara` / `riprendi` / `conferma`) che porta un lotto FBA dal file `lotto.yaml` al menù corrieri, sa riprendere un piano esistente leggendo lo stadio da Amazon, e prenota solo con `--vai`.

**Architecture:** tre moduli nuovi e puri (`lotto.py` legge il file, `stadio.py` riconosce lo stadio, `giri.py` tiene il file locale per piano) più `giro.py` che compone i 10 moduli esistenti senza cambiarne le interfacce. Due letture nuove nei moduli esistenti (`imballaggio.leggi_articoli_gruppo`, `corrieri.leggi_opzioni_trasporto`) e un campo nuovo (`OpzioneTrasporto.scadenza`). I test end-to-end girano su `tests/cliente_finto.py`, un Amazon finto **con stato** che avanza a ogni POST, così `riprendi` si prova da ogni stadio.

**Tech Stack:** Python 3.9 (`python3` di sistema), pytest, PyYAML 6, `requests`. Import piatti via `tests/conftest.py` (`sys.path.insert` su `scripts/`).

**Spec:** `DESIGN.md` §12 (e §5-§7 per il flusso, gli errori e le credenziali). `SKILL.md` per l'ordine dei passi 2-18.

## Global Constraints

- Nessuna scrittura che spende (`confirmPlacementOption`, `confirmTransportationOptions`) senza `vai=True`; senza `--vai` `conferma` non fa **nessuna** POST.
- Ogni `SpApiError` → `registro.registra_esito(..., str(errore))`, poi rialzata. Se il messaggio contiene `ESITO INCERTO` → anche `giri.aggiorna(plan_id, bloccato=str(errore))`.
- Lo stadio si rilegge sempre da Amazon (`stadio.leggi`), mai dal file locale.
- Ordine dei passi 12-17 come in `SKILL.md`: piazzamento → `registra_piazzamento_confermato` **prima di ogni altra chiamata di rete** → rilettura spedizioni → finestre → trasporto → `registra_trasporto_confermato` → etichette.
- Testi per Mariano in italiano; i prezzi come li stampa `anteprima._prezzo`.
- Il client si costruisce **solo** con `spapi_client.crea_client(cliente)`.
- Stile del repo: docstring che spiegano il *perché*, nomi italiani, niente astrazioni oltre il necessario.
- Le fixture in `tests/fixtures/stadio/*.json` sono risposte vere di Amazon (02/09/2026, telefoni omessi): non modificarle, si aggiungono casi derivandoli in memoria nel test.

---

### File Structure

- Create `scripts/lotto.py` — `Lotto`, `carica(percorso)`, `da_dizionario(dati)`, `a_dizionario(lotto)`, `pallet_amazon(lotto)`. Valida che le scatole contengano esattamente le righe del lotto.
- Create `scripts/stadio.py` — `Fotografia`, `leggi(client, plan_id)`, `riconosci(foto, adesso, etichette_scaricate)`, `scaduta(expiration, adesso)`, costanti degli stadi.
- Create `scripts/giri.py` — file locale `~/.config/newera-amazon/spedizioni/giri/<plan_id>.json`: `salva`, `carica`, `aggiorna`.
- Modify `scripts/modello.py` — `OpzioneTrasporto.scadenza: str | None = None`.
- Modify `scripts/corrieri.py` — estrae `leggi_opzioni_trasporto(client, plan_id, placement_option_id)`; `genera_opzioni_trasporto` la usa; legge `quote.expiration` in `scadenza`.
- Modify `scripts/imballaggio.py` — `leggi_articoli_gruppo(client, plan_id, gruppo_id) -> list[str]`.
- Create `scripts/giro.py` — `prepara`, `riprendi`, `conferma`, `main(argv)`.
- Create `tests/cliente_finto.py` — Amazon finto con stato.
- Create `tests/test_lotto.py`, `tests/test_stadio.py`, `tests/test_giri.py`, `tests/test_cliente_finto.py`, `tests/test_giro.py`.
- Modify `SKILL.md`, `DESIGN.md` §12 (rinomina uno stadio), `PLAN.md` (Task 11: aggiunge il collaudo di `riprendi`).

---

### Task 1: `lotto.py` — il file di lotto

**Files:**
- Create: `scripts/lotto.py`
- Test: `tests/test_lotto.py`

**Interfaces:**
- Consumes: `modello.RigaSpedizione`, `modello.Scatola`.
- Produces: `Lotto(cliente: str, righe: list[RigaSpedizione], scatole: list[Scatola], data_pronta: str, pallet: list[dict])`; `carica(percorso: str) -> Lotto`; `da_dizionario(dati: dict) -> Lotto`; `a_dizionario(lotto: Lotto) -> dict` (inverso esatto, riutilizzato dal file locale); `pallet_amazon(lotto: Lotto) -> list[dict]` (forma `pallets` di generateTransportationOptions).

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_lotto.py
import textwrap
import pytest
import lotto


_YAML = textwrap.dedent("""
    cliente: Phy to Pet
    righe:
      - {sku: DENTIFRICIO_SPRAY_DUE, quantita: 20}
    scatole:
      - {quantita: 2, peso_kg: 0.8, lunghezza_cm: 10, larghezza_cm: 25, altezza_cm: 15,
         righe: [{sku: DENTIFRICIO_SPRAY_DUE, quantita: 10}]}
    data_pronta: "2026-09-05T08:00Z"
    pallet:
      - {quantity: 2, stackability: NON_STACKABLE, peso_kg: 300,
         lunghezza_cm: 120, larghezza_cm: 80, altezza_cm: 150}
""")


@pytest.fixture
def percorso(tmp_path):
    p = tmp_path / "lotto.yaml"
    p.write_text(_YAML)
    return str(p)


def test_carica_legge_righe_scatole_data_e_pallet(percorso):
    l = lotto.carica(percorso)
    assert l.cliente == "Phy to Pet"
    assert [(r.sku, r.quantita) for r in l.righe] == [("DENTIFRICIO_SPRAY_DUE", 20)]
    assert l.scatole[0].quantita == 2 and l.scatole[0].peso_kg == 0.8
    assert [(r.sku, r.quantita) for r in l.scatole[0].righe] == [("DENTIFRICIO_SPRAY_DUE", 10)]
    assert l.data_pronta == "2026-09-05T08:00Z"
    assert l.pallet[0]["stackability"] == "NON_STACKABLE"


def test_pallet_amazon_ha_la_forma_di_generate_transportation_options(percorso):
    """Amazon vuole quantity, stackability, weight{value,unit} e
    dimensions{length,width,height,unitOfMeasurement}; NON_STACKABLE, non
    NOT_STACKABLE (respinto dal vivo il 31/08)."""
    p = lotto.pallet_amazon(lotto.carica(percorso))
    assert p == [{"quantity": 2, "stackability": "NON_STACKABLE",
                  "weight": {"value": 300.0, "unit": "KG"},
                  "dimensions": {"length": 120.0, "width": 80.0, "height": 150.0,
                                 "unitOfMeasurement": "CM"}}]


def test_stackability_sconosciuta_viene_rifiutata_prima_di_amazon(percorso):
    testo = _YAML.replace("NON_STACKABLE", "NOT_STACKABLE")
    with pytest.raises(ValueError, match="stackability.*NOT_STACKABLE.*STACKABLE, NON_STACKABLE"):
        lotto.da_dizionario(__import__("yaml").safe_load(testo))


def test_scatole_che_non_coprono_le_righe_fermano_il_lotto():
    """2 scatole da 10 = 20, il lotto ne dichiara 25: lo SKU va nominato."""
    dati = __import__("yaml").safe_load(_YAML)
    dati["righe"][0]["quantita"] = 25
    with pytest.raises(ValueError, match="DENTIFRICIO_SPRAY_DUE.*25.*20"):
        lotto.da_dizionario(dati)


def test_data_pronta_deve_essere_un_istante():
    dati = __import__("yaml").safe_load(_YAML)
    dati["data_pronta"] = "2026-09-05"
    with pytest.raises(ValueError, match="data_pronta.*2026-09-05.*istante"):
        lotto.da_dizionario(dati)


def test_data_pronta_con_secondi_letta_da_yaml_come_datetime_viene_riportata_a_testo():
    """PyYAML trasforma `2026-09-05T08:00:00Z` in datetime: va riportato
    all'istante ISO che Amazon vuole, non passato come oggetto."""
    dati = __import__("yaml").safe_load(_YAML.replace('"2026-09-05T08:00Z"', "2026-09-05T08:00:00Z"))
    assert lotto.da_dizionario(dati).data_pronta == "2026-09-05T08:00:00Z"


def test_a_dizionario_e_da_dizionario_sono_inversi(percorso):
    l = lotto.carica(percorso)
    assert lotto.da_dizionario(lotto.a_dizionario(l)) == l


def test_lotto_senza_scatole_viene_rifiutato():
    dati = __import__("yaml").safe_load(_YAML)
    dati["scatole"] = []
    with pytest.raises(ValueError, match="senza scatole"):
        lotto.da_dizionario(dati)
```

- [ ] **Step 2: Verifica che falliscano**

Run: `cd ~/.claude/skills/amazon-spedizioni && python3 -m pytest -q tests/test_lotto.py`
Expected: `ModuleNotFoundError: No module named 'lotto'`

- [ ] **Step 3: Scrivi `scripts/lotto.py`**

```python
from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
import yaml
from modello import RigaSpedizione, Scatola

# Stesso vincolo di corrieri.genera_opzioni_trasporto: un istante, non un giorno.
_RE_ISTANTE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{3})?)?Z")
_STACKABILITY = ("STACKABLE", "NON_STACKABLE")
_CAMPI_PALLET = ("quantity", "stackability", "peso_kg", "lunghezza_cm", "larghezza_cm", "altezza_cm")


@dataclass
class Lotto:
    """Il lotto come lo detta Mariano, letto da `lotto.yaml`. `pallet` resta
    nella forma del file: la forma di Amazon la dà `pallet_amazon`."""
    cliente: str
    righe: list[RigaSpedizione]
    scatole: list[Scatola]
    data_pronta: str
    pallet: list[dict] = field(default_factory=list)


def carica(percorso: str) -> Lotto:
    with open(percorso) as f:
        return da_dizionario(yaml.safe_load(f) or {})


def _righe(elenco, dove: str) -> list[RigaSpedizione]:
    righe = []
    for i, r in enumerate(elenco or [], start=1):
        try:
            righe.append(RigaSpedizione(sku=str(r["sku"]).strip(), quantita=int(r["quantita"])))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"{dove}, riga {i}: servono sku e quantita ({e})") from e
    return righe


def _istante(valore) -> str:
    # PyYAML legge `2026-09-05T08:00:00Z` come datetime; senza secondi resta testo.
    if isinstance(valore, datetime):
        if valore.tzinfo is not None:
            valore = valore.astimezone(timezone.utc)
        return valore.strftime("%Y-%m-%dT%H:%M:%SZ")
    testo = str(valore or "").strip()
    if not _RE_ISTANTE.fullmatch(testo):
        raise ValueError(
            f"data_pronta non valida: {testo!r}. Amazon vuole un istante UTC, "
            "non un giorno — es. '2026-09-05T08:00Z'"
        )
    return testo


def _controlla_contenuto(righe: list[RigaSpedizione], scatole: list[Scatola]) -> None:
    """La somma di ciò che c'è nelle scatole deve dare esattamente il lotto:
    Amazon confronta le due dichiarazioni e rifiuta se non combaciano
    (misurato il 29/08), ma lo fa solo dopo aver creato il piano."""
    dichiarato = {r.sku: r.quantita for r in righe}
    nelle_scatole: dict[str, int] = {}
    for s in scatole:
        for r in s.righe:
            nelle_scatole[r.sku] = nelle_scatole.get(r.sku, 0) + r.quantita * s.quantita
    for sku in sorted(set(dichiarato) | set(nelle_scatole)):
        if dichiarato.get(sku, 0) != nelle_scatole.get(sku, 0):
            raise ValueError(
                f"le scatole non tornano per {sku}: il lotto ne dichiara "
                f"{dichiarato.get(sku, 0)}, nelle scatole ce ne sono {nelle_scatole.get(sku, 0)}"
            )


def _pallet(p: dict, i: int) -> dict:
    mancanti = [c for c in _CAMPI_PALLET if p.get(c) in (None, "")]
    if mancanti:
        raise ValueError(f"pallet {i}: manca {', '.join(mancanti)}")
    if p["stackability"] not in _STACKABILITY:
        raise ValueError(
            f"pallet {i}: stackability {p['stackability']!r} non valida, "
            f"Amazon accetta {', '.join(_STACKABILITY)}"
        )
    return {k: (str(v) if k == "stackability" else v) for k, v in p.items()}


def da_dizionario(dati: dict) -> Lotto:
    cliente = str(dati.get("cliente") or "").strip()
    if not cliente:
        raise ValueError("lotto senza cliente")
    righe = _righe(dati.get("righe"), "righe del lotto")
    if not righe:
        raise ValueError("lotto senza righe: servono SKU e quantita")
    scatole = []
    for i, s in enumerate(dati.get("scatole") or [], start=1):
        try:
            scatole.append(Scatola(
                quantita=int(s["quantita"]), peso_kg=float(s["peso_kg"]),
                lunghezza_cm=float(s["lunghezza_cm"]), larghezza_cm=float(s["larghezza_cm"]),
                altezza_cm=float(s["altezza_cm"]),
                righe=_righe(s.get("righe"), f"scatola {i}"),
            ))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"scatola {i}: {e}") from e
    if not scatole:
        raise ValueError("lotto senza scatole: Amazon non divide il piano né quota senza misure e pesi")
    _controlla_contenuto(righe, scatole)
    pallet = [_pallet(p, i) for i, p in enumerate(dati.get("pallet") or [], start=1)]
    return Lotto(cliente=cliente, righe=righe, scatole=scatole,
                 data_pronta=_istante(dati.get("data_pronta")), pallet=pallet)


def a_dizionario(lotto: Lotto) -> dict:
    """Inverso esatto di `da_dizionario`: è ciò che finisce nel file locale del
    piano, così `riprendi` rilegge le scatole senza il file di lotto."""
    return {
        "cliente": lotto.cliente,
        "righe": [{"sku": r.sku, "quantita": r.quantita} for r in lotto.righe],
        "scatole": [{
            "quantita": s.quantita, "peso_kg": s.peso_kg, "lunghezza_cm": s.lunghezza_cm,
            "larghezza_cm": s.larghezza_cm, "altezza_cm": s.altezza_cm,
            "righe": [{"sku": r.sku, "quantita": r.quantita} for r in s.righe],
        } for s in lotto.scatole],
        "data_pronta": lotto.data_pronta,
        "pallet": list(lotto.pallet),
    }


def pallet_amazon(lotto: Lotto) -> list[dict]:
    """La forma `pallets` di generateTransportationOptions. Senza pallet Amazon
    non quota le opzioni LTL (DESIGN §5)."""
    return [{
        "quantity": int(p["quantity"]),
        "stackability": p["stackability"],
        "weight": {"value": float(p["peso_kg"]), "unit": "KG"},
        "dimensions": {"length": float(p["lunghezza_cm"]), "width": float(p["larghezza_cm"]),
                       "height": float(p["altezza_cm"]), "unitOfMeasurement": "CM"},
    } for p in lotto.pallet]
```

- [ ] **Step 4: Verifica che passino (e che tutta la suite resti verde)**

Run: `python3 -m pytest -q`
Expected: tutti `passed` (192 precedenti + 8 nuovi).

- [ ] **Step 5: Commit**

```bash
git add scripts/lotto.py tests/test_lotto.py
git commit -m "feat(orchestratore): lotto.py legge e valida lotto.yaml"
```

---

### Task 2: `stadio.py` — riconoscere lo stadio dalle risposte di Amazon

**Files:**
- Create: `scripts/stadio.py`
- Test: `tests/test_stadio.py` (usa `tests/fixtures/stadio/*.json`, già su disco: vanno committati in questo task)
- Modify: `DESIGN.md` §12 (tabella stadi: `PIAZZAMENTO_SCADUTO` → `PIAZZAMENTO_DA_GENERARE`, riconosciuto da "nessuna OFFERED valida, scadute o mai generate")

**Interfaces:**
- Consumes: `client.get(path, params)`.
- Produces: costanti `IMBALLAGGIO_DA_CONFERMARE, SCATOLE_DA_DICHIARARE, PIAZZAMENTO_DA_GENERARE, PIAZZAMENTO_DA_SCEGLIERE, TRASPORTO_DA_SCEGLIERE, TRASPORTO_PRENOTATO, COMPLETATO`; `Fotografia(piano: dict, packing_options: list[dict], placement_options: list[dict], boxes: list[dict], spedizioni: dict[str, dict])`; `leggi(client, plan_id: str) -> Fotografia`; `riconosci(foto: Fotografia, adesso: datetime, etichette_scaricate: bool = False) -> str`; `scaduta(expiration: str | None, adesso: datetime) -> bool`; `piazzamento_accettato(foto) -> dict | None`; `piazzamenti_offerti_validi(foto, adesso) -> list[dict]`; `gruppi_imballaggio_accettati(foto) -> list[str]`.

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_stadio.py
import json
import os
from datetime import datetime, timezone
import pytest
import stadio

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "stadio")
_ADESSO = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)   # dopo la scadenza del 1/09


def _foto(nome) -> stadio.Fotografia:
    with open(os.path.join(_FIXTURE, f"{nome}.json")) as f:
        d = json.load(f)
    return stadio.Fotografia(
        piano=d["piano"],
        packing_options=d["packingOptions"].get("packingOptions") or [],
        placement_options=d["placementOptions"].get("placementOptions") or [],
        boxes=d["boxes"].get("boxes") or [],
        spedizioni=d["shipments"],
    )


def test_bozza_vuota_e_imballaggio_da_confermare():
    assert stadio.riconosci(_foto("phytopet_bozza_vuota"), _ADESSO) == stadio.IMBALLAGGIO_DA_CONFERMARE


def test_piazzamenti_offerti_ma_scaduti_vanno_rigenerati():
    assert stadio.riconosci(_foto("phytopet_piazzamento_scaduto"), _ADESSO) == stadio.PIAZZAMENTO_DA_GENERARE


def test_stesso_piano_prima_della_scadenza_era_da_scegliere():
    """Stessa fotografia, orologio al 30/08: le due opzioni OFFERED valevano."""
    prima = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    assert stadio.riconosci(_foto("phytopet_piazzamento_scaduto"), prima) == stadio.PIAZZAMENTO_DA_SCEGLIERE


def test_imballaggio_accettato_senza_scatole_e_scatole_da_dichiarare():
    foto = _foto("phytopet_piazzamento_scaduto")
    foto.boxes = []
    assert stadio.riconosci(foto, _ADESSO) == stadio.SCATOLE_DA_DICHIARARE


def test_bedshire_ha_il_trasporto_prenotato():
    assert stadio.riconosci(_foto("bedshire_trasporto_prenotato"), _ADESSO) == stadio.TRASPORTO_PRENOTATO


def test_trasporto_prenotato_con_etichette_scaricate_e_completato():
    assert stadio.riconosci(_foto("bedshire_trasporto_prenotato"), _ADESSO,
                            etichette_scaricate=True) == stadio.COMPLETATO


def test_piazzamento_accettato_senza_corriere_e_trasporto_da_scegliere():
    foto = _foto("bedshire_trasporto_prenotato")
    for s in foto.spedizioni.values():
        s.pop("selectedTransportationOptionId", None)
    assert stadio.riconosci(foto, _ADESSO) == stadio.TRASPORTO_DA_SCEGLIERE


def test_scaduta_confronta_istanti_iso_con_z():
    assert stadio.scaduta("2026-09-01T20:39:56.199Z", _ADESSO) is True
    assert stadio.scaduta("2026-09-03T00:00:00Z", _ADESSO) is False
    assert stadio.scaduta(None, _ADESSO) is False


def test_letture_di_supporto():
    foto = _foto("phytopet_piazzamento_scaduto")
    assert stadio.piazzamento_accettato(foto) is None
    assert stadio.piazzamenti_offerti_validi(foto, _ADESSO) == []
    assert stadio.gruppi_imballaggio_accettati(foto) == foto.packing_options[0]["packingGroups"]
    bed = _foto("bedshire_trasporto_prenotato")
    assert stadio.piazzamento_accettato(bed)["placementOptionId"] == "pl0f47b947-9e43-49f7-b9a7-5210f0dee02e"


def test_leggi_fa_le_cinque_letture_e_le_spedizioni_di_ogni_opzione():
    class Client:
        def __init__(self):
            self.percorsi = []
        def get(self, path, params=None):
            self.percorsi.append(path)
            b = "/inbound/fba/2024-03-20/inboundPlans/wf1"
            return {
                b: {"inboundPlanId": "wf1", "shipments": [{"shipmentId": "sh9", "status": "WORKING"}]},
                f"{b}/packingOptions": {"packingOptions": [{"packingOptionId": "po1", "status": "ACCEPTED", "packingGroups": ["pg1"]}]},
                f"{b}/placementOptions": {"placementOptions": [{"placementOptionId": "pl1", "status": "OFFERED", "shipmentIds": ["sh1"]}]},
                f"{b}/boxes": {"boxes": [{"packageId": "pk1"}]},
                f"{b}/shipments/sh1": {"shipmentId": "sh1", "status": "UNCONFIRMED"},
                f"{b}/shipments/sh9": {"shipmentId": "sh9", "status": "WORKING"},
            }[path]
    c = Client()
    foto = stadio.leggi(c, "wf1")
    assert set(foto.spedizioni) == {"sh1", "sh9"}
    assert foto.boxes == [{"packageId": "pk1"}]
    assert c.percorsi.count("/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1") == 1
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest -q tests/test_stadio.py`
Expected: `ModuleNotFoundError: No module named 'stadio'`

- [ ] **Step 3: Scrivi `scripts/stadio.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime

_BASE = "/inbound/fba/2024-03-20/inboundPlans"

IMBALLAGGIO_DA_CONFERMARE = "IMBALLAGGIO_DA_CONFERMARE"
SCATOLE_DA_DICHIARARE = "SCATOLE_DA_DICHIARARE"
PIAZZAMENTO_DA_GENERARE = "PIAZZAMENTO_DA_GENERARE"
PIAZZAMENTO_DA_SCEGLIERE = "PIAZZAMENTO_DA_SCEGLIERE"
TRASPORTO_DA_SCEGLIERE = "TRASPORTO_DA_SCEGLIERE"
TRASPORTO_PRENOTATO = "TRASPORTO_PRENOTATO"
COMPLETATO = "COMPLETATO"


@dataclass
class Fotografia:
    """Ciò che Amazon dice di un piano, letto in un colpo solo. Lo stadio si
    deduce da qui e mai dal file locale: Amazon è l'unico che sa cosa è stato
    davvero accettato, confermato o prenotato (DESIGN §12)."""
    piano: dict
    packing_options: list[dict] = field(default_factory=list)
    placement_options: list[dict] = field(default_factory=list)
    boxes: list[dict] = field(default_factory=list)
    spedizioni: dict[str, dict] = field(default_factory=dict)


def leggi(client, plan_id: str) -> Fotografia:
    piano = client.get(f"{_BASE}/{plan_id}")
    packing = client.get(f"{_BASE}/{plan_id}/packingOptions").get("packingOptions") or []
    placement = client.get(f"{_BASE}/{plan_id}/placementOptions").get("placementOptions") or []
    boxes = client.get(f"{_BASE}/{plan_id}/boxes", params={"pageSize": 100}).get("boxes") or []
    ids: list[str] = []
    for o in placement:
        ids += o.get("shipmentIds") or []
    ids += [s["shipmentId"] for s in (piano.get("shipments") or [])]
    spedizioni = {sid: client.get(f"{_BASE}/{plan_id}/shipments/{sid}") for sid in dict.fromkeys(ids)}
    return Fotografia(piano, packing, placement, boxes, spedizioni)


def scaduta(expiration: str | None, adesso: datetime) -> bool:
    if not expiration:
        return False
    return datetime.fromisoformat(expiration.replace("Z", "+00:00")) <= adesso


def piazzamento_accettato(foto: Fotografia) -> dict | None:
    return next((o for o in foto.placement_options if o.get("status") == "ACCEPTED"), None)


def piazzamenti_offerti_validi(foto: Fotografia, adesso: datetime) -> list[dict]:
    return [o for o in foto.placement_options
            if o.get("status") == "OFFERED" and not scaduta(o.get("expiration"), adesso)]


def gruppi_imballaggio_accettati(foto: Fotografia) -> list[str]:
    for o in foto.packing_options:
        if o.get("status") == "ACCEPTED":
            return list(o.get("packingGroups") or [])
    return []


def riconosci(foto: Fotografia, adesso: datetime, etichette_scaricate: bool = False) -> str:
    """Un solo posto in cui si decide "a che punto è il piano". Le opzioni
    scadute contano come mai generate: rigenerarle non costa niente."""
    if not gruppi_imballaggio_accettati(foto):
        return IMBALLAGGIO_DA_CONFERMARE
    if not foto.boxes:
        return SCATOLE_DA_DICHIARARE
    accettato = piazzamento_accettato(foto)
    if accettato is None:
        return PIAZZAMENTO_DA_SCEGLIERE if piazzamenti_offerti_validi(foto, adesso) else PIAZZAMENTO_DA_GENERARE
    prenotate = [(foto.spedizioni.get(sid) or {}).get("selectedTransportationOptionId")
                 for sid in (accettato.get("shipmentIds") or [])]
    if not prenotate or not all(prenotate):
        return TRASPORTO_DA_SCEGLIERE
    return COMPLETATO if etichette_scaricate else TRASPORTO_PRENOTATO
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest -q tests/test_stadio.py`
Expected: 10 passed.

- [ ] **Step 5: Allinea DESIGN §12**

Sostituisci nella tabella degli stadi la riga `PIAZZAMENTO_SCADUTO` con:
`| PIAZZAMENTO_DA_GENERARE | nessuna placementOption OFFERED valida (scadute o mai generate) | rigenera piazzamenti e preventivi |`
e nel testo "Collaudo" `wf0dae3b7c → PIAZZAMENTO_SCADUTO` con `→ PIAZZAMENTO_DA_GENERARE`.

- [ ] **Step 6: Commit**

```bash
git add scripts/stadio.py tests/test_stadio.py tests/fixtures/stadio/ DESIGN.md
git commit -m "feat(orchestratore): stadio.py riconosce lo stadio del piano da Amazon, fixture vere del 02/09"
```

---

### Task 3: `giri.py` — file locale del piano

**Files:**
- Create: `scripts/giri.py`
- Test: `tests/test_giri.py`

**Interfaces:**
- Produces: `salva(plan_id: str, dati: dict) -> None`; `carica(plan_id: str) -> dict | None`; `aggiorna(plan_id: str, **campi) -> dict` (carica, aggiorna i campi di primo livello, salva, torna il risultato). Percorso `~/.config/newera-amazon/spedizioni/giri/<plan_id>.json`, scrittura atomica come `registro._salva`.

- [ ] **Step 1: Scrivi i test che falliscano**

```python
# tests/test_giri.py
import json
import os
import giri


def test_salva_e_carica_sotto_config_newera(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    giri.salva("wf1", {"cliente": "Phy to Pet", "riferimento": "2026-09-02#0#abc"})
    atteso = tmp_path / ".config" / "newera-amazon" / "spedizioni" / "giri" / "wf1.json"
    assert atteso.is_file()
    assert giri.carica("wf1") == {"cliente": "Phy to Pet", "riferimento": "2026-09-02#0#abc"}


def test_carica_di_un_piano_sconosciuto_torna_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert giri.carica("wf-mai-visto") is None


def test_aggiorna_tocca_solo_i_campi_dati(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    giri.salva("wf1", {"cliente": "Phy to Pet", "scelte": {}, "bloccato": None})
    dopo = giri.aggiorna("wf1", bloccato="ESITO INCERTO ...", scelte={"placement_option_id": "pl1"})
    assert dopo["cliente"] == "Phy to Pet"
    assert dopo["bloccato"].startswith("ESITO INCERTO")
    assert giri.carica("wf1")["scelte"] == {"placement_option_id": "pl1"}


def test_scrittura_atomica_non_lascia_file_di_appoggio(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    giri.salva("wf1", {"a": 1})
    cartella = tmp_path / ".config" / "newera-amazon" / "spedizioni" / "giri"
    assert sorted(os.listdir(cartella)) == ["wf1.json"]
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest -q tests/test_giri.py`
Expected: `ModuleNotFoundError: No module named 'giri'`

- [ ] **Step 3: Scrivi `scripts/giri.py`**

```python
from __future__ import annotations
import json
import os


def _percorso(plan_id: str) -> str:
    # Stessa radice del registro (registro._percorso), sottocartella per piano:
    # qui vive solo ciò che Amazon non sa (scatole, data pronta, riferimento
    # di registro, scelte fatte), mai lo stadio (DESIGN §12).
    return os.path.expanduser(f"~/.config/newera-amazon/spedizioni/giri/{plan_id}.json")


def salva(plan_id: str, dati: dict) -> None:
    percorso = _percorso(plan_id)
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    temporaneo = f"{percorso}.tmp"
    with open(temporaneo, "w") as f:
        f.write(json.dumps(dati, indent=2, ensure_ascii=False))
    os.replace(temporaneo, percorso)


def carica(plan_id: str) -> dict | None:
    percorso = _percorso(plan_id)
    if not os.path.isfile(percorso):
        return None
    with open(percorso) as f:
        return json.load(f)


def aggiorna(plan_id: str, **campi) -> dict:
    dati = carica(plan_id) or {}
    dati.update(campi)
    salva(plan_id, dati)
    return dati
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest -q tests/test_giri.py`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/giri.py tests/test_giri.py
git commit -m "feat(orchestratore): giri.py, file locale per piano"
```

---

### Task 4: due letture nuove nei moduli esistenti + `OpzioneTrasporto.scadenza`

**Files:**
- Modify: `scripts/modello.py` (classe `OpzioneTrasporto`)
- Modify: `scripts/corrieri.py` (`genera_opzioni_trasporto`, righe 82-100)
- Modify: `scripts/imballaggio.py` (in coda)
- Test: `tests/test_corrieri.py`, `tests/test_imballaggio.py` (aggiunte in coda)

**Interfaces:**
- Produces: `OpzioneTrasporto.scadenza: str | None = None` (da `quote.expiration`); `corrieri.leggi_opzioni_trasporto(client, plan_id: str, placement_option_id: str) -> list[OpzioneTrasporto]` (solo la GET, senza generare); `imballaggio.leggi_articoli_gruppo(client, plan_id: str, gruppo_id: str) -> list[str]` (gli msku del gruppo, da `GET packingGroups/{id}/items`, forma vera in `tests/fixtures/stadio/phytopet_piazzamento_scaduto.json` → `packingGroupItems`).

- [ ] **Step 1: Scrivi i test che falliscono** (in coda ai due file esistenti)

```python
# tests/test_corrieri.py — in coda
def test_leggi_opzioni_trasporto_senza_generare_e_con_scadenza():
    """`conferma` deve poter rileggere i preventivi già generati e sapere
    se sono scaduti, senza chiedere ad Amazon di rifarli."""
    class Client:
        def __init__(self):
            self.post_fatte = 0
        def post(self, *a, **k):
            self.post_fatte += 1
            return {"operationId": "op"}
        def get(self, path, params=None):
            assert path.endswith("/inboundPlans/wf1/transportationOptions")
            assert params == {"placementOptionId": "pl1"}
            return {"transportationOptions": [
                {"transportationOptionId": "to1", "shipmentId": "sh1",
                 "shippingSolution": "AMAZON_PARTNERED_CARRIER", "shippingMode": "GROUND_SMALL_PARCEL",
                 "carrier": {"name": "UPS"}, "preconditions": [],
                 "quote": {"cost": {"amount": 7.9, "code": "EUR"}, "expiration": "2026-09-03T10:00:00.000Z"}},
                {"transportationOptionId": "to2", "shipmentId": "sh1",
                 "shippingSolution": "USE_YOUR_OWN_CARRIER", "shippingMode": "FREIGHT_LTL",
                 "preconditions": ["CONFIRMED_DELIVERY_WINDOW"]},
            ]}
    c = Client()
    opzioni = corrieri.leggi_opzioni_trasporto(c, "wf1", "pl1")
    assert c.post_fatte == 0
    assert [o.transportation_option_id for o in opzioni] == ["to1", "to2"]
    assert opzioni[0].scadenza == "2026-09-03T10:00:00.000Z"
    assert opzioni[1].scadenza is None and opzioni[1].richiede_finestra_consegna
```

```python
# tests/test_imballaggio.py — in coda
def test_leggi_articoli_gruppo_torna_gli_msku_del_gruppo():
    class Client:
        def get(self, path, params=None):
            assert path == "/inbound/fba/2024-03-20/inboundPlans/wf1/packingGroups/pg1/items"
            # forma vera, tests/fixtures/stadio/phytopet_piazzamento_scaduto.json
            return {"items": [{"labelOwner": "NONE", "msku": "DENTIFRICIO_SPRAY_DUE", "quantity": 20,
                               "fnsku": "B0F9XFJZ4Y", "asin": "B0F9XFJZ4Y", "prepInstructions": []}]}
    assert imballaggio.leggi_articoli_gruppo(Client(), "wf1", "pg1") == ["DENTIFRICIO_SPRAY_DUE"]
```

Controlla in testa ai due file di test che `corrieri` e `imballaggio` siano importati come moduli (`import corrieri` / `import imballaggio`); se importano solo le funzioni, aggiungi l'import del modulo.

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest -q tests/test_corrieri.py tests/test_imballaggio.py`
Expected: 2 failed, `AttributeError: module 'corrieri' has no attribute 'leggi_opzioni_trasporto'` e lo stesso per `imballaggio`.

- [ ] **Step 3: Implementa**

In `modello.py`, dentro `OpzioneTrasporto`, dopo `richiede_finestra_consegna`:
```python
    # `quote.expiration`: un preventivo scaduto non si può più confermare.
    scadenza: str | None = None
```

In `corrieri.py`, sostituisci in `genera_opzioni_trasporto` tutto ciò che segue il controllo `operationStatus` (da `elenco = client.get(...)` fino a `return opzioni`) con:
```python
    return leggi_opzioni_trasporto(client, plan_id, placement_option_id)
```
e aggiungi, dopo `genera_opzioni_trasporto`:
```python
def leggi_opzioni_trasporto(client, plan_id: str, placement_option_id: str) -> list[OpzioneTrasporto]:
    """`listTransportationOptions`: i preventivi già generati per un'opzione
    di piazzamento. Non genera niente — serve a `conferma` per rileggere ciò
    che Mariano ha visto nel menù e controllare che non sia scaduto."""
    elenco = client.get(_path_opzioni_trasporto(plan_id),
                         params={"placementOptionId": placement_option_id})
    opzioni = []
    for o in elenco.get("transportationOptions", []):
        # Il prezzo si prende sempre che ci sia, qualunque sia il programma di
        # spedizione: `convenzionato` etichetta il tipo di opzione, non decide
        # se un prezzo esiste. Un preventivo scartato sarebbe una spesa
        # mostrata a Mariano come gratuita.
        quota = o.get("quote") or {}
        costo = quota.get("cost") or {}
        opzioni.append(OpzioneTrasporto(
            transportation_option_id=o["transportationOptionId"],
            corriere=(o.get("carrier") or {}).get("name") or "corriere proprio",
            convenzionato=o.get("shippingSolution") == "AMAZON_PARTNERED_CARRIER",
            costo=costo.get("amount"),
            valuta=costo.get("code") or "EUR",
            shipment_id=o.get("shipmentId", ""),
            modalita=o.get("shippingMode", ""),
            richiede_finestra_consegna="CONFIRMED_DELIVERY_WINDOW" in (o.get("preconditions") or []),
            scadenza=quota.get("expiration"),
        ))
    return opzioni
```

In `imballaggio.py`, in coda:
```python
def leggi_articoli_gruppo(client, plan_id: str, gruppo_id: str) -> list[str]:
    """Gli msku di un gruppo di imballaggio (`listPackingGroupItems`). Serve
    ad assegnare le scatole dettate da Mariano al gruppo giusto quando Amazon
    ne fa più d'uno."""
    risposta = client.get(f"{_BASE}/{plan_id}/packingGroups/{gruppo_id}/items")
    return [i["msku"] for i in (risposta.get("items") or []) if i.get("msku")]
```

- [ ] **Step 4: Verifica che passino, tutta la suite**

Run: `python3 -m pytest -q`
Expected: tutti passed (i test esistenti di `genera_opzioni_trasporto` devono restare verdi: la funzione fa le stesse chiamate di prima).

- [ ] **Step 5: Commit**

```bash
git add scripts/modello.py scripts/corrieri.py scripts/imballaggio.py tests/test_corrieri.py tests/test_imballaggio.py
git commit -m "feat(orchestratore): letture di supporto (preventivi con scadenza, articoli per gruppo)"
```

---

### Task 5: `tests/cliente_finto.py` — Amazon finto con stato

**Files:**
- Create: `tests/cliente_finto.py`
- Test: `tests/test_cliente_finto.py`

**Interfaces:**
- Produces: `ClienteFinto(scadenza_piazzamento="2026-09-10T00:00:00.000Z", scadenza_preventivi="2026-09-10T00:00:00.000Z")` con `get`, `post`, `attendi_operazione` (stesso contratto di `SpApiClient`), `post_fatte: list[tuple[str, dict | None]]`, e `piano_a(stadio: str, plan_id="wf1") -> str` che precostruisce un piano già a quello stadio (per provare `riprendi`). ID fissi: piano `wf1`, packing `po1`/gruppo `pg1`, piazzamenti `pl1` (sh1 → MXP5, commissione 0) e `pl2` (sh2 → TRN3, commissione 4.0), trasporto `to-ups-<sid>` (UPS convenzionato 7.9 EUR) e `to-proprio-<sid>` (corriere proprio, `CONFIRMED_DELIVERY_WINDOW`), finestra `dw1`, confirmation id `FBA1234<sid>`, cartone `FBA1234<sid>U000001`, etichette URL `https://amazon-labels.example/<conf>.pdf`.

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# tests/test_cliente_finto.py
"""Il finto deve comportarsi come l'Amazon vero letto nelle fixture: ogni
POST fa avanzare lo stadio come lo riconosce stadio.riconosci. Se il finto
non avanza come Amazon, i test di giro.py provano un mondo che non esiste."""
from datetime import datetime, timezone
import stadio
from cliente_finto import ClienteFinto
from modello import RigaSpedizione, Scatola
from piano import crea_piano
from imballaggio import genera_opzioni_imballaggio, conferma_imballaggio, imposta_informazioni_imballaggio
from piazzamento import genera_opzioni_piazzamento, conferma_piazzamento
from corrieri import genera_opzioni_trasporto
from conferma import conferma_trasporto

_ADESSO = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)
_INDIRIZZO = {"name": "X", "addressLine1": "Via Roma 1", "city": "Prato", "postalCode": "59100",
              "countryCode": "IT", "phoneNumber": "1"}


def _stadio(c, pid):
    return stadio.riconosci(stadio.leggi(c, pid), _ADESSO)


def test_il_finto_avanza_come_amazon_a_ogni_post():
    c = ClienteFinto()
    righe = [RigaSpedizione("SKU-1", 30)]
    pid = crea_piano(c, righe, _INDIRIZZO, "APJ6JRA9NG5V4").inbound_plan_id
    assert _stadio(c, pid) == stadio.IMBALLAGGIO_DA_CONFERMARE
    po = genera_opzioni_imballaggio(c, pid)[0]
    conferma_imballaggio(c, pid, po.packing_option_id)
    assert _stadio(c, pid) == stadio.SCATOLE_DA_DICHIARARE
    imposta_informazioni_imballaggio(c, pid, {po.packing_group_ids[0]: [
        Scatola(1, 8.0, 40, 30, 25, righe)]}, "APJ6JRA9NG5V4")
    assert _stadio(c, pid) == stadio.PIAZZAMENTO_DA_GENERARE
    pl = genera_opzioni_piazzamento(c, pid)
    assert [o.placement_option_id for o in pl] == ["pl1", "pl2"]
    assert _stadio(c, pid) == stadio.PIAZZAMENTO_DA_SCEGLIERE
    opz = genera_opzioni_trasporto(c, pid, "pl1", ["sh1"], "2026-09-05T08:00Z")
    assert {o.transportation_option_id for o in opz} == {"to-ups-sh1", "to-proprio-sh1"}
    conferma_piazzamento(c, pid, "pl1")
    assert _stadio(c, pid) == stadio.TRASPORTO_DA_SCEGLIERE
    assert c.get(f"/inbound/fba/2024-03-20/inboundPlans/{pid}/shipments/sh1")["shipmentConfirmationId"] == "FBA1234sh1"
    conferma_trasporto(c, pid, [o for o in opz if o.transportation_option_id == "to-ups-sh1"])
    assert _stadio(c, pid) == stadio.TRASPORTO_PRENOTATO


def test_piano_a_precostruisce_ogni_stadio():
    for s in (stadio.IMBALLAGGIO_DA_CONFERMARE, stadio.SCATOLE_DA_DICHIARARE,
              stadio.PIAZZAMENTO_DA_GENERARE, stadio.PIAZZAMENTO_DA_SCEGLIERE,
              stadio.TRASPORTO_DA_SCEGLIERE, stadio.TRASPORTO_PRENOTATO):
        c = ClienteFinto()
        pid = c.piano_a(s)
        assert _stadio(c, pid) == s, s


def test_scadenza_dei_piazzamenti_e_regolabile():
    c = ClienteFinto(scadenza_piazzamento="2026-09-01T00:00:00.000Z")
    pid = c.piano_a(stadio.PIAZZAMENTO_DA_SCEGLIERE)
    assert _stadio(c, pid) == stadio.PIAZZAMENTO_DA_GENERARE
```

- [ ] **Step 2: Verifica che fallisca**

Run: `python3 -m pytest -q tests/test_cliente_finto.py`
Expected: `ModuleNotFoundError: No module named 'cliente_finto'`

- [ ] **Step 3: Scrivi `tests/cliente_finto.py`**

```python
"""Amazon finto CON STATO per l'orchestratore. Risponde per percorso come il
ClienteFinto di test_prova_a_vuoto, ma ogni POST fa avanzare il piano come
farebbe Amazon (imballaggio accettato, scatole registrate, piazzamenti
offerti, spedizioni confermate, corriere prenotato), così che stadio.leggi
veda a ogni passo ciò che vedrebbe dal vivo. Le forme copiano le fixture di
tests/fixtures/stadio (02/09/2026)."""
from __future__ import annotations
import re

_B = "/inbound/fba/2024-03-20/inboundPlans"


class ClienteFinto:
    def __init__(self, scadenza_piazzamento="2026-09-10T00:00:00.000Z",
                 scadenza_preventivi="2026-09-10T00:00:00.000Z"):
        self.scadenza_piazzamento = scadenza_piazzamento
        self.scadenza_preventivi = scadenza_preventivi
        self.piani: dict[str, dict] = {}
        self.post_fatte: list[tuple[str, dict | None]] = []
        self.operazioni: set[str] = set()
        self._n = 0

    # ---- stato precostruito -------------------------------------------------
    def piano_a(self, stadio: str, plan_id: str = "wf1") -> str:
        import stadio as S
        p = self._nuovo(plan_id, [{"msku": "SKU-1", "quantity": 30, "prepOwner": "NONE", "labelOwner": "NONE"}])
        ordine = [S.IMBALLAGGIO_DA_CONFERMARE, S.SCATOLE_DA_DICHIARARE, S.PIAZZAMENTO_DA_GENERARE,
                  S.PIAZZAMENTO_DA_SCEGLIERE, S.TRASPORTO_DA_SCEGLIERE, S.TRASPORTO_PRENOTATO]
        passi = ordine.index(stadio)
        if passi >= 1:
            p["packing"] = {"packingOptionId": "po1", "status": "ACCEPTED", "packingGroups": ["pg1"]}
        if passi >= 2:
            p["boxes"] = [{"packageId": "pk1", "quantity": 1}]
        if passi >= 3:
            self._genera_piazzamenti(p)
        if passi >= 4:
            self._conferma_piazzamento(p, "pl1")
        if passi >= 5:
            p["spedizioni"]["sh1"]["selectedTransportationOptionId"] = "to-ups-sh1"
        return plan_id

    # ---- contratto SpApiClient ---------------------------------------------
    def attendi_operazione(self, operation_id, attesa_max_s=300):
        if operation_id not in self.operazioni:
            raise AssertionError(f"interrogata un'operazione mai aperta da una POST: {operation_id}")
        return {"operationId": operation_id, "operationStatus": "SUCCESS"}

    def post(self, path, json_body=None, params=None):
        self.post_fatte.append((path, json_body))
        self._n += 1
        op = f"op-{self._n}"
        self.operazioni.add(op)
        if path == _B:
            pid = f"wf{len(self.piani) + 1}"
            self._nuovo(pid, json_body["items"])
            return {"inboundPlanId": pid, "operationId": op}
        pid = path[len(_B) + 1:].split("/")[0]
        p = self.piani[pid]
        resto = path[len(_B) + len(pid) + 1:]
        if resto == "/packingOptions":
            p["packing"] = {"packingOptionId": "po1", "status": "OFFERED", "packingGroups": ["pg1"]}
        elif resto == "/packingOptions/po1/confirmation":
            p["packing"]["status"] = "ACCEPTED"
        elif resto == "/packingInformation":
            p["boxes"] = [{"packageId": f"pk{i}", "quantity": 1}
                          for g in json_body["packageGroupings"] for i, b in enumerate(g["boxes"], 1)
                          for _ in range(b["quantity"])]
        elif resto == "/placementOptions":
            self._genera_piazzamenti(p)
        elif re.fullmatch(r"/placementOptions/(pl\d)/confirmation", resto):
            self._conferma_piazzamento(p, resto.split("/")[2])
        elif resto == "/transportationOptions":
            pl = json_body["placementOptionId"]
            p["trasporti"][pl] = [o for c in json_body["shipmentTransportationConfigurations"]
                                  for o in self._trasporto(c["shipmentId"])]
        elif re.fullmatch(r"/shipments/(sh\d)/deliveryWindowOptions", resto):
            p["finestre"][resto.split("/")[2]] = [{"deliveryWindowOptionId": "dw1", "startDate": "2026-09-08T00:00Z",
                                                   "endDate": "2026-09-12T00:00Z", "availabilityType": "AVAILABLE"}]
        elif re.fullmatch(r"/shipments/(sh\d)/deliveryWindowOptions/dw1/confirmation", resto):
            p["spedizioni"][resto.split("/")[2]]["selectedDeliveryWindow"] = {"deliveryWindowOptionId": "dw1"}
        elif resto == "/transportationOptions/confirmation":
            for s in json_body["transportationSelections"]:
                p["spedizioni"][s["shipmentId"]]["selectedTransportationOptionId"] = s["transportationOptionId"]
        else:
            raise AssertionError(f"POST su un percorso senza comportamento: {path}")
        return {"operationId": op}

    def get(self, path, params=None):
        if path == "/inbound/fba/2024-03-20/items/prepDetails":
            return {"mskuPrepDetails": [{"msku": m, "prepOwnerConstraint": "NONE_ONLY",
                                         "labelOwnerConstraint": "NONE_ONLY", "prepCategory": "NONE"}
                                        for m in params["mskus"].split(",")]}
        m = re.fullmatch(r"/fba/inbound/v0/shipments/(FBA\w+)/labels", path)
        if m:
            return {"payload": {"DownloadURL": f"https://amazon-labels.example/{m.group(1)}.pdf"}}
        assert path.startswith(_B + "/"), f"GET su un percorso senza fixture: {path}"
        pid = path[len(_B) + 1:].split("/")[0]
        p = self.piani[pid]
        resto = path[len(_B) + len(pid) + 1:]
        if resto == "":
            return {"inboundPlanId": pid, "status": "ACTIVE", "name": p["name"],
                    "packingOptions": [p["packing"]] if p["packing"] and p["packing"]["status"] == "ACCEPTED" else [],
                    "placementOptions": [{"placementOptionId": o["placementOptionId"]} for o in p["piazzamenti"]],
                    "shipments": [{"shipmentId": sid, "status": s["status"]} for sid, s in p["spedizioni"].items()
                                  if s["status"] == "WORKING"]}
        if resto == "/packingOptions":
            return {"packingOptions": [p["packing"]] if p["packing"] else [], "pagination": {}}
        if resto == "/packingGroups/pg1/items":
            return {"items": [{"msku": i["msku"], "quantity": i["quantity"], "labelOwner": "NONE"} for i in p["items"]]}
        if resto == "/placementOptions":
            return {"placementOptions": p["piazzamenti"], "pagination": {}}
        if resto == "/boxes":
            return {"boxes": p["boxes"], "pagination": {}}
        m = re.fullmatch(r"/shipments/(sh\d)", resto)
        if m:
            return dict(p["spedizioni"][m.group(1)])
        m = re.fullmatch(r"/shipments/(sh\d)/items", resto)
        if m:
            return {"items": [{"msku": i["msku"], "quantity": i["quantity"]} for i in p["items"]]}
        m = re.fullmatch(r"/shipments/(sh\d)/boxes", resto)
        if m:
            conf = p["spedizioni"][m.group(1)].get("shipmentConfirmationId")
            return {"boxes": [{"boxId": f"{conf}U000001", "packageId": "pk1"}] if conf else []}
        if resto == "/transportationOptions":
            return {"transportationOptions": p["trasporti"].get(params["placementOptionId"], [])}
        m = re.fullmatch(r"/shipments/(sh\d)/deliveryWindowOptions", resto)
        if m:
            return {"deliveryWindowOptions": p["finestre"].get(m.group(1), [])}
        raise AssertionError(f"GET su un percorso senza fixture: {path}")

    # ---- interno ------------------------------------------------------------
    def _nuovo(self, pid, items):
        p = {"name": f"Spedizione {items[0]['msku']}", "items": items, "packing": None, "boxes": [],
             "piazzamenti": [], "spedizioni": {}, "trasporti": {}, "finestre": {}}
        self.piani[pid] = p
        return p

    def _genera_piazzamenti(self, p):
        p["piazzamenti"] = [
            {"placementOptionId": "pl1", "status": "OFFERED", "expiration": self.scadenza_piazzamento,
             "shipmentIds": ["sh1"], "fees": [{"type": "FEE", "target": "Placement Services",
                                               "value": {"amount": 0, "code": "EUR"}}], "discounts": []},
            {"placementOptionId": "pl2", "status": "OFFERED", "expiration": self.scadenza_piazzamento,
             "shipmentIds": ["sh2"], "fees": [{"type": "FEE", "target": "Placement Services",
                                               "value": {"amount": 4.0, "code": "EUR"}}], "discounts": []},
        ]
        for sid, fc in (("sh1", "MXP5"), ("sh2", "TRN3")):
            p["spedizioni"][sid] = {"shipmentId": sid, "status": "UNCONFIRMED",
                                    "destination": {"destinationType": "AMAZON_WAREHOUSE", "warehouseId": fc}}

    def _conferma_piazzamento(self, p, pl_id):
        for o in p["piazzamenti"]:
            o["status"] = "ACCEPTED" if o["placementOptionId"] == pl_id else "EXPIRED"
        scelta = next(o for o in p["piazzamenti"] if o["placementOptionId"] == pl_id)
        for sid in scelta["shipmentIds"]:
            p["spedizioni"][sid]["status"] = "WORKING"
            p["spedizioni"][sid]["shipmentConfirmationId"] = f"FBA1234{sid}"
        for b in p["boxes"]:
            b["boxId"] = f"FBA1234{scelta['shipmentIds'][0]}U000001"

    def _trasporto(self, sid):
        return [
            {"transportationOptionId": f"to-ups-{sid}", "shipmentId": sid,
             "shippingSolution": "AMAZON_PARTNERED_CARRIER", "shippingMode": "GROUND_SMALL_PARCEL",
             "carrier": {"name": "UPS", "alphaCode": "UPSN"}, "preconditions": [],
             "quote": {"cost": {"amount": 7.9, "code": "EUR"}, "expiration": self.scadenza_preventivi}},
            {"transportationOptionId": f"to-proprio-{sid}", "shipmentId": sid,
             "shippingSolution": "USE_YOUR_OWN_CARRIER", "shippingMode": "FREIGHT_LTL",
             "preconditions": ["CONFIRMED_DELIVERY_WINDOW"]},
        ]
```

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest -q tests/test_cliente_finto.py`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/cliente_finto.py tests/test_cliente_finto.py
git commit -m "test(orchestratore): Amazon finto con stato, avanza come le fixture vere"
```

---

### Task 6: `giro.prepara` e `giro.riprendi` — fino al menù

**Files:**
- Create: `scripts/giro.py`
- Test: `tests/test_giro.py`

**Interfaces:**
- Consumes: `lotto.Lotto/da_dizionario/a_dizionario/pallet_amazon`, `stadio.*`, `giri.*`, `clienti.carica_cliente`, `registro.*`, `piano.crea_piano`, `imballaggio.genera_opzioni_imballaggio/conferma_imballaggio/imposta_informazioni_imballaggio/leggi_articoli_gruppo`, `piazzamento.genera_opzioni_piazzamento/leggi_spedizioni`, `corrieri.genera_opzioni_trasporto`, `anteprima.riepilogo`.
- Produces: `prepara(client, lotto: Lotto, adesso: datetime | None = None) -> tuple[str, str]` (plan_id, testo del menù); `riprendi(client, plan_id: str, lotto: Lotto | None = None, packing_option_id: str | None = None, verificato: bool = False, adesso: datetime | None = None) -> str`; `_menu(...)` interno. Il file locale ha la forma `{"cliente", "riferimento", "lotto", "scelte": {}, "etichette": {}, "bloccato": None}`.

- [ ] **Step 1: Scrivi i test che falliscono**

```python
# tests/test_giro.py
from datetime import datetime, timezone
import pytest
import giri
import giro
import lotto as lotto_mod
import registro
import stadio
from cliente_finto import ClienteFinto

_ADESSO = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)
_CFG = {"marketplace_id": "APJ6JRA9NG5V4",
        "indirizzo_origine": {"name": "Phy to Pet", "addressLine1": "Via Roma 1", "city": "Prato",
                              "postalCode": "59100", "countryCode": "IT", "phoneNumber": "1"},
        "contatto": {"name": "Phy to Pet", "phoneNumber": "1"}}


def _lotto(pallet=False):
    dati = {"cliente": "Phy to Pet",
            "righe": [{"sku": "SKU-1", "quantita": 30}],
            "scatole": [{"quantita": 1, "peso_kg": 8, "lunghezza_cm": 40, "larghezza_cm": 30, "altezza_cm": 25,
                         "righe": [{"sku": "SKU-1", "quantita": 30}]}],
            "data_pronta": "2026-09-05T08:00Z"}
    if pallet:
        dati["pallet"] = [{"quantity": 1, "stackability": "STACKABLE", "peso_kg": 100,
                           "lunghezza_cm": 120, "larghezza_cm": 80, "altezza_cm": 100}]
    return lotto_mod.da_dizionario(dati)


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(giro.clienti, "carica_cliente", lambda nome, percorso=None: _CFG)


def _voce(plan_id):
    locale = giri.carica(plan_id)
    data, pos, _ = locale["riferimento"].split("#")
    return registro.ultimo_lotto(locale["cliente"], data)[int(pos)]


def test_prepara_arriva_al_menu_senza_spendere():
    c = ClienteFinto()
    plan_id, testo = giro.prepara(c, _lotto(), adesso=_ADESSO)
    assert plan_id == "wf1"
    assert stadio.riconosci(stadio.leggi(c, plan_id), _ADESSO) == stadio.PIAZZAMENTO_DA_SCEGLIERE
    assert "pl1" in testo and "pl2" in testo               # tutte le divisioni
    assert "0.00 EUR" in testo and "4.00 EUR" in testo     # con la commissione di piazzamento
    assert "UPS" in testo and "7.90 EUR" in testo          # e i preventivi corriere
    assert "corriere proprio" in testo
    assert "dw1" in testo                                  # finestre del corriere proprio già nel menù
    assert not any(p.endswith("/confirmation") for p, _ in c.post_fatte)
    locale = giri.carica(plan_id)
    assert locale["cliente"] == "Phy to Pet" and locale["lotto"]["data_pronta"] == "2026-09-05T08:00Z"
    assert _voce(plan_id)["inbound_plan_id"] == "wf1"


def test_prepara_manda_contatto_e_pallet_ai_preventivi():
    c = ClienteFinto()
    giro.prepara(c, _lotto(pallet=True), adesso=_ADESSO)
    corpo = next(b for p, b in c.post_fatte if p.endswith("/transportationOptions"))
    conf = corpo["shipmentTransportationConfigurations"][0]
    assert conf["contactInformation"] == _CFG["contatto"]
    assert conf["pallets"][0]["stackability"] == "STACKABLE"
    assert conf["readyToShipWindow"] == {"start": "2026-09-05T08:00Z"}


def test_prepara_registra_l_errore_di_crea_piano_e_lo_rialza():
    from spapi_client import SpApiError
    class Rotto(ClienteFinto):
        def post(self, path, json_body=None, params=None):
            raise SpApiError("400 su /inboundPlans: WARNING: Address was unable to be verified")
    with pytest.raises(SpApiError, match="Address"):
        giro.prepara(Rotto(), _lotto(), adesso=_ADESSO)
    assert registro.ultimo_lotto("Phy to Pet")[-1]["esito"].startswith("errore in crea_piano: 400")


@pytest.mark.parametrize("da", [stadio.IMBALLAGGIO_DA_CONFERMARE, stadio.SCATOLE_DA_DICHIARARE,
                                stadio.PIAZZAMENTO_DA_GENERARE, stadio.PIAZZAMENTO_DA_SCEGLIERE])
def test_riprendi_da_ogni_stadio_prima_del_cancello_arriva_al_menu(da):
    c = ClienteFinto()
    plan_id = c.piano_a(da)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    testo = giro.riprendi(c, plan_id, adesso=_ADESSO)
    assert "UPS" in testo and "7.90 EUR" in testo
    assert stadio.riconosci(stadio.leggi(c, plan_id), _ADESSO) == stadio.PIAZZAMENTO_DA_SCEGLIERE
    assert not any(p.endswith("/placementOptions/pl1/confirmation") for p, _ in c.post_fatte)


def test_riprendi_rigenera_i_piazzamenti_scaduti_senza_ridichiarare_le_scatole():
    c = ClienteFinto(scadenza_piazzamento="2026-09-01T00:00:00.000Z")
    plan_id = c.piano_a(stadio.PIAZZAMENTO_DA_SCEGLIERE)   # con quella scadenza è DA_GENERARE
    c.scadenza_piazzamento = "2026-09-10T00:00:00.000Z"     # le nuove opzioni saranno valide
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    giro.riprendi(c, plan_id, adesso=_ADESSO)
    percorsi = [p for p, _ in c.post_fatte]
    assert percorsi.count(f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/placementOptions") == 1
    assert not any(p.endswith("/packingInformation") for p in percorsi)


def test_riprendi_senza_file_locale_pretende_il_lotto():
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.SCATOLE_DA_DICHIARARE)
    with pytest.raises(ValueError, match="nessun file locale.*--lotto"):
        giro.riprendi(c, plan_id, adesso=_ADESSO)
    testo = giro.riprendi(c, plan_id, lotto=_lotto(), adesso=_ADESSO)
    assert "UPS" in testo
    assert giri.carica(plan_id)["riferimento"]


def test_riprendi_con_piu_opzioni_di_imballaggio_si_ferma_e_le_elenca():
    class DueOpzioni(ClienteFinto):
        def get(self, path, params=None):
            r = super().get(path, params)
            if path.endswith("/packingOptions") and r["packingOptions"]:
                r["packingOptions"] = r["packingOptions"] + [{"packingOptionId": "po2", "status": "OFFERED", "packingGroups": ["pg1"]}]
            return r
    c = DueOpzioni()
    plan_id = c.piano_a(stadio.IMBALLAGGIO_DA_CONFERMARE)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    with pytest.raises(ValueError, match="po1.*po2.*--imballaggio"):
        giro.riprendi(c, plan_id, adesso=_ADESSO)
    assert "UPS" in giro.riprendi(c, plan_id, packing_option_id="po1", adesso=_ADESSO)


def test_riprendi_bloccato_da_esito_incerto_rifiuta_senza_verificato():
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.PIAZZAMENTO_DA_SCEGLIERE)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {},
                         "bloccato": "ESITO INCERTO sulla conferma del piazzamento pl1"})
    with pytest.raises(ValueError, match="ESITO INCERTO.*--verificato"):
        giro.riprendi(c, plan_id, adesso=_ADESSO)
    giro.riprendi(c, plan_id, verificato=True, adesso=_ADESSO)
    assert giri.carica(plan_id)["bloccato"] is None


def test_riprendi_a_trasporto_prenotato_lo_dice_e_non_scrive():
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.TRASPORTO_PRENOTATO)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    testo = giro.riprendi(c, plan_id, adesso=_ADESSO)
    assert "già prenotato" in testo and "conferma" in testo
    assert c.post_fatte == []
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest -q tests/test_giro.py`
Expected: `ModuleNotFoundError: No module named 'giro'`

- [ ] **Step 3: Scrivi `scripts/giro.py` (parte 1: prepara, riprendi, menù)**

```python
from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone

import anteprima
import clienti
import corrieri
import giri
import imballaggio
import lotto as lotto_mod
import piano as piano_mod
import piazzamento
import registro
import stadio
from modello import PianoSpedizione, Scatola
from spapi_client import SpApiError

_BASE = "/inbound/fba/2024-03-20/inboundPlans"


# ---------------------------------------------------------------- errori ----

def _fallito(plan_id: str | None, locale: dict | None, errore: Exception) -> None:
    """Ogni errore lascia traccia: nel registro sempre, nel file locale se è
    un esito incerto (§6): da lì in poi riprendi/conferma rifiutano finché
    Mariano non ha guardato Seller Central."""
    if locale:
        registro.registra_esito(locale["cliente"], locale["riferimento"], str(errore))
        if plan_id and "ESITO INCERTO" in str(errore):
            giri.aggiorna(plan_id, bloccato=str(errore))


def _controlla_blocco(plan_id: str, locale: dict, verificato: bool) -> None:
    if not locale.get("bloccato"):
        return
    if not verificato:
        raise ValueError(
            f"piano {plan_id} bloccato da un esito incerto: {locale['bloccato']}\n"
            "Verifica su Seller Central com'è finita, poi ripeti con --verificato"
        )
    giri.aggiorna(plan_id, bloccato=None)
    locale["bloccato"] = None


def _nuovo_locale(cliente: str, riferimento: str, lotto: lotto_mod.Lotto) -> dict:
    return {"cliente": cliente, "riferimento": riferimento,
            "lotto": lotto_mod.a_dizionario(lotto), "scelte": {}, "etichette": {}, "bloccato": None}


# --------------------------------------------------------------- prepara ----

def prepara(client, lotto: lotto_mod.Lotto, adesso: datetime | None = None) -> tuple[str, str]:
    """Passi 2-10 di SKILL.md: dal lotto al menù. Il lotto si registra PRIMA
    di creare il piano; il file locale si scrive appena Amazon torna il
    plan_id, così anche un crash subito dopo lascia un piano riprendibile."""
    cfg = clienti.carica_cliente(lotto.cliente)
    riferimento = registro.registra_tentativo(lotto.cliente, lotto.righe)
    try:
        piano = piano_mod.crea_piano(client, lotto.righe, cfg["indirizzo_origine"], cfg["marketplace_id"])
    except SpApiError as e:
        registro.registra_esito(lotto.cliente, riferimento, f"errore in crea_piano: {e}")
        raise
    plan_id = piano.inbound_plan_id
    giri.salva(plan_id, _nuovo_locale(lotto.cliente, riferimento, lotto))
    registro.registra_piano(lotto.cliente, riferimento, plan_id, [])
    return plan_id, riprendi(client, plan_id, adesso=adesso)


# -------------------------------------------------------------- riprendi ----

def riprendi(client, plan_id: str, lotto: lotto_mod.Lotto | None = None,
             packing_option_id: str | None = None, verificato: bool = False,
             adesso: datetime | None = None) -> str:
    """Legge lo stadio da Amazon e fa solo ciò che manca fino al menù. Le
    opzioni scadute si rigenerano da sole: non costano."""
    locale = giri.carica(plan_id)
    if locale is None:
        if lotto is None:
            raise ValueError(
                f"nessun file locale per {plan_id} (piano non creato da giro.py): "
                "passa --lotto lotto.yaml con scatole e data pronta"
            )
        riferimento = registro.registra_tentativo(lotto.cliente, lotto.righe)
        registro.registra_piano(lotto.cliente, riferimento, plan_id, [])
        locale = _nuovo_locale(lotto.cliente, riferimento, lotto)
        giri.salva(plan_id, locale)
    _controlla_blocco(plan_id, locale, verificato)
    lotto = lotto_mod.da_dizionario(locale["lotto"])
    cfg = clienti.carica_cliente(locale["cliente"])
    adesso = adesso or datetime.now(timezone.utc)
    try:
        return _fino_al_menu(client, plan_id, locale, lotto, cfg, adesso, packing_option_id)
    except SpApiError as e:
        _fallito(plan_id, locale, e)
        raise


def _fino_al_menu(client, plan_id, locale, lotto, cfg, adesso, packing_option_id) -> str:
    foto = stadio.leggi(client, plan_id)
    st = stadio.riconosci(foto, adesso, etichette_scaricate=bool(locale.get("etichette")))
    gruppi: list[str] = stadio.gruppi_imballaggio_accettati(foto)

    if st == stadio.IMBALLAGGIO_DA_CONFERMARE:
        opzioni = imballaggio.genera_opzioni_imballaggio(client, plan_id)
        scelta = _scegli_imballaggio(opzioni, packing_option_id)
        imballaggio.conferma_imballaggio(client, plan_id, scelta.packing_option_id)
        giri.aggiorna(plan_id, scelte={**locale["scelte"], "packing_option_id": scelta.packing_option_id})
        gruppi = scelta.packing_group_ids
        st = stadio.SCATOLE_DA_DICHIARARE

    if st == stadio.SCATOLE_DA_DICHIARARE:
        per_gruppo = _assegna_scatole(client, plan_id, gruppi, lotto.scatole)
        imballaggio.imposta_informazioni_imballaggio(client, plan_id, per_gruppo, cfg["marketplace_id"])
        st = stadio.PIAZZAMENTO_DA_GENERARE

    if st == stadio.PIAZZAMENTO_DA_GENERARE:
        opzioni_piazzamento = piazzamento.genera_opzioni_piazzamento(client, plan_id)
    elif st == stadio.PIAZZAMENTO_DA_SCEGLIERE:
        opzioni_piazzamento = _da_amazon(stadio.piazzamenti_offerti_validi(foto, adesso))
    elif st == stadio.TRASPORTO_DA_SCEGLIERE:
        opzioni_piazzamento = _da_amazon([stadio.piazzamento_accettato(foto)])
    elif st == stadio.TRASPORTO_PRENOTATO:
        return (f"Piano {plan_id}: corriere già prenotato. Per le etichette: "
                f"giro.py conferma {plan_id} --vai")
    else:
        return f"Piano {plan_id}: completato, etichette in {locale.get('etichette')}"

    return _menu(client, plan_id, opzioni_piazzamento, lotto, cfg, st == stadio.TRASPORTO_DA_SCEGLIERE)


def _scegli_imballaggio(opzioni, packing_option_id):
    if packing_option_id:
        for o in opzioni:
            if o.packing_option_id == packing_option_id:
                return o
        raise ValueError(f"opzione di imballaggio {packing_option_id} non tra quelle offerte")
    if len(opzioni) == 1:
        return opzioni[0]
    elenco = "; ".join(f"{o.packing_option_id} ({o.descrizione})" for o in opzioni)
    raise ValueError(f"Amazon offre più imballaggi: {elenco}. Scegli con --imballaggio <id>")


def _assegna_scatole(client, plan_id, gruppi: list[str], scatole: list[Scatola]) -> dict[str, list[Scatola]]:
    """Con un gruppo solo tutte le scatole vanno lì; con più gruppi si
    assegnano per SKU, e una scatola che mischia gruppi ferma il giro."""
    if not gruppi:
        raise ValueError(f"piano {plan_id}: imballaggio accettato ma senza gruppi")
    if len(gruppi) == 1:
        return {gruppi[0]: list(scatole)}
    gruppo_di = {sku: g for g in gruppi for sku in imballaggio.leggi_articoli_gruppo(client, plan_id, g)}
    per_gruppo: dict[str, list[Scatola]] = {g: [] for g in gruppi}
    for i, s in enumerate(scatole, start=1):
        destinazioni = {gruppo_di.get(r.sku) for r in s.righe}
        if len(destinazioni) != 1 or None in destinazioni:
            raise ValueError(
                f"la scatola {i} contiene SKU di gruppi di imballaggio diversi "
                f"({', '.join(r.sku for r in s.righe)}): Amazon li vuole in scatole separate"
            )
        per_gruppo[destinazioni.pop()].append(s)
    return per_gruppo


def _da_amazon(opzioni_grezze: list[dict]):
    """Le stesse OpzionePiazzamento che costruisce piazzamento.genera_opzioni_piazzamento,
    ma da opzioni già lette (nessuna POST)."""
    from modello import OpzionePiazzamento
    risultato = []
    for o in opzioni_grezze:
        commissioni = [f for f in (o.get("fees") or []) if (f.get("value") or {}).get("amount") is not None]
        risultato.append(OpzionePiazzamento(
            placement_option_id=o["placementOptionId"],
            shipment_ids=list(o.get("shipmentIds") or []),
            commissioni=sum(f["value"]["amount"] for f in commissioni) if commissioni else None,
            valuta=commissioni[0]["value"].get("code", "EUR") if commissioni else "EUR",
        ))
    return risultato


def _menu(client, plan_id, opzioni_piazzamento, lotto, cfg, piazzamento_gia_confermato: bool) -> str:
    pallet = lotto_mod.pallet_amazon(lotto) or None
    blocchi = []
    for op in opzioni_piazzamento:
        spedizioni = piazzamento.leggi_spedizioni(client, plan_id, op.shipment_ids)
        opzioni = corrieri.genera_opzioni_trasporto(
            client, plan_id, op.placement_option_id, op.shipment_ids, lotto.data_pronta,
            contatto=cfg["contatto"], pallet=pallet)
        finestre_testo = []
        for s in spedizioni:
            s.opzioni_trasporto = [o for o in opzioni if o.shipment_id == s.shipment_id]
            if any(o.richiede_finestra_consegna for o in s.opzioni_trasporto):
                # SKILL.md passo 9: le finestre si generano qui, così `conferma`
                # senza --vai non fa nessuna POST
                finestre = corrieri.genera_finestre_consegna(client, plan_id, s.shipment_id)
                finestre_testo.append(f"  finestre di consegna per {s.shipment_id} (corriere proprio): " + ", ".join(
                    f"{f.delivery_window_option_id} {f.inizio}→{f.fine}" for f in finestre))
        commissione = ("nessuna commissione quotata" if op.commissioni is None
                       else f"{op.commissioni:.2f} {op.valuta}")
        stato = "CONFERMATA" if piazzamento_gia_confermato else "offerta"
        blocchi.append(
            f"Divisione {op.placement_option_id} ({stato}) — commissione di piazzamento: {commissione}\n"
            + anteprima.riepilogo(PianoSpedizione(plan_id, spedizioni, placement_option_id=op.placement_option_id))
            + ("\n" + "\n".join(finestre_testo) if finestre_testo else "")
        )
    esempio = opzioni_piazzamento[0]
    sid = esempio.shipment_ids[0] if esempio.shipment_ids else "<shipment_id>"
    return "\n\n".join(blocchi) + (
        f"\n\nNessuna spesa finora. Per prenotare:\n"
        f"  giro.py conferma {plan_id} --piazzamento {esempio.placement_option_id} "
        f"--trasporto {sid}=<transportation_option_id> [--finestra {sid}=<id>] --vai"
    )
```

`anteprima._prezzo` stampa `f"{costo:.2f} {valuta}"` per i preventivi: verifica la forma con `sed -n 5,12p scripts/anteprima.py` e adegua l'assert `"7.90 EUR"` del test se il formato differisce (non cambiare `anteprima`).

- [ ] **Step 4: Verifica che passino**

Run: `python3 -m pytest -q tests/test_giro.py`
Expected: 12 passed (i test di `conferma` arrivano nel Task 7).

- [ ] **Step 5: Commit**

```bash
git add scripts/giro.py tests/test_giro.py
git commit -m "feat(orchestratore): prepara e riprendi portano il piano al menù corrieri"
```

---

### Task 7: `giro.conferma` — il cancello

**Files:**
- Modify: `scripts/giro.py` (in coda, prima del CLI)
- Test: `tests/test_giro.py` (in coda)

**Interfaces:**
- Consumes: `corrieri.leggi_opzioni_trasporto`, `corrieri.genera_finestre_consegna`, `conferma.conferma_finestra_consegna/conferma_trasporto`, `piazzamento.conferma_piazzamento/leggi_spedizioni`, `etichette.leggi_cartoni/scarica_etichette`, `registro.*`, `anteprima.riepilogo_conferma`.
- Produces: `conferma(client, plan_id: str, placement_option_id: str | None, trasporto: dict[str, str], finestre: dict[str, str] | None = None, vai: bool = False, pallet_etichette: int | None = None, verificato: bool = False, adesso: datetime | None = None) -> str`.

- [ ] **Step 1: Scrivi i test che falliscono** (in coda a `tests/test_giro.py`)

```python
def _pronto(c, da=stadio.PIAZZAMENTO_DA_SCEGLIERE):
    plan_id = c.piano_a(da)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    registro.registra_piano("Phy to Pet", giri.carica(plan_id)["riferimento"], plan_id, [])
    giro.riprendi(c, plan_id, adesso=_ADESSO)      # genera i preventivi
    c.post_fatte.clear()
    return plan_id


def test_conferma_senza_vai_mostra_il_cancello_e_non_fa_nessuna_post():
    c = ClienteFinto()
    plan_id = _pronto(c)
    testo = giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, adesso=_ADESSO)
    assert "Sto per prenotare" in testo and "UPS" in testo and "7.90 EUR" in testo
    assert "commissione di piazzamento" in testo.lower()
    assert '"vai"' in testo
    assert c.post_fatte == []


def test_conferma_con_vai_spende_nell_ordine_di_skill_md_e_scarica_le_etichette(monkeypatch):
    from unittest.mock import Mock
    pdf = Mock(status_code=200, content=b"%PDF-1.4 finto")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, timeout: pdf)
    c = ClienteFinto()
    plan_id = _pronto(c)
    testo = giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    percorsi = [p for p, _ in c.post_fatte]
    assert percorsi == [f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/placementOptions/pl1/confirmation",
                        f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/transportationOptions/confirmation"]
    voce = _voce(plan_id)
    assert voce["piazzamento_confermato"]["placement_option_id"] == "pl1"
    assert voce["spedizioni"][0]["shipment_confirmation_id"] == "FBA1234sh1"
    assert voce["spedizioni"][0]["trasporto_confermato"]["transportation_option_id"] == "to-ups-sh1"
    assert voce["spedizioni"][0]["etichette_path"].endswith("Etichette-Phy to Pet-FBA1234sh1.pdf")
    assert voce["esito"] == "completato"
    assert giri.carica(plan_id)["etichette"]["sh1"][0].endswith("FBA1234sh1.pdf")
    assert "FBA1234sh1" in testo and "Etichette" in testo
    assert stadio.riconosci(stadio.leggi(c, plan_id), _ADESSO, etichette_scaricate=True) == stadio.COMPLETATO


def test_conferma_registra_la_commissione_prima_di_qualunque_altra_lettura(monkeypatch):
    """Passo 13 di SKILL.md: la commissione è già spesa quando si rileggono
    le spedizioni; se la rilettura fallisce, la riga della spesa deve esserci."""
    from spapi_client import SpApiError
    class RiletturaRotta(ClienteFinto):
        def get(self, path, params=None):
            p = self.piani.get("wf1", {})
            if path.endswith("/shipments/sh1") and p.get("spedizioni", {}).get("sh1", {}).get("status") == "WORKING":
                raise SpApiError("errore di rete su GET dopo 5 tentativi")
            return super().get(path, params)
    c = RiletturaRotta()
    plan_id = _pronto(c)
    with pytest.raises(SpApiError, match="errore di rete"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    voce = _voce(plan_id)
    assert voce["piazzamento_confermato"]["placement_option_id"] == "pl1"
    assert voce["esito"].startswith("errore di rete")


def test_conferma_del_corriere_proprio_pretende_la_finestra_e_la_conferma_prima(monkeypatch):
    pdf = __import__("unittest.mock").mock.Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, timeout: pdf)
    c = ClienteFinto()
    plan_id = _pronto(c)
    with pytest.raises(ValueError, match="finestra di consegna.*sh1.*--finestra"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-proprio-sh1"}, adesso=_ADESSO)
    giro.conferma(c, plan_id, "pl1", {"sh1": "to-proprio-sh1"}, finestre={"sh1": "dw1"}, vai=True, adesso=_ADESSO)
    percorsi = [p for p, _ in c.post_fatte]
    i_finestra = next(i for i, p in enumerate(percorsi) if p.endswith("/deliveryWindowOptions/dw1/confirmation"))
    i_trasporto = next(i for i, p in enumerate(percorsi) if p.endswith("/transportationOptions/confirmation"))
    assert i_finestra < i_trasporto


def test_conferma_rifiuta_un_preventivo_scaduto_e_rimanda_a_riprendi():
    c = ClienteFinto(scadenza_preventivi="2026-09-01T00:00:00.000Z")
    plan_id = _pronto(c)
    with pytest.raises(ValueError, match="to-ups-sh1.*scaduto.*riprendi"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    assert c.post_fatte == []


def test_conferma_rifiuta_un_trasporto_che_non_e_tra_le_opzioni():
    c = ClienteFinto()
    plan_id = _pronto(c)
    with pytest.raises(ValueError, match="to-inventato.*sh1"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-inventato"}, adesso=_ADESSO)


def test_esito_incerto_sul_trasporto_blocca_il_piano(monkeypatch):
    from spapi_client import SpApiError
    class AttesaRotta(ClienteFinto):
        def attendi_operazione(self, operation_id, attesa_max_s=300):
            if self.post_fatte and self.post_fatte[-1][0].endswith("/transportationOptions/confirmation"):
                raise SpApiError("operazione non conclusa dopo 300s")
            return super().attendi_operazione(operation_id, attesa_max_s)
    c = AttesaRotta()
    plan_id = _pronto(c)
    with pytest.raises(SpApiError, match="ESITO INCERTO"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    assert giri.carica(plan_id)["bloccato"].startswith("ESITO INCERTO")
    assert _voce(plan_id)["esito"].startswith("ESITO INCERTO")
    with pytest.raises(ValueError, match="--verificato"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)


def test_conferma_a_trasporto_prenotato_scarica_solo_le_etichette(monkeypatch):
    pdf = __import__("unittest.mock").mock.Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, timeout: pdf)
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.TRASPORTO_PRENOTATO)
    rif = registro.registra_tentativo("Phy to Pet", _lotto().righe)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": rif, "lotto": lotto_mod.a_dizionario(_lotto()),
                         "scelte": {}, "etichette": {}, "bloccato": None})
    registro.registra_piano("Phy to Pet", rif, plan_id, [])
    testo = giro.conferma(c, plan_id, None, {}, vai=True, pallet_etichette=2, adesso=_ADESSO)
    assert c.post_fatte == []
    assert len(giri.carica(plan_id)["etichette"]["sh1"]) == 2       # cartoni + pallet
    assert "Pallet" in giri.carica(plan_id)["etichette"]["sh1"][1]
```

Nota: `scarica_etichette` salva sempre in `~/Desktop/Etichette-<cliente>-<conf>.pdf`, per cartoni e pallet. `_etichette` scarica **prima** il PDF pallet e lo rinomina in `...-Pallet.pdf`, poi quello dei cartoni.

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest -q tests/test_giro.py -k conferma`
Expected: `AttributeError: module 'giro' has no attribute 'conferma'`

- [ ] **Step 3: Aggiungi `conferma` a `scripts/giro.py`**

Aggiungi in testa agli import: `import os`, `import conferma as conferma_mod`, `import etichette`.

```python
# -------------------------------------------------------------- conferma ----

def conferma(client, plan_id: str, placement_option_id: str | None, trasporto: dict[str, str],
             finestre: dict[str, str] | None = None, vai: bool = False,
             pallet_etichette: int | None = None, verificato: bool = False,
             adesso: datetime | None = None) -> str:
    """Il cancello 🟡 (SKILL.md passi 10-17). Senza `vai` stampa il testo di
    `riepilogo_conferma` e NON fa nessuna POST. Con `vai` spende, nell'ordine
    fissato: piazzamento → registro → rilettura → finestre → trasporto →
    registro → etichette → esito."""
    finestre = finestre or {}
    locale = giri.carica(plan_id)
    if locale is None:
        raise ValueError(f"nessun file locale per {plan_id}: prima `giro.py riprendi {plan_id} --lotto lotto.yaml`")
    _controlla_blocco(plan_id, locale, verificato)
    cliente, rif = locale["cliente"], locale["riferimento"]
    cfg = clienti.carica_cliente(cliente)
    adesso = adesso or datetime.now(timezone.utc)
    foto = stadio.leggi(client, plan_id)
    st = stadio.riconosci(foto, adesso, etichette_scaricate=bool(locale.get("etichette")))

    try:
        if st == stadio.TRASPORTO_PRENOTATO:
            accettato = stadio.piazzamento_accettato(foto)
            spedizioni = piazzamento.leggi_spedizioni(client, plan_id, accettato["shipmentIds"])
            # registra_etichette cerca la spedizione nella voce: le rilette vanno scritte prima
            registro.registra_piano(cliente, rif, plan_id, spedizioni)
            return _etichette(client, plan_id, locale, spedizioni, pallet_etichette)
        if st not in (stadio.PIAZZAMENTO_DA_SCEGLIERE, stadio.TRASPORTO_DA_SCEGLIERE):
            raise ValueError(f"il piano {plan_id} è allo stadio {st}: prima `giro.py riprendi {plan_id}`")

        # --- l'opzione di piazzamento, valida e coerente con lo stadio ---
        if st == stadio.TRASPORTO_DA_SCEGLIERE:
            grezza = stadio.piazzamento_accettato(foto)
            if placement_option_id and placement_option_id != grezza["placementOptionId"]:
                raise ValueError(f"il piazzamento è già confermato: {grezza['placementOptionId']}, non {placement_option_id}")
        else:
            valide = {o["placementOptionId"]: o for o in stadio.piazzamenti_offerti_validi(foto, adesso)}
            if placement_option_id not in valide:
                raise ValueError(
                    f"piazzamento {placement_option_id} non tra le opzioni valide "
                    f"({', '.join(valide) or 'nessuna'}): rifai `giro.py riprendi {plan_id}`"
                )
            grezza = valide[placement_option_id]
        op = _da_amazon([grezza])[0]

        # --- le opzioni di trasporto scelte, una per spedizione, non scadute ---
        spedizioni = piazzamento.leggi_spedizioni(client, plan_id, op.shipment_ids)
        disponibili = corrieri.leggi_opzioni_trasporto(client, plan_id, op.placement_option_id)
        scelte: dict = {}
        for s in spedizioni:
            voluta = trasporto.get(s.shipment_id)
            candidata = next((o for o in disponibili
                              if o.shipment_id == s.shipment_id and o.transportation_option_id == voluta), None)
            if candidata is None:
                offerte = ", ".join(o.transportation_option_id for o in disponibili if o.shipment_id == s.shipment_id)
                raise ValueError(f"trasporto {voluta!r} non tra le opzioni di {s.shipment_id} ({offerte or 'nessuna'})")
            if stadio.scaduta(candidata.scadenza, adesso):
                raise ValueError(f"il preventivo {voluta} di {s.shipment_id} è scaduto: rifai `giro.py riprendi {plan_id}`")
            if candidata.richiede_finestra_consegna and s.shipment_id not in finestre:
                raise ValueError(
                    f"{voluta} richiede una finestra di consegna per {s.shipment_id}: "
                    f"scegline una tra quelle del menù con --finestra {s.shipment_id}=<id>"
                )
            scelte[s.shipment_id] = candidata

        commissione = "nessuna commissione quotata" if op.commissioni is None else f"{op.commissioni:.2f} {op.valuta}"
        testo = "\n".join(anteprima.riepilogo_conferma(s, scelte[s.shipment_id]) for s in spedizioni)
        testo += f"\nCommissione di piazzamento ({op.placement_option_id}): {commissione}"
        if not vai:
            return testo + "\n(anteprima: nessuna scrittura fatta; ripeti con --vai per prenotare)"

        # ------------------------------ da qui si spende ------------------------------
        if st == stadio.PIAZZAMENTO_DA_SCEGLIERE:
            piazzamento.conferma_piazzamento(client, plan_id, op.placement_option_id)
            registro.registra_piazzamento_confermato(cliente, rif, op.placement_option_id, op.commissioni, op.valuta)
            giri.aggiorna(plan_id, scelte={**locale["scelte"], "placement_option_id": op.placement_option_id})
        spedizioni = piazzamento.leggi_spedizioni(client, plan_id, op.shipment_ids)
        registro.registra_piano(cliente, rif, plan_id, spedizioni)
        for sid, dw in finestre.items():
            conferma_mod.conferma_finestra_consegna(client, plan_id, sid, dw)
        conferma_mod.conferma_trasporto(client, plan_id, list(scelte.values()), contatto=cfg["contatto"])
        for s in spedizioni:
            s.trasporto_confermato = scelte[s.shipment_id]
        registro.registra_trasporto_confermato(cliente, rif, scelte)
        giri.aggiorna(plan_id, scelte={**giri.carica(plan_id)["scelte"],
                                       "trasporto": {sid: o.transportation_option_id for sid, o in scelte.items()},
                                       "finestre": finestre})
        return testo + "\n" + _etichette(client, plan_id, locale, spedizioni, pallet_etichette)
    except SpApiError as e:
        _fallito(plan_id, locale, e)
        raise


def _etichette(client, plan_id, locale, spedizioni, pallet_etichette) -> str:
    cliente, rif = locale["cliente"], locale["riferimento"]
    scaricate: dict[str, list[str]] = {}
    for s in spedizioni:
        percorso_pallet = None
        if pallet_etichette:
            # scarica_etichette salva sempre con lo stesso nome: il PDF pallet si
            # scarica PRIMA e si rinomina subito, così i cartoni non lo sovrascrivono
            temporaneo = etichette.scarica_etichette(client, s.shipment_confirmation_id, cliente,
                                                     tipo_etichetta="PALLET", numero_pallet=pallet_etichette)
            percorso_pallet = temporaneo.replace(".pdf", "-Pallet.pdf")
            os.replace(temporaneo, percorso_pallet)
        cartoni = etichette.leggi_cartoni(client, plan_id, s.shipment_id)
        percorso = etichette.scarica_etichette(client, s.shipment_confirmation_id, cliente, cartoni=cartoni)
        registro.registra_etichette(cliente, rif, s.shipment_id, s.shipment_confirmation_id, percorso)
        scaricate[s.shipment_id] = [percorso] + ([percorso_pallet] if percorso_pallet else [])
    giri.aggiorna(plan_id, etichette=scaricate)
    registro.registra_esito(cliente, rif, "completato")
    righe = [f"Prenotato. Spedizione {s.shipment_id} → {s.centro_logistico}: {s.shipment_confirmation_id}" for s in spedizioni]
    righe += [f"Etichette: {p}" for elenco in scaricate.values() for p in elenco]
    return "\n".join(righe)
```

- [ ] **Step 4: Verifica che passino, tutta la suite**

Run: `python3 -m pytest -q`
Expected: tutti passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/giro.py tests/test_giro.py
git commit -m "feat(orchestratore): conferma — cancello con --vai, ordine di spesa di SKILL.md, etichette"
```

---

### Task 8: CLI `main(argv)` + SKILL.md + PLAN.md

**Files:**
- Modify: `scripts/giro.py` (in coda)
- Test: `tests/test_giro.py` (in coda)
- Modify: `SKILL.md` (nuova sezione "Come si usa" in testa alla Procedura), `PLAN.md` (Task 11: nuovo Step 7)

**Interfaces:**
- Produces: `main(argv: list[str] | None = None) -> int`. Sottocomandi: `prepara <lotto.yaml>`; `riprendi <plan_id> [--lotto F] [--imballaggio ID] [--verificato]`; `conferma <plan_id> [--piazzamento ID] [--trasporto SID=TOID ...] [--finestra SID=DWID ...] [--vai] [--pallet N] [--verificato]`. Stampa il testo su stdout; su `ValueError`/`SpApiError` stampa `ERRORE: <messaggio>` su stderr e torna 1. Il client viene da `spapi_client.crea_client(cliente)`: per `prepara` il cliente è nel lotto, per gli altri nel file locale (o in `--lotto`).

- [ ] **Step 1: Scrivi i test che falliscono** (in coda a `tests/test_giro.py`)

```python
def test_cli_prepara_riprendi_conferma(tmp_path, monkeypatch, capsys):
    import yaml
    c = ClienteFinto()
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: c)
    f = tmp_path / "lotto.yaml"
    f.write_text(yaml.safe_dump(lotto_mod.a_dizionario(_lotto())))
    assert giro.main(["prepara", str(f)]) == 0
    out = capsys.readouterr().out
    assert "wf1" in out and "UPS" in out
    assert giro.main(["riprendi", "wf1"]) == 0
    assert "UPS" in capsys.readouterr().out
    assert giro.main(["conferma", "wf1", "--piazzamento", "pl1", "--trasporto", "sh1=to-ups-sh1"]) == 0
    out = capsys.readouterr().out
    assert "Sto per prenotare" in out and "nessuna scrittura" in out
    assert not any(p.endswith("/confirmation") for p, _ in c.post_fatte)


def test_cli_errore_va_su_stderr_con_codice_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: ClienteFinto())
    assert giro.main(["riprendi", "wf-ignoto"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERRORE:") and "nessun file locale" in err


def test_cli_conferma_con_vai_passa_vai_e_pallet(monkeypatch):
    chiamate = {}
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: ClienteFinto())
    giri.salva("wf1", {"cliente": "Phy to Pet", "riferimento": "x", "lotto": {}, "scelte": {}, "etichette": {}, "bloccato": None})
    monkeypatch.setattr(giro, "conferma", lambda client, plan_id, po, tr, **k: chiamate.update(po=po, tr=tr, **k) or "ok")
    assert giro.main(["conferma", "wf1", "--piazzamento", "pl1", "--trasporto", "sh1=to1", "--trasporto", "sh2=to2",
                      "--finestra", "sh1=dw1", "--vai", "--pallet", "2", "--verificato"]) == 0
    assert chiamate["po"] == "pl1" and chiamate["tr"] == {"sh1": "to1", "sh2": "to2"}
    assert chiamate["finestre"] == {"sh1": "dw1"} and chiamate["vai"] is True
    assert chiamate["pallet_etichette"] == 2 and chiamate["verificato"] is True
```

- [ ] **Step 2: Verifica che falliscano**

Run: `python3 -m pytest -q tests/test_giro.py -k cli`
Expected: `AttributeError: module 'giro' has no attribute 'main'`

- [ ] **Step 3: Aggiungi il CLI in coda a `scripts/giro.py`**

Aggiungi agli import: `import spapi_client`.

```python
# ------------------------------------------------------------------- CLI ----

def _coppie(valori: list[str] | None, nome: str) -> dict[str, str]:
    coppie = {}
    for v in valori or []:
        if "=" not in v:
            raise ValueError(f"--{nome} vuole SHIPMENT_ID=ID, ricevuto {v!r}")
        k, val = v.split("=", 1)
        coppie[k.strip()] = val.strip()
    return coppie


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="giro.py", description="Spedizioni FBA: prepara / riprendi / conferma")
    sub = p.add_subparsers(dest="verbo", required=True)
    a = sub.add_parser("prepara", help="dal lotto.yaml al menù corrieri, senza spendere")
    a.add_argument("lotto")
    b = sub.add_parser("riprendi", help="riconosce lo stadio su Amazon e arriva al menù")
    b.add_argument("plan_id")
    b.add_argument("--lotto", help="lotto.yaml, se il piano non è stato creato da giro.py")
    b.add_argument("--imballaggio", help="packing option da usare se Amazon ne offre più d'una")
    b.add_argument("--verificato", action="store_true", help="sblocca un esito incerto già controllato su Seller Central")
    c = sub.add_parser("conferma", help="mostra il cancello; con --vai prenota e scarica le etichette")
    c.add_argument("plan_id")
    c.add_argument("--piazzamento")
    c.add_argument("--trasporto", action="append", metavar="SHIPMENT_ID=TRANSPORTATION_OPTION_ID")
    c.add_argument("--finestra", action="append", metavar="SHIPMENT_ID=DELIVERY_WINDOW_OPTION_ID")
    c.add_argument("--vai", action="store_true", help="da qui si spende")
    c.add_argument("--pallet", type=int, help="quante etichette pallet stampare")
    c.add_argument("--verificato", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.verbo == "prepara":
            lotto = lotto_mod.carica(args.lotto)
            plan_id, testo = prepara(spapi_client.crea_client(lotto.cliente), lotto)
            print(f"Piano creato: {plan_id}\n{testo}")
            return 0
        locale = giri.carica(args.plan_id)
        lotto = lotto_mod.carica(args.lotto) if getattr(args, "lotto", None) else None
        cliente = locale["cliente"] if locale else (lotto.cliente if lotto else None)
        if not cliente:
            raise ValueError(f"nessun file locale per {args.plan_id}: passa --lotto lotto.yaml")
        client = spapi_client.crea_client(cliente)
        if args.verbo == "riprendi":
            print(riprendi(client, args.plan_id, lotto=lotto, packing_option_id=args.imballaggio,
                           verificato=args.verificato))
        else:
            print(conferma(client, args.plan_id, args.piazzamento, _coppie(args.trasporto, "trasporto"),
                           finestre=_coppie(args.finestra, "finestra"), vai=args.vai,
                           pallet_etichette=args.pallet, verificato=args.verificato))
        return 0
    except (ValueError, SpApiError) as e:
        print(f"ERRORE: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Verifica che passino, tutta la suite**

Run: `python3 -m pytest -q`
Expected: tutti passed.

- [ ] **Step 5: SKILL.md — sezione "Come si usa" (prima di "## Procedura")**

```markdown
## Come si usa

Tre comandi, da `scripts/`, con il client preso da `crea_client` e indirizzo/marketplace/
contatto da `clienti.yaml`:

    python3 giro.py prepara lotto.yaml                     # → menù corrieri, nessuna spesa
    python3 giro.py riprendi <plan_id> [--lotto lotto.yaml] # piano esistente → stesso menù
    python3 giro.py conferma <plan_id> --piazzamento P --trasporto SID=TOID [--finestra SID=DW]
    python3 giro.py conferma ... --vai [--pallet N]         # 🟡 prenota e scarica le etichette

`lotto.yaml` (forma completa in `DESIGN.md` §12): cliente, righe SKU+quantità, scatole con
contenuto/misure/peso, `data_pronta` come istante UTC, pallet facoltativi. Il `--vai` lo
passa NOVA **solo** dopo il "vai" di Mariano sul testo stampato da `conferma` senza `--vai`.
Un "ESITO INCERTO" blocca il piano finché non si ripete con `--verificato`.

La procedura qui sotto è ciò che `giro.py` esegue: resta la referenza per i singoli passi.
```

- [ ] **Step 6: PLAN.md — Task 11, nuovo step in coda**

```markdown
- [ ] **Step 7 (02/09): `python3 giro.py riprendi wf0dae3b7c-919c-4efb-8761-c93d7126908f --lotto lotto.yaml`**
  su Phy to Pet, col lotto da 20 DENTIFRICIO_SPRAY_DUE in 2 scatole (10×25×15 cm, 0,8 kg) e
  data pronta a scelta di Mariano. Atteso: stadio PIAZZAMENTO_DA_GENERARE riconosciuto,
  piazzamenti e preventivi rigenerati, menù con UPS convenzionato e corriere proprio,
  **nessuna POST di conferma**. È il piano che serve allo Step 4 (corriere convenzionato).
```

- [ ] **Step 7: Commit**

```bash
git add scripts/giro.py tests/test_giro.py SKILL.md PLAN.md
git commit -m "feat(orchestratore): CLI giro.py prepara/riprendi/conferma + SKILL.md"
```

---

### Task 9: collaudo dal vivo di `riprendi` (sola preparazione, nessuna spesa)

**Files:** nessuna modifica di codice prevista; se il vivo diverge, correggere modulo + test e committare citando cosa e perché.

- [x] **Step 1: Scrivi `~/Desktop/lotto-phytopet-test.yaml`** con: cliente `Phy to Pet`, righe `DENTIFRICIO_SPRAY_DUE` 20, due scatole `{quantita: 1, peso_kg: 0.8, lunghezza_cm: 10, larghezza_cm: 25, altezza_cm: 15, righe: [{sku: DENTIFRICIO_SPRAY_DUE, quantita: 10}]}`, `data_pronta` = un istante nei prossimi 7 giorni (chiedere a Mariano), senza pallet.
- [x] **Step 2: Sonda a vuoto** — `python3 -c "import stadio, spapi_client; ..."` che chiama solo `stadio.leggi` + `stadio.riconosci` sul piano `wf0dae3b7c...`: atteso `PIAZZAMENTO_DA_GENERARE`. Sandbox Bash disattivato (rete).
- [x] **Step 3: `python3 giro.py riprendi wf0dae3b7c-919c-4efb-8761-c93d7126908f --lotto ~/Desktop/lotto-phytopet-test.yaml`** — leggere il menù, confrontare con il registro del 29/08 (UPS ~7,90 EUR, corriere proprio, commissione 0). Verificare su `~/.config/newera-amazon/spedizioni/giri/wf0dae3b7c….json` che il file locale esista.
- [x] **Step 4: `python3 giro.py conferma <plan_id> --piazzamento <P> --trasporto <sid>=<UPS>`** **senza `--vai`**: leggere il testo del cancello e fermarsi. Riportare a Mariano. Da qui in poi è lo Step 4 del Task 11 (corriere convenzionato) e si fa solo col suo "vai".
- [x] **Step 5 (2026-09-05): memoria di progetto scritta** — `project_amazon_spedizioni.md` (mancava: gli Step 1-4 erano stati fatti il 02-03/09 senza annotarli). Nessuna correzione di codice da committare: il vivo non ha divergito dalle fixture.
