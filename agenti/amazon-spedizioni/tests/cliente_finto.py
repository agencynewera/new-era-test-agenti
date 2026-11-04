"""Amazon finto CON STATO per l'orchestratore. Risponde per percorso come il
ClienteFinto di test_prova_a_vuoto, ma ogni POST fa avanzare il piano come
farebbe Amazon (imballaggio accettato, scatole registrate, piazzamenti
offerti, spedizioni confermate, corriere prenotato), così che stadio.leggi
veda a ogni passo ciò che vedrebbe dal vivo. Le forme copiano le fixture di
tests/fixtures/stadio (02/09/2026). Il GET sul piano nudo (senza sotto-percorso)
è una proiezione: packingOptions/placementOptions portano solo id + status,
mai i dettagli (packingGroups, fees, ...), come nelle fixture reali."""
from __future__ import annotations
import re

_B = "/inbound/fba/2024-03-20/inboundPlans"

from spapi_client import SpApiError


def scatola_finta(sku, pezzi, peso, l, w, h, quantita=1, modello="P1 - B1", asin="B0TEST", altri=()):
    """Una scatola come la restituisce `GET /inboundPlans/{id}/boxes` di un
    piano spedito (forma di tests/fixtures/stadio/bedshire_trasporto_prenotato.json,
    03/09/2026). `altri` aggiunge righe di altri SKU: una scatola mista."""
    items = [{"msku": sku, "quantity": pezzi, "asin": asin, "fnsku": "X0FINTO", "labelOwner": "NONE",
              "prepInstructions": []}]
    items += [{"msku": s, "quantity": q, "asin": "B0ALTRO", "fnsku": "X1FINTO", "labelOwner": "NONE",
               "prepInstructions": []} for s, q in altri]
    return {"quantity": quantita, "externalContainerIdentifierType": "AMAZON", "templateName": modello,
            "packageId": f"pk-{sku}-{pezzi}", "weight": {"unit": "KG", "value": peso},
            "dimensions": {"unitOfMeasurement": "CM", "length": l, "width": w, "height": h},
            "items": items, "contentInformationSource": "BOX_CONTENT_PROVIDED"}


