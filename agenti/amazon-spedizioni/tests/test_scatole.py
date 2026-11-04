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
    """Tre piani: il più recente fa standard, quello di settembre 2025 porta la
    variante 9,4 kg di Reladol_100, quello del 2024 è fuori finestra.

    Nota: la data del piano di mezzo (2025-09-15) sta appena dentro la
    finestra di 12 mesi (30 giorni/mese, DESIGN.md §13: soglia 2025-09-07
    rispetto a _ADESSO 2026-09-02) — non un mese qualsiasi del 2025."""
    c = ClienteFinto()
    c.spedito("wf-ago", "2026-08-12T11:55:22Z", [
        scatola_finta("Reladol_100", 60, 9.0, 30, 40, 31, quantita=2, modello="P1 - B4", asin="B0CB4DCJPZ"),
        scatola_finta("Reladol_100", 10, 2.0, 30, 40, 31),                    # resto: non è una variante
        scatola_finta("ARTINORM_2_FBA", 18, 7, 30, 40, 30, modello="P1 - B1", asin="B0G934VLJR"),
    ])
    c.spedito("wf-set", "2025-09-15T09:00:00Z", [
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
        {"pezzi": 60, "lunghezza_cm": 30, "larghezza_cm": 40, "altezza_cm": 31, "peso_kg": 9.4, "ultima_data": "2025-09-15"}]
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


def test_istantanea_ritrovata_a_prescindere_da_maiuscole_e_spazi(tmp_path):
    """Come clienti.carica_cliente e spapi_client.crea_client: un lotto con
    `cliente: ecm` deve trovare lo storico salvato come ECM.json."""
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    scatole.salva_istantanea(ist)
    assert scatole.carica_istantanea("ecm") == ist
    assert scatole.carica_istantanea("E C M") == ist
    assert scatole.carica_istantanea("Mai letto") is None


import yaml
import lotto as lotto_mod


def test_la_tabella_dice_standard_varianti_misti_e_piani_letti():
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    t = scatole.tabella(ist)
    assert "ECM" in t and "2 piani" in t and "12 mesi" in t
    assert "Reladol_100 (B0CB4DCJPZ): 60 pz/collo, 30×40×31 cm, 9 kg, modello P1 - B4, ultimo piano 2026-08-12" in t
    assert "9.4 kg fino al 2025-09-15" in t
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


def test_gli_sku_speciali_restano_stringhe_dopo_il_giro_yaml():
    """"NO" e "007" sono scalari YAML "pericolosi": senza virgolette PyYAML li
    ritipizza (bool, ottale) e lotto.carica vedrebbe uno SKU diverso."""
    ist = scatole.leggi_storico(_cliente_ecm(), "ECM", _ADESSO)
    ist["sku"]["NO"] = dict(ist["sku"]["Reladol_100"])
    ist["sku"]["007"] = dict(ist["sku"]["Reladol_100"])
    testo = scatole.bozza_lotto(ist, {"NO": 60, "007": 60})
    dati = yaml.safe_load(testo)
    assert {r["sku"] for r in dati["righe"]} == {"NO", "007"}
    assert all(isinstance(r["sku"], str) for r in dati["righe"])
    for s in dati["scatole"]:
        for r in s["righe"]:
            assert isinstance(r["sku"], str) and r["sku"] in ("NO", "007")


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


def test_scrivi_bozza_sanifica_il_nome_cliente_nel_percorso(tmp_path):
    """Come registro._percorso: un cliente con "/" nel nome non deve far
    scrivere fuori dalla cartella Desktop."""
    percorso = scatole.scrivi_bozza("A/B", "cliente: A/B\n", _ADESSO)
    assert percorso.rsplit("/", 1)[0] == str(tmp_path / "Desktop")


def test_scrivi_bozza_sul_desktop_senza_sovrascrivere(tmp_path):
    p1 = scatole.scrivi_bozza("ECM", "cliente: ECM\n", _ADESSO)
    p2 = scatole.scrivi_bozza("ECM", "cliente: ECM\n", _ADESSO)
    assert p1 == str(tmp_path / "Desktop/Lotto-ECM-2026-09-02.yaml")
    assert p2 == str(tmp_path / "Desktop/Lotto-ECM-2026-09-02-2.yaml")
    assert open(p1).read() == "cliente: ECM\n"


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


def test_il_confine_del_dieci_per_cento_non_e_fragile_ai_float(istantanea):
    """abs(9.9 - 9) > 0.1 * 9 è True in floating point (0.9000000000000004 >
    0.9): 9.9 kg contro uno standard di 9 kg è esattamente il limite, non uno
    scostamento. 9.91 invece lo è per davvero."""
    assert scatole.confronta(_lotto([_scatola("Reladol_100", 60, 9.9, 30, 40, 31)]), istantanea).scostamenti == []
    esito = scatole.confronta(_lotto([_scatola("Reladol_100", 60, 9.91, 30, 40, 31)]), istantanea)
    assert len(esito.scostamenti) == 1


def test_scatola_senza_contenuto_dichiarato_e_una_nota_non_mista(istantanea):
    """`lotto.da_dizionario` accetta una scatola con `righe: []` quando altre
    scatole coprono già il lotto (§ contenuto): qui non è una scatola mista
    (più SKU), è una scatola di cui Mariano non ha ancora detto il contenuto."""
    scatole_dict = [
        _scatola("Reladol_100", 60, 9.0, 30, 40, 31),
        {"quantita": 1, "peso_kg": 5.0, "lunghezza_cm": 50, "larghezza_cm": 50, "altezza_cm": 50, "righe": []},
    ]
    esito = scatole.confronta(_lotto(scatole_dict), istantanea)
    assert esito.scostamenti == []
    assert esito.note == ["scatola 2: senza contenuto dichiarato, non confrontata"]


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
