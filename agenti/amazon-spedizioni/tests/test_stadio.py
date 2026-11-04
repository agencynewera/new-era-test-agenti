import json
import os
from datetime import datetime, timezone
import pytest
import stadio

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "stadio")
_ADESSO = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)   # dopo la scadenza del 1/09


def _foto(nome) -> stadio.Fotografia:
    with open(os.path.join(_FIXTURE, f"{nome}.json")) as f:
        d = json.load(f)
    return stadio.Fotografia(
        piano=d["piano"],
        packing_options=d["packingOptions"].get("packingOptions") or [],
        placement_options=d["placementOptions"].get("placementOptions") or [],
        boxes=d["boxes"].get("boxes") or [],
        spedizioni=d["shipments"],
    )


def test_bozza_vuota_e_imballaggio_da_confermare():
    assert stadio.riconosci(_foto("phytopet_bozza_vuota"), _ADESSO) == stadio.IMBALLAGGIO_DA_CONFERMARE


def test_piazzamenti_offerti_ma_scaduti_vanno_rigenerati():
    assert stadio.riconosci(_foto("phytopet_piazzamento_scaduto"), _ADESSO) == stadio.PIAZZAMENTO_DA_GENERARE


def test_stesso_piano_prima_della_scadenza_era_da_scegliere():
    """Stessa fotografia, orologio al 30/08: le due opzioni OFFERED valevano."""
    prima = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    assert stadio.riconosci(_foto("phytopet_piazzamento_scaduto"), prima) == stadio.PIAZZAMENTO_DA_SCEGLIERE


def test_imballaggio_accettato_senza_scatole_e_scatole_da_dichiarare():
    foto = _foto("phytopet_piazzamento_scaduto")
    foto.boxes = []
    assert stadio.riconosci(foto, _ADESSO) == stadio.SCATOLE_DA_DICHIARARE


def test_bedshire_ha_il_trasporto_prenotato():
    assert stadio.riconosci(_foto("bedshire_trasporto_prenotato"), _ADESSO) == stadio.TRASPORTO_PRENOTATO


def test_trasporto_prenotato_con_etichette_scaricate_e_completato():
    assert stadio.riconosci(_foto("bedshire_trasporto_prenotato"), _ADESSO,
                            etichette_scaricate=True) == stadio.COMPLETATO


def test_piazzamento_accettato_senza_corriere_e_trasporto_da_scegliere():
    foto = _foto("bedshire_trasporto_prenotato")
    for s in foto.spedizioni.values():
        s.pop("selectedTransportationOptionId", None)
    assert stadio.riconosci(foto, _ADESSO) == stadio.TRASPORTO_DA_SCEGLIERE


def test_scaduta_confronta_istanti_iso_con_z():
    assert stadio.scaduta("2026-09-01T20:39:56.199Z", _ADESSO) is True
    assert stadio.scaduta("2026-09-03T00:00:00Z", _ADESSO) is False
    assert stadio.scaduta(None, _ADESSO) is False


def test_letture_di_supporto():
    foto = _foto("phytopet_piazzamento_scaduto")
    assert stadio.piazzamento_accettato(foto) is None
    assert stadio.piazzamenti_offerti_validi(foto, _ADESSO) == []
    assert stadio.gruppi_imballaggio_accettati(foto) == foto.packing_options[0]["packingGroups"]
    bed = _foto("bedshire_trasporto_prenotato")
    assert stadio.piazzamento_accettato(bed)["placementOptionId"] == "pl0f47b947-9e43-49f7-b9a7-5210f0dee02e"


def test_leggi_fa_le_cinque_letture_e_le_spedizioni_di_ogni_opzione():
    class Client:
        def __init__(self):
            self.percorsi = []
        def get(self, path, params=None):
            self.percorsi.append(path)
            b = "/inbound/fba/2024-03-20/inboundPlans/wf1"
            return {
                b: {"inboundPlanId": "wf1", "shipments": [{"shipmentId": "sh9", "status": "WORKING"}]},
                f"{b}/packingOptions": {"packingOptions": [{"packingOptionId": "po1", "status": "ACCEPTED", "packingGroups": ["pg1"]}]},
                f"{b}/placementOptions": {"placementOptions": [{"placementOptionId": "pl1", "status": "OFFERED", "shipmentIds": ["sh1"]}]},
                f"{b}/boxes": {"boxes": [{"packageId": "pk1"}]},
                f"{b}/shipments/sh1": {"shipmentId": "sh1", "status": "UNCONFIRMED"},
                f"{b}/shipments/sh9": {"shipmentId": "sh9", "status": "WORKING"},
            }[path]
    c = Client()
    foto = stadio.leggi(c, "wf1")
    assert set(foto.spedizioni) == {"sh1", "sh9"}
    assert foto.boxes == [{"packageId": "pk1"}]
    assert c.percorsi.count("/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1") == 1


def test_leggi_segue_le_pagine_delle_scatole():
    """`Fotografia.boxes` dice di essere l'elenco delle scatole: con più di
    una pagina (pageSize 100) deve portarle tutte, non la prima."""
    class Client:
        def get(self, path, params=None):
            b = "/inbound/fba/2024-03-20/inboundPlans/wf1"
            if path == f"{b}/boxes":
                if (params or {}).get("paginationToken") == "p2":
                    return {"boxes": [{"packageId": "pk2"}], "pagination": {}}
                return {"boxes": [{"packageId": "pk1"}], "pagination": {"nextToken": "p2"}}
            return {
                b: {"inboundPlanId": "wf1", "shipments": []},
                f"{b}/packingOptions": {"packingOptions": []},
                f"{b}/placementOptions": {"placementOptions": []},
            }[path]
    foto = stadio.leggi(Client(), "wf1")
    assert [x["packageId"] for x in foto.boxes] == ["pk1", "pk2"]
