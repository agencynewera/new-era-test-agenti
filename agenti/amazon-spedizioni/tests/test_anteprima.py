from modello import PianoSpedizione, Spedizione, RigaSpedizione, OpzioneTrasporto
from anteprima import riepilogo, riepilogo_conferma


def _spedizione(**kwargs):
    parametri = {"shipment_id": "sh1", "centro_logistico": "MXP5",
                  "righe": [RigaSpedizione(sku="SKU-1", quantita=30)]}
    parametri.update(kwargs)
    return Spedizione(**parametri)


def test_riepilogo_mostra_pezzi_e_destinazione():
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[
        _spedizione(righe=[RigaSpedizione(sku="SKU-1", quantita=30),
                            RigaSpedizione(sku="SKU-2", quantita=10)]),
    ])
    testo = riepilogo(piano)
    assert "MXP5" in testo
    assert "40 pezzi" in testo
    assert "2 SKU" in testo


def test_riepilogo_senza_opzioni_di_trasporto_lo_dice():
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[_spedizione()])
    assert "nessuna opzione di trasporto" in riepilogo(piano)


def test_riepilogo_mostra_il_prezzo_del_convenzionato():
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[
        _spedizione(opzioni_trasporto=[OpzioneTrasporto("to1", "UPS", True, 42.5, "EUR")]),
    ])
    testo = riepilogo(piano)
    assert "42.50 EUR" in testo
    assert "convenzionato Amazon" in testo


def test_riepilogo_corriere_proprio_senza_quota_non_lo_dice_gratis():
    """Nessun preventivo non vuol dire gratis: il testo dice che Amazon non
    ha quotato, non che la spedizione non costa nulla."""
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[
        _spedizione(opzioni_trasporto=[OpzioneTrasporto("to2", "BRT", False)]),
    ])
    testo = riepilogo(piano)
    assert "nessun costo quotato da Amazon" in testo
    assert "corriere proprio" in testo
    assert "solo etichetta" not in testo


def test_riepilogo_corriere_proprio_con_quota_mostra_il_prezzo():
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[
        _spedizione(opzioni_trasporto=[OpzioneTrasporto("to2", "BRT", False, 15.9, "EUR")]),
    ])
    testo = riepilogo(piano)
    assert "15.90 EUR" in testo
    assert "nessun costo" not in testo


def test_riepilogo_piu_spedizioni_le_mostra_tutte():
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[
        _spedizione(shipment_id="sh1", centro_logistico="MXP5"),
        _spedizione(shipment_id="sh2", centro_logistico="BLQ1",
                     righe=[RigaSpedizione(sku="SKU-2", quantita=10)]),
    ])
    testo = riepilogo(piano)
    assert "sh1" in testo and "sh2" in testo


def test_riepilogo_convenzionato_senza_prezzo_non_dice_nessun_costo():
    piano = PianoSpedizione(inbound_plan_id="wf1", spedizioni=[
        _spedizione(opzioni_trasporto=[OpzioneTrasporto("to3", "UPS", True, None, "EUR")]),
    ])
    testo = riepilogo(piano)
    assert "prezzo non ancora disponibile" in testo
    assert "nessun costo quotato" not in testo


def test_riepilogo_conferma_nomina_una_sola_spedizione_e_un_solo_prezzo():
    spedizione = _spedizione()
    opzione = OpzioneTrasporto("to1", "UPS", True, 42.5, "EUR", shipment_id="sh1")
    testo = riepilogo_conferma(spedizione, opzione)
    assert "sh1" in testo
    assert "MXP5" in testo
    assert "30 pezzi" in testo
    assert "UPS" in testo
    assert "42.50 EUR" in testo
    assert "convenzionato Amazon" in testo
    assert '"vai"' in testo


def test_riepilogo_conferma_non_elenca_le_altre_opzioni():
    """È il testo del cancello: deve legare il 'vai' a una scelta sola,
    quindi le alternative della spedizione non compaiono."""
    scelta = OpzioneTrasporto("to1", "UPS", True, 42.5, "EUR", shipment_id="sh1")
    scartata = OpzioneTrasporto("to2", "BRT", False, shipment_id="sh1")
    spedizione = _spedizione(opzioni_trasporto=[scelta, scartata])
    testo = riepilogo_conferma(spedizione, scelta)
    assert "BRT" not in testo
    assert "to2" not in testo


def test_riepilogo_conferma_corriere_proprio_non_promette_gratis():
    spedizione = _spedizione()
    opzione = OpzioneTrasporto("to2", "BRT", False, shipment_id="sh1",
                                richiede_finestra_consegna=True)
    testo = riepilogo_conferma(spedizione, opzione)
    assert "nessun costo quotato da Amazon" in testo
    assert "corriere proprio" in testo
    assert "finestra di consegna confermata" in testo


def test_riepilogo_conferma_senza_finestra_richiesta_non_la_nomina():
    spedizione = _spedizione()
    opzione = OpzioneTrasporto("to1", "UPS", True, 42.5, "EUR", shipment_id="sh1")
    assert "finestra di consegna" not in riepilogo_conferma(spedizione, opzione)
