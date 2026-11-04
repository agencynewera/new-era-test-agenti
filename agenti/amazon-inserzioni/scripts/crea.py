from __future__ import annotations
from dataclasses import dataclass
from modello import Prodotto
from spapi_client import SpApiError, percorso_listing
import registro

_MAPPA_CONDIZIONE = {"New": "new_new"}


def costruisci_payload(prodotto: Prodotto, marketplace_id: str, *, senza_offerta: bool = False) -> dict:
    """Ogni attributo Amazon è un elenco di oggetti valore, anche quando ce
    n'è uno solo. Non emette mai una chiave per un campo vuoto: farlo
    equivarrebbe a dichiarare un attributo vuoto invece di ometterlo.

    `senza_offerta=True` è per uno SKU padre di una relazione di variazione:
    non è vendibile da solo, quindi niente prezzo/quantità/condizione (li
    portano solo i figli). Vedi unifica.py."""
    attributi: dict = {}

    def _imposta(chiave, valore):
        if valore not in (None, "", [], {}):
            attributi[chiave] = valore

    _imposta("item_name", [{"value": prodotto.title, "marketplace_id": marketplace_id}])
    if prodotto.brand:
        _imposta("brand", [{"value": prodotto.brand, "marketplace_id": marketplace_id}])
    if not senza_offerta:
        # Il prezzo di vendita sta in purchasable_offer.our_price; list_price è
        # l'RRP, vuole value_with_tax (mai "value", err 99022) e Amazon lo
        # pretende comunque accanto all'offerta (smoke 2026-09-02): con un solo
        # prezzo nel modello va su entrambi, come nelle inserzioni vive.
        _imposta("purchasable_offer", [{
            "currency": "EUR",
            "marketplace_id": marketplace_id,
            "our_price": [{"schedule": [{"value_with_tax": prodotto.price}]}],
        }])
        _imposta("list_price", [{
            "value_with_tax": prodotto.price, "currency": "EUR", "marketplace_id": marketplace_id,
        }])
    if prodotto.description:
        _imposta("product_description", [{"value": prodotto.description, "marketplace_id": marketplace_id}])
    if prodotto.bullet_points:
        attributi["bullet_point"] = [{"value": b, "marketplace_id": marketplace_id} for b in prodotto.bullet_points]
    if prodotto.images:
        attributi["main_product_image_locator"] = [{"media_location": prodotto.images[0], "marketplace_id": marketplace_id}]
        for i, url in enumerate(prodotto.images[1:9], start=2):
            attributi[f"other_product_image_locator_{i - 1}"] = [{"media_location": url, "marketplace_id": marketplace_id}]
    if prodotto.ean:
        # L'enum dello schema è minuscolo ("ean"): "EAN" fa fallire il matching
        # GTIN e fa scattare a cascata merchant_suggested_asin (smoke 2026-09-01).
        _imposta("externally_assigned_product_identifier",
                  [{"type": "ean", "value": prodotto.ean, "marketplace_id": marketplace_id}])

    # Sempre presenti per un prodotto vendibile: quantità (anche 0,
    # out-of-stock ma listato) e condizione. Uno SKU padre (senza_offerta)
    # non ha un'offerta propria: li porta solo il figlio.
    if not senza_offerta:
        attributi["fulfillment_availability"] = [
            {"fulfillment_channel_code": "DEFAULT", "quantity": prodotto.quantity}
        ]
        attributi["condition_type"] = [
            {"value": _MAPPA_CONDIZIONE.get(prodotto.condition, "new_new"), "marketplace_id": marketplace_id}
        ]

    attributi.update(prodotto.attributes)  # attributi di categoria, già nel formato Amazon

    requirements = "LISTING_PRODUCT_ONLY" if senza_offerta else "LISTING"
    return {"productType": prodotto.product_type, "requirements": requirements, "attributes": attributi}


@dataclass
class EsitoCreazione:
    sku: str
    stato: str  # "ACCEPTED" | "INVALID" | "errore"
    issues: list[dict]


def pubblica_uno(client, seller_id: str, marketplace_id: str, prodotto: Prodotto, *,
                  senza_offerta: bool = False, anteprima: bool = False) -> EsitoCreazione:
    """`anteprima=True` chiede ad Amazon solo la validazione (mode=VALIDATION_PREVIEW,
    nulla scritto): fa emergere gli obbligatori condizionali della categoria, che
    lo schema nasconde negli allOf e la validazione locale non vede (smoke
    2026-09-01, PET_FOOD). Da fare prima del "vai", mai al posto del "vai"."""
    payload = costruisci_payload(prodotto, marketplace_id, senza_offerta=senza_offerta)
    params = {"marketplaceIds": marketplace_id}
    if anteprima:
        params["mode"] = "VALIDATION_PREVIEW"
    risposta = client.put(
        percorso_listing(seller_id, prodotto.sku),
        json_body=payload,
        params=params,
    )
    return EsitoCreazione(
        sku=risposta.get("sku", prodotto.sku),
        stato=risposta.get("status", "INVALID"),
        issues=risposta.get("issues", []),
    )


def pubblica_lotto(client, seller_id: str, marketplace_id: str, cliente: str,
                    prodotti: list[Prodotto]) -> list[EsitoCreazione]:
    """Un'inserzione alla volta. Un fallimento non ferma le altre: si
    registra e si continua (DESIGN §10)."""
    esiti = []
    for prodotto in prodotti:
        indice = registro.registra_tentativo_creazione(cliente, prodotto.sku)
        try:
            esito = pubblica_uno(client, seller_id, marketplace_id, prodotto)
        except SpApiError as errore:
            registro.registra_esito_creazione(cliente, indice, "errore", [{"message": str(errore)}])
            esiti.append(EsitoCreazione(prodotto.sku, "errore", [{"message": str(errore)}]))
            continue
        registro.registra_esito_creazione(cliente, indice, esito.stato, esito.issues)
        esiti.append(esito)
    return esiti
