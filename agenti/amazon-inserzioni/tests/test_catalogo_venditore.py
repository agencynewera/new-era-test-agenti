# tests/test_catalogo_venditore.py
from __future__ import annotations
# Il catalogo del venditore letto con searchListingsItems: paginazione, filtri
# lato Amazon (padre, stato, severita') e filtri locali (prefisso, valore
# attuale di un campo in italiano). Forme viste dal vivo su Phy to Pet 03/09.
from unittest.mock import Mock
import pytest
from spapi_client import SpApiError
import catalogo_venditore as cv

IT = "APJ6JRA9NG5V4"


def _item(sku, *, asin="B0X", stati=("DISCOVERABLE", "BUYABLE"), titolo="Titolo", issues=(),
          attributi=None, canali=None, padre=None):
    return {"sku": sku,
            "summaries": [{"marketplaceId": IT, "asin": asin, "productType": "PET_FOOD",
                           "status": list(stati), "itemName": titolo}],
            "issues": list(issues), "attributes": attributi or {},
            "fulfillmentAvailability": canali or [{"fulfillmentChannelCode": "DEFAULT", "quantity": 100}],
            "_padre": padre}


class _Ricerca:
    """Un searchListingsItems in memoria: applica i filtri che Amazon applica
    (variationParentSku, withStatus/withoutStatus, withIssueSeverity) e pagina
    a blocchi di pageSize con nextToken."""

    def __init__(self, items, guasto_pagina=None):
        self.items = items
        self.chiamate = []
        self.guasto_pagina = guasto_pagina
        self.client = Mock()
        self.client.get.side_effect = self._get

    def _get(self, path, params=None):
        params = params or {}
        self.chiamate.append(dict(params))
        assert path == "/listings/2021-08-01/items/SELLER1"
        voci = self.items
        if params.get("variationParentSku"):
            voci = [i for i in voci if i["_padre"] == params["variationParentSku"]]
        if params.get("withStatus"):
            voci = [i for i in voci if params["withStatus"] in i["summaries"][0]["status"]]
        if params.get("withoutStatus"):
            voci = [i for i in voci if params["withoutStatus"] not in i["summaries"][0]["status"]]
        if params.get("withIssueSeverity"):
            voci = [i for i in voci if any(x["severity"] == params["withIssueSeverity"] for x in i["issues"])]
        n = int(params.get("pageSize", 20))
        da = int(params.get("pageToken") or 0)
        if self.guasto_pagina is not None and da == self.guasto_pagina:
            raise SpApiError("500 su pagina")
        pagina = voci[da:da + n]
        risposta = {"numberOfResults": len(voci), "items": [{k: v for k, v in i.items() if k != "_padre"} for i in pagina]}
        if da + n < len(voci):
            risposta["pagination"] = {"nextToken": str(da + n)}
        return risposta


# --------------------------------------------------------------- cerca ----

def test_cerca_pagina_fino_all_ultimo_token_e_restituisce_voci(monkeypatch):
    monkeypatch.setattr(cv, "PAGINA", 2)
    az = _Ricerca([_item("A"), _item("B"), _item("C")])
    voci = cv.cerca(az.client, "SELLER1", IT)
    assert [v.sku for v in voci] == ["A", "B", "C"]
    assert len(az.chiamate) == 2
    assert az.chiamate[1]["pageToken"] == "2"
    assert az.chiamate[0]["marketplaceIds"] == IT
    assert "issues" in az.chiamate[0]["includedData"] and "attributes" in az.chiamate[0]["includedData"]


def test_voce_porta_asin_stato_titolo_issues_attributi_e_canali():
    az = _Ricerca([_item("A", asin="B0HHFWM6G2", stati=("DISCOVERABLE",), titolo="Snack",
                         issues=[{"code": "18448", "message": "m", "severity": "WARNING"}],
                         attributi={"item_name": [{"value": "Snack"}]})])
    v = cv.cerca(az.client, "SELLER1", IT)[0]
    assert (v.sku, v.asin, v.product_type, v.stati, v.titolo) == ("A", "B0HHFWM6G2", "PET_FOOD", ["DISCOVERABLE"], "Snack")
    assert v.issues[0]["code"] == "18448"
    assert v.attributi == {"item_name": [{"value": "Snack"}]}
    assert v.canali == [{"fulfillmentChannelCode": "DEFAULT", "quantity": 100}]
    assert v.in_vendita is False


def test_cerca_per_padre_manda_variation_parent_sku():
    az = _Ricerca([_item("FIGLIO-1", padre="PADRE"), _item("FIGLIO-2", padre="PADRE"), _item("ALTRO")])
    voci = cv.cerca(az.client, "SELLER1", IT, padre="PADRE")
    assert [v.sku for v in voci] == ["FIGLIO-1", "FIGLIO-2"]
    assert az.chiamate[0]["variationParentSku"] == "PADRE"


def test_cerca_non_in_vendita_usa_without_status_buyable():
    az = _Ricerca([_item("OK"), _item("INCOMPLETO", stati=("DISCOVERABLE",))])
    voci = cv.cerca(az.client, "SELLER1", IT, in_vendita=False)
    assert [v.sku for v in voci] == ["INCOMPLETO"]
    assert az.chiamate[0]["withoutStatus"] == "BUYABLE" and "withStatus" not in az.chiamate[0]


