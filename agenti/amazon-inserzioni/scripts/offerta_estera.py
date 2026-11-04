"""Offerta su una scheda gia' presente a catalogo in un altro marketplace.

Caso d'uso (Tecno Cold, 2026-09-23): il venditore vende su IT; su FR ed ES
Amazon ha gia' creato da sola le schede localizzate di meta' degli ASIN, ma
il venditore non ha nessuna offerta. Per metterle online basta un PUT con
`requirements=LISTING_OFFER_ONLY`: ASIN della scheda, condizione, prezzo e
disponibilita'. Niente testo (lo porta la scheda), niente EAN (la scheda la
sceglie l'ASIN), niente list_price (in questo modo Amazon lo ignora con
l'avviso 90000900, sonda del 23/09). Forma verificata VALID su FR ed ES per
SODA_MAKER, KITCHEN, HVAC_AIR_FILTER, ELECTRONIC_COMPONENT_FAN,
POTABLE_WATER_FILTER, REFRIGERATION_APPLIANCE.

Prezzo e disponibilita' vengono dal listing di origine (riga_da_listing):
mai inventati, mai un default. Cancello: anteprima -> "vai" per lotto, un
marketplace alla volta, come ogni scrittura di questa skill.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from crea import EsitoCreazione
from spapi_client import SpApiError, percorso_listing
import registro
from campi import _VALUTA


@dataclass(frozen=True)
class RigaOfferta:
    sku: str
    product_type: str
    asin: str          # la scheda di destinazione, a catalogo su quel marketplace
    prezzo: float      # IVA inclusa, nella valuta del marketplace di destinazione
    quantita: int      # canale DEFAULT (spedizione del venditore)


def payload_offerta(riga: RigaOfferta, marketplace_id: str, condizione: str = "new_new") -> dict:
    if not riga.product_type:
        raise ValueError(f"{riga.sku}: product_type mancante")
    if not riga.asin:
        raise ValueError(f"{riga.sku}: ASIN della scheda mancante")
    if riga.prezzo is None or riga.prezzo <= 0:
        raise ValueError(f"{riga.sku}: prezzo mancante o non positivo ({riga.prezzo})")
    if riga.quantita is None or riga.quantita < 0:
        raise ValueError(f"{riga.sku}: quantita' mancante o negativa ({riga.quantita})")
    valuta = _VALUTA.get(marketplace_id)
    if not valuta:
        raise ValueError(f"{riga.sku}: valuta del marketplace {marketplace_id} sconosciuta")
    return {
        "productType": riga.product_type,
        "requirements": "LISTING_OFFER_ONLY",
        "attributes": {
            "merchant_suggested_asin": [{"value": riga.asin, "marketplace_id": marketplace_id}],
            "condition_type": [{"value": condizione, "marketplace_id": marketplace_id}],
            "purchasable_offer": [{
                "currency": valuta, "marketplace_id": marketplace_id,
                "our_price": [{"schedule": [{"value_with_tax": riga.prezzo}]}],
            }],
            "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": riga.quantita}],
        },
    }


def converti_riga(riga: RigaOfferta, cambio: float) -> RigaOfferta:
    """Prezzo di origine (EUR) portato in un'altra valuta al cambio dato,
    arrotondato all'unita' (Tecno Cold IT -> SE in corone, 14/10/2026)."""
    if not cambio or cambio <= 0:
        raise ValueError(f"{riga.sku}: cambio mancante o non positivo ({cambio})")
    return replace(riga, prezzo=float(round(riga.prezzo * cambio)))


def riga_da_listing(sku: str, product_type: str, attributi: dict, asin_destinazione: str) -> RigaOfferta:
    """Prezzo (our_price, IVA inclusa) e disponibilita' del canale DEFAULT
    letti dal listing di origine. Se uno dei due manca, la riga non si
    costruisce: un'offerta senza prezzo vero non si propone."""
    # Solo l'offerta per tutti i clienti (audience ALL, o senza audience). Il
    # listing puo' portare anche l'offerta B2B con gli sconti quantita': non e'
    # il prezzo al pubblico e non si copia (Tecno Cold IT, 23/09/2026).
    prezzo = None
    for po in attributi.get("purchasable_offer") or []:
        if po.get("audience", "ALL") != "ALL":
            continue
        for op in po.get("our_price") or []:
            for s in op.get("schedule") or []:
                if s.get("value_with_tax") is not None and prezzo is None:
                    prezzo = s["value_with_tax"]
    if prezzo is None:
        raise ValueError(f"{sku}: prezzo di origine (per tutti i clienti) mancante")
    quantita = None
    for fa in attributi.get("fulfillment_availability") or []:
        if fa.get("fulfillment_channel_code") == "DEFAULT":
            quantita = fa.get("quantity")
    if quantita is None:
        raise ValueError(f"{sku}: disponibilita' DEFAULT di origine mancante")
    return RigaOfferta(sku=sku, product_type=product_type, asin=asin_destinazione,
                       prezzo=float(prezzo), quantita=int(quantita))


