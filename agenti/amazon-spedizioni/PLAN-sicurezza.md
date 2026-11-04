# Sicurezza del giro — piano di esecuzione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** chiudere i sette rilievi del collaudo di sicurezza del 06/09/2026 (DESIGN §17) senza cambiare il comportamento del giro quando tutto va bene.

**Architecture:** otto correzioni piccole e indipendenti, ciascuna col suo test che prima fallisce. Due lucchetti `fcntl.flock` (registro per cliente, piano per `conferma --vai`), l'orologio da parete per il token, `yaml.YAMLError` nel contratto d'errore, il nome cliente sanificato nei percorsi, l'ambiente di Chrome in lista bianca, tre difese sui file scritti, il troncamento dei corpi d'errore.

**Tech Stack:** Python 3.9 (`/usr/bin/python3`, quello del LaunchAgent), pytest, `fcntl` della libreria standard. Nessuna dipendenza nuova.

**Spec:** `DESIGN.md` §17 (le decisioni) — i rilievi con le prove stanno nella memoria di progetto (`project_amazon_spedizioni.md`, "Collaudo di sicurezza del 2026-09-06 sera").

## Global Constraints

- Test con `/usr/bin/python3 -m pytest tests -q -p no:cacheprovider` dalla radice del repo; `tests/conftest.py` mette `scripts/` nel path.
- Modifiche chirurgiche: solo le righe del rilievo, stile dei file esistenti, commenti in italiano che dicono il perché.
- Un commit per task, messaggio in italiano nello stile del log (`fix(<modulo>): <cosa, al presente>`), con il trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Niente `git add -A`: nel worktree possono esserci modifiche di altre sessioni.

---

### Task 1: Registro — un lucchetto per cliente

**Files:**
- Modify: `scripts/registro.py` (import, `_lucchetto`, e ogni `registra_*`)
- Test: `tests/test_registro.py`

**Interfaces:**
- Produces: `registro._lucchetto(cliente: str)` context manager, lucchetto esclusivo bloccante su `~/.config/newera-amazon/spedizioni/<nome_cliente>.lock`.

- [ ] **Step 1: il test che fallisce**

```python
def test_venti_scritture_insieme_non_perdono_voci_e_non_corrompono_il_file(tmp_path, monkeypatch):
    """Collaudo del 06/09: venti processi su registra_tentativo perdevano voci
    e al terzo giro corrompevano il JSON (stesso .tmp per tutti). Qui venti
    thread, con una lettura lenta che allarga la finestra della corsa: ognuno
    apre il suo lucchetto, quindi si mettono in fila."""
    import threading
    import time
    monkeypatch.setenv("HOME", str(tmp_path))
    originale = registro._carica

    def carica_lenta(percorso):
        log = originale(percorso)
        time.sleep(0.01)
        return log

    monkeypatch.setattr(registro, "_carica", carica_lenta)
    righe = [{"sku": "SKU-1", "quantita": 1}]
    errori = []

    def uno():
        try:
            registro.registra_tentativo("Phy to Pet", righe)
        except Exception as e:      # il test deve vedere qualunque cosa
            errori.append(e)

    fili = [threading.Thread(target=uno) for _ in range(20)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()
    assert errori == []
    voci = registro.ultimo_lotto("Phy to Pet")
    assert len(voci) == 20
    assert len({v["lotto_id"] for v in voci}) == 20
```

- [ ] **Step 2: vederlo fallire** — `pytest tests/test_registro.py -k venti_scritture -q`. Atteso: FAIL, `len(voci)` molto sotto 20 e/o `errori` con `FileNotFoundError` sul `.tmp` conteso.

- [ ] **Step 3: il codice minimo**

In testa a `registro.py`: `import fcntl` e `from contextlib import contextmanager`. Poi, sotto `_percorso`:

```python
@contextmanager
def _lucchetto(cliente: str):
    """Un lucchetto per cliente attorno a ogni lettura-modifica-scrittura del
    registro (DESIGN §17). Senza, due `giro.py` insieme si perdono le voci a
    vicenda e — scrivendo lo stesso file di appoggio — corrompono il JSON.
    `flock` è per descrittore aperto: vale fra processi e fra thread, e cade
    da solo se il processo muore."""
    cartella = os.path.dirname(_percorso(cliente))
    with open(os.path.join(cartella, f"{_nome_cliente(cliente)}.lock"), "a") as lucchetto:
        fcntl.flock(lucchetto, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lucchetto, fcntl.LOCK_UN)
```

