# Stock e riassortimento — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** avvisare per mail quando uno SKU di un cliente va rifornito in FBA, con la bozza di lotto già pronta quando il cliente spedisce sempre uguale.

**Architecture:** un giro deterministico (niente `claude -p`: la logica è tutta specificata) legge una volta al giorno il report Restock di ogni cliente, classifica ogni SKU in `ok` / `riassortire` / `critico`, confronta con lo stato del giorno prima e manda **una mail per cliente** solo sulle transizioni. I modelli d'imballaggio veri del cliente, letti dai piani spediti, scelgono la scatola; `scatole.bozza_lotto` — già collaudato — scrive il file.

**Tech Stack:** Python 3.9 stdlib + `PyYAML`, `spapi_client.SpApiClient` (SP-API Reports 2021-06-30 e Fulfillment Inbound 2024-03-20), `pytest`, LaunchAgent macOS, SMTP IONOS.

**Spec:** `DESIGN.md` §14 (leggerla prima di iniziare: contiene i numeri veri su cui il piano è tarato).

## Global Constraints

- **Sola lettura verso Amazon.** L'unica POST ammessa è `POST /reports` (crea un report, non tocca l'account). Nessun piano, nessuna scatola, nessuna prenotazione. Unica eccezione dichiarata: il Task 9, che aggiunge un campo a una POST già esistente.
- **Nessuna chiamata vera nei test.** Si usa `tests/cliente_finto.py`, esteso nel Task 1. Nessun `time.sleep` reale: la pausa del polling è un parametro iniettabile.
- **Orologio iniettato.** Ogni funzione che dipende dal tempo prende `adesso: datetime` esplicito, come `scatole.leggi_storico` e `giro.scatole`. Mai `datetime.now()` dentro la logica.
- **Italiano** nei nomi di funzioni, variabili, messaggi e commenti, come tutto il resto della skill.
- **Segreti fuori dal repo.** La password SMTP sta in `~/.config/newera-amazon/spedizioni/mail.yaml`, mai in git, mai in chat.
- **Stato** in `~/.config/newera-amazon/spedizioni/stock/<cliente>.json`, accanto a `scatole/` e `giri/`.
- **Marketplace**: `clienti.carica_cliente(cliente)["marketplace_id"]`, mai una costante scritta a mano.
- Ogni task finisce con `python3 -m pytest tests -q` verde (339 test al 2026-09-05) e un commit.

---

### Task 1: `stock.py` — il report Restock, letto e filtrato

**Files:**
- Create: `scripts/stock.py`
- Modify: `tests/cliente_finto.py` (in coda: risposte per Reports API)
- Test: `tests/test_stock.py`

**Interfaces:**
- Consumes: `spapi_client.SpApiClient.get/post`, `spapi_client.SpApiError`.
- Produces: `stock.RigaStock` (dataclass congelata con `sku`, `asin`, `disponibili`, `in_arrivo`, `venduti_30gg`, `consigliati`, `entro`, `alert_amazon` e la property `copertura_giorni`), `stock.leggi_stock(client, paese, adesso, attesa_max_s=600, pausa=time.sleep) -> list[RigaStock]`, `stock.ReportFallito(SpApiError)`.

- [ ] **Step 1: Estendi il cliente finto con la Reports API**

In coda a `tests/cliente_finto.py`, dentro la classe `ClienteFinto`, aggiungi lo stato nel costruttore (`self.report_tsv: str | None = None`, `self.report_stato = "DONE"`, `self.report_creati: list[dict] = []`) e le risposte. Il documento non passa da SP-API ma da un URL: il finto lo espone come `file://` così il download vero viene esercitato senza rete.

```python
    # ---- Reports API 2021-06-30 --------------------------------------------
    def prepara_report(self, tsv: str, stato: str = "DONE") -> None:
        """Il TSV che `GET /documents/{id}` servirà, e in che stato finisce il
        report (DONE, FATAL, CANCELLED o IN_PROGRESS per il timeout)."""
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False, encoding="utf-8")
        f.write(tsv)
        f.close()
        self.report_tsv = f.name
        self.report_stato = stato
```

Nel `post`, prima del ramo `_B`:

```python
        if path == "/reports/2021-06-30/reports":
            self.report_creati.append(json_body)
            return {"reportId": "rep-1"}
```

Nel `get`, prima degli altri rami:

```python
        if path == "/reports/2021-06-30/reports/rep-1":
            return {"reportId": "rep-1", "processingStatus": self.report_stato,
                    "reportDocumentId": "doc-1"}
        if path == "/reports/2021-06-30/documents/doc-1":
            return {"reportDocumentId": "doc-1", "url": f"file://{self.report_tsv}"}
```

- [ ] **Step 2: Scrivi il test che fallisce**

```python
# tests/test_stock.py
import pytest
from datetime import datetime, timezone

import stock
from cliente_finto import ClienteFinto

ADESSO = datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc)

INTESTAZIONE = ("Country\tProduct Name\tFNSKU\tMerchant SKU\tASIN\tCondition\tSupplier\t"
                "Units Sold Last 30 Days\tTotal Units\tInbound\tAvailable\tAlert\t"
                "Recommended replenishment qty\tRecommended ship date")


def _riga(paese, sku, venduti, inbound, disponibili, alert="", repl="", entro=""):
    return (f"{paese}\tProdotto\tX0\t{sku}\tB0TEST\tNew\t--\t{venduti}\t0\t{inbound}\t"
            f"{disponibili}\t{alert}\t{repl}\t{entro}")


def _tsv(*righe):
    return "\n".join([INTESTAZIONE, *righe]) + "\n"


def test_tiene_solo_la_riga_del_paese_del_cliente():
    """La trappola vera del report (DESIGN §14): una riga per paese, e le righe
    estere dicono out_of_stock su prodotti che in Italia sono pieni."""
    c = ClienteFinto()
    c.prepara_report(_tsv(
        _riga("GB", "Reladol_100", 0, 0, 0, alert="out_of_stock"),
        _riga("IT", "Reladol_100", 55, 60, 47),
    ))
    righe = stock.leggi_stock(c, "IT", ADESSO, pausa=lambda _: None)
    assert [r.sku for r in righe] == ["Reladol_100"]
    assert (righe[0].disponibili, righe[0].in_arrivo, righe[0].venduti_30gg) == (47, 60, 55)
    assert righe[0].alert_amazon is None


def test_copertura_e_infinita_senza_vendite():
    """Uno SKU esaurito vende zero: la copertura non lo vede, e deve dirlo
    invece di dividere per zero."""
    c = ClienteFinto()
    c.prepara_report(_tsv(_riga("IT", "GermGEL_5L_FBA", 0, 0, 0, alert="out_of_stock", repl="5")))
    r = stock.leggi_stock(c, "IT", ADESSO, pausa=lambda _: None)[0]
    assert r.copertura_giorni == float("inf")
    assert (r.consigliati, r.alert_amazon) == (5, "out_of_stock")


def test_copertura_conta_anche_la_merce_in_arrivo():
    c = ClienteFinto()
    c.prepara_report(_tsv(_riga("IT", "Reladol_300", 15, 22, 3, repl="35", entro="09/12/2026")))
    r = stock.leggi_stock(c, "IT", ADESSO, pausa=lambda _: None)[0]
    assert r.copertura_giorni == pytest.approx(50.0)
    assert (r.consigliati, r.entro) == (35, "09/12/2026")


def test_report_fallito_non_diventa_una_lista_vuota():
    """Un report FATAL letto come 'nessuno SKU sotto soglia' sarebbe un
    silenzio bugiardo: deve alzare."""
    c = ClienteFinto()
    c.prepara_report(_tsv(), stato="FATAL")
    with pytest.raises(stock.ReportFallito, match="FATAL"):
        stock.leggi_stock(c, "IT", ADESSO, pausa=lambda _: None)


def test_report_che_non_finisce_scade():
    c = ClienteFinto()
    c.prepara_report(_tsv(), stato="IN_PROGRESS")
    with pytest.raises(stock.ReportFallito, match="non pronto"):
        stock.leggi_stock(c, "IT", ADESSO, attesa_max_s=30, pausa=lambda _: None)


def test_chiede_il_report_giusto_per_il_marketplace_giusto():
    c = ClienteFinto()
    c.prepara_report(_tsv(_riga("IT", "SKU-1", 10, 0, 100)))
    stock.leggi_stock(c, "IT", ADESSO, marketplace_id="APJ6JRA9NG5V4", pausa=lambda _: None)
    assert c.report_creati == [{"reportType": "GET_RESTOCK_INVENTORY_RECOMMENDATIONS_REPORT",
                                "marketplaceIds": ["APJ6JRA9NG5V4"]}]
```

- [ ] **Step 3: Lancia i test e verifica che falliscano**

Run: `python3 -m pytest tests/test_stock.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'stock'`

- [ ] **Step 4: Scrivi `scripts/stock.py`**

```python
"""Lo stock FBA di un cliente dal report Restock di Amazon (DESIGN §14).
Sola lettura: l'unica POST crea il report, non tocca l'account."""
from __future__ import annotations
import csv
import gzip
import io
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from spapi_client import SpApiError

TIPO_REPORT = "GET_RESTOCK_INVENTORY_RECOMMENDATIONS_REPORT"
_BASE = "/reports/2021-06-30"
_FINITI = ("DONE", "FATAL", "CANCELLED")


class ReportFallito(SpApiError):
    """Il report non è arrivato: il giro deve nominare il cliente saltato,
    non far finta che non avesse SKU sotto soglia."""


@dataclass(frozen=True)
class RigaStock:
    sku: str
    asin: str
    disponibili: int
    in_arrivo: int
    venduti_30gg: int
    consigliati: int
    entro: str | None
    alert_amazon: str | None

    @property
    def copertura_giorni(self) -> float:
        """Giorni di vendita davanti, merce in arrivo compresa. Senza vendite
        è infinita per costruzione: è il limite della metrica, non un errore —
        uno SKU esaurito vende zero proprio perché è esaurito (DESIGN §14)."""
        if self.venduti_30gg <= 0:
            return float("inf")
        return (self.disponibili + self.in_arrivo) / (self.venduti_30gg / 30)


def _numero(valore: str | None) -> int:
    testo = (valore or "").strip().replace(",", "")
    try:
        return int(float(testo))
    except ValueError:
        return 0


def _testo(valore: str | None) -> str | None:
    testo = (valore or "").strip()
    return testo or None


def _attendi(client, report_id: str, attesa_max_s: int, pausa) -> str:
    """Amazon prepara il report in una manciata di secondi (30 s su ECM il
    05/09), ma non lo garantisce: si aspetta con un tetto."""
    speso = 0
    while True:
        stato = client.get(f"{_BASE}/reports/{report_id}")
        processing = stato.get("processingStatus")
        if processing in _FINITI:
            if processing != "DONE":
                raise ReportFallito(f"report {TIPO_REPORT} chiuso in stato {processing}")
            return stato["reportDocumentId"]
        if speso >= attesa_max_s:
            raise ReportFallito(
                f"report {TIPO_REPORT} non pronto dopo {attesa_max_s} s (stato {processing})")
        pausa(15)
        speso += 15


def _scarica(client, document_id: str) -> str:
    documento = client.get(f"{_BASE}/documents/{document_id}")
    grezzo = urllib.request.urlopen(documento["url"]).read()
    if documento.get("compressionAlgorithm") == "GZIP":
        grezzo = gzip.decompress(grezzo)
    return grezzo.decode("utf-8", "replace")


def leggi_stock(client, paese: str, adesso: datetime, marketplace_id: str = "APJ6JRA9NG5V4",
                attesa_max_s: int = 600, pausa=time.sleep) -> list[RigaStock]:
    """Le righe del report che riguardano il paese del cliente. Il report ne
    porta una per paese (115 righe per 33 SKU su ECM): senza il filtro, gli
    out_of_stock dei mercati esteri seppelliscono il segnale (DESIGN §14)."""
    creato = client.post(f"{_BASE}/reports",
                         json_body={"reportType": TIPO_REPORT, "marketplaceIds": [marketplace_id]})
    documento = _attendi(client, creato["reportId"], attesa_max_s, pausa)
    lettore = csv.DictReader(io.StringIO(_scarica(client, documento)), delimiter="\t")
    righe = []
    for r in lettore:
        if (r.get("Country") or "").strip().upper() != paese.upper():
            continue
        righe.append(RigaStock(
            sku=(r.get("Merchant SKU") or "").strip(),
            asin=(r.get("ASIN") or "").strip(),
            disponibili=_numero(r.get("Available")),
            in_arrivo=_numero(r.get("Inbound")),
            venduti_30gg=_numero(r.get("Units Sold Last 30 Days")),
            consigliati=_numero(r.get("Recommended replenishment qty")),
            entro=_testo(r.get("Recommended ship date")),
            alert_amazon=_testo(r.get("Alert")),
        ))
    return righe
```

