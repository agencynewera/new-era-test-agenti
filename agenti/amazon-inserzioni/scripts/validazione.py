from __future__ import annotations
from dataclasses import dataclass
from modello import Prodotto

_CAMPI_BASE_OBBLIGATORI = ("sku", "title", "price", "product_type", "images")

# Attributi che Amazon elenca in schema["required"] ma che il modello porta
# come campi base (crea.costruisci_payload li traduce): cercarli solo in
# prodotto.attributes dava falsi rossi a dati completi (smoke 2026-09-01).
_CAMPO_BASE_PER_ATTRIBUTO = {
    "item_name": "title",
    "brand": "brand",
    "bullet_point": "bullet_points",
    "product_description": "description",
}


@dataclass
class Rilievo:
    prodotto: Prodotto
    semaforo: str  # "verde" | "giallo" | "rosso"
    mancanti_obbligatori: list[str]
    mancanti_opzionali: list[str]


def valuta(prodotto: Prodotto, schema: dict) -> Rilievo:
    """Mai un valore inventato per un campo mancante: si segnala e basta
    (DESIGN §3.1, §9). `quantity` non è nell'elenco: è sempre presente (campo
    obbligatorio del dataclass) e 0 è un valore legittimo — out-of-stock ma
    listato, come in crea.costruisci_payload — non un campo mancante."""
    mancanti_base = [c for c in _CAMPI_BASE_OBBLIGATORI if not getattr(prodotto, c)]
    attributi_richiesti = schema.get("required", [])
    mancanti_categoria = [
        a for a in attributi_richiesti
        if a not in prodotto.attributes
        and not getattr(prodotto, _CAMPO_BASE_PER_ATTRIBUTO.get(a, ""), None)
    ]
    mancanti_obbligatori = mancanti_base + mancanti_categoria

    mancanti_opzionali = []
    if not prodotto.bullet_points:
        mancanti_opzionali.append("bullet_points")
    if not prodotto.description:
        mancanti_opzionali.append("description")
    if len(prodotto.images) < 2:
        mancanti_opzionali.append("almeno una seconda immagine")

    if mancanti_obbligatori:
        semaforo = "rosso"
    elif mancanti_opzionali:
        semaforo = "giallo"
    else:
        semaforo = "verde"
    return Rilievo(prodotto, semaforo, mancanti_obbligatori, mancanti_opzionali)


def valuta_tutti(prodotti: list[Prodotto], schema: dict) -> list[Rilievo]:
    return [valuta(p, schema) for p in prodotti]
