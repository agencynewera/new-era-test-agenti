# tests/test_giro.py
from __future__ import annotations
# Il giro operativo: da un'istruzione (dettata o da tabella) all'anteprima,
# dal "vai" alla scrittura in lotto con verifica differita. Cio' che si
# esegue e' ESATTAMENTE cio' che si e' mostrato: il lotto vive su file.
import csv
import json
import os
from datetime import datetime, timezone
from unittest.mock import Mock
import pytest
from openpyxl import Workbook
from spapi_client import SpApiError
import giro

IT = "APJ6JRA9NG5V4"


def _offerta(prezzo):
    return [{"currency": "EUR", "audience": "ALL",
             "our_price": [{"schedule": [{"value_with_tax": prezzo}]}], "marketplace_id": IT}]


class _Amazon:
    """Un Amazon in memoria: GET legge lo stato, PATCH ACCEPTED lo applica.
    Cosi' la verifica differita e' provata sul comportamento, non sul mock."""

    def __init__(self, stati: dict[str, dict], esito_patch: dict | None = None, guasti=()):
        self.stati = stati
        self.esito_patch = esito_patch or {"status": "ACCEPTED", "issues": []}
        self.guasti = set(guasti)
        self.scritture: list[tuple[str, dict]] = []
        self.client = Mock()
        self.client.get.side_effect = self._get
        self.client.patch.side_effect = self._patch

    @staticmethod
    def _sku(path):
        return path.rsplit("/", 1)[-1]

    def _get(self, path, params=None):
        sku = self._sku(path)
        if sku not in self.stati:
            raise SpApiError(f"404 su {path}")
        return {"sku": sku, "attributes": self.stati[sku], "summaries": [{"productType": "PET_FOOD"}]}

    def _patch(self, path, json_body, params=None):
        sku = self._sku(path)
        self.scritture.append((sku, json_body))
        if sku in self.guasti:
            raise SpApiError(f"500 su {sku}: gateway")
        if self.esito_patch["status"] == "ACCEPTED":
            for p in json_body["patches"]:
                self.stati[sku][p["path"].rsplit("/", 1)[-1]] = p["value"]
        return dict(self.esito_patch)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _registro_oggi(home, cliente="Phy to Pet"):
    data = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    import registro
    return json.load(open(home / f".config/newera-amazon/aggiornamenti/{registro.slug_cliente(cliente)}-{data}.json"))


# ---------------------------------------------------------- istruzioni ----

def test_da_dettatura_stessi_campi_per_tutti_gli_sku():
    istruzioni = giro.da_dettatura(["A", "B"], {"prezzo": "13,90"})
    assert [(i.sku, i.campi) for i in istruzioni] == [("A", {"prezzo": "13,90"}), ("B", {"prezzo": "13,90"})]


def test_da_tabella_csv_raggruppa_per_sku_nell_ordine_di_apparizione(tmp_path):
    percorso = tmp_path / "modifiche.csv"
    with open(percorso, "w", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["sku", "campo", "valore"])
        w.writerow(["B", "prezzo", "13,90"])
        w.writerow(["A", "quantita", "5"])
        w.writerow(["B", "titolo", "Nuovo"])
    istruzioni = giro.da_tabella(str(percorso))
    assert [(i.sku, i.campi) for i in istruzioni] == [
        ("B", {"prezzo": "13,90", "titolo": "Nuovo"}), ("A", {"quantita": "5"})]


def test_da_tabella_xlsx_legge_il_primo_foglio(tmp_path):
    percorso = tmp_path / "modifiche.xlsx"
    wb = Workbook(); ws = wb.active
    ws.append(["SKU", "Campo", "Valore"])
    ws.append(["A", "Tempo di gestione", 2])
    wb.save(percorso)
    istruzioni = giro.da_tabella(str(percorso))
    assert [(i.sku, i.campi) for i in istruzioni] == [("A", {"Tempo di gestione": 2})]