- [ ] **Step 5: Lancia i test e verifica che passino**

Run: `python3 -m pytest tests/test_stock.py -q`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add scripts/stock.py tests/test_stock.py tests/cliente_finto.py
git commit -m "feat(stock): il report Restock letto e filtrato sul paese del cliente"
```

---

### Task 2: la soglia — da riga a zona

**Files:**
- Modify: `scripts/stock.py` (in coda)
- Create: `soglie.yaml`
- Test: `tests/test_stock.py` (in coda)

**Interfaces:**
- Consumes: `stock.RigaStock` dal Task 1.
- Produces: `stock.OK`, `stock.RIASSORTIRE`, `stock.CRITICO` (stringhe `"ok"`, `"riassortire"`, `"critico"`), `stock.zona(riga, soglia_giorni, soglia_dura=7.0) -> str`, `stock.carica_soglie(percorso=None) -> dict`, `stock.soglia_per(soglie, cliente, sku) -> float`.

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# tests/test_stock.py (in coda)
def _r(sku="SKU-1", disponibili=0, in_arrivo=0, venduti=0, consigliati=0):
    return stock.RigaStock(sku=sku, asin="B0TEST", disponibili=disponibili, in_arrivo=in_arrivo,
                           venduti_30gg=venduti, consigliati=consigliati, entro=None, alert_amazon=None)


def test_il_consiglio_di_amazon_e_il_segnale_primario():
    """Reladol_300 il 05/09: 3 pezzi, 22 in arrivo, 15 vendite → 50 giorni di
    copertura, sopra soglia. Amazon chiede 35 pezzi perché conosce i tempi di
    trasporto, che noi non possiamo calcolare (DESIGN §14)."""
    riga = _r(disponibili=3, in_arrivo=22, venduti=15, consigliati=35)
    assert riga.copertura_giorni > 30
    assert stock.zona(riga, soglia_giorni=30) == stock.RIASSORTIRE


def test_esaurito_con_consiglio_aperto_e_critico():
    assert stock.zona(_r(consigliati=5), soglia_giorni=30) == stock.CRITICO


def test_esaurito_senza_vendite_e_senza_consiglio_tace():
    """Uno SKU che non vende e che nessuno rifornisce non si riassortisce."""
    assert stock.zona(_r(), soglia_giorni=30) == stock.OK


def test_copertura_e_la_rete_di_sicurezza():
    assert stock.zona(_r(disponibili=6, venduti=7), soglia_giorni=30) == stock.RIASSORTIRE
    assert stock.zona(_r(disponibili=100, venduti=7), soglia_giorni=30) == stock.OK


def test_sotto_la_soglia_dura_e_critico():
    assert stock.zona(_r(disponibili=1, venduti=30), soglia_giorni=30) == stock.CRITICO


def test_la_soglia_per_sku_batte_quella_del_cliente_che_batte_il_default():
    soglie = {"default_giorni": 30, "clienti": {"ECM": {"default_giorni": 45,
                                                        "sku": {"Reladol_100": 60}}}}
    assert stock.soglia_per(soglie, "ECM", "Reladol_100") == 60
    assert stock.soglia_per(soglie, "ECM", "Altro") == 45
    assert stock.soglia_per(soglie, "Bedshire", "Altro") == 30
    assert stock.soglia_per(soglie, "ecm", "Reladol_100") == 60  # come clienti._chiave
```

- [ ] **Step 2: Lancia i test e verifica che falliscano**

Run: `python3 -m pytest tests/test_stock.py -q -k "zona or soglia or consiglio or esaurito or copertura_e"`
Expected: FAIL, `AttributeError: module 'stock' has no attribute 'zona'`

- [ ] **Step 3: Aggiungi in coda a `scripts/stock.py`**

```python
OK, RIASSORTIRE, CRITICO = "ok", "riassortire", "critico"
SOGLIA_DURA_DEFAULT = 7.0
_SOGLIE_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "soglie.yaml")


def zona(riga: RigaStock, soglia_giorni: float, soglia_dura: float = SOGLIA_DURA_DEFAULT) -> str:
    """Tre segnali, in ordine di gravità (DESIGN §14): esaurito con un consiglio
    aperto, copertura sotto la soglia dura, poi il consiglio di Amazon o la
    copertura sotto la soglia del cliente."""
    if riga.disponibili + riga.in_arrivo == 0 and riga.consigliati > 0:
        return CRITICO
    if riga.copertura_giorni < soglia_dura:
        return CRITICO
    if riga.consigliati > 0 or riga.copertura_giorni < soglia_giorni:
        return RIASSORTIRE
    return OK


def carica_soglie(percorso: str | None = None) -> dict:
    """Assente vale come vuoto: il default di 30 giorni sta nel codice, il file
    serve solo a scostarsene."""
    percorso = percorso or _SOGLIE_DEFAULT
    if not os.path.exists(percorso):
        return {}
    with open(percorso) as f:
        return yaml.safe_load(f) or {}


def soglia_per(soglie: dict, cliente: str, sku: str) -> float:
    """Dalla più specifica alla più generale: SKU, cliente, default."""
    per_cliente = {_chiave(k): v for k, v in (soglie.get("clienti") or {}).items()}
    conf = per_cliente.get(_chiave(cliente)) or {}
    per_sku = (conf.get("sku") or {}).get(sku)
    if per_sku is not None:
        return float(per_sku)
    if conf.get("default_giorni") is not None:
        return float(conf["default_giorni"])
    return float(soglie.get("default_giorni", 30))
```

In testa al modulo aggiungi `import os`, `import yaml` e `from clienti import _chiave`.

- [ ] **Step 4: Scrivi `soglie.yaml`**

```yaml
# Soglie di copertura per il giro dello stock (DESIGN §14).
# Il segnale primario resta il consiglio di riassortimento di Amazon: questi
# giorni servono solo come rete di sicurezza, e a renderla più nervosa dove serve.
default_giorni: 30
clienti: {}
```

- [ ] **Step 5: Lancia tutti i test**

Run: `python3 -m pytest tests -q`
Expected: 345+ passed

- [ ] **Step 6: Commit**

```bash
git add scripts/stock.py tests/test_stock.py soglie.yaml
git commit -m "feat(stock): da riga a zona - il consiglio di Amazon, l'esaurito, la copertura"
```

---

### Task 3: `modelli.py` — i modelli d'imballaggio veri del cliente

**Files:**
- Create: `scripts/modelli.py`
- Test: `tests/test_modelli.py`

**Interfaces:**
- Consumes: `scatole._piani_spediti(client, adesso, mesi)` e `scatole._scatole_del_piano(client, plan_id)` (importare i privati fra moduli è già la convenzione della casa: `scatole.py` fa `from clienti import _chiave` e `from registro import _nome_cliente`).
- Produces: `modelli.PianoSpedito(plan_id, data, scatole)`, `modelli.leggi_piani(client, adesso, mesi=12) -> tuple[list[PianoSpedito], list[str]]` (piani e nomi dei saltati), `modelli.Modello(nome, sku, pezzi, lunghezza_cm, larghezza_cm, altezza_cm, peso_kg, scatole, piani, ultima_data, automatico)`, `modelli.modelli_per_sku(piani) -> dict[str, list[Modello]]` (lista ordinata per preferenza), `modelli.e_automatico(nome) -> bool`.

**Perché non riusare `scatole.leggi_storico`:** quello indicizza per SKU e tiene una scatola sola per piano. Qui serve l'oggetto "modello", che è un'altra chiave (`templateName`) e porta con sé quante volte è stato usato — il dato che decide quale proporre. `leggi_piani` fa il giro delle API **una volta sola** e serve sia questo task sia il Task 4.

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# tests/test_modelli.py
from datetime import datetime, timezone

import modelli
from cliente_finto import ClienteFinto, scatola_finta

ADESSO = datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc)


def test_riconosce_i_nomi_generati_da_amazon():
    """P1 - B1 lo scrive il flusso Send-to-Amazon quando nessuno passa un
    modello; 'PALLET 36 PACCHI' l'ha scritto una persona (DESIGN §14)."""
    assert modelli.e_automatico("P1 - B1") is True
    assert modelli.e_automatico("P12 - B36") is True
    assert modelli.e_automatico("PALLET 36 PACCHI") is False
    assert modelli.e_automatico("Reladol 100") is False
    assert modelli.e_automatico(None) is True


def test_un_modello_porta_quante_volte_e_stato_usato():
    c = ClienteFinto()
    c.spedito("wf1", "2026-08-12T10:00:00Z",
              [scatola_finta("Germ_5x200_FBA", 10, 11.8, 30, 40, 30, quantita=4,
                             modello="GERMISTOP 5x200ml - 10 PZ")])
    c.spedito("wf2", "2026-07-01T10:00:00Z",
              [scatola_finta("Germ_5x200_FBA", 10, 11.8, 30, 40, 30, quantita=6,
                             modello="GERMISTOP 5x200ml - 10 PZ")])
    piani, saltati = modelli.leggi_piani(c, ADESSO)
    m = modelli.modelli_per_sku(piani)["Germ_5x200_FBA"][0]
    assert (m.nome, m.pezzi, m.peso_kg) == ("GERMISTOP 5x200ml - 10 PZ", 10, 11.8)
    assert (m.scatole, m.piani, m.ultima_data) == (10, 2, "2026-08-12")
    assert saltati == []


def test_il_nome_vero_batte_quello_automatico_anche_se_piu_vecchio():
    """Il caso Arcipelago: la nostra spedizione del 03/09 ha scritto P1 - B1
    sopra dieci spedizioni di PALLET 36 PACCHI."""
    c = ClienteFinto()
    c.spedito("wf_nuovo", "2026-09-03T10:00:00Z",
              [scatola_finta("138_FBA", 3, 2.22, 32, 42.7, 25.3, modello="P1 - B1")])
    c.spedito("wf_vecchio", "2026-07-16T10:00:00Z",
              [scatola_finta("138_FBA", 3, 2.22, 32, 42.7, 25.3, quantita=36,
                             modello="PALLET 36 PACCHI")])
    scelti = modelli.modelli_per_sku(modelli.leggi_piani(c, ADESSO)[0])["138_FBA"]
    assert [m.nome for m in scelti] == ["PALLET 36 PACCHI", "P1 - B1"]
    assert scelti[0].automatico is False


