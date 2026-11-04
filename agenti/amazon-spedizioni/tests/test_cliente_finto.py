"""Il finto deve comportarsi come l'Amazon vero letto nelle fixture: ogni
POST fa avanzare lo stadio come lo riconosce stadio.riconosci. Se il finto
non avanza come Amazon, i test di giro.py provano un mondo che non esiste."""
from datetime import datetime, timezone
import pytest
import stadio
from cliente_finto import ClienteFinto, scatola_finta
from modello import RigaSpedizione, Scatola
from piano import crea_piano
from imballaggio import genera_opzioni_imballaggio, conferma_imballaggio, imposta_informazioni_imballaggio
from piazzamento import genera_opzioni_piazzamento, conferma_piazzamento
from corrieri import genera_opzioni_trasporto
from conferma import conferma_trasporto
from spapi_client import SpApiError

_ADESSO = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)
_INDIRIZZO = {"name": "X", "addressLine1": "Via Roma 1", "city": "Prato", "postalCode": "59100",
              "countryCode": "IT", "phoneNumber": "1"}


def _stadio(c, pid):
    return stadio.riconosci(stadio.leggi(c, pid), _ADESSO)


def test_il_finto_avanza_come_amazon_a_ogni_post():
    c = ClienteFinto()
    righe = [RigaSpedizione("SKU-1", 30)]
    pid = crea_piano(c, righe, _INDIRIZZO, "APJ6JRA9NG5V4").inbound_plan_id
    assert _stadio(c, pid) == stadio.IMBALLAGGIO_DA_CONFERMARE
    po = genera_opzioni_imballaggio(c, pid)[0]
    conferma_imballaggio(c, pid, po.packing_option_id)
    assert _stadio(c, pid) == stadio.SCATOLE_DA_DICHIARARE
    imposta_informazioni_imballaggio(c, pid, {po.packing_group_ids[0]: [
        Scatola(1, 8.0, 40, 30, 25, righe)]}, "APJ6JRA9NG5V4")
    assert _stadio(c, pid) == stadio.PIAZZAMENTO_DA_GENERARE
    pl = genera_opzioni_piazzamento(c, pid)
    assert [o.placement_option_id for o in pl] == ["pl1", "pl2"]
    assert _stadio(c, pid) == stadio.PIAZZAMENTO_DA_SCEGLIERE
    opz = genera_opzioni_trasporto(c, pid, "pl1", ["sh1"], "2026-09-05T08:00Z")
    assert {o.transportation_option_id for o in opz} == {"to-ups-sh1", "to-proprio-sh1"}
    conferma_piazzamento(c, pid, "pl1")
    assert _stadio(c, pid) == stadio.TRASPORTO_DA_SCEGLIERE
    assert c.get(f"/inbound/fba/2024-03-20/inboundPlans/{pid}/shipments/sh1")["shipmentConfirmationId"] == "FBA1234sh1"
    conferma_trasporto(c, pid, [o for o in opz if o.transportation_option_id == "to-ups-sh1"])
    assert _stadio(c, pid) == stadio.TRASPORTO_PRENOTATO


def test_piano_a_precostruisce_ogni_stadio():
    for s in (stadio.IMBALLAGGIO_DA_CONFERMARE, stadio.SCATOLE_DA_DICHIARARE,
              stadio.PIAZZAMENTO_DA_GENERARE, stadio.PIAZZAMENTO_DA_SCEGLIERE,
              stadio.TRASPORTO_DA_SCEGLIERE, stadio.TRASPORTO_PRENOTATO):
        c = ClienteFinto()
        pid = c.piano_a(s)
        assert _stadio(c, pid) == s, s


def test_scadenza_dei_piazzamenti_e_regolabile():
    c = ClienteFinto(scadenza_piazzamento="2026-09-01T00:00:00.000Z")
    pid = c.piano_a(stadio.PIAZZAMENTO_DA_SCEGLIERE)
    assert _stadio(c, pid) == stadio.PIAZZAMENTO_DA_GENERARE