class ClienteFinto:
    def __init__(self, scadenza_piazzamento="2026-09-10T00:00:00.000Z",
                 scadenza_preventivi="2026-09-10T00:00:00.000Z"):
        self.scadenza_piazzamento = scadenza_piazzamento
        self.scadenza_preventivi = scadenza_preventivi
        self.piani: dict[str, dict] = {}
        self.post_fatte: list[tuple[str, dict | None]] = []
        self.get_fatte: list[tuple[str, dict | None]] = []
        self.operazioni: set[str] = set()
        self._n = 0
        self.spediti: list[dict] = []                 # listInboundPlans?status=SHIPPED
        self.scatole_spedite: dict[str, list[dict]] = {}
        self.illeggibili: set[str] = set()
        self.vendite: dict[str, int] = {}      # ASIN -> unita' vendute, per /sales/v1/orderMetrics
        self.vendite_chieste: list[dict] = []
        self.report_tsv: str | None = None
        self.report_stato = "DONE"
        self.report_creati: list[dict] = []

    # ---- Reports API 2021-06-30 --------------------------------------------
    def prepara_report(self, tsv: str, stato: str = "DONE") -> None:
        """Il TSV che `GET /documents/{id}` servirà, e in che stato finisce il
        report (DONE, FATAL, CANCELLED o IN_PROGRESS per il timeout)."""
        import tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False, encoding="utf-8")
        f.write(tsv)
        f.close()
        self.report_tsv = f.name
        self.report_stato = stato

    # ---- stato precostruito -------------------------------------------------
    def piano_a(self, stadio: str, plan_id: str = "wf1") -> str:
        import stadio as S
        p = self._nuovo(plan_id, [{"msku": "SKU-1", "quantity": 30, "prepOwner": "NONE", "labelOwner": "NONE"}])
        ordine = [S.IMBALLAGGIO_DA_CONFERMARE, S.SCATOLE_DA_DICHIARARE, S.PIAZZAMENTO_DA_GENERARE,
                  S.PIAZZAMENTO_DA_SCEGLIERE, S.TRASPORTO_DA_SCEGLIERE, S.TRASPORTO_PRENOTATO]
        passi = ordine.index(stadio)
        if passi >= 1:
            p["packing"] = {"packingOptionId": "po1", "status": "ACCEPTED", "packingGroups": ["pg1"]}
        if passi >= 2:
            p["boxes"] = [{"packageId": "pk1", "quantity": 1}]
        if passi >= 3:
            self._genera_piazzamenti(p)
        if passi >= 4:
            self._conferma_piazzamento(p, "pl1")
        if passi >= 5:
            # Prenotare passa dai preventivi: a corriere prenotato Amazon li elenca ancora.
            p["trasporti"]["pl1"] = self._trasporto("sh1")
            p["spedizioni"]["sh1"]["selectedTransportationOptionId"] = "to-ups-sh1"
        return plan_id

    def spedito(self, plan_id: str, creato: str, scatole: list[dict], leggibile: bool = True) -> None:
        """Un piano SHIPPED com'è nella lista di Amazon (forma vera del
        03/09/2026: niente shipments né items nella voce di lista), con le
        sue scatole. `leggibile=False` fa rispondere 403 su /boxes."""
        self.spediti.append({"inboundPlanId": plan_id, "status": "SHIPPED", "createdAt": creato,
                             "lastUpdatedAt": creato, "name": "", "marketplaceIds": ["APJ6JRA9NG5V4"],
                             "sourceAddress": {"name": "Finto", "city": "Prato", "countryCode": "IT"}})
        self.scatole_spedite[plan_id] = list(scatole)
        if not leggibile:
            self.illeggibili.add(plan_id)

    @staticmethod
    def _pagina(elenco, params, chiave, massimo):
        params = params or {}
        da = int(params.get("paginationToken") or 0)
        quanti = min(int(params.get("pageSize") or 10), massimo)
        prossimo = da + quanti
        return {chiave: elenco[da:prossimo],
                "pagination": ({"nextToken": str(prossimo)} if prossimo < len(elenco) else {})}

    # ---- contratto SpApiClient ---------------------------------------------
    def attendi_operazione(self, operation_id, attesa_max_s=300):
        if operation_id not in self.operazioni:
            raise AssertionError(f"interrogata un'operazione mai aperta da una POST: {operation_id}")
        return {"operationId": operation_id, "operationStatus": "SUCCESS"}

    def post(self, path, json_body=None, params=None):
        self.post_fatte.append((path, json_body))
        self._n += 1
        op = f"op-{self._n}"
        self.operazioni.add(op)
        if path == "/reports/2021-06-30/reports":
            self.report_creati.append(json_body)
            return {"reportId": "rep-1"}
        if path == _B:
            pid = f"wf{len(self.piani) + 1}"
            self._nuovo(pid, json_body["items"])
            return {"inboundPlanId": pid, "operationId": op}
        pid = path[len(_B) + 1:].split("/")[0]
        p = self.piani[pid]
        resto = path[len(_B) + len(pid) + 1:]
        if resto == "/packingOptions":
            p["packing"] = {"packingOptionId": "po1", "status": "OFFERED", "packingGroups": ["pg1"]}
        elif resto == "/packingOptions/po1/confirmation":
            p["packing"]["status"] = "ACCEPTED"
        elif resto == "/packingInformation":
            p["boxes"] = [{"packageId": f"pk{i}", "quantity": 1}
                          for g in json_body["packageGroupings"] for i, b in enumerate(g["boxes"], 1)
                          for _ in range(b["quantity"])]
        elif resto == "/placementOptions":
            self._genera_piazzamenti(p)
        elif re.fullmatch(r"/placementOptions/(pl\d)/confirmation", resto):
            self._conferma_piazzamento(p, resto.split("/")[2])
        elif resto == "/transportationOptions":
            pl = json_body["placementOptionId"]
            p["trasporti"][pl] = [o for c in json_body["shipmentTransportationConfigurations"]
                                  for o in self._trasporto(c["shipmentId"])]
        elif re.fullmatch(r"/shipments/(sh\d)/deliveryWindowOptions", resto):
            p["finestre"][resto.split("/")[2]] = [{"deliveryWindowOptionId": "dw1", "startDate": "2026-09-08T00:00Z",
                                                   "endDate": "2026-09-12T00:00Z", "availabilityType": "AVAILABLE"}]
        elif re.fullmatch(r"/shipments/(sh\d)/deliveryWindowOptions/dw1/confirmation", resto):
            p["spedizioni"][resto.split("/")[2]]["selectedDeliveryWindow"] = {"deliveryWindowOptionId": "dw1"}
        elif resto == "/transportationOptions/confirmation":
            for s in json_body["transportationSelections"]:
                p["spedizioni"][s["shipmentId"]]["selectedTransportationOptionId"] = s["transportationOptionId"]
        else:
            raise AssertionError(f"POST su un percorso senza comportamento: {path}")
        return {"operationId": op}

    def get(self, path, params=None):
        self.get_fatte.append((path, params))
        if path == "/sales/v1/orderMetrics":
            self.vendite_chieste.append(dict(params or {}))
            asin = (params or {}).get("asin")
            return {"payload": [{"unitCount": self.vendite.get(asin, 0), "orderCount": 0}]}
        if path == "/reports/2021-06-30/reports/rep-1":
            return {"reportId": "rep-1", "processingStatus": self.report_stato,
                    "reportDocumentId": "doc-1"}
        if path == "/reports/2021-06-30/documents/doc-1":
            return {"reportDocumentId": "doc-1", "url": f"file://{self.report_tsv}"}
        if path == "/inbound/fba/2024-03-20/items/prepDetails":
            return {"mskuPrepDetails": [{"msku": m, "prepOwnerConstraint": "NONE_ONLY",
                                         "labelOwnerConstraint": "NONE_ONLY", "prepCategory": "NONE"}
                                        for m in params["mskus"].split(",")]}
        m = re.fullmatch(r"/fba/inbound/v0/shipments/(FBA\w+)/labels", path)
        if m:
            return {"payload": {"DownloadURL": f"https://amazon-labels.example/{m.group(1)}.pdf"}}
        if path == _B:
            assert int((params or {}).get("pageSize") or 10) <= 30, "listInboundPlans: pageSize massimo 30"
            if (params or {}).get("status") != "SHIPPED":
                return {"inboundPlans": [], "pagination": {}}
            return self._pagina(self.spediti, params, "inboundPlans", 30)
        assert path.startswith(_B + "/"), f"GET su un percorso senza fixture: {path}"
        pid = path[len(_B) + 1:].split("/")[0]
        if pid in self.scatole_spedite:
            assert path == f"{_B}/{pid}/boxes", f"di un piano spedito si leggono solo le scatole: {path}"
            if pid in self.illeggibili:
                raise SpApiError(f"403 su {path}: Unauthorized")
            return self._pagina(self.scatole_spedite[pid], params, "boxes", 100)
        p = self.piani[pid]
        resto = path[len(_B) + len(pid) + 1:]
        if resto == "":
            return {"inboundPlanId": pid, "status": "ACTIVE", "name": p["name"],
                    "packingOptions": [{"packingOptionId": p["packing"]["packingOptionId"],
                                        "status": p["packing"]["status"]}] if p["packing"] else [],
                    "placementOptions": [{"placementOptionId": o["placementOptionId"], "status": o["status"]}
                                         for o in p["piazzamenti"]],
                    "shipments": [{"shipmentId": sid, "status": s["status"]} for sid, s in p["spedizioni"].items()
                                  if s["status"] == "WORKING"]}
        if resto == "/packingOptions":
            return {"packingOptions": [p["packing"]] if p["packing"] else [], "pagination": {}}
        if resto == "/packingGroups/pg1/items":
            return {"items": [{"msku": i["msku"], "quantity": i["quantity"], "labelOwner": "NONE"} for i in p["items"]]}
        if resto == "/placementOptions":
            return {"placementOptions": p["piazzamenti"], "pagination": {}}
        if resto == "/boxes":
            return {"boxes": p["boxes"], "pagination": {}}
        m = re.fullmatch(r"/shipments/(sh\d)", resto)
        if m:
            return dict(p["spedizioni"][m.group(1)])
        m = re.fullmatch(r"/shipments/(sh\d)/items", resto)
        if m:
            return {"items": [{"msku": i["msku"], "quantity": i["quantity"]} for i in p["items"]]}
        m = re.fullmatch(r"/shipments/(sh\d)/boxes", resto)
        if m:
            conf = p["spedizioni"][m.group(1)].get("shipmentConfirmationId")
            return {"boxes": [{"boxId": f"{conf}U000001", "packageId": "pk1"}] if conf else []}
        if resto == "/transportationOptions":
            return {"transportationOptions": p["trasporti"].get(params["placementOptionId"], [])}
        m = re.fullmatch(r"/shipments/(sh\d)/deliveryWindowOptions", resto)
        if m:
            return {"deliveryWindowOptions": p["finestre"].get(m.group(1), [])}
        raise AssertionError(f"GET su un percorso senza fixture: {path}")

    # ---- interno ------------------------------------------------------------
    def _nuovo(self, pid, items):
        p = {"name": f"Spedizione {items[0]['msku']}", "items": items, "packing": None, "boxes": [],
             "piazzamenti": [], "spedizioni": {}, "trasporti": {}, "finestre": {}}
        self.piani[pid] = p
        return p

    def _genera_piazzamenti(self, p):
        p["piazzamenti"] = [
            {"placementOptionId": "pl1", "status": "OFFERED", "expiration": self.scadenza_piazzamento,
             "shipmentIds": ["sh1"], "fees": [{"type": "FEE", "target": "Placement Services",
                                               "value": {"amount": 0, "code": "EUR"}}], "discounts": []},
            {"placementOptionId": "pl2", "status": "OFFERED", "expiration": self.scadenza_piazzamento,
             "shipmentIds": ["sh2"], "fees": [{"type": "FEE", "target": "Placement Services",
                                               "value": {"amount": 4.0, "code": "EUR"}}], "discounts": []},
        ]
        for sid, fc in (("sh1", "MXP5"), ("sh2", "TRN3")):
            p["spedizioni"][sid] = {"shipmentId": sid, "status": "UNCONFIRMED",
                                    "destination": {"destinationType": "AMAZON_WAREHOUSE", "warehouseId": fc}}

    def _conferma_piazzamento(self, p, pl_id):
        for o in p["piazzamenti"]:
            o["status"] = "ACCEPTED" if o["placementOptionId"] == pl_id else "EXPIRED"
        scelta = next(o for o in p["piazzamenti"] if o["placementOptionId"] == pl_id)
        for sid in scelta["shipmentIds"]:
            p["spedizioni"][sid]["status"] = "WORKING"
            p["spedizioni"][sid]["shipmentConfirmationId"] = f"FBA1234{sid}"
        for b in p["boxes"]:
            b["boxId"] = f"FBA1234{scelta['shipmentIds'][0]}U000001"

    def _trasporto(self, sid):
        return [
            {"transportationOptionId": f"to-ups-{sid}", "shipmentId": sid,
             "shippingSolution": "AMAZON_PARTNERED_CARRIER", "shippingMode": "GROUND_SMALL_PARCEL",
             "carrier": {"name": "UPS", "alphaCode": "UPSN"}, "preconditions": [],
             "quote": {"cost": {"amount": 7.9, "code": "EUR"}, "expiration": self.scadenza_preventivi}},
            {"transportationOptionId": f"to-proprio-{sid}", "shipmentId": sid,
             "shippingSolution": "USE_YOUR_OWN_CARRIER", "shippingMode": "FREIGHT_LTL",
             "preconditions": ["CONFIRMED_DELIVERY_WINDOW"]},
        ]
