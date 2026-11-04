"""Creazione di una scheda su un altro marketplace partendo dal listing di
origine (Tecno Cold IT -> FR/ES, 2026-09-23).

Ricetta verificata su DE (09/2026) e in VALIDATION_PREVIEW su FR/ES: gli
attributi del listing di origine traslati (marketplace_id, language_tag), i
testi sostituiti con la traduzione, `merchant_suggested_asin` = ASIN di
origine (con l'EAN che combacia Amazon estende lo stesso ASIN), parentela
verso un padre gia' creato sul marketplace di destinazione. Fuori: cio' che
e' del marketplace di origine (nodi di navigazione, modello di spedizione,
documenti caricati, EPREL fittizio) e l'offerta B2B.

Chi chiama porta i testi tradotti: qui non si traduce e non si inventa; un
campo di testo presente in origine e senza traduzione e' un errore.
"""
from __future__ import annotations
import copy
from crea import EsitoCreazione
from spapi_client import SpApiError, percorso_listing
import registro

# Campi di testo: se il listing di origine li ha, la traduzione e' obbligatoria.
CAMPI_TESTO = ("item_name", "bullet_point", "product_description", "size", "color", "style",
               "included_components", "warranty_description", "generic_keyword", "material",
               "special_feature")
# Mai portati su un altro marketplace: sono del marketplace di origine.
ESCLUSI_SEMPRE = ("recommended_browse_nodes", "merchant_shipping_group", "compliance_media",
                  "eprel_registration_number", "skip_offer", "package_level")
# list_price resta: su FR/ES SODA_MAKER e ELECTRONIC_COMPONENT_FAN lo pretendono
# nella creazione (90220 "Prix catalogue HT", sonda del 23/09), anche se nella
# sola offerta lo ignorano (90000900).
# Tolti e ricalcolati per la destinazione (padre, ASIN, tema).
RICALCOLATI = ("merchant_suggested_asin", "parentage_level", "child_parent_sku_relationship",
               "variation_theme")
SOLO_FIGLI = ("purchasable_offer", "fulfillment_availability", "condition_type",
              "externally_assigned_product_identifier")


def trasla(attributi: dict, marketplace_id: str, lingua: str) -> dict:
    """Copia profonda con marketplace_id e language_tag del marketplace di
    destinazione, ovunque compaiano. I valori non si toccano."""
    def _t(o):
        if isinstance(o, dict):
            return {k: (marketplace_id if k == "marketplace_id" else lingua if k == "language_tag" else _t(v))
                    for k, v in o.items()}
        if isinstance(o, list):
            return [_t(x) for x in o]
        return o
    return _t(copy.deepcopy(attributi))


def _testo(valori: list[str], marketplace_id: str, lingua: str, con_lingua: bool) -> list[dict]:
    out = []
    for v in valori:
        el = {"value": v, "marketplace_id": marketplace_id}
        if con_lingua:
            el["language_tag"] = lingua
        out.append(el)
    return out


def costruisci_scheda(attributi_origine: dict, product_type: str, marketplace_id: str, lingua: str,
                      testi: dict[str, list[str]], *, asin_suggerito: str | None = None,
                      parent_sku: str | None = None, tema: str | None = None,
                      padre: bool = False) -> dict:
    if not padre and not asin_suggerito:
        raise ValueError("figlio senza ASIN di origine da suggerire")
    if not padre and not parent_sku:
        raise ValueError("figlio senza padre: la scheda nascerebbe sciolta")
    a = trasla(attributi_origine, marketplace_id, lingua)
    for k in ESCLUSI_SEMPRE + RICALCOLATI:
        a.pop(k, None)
    if padre:
        for k in SOLO_FIGLI:
            a.pop(k, None)
    for campo in CAMPI_TESTO:
        if campo in attributi_origine and campo not in testi:
            raise ValueError(f"{campo}: presente in origine ma senza traduzione")
    for campo, valori in testi.items():
        if not valori:
            continue
        orig = attributi_origine.get(campo) or [{}]
        con_lingua = "language_tag" in orig[0] or campo in CAMPI_TESTO
        a[campo] = _testo(valori, marketplace_id, lingua, con_lingua)
    if not padre:
        # solo l'offerta per tutti i clienti (mai il B2B) e lo stesso ASIN di origine
        a["purchasable_offer"] = [po for po in a.get("purchasable_offer") or []
                                  if po.get("audience", "ALL") == "ALL"]
        if not a["purchasable_offer"]:
            raise ValueError("offerta per tutti i clienti mancante in origine")
        a["merchant_suggested_asin"] = [{"value": asin_suggerito, "marketplace_id": marketplace_id}]
        a["parentage_level"] = [{"value": "child", "marketplace_id": marketplace_id}]
        a["child_parent_sku_relationship"] = [{"child_relationship_type": "variation",
                                               "parent_sku": parent_sku, "marketplace_id": marketplace_id}]
    else:
        a["parentage_level"] = [{"value": "parent", "marketplace_id": marketplace_id}]
    if tema:
        a["variation_theme"] = [{"name": tema, "marketplace_id": marketplace_id}]
    return {"productType": product_type,
            "requirements": "LISTING_PRODUCT_ONLY" if padre else "LISTING",
            "attributes": a}


def pubblica_scheda(client, seller_id: str, marketplace_id: str, sku: str, payload: dict, *,
                    anteprima: bool = False) -> EsitoCreazione:
    params = {"marketplaceIds": marketplace_id}
    if anteprima:
        params["mode"] = "VALIDATION_PREVIEW"
    r = client.put(percorso_listing(seller_id, sku), json_body=payload, params=params)
    return EsitoCreazione(sku=r.get("sku", sku), stato=r.get("status", "INVALID"), issues=r.get("issues", []))


def pubblica_lotto_schede(client, seller_id: str, marketplace_id: str, cliente: str,
                          righe: list[tuple[str, dict]]) -> list[EsitoCreazione]:
    """Una scheda alla volta, nell'ordine dato (i padri prima dei figli), nel
    registro `pubblicazioni`. Un errore di trasporto non ferma le altre."""
    esiti: list[EsitoCreazione] = []
    for sku, payload in righe:
        indice = registro.registra_tentativo_creazione(cliente, sku)
        try:
            esito = pubblica_scheda(client, seller_id, marketplace_id, sku, payload)
        except SpApiError as errore:
            registro.registra_esito_creazione(cliente, indice, "errore", [{"message": str(errore)}])
            esiti.append(EsitoCreazione(sku, "errore", [{"message": str(errore)}]))
            continue
        registro.registra_esito_creazione(cliente, indice, esito.stato, esito.issues)
        esiti.append(esito)
    return esiti


def patch_ops(attributi: dict) -> list[dict]:
    """Un'operazione replace per attributo, valore intero (mai un elemento
    isolato, mai un valore vuoto: uno svuotamento non si propone)."""
    ops = []
    for campo, valore in attributi.items():
        if valore in (None, "", [], {}):
            raise ValueError(f"{campo}: valore vuoto in un PATCH")
        ops.append({"op": "replace", "path": f"/attributes/{campo}", "value": valore})
    return ops
