"""Cosa scrivere nel blocco Regulatory di un'inserzione, e quando non scrivere.

L'anagrafica di un cliente sta in `ebay-core/gpsr/<cliente>.yaml`: responsabile
UE, avvertenze di default, e la tabella marca -> produttore. La tabella e' per
MARCA e non per articolo perche' il produttore cambia con la marca, non con il
cliente; ed e' per marca e non unica perche' un cliente rivende decine di
fabbricanti diversi (Tecno Cold: 29 marche mappate su 144 inserzioni).

Le tre porte chiuse - marca sconosciuta, articolo senza marca, marca su cui il
catalogo si contraddice - esistono per la stessa ragione: un produttore
sbagliato in un campo di conformita' e' peggio di un campo vuoto, perche' il
campo vuoto si vede e quello sbagliato no.
"""
from __future__ import annotations
import os

NON_SONO_MARCHE = ("- senza marca/generico -", "senza marca/generico",
                   "senza marca", "generico", "unbranded", "")
CARTELLA = os.path.expanduser("~/.claude/skills/ebay-core/gpsr")


def anagrafica(cliente: str, cartella: str = None) -> dict:
    import yaml
    nome = cliente.strip().lower().replace(" ", "-")
    percorso = os.path.join(cartella or CARTELLA, "%s.yaml" % nome)
    if not os.path.exists(percorso):
        raise FileNotFoundError(
            "manca l'anagrafica GPSR di %s (%s): senza quella non si scrive "
            "niente, si tirerebbe a indovinare" % (cliente, percorso))
    with open(percorso) as f:
        return yaml.safe_load(f)


def _marca(stato) -> str:
    # "Marke" e' il nome della specifica su eBay.de (06/10/2026, Tecno Cold).
    aspetti = stato.aspetti or {}
    valori = aspetti.get("Marca") or aspetti.get("Marke") or aspetti.get("Brand") or []
    if isinstance(valori, str):
        return valori
    return valori[0] if valori else ""


def proposta_per(stato, dati: dict):
    """(proposta, motivo). proposta None = non si tocca, e il motivo lo dice.

    Cio' che il cliente ha gia' scritto ha la precedenza sulla tabella: le 17
    inserzioni parziali di Tecno Cold hanno un produttore messo a mano, spesso
    piu' preciso della riga per marca. Si riempie il vuoto, non si sovrascrive.
    """
    marca = _marca(stato)
    if marca.strip().lower() in NON_SONO_MARCHE:
        return None, ("articolo senza marca: il produttore non si deduce, "
                      "va deciso per articolo")
    produttori = dati.get("produttori") or {}
    ora = stato.regulatory or {}
    manca_produttore = not ora.get("produttore")
    if manca_produttore and marca not in produttori:
        return None, ("marca non in anagrafica (%s): il produttore non si "
                      "inventa" % marca)

    proposta = {
        "produttore": ora.get("produttore") or produttori.get(marca),
        "responsabili": ora.get("responsabili") or [dati["responsabile_ue"]],
        "pittogrammi": ora.get("pittogrammi") or list(dati.get("avvertenze_default") or []),
        "dichiarazioni": list(ora.get("dichiarazioni") or []),
    }
    adesso = {"produttore": ora.get("produttore"),
              "responsabili": list(ora.get("responsabili") or []),
              "pittogrammi": list(ora.get("pittogrammi") or []),
              "dichiarazioni": list(ora.get("dichiarazioni") or [])}
    if proposta == adesso:
        return None, "gia' completa"
    return proposta, ""