def test_lo_stesso_modello_con_due_contenuti_resta_due_voci():
    """'Reladol 100' su ECM è stato usato da 30 e da 60 pezzi: un modello non
    garantisce una scatola sola, e vince l'uso più recente."""
    c = ClienteFinto()
    c.spedito("wf1", "2026-05-28T10:00:00Z",
              [scatola_finta("Reladol_100", 60, 9.0, 30, 40, 31, modello="Reladol 100")])
    c.spedito("wf2", "2025-11-11T10:00:00Z",
              [scatola_finta("Reladol_100", 30, 4.7, 30, 40, 31, modello="Reladol 100")])
    scelti = modelli.modelli_per_sku(modelli.leggi_piani(c, ADESSO)[0])["Reladol_100"]
    assert [(m.nome, m.pezzi) for m in scelti] == [("Reladol 100", 60), ("Reladol 100", 30)]


def test_le_scatole_miste_non_fanno_modello():
    """Una scatola con due SKU non dice quanto pesa un collo di uno solo."""
    c = ClienteFinto()
    c.spedito("wf1", "2026-08-01T10:00:00Z",
              [scatola_finta("SKU-A", 10, 5.0, 30, 40, 30, modello="MISTO", altri=[("SKU-B", 4)])])
    assert modelli.modelli_per_sku(modelli.leggi_piani(c, ADESSO)[0]) == {}


def test_un_piano_illeggibile_e_nominato_non_ingoiato():
    c = ClienteFinto()
    c.spedito("wf_rotto", "2026-08-01T10:00:00Z", [], leggibile=False)
    c.spedito("wf_ok", "2026-07-01T10:00:00Z",
              [scatola_finta("SKU-A", 10, 5.0, 30, 40, 30, modello="BUONO")])
    piani, saltati = modelli.leggi_piani(c, ADESSO)
    assert [p.plan_id for p in piani] == ["wf_ok"]
    assert len(saltati) == 1 and "wf_rotto" in saltati[0]
```

- [ ] **Step 2: Lancia i test e verifica che falliscano**

Run: `python3 -m pytest tests/test_modelli.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'modelli'`

- [ ] **Step 3: Scrivi `scripts/modelli.py`**

```python
"""I modelli d'imballaggio del cliente, letti dai piani spediti (DESIGN §14).
Regola di Mariano (2026-09-05): prima di proporre una scatola si guarda che
modelli il cliente ha davvero creato nel suo account. Indicizzati per modello
e non per SKU come in scatole.py, perché è il modello a portare con sé quante
volte è stato usato — il dato che decide quale proporre."""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime

from scatole import _piani_spediti, _scatole_del_piano
from spapi_client import SpApiError

_AUTOMATICO = re.compile(r"^P\d+ - B\d+$")
_MISURE = ("pezzi", "lunghezza_cm", "larghezza_cm", "altezza_cm", "peso_kg")


def e_automatico(nome: str | None) -> bool:
    """`P1 - B1`: il nome che Amazon si inventa quando la scatola arriva senza
    modello. Vale meno di un nome scritto da una persona."""
    return not nome or bool(_AUTOMATICO.match(nome.strip()))


@dataclass(frozen=True)
class PianoSpedito:
    plan_id: str
    data: str
    scatole: tuple[dict, ...]


@dataclass(frozen=True)
class Modello:
    nome: str
    sku: str
    pezzi: int
    lunghezza_cm: float
    larghezza_cm: float
    altezza_cm: float
    peso_kg: float
    scatole: int
    piani: int
    ultima_data: str
    automatico: bool


def leggi_piani(client, adesso: datetime, mesi: int = 12) -> tuple[list[PianoSpedito], list[str]]:
    """Un giro solo delle API per tutto ciò che serve allo stock: i modelli
    (qui) e le spedizioni ricorrenti (ricorrenza.py). Un piano le cui scatole
    non si leggono viene saltato e nominato."""
    piani, saltati = [], []
    for p in _piani_spediti(client, adesso, mesi):
        pid = p["inboundPlanId"]
        try:
            scatole = _scatole_del_piano(client, pid)
        except SpApiError as e:
            saltati.append(f"{pid}: {e}")
            continue
        piani.append(PianoSpedito(pid, p["createdAt"][:10], tuple(scatole)))
    return piani, saltati


def _colli_mono_sku(piano: PianoSpedito):
    for b in piano.scatole:
        items = b.get("items") or []
        d, w = b.get("dimensions") or {}, b.get("weight") or {}
        if (b.get("contentInformationSource") != "BOX_CONTENT_PROVIDED"
                or len(items) != 1 or not d or not w):
            continue
        yield b, items[0], d, w


def modelli_per_sku(piani: list[PianoSpedito]) -> dict[str, list[Modello]]:
    """Per ogni SKU i suoi modelli, dal più adatto da proporre al meno: prima
    quelli con un nome vero, poi i più recenti, poi i più usati."""
    conta: dict[tuple, dict] = {}
    for piano in piani:
        for b, it, d, w in _colli_mono_sku(piano):
            nome = (b.get("templateName") or "").strip() or "—"
            chiave = (it["msku"], nome, int(it["quantity"]), float(d["length"]),
                      float(d["width"]), float(d["height"]), float(w["value"]))
            voce = conta.setdefault(chiave, {"scatole": 0, "piani": set(), "ultima": ""})
            voce["scatole"] += int(b.get("quantity") or 1)
            voce["piani"].add(piano.plan_id)
            voce["ultima"] = max(voce["ultima"], piano.data)
    per_sku: dict[str, list[Modello]] = {}
    for (sku, nome, pezzi, lung, larg, alt, peso), voce in conta.items():
        per_sku.setdefault(sku, []).append(Modello(
            nome=nome, sku=sku, pezzi=pezzi, lunghezza_cm=lung, larghezza_cm=larg,
            altezza_cm=alt, peso_kg=peso, scatole=voce["scatole"], piani=len(voce["piani"]),
            ultima_data=voce["ultima"], automatico=e_automatico(nome)))
    for lista in per_sku.values():
        lista.sort(key=lambda m: (m.automatico, _negativo(m.ultima_data), -m.scatole))
    return per_sku


def _negativo(data: str) -> tuple:
    """Ordine decrescente su una data ISO senza convertirla: le cifre invertite."""
    return tuple(-ord(c) for c in data)
```

- [ ] **Step 4: Lancia i test e verifica che passino**

Run: `python3 -m pytest tests/test_modelli.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/modelli.py tests/test_modelli.py
git commit -m "feat(stock): i modelli d'imballaggio del cliente, col nome vero davanti a P1 - B1"
```

---

### Task 4: `ricorrenza.py` — la spedizione tipo

**Files:**
- Create: `scripts/ricorrenza.py`
- Test: `tests/test_ricorrenza.py`

**Interfaces:**
- Consumes: `modelli.PianoSpedito`, `modelli.leggi_piani` dal Task 3.
- Produces: `ricorrenza.Tipo(sku, pezzi_base, colli_base, modello, volte)`, `ricorrenza.spedizione_tipo(piani, sku, minimo=3) -> Tipo | None`.

**La regola, tarata sui dati veri del 2026-09-05:** si guardano solo le spedizioni **mono-SKU** di quello SKU. La base è la quantità più piccola; è una spedizione tipo se ci sono almeno `minimo` spedizioni, se **ogni** quantità è un multiplo esatto della base, e se uno stesso modello copre la maggioranza delle scatole. Arcipelago: 108 pezzi × 10 e 216 × 1 → base 108, tutti multipli, modello `PALLET 36 PACCHI` in 10 spedizioni su 11 → **tipo**. Bedshire su `XJ-YOJW-FUSZ`: 60, 120, 240 → base 60, tutti multipli, `COPPIA CUSCINI 60 PEZZI` sempre → **tipo**. Gli assortimenti di Bedshire, che non si ripetono mai uguali, non sono mono-SKU → **niente tipo**.

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# tests/test_ricorrenza.py
from datetime import datetime, timezone

import modelli
import ricorrenza
from cliente_finto import ClienteFinto, scatola_finta

ADESSO = datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc)


def _piani(c):
    return modelli.leggi_piani(c, ADESSO)[0]


def test_arcipelago_e_sempre_la_stessa_spedizione():
    """10 spedizioni da 108 pezzi e una doppia da 216: la doppia non rompe la
    regolarità, è la stessa spedizione fatta due volte (DESIGN §14)."""
    c = ClienteFinto()
    for i, (data, colli) in enumerate([("2026-07-16", 36), ("2026-06-11", 36), ("2026-04-24", 36),
                                       ("2026-03-26", 36), ("2025-10-02", 72)]):
        c.spedito(f"wf{i}", f"{data}T10:00:00Z",
                  [scatola_finta("138_FBA", 3, 2.22, 32, 42.7, 25.3, quantita=colli,
                                 modello="PALLET 36 PACCHI")])
    t = ricorrenza.spedizione_tipo(_piani(c), "138_FBA")
    assert (t.pezzi_base, t.colli_base, t.modello, t.volte) == (108, 36, "PALLET 36 PACCHI", 5)


def test_bedshire_varia_ma_a_multipli():
    c = ClienteFinto()
    for i, (data, colli) in enumerate([("2026-04-11", 24), ("2026-02-23", 48), ("2026-01-27", 24),
                                       ("2025-12-19", 12), ("2025-10-15", 48)]):
        c.spedito(f"wf{i}", f"{data}T10:00:00Z",
                  [scatola_finta("XJ-YOJW-FUSZ", 5, 6.0, 40, 40, 40, quantita=colli,
                                 modello="COPPIA CUSCINI 60 PEZZI")])
    t = ricorrenza.spedizione_tipo(_piani(c), "XJ-YOJW-FUSZ")
    assert (t.pezzi_base, t.colli_base) == (60, 12)


def test_quantita_che_non_sono_multipli_non_fanno_tipo():
    c = ClienteFinto()
    for i, (data, pezzi) in enumerate([("2026-04-11", 100), ("2026-02-23", 137), ("2026-01-27", 62)]):
        c.spedito(f"wf{i}", f"{data}T10:00:00Z",
                  [scatola_finta("SKU-A", pezzi, 6.0, 40, 40, 40, modello="M")])
    assert ricorrenza.spedizione_tipo(_piani(c), "SKU-A") is None


def test_due_sole_spedizioni_non_fanno_abitudine():
    c = ClienteFinto()
    for i, data in enumerate(["2026-04-11", "2026-02-23"]):
        c.spedito(f"wf{i}", f"{data}T10:00:00Z",
                  [scatola_finta("SKU-A", 10, 6.0, 40, 40, 40, quantita=2, modello="M")])
    assert ricorrenza.spedizione_tipo(_piani(c), "SKU-A") is None


def test_le_spedizioni_miste_non_contano():
    """Gli assortimenti di Bedshire: tre SKU insieme, mai due volte uguali."""
    c = ClienteFinto()
    for i, data in enumerate(["2026-04-11", "2026-02-23", "2026-01-27"]):
        c.spedito(f"wf{i}", f"{data}T10:00:00Z",
                  [scatola_finta("SKU-A", 10, 6.0, 40, 40, 40, modello="M"),
                   scatola_finta("SKU-B", 7, 6.0, 40, 40, 40, modello="N")])
    assert ricorrenza.spedizione_tipo(_piani(c), "SKU-A") is None
```

- [ ] **Step 2: Lancia i test e verifica che falliscano**

Run: `python3 -m pytest tests/test_ricorrenza.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'ricorrenza'`

- [ ] **Step 3: Scrivi `scripts/ricorrenza.py`**

