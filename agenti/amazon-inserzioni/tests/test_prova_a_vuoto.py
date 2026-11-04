import json
import os
from estrattore_manuale import da_dati_manuali
from validazione import valuta
from crea import costruisci_payload

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_catena_creazione_fino_al_payload_esatto():
    with open(os.path.join(_FIXTURES, "schema_categoria_test.json")) as f:
        schema = json.load(f)

    prodotto = da_dati_manuali({
        "sku": "TEST-001", "title": "Tappeto yoga antiscivolo 183x61cm",
        "price": 29.90, "quantity": 50, "product_type": "EXERCISE_MAT",
        "brand": "New Era Test", "description": "Tappeto in TPE, 183x61cm",
        "bullet_points": ["Antiscivolo", "Ecologico"],
        "images": ["https://esempio.it/1.jpg", "https://esempio.it/2.jpg"],
        "attributes": {
            "item_name": [{"value": "Tappeto yoga antiscivolo 183x61cm", "marketplace_id": "APJ6JRA9NG5V4"}],
            "brand": [{"value": "New Era Test", "marketplace_id": "APJ6JRA9NG5V4"}],
            "colore": [{"value": "Blu", "marketplace_id": "APJ6JRA9NG5V4"}],
        },
    })

    rilievo = valuta(prodotto, schema)
    assert rilievo.semaforo == "verde", rilievo.mancanti_obbligatori

    payload = costruisci_payload(prodotto, marketplace_id="APJ6JRA9NG5V4")
    assert payload["productType"] == "EXERCISE_MAT"
    assert payload["attributes"]["item_name"][0]["value"] == "Tappeto yoga antiscivolo 183x61cm"
    assert payload["attributes"]["colore"][0]["value"] == "Blu"
    assert "purchasable_offer" in payload["attributes"]
    assert "list_price" in payload["attributes"]
