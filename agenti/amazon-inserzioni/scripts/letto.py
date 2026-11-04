from __future__ import annotations
from dataclasses import dataclass
from spapi_client import percorso_listing


@dataclass
class StatoAttuale:
    sku: str
    product_type: str
    attributi: dict


def leggi(client, seller_id: str, marketplace_id: str, sku: str) -> StatoAttuale:
    risposta = client.get(
        percorso_listing(seller_id, sku),
        params={"marketplaceIds": marketplace_id, "includedData": "attributes,issues,summaries"},
    )
    riassunti = risposta.get("summaries") or [{}]
    product_type = riassunti[0].get("productType", "")
    return StatoAttuale(sku=sku, product_type=product_type, attributi=risposta.get("attributes", {}))
