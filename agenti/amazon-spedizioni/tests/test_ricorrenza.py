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


def test_una_scatola_senza_misure_non_fa_spedizione_tipo():
    """Task 3: stesso filtro di modelli._colli_mono_sku. Senza questa guardia
    ricorrenza puo' eleggere un modello che modelli ha gia' scartato, e
    giro._blocco_cliente chiamerebbe modelli.scegli su un nome inesistente
    (DESIGN §14)."""
    c = ClienteFinto()
    for i, (data, colli) in enumerate([("2026-07-16", 36), ("2026-06-11", 36), ("2026-04-24", 36)]):
        scatola = scatola_finta("138_FBA", 3, 2.22, 32, 42.7, 25.3, quantita=colli,
                                modello="PALLET 36 PACCHI")
        if i == 0:
            scatola = dict(scatola, dimensions={})   # piano senza misure: non utilizzabile
        c.spedito(f"wf{i}", f"{data}T10:00:00Z", [scatola])
    assert ricorrenza.spedizione_tipo(_piani(c), "138_FBA") is None


def test_le_spedizioni_miste_non_contano():
    """Gli assortimenti di Bedshire: tre SKU insieme, mai due volte uguali."""
    c = ClienteFinto()
    for i, data in enumerate(["2026-04-11", "2026-02-23", "2026-01-27"]):
        c.spedito(f"wf{i}", f"{data}T10:00:00Z",
                  [scatola_finta("SKU-A", 10, 6.0, 40, 40, 40, modello="M"),
                   scatola_finta("SKU-B", 7, 6.0, 40, 40, 40, modello="N")])
    assert ricorrenza.spedizione_tipo(_piani(c), "SKU-A") is None