E ogni funzione che scrive fa lettura-modifica-scrittura dentro `with _lucchetto(cliente):` — `registra_tentativo` (dal `log = _carica(percorso)` alla `_salva`), `registra_piano`, `registra_piazzamento_confermato`, `registra_asn`, `registra_esito` (tutto il corpo), `registra_trasporto_confermato` e `registra_etichette` (il blocco dentro il `try:`, così anche un lucchetto che fallisce lascia il dato pagato nel messaggio).

- [ ] **Step 4: verde** — `pytest tests/test_registro.py -q`, poi la suite intera.
- [ ] **Step 5: commit** — `git add scripts/registro.py tests/test_registro.py` · `fix(registro): un lucchetto per cliente, venti scritture insieme non si perdono più`

### Task 2: Una sola `conferma --vai` per piano alla volta

**Files:**
- Modify: `scripts/giri.py` (import, `lucchetto`), `scripts/giro.py` (`conferma` diventa un involucro attorno a `_conferma`)
- Test: `tests/test_giri.py`, `tests/test_giro.py`

**Interfaces:**
- Produces: `giri.lucchetto(plan_id: str)` context manager, NON bloccante: alza `ValueError("un'altra conferma di <plan_id> è in corso: aspetta che finisca")` se il lucchetto è preso.
- Consumes: `giri._percorso(plan_id)` (valida il plan_id, torna `…/giri/<plan_id>.json`).

- [ ] **Step 1: i test che falliscono**

`tests/test_giri.py`:
```python
def test_il_lucchetto_del_piano_non_si_prende_due_volte(tmp_path, monkeypatch):
    """Due `conferma --vai` insieme leggerebbero la stessa foto e prenoterebbero
    entrambe: la seconda deve fermarsi subito, non aspettare."""
    monkeypatch.setenv("HOME", str(tmp_path))
    with giri.lucchetto("wf1"):
        with pytest.raises(ValueError, match="in corso"):
            with giri.lucchetto("wf1"):
                pass
    with giri.lucchetto("wf1"):      # rilasciato: si riprende
        pass
```

`tests/test_giro.py`:
```python
def test_due_conferme_con_vai_sullo_stesso_piano_non_passano_insieme(monkeypatch):
    """Collaudo del 06/09: due `conferma --vai` insieme leggevano la stessa
    foto e prenotavano entrambe (nessuna chiave di idempotenza). La seconda
    si ferma prima di qualunque POST; senza --vai il cancello si legge lo stesso."""
    _pdf_finto(monkeypatch)
    c = ClienteFinto()
    plan_id = _pronto(c)
    with giri.lucchetto(plan_id):          # "l'altra" conferma, ancora in corso
        with pytest.raises(ValueError, match="in corso"):
            giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
        assert c.post_fatte == []
        assert "Sto per prenotare" in giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, adesso=_ADESSO)
```

- [ ] **Step 2: vederli fallire** — `pytest tests/test_giri.py tests/test_giro.py -k "lucchetto or due_conferme" -q`. Atteso: `AttributeError: module 'giri' has no attribute 'lucchetto'`.

- [ ] **Step 3: il codice minimo**

`giri.py`, con `import fcntl` e `from contextlib import contextmanager`:
```python
@contextmanager
def lucchetto(plan_id: str):
    """Un solo `conferma --vai` per piano alla volta (DESIGN §17). Non aspetta:
    se un altro processo lo tiene alza subito — la seconda conferma dello
    stesso piano non ha niente di buono da fare, e
    `confirmTransportationOptions` non ha chiave di idempotenza."""
    percorso = _percorso(plan_id) + ".lock"
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"un'altra conferma di {plan_id} è in corso: aspetta che finisca") from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
```

