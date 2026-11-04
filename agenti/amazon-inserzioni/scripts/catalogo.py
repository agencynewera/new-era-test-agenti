from __future__ import annotations
import json
import os
import requests
from spapi_client import _USER_AGENT


class CatalogoError(Exception):
    """Errore nel recuperare o leggere lo schema di una categoria Amazon."""


def cerca_product_type(client, marketplace_id: str, parola_chiave: str) -> list[str]:
    body = client.get("/definitions/2020-09-01/productTypes", params={
        "marketplaceIds": marketplace_id, "keywords": parola_chiave,
    })
    return [pt["name"] for pt in body.get("productTypes", [])]


def schema_categoria(client, marketplace_id: str, seller_id: str, product_type: str,
                      cache_dir: str | None = None) -> dict:
    cache_dir = cache_dir or os.path.expanduser("~/.config/newera-amazon/schema")
    cache_path = os.path.join(cache_dir, f"{product_type}-{marketplace_id}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    definizione = client.get(f"/definitions/2020-09-01/productTypes/{product_type}", params={
        "marketplaceIds": marketplace_id, "sellerId": seller_id, "requirements": "LISTING",
    })
    link = definizione.get("schema", {}).get("link", {}).get("resource")
    if not link:
        raise CatalogoError(f"nessuno schema disponibile per {product_type} su {marketplace_id}")

    r = requests.get(link, headers={"User-Agent": _USER_AGENT}, timeout=60)
    if r.status_code >= 400:
        raise CatalogoError(f"download schema {product_type} -> {r.status_code}")
    schema = r.json()

    os.makedirs(cache_dir, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(schema, f)
    return schema