def test_da_tabella_senza_le_tre_colonne_alza_valueerror(tmp_path):
    percorso = tmp_path / "x.csv"
    percorso.write_text("sku;prezzo\nA;13,90\n")
    with pytest.raises(ValueError, match="colonne"):
        giro.da_tabella(str(percorso))


# ----------------------------------------------------------- anteprima ----

def test_anteprima_legge_lo_stato_vivo_e_propone_solo_cio_che_cambia(home):
    az = _Amazon({"A": {"purchasable_offer": _offerta(13.99)}, "B": {"purchasable_offer": _offerta(13.9)}})
    lotto = giro.anteprima(az.client, "SELLER1", IT, "Phy to Pet",
                           giro.da_dettatura(["A", "B"], {"prezzo": "13,90"}), fonte="chat")
    assert list(lotto.pacchetti) == ["A"]
    m = lotto.pacchetti["A"]["modifiche"][0]
    assert (m.campo, m.prima, m.dopo, m.fonte) == ("purchasable_offer", _offerta(13.99), _offerta(13.9), "chat")
    assert lotto.pacchetti["A"]["product_type"] == "PET_FOOD"
    assert lotto.senza_modifiche == ["B"]
    assert lotto.errori == {}
    assert az.scritture == []


def test_anteprima_isola_l_errore_di_uno_sku_e_va_avanti_con_gli_altri(home):
    # Un valore sbagliato o uno SKU illeggibile non devono far saltare
    # l'anteprima intera: Mariano deve vedere tutto, compreso cio' che non va.
    az = _Amazon({"A": {"purchasable_offer": _offerta(13.99)}})
    istruzioni = [giro.Istruzione("A", {"prezzo": "tredici"}),
                  giro.Istruzione("INESISTENTE", {"prezzo": "13,90"}),
                  giro.Istruzione("A", {"quantita": "7"})]
    lotto = giro.anteprima(az.client, "SELLER1", IT, "Phy to Pet", istruzioni, fonte="chat")
    assert "tredici" in lotto.errori["A#prezzo"]
    assert "404" in lotto.errori["INESISTENTE"]
    assert [m.campo for m in lotto.pacchetti["A"]["modifiche"]] == ["fulfillment_availability"]


def test_anteprima_senza_product_type_leggibile_non_propone_lo_sku(home):
    # La PATCH esige productType: senza, meglio un errore chiaro in anteprima
    # che un 400 al momento del "vai".
    az = _Amazon({"A": {"purchasable_offer": _offerta(13.99)}})
    az.client.get.side_effect = lambda path, params=None: {"sku": "A", "attributes": az.stati["A"], "summaries": []}
    lotto = giro.anteprima(az.client, "SELLER1", IT, "Phy to Pet",
                           giro.da_dettatura(["A"], {"prezzo": "13,90"}), fonte="chat")
    assert lotto.pacchetti == {}
    assert "product_type" in lotto.errori["A"]


def test_anteprima_salva_il_lotto_su_file_e_carica_lo_rilegge_identico(home):
    az = _Amazon({"A": {"purchasable_offer": _offerta(13.99)}})
    lotto = giro.anteprima(az.client, "SELLER1", IT, "Phy to Pet",
                           giro.da_dettatura(["A"], {"prezzo": "13,90"}), fonte="chat")
    assert os.path.exists(giro.percorso_lotto(lotto.id))
    ricaricato = giro.carica(lotto.id)
    assert ricaricato.cliente == "Phy to Pet" and ricaricato.marketplace_id == IT
    assert ricaricato.pacchetti["A"]["modifiche"] == lotto.pacchetti["A"]["modifiche"]
    assert ricaricato.eseguito is False


def test_tabella_mostra_ogni_sku_con_prima_e_dopo_e_gli_errori(home):
    az = _Amazon({"A": {"purchasable_offer": _offerta(13.99)}, "B": {"purchasable_offer": _offerta(13.9)}})
    istruzioni = giro.da_dettatura(["A", "B"], {"prezzo": "13,90"}) + [giro.Istruzione("Z", {"prezzo": "1"})]
    testo = giro.anteprima(az.client, "SELLER1", IT, "Phy to Pet", istruzioni, fonte="chat").tabella()
    assert "A" in testo and "13.99" in testo and "13.9" in testo
    assert "B: nessuna modifica" in testo
    assert "Z" in testo and "404" in testo


