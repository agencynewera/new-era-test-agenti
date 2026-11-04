from datetime import datetime, timezone

import modelli
import scatole
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


def test_una_scatola_senza_templatename_e_automatica():
    """Task 10: prima del fix, il nome veniva normalizzato a '—' e
    e_automatico('—') tornava False — una scatola senza modello scavalcava
    'P1 - B1' invece di finire in fondo alla graduatoria come lui (DESIGN §14)."""
    c = ClienteFinto()
    c.spedito("wf1", "2026-08-01T10:00:00Z",
              [scatola_finta("SKU-A", 10, 5.0, 30, 40, 30, modello=None)])
    m = modelli.modelli_per_sku(modelli.leggi_piani(c, ADESSO)[0])["SKU-A"][0]
    assert m.automatico is True


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
