import json
from datetime import datetime

import openpyxl
import pytest

import abbina
import controllo
import listino
import margine
import mercato
from spapi_client import SpApiError

P = margine.Parametri()


# --- listino -----------------------------------------------------------------

def test_listino_csv_con_ean_e_intestazione_spostata(tmp_path):
    f = tmp_path / "l.csv"
    f.write_text("Listino autunno;;;\n;;;\nCodice;Descrizione;EAN;Prezzo netto;U.M.\n"
                 "A1;Stucco bianco 1 kg;8001234567890;2,50;PZ\n"
                 "A2;Silicone;801234567890;1.234,00;KG\n"
                 ";;;;\nA3;Senza prezzo;;;PZ\n", encoding="utf-8")
    righe = listino.leggi_listino(str(f))
    assert [r.codice for r in righe] == ["A1", "A2"]
    assert righe[0].ean == "8001234567890" and righe[0].costo == 2.5 and righe[0].a_pezzo
    assert righe[1].ean == "0801234567890"     # lo zero iniziale tolto da Excel torna
    assert righe[1].costo == 1234.0 and not righe[1].a_pezzo


def test_cella_unica_codice_descrizione():
    righe = [[" ", "", ""], ["Codice Articolo\nDescrizione", "U.M.", "Prezzo Netto"], ["", "", ""],
             ["1\nALCOOL ET.DEN.90° LT. 1", "N.", 1.3357]]
    testa, col, unita = listino.riconosci_colonne(righe)
    assert (testa, unita) == (1, True)
    assert col["codice"] == col["descrizione"] == 0 and col["costo"] == 2


def test_um_cambioni():
    assert listino.RigaListino(1, "1", "x", 1, um="N,").a_pezzo
    assert listino.RigaListino(1, "1", "x", 1, um="N?").a_pezzo
    assert not listino.RigaListino(1, "1", "x", 1, um="MT").a_pezzo


# --- abbinamento -------------------------------------------------------------

def test_punteggio_abbreviazioni_e_formato():
    assert abbina.punteggio("ACETONE 125 ML.", "Irge Acetone Unghie 125ml Classico") == pytest.approx(1.0)
    # Stesso prodotto, formato diverso: penalità
    assert abbina.punteggio("ACETONE 125 ML.", "Acetone puro 1000 ml") < 0.8
    assert abbina.punteggio("ALCOOL ET.DEN.90° LT. 1", "ALCOOL ETILICO DENATURATO 90° LT 1") == pytest.approx(1.0)


def test_formato_diverso_e_altro_prodotto():
    """Collaudo Cambioni del 02/11: davano «alta» con formato sbagliato."""
    assert abbina.punteggio("STUCCO METALLICO 150 ML", "Stucco Metallico per Carrozzeria 750 ml") <= 0.2
    assert abbina.punteggio("STUCCO DOUGLAS 500 GR.", "KIT FAI TU STUCCO LEGNO DOUGLAS GR 200") <= 0.2
    assert abbina.punteggio("STUCCO BIANCO KG. 5", "Stucco bianco 5000 g") > 0.85
    assert abbina.punteggio("OSSIDO GIALLO FIORE 667. GR.500", "Colorante Siof 500 gr Ossido Ferro Giallo Fiore 667") > 0.85


def test_parola_lunga_che_manca_pesa():
    assert abbina.punteggio("ALCOOL ISOPROPILICO DEN 99 1LT", "ALCOOL ETILICO DENATURATO 99,9° LT.1") < 0.6


def test_scegli_per_somiglianza_poi_classifica():
    a = abbina.Scheda("B1", "Acetone 125 ml", classifica=50_000)
    b = abbina.Scheda("B2", "Acetone 125 ml unghie", classifica=9_000)
    c = abbina.Scheda("B3", "Martello da carpentiere")
    abb = abbina.scegli("ACETONE 125 ML", [c, a, b])
    assert abb.scheda.asin == "B2" and abb.confidenza == "alta" and not abb.sicuro
    assert abbina.scegli("ACETONE 125 ML", [c]).scheda is None


def test_ean_su_piu_schede_non_e_sicuro():
    s1, s2 = abbina.Scheda("B1"), abbina.Scheda("B2")
    assert abbina.abbina_ean("8", {"8": [s1]}).sicuro
    abb = abbina.abbina_ean("8", {"8": [s1, s2]})
    assert not abb.sicuro and "2 schede" in abb.nota
    assert abbina.abbina_ean("9", {}).scheda is None


