"""Dizionario dei campi in italiano: da "prezzo 13,90" all'attributo Amazon
COMPLETO da scrivere.

Mariano parla di prezzo, quantita', tempo di gestione, titolo. Amazon vuole
`purchasable_offer[].our_price[].schedule[].value_with_tax` e un array intero
per ogni attributo (omettere un elemento = cancellarlo). Questo modulo e'
l'unico posto dove vive quella traduzione: ogni costruttore parte
dall'attributo VIVO e cambia solo cio' che Mariano ha chiesto.
"""
from __future__ import annotations
import copy
import unicodedata
from letto import StatoAttuale
from modifiche import sostituisci_elemento, aggiungi_elemento

# Lingua e valuta sono fatti del marketplace, non scelte: per quelli non in
# lista ci si ferma invece di indovinare.
_LINGUA = {
    "APJ6JRA9NG5V4": "it_IT", "A1PA6795UKMFR9": "de_DE", "A1RKKUPIHCS9HS": "es_ES",
    "A13V1IB3VIYZZH": "fr_FR", "A1F83G8C2ARO7P": "en_GB", "A1805IZSGTT6HS": "nl_NL",
    "A2NODRKZP88ZB9": "sv_SE", "A1C3SOZRARQ6R3": "pl_PL", "AMEN7PMS3EDWL": "fr_BE",
}
_VALUTA = {
    "APJ6JRA9NG5V4": "EUR", "A1PA6795UKMFR9": "EUR", "A1RKKUPIHCS9HS": "EUR",
    "A13V1IB3VIYZZH": "EUR", "A1805IZSGTT6HS": "EUR", "AMEN7PMS3EDWL": "EUR",
    "A1F83G8C2ARO7P": "GBP", "A2NODRKZP88ZB9": "SEK", "A1C3SOZRARQ6R3": "PLN",
}


def normalizza_nome(nome: str) -> str:
    senza_accenti = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return "_".join(senza_accenti.strip().lower().split())


# ----------------------------------------------------------- parsing ----

def _numero(valore, nome: str) -> float:
    try:
        return round(float(str(valore).replace(",", ".").strip()), 2)
    except ValueError:
        raise ValueError(f"{nome}: '{valore}' non e' un numero")


def _intero(valore, nome: str) -> int:
    testo = str(valore).strip()
    if not testo.isdigit():
        raise ValueError(f"{nome}: '{valore}' non e' un intero senza segno")
    return int(testo)


def _testo(valore, nome: str) -> str:
    testo = str(valore).strip()
    if not testo:
        raise ValueError(f"{nome}: testo vuoto")
    return testo


def _url(valore, nome: str) -> str:
    testo = _testo(valore, nome)
    if not testo.lower().startswith(("http://", "https://")):
        raise ValueError(f"{nome}: serve un URL pubblico http(s), non '{testo}'")
    return testo


def _contatto(valore, nome: str) -> str:
    # Amazon accetta solo e-mail o URL, mai un nome o un indirizzo postale.
    testo = _testo(valore, nome)
    if "@" not in testo and not testo.lower().startswith(("http://", "https://", "www.")):
        raise ValueError(f"{nome}: serve un'e-mail o un URL, non '{testo}'")
    return testo


def _si_no(valore, nome: str) -> bool:
    testo = normalizza_nome(str(valore))
    if testo in ("si", "true", "1"):
        return True
    if testo in ("no", "false", "0"):
        return False
    raise ValueError(f"{nome}: serve si' o no, non '{valore}'")


def _positivo(valore, nome: str) -> float:
    numero = _numero(valore, nome)
    if numero <= 0:
        raise ValueError(f"{nome}: '{valore}' deve essere maggiore di zero")
    return numero


def _tre_misure(valore, nome: str) -> tuple[float, float, float]:
    """"21x8x8" -> (21.0, 8.0, 8.0): lunghezza x larghezza x altezza, in cm."""
    parti = str(valore).lower().replace("×", "x").split("x")
    if len(parti) != 3:
        raise ValueError(f"{nome}: servono tre misure LxPxA in cm, non '{valore}'")
    return tuple(_positivo(p, nome) for p in parti)


def _lingua(attuale: list | None, marketplace_id: str) -> str:
    if attuale and attuale[0].get("language_tag"):
        return attuale[0]["language_tag"]
    if marketplace_id in _LINGUA:
        return _LINGUA[marketplace_id]
    raise ValueError(f"non conosco la lingua del marketplace {marketplace_id}: "
                     "nessun elemento vivo da cui leggerla")


def _valuta(stato: StatoAttuale, marketplace_id: str) -> str:
    for attributo in ("purchasable_offer", "list_price"):
        vivo = stato.attributi.get(attributo)
        if vivo and vivo[0].get("currency"):
            return vivo[0]["currency"]
    if marketplace_id in _VALUTA:
        return _VALUTA[marketplace_id]
    raise ValueError(f"non conosco la valuta del marketplace {marketplace_id}")


