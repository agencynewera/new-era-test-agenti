"""Prezzo, concorrenza, commissioni e restrizioni di una scheda. Sola lettura:
l'unico PUT è in `prova_offerta`, con mode=VALIDATION_PREVIEW, che Amazon
valida senza scrivere nulla."""
from __future__ import annotations
import time
from dataclasses import dataclass, field

from spapi_client import SpApiError, percorso_listing

# Seller ID di Amazon stessa come venditore, per negozio. Se compare fra le
# offerte, «Amazon vende» e il semaforo non va oltre il giallo.
AMAZON_VENDITORE = {
    "APJ6JRA9NG5V4": "A11IL2BNXBQZ4K",    # Amazon.it
    "A1PA6795UKMFR9": "A3JWKAKR8XB7XF",   # Amazon.de
    "A13V1IB3VIYZZH": "A1X6FK5RDHNB96",   # Amazon.fr
    "A1RKKUPIHCS9HS": "A1AT7YVPFBWXBL",   # Amazon.es
}


@dataclass
class Offerte:
    prezzo: float | None = None       # buy box IVA inclusa (prezzo + spedizione); senza buy box il più basso
    da_buy_box: bool = False
    n_offerte: int = 0
    n_fba: int = 0
    amazon_vende: bool = False


def leggi_offerte(client, marketplace_id: str, asins: list[str], pausa: float = 10.5) -> dict[str, Offerte]:
    """getItemOffersBatch, 20 ASIN a chiamata, 0,1 richieste al secondo."""
    out: dict[str, Offerte] = {}
    amazon = AMAZON_VENDITORE.get(marketplace_id)
    for i in range(0, len(asins), 20):
        lotto = asins[i:i + 20]
        corpo = {"requests": [{"uri": f"/products/pricing/v0/items/{a}/offers", "method": "GET",
                               "MarketplaceId": marketplace_id, "ItemCondition": "New",
                               "CustomerType": "Consumer"} for a in lotto]}
        risposta = client.post("/batches/products/pricing/v0/itemOffers", json_body=corpo)
        for voce in risposta.get("responses") or []:
            corpo_v = voce.get("body") or {}
            dati = corpo_v.get("payload") or {}
            asin = dati.get("ASIN") or (voce.get("request") or {}).get("Asin")
            if not asin:
                continue
            out[asin] = _offerte_da(dati, amazon)
        if pausa and i + 20 < len(asins):
            time.sleep(pausa)
    return out


def _prezzo(o: dict) -> float:
    return float((o.get("ListingPrice") or {}).get("Amount") or 0) + float((o.get("Shipping") or {}).get("Amount") or 0)


def _offerte_da(dati: dict, amazon: str | None) -> Offerte:
    offerte = dati.get("Offers") or []
    sommario = dati.get("Summary") or {}
    o = Offerte(n_offerte=int(sommario.get("TotalOfferCount") or len(offerte)),
                n_fba=sum(1 for x in offerte if x.get("IsFulfilledByAmazon")),
                amazon_vende=bool(amazon) and any(x.get("SellerId") == amazon for x in offerte))
    vincente = next((x for x in offerte if x.get("IsBuyBoxWinner")), None)
    if vincente:
        o.prezzo, o.da_buy_box = round(_prezzo(vincente), 2), True
    elif offerte:
        o.prezzo = round(min(_prezzo(x) for x in offerte), 2)
    return o


@dataclass
class Commissioni:
    commissione: float | None = None   # commissione di vendita €
    logistica: float | None = None     # tariffa FBA € (solo FBA)
    errore: str = ""


def stima_commissioni(client, marketplace_id: str, asin: str, prezzo: float, fba: bool) -> Commissioni:
    """Fees API sul prezzo IVA inclusa. Sulle schede senza misure la tariffa
    FBA non arriva (InvalidParameterValue): la riga lo dice, nessun gemello
    inventato (listino Cambioni, 25/09/2026)."""
    corpo = {"FeesEstimateRequest": {
        "MarketplaceId": marketplace_id, "IsAmazonFulfilled": fba, "Identifier": asin,
        "PriceToEstimateFees": {"ListingPrice": {"CurrencyCode": "EUR", "Amount": prezzo}}}}
    try:
        r = client.post(f"/products/fees/v0/items/{asin}/feesEstimate", json_body=corpo)
    except SpApiError as e:
        return Commissioni(errore=str(e)[:200])
    risultato = (r.get("payload") or {}).get("FeesEstimateResult") or {}
    if risultato.get("Status") != "Success":
        return Commissioni(errore=((risultato.get("Error") or {}).get("Message") or "stima non riuscita")[:200])
    voci = (risultato.get("FeesEstimate") or {}).get("FeeDetailList") or []
    c = Commissioni()
    for v in voci:
        importo = float((v.get("FinalFee") or v.get("FeeAmount") or {}).get("Amount") or 0)
        if v.get("FeeType") == "ReferralFee":
            c.commissione = importo
        elif v.get("FeeType") == "FBAFees":
            c.logistica = importo
        elif v.get("FeeType") == "VariableClosingFee":
            c.commissione = (c.commissione or 0) + importo
    if fba and c.logistica is None:
        c.errore = "tariffa FBA non calcolata da Amazon (scheda senza misure?)"
    return c


@dataclass
class Restrizioni:
    blocchi: list[str] = field(default_factory=list)   # motivi, vuoto = nessuna restrizione
    approvazione: bool = False                          # APPROVAL_REQUIRED (sbloccabile)


def leggi_restrizioni(client, seller_id: str, marketplace_id: str, asin: str) -> Restrizioni:
    r = client.get("/listings/2021-08-01/restrictions",
                   params={"asin": asin, "sellerId": seller_id, "marketplaceIds": marketplace_id,
                           "conditionType": "new_new"})
    out = Restrizioni()
    for voce in r.get("restrictions") or []:
        for motivo in voce.get("reasons") or []:
            codice = motivo.get("reasonCode", "")
            out.blocchi.append(codice or motivo.get("message", "")[:80])
            if codice == "APPROVAL_REQUIRED":
                out.approvazione = True
    return out


def prova_offerta(client, seller_id: str, marketplace_id: str, asin: str, product_type: str,
                  prezzo: float) -> tuple[str, list[str]]:
    """PUT con mode=VALIDATION_PREVIEW: nulla viene scritto. È il solo controllo
    che vede l'errore 5886/5885 (ASIN generico soggetto a restrizioni), che
    getListingsRestrictions non segnala (wiki, 05/10/2026). SKU di prova
    fittizio: non resta niente sull'account."""
    corpo = {"productType": product_type or "PRODUCT", "requirements": "LISTING_OFFER_ONLY",
             "attributes": {
                 "merchant_suggested_asin": [{"value": asin, "marketplace_id": marketplace_id}],
                 "condition_type": [{"value": "new_new", "marketplace_id": marketplace_id}],
                 "purchasable_offer": [{"currency": "EUR", "marketplace_id": marketplace_id,
                                        "our_price": [{"schedule": [{"value_with_tax": prezzo}]}]}],
                 "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": 1}]}}
    r = client.put(percorso_listing(seller_id, f"PROVA-LISTINO-{asin}"), json_body=corpo,
                   params={"marketplaceIds": marketplace_id, "mode": "VALIDATION_PREVIEW"})
    errori = [f"{i.get('code')} {i.get('message', '')[:90]}" for i in r.get("issues") or []
              if i.get("severity") == "ERROR"]
    return r.get("status", "INVALID"), errori
