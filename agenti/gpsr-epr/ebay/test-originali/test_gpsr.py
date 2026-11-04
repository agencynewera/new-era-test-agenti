"""I dati GPSR (Item.Regulatory): produttore, responsabile UE, avvertenze.

Il container non e' fatto di item specifics: e' un blocco a se' che eBay emette
DOPO Variations nella xs:sequence di ItemType (misurato il 2026-09-02 su
387062159404: ItemSpecifics 34, Variations 38, Regulatory 40).
"""
from __future__ import annotations
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import applica        # noqa: E402
import diff           # noqa: E402
import letto          # noqa: E402
import modifiche      # noqa: E402
import revisione_xml  # noqa: E402
import ripristino     # noqa: E402

PRODUTTORE = {"CompanyName": "Pego S.r.l.", "Street1": "Via Fittizia 2",
              "CityName": "Occhiobello", "StateOrProvince": "Rovigo, Italia",
              "PostalCode": "45030", "Country": "IT",
              "Phone": "3330000001", "Email": "produttore@example.com"}
RESPONSABILE = {"CompanyName": "Mario Rossi", "Street1": "Via Esempio 1",
                "CityName": "Albinea", "StateOrProvince": "Reggio Emilia, Italia",
                "PostalCode": "42020", "Country": "IT",
                "Phone": "3330000000", "Email": "cliente@example.com"}
GPSR = {"produttore": PRODUTTORE, "responsabili": [RESPONSABILE],
        "pittogrammi": ["EBPSP101", "EBPSP201"]}


def stato_base(**extra):
    d = dict(item_id="357839242018",
             titolo="Centralina Pego ECP 200 Expert",
             descrizione="<p>Centralina.</p>",
             categoria="177750",
             prezzo=249.00,
             quantita=3,
             valuta="EUR",
             aspetti={"Marca": ["Pego"]},
             foto=["https://x/1.jpg"],
             tipo="FixedPriceItem")
    d.update(extra)
    return letto.Stato(**d)


def approvate(stato, richieste, fonte="dettatura"):
    da_fare, _ = modifiche.filtra(stato, modifiche.proponi(stato, richieste, fonte))
    return da_fare


class TestIlCampoEsiste(unittest.TestCase):
    def test_regulatory_arriva_fra_le_modifiche_da_applicare(self):
        # proponi restituisce una Modifica anche per un campo che rifiuta
        # (con .rifiutata valorizzato): il comportamento che conta e' che
        # sopravviva a filtra, non che l'oggetto esista.
        s = stato_base()
        da_fare = approvate(s, {"regulatory": GPSR})
        self.assertEqual([m.campo for m in da_fare], ["regulatory"])
        self.assertEqual(da_fare[0].dopo, GPSR)


class TestIlDocumento(unittest.TestCase):
    def test_il_blocco_esce_con_produttore_responsabile_e_pittogrammi(self):
        s = stato_base()
        doc = revisione_xml.costruisci(s, approvate(s, {"regulatory": GPSR}), "EBAY_IT")
        self.assertIn("<Regulatory>", doc)
        self.assertIn("<CompanyName>Pego S.r.l.</CompanyName>", doc)
        self.assertIn("<Manufacturer>", doc)
        self.assertIn("<ResponsiblePerson>", doc)
        self.assertIn("<Type>EUResponsiblePerson</Type>", doc)
        self.assertIn("<Pictogram>EBPSP101</Pictogram>", doc)
        self.assertIn("<Pictogram>EBPSP201</Pictogram>", doc)

    def test_ordine_interno_come_lo_emette_ebay(self):
        # RegulatoryType e' anch'esso una xs:sequence: l'ordine osservato su
        # 357839242018 e' ProductSafety, Manufacturer, ResponsiblePersons.
        s = stato_base()
        doc = revisione_xml.costruisci(s, approvate(s, {"regulatory": GPSR}), "EBAY_IT")
        self.assertLess(doc.index("<ProductSafety>"), doc.index("<Manufacturer>"))
        self.assertLess(doc.index("<Manufacturer>"), doc.index("<ResponsiblePersons>"))

    def test_regulatory_esce_dopo_gli_altri_campi(self):
        # Nella xs:sequence di ItemType, Regulatory (40) viene dopo
        # ItemSpecifics (34) e Variations (38).
        s = stato_base()
        doc = revisione_xml.costruisci(
            s, approvate(s, {"regulatory": GPSR, "titolo": "Centralina Pego ECP 200"}),
            "EBAY_IT")
        self.assertLess(doc.index("<Title>"), doc.index("<Regulatory>"))

    def test_i_campi_vuoti_del_produttore_non_escono(self):
        # Email vuota su meta' dei produttori veri di Tecno Cold: un tag vuoto
        # nel documento e' un valore che eBay prende alla lettera.
        s = stato_base()
        senza_email = dict(GPSR)
        senza_email["produttore"] = dict(PRODUTTORE, Email="")
        doc = revisione_xml.costruisci(
            s, approvate(s, {"regulatory": senza_email}), "EBAY_IT")
        self.assertNotIn("<Email></Email>", doc)
        self.assertNotIn("<Email/>", doc)