`giro.py`: `import contextlib`; la `def conferma(` esistente diventa `def _conferma(` (corpo e docstring invariati) e sopra di lei:
```python
def conferma(client, plan_id: str, placement_option_id: str | None, trasporto: dict[str, str],
             finestre: dict[str, str] | None = None, vai: bool = False,
             pallet_etichette: int | dict[str, int] | None = None, verificato: bool = False,
             adesso: datetime | None = None) -> str:
    """Il cancello 🟡. Con `vai` un solo processo per piano alla volta (DESIGN
    §17): due `--vai` insieme leggerebbero la stessa foto e prenoterebbero
    entrambi. Senza `vai` non si spende e non si aspetta nessuno."""
    lucchetto = giri.lucchetto(plan_id) if vai else contextlib.nullcontext()
    with lucchetto:
        return _conferma(client, plan_id, placement_option_id, trasporto, finestre, vai,
                         pallet_etichette, verificato, adesso)
```

- [ ] **Step 4: verde** — i due test, poi la suite intera.
- [ ] **Step 5: commit** — `git add scripts/giri.py scripts/giro.py tests/test_giri.py tests/test_giro.py` · `fix(giro): una sola conferma --vai per piano alla volta`

### Task 3: Token LWA sull'orologio da parete

**Files:**
- Modify: `scripts/spapi_client.py:128,152` (`time.monotonic()` → `time.time()` solo per la scadenza del token)
- Test: `tests/test_spapi_client.py` (usa `_OrologioFinto` già definito lì)

- [ ] **Step 1: il test che fallisce**

```python
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
```

- [ ] **Step 2: vederlo fallire** — atteso `assert 1 == 2`.
- [ ] **Step 3: il codice minimo** — in `access_token`: `if self._token and time.time() < self._token_scade:` e `self._token_scade = time.time() + scadenza - 60`, con il commento: `# orologio da parete, non monotonic: su macOS il monotonic si ferma nel sonno (DESIGN §17)`.
- [ ] **Step 4: verde** — `pytest tests/test_spapi_client.py -q`, poi la suite.
- [ ] **Step 5: commit** — `fix(spapi): la scadenza del token si conta sull'orologio da parete, il sonno del Mac non la ferma`

### Task 4: `yaml.YAMLError` dentro il contratto d'errore

**Files:**
- Modify: `scripts/giro.py` (`import yaml`; `except` di `stock` riga ~674 e di `main`)
- Test: `tests/test_giro.py`

- [ ] **Step 1: i test che falliscono**

```python
def test_un_soglie_yaml_rotto_non_uccide_il_giro_dello_stock(tmp_path, monkeypatch):
    """Collaudo del 06/09: yaml.YAMLError non è ValueError e non era fra le
    eccezioni del ciclo per cliente: traceback grezzo, niente mail per nessuno."""
    import yaml
    (tmp_path / "Desktop").mkdir()
    monkeypatch.setattr(giro.stock_mod, "carica_soglie", lambda: yaml.safe_load("clienti: ["))
    mandate = []
    giro.stock(lambda _: ClienteFinto(), ["ECM"], oggi=date(2026, 9, 11),
               adesso=datetime(2026, 9, 11, 11, 0, tzinfo=timezone.utc),
               manda=lambda o, html: mandate.append(html) or "agenti@x.it")
    assert len(mandate) == 1
    assert "ECM" in mandate[0] and "ParserError" in mandate[0]


def test_un_clienti_yaml_rotto_esce_con_errore_parlante_non_traceback(monkeypatch, capsys):
    import yaml
    monkeypatch.setattr(giro.clienti, "elenco", lambda: yaml.safe_load("a: ["))
    assert giro.main(["stock"]) == 1
    assert "ERRORE:" in capsys.readouterr().err
```

- [ ] **Step 2: vederli fallire** — atteso: `yaml.parser.ParserError` non catturato in entrambi.
- [ ] **Step 3: il codice minimo** — `import yaml` fra gli import di `giro.py`; in `stock`: `except (SpApiError, ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as e:`; in `main`: `except (ValueError, SpApiError, yaml.YAMLError) as e:`.
- [ ] **Step 4: verde**, suite intera.
- [ ] **Step 5: commit** — `fix(giro): un YAML rotto è un errore parlante, non un traceback che spegne il giro`