# ----------------------------------------------------------------- vai ----

def _pronto(stati, **kw):
    az = _Amazon(stati, **kw)
    lotto = giro.anteprima(az.client, "SELLER1", IT, "Phy to Pet",
                           giro.da_dettatura(list(stati), {"prezzo": "13,90"}), fonte="chat")
    return az, lotto


def test_vai_scrive_esattamente_i_pacchetti_dell_anteprima_poi_aspetta_e_verifica(home):
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}, "B": {"purchasable_offer": _offerta(12.0)}})
    dormite = []

    esito = giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Phy to Pet", attesa=60, dormi=dormite.append)

    assert [(sku, corpo["patches"][0]["value"]) for sku, corpo in az.scritture] == [
        ("A", _offerta(13.9)), ("B", _offerta(13.9))]
    assert dormite == [60]
    assert esito.scritti == {"A": ("ACCEPTED", []), "B": ("ACCEPTED", [])}
    assert esito.verifiche == {"A": {"purchasable_offer": "applicato"}, "B": {"purchasable_offer": "applicato"}}
    assert giro.carica(lotto.id).eseguito is True


def test_vai_rifiuta_un_lotto_gia_eseguito(home):
    # Un "vai" ripetuto per sbaglio in chat non deve scrivere due volte.
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}})
    giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Phy to Pet", attesa=0, dormi=lambda s: None)
    with pytest.raises(ValueError, match="gia' eseguito"):
        giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Phy to Pet", attesa=0, dormi=lambda s: None)
    assert len(az.scritture) == 1


def test_vai_salta_lo_sku_cambiato_tra_anteprima_e_vai_senza_scriverlo(home):
    # Il "dopo" e' costruito sull'array vivo di quando si e' fatta l'anteprima:
    # se nel frattempo qualcuno ha cambiato quell'attributo (es. giacenza FBA),
    # riscriverlo intero cancellerebbe la sua modifica. Meglio rifare l'anteprima.
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}, "B": {"purchasable_offer": _offerta(12.0)}})
    az.stati["B"]["purchasable_offer"] = _offerta(11.0)

    esito = giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Phy to Pet", attesa=0, dormi=lambda s: None)

    assert [sku for sku, _ in az.scritture] == ["A"]
    assert "cambiato" in esito.saltati["B"]
    assert az.stati["B"]["purchasable_offer"] == _offerta(11.0)


def test_vai_su_errore_di_trasporto_registra_e_continua_con_lo_sku_dopo(home):
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}, "B": {"purchasable_offer": _offerta(12.0)}},
                        guasti={"A"})

    esito = giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Phy to Pet", attesa=0, dormi=lambda s: None)

    assert "500" in esito.saltati["A"]
    assert esito.scritti == {"B": ("ACCEPTED", [])}
    assert [r["esito"] for r in _registro_oggi(home)] == ["ERRORE TRASPORTO", "ACCEPTED"]


def test_vai_non_verifica_gli_sku_invalid_ma_riporta_gli_issues(home):
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}},
                        esito_patch={"status": "INVALID", "issues": [{"code": "99022", "message": "prezzo"}]})
    dormite = []
    esito = giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Phy to Pet", attesa=60, dormi=dormite.append)
    assert esito.scritti["A"] == ("INVALID", [{"code": "99022", "message": "prezzo"}])
    assert esito.verifiche == {} and dormite == []


# ---------------------------------------------------- verifica differita ----