# ------------------------------------------------------- costruttori ----
# Ogni costruttore: (stato, valore_interpretato, marketplace_id) -> valore completo.

def _prezzo(stato, valore, mkt):
    attuale = stato.attributi.get("purchasable_offer")
    if attuale:
        nuovo = copy.deepcopy(attuale[0])
        # Alla creazione Amazon puo' accettare l'inserzione e scartare il prezzo,
        # lasciando l'offerta col solo audience (Tecno Cold DE, 2026-09-04): li'
        # our_price va costruito, non sostituito, o l'inserzione resta Incomplete.
        if _annidato(nuovo.get("our_price"), 0, "schedule", 0, "value_with_tax") is None:
            nuovo["our_price"] = [{"schedule": [{"value_with_tax": valore}]}]
            nuovo.setdefault("currency", _valuta(stato, mkt))
        else:
            nuovo["our_price"][0]["schedule"][0]["value_with_tax"] = valore
        return sostituisci_elemento(attuale, 0, nuovo)
    return [{"currency": _valuta(stato, mkt), "audience": "ALL",
             "our_price": [{"schedule": [{"value_with_tax": valore}]}],
             "marketplace_id": mkt}]


def _prezzo_listino(stato, valore, mkt):
    attuale = stato.attributi.get("list_price")
    if attuale:
        nuovo = dict(attuale[0], value_with_tax=valore)
        return sostituisci_elemento(attuale, 0, nuovo)
    return [{"value_with_tax": valore, "currency": _valuta(stato, mkt), "marketplace_id": mkt}]


def _su_canale_default(stato, chiave: str, valore):
    # Un elemento per canale (AMAZON_EU, DEFAULT...): si tocca SOLO il
    # DEFAULT (FBM), che non e' sempre il primo. Le righe FBA restano intatte.
    attuale = stato.attributi.get("fulfillment_availability") or []
    for i, el in enumerate(attuale):
        if el.get("fulfillment_channel_code") == "DEFAULT":
            return sostituisci_elemento(attuale, i, dict(el, **{chiave: valore}))
    return aggiungi_elemento(attuale, {"fulfillment_channel_code": "DEFAULT", chiave: valore})


def _quantita(stato, valore, mkt):
    return _su_canale_default(stato, "quantity", valore)


def _tempo_di_gestione(stato, valore, mkt):
    return _su_canale_default(stato, "lead_time_to_ship_max_days", valore)


def _testo_singolo(attributo: str):
    def costruisci(stato, valore, mkt):
        attuale = stato.attributi.get(attributo)
        return [{"value": valore, "language_tag": _lingua(attuale, mkt), "marketplace_id": mkt}]
    return costruisci


def _punti(valore, nome: str) -> list[str]:
    punti = valore if isinstance(valore, list) else str(valore).split("|")
    punti = [str(p).strip() for p in punti if str(p).strip()]
    if not punti:
        raise ValueError(f"{nome}: nessun punto")
    return punti


def _bullet(stato, valore, mkt):
    lingua = _lingua(stato.attributi.get("bullet_point"), mkt)
    return [{"value": p, "language_tag": lingua, "marketplace_id": mkt} for p in valore]


def _immagine(stato, valore, mkt):
    return [{"media_location": valore, "marketplace_id": mkt}]


def _misure_confezione(stato, valore, mkt):
    lunghezza, larghezza, altezza = valore
    return [{"length": {"unit": "centimeters", "value": lunghezza},
             "width": {"unit": "centimeters", "value": larghezza},
             "height": {"unit": "centimeters", "value": altezza},
             "marketplace_id": mkt}]


def _peso_confezione(stato, valore, mkt):
    return [{"unit": "kilograms", "value": valore, "marketplace_id": mkt}]


def _gpsr_produttore(stato, valore, mkt):
    return [{"gpsr_manufacturer_email_address": valore, "marketplace_id": mkt}]


def _valore_semplice(stato, valore, mkt):
    return [{"value": valore, "marketplace_id": mkt}]