### Task 5: Il nome cliente non compone mai da solo un percorso

**Files:**
- Modify: `scripts/etichette.py:98`, `scripts/etichette_fnsku.py` (`destinazione`), `scripts/giro.py` (`_scrivi_elenco_corrieri`, `_menu`)
- Test: `tests/test_etichette.py`, `tests/test_etichette_fnsku.py`, `tests/test_giro.py`

**Interfaces:**
- Consumes: `registro._nome_cliente(cliente) -> str` (già esiste: tutto ciò che non è lettera, numero, spazio, punto, trattino o underscore diventa `_`).
- Produces: `giro._percorso_elenco_corrieri(cliente, plan_id) -> str`, usato sia da chi scrive l'elenco sia da chi lo cancella.

- [ ] **Step 1: i test che falliscono**

`tests/test_etichette.py`:
```python
@patch("etichette.requests.get")
def test_un_nome_cliente_con_percorso_dentro_resta_sul_desktop(mock_get, tmp_path, monkeypatch):
    """Collaudo del 06/09: "Phy to Pet/../../" portava il PDF in $HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"payload": {"DownloadURL": "https://amazon-labels.example/x.pdf"}}
    mock_get.return_value = Mock(status_code=200, content=b"%PDF-1.4")
    percorso = scarica_etichette(client, "FBA1234ABCD", "Phy to Pet/../../", cartoni=_CARTONI)
    assert os.path.dirname(percorso) == str(tmp_path / "Desktop")
    assert os.path.exists(percorso)
```

`tests/test_etichette_fnsku.py` (aggiungere `import os`):
```python
def test_un_nome_cliente_con_percorso_dentro_resta_sul_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    base = etichette_fnsku.destinazione("ECM/../../", "wf1", "")
    assert os.path.dirname(base) == str(tmp_path / "Desktop")
```

`tests/test_giro.py`:
```python
def test_l_elenco_corrieri_resta_sul_desktop_anche_con_un_nome_cliente_strano(tmp_path):
    """Collaudo del 06/09: il file finiva in $HOME, e _menu lo cancella pure."""
    percorso = giro._scrivi_elenco_corrieri("Phy to Pet/../../", "wf1", [])
    assert os.path.dirname(percorso) == str(tmp_path / "Desktop")
    assert os.path.isfile(percorso)
```

- [ ] **Step 2: vederli fallire** — dirname diverso dal Desktop (o `FileNotFoundError` sulla cartella intermedia).
- [ ] **Step 3: il codice minimo**
  - `etichette.py`: `from registro import _nome_cliente`; `nome = f"Etichette-{_nome_cliente(cliente)}-{shipment_confirmation_id}.pdf"`.
  - `etichette_fnsku.py`: `from registro import _nome_cliente`; `nome = f"Etichette-FNSKU-{_nome_cliente(cliente)}-{plan_id[:10]}{suffisso}"`.
  - `giro.py`:
    ```python
    def _percorso_elenco_corrieri(cliente: str, plan_id: str) -> str:
        # Il nome cliente lo scrive Mariano: passa dal sanificatore del registro,
        # o un "../" porterebbe il file (e la sua cancellazione) fuori dal Desktop.
        return os.path.expanduser(f"~/Desktop/Corrieri-{registro._nome_cliente(cliente)}-{plan_id}.txt")
    ```
    usata in `_scrivi_elenco_corrieri` (`percorso = _percorso_elenco_corrieri(cliente, plan_id)`) e in `_menu` (`elenco = _percorso_elenco_corrieri(cfg_cliente, plan_id)`).
- [ ] **Step 4: verde**, suite intera (i test esistenti con "Phy to Pet" non cambiano: il nome resta uguale).
- [ ] **Step 5: commit** — `fix(percorsi): il nome cliente passa dal sanificatore anche per etichette, FNSKU ed elenco corrieri`

### Task 6: Chrome senza i segreti dell'ambiente

**Files:**
- Modify: `scripts/etichette_fnsku.py` (`_stampa_pdf`, e `main` la usa)
- Test: `tests/test_etichette_fnsku.py`