def pianifica(voci_origine: list[dict], per_asin: set[str], per_ean: dict[str, list[str]],
              ean_doppi: set[str]) -> tuple[list[RigaOfferta], dict[str, str]]:
    """Da ogni SKU di origine ({sku, asin, product_type, parentage, ean,
    attributi}) decide se sul marketplace di destinazione esiste gia' una
    scheda su cui appoggiare l'offerta: prima lo stesso ASIN, poi l'EAN
    (Amazon puo' aver dato alla stessa scheda un ASIN diverso; l'ASIN che si
    manda resta comunque quello di origine, vedi sotto). Un EAN usato
    da piu' prodotti su IT non si segue: sceglierebbe la scheda sbagliata.
    Padri, schede assenti e prezzi mancanti finiscono nei saltati con il
    motivo, mai in silenzio."""
    righe: list[RigaOfferta] = []
    saltati: dict[str, str] = {}
    for v in voci_origine:
        sku = v["sku"]
        if v.get("parentage") == "parent":
            saltati[sku] = "padre: si crea a parte, senza offerta"
            continue
        ean = v.get("ean")
        # L'ASIN da suggerire e' SEMPRE quello di origine: uno SKU deve puntare
        # allo stesso ASIN su tutti i marketplace attivi (errore 101077, sonda
        # del 23/09 su 9 bombole CO2 il cui EAN su FR/ES sta su un ASIN diverso).
        # Il catalogo di destinazione serve solo a sapere se una scheda c'e' gia'
        # (stesso ASIN, o stesso EAN sotto un altro ASIN: sara' Amazon a estendere).
        if v.get("asin") in per_asin:
            pass
        elif ean and ean in ean_doppi:
            saltati[sku] = "EAN usato da piu' prodotti su IT e scheda dell'ASIN assente: si crea nel lotto 2"
            continue
        elif ean and per_ean.get(ean):
            # La scheda c'e' ma sotto un altro ASIN: con l'ASIN di origine Amazon
            # pretende il titolo (90220), cioe' una creazione con testo: lotto 2.
            saltati[sku] = f"scheda presente solo sotto un altro ASIN ({per_ean[ean][0]}): serve il testo, si crea nel lotto 2"
            continue
        else:
            saltati[sku] = "scheda assente a catalogo: si crea nel lotto 2"
            continue
        try:
            righe.append(riga_da_listing(sku, v.get("product_type", ""), v.get("attributi") or {}, v["asin"]))
        except ValueError as e:
            saltati[sku] = str(e).split(": ", 1)[1]
    return righe, saltati


def pubblica_offerta(client, seller_id: str, marketplace_id: str, riga: RigaOfferta, *,
                     anteprima: bool = False) -> EsitoCreazione:
    """`anteprima=True` = mode=VALIDATION_PREVIEW: Amazon valida senza scrivere
    e senza registro. Il PUT vero lo chiama solo pubblica_lotto_offerte, che
    registra ogni tentativo."""
    params = {"marketplaceIds": marketplace_id}
    if anteprima:
        params["mode"] = "VALIDATION_PREVIEW"
    risposta = client.put(percorso_listing(seller_id, riga.sku),
                          json_body=payload_offerta(riga, marketplace_id), params=params)
    return EsitoCreazione(sku=risposta.get("sku", riga.sku), stato=risposta.get("status", "INVALID"),
                          issues=risposta.get("issues", []))


def pubblica_lotto_offerte(client, seller_id: str, marketplace_id: str, cliente: str,
                           righe: list[RigaOfferta]) -> list[EsitoCreazione]:
    """Un'offerta alla volta, nel registro `pubblicazioni` come ogni creazione.
    Un errore di trasporto su una riga non ferma le altre: si registra e si
    continua (DESIGN §10)."""
    esiti: list[EsitoCreazione] = []
    for riga in righe:
        indice = registro.registra_tentativo_creazione(cliente, riga.sku)
        try:
            esito = pubblica_offerta(client, seller_id, marketplace_id, riga)
        except SpApiError as errore:
            registro.registra_esito_creazione(cliente, indice, "errore", [{"message": str(errore)}])
            esiti.append(EsitoCreazione(riga.sku, "errore", [{"message": str(errore)}]))
            continue
        registro.registra_esito_creazione(cliente, indice, esito.stato, esito.issues)
        esiti.append(esito)
    return esiti


def cerca_per_identificativi(client, marketplace_id: str, identificativi: list[str], tipo: str,
                             *, pausa: float = 0.6) -> dict[str, dict]:
    """Catalog Items 2022-04-01 per lotti di 20 identificativi (ASIN o EAN),
    **sempre** con pageSize=20 e seguendo pagination.nextToken: col default
    (10 per pagina) meta' dei risultati sparisce e un ASIN presente sembra
    assente (misurato il 23/09/2026 su Tecno Cold FR). Ritorna {asin: item}."""
    import time
    out: dict[str, dict] = {}
    for i in range(0, len(identificativi), 20):
        lotto = identificativi[i:i + 20]
        token = None
        while True:
            params = {"identifiers": ",".join(lotto), "identifiersType": tipo,
                      "marketplaceIds": marketplace_id, "pageSize": 20,
                      "includedData": "summaries,relationships,identifiers"}
            if token:
                params["pageToken"] = token
            risposta = client.get("/catalog/2022-04-01/items", params=params)
            for item in risposta.get("items") or []:
                out[item["asin"]] = item
            token = (risposta.get("pagination") or {}).get("nextToken")
            if not token:
                break
        if pausa:
            time.sleep(pausa)
    return out


def ean_delle_voci(voci: dict[str, dict]) -> dict[str, list[str]]:
    """{ean: [asin, ...]} dalle voci di catalogo: un EAN puo' stare su piu'
    schede (Tecno Cold ne ha tre cosi'), e chi pianifica deve vederlo."""
    out: dict[str, list[str]] = {}
    for asin, item in voci.items():
        for blocco in item.get("identifiers") or []:
            for idd in blocco.get("identifiers") or []:
                if idd.get("identifierType") == "EAN":
                    out.setdefault(idd["identifier"], []).append(asin)
    return out