def fixture(nome):
    with open(os.path.join(os.path.dirname(__file__), nome)) as f:
        return f.read()


class TestLaLettura(unittest.TestCase):
    def test_legge_il_blocco_da_una_risposta_vera(self):
        s = letto.analizza(fixture("fixture_getitem_reale_regulatory.xml"))
        self.assertEqual(s.regulatory["produttore"]["CompanyName"], "Pego S.r.l.")
        self.assertEqual(s.regulatory["produttore"]["Email"], "produttore@example.com")
        self.assertEqual(s.regulatory["responsabili"][0]["CompanyName"], "Rosa Busà")
        self.assertEqual(s.regulatory["responsabili"][0]["Types"], ["EUResponsiblePerson"])
        self.assertEqual(s.regulatory["pittogrammi"], ["EBPSP101", "EBPSP201"])

    def test_senza_blocco_resta_None_non_un_dizionario_vuoto(self):
        # "eBay non ha mandato il blocco" e "il blocco c'e' ed e' vuoto" sono
        # fatti diversi: il primo e' il caso delle 8 inserzioni scoperte vuote,
        # e confonderli farebbe dire all'anteprima che non cambia nulla.
        s = letto.analizza(fixture("fixture_getitem_semplice.xml"))
        self.assertIsNone(s.regulatory)

    def test_il_letto_si_puo_rimandare_indietro_identico(self):
        # Il giro completo: cio' che si legge deve ricomporsi uguale, o
        # "annulla" rimanderebbe un blocco diverso da quello di prima.
        s = letto.analizza(fixture("fixture_getitem_reale_regulatory.xml"))
        rifatto = revisione_xml._regulatory(s.regulatory)
        originale = fixture("fixture_getitem_reale_regulatory.xml")
        dentro = originale[originale.index("<Regulatory>"):
                           originale.index("</Regulatory>") + len("</Regulatory>")]
        self.assertEqual(rifatto, dentro)


class TestAnteprima(unittest.TestCase):
    def test_mostra_produttore_responsabile_e_avvertenze_in_chiaro(self):
        s = stato_base()
        testo = diff.blocco(s, approvate(s, {"regulatory": GPSR}), [])
        self.assertIn("Pego S.r.l.", testo)
        self.assertIn("Mario Rossi", testo)
        self.assertNotIn("{", testo)   # non il dizionario grezzo

    def test_i_codici_pittogramma_non_restano_opachi(self):
        # "EBPSP101" non dice niente a chi approva: l'anteprima che non fa
        # capire cosa si sta approvando non e' un'anteprima.
        s = stato_base()
        testo = diff.blocco(s, approvate(s, {"regulatory": GPSR}), [])
        self.assertIn("bambini", testo.lower())
        self.assertIn("CE", testo)

    def test_dice_che_prima_non_c_era_niente(self):
        s = stato_base()
        testo = diff.blocco(s, approvate(s, {"regulatory": GPSR}), [])
        self.assertIn("nessuno", testo.lower())

    def test_non_mostra_le_righe_che_non_cambiano(self):
        # "responsabile UE: Mario Rossi → Mario Rossi" e' rumore che nasconde la
        # riga che conta davvero, come lo SCARTO_RUMORE degli aspetti.
        prima = {"produttore": {"CompanyName": "Vecchio S.p.A."},
                 "responsabili": [RESPONSABILE],
                 "pittogrammi": ["EBPSP101", "EBPSP201"], "dichiarazioni": []}
        s = stato_base(regulatory=prima)
        testo = diff.blocco(s, approvate(s, {"regulatory": GPSR}), [])
        self.assertIn("produttore:", testo)
        self.assertNotIn("responsabile UE:", testo)

    def test_distingue_un_valore_che_cambia_da_uno_che_nasce(self):
        prima = {"produttore": {"CompanyName": "Vecchio S.p.A."},
                 "responsabili": [], "pittogrammi": [], "dichiarazioni": []}
        s = stato_base(regulatory=prima)
        testo = diff.blocco(s, approvate(s, {"regulatory": GPSR}), [])
        self.assertIn("Vecchio S.p.A.", testo)
        self.assertIn("Pego S.r.l.", testo)