```python
"""La spedizione tipo di un cliente (DESIGN §14). Alcuni clienti spediscono
sempre la stessa cosa — Arcipelago 108 pezzi in 36 colli, dieci volte su
undici — e per loro la bozza si può proporre da sola. Chi varia va chiesto."""
from __future__ import annotations
import collections
from dataclasses import dataclass

from modelli import PianoSpedito


@dataclass(frozen=True)
class Tipo:
    sku: str
    pezzi_base: int
    colli_base: int
    modello: str
    volte: int


def _spedizioni_mono_sku(piani: list[PianoSpedito], sku: str) -> list[tuple[int, int, str]]:
    """Per ogni piano che contiene solo quello SKU: pezzi totali, colli, modello
    più usato. Un piano con più SKU non dice niente sull'abitudine di uno solo."""
    fuori = []
    for piano in piani:
        pezzi, colli, per_modello = 0, 0, collections.Counter()
        altri = False
        for b in piano.scatole:
            items = b.get("items") or []
            quante = int(b.get("quantity") or 1)
            if len(items) != 1 or items[0]["msku"] != sku:
                altri = True
                break
            pezzi += int(items[0]["quantity"]) * quante
            colli += quante
            per_modello[(b.get("templateName") or "—").strip()] += quante
        if altri or not pezzi:
            continue
        fuori.append((pezzi, colli, per_modello.most_common(1)[0][0]))
    return fuori


def spedizione_tipo(piani: list[PianoSpedito], sku: str, minimo: int = 3) -> Tipo | None:
    """La forma che il cliente ripete. Serve che le spedizioni siano almeno
    `minimo`, che ogni quantità sia un multiplo esatto della più piccola (una
    spedizione doppia è la stessa spedizione fatta due volte) e che un modello
    copra la maggioranza."""
    spedizioni = _spedizioni_mono_sku(piani, sku)
    if len(spedizioni) < minimo:
        return None
    base_pezzi, base_colli, _ = min(spedizioni, key=lambda s: s[0])
    if any(p % base_pezzi for p, _, _ in spedizioni):
        return None
    per_modello = collections.Counter(m for _, _, m in spedizioni)
    modello, quante = per_modello.most_common(1)[0]
    if quante * 2 <= len(spedizioni):
        return None
    return Tipo(sku=sku, pezzi_base=base_pezzi, colli_base=base_colli,
                modello=modello, volte=len(spedizioni))
```

- [ ] **Step 4: Lancia i test e verifica che passino**

Run: `python3 -m pytest tests/test_ricorrenza.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/ricorrenza.py tests/test_ricorrenza.py
git commit -m "feat(stock): la spedizione tipo - chi ripete la stessa forma e chi varia"
```

---

### Task 5: dalla quantità alla bozza, col modello scelto

**Files:**
- Modify: `scripts/modelli.py` (in coda), `scripts/giro.py` (funzione `scatole` e parser `e`)
- Test: `tests/test_modelli.py` (in coda), `tests/test_giro.py` (in coda)

**Interfaces:**
- Consumes: `modelli.Modello` (Task 3), `scatole.bozza_lotto(istantanea, quantita)` e `scatole.scrivi_bozza(cliente, testo, adesso)` — invariati.
- Produces: `modelli.a_colli_pieni(quantita, pezzi_per_collo) -> int`, `modelli.istantanea_da(cliente, scelte: dict[str, Modello], adesso) -> dict`, `giro.scatole(client, cliente, quantita, adesso=None, mesi=12, modello=None) -> str`, CLI `giro.py scatole CLIENTE SKU=QTA --modello "NOME"`.
- Nota sui tipi: l'istantanea sintetica porta `"asin": None`, perché `Modello` non trasporta l'ASIN. `scatole.bozza_lotto` non lo usa; `scatole.tabella` sì (stamperebbe `ASIN ?`) e infatti su questa istantanea non va chiamata.

**Perché non toccare `bozza_lotto`:** arrotondando a colli pieni **prima** di chiamarla, il resto è sempre zero e il ramo del collo parziale non si attiva mai. Zero righe cambiate in codice già collaudato dal vivo.

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# tests/test_modelli.py (in coda)
from datetime import datetime, timezone

import scatole


def test_arrotonda_al_collo_pieno_superiore():
    """35 pezzi consigliati su colli da 22 diventano 2 colli: niente parziali
    da mettere sulla bilancia, il file è usabile appena arriva (DESIGN §14)."""
    assert modelli.a_colli_pieni(35, 22) == 44
    assert modelli.a_colli_pieni(44, 22) == 44
    assert modelli.a_colli_pieni(1, 22) == 22
    assert modelli.a_colli_pieni(0, 22) == 0


def test_la_bozza_dal_modello_non_ha_parziali():
    m = modelli.Modello(nome="Reladol 300", sku="Reladol_300", pezzi=22, lunghezza_cm=38,
                        larghezza_cm=51, altezza_cm=47, peso_kg=9.5, scatole=8, piani=4,
                        ultima_data="2026-05-14", automatico=False)
    adesso = datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc)
    istantanea = modelli.istantanea_da("ECM", {"Reladol_300": m}, adesso)
    testo = scatole.bozza_lotto(istantanea, {"Reladol_300": modelli.a_colli_pieni(35, 22)})
    assert "quantita: 2, peso_kg: 9.5" in testo
    assert "da pesare" not in testo
    assert "# standard: Reladol 300" in testo
```

```python
# tests/test_giro.py (in coda)
def test_scatole_con_modello_scelto_scrive_la_bozza_a_colli_pieni(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.spedito("wf1", "2026-05-14T10:00:00Z",
              [scatola_finta("Reladol_300", 22, 9.5, 38, 51, 47, quantita=2, modello="Reladol 300")])
    c.spedito("wf2", "2026-09-03T10:00:00Z",
              [scatola_finta("Reladol_300", 22, 9.5, 38, 51, 47, modello="P1 - B2")])
    adesso = datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc)
    testo = giro.scatole(c, "ECM", {"Reladol_300": 35}, adesso=adesso, modello="Reladol 300")
    assert "Reladol 300" in testo and "44 pz" in testo
    bozza = (tmp_path / "Desktop" / "Lotto-ECM-2026-09-05.yaml").read_text()
    assert "quantita: 2, peso_kg: 9.5" in bozza and "da pesare" not in bozza


