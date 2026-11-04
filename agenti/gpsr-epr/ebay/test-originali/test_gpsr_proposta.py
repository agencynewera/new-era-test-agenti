"""Quali inserzioni si toccano, e con che cosa.

La regola che questo modulo deve far rispettare e' una sola: si scrive un dato
di conformita' SOLO quando si sa da dove viene. Una marca sconosciuta, un
articolo senza marca, una marca su cui il catalogo si contraddice: in tutti e
tre i casi non si tira a indovinare, si lascia stare e si dice perche'.
"""
from __future__ import annotations
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import gpsr   # noqa: E402
import letto  # noqa: E402

PEGO = {"CompanyName": "Pego S.r.l.", "CityName": "Occhiobello",
        "Country": "IT", "Email": "produttore@example.com"}
RESPONSABILE = {"CompanyName": "Mario Rossi", "CityName": "Albinea",
                "PostalCode": "42020", "Country": "IT",
                "Email": "cliente@example.com"}
ANAGRAFICA = {"responsabile_ue": RESPONSABILE,
              "avvertenze_default": ["EBPSP101", "EBPSP201"],
              "produttori": {"Pego": PEGO}}


def stato(marca, regulatory=None, **extra):
    d = dict(item_id="1", titolo="x", descrizione="y", categoria="1",
             prezzo=1.0, quantita=1, tipo="FixedPriceItem",
             aspetti={"Marca": [marca]} if marca else {},
             regulatory=regulatory)
    d.update(extra)
    return letto.Stato(**d)


class TestQuandoSiPropone(unittest.TestCase):
    def test_inserzione_vuota_con_marca_nota_riceve_tutto(self):
        p, motivo = gpsr.proposta_per(stato("Pego"), ANAGRAFICA)
        self.assertEqual(motivo, "")
        self.assertEqual(p["produttore"], PEGO)
        self.assertEqual(p["responsabili"], [RESPONSABILE])
        self.assertEqual(p["pittogrammi"], ["EBPSP101", "EBPSP201"])

    def test_marca_sconosciuta_non_si_tocca(self):
        p, motivo = gpsr.proposta_per(stato("Marca Mai Vista"), ANAGRAFICA)
        self.assertIsNone(p)
        self.assertIn("Marca Mai Vista", motivo)

    def test_articolo_senza_marca_non_si_tocca(self):
        # 14 articoli di Tecno Cold stanno sotto "- Senza marca/Generico -":
        # non c'e' nessuna chiave a cui agganciare un produttore.
        p, motivo = gpsr.proposta_per(stato("- Senza marca/Generico -"), ANAGRAFICA)
        self.assertIsNone(p)
        self.assertIn("senza marca", motivo.lower())

    def test_inserzione_gia_completa_non_si_tocca(self):
        completa = {"produttore": PEGO, "responsabili": [RESPONSABILE],
                    "pittogrammi": ["EBPSP101", "EBPSP201"], "dichiarazioni": []}
        p, motivo = gpsr.proposta_per(stato("Pego", completa), ANAGRAFICA)
        self.assertIsNone(p)
        self.assertIn("gia'", motivo)


class TestMarcaSuEbayDe(unittest.TestCase):
    """Su eBay.de la specifica si chiama "Marke": letta solo "Marca"/"Brand",
    le 39 inserzioni DE di Tecno Cold uscivano tutte "senza marca" (06/10/2026)."""

    def test_marke_si_legge_come_marca(self):
        s = stato(None)
        s.aspetti = {"Marke": ["Pego"]}
        p, motivo = gpsr.proposta_per(s, ANAGRAFICA)
        self.assertEqual(motivo, "")
        self.assertEqual(p["produttore"], PEGO)


class TestCosaSiConserva(unittest.TestCase):
    def test_un_produttore_gia_scritto_non_viene_sovrascritto(self):
        # 17 inserzioni sono parziali. Il produttore che c'e' l'ha messo il
        # cliente e puo' essere piu' preciso della nostra tabella per marca:
        # si aggiunge cio' che manca, non si riscrive cio' che c'e'.
        suo = {"CompanyName": "Pego Service S.r.l.", "CityName": "Rovigo"}
        parziale = {"produttore": suo, "responsabili": [],
                    "pittogrammi": [], "dichiarazioni": []}
        p, motivo = gpsr.proposta_per(stato("Pego", parziale), ANAGRAFICA)
        self.assertEqual(p["produttore"], suo)
        self.assertEqual(p["responsabili"], [RESPONSABILE])

    def test_le_avvertenze_gia_scelte_dal_cliente_restano(self):
        parziale = {"produttore": None, "responsabili": [RESPONSABILE],
                    "pittogrammi": ["EBPSP103"], "dichiarazioni": []}
        p, _ = gpsr.proposta_per(stato("Pego", parziale), ANAGRAFICA)
        self.assertEqual(p["pittogrammi"], ["EBPSP103"])
        self.assertEqual(p["produttore"], PEGO)


if __name__ == "__main__":
    unittest.main()