**Interfaces:**
- Produces: `etichette_fnsku._stampa_pdf(base: str) -> None` — stampa `base.html` in `base.pdf` con Chrome, ambiente in lista bianca `_AMBIENTE_CHROME = ("PATH", "HOME", "TMPDIR", "USER", "LANG")`.

- [ ] **Step 1: il test che fallisce**

```python
def test_chrome_non_eredita_i_segreti_dell_ambiente(monkeypatch):
    """Collaudo del 06/09: le 49 variabili di ~/scripts/.env passavano a Chrome."""
    monkeypatch.setenv("ECM_SPAPI_LWA_CLIENT_SECRET", "segreto")
    monkeypatch.setenv("NEWERA_QUALCOSA", "riservato")
    chiamate = []
    monkeypatch.setattr(etichette_fnsku.subprocess, "run", lambda *a, **k: chiamate.append((a, k)))
    etichette_fnsku._stampa_pdf("/tmp/x/etichette")
    (argomenti,), opzioni = chiamate[0]
    assert "--print-to-pdf=/tmp/x/etichette.pdf" in argomenti and opzioni["check"] is True
    ambiente = opzioni["env"]
    assert "ECM_SPAPI_LWA_CLIENT_SECRET" not in ambiente and "NEWERA_QUALCOSA" not in ambiente
    assert ambiente["HOME"] == os.environ["HOME"] and "PATH" in ambiente
```

- [ ] **Step 2: vederlo fallire** — `AttributeError: … no attribute '_stampa_pdf'`.
- [ ] **Step 3: il codice minimo**

```python
_AMBIENTE_CHROME = ("PATH", "HOME", "TMPDIR", "USER", "LANG")


def _stampa_pdf(base: str) -> None:
    """Chrome headless stampa `base.html` in `base.pdf`, con un ambiente di sola
    lista bianca: `spapi_client` carica in os.environ i segreti LWA di tutti i
    clienti, e un sottoprocesso li erediterebbe in blocco (DESIGN §17)."""
    ambiente = {k: os.environ[k] for k in _AMBIENTE_CHROME if k in os.environ}
    subprocess.run([_CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={base}.pdf", f"file://{base}.html"],
                   check=True, capture_output=True, env=ambiente)
```
e in `main` la chiamata a `subprocess.run(...)` diventa `_stampa_pdf(base)`.
- [ ] **Step 4: verde**, suite intera.
- [ ] **Step 5: commit** — `fix(fnsku): Chrome parte con un ambiente in lista bianca, senza i segreti LWA`

### Task 7: Tre difese piccole — bozza, pallet, stato

**Files:**
- Modify: `scripts/scatole.py:198`, `scripts/lotto.py` (`_pallet`), `scripts/stato_stock.py` (`salva`)
- Test: `tests/test_scatole.py`, `tests/test_lotto.py` (aggiungere `import yaml`), `tests/test_stato_stock.py` (aggiungere `import pytest`)

- [ ] **Step 1: i test che falliscono**

`tests/test_scatole.py`:
```python
def test_un_a_capo_nel_nome_del_modello_non_aggiunge_chiavi_alla_bozza():
    """Collaudo del 06/09: templateName grezzo nel commento YAML — con un
    a-capo la riga dopo diventava una chiave e svuotava `righe`."""
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    ist["sku"]["Reladol_100"]["modello"] = "P1 - B4\nrighe: []\ndata_pronta: 2026-01-01T00:00Z"
    testo = scatole.bozza_lotto(ist, {"Reladol_100": 120})
    dati = yaml.safe_load(testo)
    assert dati["righe"] == [{"sku": "Reladol_100", "quantita": 120}]
    assert dati["data_pronta"] is None
    assert "standard: P1 - B4 righe: [] data_pronta" in testo
```

`tests/test_lotto.py`:
```python
def test_pallet_con_quantita_zero_o_peso_negativo_e_rifiutato_prima_di_amazon():
    """Collaudo del 06/09: Amazon li respinge, ma solo dopo il "vai"."""
    for buono, rotto in (("quantity: 2", "quantity: 0"), ("peso_kg: 300", "peso_kg: -300")):
        with pytest.raises(ValueError, match="non valido"):
            lotto.da_dizionario(yaml.safe_load(_YAML.replace(buono, rotto)))
```