def test_pezzi_dal_titolo():
    for titolo, pezzi in (("ALCOOL ETILICO 96° 12 X 1 LT", 12), ("Antitarlo 'complet' Lt. 5 4pz", 4),
                          ("Stucco bianco 5 kg", 1), ("Isopropanolo 70% 2 x 1 L", 2)):
        assert abbina.scheda_da_item({"asin": "B", "summaries": [{"itemName": titolo}]}).pezzi == pezzi


def test_scheda_da_item_pezzi_e_classifica_principale():
    item = {"asin": "B1", "summaries": [{"itemName": "Ossido giallo", "brand": "Siof"}],
            "salesRanks": [{"classificationRanks": [{"rank": 12, "title": "Pigmenti"}],
                            "displayGroupRanks": [{"rank": 3585, "title": "Fai da te"}]}],
            "attributes": {"number_of_items": [{"value": 4}]},
            "productTypes": [{"productType": "PAINT"}],
            "identifiers": [{"identifiers": [{"identifierType": "EAN", "identifier": "800"}]}]}
    s = abbina.scheda_da_item(item)
    assert (s.classifica, s.categoria, s.pezzi, s.product_type, s.ean) == (3585, "Fai da te", 4, "PAINT", ["800"])


# --- mercato -----------------------------------------------------------------

def test_offerte_buy_box_e_amazon():
    dati = {"Summary": {"TotalOfferCount": 3}, "Offers": [
        {"SellerId": "X", "ListingPrice": {"Amount": 9.0}, "Shipping": {"Amount": 1.0}, "IsFulfilledByAmazon": False},
        {"SellerId": "A11IL2BNXBQZ4K", "ListingPrice": {"Amount": 9.5}, "Shipping": {"Amount": 0},
         "IsFulfilledByAmazon": True, "IsBuyBoxWinner": True}]}
    o = mercato._offerte_da(dati, "A11IL2BNXBQZ4K")
    assert (o.prezzo, o.da_buy_box, o.n_offerte, o.n_fba, o.amazon_vende) == (9.5, True, 3, 1, True)
    senza = mercato._offerte_da({"Offers": dati["Offers"][:1]}, "A11IL2BNXBQZ4K")
    assert (senza.prezzo, senza.da_buy_box, senza.amazon_vende) == (10.0, False, False)


class _Client:
    def __init__(self, risposte):
        self.risposte, self.chiamate = risposte, []

    def _r(self, metodo, path, **kw):
        self.chiamate.append((metodo, path, kw))
        for chiave, valore in self.risposte.items():
            if chiave in path or chiave in json.dumps(kw.get("params") or {}):
                r = valore(path, kw) if callable(valore) else valore
                if isinstance(r, Exception):
                    raise r
                return r
        raise AssertionError(f"chiamata inattesa {metodo} {path} {kw}")

    def get(self, path, params=None):
        return self._r("GET", path, params=params)

    def post(self, path, json_body=None, params=None):
        return self._r("POST", path, json_body=json_body, params=params)

    def put(self, path, json_body, params=None):
        return self._r("PUT", path, json_body=json_body, params=params)


def test_commissioni_e_errore_fba():
    ok = {"payload": {"FeesEstimateResult": {"Status": "Success", "FeesEstimate": {"FeeDetailList": [
        {"FeeType": "ReferralFee", "FinalFee": {"Amount": 1.5}},
        {"FeeType": "FBAFees", "FinalFee": {"Amount": 3.2}}]}}}}
    c = mercato.stima_commissioni(_Client({"feesEstimate": ok}), "M", "B1", 10, True)
    assert (c.commissione, c.logistica, c.errore) == (1.5, 3.2, "")
    ko = {"payload": {"FeesEstimateResult": {"Status": "ClientError", "Error": {"Message": "InvalidParameterValue"}}}}
    assert "InvalidParameterValue" in mercato.stima_commissioni(_Client({"feesEstimate": ko}), "M", "B1", 10, True).errore
    giu = mercato.stima_commissioni(_Client({"feesEstimate": SpApiError("500")}), "M", "B1", 10, False)
    assert giu.commissione is None and giu.errore


def test_prova_offerta_non_scrive():
    c = _Client({"/listings/": {"status": "INVALID", "issues": [
        {"code": "5886", "message": "ASIN generico", "severity": "ERROR"},
        {"code": "1", "message": "nota", "severity": "WARNING"}]}})
    stato, errori = mercato.prova_offerta(c, "S", "M", "B1", "PAINT", 9.9)
    assert stato == "INVALID" and errori == ["5886 ASIN generico"]
    assert c.chiamate[0][2]["params"]["mode"] == "VALIDATION_PREVIEW"