def test_verifica_considera_applicato_un_valore_arricchito_da_amazon(home):
    # Amazon aggiunge language_tag e altre chiavi: se tutto cio' che abbiamo
    # scritto c'e' col valore giusto, e' applicato — non "diversamente".
    az = _Amazon({"A": {"item_name": [{"value": "Vecchio", "marketplace_id": IT}]}})
    lotto = giro.anteprima(az.client, "SELLER1", IT, "Phy to Pet",
                           giro.da_dettatura(["A"], {"titolo": "Nuovo"}), fonte="chat")
    az.stati["A"]["item_name"] = [{"value": "Nuovo", "language_tag": "it_IT", "marketplace_id": IT, "extra": 1}]
    assert giro.verifica_lotto(az.client, "SELLER1", IT, lotto) == {"A": {"item_name": "applicato"}}


def test_verifica_distingue_non_applicato_e_applicato_diversamente(home):
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}, "B": {"purchasable_offer": _offerta(12.0)}})
    az.stati["B"]["purchasable_offer"] = _offerta(9.99)
    assert giro.verifica_lotto(az.client, "SELLER1", IT, lotto) == {
        "A": {"purchasable_offer": "non applicato"}, "B": {"purchasable_offer": "applicato diversamente"}}


def test_riassunto_dell_esito_nomina_gli_sku_che_non_sono_passati(home):
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}, "B": {"purchasable_offer": _offerta(12.0)}},
                        guasti={"A"})
    testo = giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Phy to Pet", attesa=0, dormi=lambda s: None).riassunto()
    assert "1 scritt" in testo and "B" in testo
    assert "A" in testo and "500" in testo


# ----------------------------------------------------------------- cli ----

def test_argomenti_anteprima_da_chat_con_campi_nome_uguale_valore():
    ns = giro._argomenti(["anteprima", "--cliente", "Phy to Pet", "--marketplace", "IT",
                          "--sku", "A", "--sku", "B", "--campo", "prezzo=13,90", "--campo", "quantita=5"])
    assert ns.comando == "anteprima" and ns.cliente == "Phy to Pet" and ns.marketplace == "IT"
    assert ns.sku == ["A", "B"]
    assert giro._campi_da_argomenti(ns.campo) == {"prezzo": "13,90", "quantita": "5"}


def test_argomenti_campo_senza_uguale_alza_valueerror():
    with pytest.raises(ValueError, match="nome=valore"):
        giro._campi_da_argomenti(["prezzo 13,90"])


# ------------------------------------------------------- selezione ----
# Gli SKU per criterio invece che per nome: "tutti gli snack", "tutti i non
# in vendita", "tutti con tempo di gestione 6".

def _ricerca_finta(monkeypatch, voci):
    import catalogo_venditore as cv
    chiamate = []

    def cerca(client, seller_id, marketplace_id, **filtri):
        chiamate.append(filtri)
        out = voci
        if filtri.get("padre"):
            out = [v for v in out if v.sku.startswith("FIGLIO")]
        return out

    monkeypatch.setattr(cv, "cerca", cerca)
    return chiamate


def _voce(sku, attributi=None, stati=("BUYABLE",)):
    import catalogo_venditore as cv
    return cv.Voce(sku=sku, asin="B0X", product_type="PET_FOOD", stati=list(stati), titolo="T",
                   issues=[], attributi=attributi or {}, canali=[])


def test_argomenti_anteprima_accetta_i_criteri_di_selezione():
    ns = giro._argomenti(["anteprima", "--cliente", "X", "--padre", "P", "--prefisso", "PTP-",
                          "--stato", "non-in-vendita", "--con-issue", "ERROR", "--dove", "tempo di gestione=6",
                          "--campo", "tempo di gestione=2"])
    assert (ns.padre, ns.prefisso, ns.stato, ns.con_issue) == ("P", "PTP-", "non-in-vendita", "ERROR")
    assert ns.dove == ["tempo di gestione=6"]


def test_seleziona_unisce_sku_espliciti_e_criteri_senza_doppioni(monkeypatch):
    chiamate = _ricerca_finta(monkeypatch, [_voce("FIGLIO-1"), _voce("FIGLIO-2"), _voce("ALTRO")])
    ns = giro._argomenti(["anteprima", "--cliente", "X", "--sku", "FIGLIO-2", "--sku", "MANUALE",
                          "--padre", "PADRE", "--campo", "prezzo=1"])
    skus = giro.seleziona(Mock(), "SELLER1", IT, ns)
    assert skus == ["FIGLIO-2", "MANUALE", "FIGLIO-1"]
    assert chiamate == [{"padre": "PADRE", "in_vendita": None, "severita": None}]