class TestEsitoDopoLaScrittura(unittest.TestCase):
    def test_riletto_uguale_a_cio_che_si_e_chiesto_e_applicato(self):
        s = stato_base()
        m = approvate(s, {"regulatory": GPSR})[0]
        riletto = stato_base(regulatory=GPSR)
        esiti = applica.confronta([m], riletto)
        self.assertEqual(esiti[0]["esito"], "applicato")

    def test_se_ebay_non_ha_scritto_niente_non_dice_applicato(self):
        # Il caso che conta: eBay accetta la revisione ma ignora il blocco.
        # Un "applicato" qui sarebbe una compliance dichiarata e inesistente.
        s = stato_base()
        m = approvate(s, {"regulatory": GPSR})[0]
        riletto = stato_base(regulatory=None)
        esiti = applica.confronta([m], riletto)
        self.assertNotEqual(esiti[0]["esito"], "applicato")


class TestAnnulla(unittest.TestCase):
    def voce(self, prima, dopo, riletto):
        # la rilettura di verifica sta in "verifica", non dentro "campi":
        # e' la forma che scrive applica._per_registro.
        return {"item_id": "357839242018", "esito": "applicato",
                "campi": [{"campo": "regulatory", "prima": prima, "dopo": dopo}],
                "verifica": [{"campo": "regulatory", "riletto": riletto,
                              "esito": "applicato"}]}

    def test_rimette_il_blocco_che_c_era_prima(self):
        vecchio = {"produttore": {"CompanyName": "Vecchio S.p.A."},
                   "responsabili": [], "pittogrammi": [], "dichiarazioni": []}
        stato_ora = stato_base(regulatory=GPSR)
        inverse = ripristino.modifiche_inverse(
            self.voce(vecchio, GPSR, GPSR), stato_ora)
        self.assertEqual([m.campo for m in inverse], ["regulatory"])
        self.assertEqual(inverse[0].dopo, vecchio)

    def test_non_rimette_se_un_terzo_ha_cambiato_il_blocco_dopo_di_noi(self):
        # Stessa regola degli altri campi: se cio' che si rilegge ora non e'
        # cio' che avevamo scritto, l'annulla cancellerebbe il lavoro di
        # qualcun altro.
        vecchio = {"produttore": {"CompanyName": "Vecchio S.p.A."},
                   "responsabili": [], "pittogrammi": [], "dichiarazioni": []}
        di_un_terzo = {"produttore": {"CompanyName": "Terzo S.r.l."},
                       "responsabili": [], "pittogrammi": [], "dichiarazioni": []}
        inverse = ripristino.modifiche_inverse(
            self.voce(vecchio, GPSR, GPSR), stato_base(regulatory=di_un_terzo))
        # non esclusa dalla lista ma RIFIUTATA con un motivo: cosi' l'anteprima
        # dell'annulla la mostra invece di farla sparire in silenzio, e il
        # resto del lotto non cade per colpa sua.
        self.assertEqual(len(inverse), 1)
        self.assertTrue(inverse[0].rifiutata)
        self.assertIn("Terzo S.r.l.", str(inverse[0].rifiutata))


if __name__ == "__main__":
    unittest.main()