def test_cerca_in_vendita_usa_with_status_buyable():
    az = _Ricerca([_item("OK"), _item("INCOMPLETO", stati=("DISCOVERABLE",))])
    assert [v.sku for v in cv.cerca(az.client, "SELLER1", IT, in_vendita=True)] == ["OK"]
    assert az.chiamate[0]["withStatus"] == "BUYABLE"


def test_cerca_per_severita_usa_with_issue_severity():
    err = [{"code": "1", "message": "grave", "severity": "ERROR"}]
    az = _Ricerca([_item("ROTTO", issues=err), _item("SANO")])
    assert [v.sku for v in cv.cerca(az.client, "SELLER1", IT, severita="ERROR")] == ["ROTTO"]
    assert az.chiamate[0]["withIssueSeverity"] == "ERROR"


def test_cerca_severita_sconosciuta_alza_valueerror():
    with pytest.raises(ValueError, match="severita"):
        cv.cerca(Mock(), "SELLER1", IT, severita="GRAVE")


def test_cerca_errore_di_trasporto_a_meta_risale_senza_risultati_parziali(monkeypatch):
    # Un catalogo a meta' e' peggio di nessun catalogo: "tutti gli Incomplete"
    # su una lista parziale farebbe credere che gli altri siano a posto.
    monkeypatch.setattr(cv, "PAGINA", 1)
    az = _Ricerca([_item("A"), _item("B")], guasto_pagina=1)
    with pytest.raises(SpApiError, match="500"):
        cv.cerca(az.client, "SELLER1", IT)


# -------------------------------------------------------------- filtra ----

def _voci(*items):
    return cv.cerca(_Ricerca(list(items)).client, "SELLER1", IT)


def test_filtra_per_prefisso_sku():
    voci = _voci(_item("PTP-SNK-1"), _item("PTP-SNK-2"), _item("SPAZZOLINO"))
    assert [v.sku for v in cv.filtra(voci, prefisso="PTP-SNK-")] == ["PTP-SNK-1", "PTP-SNK-2"]


def test_filtra_dove_confronta_il_valore_attuale_di_un_campo_in_italiano():
    # "tutti i Bedshire con tempo di gestione 6": il valore si legge dal DEFAULT.
    sei = {"fulfillment_availability": [{"fulfillment_channel_code": "AMAZON_EU"},
                                        {"fulfillment_channel_code": "DEFAULT", "quantity": 1000, "lead_time_to_ship_max_days": 6}]}
    due = {"fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": 1000, "lead_time_to_ship_max_days": 2}]}
    voci = _voci(_item("LENTO", attributi=sei), _item("VELOCE", attributi=due), _item("SENZA"))
    assert [v.sku for v in cv.filtra(voci, dove={"tempo di gestione": "6"})] == ["LENTO"]


def test_filtra_dove_prezzo_con_virgola():
    prezzo = lambda p: {"purchasable_offer": [{"currency": "EUR", "our_price": [{"schedule": [{"value_with_tax": p}]}]}]}
    voci = _voci(_item("A", attributi=prezzo(13.99)), _item("B", attributi=prezzo(13.9)))
    assert [v.sku for v in cv.filtra(voci, dove={"prezzo": "13,90"})] == ["B"]


def test_filtra_dove_campo_sconosciuto_alza_valueerror():
    with pytest.raises(ValueError, match="sconosciuto"):
        cv.filtra(_voci(_item("A")), dove={"materiale": "lana"})


# ------------------------------------------------- attributi mancanti ----

def test_attributi_mancanti_li_legge_dall_avviso_18448():
    msg = ("Nella tua richiesta mancano alcuni attributi chiave: age_range_description, "
           "specific_uses_for_product. La mancanza di questi attributi non impedisce la visualizzazione.")
    v = _voci(_item("A", issues=[{"code": "18448", "message": msg, "severity": "WARNING"}]))[0]
    assert cv.attributi_mancanti(v) == ["age_range_description", "specific_uses_for_product"]


def test_attributi_mancanti_vuoto_senza_quell_avviso():
    v = _voci(_item("A", issues=[{"code": "1", "message": "altro", "severity": "ERROR"}]))[0]
    assert cv.attributi_mancanti(v) == []


# ------------------------------------------------------------- tabella ----

def test_tabella_una_riga_per_sku_con_stato_e_conteggio_avvisi_e_gli_issues_se_richiesti():
    err = [{"code": "8541", "message": "Immagine principale mancante", "severity": "ERROR"}]
    voci = _voci(_item("A", asin="B0A", titolo="Snack lungo " * 20, issues=err),
                 _item("B", asin="B0B", stati=("DISCOVERABLE",)))
    testo = cv.tabella(voci)
    assert "2 SKU" in testo
    assert "A" in testo and "B0A" in testo and "in vendita" in testo and "1 ERROR" in testo
    assert "B" in testo and "NON in vendita" in testo
    assert "Immagine principale mancante" not in testo
    assert "Immagine principale mancante" in cv.tabella(voci, con_issues=True)


def test_attributi_mancanti_conserva_i_nomi_con_il_punto():
    # Dal vivo su Bedshire (03/09): "closure.type" veniva troncato a "closure"
    # perche' il punto nel nome sembrava la fine della frase.
    msg = ("Nella tua richiesta mancano alcuni attributi chiave: closure.type, item_weight. "
           "La mancanza di questi attributi non impedisce la visualizzazione.")
    v = _voci(_item("A", issues=[{"code": "18448", "message": msg, "severity": "WARNING"}]))[0]
    assert cv.attributi_mancanti(v) == ["closure.type", "item_weight"]