def test_seleziona_traduce_stato_e_applica_prefisso_e_dove(monkeypatch):
    sei = {"fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "lead_time_to_ship_max_days": 6}]}
    due = {"fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "lead_time_to_ship_max_days": 2}]}
    chiamate = _ricerca_finta(monkeypatch, [_voce("IL-LENTO", sei), _voce("IL-VELOCE", due), _voce("XX-LENTO", sei)])
    ns = giro._argomenti(["cerca", "--cliente", "X", "--stato", "in-vendita", "--prefisso", "IL-",
                          "--dove", "tempo di gestione=6"])
    assert giro.seleziona(Mock(), "SELLER1", IT, ns) == ["IL-LENTO"]
    assert chiamate[0]["in_vendita"] is True


def test_seleziona_senza_nessun_criterio_ne_sku_alza_valueerror():
    ns = giro._argomenti(["anteprima", "--cliente", "X", "--campo", "prezzo=1"])
    with pytest.raises(ValueError, match="criterio"):
        giro.seleziona(Mock(), "SELLER1", IT, ns)


def test_seleziona_senza_criteri_non_interroga_il_catalogo(monkeypatch):
    chiamate = _ricerca_finta(monkeypatch, [_voce("QUALSIASI")])
    ns = giro._argomenti(["anteprima", "--cliente", "X", "--sku", "A", "--campo", "prezzo=1"])
    assert giro.seleziona(Mock(), "SELLER1", IT, ns) == ["A"]
    assert chiamate == []


# ------------------------------------- campi sullo stesso attributo ----
# Collaudo 06/09/2026: quantita e tempo di gestione vivono entrambi in
# fulfillment_availability; il secondo costruttore ripartiva dal vivo e
# cancellava in silenzio la richiesta del primo. Mariano approvava una
# tabella con meta' della sua richiesta.

_FA = [{"fulfillment_channel_code": "AMAZON_EU"},
       {"fulfillment_channel_code": "DEFAULT", "quantity": 1000, "lead_time_to_ship_max_days": 6}]


@pytest.mark.parametrize("campi_richiesti", [
    {"quantita": "250", "tempo di gestione": "2"},
    {"tempo di gestione": "2", "quantita": "250"},
])
def test_anteprima_compone_due_campi_sullo_stesso_attributo_in_qualunque_ordine(home, campi_richiesti):
    az = _Amazon({"A": {"fulfillment_availability": [dict(e) for e in _FA]}})
    lotto = giro.anteprima(az.client, "SELLER1", IT, "Phy to Pet",
                           [giro.Istruzione("A", campi_richiesti)], fonte="chat")
    mods = lotto.pacchetti["A"]["modifiche"]
    assert [m.campo for m in mods] == ["fulfillment_availability"]
    assert mods[0].prima == _FA
    assert mods[0].dopo == [{"fulfillment_channel_code": "AMAZON_EU"},
                            {"fulfillment_channel_code": "DEFAULT", "quantity": 250, "lead_time_to_ship_max_days": 2}]
    assert lotto.errori == {}


def test_anteprima_stesso_sku_con_valori_in_conflitto_va_in_errore_e_non_propone_il_campo(home):
    # Due replace sullo stesso path nella stessa PATCH: cosa faccia Amazon
    # non e' documentato. Un'ambiguita' si mostra, non si sceglie.
    az = _Amazon({"A": {"purchasable_offer": _offerta(13.99)}})
    istruzioni = [giro.Istruzione("A", {"prezzo": "13,90"}), giro.Istruzione("A", {"Prezzo": "12,00"})]
    lotto = giro.anteprima(az.client, "SELLER1", IT, "Phy to Pet", istruzioni, fonte="chat")
    assert "A" not in lotto.pacchetti
    assert "conflitto" in lotto.errori["A#prezzo"]
    assert "13,90" in lotto.errori["A#prezzo"] and "12,00" in lotto.errori["A#prezzo"]


def test_anteprima_stesso_sku_ripetuto_con_lo_stesso_valore_propone_una_sola_modifica(home):
    # "--sku A --sku A" o un SKU che compare due volte in tabella: una sola
    # replace, non due identiche nella stessa PATCH.
    az = _Amazon({"A": {"purchasable_offer": _offerta(13.99)}})
    istruzioni = [giro.Istruzione("A", {"prezzo": "13,90"}), giro.Istruzione("A", {"prezzo": "13,90"})]
    lotto = giro.anteprima(az.client, "SELLER1", IT, "Phy to Pet", istruzioni, fonte="chat")
    assert len(lotto.pacchetti["A"]["modifiche"]) == 1
    assert lotto.errori == {}
    assert az.client.get.call_count == 1


# ------------------------------------ il lotto appartiene a un cliente ----
# Collaudo 06/09/2026: vai prendeva credenziali e marketplace dalla riga di
# comando senza confrontarli con quelli salvati nel lotto. Un lotto Phy to
# Pet su IT lanciato con --cliente Bedshire --marketplace DE scriveva con le
# credenziali di Bedshire su DE e registrava sotto Phy to Pet.

def test_vai_rifiuta_un_lotto_di_un_altro_cliente_senza_scrivere(home):
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}})
    with pytest.raises(ValueError, match="appartiene"):
        giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Bedshire", attesa=0, dormi=lambda s: None)
    assert az.scritture == []
    assert giro.carica(lotto.id).eseguito is False