_B = "/inbound/fba/2024-03-20/inboundPlans"


def test_scatola_finta_ha_la_forma_di_amazon():
    s = scatola_finta("SKU-A", 18, 7, 30, 40, 30, quantita=2, modello="P1 - B3", asin="B0G934VLJR")
    assert s["quantity"] == 2 and s["templateName"] == "P1 - B3"
    assert s["dimensions"] == {"unitOfMeasurement": "CM", "length": 30, "width": 40, "height": 30}
    assert s["weight"] == {"unit": "KG", "value": 7}
    assert s["contentInformationSource"] == "BOX_CONTENT_PROVIDED"
    assert [(i["msku"], i["quantity"], i["asin"]) for i in s["items"]] == [("SKU-A", 18, "B0G934VLJR")]


def test_scatola_finta_mista_porta_piu_righe():
    s = scatola_finta("SKU-A", 10, 5, 30, 40, 30, altri=[("SKU-B", 4)])
    assert [(i["msku"], i["quantity"]) for i in s["items"]] == [("SKU-A", 10), ("SKU-B", 4)]


def test_la_lista_dei_piani_spediti_e_paginata_a_30_e_solo_per_status_shipped():
    c = ClienteFinto()
    for i in range(31):
        c.spedito(f"wfs{i:02d}", "2026-08-01T10:00:00Z", [scatola_finta("SKU-A", 18, 7, 30, 40, 30)])
    prima = c.get(_B, params={"status": "SHIPPED", "pageSize": 30})
    assert len(prima["inboundPlans"]) == 30 and prima["pagination"]["nextToken"]
    assert prima["inboundPlans"][0]["status"] == "SHIPPED" and prima["inboundPlans"][0]["createdAt"] == "2026-08-01T10:00:00Z"
    seconda = c.get(_B, params={"status": "SHIPPED", "pageSize": 30, "paginationToken": prima["pagination"]["nextToken"]})
    assert [p["inboundPlanId"] for p in seconda["inboundPlans"]] == ["wfs30"] and seconda["pagination"] == {}
    assert c.get(_B, params={"status": "ACTIVE", "pageSize": 30}) == {"inboundPlans": [], "pagination": {}}
    with pytest.raises(AssertionError, match="30"):
        c.get(_B, params={"status": "SHIPPED", "pageSize": 100})


def test_le_scatole_di_un_piano_spedito_sono_paginate_a_100():
    c = ClienteFinto()
    c.spedito("wfs1", "2026-08-01T10:00:00Z", [scatola_finta("SKU-A", 18, 7, 30, 40, 30)] * 101)
    prima = c.get(f"{_B}/wfs1/boxes", params={"pageSize": 100})
    assert len(prima["boxes"]) == 100 and prima["pagination"]["nextToken"]
    seconda = c.get(f"{_B}/wfs1/boxes", params={"pageSize": 100, "paginationToken": prima["pagination"]["nextToken"]})
    assert len(seconda["boxes"]) == 1 and seconda["pagination"] == {}


def test_un_piano_spedito_illeggibile_alza_spapierror_sulle_scatole():
    c = ClienteFinto()
    c.spedito("wfs1", "2026-08-01T10:00:00Z", [scatola_finta("SKU-A", 18, 7, 30, 40, 30)], leggibile=False)
    with pytest.raises(SpApiError, match="403"):
        c.get(f"{_B}/wfs1/boxes", params={"pageSize": 100})


def test_i_piani_con_stato_restano_come_prima():
    """La lista spediti non deve rompere il GET del piano nudo dei piani ACTIVE."""
    c = ClienteFinto()
    c.spedito("wfs1", "2026-08-01T10:00:00Z", [scatola_finta("SKU-A", 18, 7, 30, 40, 30)])
    plan_id = c.piano_a(__import__("stadio").SCATOLE_DA_DICHIARARE)
    assert c.get(f"{_B}/{plan_id}")["status"] == "ACTIVE"
