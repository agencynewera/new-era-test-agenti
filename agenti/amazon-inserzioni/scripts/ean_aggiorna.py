"""Raccoglie i codici di un cliente Amazon e li salva per ean_catalogo.

Tre fonti in cascata, perche' nessuna da sola basta (verificato il 05/09/2026):

1. il report GET_MERCHANT_LISTINGS_ALL_DATA e' l'unica enumerazione completa
   del catalogo -- searchListingsItems tronca in silenzio: su Galeone dava 634
   SKU su IT contro i 12.484 del report. Porta gia' product-id/product-id-type,
   cioe' il codice con cui l'inserzione e' stata creata;
2. per le inserzioni agganciate a un ASIN esistente quel codice non c'e': sta
   sulla scheda di catalogo, che si legge con Catalog Items;
3. la scheda e' per marketplace, e lo stesso ASIN puo' rispondere 404 su un
   mercato e portare l'EAN su un altro: vanno provati tutti, IT per primo.
"""
from __future__ import annotations
import csv
import io
import json
import os
import time

CODICI = ("3", "4")   # 3 = UPC, 4 = EAN. 1 = ASIN, che non e' un codice prodotto.


def righe_da_report(testo: str) -> list:
    """Le righe utili del report. Una riga senza SKU non identifica niente."""
    fuori = []
    for x in csv.DictReader(io.StringIO(testo), delimiter="\t"):
        sku = (x.get("seller-sku") or "").strip()
        if not sku:
            continue
        pid = (x.get("product-id") or "").strip()
        fuori.append({"sku": sku,
                      "ean": pid if x.get("product-id-type") in CODICI else "",
                      "asin": (x.get("asin1") or "").strip(),
                      "stato": x.get("status", ""),
                      "titolo": (x.get("item-name") or "")[:200]})
    return fuori


def fondi(per_mercato: dict) -> list:
    """Una riga per SKU. Il codice si prende dal primo mercato che ce l'ha:
    e' del prodotto, non del mercato."""
    uniti = {}
    for mercato, righe in sorted(per_mercato.items()):
        for r in righe:
            u = uniti.setdefault(r["sku"], {"sku": r["sku"], "ean": "", "asin": "",
                                            "tipo": "", "mercati": [], "stato": set(),
                                            "titolo": ""})
            u["ean"] = u["ean"] or r["ean"]
            u["asin"] = u["asin"] or r["asin"]
            u["titolo"] = u["titolo"] or r["titolo"]
            u["stato"].add(r["stato"])
            if mercato not in u["mercati"]:
                u["mercati"].append(mercato)
    fuori = []
    for u in uniti.values():
        u["mercati"] = " ".join(sorted(u["mercati"]))
        u["stato"] = " ".join(sorted(s for s in u["stato"] if s))
        fuori.append(u)
    return fuori


def salva(cliente: str, righe: list, radice: str = None) -> str:
    import ean_catalogo
    radice = radice or ean_catalogo.RADICE
    os.makedirs(radice, exist_ok=True)
    percorso = os.path.join(radice, ean_catalogo.nome_file(cliente))
    with open(percorso, "w") as f:
        json.dump({"cliente": cliente,
                   "generata": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "righe": righe}, f, ensure_ascii=False)
    return percorso


# --- da qui in giu' si parla con Amazon -------------------------------------

EU = {"APJ6JRA9NG5V4": "IT", "A1PA6795UKMFR9": "DE", "A13V1IB3VIYZZH": "FR",
      "A1RKKUPIHCS9HS": "ES", "A1F83G8C2ARO7P": "UK", "A1805IZSGTT6HS": "NL",
      "AMEN7PMS3EDWL": "BE", "A1C3SOZRARQ6R3": "PL", "A2NODRKZP88ZB9": "SE"}
ORDINE = ["APJ6JRA9NG5V4", "A1PA6795UKMFR9", "A13V1IB3VIYZZH", "A1RKKUPIHCS9HS",
          "A1F83G8C2ARO7P", "A1805IZSGTT6HS", "AMEN7PMS3EDWL", "A1C3SOZRARQ6R3",
          "A2NODRKZP88ZB9"]
CLASSE = {"VARIATION_PARENT": "padre di variazione", "BASE_PRODUCT": "prodotto",
          "PRODUCT_BUNDLE": "bundle"}


def mercati_del_cliente(client) -> list:
    r = client.get("/sellers/v1/marketplaceParticipations")
    attivi = {p["marketplace"]["id"] for p in r.get("payload") or []
              if (p.get("participation") or {}).get("isParticipating")}
    return [m for m in ORDINE if m in attivi]