def test_vai_rifiuta_un_lotto_di_un_altro_marketplace_senza_scrivere(home):
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}})
    with pytest.raises(ValueError, match="appartiene"):
        giro.vai(az.client, "SELLER1", "A1PA6795UKMFR9", lotto.id, cliente="Phy to Pet", attesa=0, dormi=lambda s: None)
    assert az.scritture == []


def test_vai_accetta_il_cliente_del_lotto_in_qualunque_grafia(home):
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}})
    esito = giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="PHYTOPET", attesa=0, dormi=lambda s: None)
    assert esito.scritti == {"A": ("ACCEPTED", [])}


def test_main_vai_passa_il_cliente_della_riga_di_comando_al_cancello(home, monkeypatch, capsys):
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}})
    monkeypatch.setattr(giro, "crea_client", lambda cliente: az.client)
    monkeypatch.setattr(giro, "seller_id_di", lambda cliente: "SELLER1")
    with pytest.raises(ValueError, match="appartiene"):
        giro.main(["vai", "--cliente", "Bedshire", "--marketplace", "IT", "--lotto", lotto.id, "--attesa", "60"])
    assert az.scritture == []


# ------------------------------------------ il file del lotto e' fidato ----
# Collaudo 06/09/2026: "vai" fidava ciecamente del file su disco e del suo
# nome. Qui si chiudono le quattro porte trovate.

def test_percorso_lotto_rifiuta_un_id_che_non_e_un_id(home):
    for brutto in ["../../fuori", "/abs/x", "phytopet-2026 ; rm", "A.json", "", "phytopet-1\n"]:
        with pytest.raises(ValueError, match="id di lotto"):
            giro.percorso_lotto(brutto)


def test_carica_rifiuta_una_copia_del_lotto_con_id_interno_diverso(home):
    # vai marcava eseguito=True sotto l'id INTERNO del file: una copia con un
    # altro nome restava rieseguibile per sempre.
    import shutil
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}})
    shutil.copy(giro.percorso_lotto(lotto.id), giro.percorso_lotto("copia-del-lotto"))
    with pytest.raises(ValueError, match="copia"):
        giro.carica("copia-del-lotto")
    with pytest.raises(ValueError, match="copia"):
        giro.vai(az.client, "SELLER1", IT, "copia-del-lotto", cliente="Phy to Pet", attesa=0, dormi=lambda s: None)
    assert az.scritture == []