def test_scatole_con_un_modello_che_non_esiste_lo_dice(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.spedito("wf1", "2026-05-14T10:00:00Z",
              [scatola_finta("Reladol_300", 22, 9.5, 38, 51, 47, modello="Reladol 300")])
    with pytest.raises(ValueError, match="Reladol 300"):
        giro.scatole(c, "ECM", {"Reladol_300": 35},
                     adesso=datetime(2026, 9, 5, tzinfo=timezone.utc), modello="INVENTATO")
```

- [ ] **Step 2: Lancia i test e verifica che falliscano**

Run: `python3 -m pytest tests/test_modelli.py tests/test_giro.py -q -k "colli_pieni or dal_modello or modello_scelto or non_esiste"`
Expected: FAIL, `AttributeError: module 'modelli' has no attribute 'a_colli_pieni'`

- [ ] **Step 3: Aggiungi in coda a `scripts/modelli.py`**

```python
def a_colli_pieni(quantita: int, pezzi_per_collo: int) -> int:
    """Si spedisce a colli interi: 35 pezzi su colli da 22 diventano 44. Un
    parziale costerebbe una pesata prima di poter usare il file."""
    if quantita <= 0:
        return 0
    return -(-quantita // pezzi_per_collo) * pezzi_per_collo


def istantanea_da(cliente: str, scelte: dict[str, Modello], adesso: datetime) -> dict:
    """Un'istantanea sintetica nella forma che `scatole.bozza_lotto` si aspetta,
    costruita dai modelli scelti invece che dall'ultima scatola vista. Così la
    bozza resta scritta dalla funzione già collaudata dal vivo."""
    return {
        "cliente": cliente,
        "letto_il": adesso.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mesi": 12, "piani_letti": 0, "piani_saltati": [], "colli_misti_esclusi": 0,
        "sku": {sku: {"pezzi": m.pezzi, "lunghezza_cm": m.lunghezza_cm,
                      "larghezza_cm": m.larghezza_cm, "altezza_cm": m.altezza_cm,
                      "peso_kg": m.peso_kg, "modello": m.nome, "asin": None,
                      "ultimo_piano": "", "ultima_data": m.ultima_data, "varianti": []}
                for sku, m in scelte.items()},
    }


def scegli(per_sku: dict[str, list[Modello]], sku: str, nome: str) -> Modello:
    """Il modello chiamato per nome, come lo scrive Mariano nel comando: senza
    badare a maiuscole e spazi, come `clienti._chiave`."""
    voluto = " ".join(nome.split()).casefold()
    for m in per_sku.get(sku, []):
        if " ".join(m.nome.split()).casefold() == voluto:
            return m
    noti = ", ".join(sorted({m.nome for m in per_sku.get(sku, [])})) or "nessuno"
    raise ValueError(f"nessun modello {nome!r} per {sku}: modelli noti {noti}")
```

- [ ] **Step 4: Modifica `giro.scatole` in `scripts/giro.py`**

Sostituisci la firma e aggiungi il ramo del modello, lasciando intatto il ramo senza `--modello`:

```python
def scatole(client, cliente: str, quantita: dict[str, int], adesso: datetime | None = None,
            mesi: int = 12, modello: str | None = None) -> str:
    """DESIGN §13: i colli standard del cliente dai piani spediti, l'istantanea
    per il controllo di `prepara`, e con le quantità la bozza del lotto. Con
    `modello` (DESIGN §14) la bozza esce dai modelli d'imballaggio dell'account
    e a colli pieni. Sola lettura: nessun registro, nessuna POST."""
    adesso = adesso or datetime.now(timezone.utc)
    if modello:
        if not quantita:
            raise ValueError("--modello serve a scrivere una bozza: passa almeno uno SKU=QTA")
        piani, saltati = modelli_mod.leggi_piani(client, adesso, mesi)
        per_sku = modelli_mod.modelli_per_sku(piani)
        scelte = {sku: modelli_mod.scegli(per_sku, sku, modello) for sku in quantita}
        pieni = {sku: modelli_mod.a_colli_pieni(q, scelte[sku].pezzi) for sku, q in quantita.items()}
        istantanea = modelli_mod.istantanea_da(cliente, scelte, adesso)
        percorso = scatole_mod.scrivi_bozza(cliente, scatole_mod.bozza_lotto(istantanea, pieni), adesso)
        righe = [f"Bozza di {cliente} dai modelli dell'account:"]
        righe += [f"  {sku}: {pieni[sku]} pz in {pieni[sku] // scelte[sku].pezzi} colli, "
                  f"modello {scelte[sku].nome} ({scelte[sku].pezzi} pz/collo)" for sku in quantita]
        righe += [f"  piano non letto: {s}" for s in saltati]
        return "\n".join(righe) + f"\nBozza del lotto: {percorso}"
    istantanea = scatole_mod.leggi_storico(client, cliente, adesso, mesi)
    ...  # resto invariato
```

In testa al modulo aggiungi `import modelli as modelli_mod` — attenzione, **non** è il `modello.py` già importato per `PianoSpedizione` e `Scatola`: sono due moduli diversi. Nel parser `e` del `main` aggiungi:

```python
    e.add_argument("--modello", help='usa questo modello d\'imballaggio dell\'account e arrotonda a colli pieni')
```

e nel ramo di dispatch passa `modello=args.modello`.

- [ ] **Step 5: Lancia tutti i test**

Run: `python3 -m pytest tests -q`
Expected: tutti verdi, nessuna regressione su `test_scatole.py` e `test_giro.py`

- [ ] **Step 6: Commit**

```bash
git add scripts/modelli.py scripts/giro.py tests/test_modelli.py tests/test_giro.py
git commit -m "feat(stock): giro.py scatole --modello - bozza dai modelli dell'account, a colli pieni"
```

---

### Task 6: `stato_stock.py` — parlare sulla transizione, non sullo stato

**Files:**
- Create: `scripts/stato_stock.py`
- Test: `tests/test_stato_stock.py`

**Interfaces:**
- Consumes: `stock.RigaStock`, `stock.OK/RIASSORTIRE/CRITICO` (Task 1-2).
- Produces: `stato_stock.Avviso(sku, zona, motivo, riga)` con `motivo` in `"nuovo" | "peggiorato" | "sollecito"`, `stato_stock.percorso(cliente) -> str`, `stato_stock.carica(cliente) -> dict`, `stato_stock.salva(cliente, stato) -> str`, `stato_stock.da_avvisare(zone, stato, oggi, giorni_sollecito=7) -> tuple[list[Avviso], dict]` dove `zone` è `list[tuple[RigaStock, str]]` e `oggi` è una `date`.

**L'ordine dei controlli conta.** La crescita di `Inbound` si guarda **per prima**: è il segnale che la spedizione è partita. E "azzerare" non vuol dire cancellare la voce — cancellandola, il giorno dopo lo SKU tornerebbe "nuovo" e riavviserebbe. Si aggiorna la voce in silenzio e il timer del sollecito riparte. La voce si cancella solo quando la zona torna `ok`.

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# tests/test_stato_stock.py
from datetime import date

import stato_stock
import stock

OGGI = date(2026, 9, 5)


def _riga(sku="Reladol_300", disponibili=3, in_arrivo=22, venduti=15, consigliati=35):
    return stock.RigaStock(sku=sku, asin="B0TEST", disponibili=disponibili, in_arrivo=in_arrivo,
                           venduti_30gg=venduti, consigliati=consigliati, entro="09/12/2026",
                           alert_amazon=None)


def test_avvisa_una_volta_sola_quando_entra_in_zona():
    avvisi, nuovo = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], {}, OGGI)
    assert [(a.sku, a.motivo) for a in avvisi] == [("Reladol_300", "nuovo")]
    domani, _ = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], nuovo, date(2026, 9, 6))
    assert domani == []


def test_parla_di_nuovo_se_peggiora():
    _, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], {}, OGGI)
    peggio = _riga(disponibili=0, in_arrivo=0, consigliati=35)
    avvisi, stato2 = stato_stock.da_avvisare([(peggio, stock.CRITICO)], stato, date(2026, 9, 6))
    assert [(a.sku, a.motivo) for a in avvisi] == [("Reladol_300", "peggiorato")]
    ancora, _ = stato_stock.da_avvisare([(peggio, stock.CRITICO)], stato2, date(2026, 9, 7))
    assert ancora == []


def test_la_spedizione_partita_azzera_senza_avvisare():
    """Inbound cresciuto = qualcuno ha spedito. Nessuna mail, e il timer del
    sollecito riparte da oggi: la voce non si cancella, o domani sarebbe 'nuovo'."""
    _, stato = stato_stock.da_avvisare([(_riga(in_arrivo=22), stock.RIASSORTIRE)], {}, OGGI)
    avvisi, dopo = stato_stock.da_avvisare(
        [(_riga(in_arrivo=66), stock.RIASSORTIRE)], stato, date(2026, 9, 8))
    assert avvisi == []
    assert dopo["Reladol_300"]["in_arrivo"] == 66
    assert dopo["Reladol_300"]["dal"] == "2026-09-08"


def test_sollecita_dopo_sette_giorni_di_immobilita():
    _, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], {}, OGGI)
    zitto, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], stato, date(2026, 9, 11))
    assert zitto == []
    avvisi, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], stato, date(2026, 9, 12))
    assert [(a.sku, a.motivo) for a in avvisi] == [("Reladol_300", "sollecito")]
    ancora, _ = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], stato, date(2026, 9, 13))
    assert ancora == []


def test_tornare_a_posto_cancella_la_voce():
    _, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], {}, OGGI)
    avvisi, dopo = stato_stock.da_avvisare(
        [(_riga(disponibili=300, consigliati=0), stock.OK)], stato, date(2026, 9, 20))
    assert avvisi == [] and dopo == {}


def test_stato_illeggibile_riparte_da_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    percorso = stato_stock.percorso("ECM")
    import os
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    open(percorso, "w").write("{ rotto")
    assert stato_stock.carica("ECM") == {}


def test_salva_e_rilegge(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _, stato = stato_stock.da_avvisare([(_riga(), stock.RIASSORTIRE)], {}, OGGI)
    stato_stock.salva("ECM", stato)
    assert stato_stock.carica("ECM") == stato
```

- [ ] **Step 2: Lancia i test e verifica che falliscano**

Run: `python3 -m pytest tests/test_stato_stock.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'stato_stock'`

- [ ] **Step 3: Scrivi `scripts/stato_stock.py`**

```python
"""Cosa è già stato detto su ogni SKU (DESIGN §14). Uno SKU sotto soglia resta
sotto soglia anche domani: senza questa memoria l'avviso diventerebbe il
bollettino quotidiano che Mariano non vuole."""
from __future__ import annotations
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta

from registro import _nome_cliente
from stock import CRITICO, OK, RigaStock

GIORNI_SOLLECITO = 7
_BASE = "~/.config/newera-amazon/spedizioni/stock"


@dataclass(frozen=True)
class Avviso:
    sku: str
    zona: str
    motivo: str          # nuovo | peggiorato | sollecito
    riga: RigaStock


def percorso(cliente: str) -> str:
    return os.path.expanduser(f"{_BASE}/{_nome_cliente(cliente)}.json")


def carica(cliente: str) -> dict:
    """Uno stato illeggibile vale come vuoto: si riparte da zero e si riavvisa.
    Un doppione è meglio di un silenzio."""
    p = percorso(cliente)
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def salva(cliente: str, stato: dict) -> str:
    p = percorso(cliente)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(json.dumps(stato, indent=2, ensure_ascii=False))
    return p


def _voce(riga: RigaStock, zona: str, giorno: date) -> dict:
    return {"zona": zona, "dal": giorno.isoformat(), "avvisato_il": giorno.isoformat(),
            "in_arrivo": riga.in_arrivo}


def da_avvisare(zone: list[tuple[RigaStock, str]], stato: dict, oggi: date,
                giorni_sollecito: int = GIORNI_SOLLECITO) -> tuple[list[Avviso], dict]:
    """Gli avvisi da mandare e lo stato da salvare. L'ordine dei controlli è la
    regola: prima la spedizione partita, poi il peggioramento, poi il sollecito."""
    avvisi, nuovo = [], {}
    for riga, zona in zone:
        prima = stato.get(riga.sku)
        if zona == OK:
            continue                                   # la voce sparisce: il prossimo calo è nuovo
        if prima is None:
            avvisi.append(Avviso(riga.sku, zona, "nuovo", riga))
            nuovo[riga.sku] = _voce(riga, zona, oggi)
            continue
        if riga.in_arrivo > int(prima.get("in_arrivo", 0)):
            nuovo[riga.sku] = _voce(riga, zona, oggi)  # spedizione partita: zitti, timer da capo
            continue
        if zona == CRITICO and prima.get("zona") != CRITICO:
            avvisi.append(Avviso(riga.sku, zona, "peggiorato", riga))
            nuovo[riga.sku] = _voce(riga, zona, oggi)
            continue
        avvisato = date.fromisoformat(prima["avvisato_il"])
        if oggi - avvisato >= timedelta(days=giorni_sollecito):
            avvisi.append(Avviso(riga.sku, zona, "sollecito", riga))
            nuovo[riga.sku] = {**prima, "zona": zona, "avvisato_il": oggi.isoformat(),
                               "in_arrivo": riga.in_arrivo}
            continue
        nuovo[riga.sku] = {**prima, "zona": zona, "in_arrivo": riga.in_arrivo}
    return avvisi, nuovo
```

- [ ] **Step 4: Lancia i test e verifica che passino**

Run: `python3 -m pytest tests/test_stato_stock.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/stato_stock.py tests/test_stato_stock.py
git commit -m "feat(stock): lo stato che fa parlare sulla transizione e non ogni giorno"
```

---

### Task 7: `avviso_mail.py` — la mail, costruita e spedita

**Files:**
- Create: `scripts/avviso_mail.py`
- Test: `tests/test_avviso_mail.py`

**Interfaces:**
- Consumes: `stato_stock.Avviso` (Task 6).
- Produces: `avviso_mail.Blocco(cliente, avvisi, bozza, candidati, saltati, errore)`, `avviso_mail.oggetto(blocchi) -> str`, `avviso_mail.corpo(blocchi, oggi) -> str` (HTML), `avviso_mail.carica_config(percorso=None) -> dict`, `avviso_mail.manda(config, oggetto, html, spedisci=None) -> str` (torna il destinatario usato).
- `candidati`: `list[tuple[str, str]]`, coppie (SKU, testo dei modelli possibili) per i clienti "vari".

**Il fallback conta quanto l'invio:** finché `agenti@agencynewera.it` non esiste, la mail va a `promozione@` e il giro lo **dice** nel log. Una mail persa in silenzio è peggio di nessuna mail.

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# tests/test_avviso_mail.py
from datetime import date

import avviso_mail
import stato_stock
import stock

OGGI = date(2026, 9, 5)


def _avviso(sku, motivo="nuovo", zona=stock.RIASSORTIRE, disponibili=3, in_arrivo=22,
            venduti=15, consigliati=35, entro="09/12/2026"):
    riga = stock.RigaStock(sku=sku, asin="B0TEST", disponibili=disponibili, in_arrivo=in_arrivo,
                           venduti_30gg=venduti, consigliati=consigliati, entro=entro,
                           alert_amazon=None)
    return stato_stock.Avviso(sku, zona, motivo, riga)


def test_oggetto_conta_gli_sku_di_un_cliente_solo():
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("Reladol_300"), _avviso("CALMAPROST_FBA")],
                                  bozza=None, candidati=[], saltati=[], errore=None)]
    assert avviso_mail.oggetto(blocchi) == "[STOCK] ECM — 2 SKU da riassortire"


def test_oggetto_con_piu_clienti_li_nomina():
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("A")], None, [], [], None),
               avviso_mail.Blocco("Arcipelago", [_avviso("B")], None, [], [], None)]
    assert avviso_mail.oggetto(blocchi) == "[STOCK] Arcipelago, ECM — 2 SKU da riassortire"


def test_il_corpo_porta_i_numeri_che_servono_a_decidere():
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("Reladol_300")],
                                  bozza="/Users/x/Desktop/Lotto-ECM-2026-09-05.yaml",
                                  candidati=[], saltati=[], errore=None)]
    html = avviso_mail.corpo(blocchi, OGGI)
    for atteso in ("Reladol_300", "3", "22", "15", "50", "35", "09/12/2026",
                   "Lotto-ECM-2026-09-05.yaml"):
        assert atteso in html


def test_lo_sku_esaurito_non_stampa_una_copertura_infinita():
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("GermGEL_5L_FBA", motivo="nuovo",
                                                  zona=stock.CRITICO, disponibili=0, in_arrivo=0,
                                                  venduti=0, consigliati=5)],
                                  None, [], [], None)]
    html = avviso_mail.corpo(blocchi, OGGI)
    assert "inf" not in html.lower()
    assert "esaurito" in html.lower()


