import textwrap
import pytest
import yaml
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


@pytest.mark.parametrize("campo", ["quantita", "peso_kg", "lunghezza_cm", "larghezza_cm", "altezza_cm"])
def test_scatola_con_misura_non_positiva_si_ferma_prima_di_amazon(campo):
    """DESIGN §6: si ferma prima di chiamare Amazon. Specchio del controllo di
    imballaggio._scatola_a_box, ma qui il piano non è ancora stato creato."""
    dati = {"cliente": "Phy to Pet", "righe": [{"sku": "A", "quantita": 10}],
            "scatole": [{"quantita": 1, "peso_kg": 8, "lunghezza_cm": 40, "larghezza_cm": 30,
                         "altezza_cm": 25, "righe": [{"sku": "A", "quantita": 10}]}],
            "data_pronta": "2026-09-05T08:00Z"}
    dati["scatole"][0][campo] = 0
    with pytest.raises(ValueError, match=f"scatola 1.*{campo}"):
        lotto.da_dizionario(dati)


_YAML_SCADENZA = textwrap.dedent("""
    cliente: ECM
    righe:
      - {sku: Septavir100_FBA, quantita: 50, scadenza: 2028-03-14}
      - {sku: Germ_5x200_FBA, quantita: 10}
    scatole:
      - {quantita: 1, peso_kg: 6, lunghezza_cm: 20, larghezza_cm: 33, altezza_cm: 23,
         righe: [{sku: Septavir100_FBA, quantita: 50}]}
      - {quantita: 1, peso_kg: 11.8, lunghezza_cm: 30, larghezza_cm: 40, altezza_cm: 30,
         righe: [{sku: Germ_5x200_FBA, quantita: 10}]}
    data_pronta: "2026-09-05T08:00Z"
""")


def test_scadenza_si_dichiara_una_volta_nelle_righe_e_arriva_alle_scatole(tmp_path):
    """PyYAML legge `2028-03-14` come date: deve tornare testo AAAA-MM-GG,
    e le scatole con lo stesso SKU la ereditano senza ripeterla."""
    p = tmp_path / "lotto.yaml"
    p.write_text(_YAML_SCADENZA)
    l = lotto.carica(str(p))
    assert [(r.sku, r.scadenza) for r in l.righe] == [("Septavir100_FBA", "2028-03-14"), ("Germ_5x200_FBA", None)]
    assert l.scatole[0].righe[0].scadenza == "2028-03-14"
    assert l.scatole[1].righe[0].scadenza is None
    assert lotto.da_dizionario(lotto.a_dizionario(l)) == l


@pytest.mark.parametrize("valore", ["14/03/2028", "2028-3-14", "2028-03", "domani"])
def test_scadenza_non_valida_si_ferma_prima_di_amazon(valore):
    dati = {"cliente": "ECM",
            "righe": [{"sku": "S", "quantita": 1, "scadenza": valore}],
            "scatole": [{"quantita": 1, "peso_kg": 1, "lunghezza_cm": 1, "larghezza_cm": 1, "altezza_cm": 1,
                         "righe": [{"sku": "S", "quantita": 1}]}],
            "data_pronta": "2026-09-05T08:00Z"}
    with pytest.raises(ValueError, match="scadenza"):
        lotto.da_dizionario(dati)


def test_un_campo_mancante_in_una_scatola_viene_nominato():
    """La bozza di `scatole.bozza_lotto` lascia vuoto il peso del collo
    parziale: l'errore deve dire quale campo manca, non `float() argument`."""
    dati = __import__("yaml").safe_load(_YAML)
    dati["scatole"][0]["peso_kg"] = None
    with pytest.raises(ValueError, match="scatola 1: manca peso_kg"):
        lotto.da_dizionario(dati)


def test_una_scatola_malformata_non_e_un_dizionario_da_lo_stesso_errore_nominato():
    """`s.get(...)` su una voce che non è un dizionario (YAML scritto a mano
    male) alza AttributeError: deve finire nello stesso `scatola i: ...`,
    non in una traceback non gestita."""
    dati = __import__("yaml").safe_load(_YAML)
    dati["scatole"] = ["non una scatola"]
    with pytest.raises(ValueError, match="scatola 1"):
        lotto.da_dizionario(dati)


def test_pallet_con_quantita_zero_o_peso_negativo_e_rifiutato_prima_di_amazon():
    """Collaudo del 06/09: Amazon li respinge, ma solo dopo il "vai"."""
    for buono, rotto in (("quantity: 2", "quantity: 0"), ("peso_kg: 300", "peso_kg: -300")):
        with pytest.raises(ValueError, match="non valido"):
            lotto.da_dizionario(yaml.safe_load(_YAML.replace(buono, rotto)))