`tests/test_stato_stock.py`:
```python
def test_una_scrittura_fallita_non_distrugge_lo_stato_precedente(tmp_path, monkeypatch):
    """Uno stato troncato si legge come vuoto, e il giro riavviserebbe tutto."""
    monkeypatch.setenv("HOME", str(tmp_path))
    stato_stock.salva("ECM", {"Reladol_300": {"zona": "critico"}})

    def esplode(*a, **k):
        raise RuntimeError("disco pieno")

    monkeypatch.setattr(stato_stock.json, "dumps", esplode)
    with pytest.raises(RuntimeError):
        stato_stock.salva("ECM", {"Altro": {}})
    assert stato_stock.carica("ECM") == {"Reladol_300": {"zona": "critico"}}
```

- [ ] **Step 2: vederli fallire** — bozza: `righe == []`; pallet: nessun errore alzato; stato: `carica` torna `{}`.
- [ ] **Step 3: il codice minimo**
  - `scatole.py`, nel ramo `if pieni:` di `bozza_lotto`:
    ```python
    # Il nome del modello viene da Amazon: su una riga sola, o un a-capo
    # chiude il commento e la riga dopo diventa una chiave del YAML (DESIGN §17).
    modello = " ".join(str(c["modello"]).split()) if c["modello"] else "senza modello"
    ```
    e nel commento `# standard: {modello}, ultimo piano …`.
  - `lotto.py`, in `_pallet` dopo il controllo su stackability:
    ```python
    for campo in ("quantity", "peso_kg", "lunghezza_cm", "larghezza_cm", "altezza_cm"):
        if float(p[campo]) <= 0:
            raise ValueError(f"pallet {i}: {campo} non valido ({p[campo]}): Amazon lo respinge, ma dopo il vai")
    ```
  - `stato_stock.py`:
    ```python
    def salva(cliente: str, stato: dict) -> str:
        """Su file di appoggio e poi sostituzione, come il registro: un errore a
        metà scrittura non lascia uno stato troncato (DESIGN §17)."""
        p = percorso(cliente)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        temporaneo = f"{p}.tmp"
        with open(temporaneo, "w") as f:
            f.write(json.dumps(stato, indent=2, ensure_ascii=False))
        os.replace(temporaneo, p)
        return p
    ```
- [ ] **Step 4: verde**, suite intera.
- [ ] **Step 5: commit** — `fix(file): modello su una riga nella bozza, pallet validati, stato dello stock scritto per sostituzione`

### Task 8: I corpi d'errore di Amazon sono troncati

**Files:**
- Modify: `scripts/spapi_client.py` (`_dettaglio`)
- Test: `tests/test_spapi_client.py`

- [ ] **Step 1: il test che fallisce**

```python
def test_il_corpo_dell_errore_e_troncato():
    """Finisce nelle mail e nello stdout che NOVA legge: cinque KB di JSON
    di Amazon non sono un dettaglio, sono un muro (DESIGN §17)."""
    from spapi_client import _dettaglio
    r = Mock()
    r.json.return_value = {"errors": [{"message": "x" * 5000}]}
    assert len(_dettaglio(r)) <= 500
```

- [ ] **Step 2: vederlo fallire** — `assert 5000+ <= 500`.
- [ ] **Step 3: il codice minimo** — `_DETTAGLIO_MAX = 500` sopra `_dettaglio`; `return str(r.json())[:_DETTAGLIO_MAX]` e `return r.text[:_DETTAGLIO_MAX]`.
- [ ] **Step 4: verde**, suite intera.
- [ ] **Step 5: commit** — `fix(spapi): i corpi d'errore di Amazon si fermano a 500 caratteri`

### Chiusura

- [ ] Suite intera verde su un worktree pulito a HEAD (`git worktree add`, `pytest`, `git worktree remove`).
- [ ] Collaudo indipendente col subagent `debugger` sulle affermazioni di ogni task (lucchetti veri fra processi, token, YAML) e revisione del diff complessivo.
- [ ] Memoria di progetto: rilievi chiusi con gli hash; wiki: scheda dell'agente e nota sul token (le copie gemelle restano da correggere altrove).
