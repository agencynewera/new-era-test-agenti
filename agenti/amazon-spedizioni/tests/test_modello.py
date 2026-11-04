from modello import (RigaSpedizione, Scatola, OpzioneImballaggio, OpzionePiazzamento,
                      FinestraConsegna, OpzioneTrasporto, Spedizione, PianoSpedizione)


def test_riga_spedizione_campi_minimi():
    r = RigaSpedizione(sku="SKU-1", quantita=30)
    assert r.sku == "SKU-1"
    assert r.quantita == 30


def test_opzione_trasporto_costo_di_default_assente():
    o = OpzioneTrasporto(transportation_option_id="to1", corriere="UPS", convenzionato=True)
    assert o.costo is None
    assert o.valuta == "EUR"
    assert o.shipment_id == ""
    assert o.modalita == ""
    assert o.richiede_finestra_consegna is False


def test_spedizione_liste_di_default_vuote():
    s = Spedizione(shipment_id="sh1", centro_logistico="MXP5")
    assert s.righe == []
    assert s.opzioni_trasporto == []
    assert s.trasporto_confermato is None
    assert s.etichette_path is None
    assert s.shipment_confirmation_id is None


def test_piano_spedizione_liste_di_default_vuote():
    p = PianoSpedizione(inbound_plan_id="wf1")
    assert p.spedizioni == []
    assert p.packing_option_id is None
    assert p.placement_option_id is None


def test_due_spedizioni_non_condividono_la_stessa_lista_di_righe():
    s1 = Spedizione(shipment_id="sh1", centro_logistico="MXP5")
    s2 = Spedizione(shipment_id="sh2", centro_logistico="BLQ1")
    s1.righe.append(RigaSpedizione(sku="SKU-1", quantita=10))
    assert s2.righe == []


def test_scatola_senza_contenuto_dichiarato():
    s = Scatola(quantita=2, peso_kg=6.5, lunghezza_cm=40, larghezza_cm=30, altezza_cm=20)
    assert s.righe == []


def test_opzione_imballaggio_porta_i_gruppi():
    o = OpzioneImballaggio(packing_option_id="po1", descrizione="1 gruppo/i di imballaggio",
                            packing_group_ids=["pg1"])
    assert o.packing_group_ids == ["pg1"]


def test_opzione_imballaggio_senza_gruppi_ha_lista_vuota():
    assert OpzioneImballaggio(packing_option_id="po1", descrizione="x").packing_group_ids == []


def test_opzione_piazzamento_senza_commissioni():
    o = OpzionePiazzamento(placement_option_id="pl1")
    assert o.shipment_ids == []
    assert o.commissioni is None
    assert o.valuta == "EUR"


def test_finestra_consegna_campi_minimi():
    f = FinestraConsegna(delivery_window_option_id="dw1", inizio="2026-09-10T00:00Z",
                          fine="2026-09-17T00:00Z")
    assert f.disponibilita == ""