def scarica_report(client, mercati: list, avviso=print) -> dict:
    """Un report per mercato. createReport accetta 1 richiesta al minuto con
    una riserva di 15: oltre quella si prende 429 e va solo aspettato."""
    import gzip
    import requests
    from spapi_client import SpApiError, _USER_AGENT
    in_corso, per_mercato = {}, {}
    restano = list(mercati)
    while restano:
        m = restano[0]
        try:
            r = client.post("/reports/2021-06-30/reports", json_body={
                "reportType": "GET_MERCHANT_LISTINGS_ALL_DATA", "marketplaceIds": [m]})
            in_corso[m] = r["reportId"]
            restano.pop(0)
        except SpApiError as e:
            if "429" not in str(e):
                avviso(f"  {EU[m]}: report non richiesto ({str(e)[:60]})")
                restano.pop(0)
                continue
            time.sleep(20)
    scadenza = time.time() + 2400
    while in_corso and time.time() < scadenza:
        time.sleep(30)
        for m, rid in list(in_corso.items()):
            try:
                d = client.get(f"/reports/2021-06-30/reports/{rid}")
            except SpApiError:
                continue
            stato = d.get("processingStatus")
            if stato in ("IN_QUEUE", "IN_PROGRESS"):
                continue
            del in_corso[m]
            if stato != "DONE":
                avviso(f"  {EU[m]}: report {stato}")
                continue
            doc = client.get(f"/reports/2021-06-30/documents/{d['reportDocumentId']}")
            grezzo = requests.get(doc["url"], headers={"User-Agent": _USER_AGENT}, timeout=300).content
            if doc.get("compressionAlgorithm") == "GZIP":
                grezzo = gzip.decompress(grezzo)
            per_mercato[EU[m]] = righe_da_report(grezzo.decode("utf-8", "replace"))
            avviso(f"  {EU[m]}: {len(per_mercato[EU[m]])} inserzioni")
    for m in in_corso:
        avviso(f"  {EU[m]}: report non pronto entro il tempo massimo, mercato saltato")
    return per_mercato


def completa_da_catalogo(client, righe: list, mercati: list, avviso=print) -> None:
    """Gli EAN che il report non porta stanno sulla scheda di catalogo. 20 ASIN
    per chiamata, un mercato alla volta finche' restano scoperti."""
    from spapi_client import SpApiError
    for m in mercati:
        aperte = [r for r in righe if not r["ean"] and r["asin"]
                  and r["tipo"] != "padre di variazione"]
        if not aperte:
            break
        per_asin = {}
        for r in aperte:
            per_asin.setdefault(r["asin"], []).append(r)
        asins = list(per_asin)
        for i in range(0, len(asins), 20):
            blocco = asins[i:i + 20]
            try:
                risposta = client.get("/catalog/2022-04-01/items", params={
                    "marketplaceIds": m, "identifiers": ",".join(blocco),
                    "identifiersType": "ASIN", "includedData": "identifiers,summaries",
                    "pageSize": 20})
            except SpApiError:
                time.sleep(3)
                continue
            for voce in risposta.get("items") or []:
                ean = ""
                for b in voce.get("identifiers") or []:
                    for idn in b.get("identifiers") or []:
                        if idn.get("identifierType") in ("EAN", "UPC", "GTIN"):
                            v = str(idn.get("identifier"))
                            # Amazon manda sia 0806891539045 sia 806891539045
                            if not ean or (len(ean) != 13 and len(v) == 13):
                                ean = v
                classe = next((s.get("itemClassification") for s in voce.get("summaries") or []
                               if s.get("itemClassification")), "")
                for r in per_asin.get(voce.get("asin"), []):
                    if ean and not r["ean"]:
                        r["ean"] = ean
                    if classe and not r["tipo"]:
                        r["tipo"] = CLASSE.get(classe, classe.lower())
            time.sleep(0.55)
        avviso(f"  dopo il catalogo {EU[m]}: {sum(1 for r in righe if not r['ean'])} senza codice")


def raccogli(cliente: str, avviso=print) -> list:
    import spapi_client
    client = spapi_client.crea_client(cliente)
    mercati = mercati_del_cliente(client)
    avviso(f"{cliente}: mercati {', '.join(EU[m] for m in mercati)}")
    righe = fondi(scarica_report(client, mercati, avviso))
    avviso(f"  {len(righe)} SKU, {sum(1 for r in righe if r['ean'])} con codice dal report")
    completa_da_catalogo(client, righe, mercati, avviso)
    return righe


def main(argv=None):
    import argparse
    a = argparse.ArgumentParser(description="Raccoglie i codici EAN di un cliente Amazon.")
    a.add_argument("cliente", help="nome del cliente (es. \"Tecno Cold\")")
    a.add_argument("--radice", default=None, help="dove salvare (default ~/.config/newera-amazon/ean)")
    o = a.parse_args(argv)
    righe = raccogli(o.cliente)
    percorso = salva(o.cliente, righe, o.radice)
    con = sum(1 for r in righe if r["ean"])
    print(f"\n{len(righe)} SKU | con codice {con} | senza {len(righe) - con}")
    print("salvato in", percorso)


if __name__ == "__main__":
    main()