# --- margine e semaforo ------------------------------------------------------

def test_conto_fbm():
    # 24,40 IVA incl. → 20 netti; 2 commissione, 4,50 spedizione, 1 resi, merce 2×3
    c = margine.calcola(24.40, 2.0, 3.0, 2, P)
    assert (c.prezzo_netto, c.merce, c.logistica, c.resi) == (20.0, 6.0, 4.5, 1.0)
    assert c.margine == pytest.approx(6.5) and c.margine_pct == 32.5
    assert c.roi == pytest.approx(108.3)
    # Al prezzo di pareggio il margine torna zero
    zero = margine.calcola(c.pareggio, 2.0 / 24.40 * c.pareggio, 3.0, 2, P)
    assert zero.margine == pytest.approx(0, abs=0.02)


def test_conto_fba_usa_la_tariffa_amazon():
    c = margine.calcola(24.40, 2.0, 3.0, 1, margine.Parametri(fba=True), logistica_fba=3.1)
    assert c.logistica == 3.1


def _sem(**kw):
    base = dict(trovato=True, sicuro=True, conto=margine.calcola(24.40, 2.0, 3.0, 1, P), classifica=5_000,
                n_offerte=3, amazon_vende=False, blocchi=[], approvazione=False, prova="VALID",
                a_pezzo=True, p=P)
    base.update(kw)
    return margine.semaforo(**base)


def test_semaforo():
    assert _sem().colore == "verde"
    assert _sem(sicuro=False).colore == "giallo"
    assert _sem(amazon_vende=True).colore == "giallo"
    assert _sem(n_offerte=12).colore == "giallo"
    assert _sem(classifica=150_000).colore == "giallo"
    assert _sem(classifica=None).colore == "rosso"
    assert _sem(approvazione=True, blocchi=["APPROVAL_REQUIRED"]).colore == "giallo"
    assert _sem(blocchi=["NOT_ELIGIBLE"]).colore == "rosso"
    assert _sem(prova="INVALID").colore == "rosso"
    assert _sem(prova=None).colore == "giallo"
    assert _sem(conto=margine.calcola(10, 1.5, 3.0, 1, P)).colore == "rosso"   # in perdita
    assert _sem(trovato=False).colore == "grigio"
    assert _sem(a_pezzo=False).colore == "grigio"


def test_margine_al_limite_e_giallo():
    # margine 17%: sopra la soglia del 15 ma entro 5 punti
    conto = margine.Conto(10, 0, 0, 0, 1.7, 17.0, None, None)
    e = _sem(conto=conto)
    assert e.colore == "giallo" and "al limite" in e.motivi[0]


def test_fasce():
    assert [margine.fascia(x) for x in (20_000, 20_001, 100_001, 250_001, None)] == \
        ["vende bene", "vende", "vende poco", "quasi ferma", "senza classifica"]


# --- giro completo con un client finto --------------------------------------

def _item(asin, nome, rank, ean=None):
    return {"asin": asin, "summaries": [{"itemName": nome, "brand": "X"}],
            "salesRanks": [{"displayGroupRanks": [{"rank": rank, "title": "Fai da te"}]}],
            "productTypes": [{"productType": "PAINT"}],
            "identifiers": [{"identifiers": [{"identifierType": "EAN", "identifier": ean}]}] if ean else []}


def _offerte(path, kw):
    risposte = []
    for req in kw["json_body"]["requests"]:
        asin = req["uri"].split("/")[5]
        prezzo = {"B1": 24.40, "B2": 5.0}[asin]
        risposte.append({"body": {"payload": {"ASIN": asin, "Summary": {"TotalOfferCount": 2}, "Offers": [
            {"SellerId": "Z", "ListingPrice": {"Amount": prezzo}, "Shipping": {"Amount": 0},
             "IsBuyBoxWinner": True}]}}})
    return {"responses": risposte}