def test_carica_rifiuta_un_lotto_con_un_dopo_vuoto(home):
    # Un lotto manomesso con dopo=null e fonte "annulla" passava da vai come
    # op delete: la guardia anti-svuotamento vale anche per il file.
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}})
    percorso = giro.percorso_lotto(lotto.id)
    dati = json.load(open(percorso))
    dati["pacchetti"]["A"]["modifiche"][0]["dopo"] = None
    dati["pacchetti"]["A"]["modifiche"][0]["fonte"] = "annulla"
    json.dump(dati, open(percorso, "w"))
    with pytest.raises(ValueError, match="dopo vuoto"):
        giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Phy to Pet", attesa=0, dormi=lambda s: None)
    assert az.scritture == []


def test_salva_interrotto_a_meta_lascia_il_file_precedente_intatto(home):
    # Il secondo vai leggeva il file mentre il primo lo riscriveva:
    # JSONDecodeError su un lotto vero. Il salvataggio deve essere atomico.
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}})
    prima = open(giro.percorso_lotto(lotto.id)).read()

    def dump_rotto(dati, f, **kw):
        f.write('{"id": "mezzo')
        raise OSError("disco pieno")
    lotto.eseguito = True
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(giro.json, "dump", dump_rotto)
        with pytest.raises(OSError):
            giro.salva(lotto)
    assert open(giro.percorso_lotto(lotto.id)).read() == prima
    assert giro.carica(lotto.id).eseguito is False


def test_due_vai_concorrenti_sullo_stesso_lotto_scrivono_una_volta_sola(home, monkeypatch):
    # 59 giri su 60 con doppia scrittura: fra "e' gia' eseguito?" e "lo
    # marco" non c'era nessun lucchetto. Un disco lento lo rende certo.
    import threading
    import time as orologio
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}})
    carica_vera = giro.carica

    def carica_lenta(lotto_id):
        caricato = carica_vera(lotto_id)
        orologio.sleep(0.2)
        return caricato
    monkeypatch.setattr(giro, "carica", carica_lenta)
    via = threading.Barrier(2)
    esiti = []

    def corri():
        via.wait()
        try:
            giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Phy to Pet", attesa=0, dormi=lambda s: None)
            esiti.append("eseguito")
        except ValueError as e:
            esiti.append(str(e))
    fili = [threading.Thread(target=corri) for _ in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()
    assert len(az.scritture) == 1
    assert esiti.count("eseguito") == 1
    assert any("gia' eseguito" in e for e in esiti)


def test_argomenti_vai_rifiuta_un_attesa_sotto_i_20_secondi(capsys):
    # La rilettura immediata restituisce snapshot fantasma (Bedshire 03/09):
    # --attesa 0 era un falso allarme sistematico, --attesa -5 crollava DOPO
    # la scrittura.
    for a in ["0", "-5", "19"]:
        with pytest.raises(SystemExit):
            giro._argomenti(["vai", "--cliente", "X", "--lotto", "l", "--attesa", a])
    assert giro._argomenti(["vai", "--cliente", "X", "--lotto", "l", "--attesa", "20"]).attesa == 20


# ------------------------------------- la verifica non fa perdere l'esito ----
# Collaudo 06/09/2026: una rilettura fallita DOPO le scritture faceva uscire
# un'eccezione da vai, e Mariano non riceveva mai il riassunto di cio' che
# era stato scritto davvero.

def _get_fragile_dopo_la_scrittura(az, sku_fragile):
    get_vero = az.client.get.side_effect

    def get_fragile(path, params=None):
        if az._sku(path) == sku_fragile and any(s == sku_fragile for s, _ in az.scritture):
            raise SpApiError(f"503 su {sku_fragile}: gateway")
        return get_vero(path, params)
    az.client.get.side_effect = get_fragile


def test_verifica_lotto_segna_non_verificabile_lo_sku_illeggibile_senza_perdere_gli_altri(home):
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}, "B": {"purchasable_offer": _offerta(12.0)}})
    del az.stati["B"]
    verifiche = giro.verifica_lotto(az.client, "SELLER1", IT, lotto)
    assert verifiche["A"] == {"purchasable_offer": "non applicato"}
    assert verifiche["B"]["purchasable_offer"].startswith("non verificabile")
    assert "404" in verifiche["B"]["purchasable_offer"]