def test_il_cliente_vario_riceve_i_candidati_e_il_comando():
    blocchi = [avviso_mail.Blocco("Bedshire", [_avviso("PILLOW-FBA")], bozza=None,
                                  candidati=[("PILLOW-FBA", "PILLOW 10 PZ (6 scatole, 2 piani)")],
                                  saltati=[], errore=None)]
    html = avviso_mail.corpo(blocchi, OGGI)
    assert "PILLOW 10 PZ" in html
    assert "giro.py scatole" in html and "--modello" in html


def test_un_cliente_saltato_e_scritto_non_taciuto():
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("A")], None, [], [], None),
               avviso_mail.Blocco("Arcipelago", [], None, [], [], "report chiuso in stato FATAL")]
    html = avviso_mail.corpo(blocchi, OGGI)
    assert "Arcipelago" in html and "FATAL" in html


def test_manda_usa_il_ripiego_quando_il_destinatario_rifiuta():
    import smtplib
    tentativi = []

    def spedisci(config, destinatario, msg):
        tentativi.append(destinatario)
        if destinatario == "agenti@agencynewera.it":
            raise smtplib.SMTPRecipientsRefused({destinatario: (550, b"no such user")})

    config = {"smtp_host": "smtp.ionos.it", "smtp_port": 587, "mittente": "promozione@x.it",
              "password": "segreta", "destinatario": "agenti@agencynewera.it",
              "ripiego": "promozione@agencynewera.it"}
    usato = avviso_mail.manda(config, "oggetto", "<p>ciao</p>", spedisci=spedisci)
    assert tentativi == ["agenti@agencynewera.it", "promozione@agencynewera.it"]
    assert usato == "promozione@agencynewera.it"


def test_config_assente_lo_dice_invece_di_esplodere(tmp_path):
    assert avviso_mail.carica_config(str(tmp_path / "manca.yaml")) == {}
```

- [ ] **Step 2: Lancia i test e verifica che falliscano**

Run: `python3 -m pytest tests/test_avviso_mail.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'avviso_mail'`

- [ ] **Step 3: Scrivi `scripts/avviso_mail.py`**

```python
"""La mail del giro dello stock (DESIGN §14). Mail e non Telegram: la copertura
si misura in giorni, e una mail si inoltra a chi prepara la spedizione."""
from __future__ import annotations
import html as _html
import os
import smtplib
from dataclasses import dataclass, field
from datetime import date
from email.mime.text import MIMEText

import yaml

CONFIG_DEFAULT = "~/.config/newera-amazon/spedizioni/mail.yaml"
_MOTIVI = {"nuovo": "da riassortire", "peggiorato": "peggiorato",
           "sollecito": "ancora fermo"}


@dataclass
class Blocco:
    cliente: str
    avvisi: list
    bozza: str | None = None
    candidati: list = field(default_factory=list)
    saltati: list = field(default_factory=list)
    errore: str | None = None


def oggetto(blocchi: list[Blocco]) -> str:
    quanti = sum(len(b.avvisi) for b in blocchi)
    nomi = ", ".join(sorted(b.cliente for b in blocchi if b.avvisi))
    return f"[STOCK] {nomi} — {quanti} SKU da riassortire"


def _copertura(riga) -> str:
    """Un esaurito ha copertura infinita per costruzione: scriverlo come 'inf'
    sarebbe una presa in giro (DESIGN §14)."""
    if riga.disponibili + riga.in_arrivo == 0:
        return "esaurito"
    if riga.copertura_giorni == float("inf"):
        return "nessuna vendita"
    return f"{riga.copertura_giorni:.0f} gg"


def corpo(blocchi: list[Blocco], oggi: date) -> str:
    p = [f"<p>Giro stock del {oggi.strftime('%d/%m/%Y')}.</p>"]
    for b in blocchi:
        p.append(f"<h3>{_html.escape(b.cliente)}</h3>")
        if b.errore:
            p.append(f"<p><b>Saltato</b>: {_html.escape(b.errore)}</p>")
            continue
        p.append("<table cellpadding='6' border='1' style='border-collapse:collapse'>"
                 "<tr><th>SKU</th><th>disp.</th><th>in arrivo</th><th>venduti 30gg</th>"
                 "<th>copertura</th><th>consigliati</th><th>entro</th><th></th></tr>")
        for a in b.avvisi:
            r = a.riga
            p.append(f"<tr><td>{_html.escape(a.sku)}</td><td>{r.disponibili}</td>"
                     f"<td>{r.in_arrivo}</td><td>{r.venduti_30gg}</td><td>{_copertura(r)}</td>"
                     f"<td>{r.consigliati or '—'}</td><td>{_html.escape(r.entro or '—')}</td>"
                     f"<td>{_MOTIVI.get(a.motivo, a.motivo)}</td></tr>")
        p.append("</table>")
        if b.bozza:
            p.append(f"<p>Bozza pronta: <code>{_html.escape(b.bozza)}</code><br>"
                     "Completa <code>data_pronta</code> e lancia "
                     "<code>giro.py prepara &lt;file&gt;</code>.</p>")
        for sku, testo in b.candidati:
            p.append(f"<p>{_html.escape(sku)}: nessuna spedizione tipo. Modelli possibili — "
                     f"{_html.escape(testo)}.<br>Per la bozza: "
                     f"<code>giro.py scatole \"{_html.escape(b.cliente)}\" "
                     f"{_html.escape(sku)}=&lt;qta&gt; --modello \"&lt;nome&gt;\"</code></p>")
        for s in b.saltati:
            p.append(f"<p>Piano non letto: {_html.escape(s)}</p>")
    return "\n".join(p)


def carica_config(percorso: str | None = None) -> dict:
    p = os.path.expanduser(percorso or CONFIG_DEFAULT)
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _spedisci(config, destinatario, msg) -> None:
    with smtplib.SMTP(config["smtp_host"], int(config["smtp_port"]), timeout=30) as server:
        server.starttls()
        server.login(config["mittente"], config["password"])
        server.sendmail(config["mittente"], [destinatario], msg.as_string())


def manda(config: dict, oggetto_testo: str, corpo_html: str, spedisci=None) -> str:
    """Torna il destinatario che ha accettato. Se la casella degli agenti non
    esiste ancora si ripiega su quella nota, e chi chiama lo scrive nel log."""
    spedisci = spedisci or _spedisci
    msg = MIMEText(corpo_html, "html", "utf-8")
    msg["From"] = config["mittente"]
    msg["Subject"] = oggetto_testo
    for destinatario in (config["destinatario"], config.get("ripiego")):
        if not destinatario:
            continue
        msg.replace_header("To", destinatario) if "To" in msg else msg.add_header("To", destinatario)
        try:
            spedisci(config, destinatario, msg)
            return destinatario
        except smtplib.SMTPRecipientsRefused:
            continue
    raise RuntimeError(f"nessun destinatario ha accettato la mail: {oggetto_testo!r}")
```

- [ ] **Step 4: Lancia i test e verifica che passino**

Run: `python3 -m pytest tests/test_avviso_mail.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/avviso_mail.py tests/test_avviso_mail.py
git commit -m "feat(stock): la mail dell'avviso, col ripiego quando agenti@ non esiste ancora"
```

---

### Task 8: il giro — `giro.py stock`, il LaunchAgent, la documentazione

**Files:**
- Modify: `scripts/clienti.py` (aggiungi `elenco`), `scripts/giro.py` (verbo `stock`), `SKILL.md`
- Create: `scripts/giro_stock.sh`, `launchd/it.agencynewera.amazon-spedizioni-stock.plist`, `logs/.gitkeep`
- Test: `tests/test_giro.py` (in coda)

**Interfaces:**
- Consumes: tutto quanto sopra.
- Produces: `clienti.elenco(percorso=_FILE_DEFAULT) -> list[str]`, `giro.stock(fabbrica, clienti_da_fare, oggi, adesso, prova=False, manda=None) -> str`, CLI `giro.py stock [CLIENTE ...] [--prova]`. `fabbrica` è `spapi_client.crea_client` iniettabile: nei test è una lambda che torna il `ClienteFinto`.

**Quali clienti.** Quelli scritti in `clienti.yaml` — oggi quattro (Phy to Pet, Bedshire, Arcipelago, ECM), non i nove che hanno credenziali in `~/scripts/.env`: senza `marketplace_id` non si sa che report chiedere. Aggiungere un cliente al giro = aggiungerlo a `clienti.yaml`. (Correzione a `DESIGN.md` §14, che dice "nove clienti": va corretta nello stesso commit.)

**Niente `claude -p`.** A differenza della sentinella Ads, qui non c'è niente da giudicare: il giro è deterministico e gira in Python puro. Costa meno, è testabile, e non può inventarsi un numero.

- [ ] **Step 1: Scrivi il test che fallisce**

```python
# tests/test_giro.py (in coda)
def test_stock_avvisa_una_volta_e_scrive_la_bozza_al_cliente_regolare(tmp_path, monkeypatch):
    """Arcipelago: tre spedizioni identiche → spedizione tipo → bozza da sola."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(
        "Country\tProduct Name\tFNSKU\tMerchant SKU\tASIN\tCondition\tSupplier\t"
        "Units Sold Last 30 Days\tTotal Units\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tGin\tX0\t138_FBA\tB0F94GXPN5\tNew\t--\t40\t0\t0\t12\t\t100\t09/12/2026\n")
    for i, data in enumerate(["2026-07-16", "2026-06-11", "2026-04-24"]):
        c.spedito(f"wf{i}", f"{data}T10:00:00Z",
                  [scatola_finta("138_FBA", 3, 2.22, 32, 42.7, 25.3, quantita=36,
                                 modello="PALLET 36 PACCHI")])
    mandate = []
    testo = giro.stock(lambda _: c, ["Arcipelago"], oggi=date(2026, 9, 5),
                       adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc),
                       manda=lambda ogg, html: mandate.append(ogg) or "agenti@agencynewera.it")
    assert mandate == ["[STOCK] Arcipelago — 1 SKU da riassortire"]
    bozza = (tmp_path / "Desktop" / "Lotto-Arcipelago-2026-09-05.yaml").read_text()
    assert "quantita: 34" in bozza      # 100 pz arrotondati: 34 colli da 3 = 102
    assert "PALLET 36 PACCHI" in bozza
    assert "Bozza" in testo


def test_stock_secondo_giro_non_manda_niente(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\t138_FBA\tB0F94GXPN5\t40\t0\t12\t\t100\t09/12/2026\n")
    for i, data in enumerate(["2026-07-16", "2026-06-11", "2026-04-24"]):
        c.spedito(f"wf{i}", f"{data}T10:00:00Z",
                  [scatola_finta("138_FBA", 3, 2.22, 32, 42.7, 25.3, quantita=36,
                                 modello="PALLET 36 PACCHI")])
    argomenti = dict(oggi=date(2026, 9, 5),
                     adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc))
    mandate = []
    giro.stock(lambda _: c, ["Arcipelago"], manda=lambda o, h: mandate.append(o), **argomenti)
    giro.stock(lambda _: c, ["Arcipelago"], oggi=date(2026, 9, 6),
               adesso=datetime(2026, 9, 6, 11, 0, tzinfo=timezone.utc),
               manda=lambda o, h: mandate.append(o))
    assert len(mandate) == 1


def test_stock_in_prova_non_manda_e_non_salva(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tSKU-1\tB0TEST\t30\t0\t2\t\t50\t09/09/2026\n")
    import stato_stock
    testo = giro.stock(lambda _: c, ["ECM"], oggi=date(2026, 9, 5),
                       adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc), prova=True,
                       manda=lambda o, h: pytest.fail("in prova non si manda niente"))
    assert "PROVA" in testo and "SKU-1" in testo
    assert stato_stock.carica("ECM") == {}      # niente stato: domani riavvisa


def test_stock_nomina_il_cliente_saltato_e_va_avanti(tmp_path, monkeypatch):
    """Un report FATAL su un cliente non deve far saltare il giro degli altri,
    e deve comparire nella mail: il silenzio sarebbe una bugia."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    rotto, sano = ClienteFinto(), ClienteFinto()
    rotto.prepara_report("Country\tMerchant SKU\n", stato="FATAL")
    sano.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tSKU-1\tB0TEST\t30\t0\t2\t\t50\t09/09/2026\n")
    corpi = []
    giro.stock(lambda nome: rotto if nome == "Arcipelago" else sano, ["Arcipelago", "ECM"],
               oggi=date(2026, 9, 5), adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc),
               manda=lambda o, html: corpi.append(html))
    assert len(corpi) == 1 and "FATAL" in corpi[0] and "SKU-1" in corpi[0]