# nome italiano -> (attributo Amazon, interprete, costruttore)
_CAMPI = {
    "prezzo": ("purchasable_offer", _numero, _prezzo),
    "prezzo_listino": ("list_price", _numero, _prezzo_listino),
    "quantita": ("fulfillment_availability", _intero, _quantita),
    "tempo_di_gestione": ("fulfillment_availability", _intero, _tempo_di_gestione),
    "titolo": ("item_name", _testo, _testo_singolo("item_name")),
    "descrizione": ("product_description", _testo, _testo_singolo("product_description")),
    "parole_chiave": ("generic_keyword", _testo, _testo_singolo("generic_keyword")),
    "colore": ("color", _testo, _testo_singolo("color")),
    "bullet": ("bullet_point", _punti, _bullet),
    "immagine_principale": ("main_product_image_locator", _url, _immagine),
    "immagine_campione": ("swatch_product_image_locator", _url, _immagine),
    "misure_confezione": ("item_package_dimensions", _tre_misure, _misure_confezione),
    "peso_confezione": ("item_package_weight", _positivo, _peso_confezione),
    "gpsr_produttore": ("gpsr_manufacturer_reference", _contatto, _gpsr_produttore),
    "gpsr_responsabile_ue": ("dsa_responsible_party_address", _contatto, _valore_semplice),
    "gpsr_attestazione": ("gpsr_safety_attestation", _si_no, _valore_semplice),
    "avvertenze": ("safety_warning", _testo, _testo_singolo("safety_warning")),
}
for _n in range(2, 10):
    _CAMPI[f"immagine_{_n}"] = (f"other_product_image_locator_{_n - 1}", _url, _immagine)


def _annidato(valore, *chiavi):
    for k in chiavi:
        try:
            valore = valore[k]
        except (KeyError, IndexError, TypeError):
            return None
    return valore


def _misure_lette(attributi: dict):
    # Dal vivo i valori arrivano come stringhe ("21.0") e l'unità può essere "CM":
    # si leggono i numeri, l'unità non si converte.
    el = _annidato(attributi.get("item_package_dimensions"), 0)
    if not el:
        return None
    return tuple(float(_annidato(el, k, "value")) for k in ("length", "width", "height"))


def _peso_letto(attributi: dict):
    valore = _annidato(attributi.get("item_package_weight"), 0, "value")
    return None if valore is None else float(valore)


def _canale_default(attributi: dict) -> dict:
    for el in attributi.get("fulfillment_availability") or []:
        if el.get("fulfillment_channel_code") == "DEFAULT":
            return el
    return {}


# nome italiano -> come si legge il valore attuale dagli attributi vivi
_LETTORI = {
    "prezzo": lambda a: _annidato(a.get("purchasable_offer"), 0, "our_price", 0, "schedule", 0, "value_with_tax"),
    "prezzo_listino": lambda a: _annidato(a.get("list_price"), 0, "value_with_tax"),
    "quantita": lambda a: _canale_default(a).get("quantity"),
    "tempo_di_gestione": lambda a: _canale_default(a).get("lead_time_to_ship_max_days"),
    "titolo": lambda a: _annidato(a.get("item_name"), 0, "value"),
    "descrizione": lambda a: _annidato(a.get("product_description"), 0, "value"),
    "parole_chiave": lambda a: _annidato(a.get("generic_keyword"), 0, "value"),
    "colore": lambda a: _annidato(a.get("color"), 0, "value"),
    "bullet": lambda a: [b.get("value") for b in a.get("bullet_point") or []] or None,
    "immagine_principale": lambda a: _annidato(a.get("main_product_image_locator"), 0, "media_location"),
    "immagine_campione": lambda a: _annidato(a.get("swatch_product_image_locator"), 0, "media_location"),
    "misure_confezione": _misure_lette,
    "peso_confezione": _peso_letto,
    "gpsr_produttore": lambda a: _annidato(a.get("gpsr_manufacturer_reference"), 0, "gpsr_manufacturer_email_address"),
    "gpsr_responsabile_ue": lambda a: _annidato(a.get("dsa_responsible_party_address"), 0, "value"),
    "gpsr_attestazione": lambda a: _annidato(a.get("gpsr_safety_attestation"), 0, "value"),
    "avvertenze": lambda a: _annidato(a.get("safety_warning"), 0, "value"),
}
for _n in range(2, 10):
    _LETTORI[f"immagine_{_n}"] = (lambda k: (lambda a: _annidato(a.get(k), 0, "media_location")))(
        f"other_product_image_locator_{_n - 1}")


def nomi() -> list[str]:
    return list(_CAMPI)


def _chiave(nome: str) -> str:
    chiave = normalizza_nome(nome)
    if chiave not in _CAMPI:
        raise ValueError(f"campo '{nome}' sconosciuto. Campi validi: {', '.join(_CAMPI)}")
    return chiave


def interpreta(nome: str, valore):
    """Il valore normalizzato come lo userebbe proponi ("13,90" -> 13.9)."""
    chiave = _chiave(nome)
    return _CAMPI[chiave][1](valore, chiave)


def valore_attuale(nome: str, stato: StatoAttuale):
    """Il valore del campo in italiano letto dall'inserzione viva, o None."""
    return _LETTORI[_chiave(nome)](stato.attributi)


def proponi(nome: str, valore, stato: StatoAttuale, marketplace_id: str) -> tuple[str, object]:
    """(attributo Amazon, valore completo nuovo) pronto per modifiche.componi."""
    chiave = _chiave(nome)
    attributo, _, costruisci = _CAMPI[chiave]
    return attributo, costruisci(stato, interpreta(chiave, valore), marketplace_id)