def test_vai_riporta_l_esito_anche_se_la_verifica_differita_fallisce_su_uno_sku(home):
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}, "B": {"purchasable_offer": _offerta(12.0)}})
    _get_fragile_dopo_la_scrittura(az, "B")
    esito = giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Phy to Pet", attesa=0, dormi=lambda s: None)
    assert esito.scritti == {"A": ("ACCEPTED", []), "B": ("ACCEPTED", [])}
    assert esito.saltati == {}
    assert esito.verifiche["A"] == {"purchasable_offer": "applicato"}
    assert esito.verifiche["B"]["purchasable_offer"].startswith("non verificabile")
    assert "non verificabile" in esito.riassunto() and "2 scritti ACCEPTED" in esito.riassunto()


# ------------------------------------------- residui del ricollaudo 06/09 ----

def test_da_tabella_stesso_sku_e_campo_con_valori_diversi_non_fa_vincere_l_ultima_riga(tmp_path):
    # Due righe CSV con stesso SKU e stesso campo: l'ultima vinceva in
    # silenzio. La riga in conflitto diventa un'istruzione a parte, cosi'
    # l'anteprima la marca come conflitto; la riga identica si fonde.
    percorso = tmp_path / "modifiche.csv"
    with open(percorso, "w", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["sku", "campo", "valore"])
        w.writerow(["A", "prezzo", "13,90"])
        w.writerow(["B", "prezzo", "5"])
        w.writerow(["A", "prezzo", "12,00"])
        w.writerow(["A", "Prezzo", "13,90"])
    istruzioni = giro.da_tabella(str(percorso))
    assert [(i.sku, i.campi) for i in istruzioni] == [
        ("A", {"prezzo": "13,90"}), ("B", {"prezzo": "5"}), ("A", {"prezzo": "12,00"})]


def test_anteprima_da_tabella_con_conflitto_lo_mostra_e_non_propone_il_campo(home, tmp_path):
    percorso = tmp_path / "modifiche.csv"
    percorso.write_text("sku;campo;valore\nA;prezzo;13,90\nA;prezzo;12,00\n")
    az = _Amazon({"A": {"purchasable_offer": _offerta(13.99)}})
    lotto = giro.anteprima(az.client, "SELLER1", IT, "Phy to Pet", giro.da_tabella(str(percorso)), fonte="tabella")
    assert "A" not in lotto.pacchetti
    assert "conflitto" in lotto.errori["A#prezzo"]


def test_vai_rifiuta_un_attesa_negativa_prima_di_scrivere(home):
    # Chiamata diretta (non CLI): time.sleep(-5) crollava DOPO la scrittura,
    # con lotto marcato e Esito perso.
    az, lotto = _pronto({"A": {"purchasable_offer": _offerta(13.99)}})
    with pytest.raises(ValueError, match="attesa"):
        giro.vai(az.client, "SELLER1", IT, lotto.id, cliente="Phy to Pet", attesa=-5, dormi=lambda s: None)
    assert az.scritture == []
    assert giro.carica(lotto.id).eseguito is False


def test_anteprima_accetta_un_nome_cliente_con_accenti(home):
    # slug_cliente teneva l'unicode ("café") ma l'id del lotto e' solo ASCII:
    # l'anteprima cadeva su "id di lotto non valido".
    az = _Amazon({"A": {"purchasable_offer": _offerta(13.99)}})
    lotto = giro.anteprima(az.client, "SELLER1", IT, "Café Noir",
                           giro.da_dettatura(["A"], {"prezzo": "13,90"}), fonte="chat")
    assert lotto.id.startswith("cafnoir-")