def test_stock_senza_niente_da_dire_non_manda_niente(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tSKU-1\tB0TEST\t10\t0\t500\t\t0\t\n")
    testo = giro.stock(lambda _: c, ["ECM"], oggi=date(2026, 9, 5),
                       adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc),
                       manda=lambda o, h: pytest.fail("niente da dire, niente mail"))
    assert "niente da segnalare" in testo.lower()
```

- [ ] **Step 2: Lancia i test e verifica che falliscano**

Run: `python3 -m pytest tests/test_giro.py -q -k stock`
Expected: FAIL, `AttributeError: module 'giro' has no attribute 'stock'`

- [ ] **Step 3: Aggiungi `clienti.elenco`**

```python
def elenco(percorso: str = _FILE_DEFAULT) -> list[str]:
    """I clienti configurati, nell'ordine del file. Il giro dello stock parte
    da qui e non dalle credenziali in ~/scripts/.env: senza marketplace_id non
    si sa che report chiedere."""
    with open(percorso) as f:
        return list((yaml.safe_load(f) or {}).keys())
```

- [ ] **Step 4: Aggiungi `giro.stock` in `scripts/giro.py`**

```python
def _blocco_cliente(client, cliente: str, soglie: dict, oggi, adesso, prova: bool):
    """Un cliente: report, zone, transizioni, e — se il cliente ripete sempre la
    stessa spedizione — la bozza già scritta."""
    cfg = clienti.carica_cliente(cliente)
    paese = cfg["indirizzo_origine"]["countryCode"]
    righe = stock_mod.leggi_stock(client, paese, adesso, marketplace_id=cfg["marketplace_id"])
    zone = [(r, stock_mod.zona(r, stock_mod.soglia_per(soglie, cliente, r.sku))) for r in righe]
    stato = {} if prova else stato_stock.carica(cliente)
    avvisi, nuovo = stato_stock.da_avvisare([(r, z) for r, z in zone if z != stock_mod.OK], stato, oggi)
    blocco = avviso_mail.Blocco(cliente, avvisi)
    if not avvisi:
        return blocco, nuovo
    piani, blocco.saltati = modelli_mod.leggi_piani(client, adesso)
    per_sku = modelli_mod.modelli_per_sku(piani)
    scelte, quantita = {}, {}
    for a in avvisi:
        tipo = ricorrenza.spedizione_tipo(piani, a.sku)
        se_noti = per_sku.get(a.sku, [])
        if tipo and se_noti:
            scelte[a.sku] = modelli_mod.scegli(per_sku, a.sku, tipo.modello)
            quantita[a.sku] = modelli_mod.a_colli_pieni(max(a.riga.consigliati, 1),
                                                        scelte[a.sku].pezzi)
        else:
            blocco.candidati.append((a.sku, "; ".join(
                f"{m.nome} ({m.scatole} scatole, {m.piani} piani)" for m in se_noti[:3])
                or "nessun modello in storico"))
    if scelte:
        istantanea = modelli_mod.istantanea_da(cliente, scelte, adesso)
        blocco.bozza = scatole_mod.scrivi_bozza(
            cliente, scatole_mod.bozza_lotto(istantanea, quantita), adesso)
    return blocco, nuovo


def stock(fabbrica, clienti_da_fare: list[str], oggi, adesso,
          prova: bool = False, manda=None) -> str:
    """DESIGN §14. Un report per cliente, una mail sola per tutti, e solo se
    qualcosa è cambiato. Un cliente che fallisce non ferma gli altri: viene
    nominato nella mail."""
    blocchi, stati = [], {}
    for cliente in clienti_da_fare:
        try:
            blocco, nuovo = _blocco_cliente(fabbrica(cliente), cliente, stock_mod.carica_soglie(),
                                            oggi, adesso, prova)
            stati[cliente] = nuovo
        except (SpApiError, ValueError, OSError) as e:
            blocchi.append(avviso_mail.Blocco(cliente, [], errore=f"{type(e).__name__}: {e}"))
            continue
        if blocco.avvisi:
            blocchi.append(blocco)
    da_dire = [b for b in blocchi if b.avvisi or b.errore]
    if not da_dire:
        return "Giro stock: niente da segnalare."
    testo = avviso_mail.corpo(da_dire, oggi)
    if prova:
        return "PROVA — mail non inviata, stato non salvato:\n" + testo
    manda = manda or (lambda ogg, html: avviso_mail.manda(avviso_mail.carica_config(), ogg, html))
    destinatario = manda(avviso_mail.oggetto(da_dire), testo)
    for cliente, nuovo in stati.items():
        stato_stock.salva(cliente, nuovo)
    righe = [f"Giro stock: mail a {destinatario} — " +
             ", ".join(f"{b.cliente} {len(b.avvisi)} SKU" for b in da_dire if b.avvisi)]
    righe += [f"  bozza: {b.bozza}" for b in da_dire if b.bozza]
    righe += [f"  saltato: {b.cliente} — {b.errore}" for b in da_dire if b.errore]
    return "\n".join(righe)
```

**Attenzione a due nomi.** La funzione si chiama `stock` e il modulo pure: per questo il modulo si importa come `stock_mod`, come `giro.py` fa già con `lotto as lotto_mod` e `scatole as scatole_mod`. E `modelli.py` (nuovo, i modelli d'imballaggio) non è `modello.py` (esistente, i dati canonici della spedizione, da cui `giro.py` importa già `PianoSpedizione` e `Scatola`): sono due moduli diversi con nomi quasi uguali.

Aggiungi in testa a `giro.py`: `import avviso_mail`, `import modelli as modelli_mod`, `import ricorrenza`, `import stato_stock`, `import stock as stock_mod` (`clienti` è già importato).

- [ ] **Step 5: Aggiungi il verbo alla CLI**

```python
    f = sub.add_parser("stock", help="avvisa per mail gli SKU da riassortire (sola lettura)")
    f.add_argument("clienti", nargs="*", metavar="CLIENTE",
                   help="quali clienti; senza argomenti, tutti quelli di clienti.yaml")
    f.add_argument("--prova", action="store_true",
                   help="stampa la mail invece di mandarla, e non salva lo stato")
```

e nel dispatch:

```python
        if args.verbo == "stock":
            adesso = datetime.now(timezone.utc)
            print(stock(spapi_client.crea_client, args.clienti or clienti.elenco(),
                        oggi=adesso.date(), adesso=adesso, prova=args.prova))
            return 0
```

- [ ] **Step 6: Scrivi `scripts/giro_stock.sh`**

```bash
#!/bin/bash
# Giro quotidiano dello stock (DESIGN §14). Lanciato dal LaunchAgent alle 11:00.
# Python puro, niente claude -p: qui non c'è niente da giudicare.
cd "$(dirname "$0")" || exit 1
exec /usr/bin/python3 giro.py stock
```

Poi `chmod +x scripts/giro_stock.sh`.

- [ ] **Step 7: Scrivi `launchd/it.agencynewera.amazon-spedizioni-stock.plist`**

Copia struttura e commenti dal plist della sentinella Ads (`~/.claude/skills/amazon-ads-sentinel/launchd/it.agencynewera.amazon-ads-sentinel.plist`), cambiando: `Label` `it.agencynewera.amazon-spedizioni-stock`; `ProgramArguments` `/bin/bash` + il percorso assoluto di `scripts/giro_stock.sh`; `WorkingDirectory` la cartella della skill; `StartCalendarInterval` **Hour 11, Minute 0**; log in `logs/stock.out.log` e `logs/stock.err.log`. La sentinella Ads gira anch'essa alle 11:00 ma scrive su Telegram: nessun conflitto, due canali diversi. **Non caricarlo** (`launchctl load`) prima del Task 10.

- [ ] **Step 8: Aggiorna `SKILL.md` e correggi `DESIGN.md`**

In `SKILL.md`, una sezione "Stock e riassortimento" con: cosa fa, il comando `giro.py stock --prova`, dove sta lo stato, dove stanno le soglie, e che è sola lettura. In `DESIGN.md` §14 correggi "Nove clienti, un report a testa" in "Un report per ogni cliente di `clienti.yaml` (quattro oggi)".

- [ ] **Step 9: Lancia tutti i test**

Run: `python3 -m pytest tests -q`
Expected: tutti verdi

- [ ] **Step 10: Commit**

```bash
git add scripts/giro.py scripts/clienti.py scripts/giro_stock.sh launchd/ SKILL.md DESIGN.md tests/test_giro.py
git commit -m "feat(stock): giro.py stock - il giro completo, la mail e il LaunchAgent delle 11:00"
```

---

### Task 9: smettere di sporcare i modelli del cliente — ANNULLATO (2026-09-05)

> **Non si fa.** Lo Step 1 di questo task era la verifica che doveva autorizzarlo, ed è
> fallita: nel modello ufficiale `fulfillmentInbound_2024-03-20`, `templateName` esiste
> solo in `Box` (lettura) e **non** in `BoxInput`, che è ciò che `setPackingInformation`
> manda. Nessun endpoint e nessuna definizione dell'API riguarda i modelli d'imballaggio.
> Il task si ferma qui come il suo stesso Step 1 prescrive; esito scritto in `DESIGN.md` §14.
> Gli step sotto restano per memoria di cosa si sarebbe fatto se il campo ci fosse stato.

**Files:**
- Modify: `scripts/modello.py` (campo `modello` sulla dataclass `Scatola`), `scripts/lotto.py:133` (il costruttore lo legge dal YAML), `scripts/scatole.py` (`bozza_lotto` scrive il campo), `scripts/imballaggio.py` (`_scatola_a_box` lo manda)
- Test: `tests/test_lotto.py`, `tests/test_scatole.py`, `tests/test_imballaggio.py` (in coda a ciascuno)

**Interfaces:**
- Consumes: `modello.Scatola` (nuovo campo opzionale `modello: str | None = None`). **Attenzione**: la dataclass `Scatola` sta in `scripts/modello.py`, non in `lotto.py` — `lotto.py` la importa (`from modello import RigaSpedizione, Scatola`) e la costruisce alla riga 133. Le righe sono `RigaSpedizione`, non `Riga`.
- Produces: la chiave `templateName` in `setPackingInformation`, quando il lotto la porta.

**Il fatto.** La spedizione Arcipelago del 03/09, fatta da noi via API, ha prodotto 36 modelli usa e getta (`P1 - B1` … `P1 - B36`) al posto dell'unico `PALLET 36 PACCHI` che il cliente usava da dieci spedizioni. Non mandiamo `templateName` e Amazon se lo inventa: ogni nostra spedizione degrada lo storico che il Task 3 legge.

- [ ] **Step 1: Verifica che il campo esista in scrittura, prima di scrivere codice**

Run:
```bash
curl -s https://raw.githubusercontent.com/amzn/selling-partner-api-models/main/models/fulfillment-inbound-api-model/fulfillmentInbound_2024-03-20.json \
  | python3 -c "import json,sys; m=json.load(sys.stdin)['definitions']; print({k: sorted(v.get('properties', {})) for k,v in m.items() if k in ('Box','BoxInput')})"