def test_giro_completo(tmp_path):
    righe = [listino.RigaListino(2, "A1", "Stucco bianco 1 kg", 3.0, ean="8001234567890", um="PZ"),
             listino.RigaListino(3, "A2", "Acetone 125 ml", 4.0, um="PZ"),
             listino.RigaListino(4, "A3", "Cosa introvabile", 1.0, um="PZ")]
    fees = {"payload": {"FeesEstimateResult": {"Status": "Success", "FeesEstimate": {"FeeDetailList": [
        {"FeeType": "ReferralFee", "FinalFee": {"Amount": 2.0}}]}}}}

    def catalogo(path, kw):
        prm = kw["params"]
        if prm.get("identifiersType") == "EAN":
            return {"items": [_item("B1", "Stucco bianco 1 kg", 8_000, "8001234567890")]}
        if "acetone" in prm["keywords"]:
            return {"items": [_item("B2", "Acetone 125 ml", 30_000)]}
        return {"items": []}

    client = _Client({"/catalog/": catalogo, "itemOffers": _offerte, "feesEstimate": fees,
                      "restrictions": {"restrictions": []},
                      "/listings/2021-08-01/items": {"status": "VALID", "issues": []}})
    stato = controllo.Stato(str(tmp_path / "stato.json"))
    controllo.fase_abbinamento(client, "M", righe, stato, pausa=0, log=lambda *_: None)
    seller = controllo.fase_mercato(client, "Prova", "M", righe, P, stato, pausa_offerte=0, pausa=0,
                                    log=lambda *_: None, seller_id="S")
    foglio_righe = controllo.costruisci_righe(righe, P, stato, {})
    controllo.fase_prova(client, seller, "M", foglio_righe, stato, pausa=0, log=lambda *_: None)
    foglio_righe = controllo.costruisci_righe(righe, P, stato, {})
    colori = {r["r"].codice: r["esito"].colore for r in foglio_righe}
    # B2 a 5 € con costo 4 è in perdita già alla stima: niente commissioni né prova
    assert colori == {"A1": "verde", "A2": "rosso", "A3": "grigio"}
    assert not any("B2" in c[1] for c in client.chiamate if "feesEstimate" in c[1])
    assert sum(1 for c in client.chiamate if c[0] == "PUT") == 1

    # Rilancio: tutto dalla cache, nessuna chiamata nuova
    n = len(client.chiamate)
    stato2 = controllo.Stato(str(tmp_path / "stato.json"))
    controllo.fase_abbinamento(client, "M", righe, stato2, pausa=0, log=lambda *_: None)
    controllo.fase_mercato(client, "Prova", "M", righe, P, stato2, pausa_offerte=0, pausa=0,
                           log=lambda *_: None, seller_id="S")
    assert len(client.chiamate) == n

    import foglio
    percorso = foglio.scrivi("Prova", "listino", foglio_righe, P, datetime(2026, 11, 2, 10, 0), 3, 1,
                             cartella=str(tmp_path))
    wb = openpyxl.load_workbook(percorso)
    assert wb.sheetnames == ["Riepilogo", "Controllo", "Da comprare"]
    assert wb["Da comprare"].max_row == 2 and wb["Da comprare"]["G2"].value == "B1"
    assert foglio.scrivi("Prova", "listino", foglio_righe, P, datetime(2026, 11, 2, 10, 0), 3, 1,
                         cartella=str(tmp_path)).endswith("-2.xlsx")


def test_conferme_della_revisione(tmp_path):
    righe = [listino.RigaListino(3, "A2", "Acetone 125 ml", 1.0, um="PZ")]
    stato = controllo.Stato(str(tmp_path / "s.json"))
    stato["abbinamenti"]["3:A2"] = {"scheda": vars(abbina.Scheda("B2", "Acetone 125 ml", classifica=5000)),
                                   "confidenza": "alta", "nota": "abbinato per descrizione: da confermare"}
    stato["offerte"]["B2"] = vars(mercato.Offerte(24.40, True, 2, 0, False))
    stato["commissioni"]["B2|24.4|FBM"] = vars(mercato.Commissioni(2.0))
    stato["restrizioni"]["B2"] = vars(mercato.Restrizioni())
    stato["prove"]["B2"] = {"stato": "VALID", "errori": []}
    prima = controllo.costruisci_righe(righe, P, stato, {})
    assert prima[0]["esito"].colore == "giallo"
    assert [d["asin"] for d in controllo.da_rivedere(prima)] == ["B2"]
    assert controllo.costruisci_righe(righe, P, stato, {"A2": "sì"})[0]["esito"].colore == "verde"
    scartata = controllo.costruisci_righe(righe, P, stato, {"A2": "no"})[0]
    assert scartata["esito"].colore == "grigio" and "scartato" in "; ".join(scartata["esito"].motivi)