```
Expected: `BoxInput` elenca `templateName` fra le proprietà.
**Se non c'è**: fermati, scrivi l'esito in `DESIGN.md` §14 (il paragrafo "Il verso che mancava" lo prevede) e salta al Task 10. Non inventare un campo che l'API non ha.

- [ ] **Step 2: Scrivi i test che falliscono**

```python
# tests/test_lotto.py (in coda)
def test_una_scatola_puo_dichiarare_il_suo_modello(tmp_path):
    f = tmp_path / "l.yaml"
    f.write_text(
        'cliente: "Arcipelago"\n'
        'righe:\n  - {sku: "138_FBA", quantita: 108}\n'
        'scatole:\n'
        '  - {quantita: 36, peso_kg: 2.22, lunghezza_cm: 32, larghezza_cm: 42.7, altezza_cm: 25.3,\n'
        '     modello: "PALLET 36 PACCHI", righe: [{sku: "138_FBA", quantita: 3}]}\n'
        'data_pronta: 2026-09-08T08:00Z\n')
    assert lotto.carica(str(f)).scatole[0].modello == "PALLET 36 PACCHI"


def test_il_modello_e_facoltativo(tmp_path):
    f = tmp_path / "l.yaml"
    f.write_text(
        'cliente: "ECM"\n'
        'righe:\n  - {sku: "SKU-1", quantita: 10}\n'
        'scatole:\n'
        '  - {quantita: 1, peso_kg: 2, lunghezza_cm: 30, larghezza_cm: 40, altezza_cm: 30,\n'
        '     righe: [{sku: "SKU-1", quantita: 10}]}\n'
        'data_pronta: 2026-09-08T08:00Z\n')
    assert lotto.carica(str(f)).scatole[0].modello is None
```

```python
# tests/test_scatole.py (in coda)
def test_la_bozza_dichiara_il_modello_da_riusare():
    """Il nome del modello era già nel commento: metterlo anche nel campo lo fa
    tornare ad Amazon, così lo storico del cliente non si sporca (DESIGN §14)."""
    istantanea = {"cliente": "Arcipelago", "sku": {"138_FBA": {
        "pezzi": 3, "lunghezza_cm": 32, "larghezza_cm": 42.7, "altezza_cm": 25.3,
        "peso_kg": 2.22, "modello": "PALLET 36 PACCHI", "asin": "B0F94GXPN5",
        "ultima_data": "2026-07-16", "varianti": []}}}
    testo = scatole.bozza_lotto(istantanea, {"138_FBA": 108})
    assert 'modello: "PALLET 36 PACCHI"' in testo
    import yaml
    assert yaml.safe_load(testo)["scatole"][0]["modello"] == "PALLET 36 PACCHI"
```

```python
# tests/test_imballaggio.py (in coda)
def test_manda_il_modello_ad_amazon_quando_il_lotto_lo_porta():
    # test_imballaggio.py importa già `from modello import RigaSpedizione, Scatola`
    scatola = Scatola(quantita=36, peso_kg=2.22, lunghezza_cm=32, larghezza_cm=42.7,
                      altezza_cm=25.3, righe=[RigaSpedizione("138_FBA", 3)],
                      modello="PALLET 36 PACCHI")
    box = imballaggio._scatola_a_box(scatola, "pg1", 1, {"138_FBA": ("NONE", "NONE")})
    assert box["templateName"] == "PALLET 36 PACCHI"


def test_senza_modello_non_manda_la_chiave_vuota():
    scatola = Scatola(quantita=1, peso_kg=2, lunghezza_cm=30, larghezza_cm=40,
                      altezza_cm=30, righe=[RigaSpedizione("SKU-1", 10)])
    box = imballaggio._scatola_a_box(scatola, "pg1", 1, {"SKU-1": ("NONE", "NONE")})
    assert "templateName" not in box
```

- [ ] **Step 3: Lancia i test e verifica che falliscano**

Run: `python3 -m pytest tests/test_lotto.py tests/test_scatole.py tests/test_imballaggio.py -q -k "modello"`
Expected: FAIL, `TypeError: __init__() got an unexpected keyword argument 'modello'`

- [ ] **Step 4: Implementa nei tre moduli**

In `scripts/modello.py`, aggiungi alla dataclass `Scatola` il campo `modello: str | None = None` **dopo** `righe` (che ha già un default, quindi ogni campo successivo deve averlo).

In `scripts/lotto.py`, nella costruzione della `Scatola` (riga 133 circa, dentro il `for` sulle scatole), aggiungi l'argomento dopo `righe=`:

```python
                righe=_righe(s.get("righe"), f"scatola {i}"),
                modello=(str(s["modello"]).strip() or None) if s.get("modello") else None,
```

Il campo non entra nella lista `mancanti`: è facoltativo, e un lotto dettato a mano non deve dichiararlo.

In `scatole.bozza_lotto`, nella riga del collo pieno, aggiungi il campo dopo le misure e **lascia il commento**, che dice una cosa diversa (da quale piano viene):

```python
        if pieni:
            modello = f', modello: {json.dumps(c["modello"])}' if c.get("modello") else ""
            righe.append(f"  - {{quantita: {pieni}, peso_kg: {c['peso_kg']:g}, {misure}{modello},")
```

In `imballaggio._scatola_a_box`, dopo la costruzione di `box`:

```python
    if s.modello:
        box["templateName"] = s.modello      # DESIGN §14: riusa il modello del cliente
```

- [ ] **Step 5: Lancia tutti i test**

Run: `python3 -m pytest tests -q`
Expected: tutti verdi. Se `test_scatole.py` ha asserzioni sul testo esatto della bozza, aggiornale: ora la riga porta anche `modello`.

- [ ] **Step 6: Commit**

```bash
git add scripts/lotto.py scripts/scatole.py scripts/imballaggio.py tests/
git commit -m "fix(imballaggio): manda templateName, cosi' smettiamo di sporcare i modelli del cliente"
```

- [ ] **Step 7: Annota il controllo differito**

In `PLAN.md` Task 11, aggiungi uno step: alla prossima spedizione vera, rileggere `GET /inboundPlans/{id}/boxes` e verificare che `templateName` sia quello mandato e non un `P1 - Bn`. È l'unica prova che conta: il modello dell'API dice che il campo esiste, non che Amazon lo rispetti.

---

### Task 10: collaudo dal vivo (sola lettura, nessuna mail)

**Files:** nessuno da modificare. Output sul Desktop e in `~/.config/newera-amazon/spedizioni/stock/`.

**Prerequisito:** `~/.config/newera-amazon/spedizioni/mail.yaml` scritto da Mariano (host, porta, mittente `promozione@agencynewera.it`, password, destinatario `agenti@agencynewera.it`, ripiego `promozione@agencynewera.it`). Non serve per `--prova`, che non manda niente: serve solo prima del Task 10 Step 5.

- [ ] **Step 1: ECM, la lista attesa**

Run: `cd ~/.claude/skills/amazon-spedizioni/scripts && python3 giro.py stock ECM --prova`
Expected: sette SKU, gli stessi misurati il 2026-09-05 — `CALMAPROST_FBA`, `Reladol_300`, `PROBIOLAX_FBA`, `ARTINORM_2_FBA`, `GermGEL_5L_FBA`, `Int_Q10`, `EN-GYM0-KBDL_FBA` — e nessuna riga di mercati esteri. Se compaiono SKU con `Country` diverso da IT, il filtro del Task 1 è rotto. Se lo stato viene salvato, `--prova` è rotto.

- [ ] **Step 2: Arcipelago, il cliente regolare**

Run: `python3 giro.py stock Arcipelago --prova`
Expected: se `138_FBA` è sotto soglia, la bozza cita **`PALLET 36 PACCHI`** e non `P1 - B1` — è la prova che la regola dei modelli funziona sui dati veri. Se `138_FBA` è pieno, il giro dice "niente da segnalare": va bene lo stesso, e lo si annota.

- [ ] **Step 3: il giro completo, ancora senza mandare**

Run: `python3 giro.py stock --prova`
Expected: i quattro clienti di `clienti.yaml`, una mail sola composta, nessun invio, nessuno stato salvato. Misura quanto dura: quattro report più i piani spediti. Se supera i 10 minuti, annota il tempo (il LaunchAgent non ha fretta, ma va saputo).

- [ ] **Step 4: la transizione**

Run: due volte di fila `python3 giro.py stock ECM` (**senza** `--prova`, con `mail.yaml` a posto)
Expected: la prima manda una mail e salva `~/.config/newera-amazon/spedizioni/stock/ECM.json`; la seconda stampa "niente da segnalare" e **non** manda niente. È la regola anti-rumore, verificata sul vivo e non solo nei test.

- [ ] **Step 5: carica il LaunchAgent**

Run: `launchctl load ~/Library/LaunchAgents/it.agencynewera.amazon-spedizioni-stock.plist` (dopo aver copiato o linkato il plist), poi `launchctl list | grep spedizioni-stock`
Expected: il job è elencato. Il giorno dopo alle 11:00 controlla `logs/stock.out.log`.

- [ ] **Step 6: annota l'esito** in `~/nova/CLIENTI/ecm.md` e `arcipelago-gin.md`, nella memoria di progetto (`project_amazon_spedizioni.md`), e spunta gli step di questo task.

---

## Self-review (fatto scrivendo il piano)

- **Copertura della spec §14**: sorgente e Reports API (Task 1), filtro per paese e inaffidabilità di `Alert` (Task 1), le tre soglie e le eccezioni (Task 2), i modelli indicizzati per modello con i nomi automatici in fondo (Task 3), la spedizione tipo coi multipli (Task 4), la bozza a colli pieni e `--modello` (Task 5), la transizione con le cinque regole (Task 6), la mail con oggetto taggato e ripiego (Task 7), il giro, il LaunchAgent delle 11:00 e i documenti (Task 8), il verso che mancava sui modelli (Task 9), il collaudo su ECM e Arcipelago (Task 10).
- **Nomi coerenti fra i task**: `RigaStock`, `copertura_giorni`, `leggi_stock`, `zona`, `soglia_per`, `carica_soglie`, `PianoSpedito`, `leggi_piani`, `Modello`, `modelli_per_sku`, `e_automatico`, `scegli`, `a_colli_pieni`, `istantanea_da`, `Tipo`, `spedizione_tipo`, `Avviso`, `da_avvisare`, `Blocco`, `oggetto`, `corpo`, `manda`, `giro.stock`, `clienti.elenco`.
- **Deviazioni dalla spec, dichiarate**: (a) il giro copre i clienti di `clienti.yaml` — quattro oggi, non nove: senza `marketplace_id` non si sa che report chiedere, e il Task 8 corregge la riga della spec; (b) `--prova` scrive comunque la bozza sul Desktop (file innocuo e utile al collaudo) ma non manda mail e non salva stato; (c) `Modello` non porta l'ASIN, quindi l'istantanea sintetica ha `asin: None` — `bozza_lotto` non lo usa, `tabella` non va chiamata su di essa.
- **Rischio noto**: il Task 9 dipende da un campo che l'API espone in lettura ma che non abbiamo mai provato in scrittura. Lo Step 1 lo verifica sul modello ufficiale prima di scrivere codice, e la prova vera resta la prossima spedizione.
