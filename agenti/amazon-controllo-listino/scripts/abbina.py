"""Da riga di listino a scheda Amazon (ASIN). Sola lettura.

Con l'EAN l'abbinamento è sicuro se l'EAN porta a una scheda sola. Senza EAN
si cerca per parole e si sceglie il candidato più simile, ma resta «da
confermare»: sul listino Cambioni (25/09/2026) un primo controllo lasciava
passare errori grossolani e il secondo, avversario, ne scartava ~70%.
Nessuna riga abbinata per parole diventa verde senza conferma."""
from __future__ import annotations
import re
import time
import unicodedata
from dataclasses import dataclass, field

_CATALOGO = "/catalog/2022-04-01/items"
_DATI = "summaries,salesRanks,attributes,productTypes,identifiers"


@dataclass
class Scheda:
    asin: str
    titolo: str = ""
    marca: str = ""
    classifica: int | None = None
    categoria: str = ""
    product_type: str = ""
    pezzi: int = 1
    ean: list[str] = field(default_factory=list)


@dataclass
class Abbinamento:
    scheda: Scheda | None
    confidenza: str            # "EAN", "alta", "media", "bassa", "" se non trovato
    nota: str = ""

    @property
    def sicuro(self) -> bool:
        return self.confidenza == "EAN"


def _valore(attributi: dict, nome: str):
    voce = (attributi.get(nome) or [{}])[0]
    return voce.get("value")


def scheda_da_item(item: dict) -> Scheda:
    """La classifica è quella della categoria principale (displayGroupRanks):
    è il numero con cui si giudicava la vendibilità nel reselling Cambioni."""
    summ = (item.get("summaries") or [{}])[0]
    ranks = (item.get("salesRanks") or [{}])[0]
    gruppo = (ranks.get("displayGroupRanks") or ranks.get("classificationRanks") or [{}])[0]
    attributi = item.get("attributes") or {}
    pezzi = 1
    for nome in ("item_package_quantity", "number_of_items"):
        try:
            v = int(float(_valore(attributi, nome) or 0))
        except (TypeError, ValueError):
            v = 0
        if v > 1:
            pezzi = v
            break
    titolo = summ.get("itemName", "")
    if pezzi == 1:
        # Confezioni multiple scritte solo nel titolo («12 X 1 LT», «Lt. 5 4pz»):
        # senza, il costo di un pezzo sta contro il prezzo di dodici (collaudo 02/11).
        m = re.search(r"\b(\d{1,3})\s*(?:x\s|pz\b|pezzi\b|confezioni\b)", titolo.lower())
        if m and 2 <= int(m.group(1)) <= 100:
            pezzi = int(m.group(1))
    ean = [i["identifier"] for blocco in item.get("identifiers") or []
           for i in blocco.get("identifiers") or [] if i.get("identifierType") == "EAN"]
    return Scheda(item["asin"], titolo, summ.get("brand", ""),
                  gruppo.get("rank"), gruppo.get("title", ""),
                  ((item.get("productTypes") or [{}])[0]).get("productType", ""), pezzi, ean)


def cerca_ean(client, marketplace_id: str, eans: list[str], pausa: float = 0.6) -> dict[str, list[Scheda]]:
    """{ean: [schede]} a lotti di 20, con pageSize=20 (col default ne tornano
    10 e metà sembrano assenti: wiki, Dati dei concorrenti)."""
    out: dict[str, list[Scheda]] = {}
    for i in range(0, len(eans), 20):
        lotto = eans[i:i + 20]
        token = None
        while True:
            params = {"identifiers": ",".join(lotto), "identifiersType": "EAN",
                      "marketplaceIds": marketplace_id, "pageSize": 20, "includedData": _DATI}
            if token:
                params["pageToken"] = token
            risposta = client.get(_CATALOGO, params=params)
            for item in risposta.get("items") or []:
                scheda = scheda_da_item(item)
                for e in scheda.ean or []:
                    if e in lotto:
                        out.setdefault(e, []).append(scheda)
            token = (risposta.get("pagination") or {}).get("nextToken")
            if not token:
                break
        if pausa:
            time.sleep(pausa)
    return out


def _parole(testo: str) -> list[str]:
    testo = unicodedata.normalize("NFKD", testo).encode("ascii", "ignore").decode().lower()
    testo = re.sub(r"\([^)]*\)", " ", testo)        # (ADR ES,LQ) e simili: note di trasporto
    # Lettere e cifre separate: «125ml» del titolo vale «125 ML.» del listino.
    return [p for p in re.findall(r"[a-z]+|\d+", testo) if len(p) > 1 or p.isdigit()]


def _numeri(testo: str) -> set[str]:
    return {n.lstrip("0") or "0" for n in re.findall(r"\d+", testo)}


def parole_chiave(descrizione: str) -> str:
    return " ".join(_parole(descrizione))


_UNITA = {"ml": ("vol", 1), "cl": ("vol", 10), "l": ("vol", 1000), "lt": ("vol", 1000), "litri": ("vol", 1000),
          "litro": ("vol", 1000), "g": ("peso", 1), "gr": ("peso", 1), "grammi": ("peso", 1),
          "kg": ("peso", 1000), "mm": ("lung", 1), "cm": ("lung", 10), "m": ("lung", 1000), "mt": ("lung", 1000)}
_U = "|".join(sorted(_UNITA, key=len, reverse=True))
_DOPO = re.compile(rf"(\d+(?:[.,]\d+)?)\s*({_U})\b")          # 125 ML, 5kg
_PRIMA = re.compile(rf"\b({_U})\.?\s*(\d+(?:[.,]\d+)?)")       # LT. 1, KG. 5, GR.500 (stile listino)


def _formati(testo: str) -> dict[str, set[float]]:
    """Formati espressi con l'unità, ridotti a ml, grammi e millimetri."""
    testo = testo.lower()
    out: dict[str, set[float]] = {}
    coppie = [(n, u) for n, u in _DOPO.findall(testo)] + [(n, u) for u, n in _PRIMA.findall(testo)]
    for numero, unita in coppie:
        dim, fattore = _UNITA[unita]
        out.setdefault(dim, set()).add(round(float(numero.replace(",", ".")) * fattore, 1))
    return out


def punteggio(descrizione: str, titolo: str) -> float:
    """Quota delle parole del listino che ritornano nel titolo, pesate sulla
    lunghezza (manca «isopropilico» conta più di manca «lt»), anche come inizio
    di parola: «DEN.» vale «denaturato». Poi il formato: se listino e titolo
    dichiarano la stessa grandezza (volume, peso, lunghezza) con valori
    diversi, è un altro prodotto (150 ml contro 750 ml, collaudo del 02/11)."""
    voci = [p for p in _parole(descrizione) if not p.isdigit()]
    if not voci:
        return 0.0
    nel_titolo = _parole(titolo)
    peso = lambda p: min(len(p), 8)
    trovate = sum(peso(p) for p in voci
                  if any(t.startswith(p) or (len(t) > 2 and p.startswith(t)) for t in nel_titolo))
    quota = trovate / sum(peso(p) for p in voci)
    formati_l, formati_t = _formati(descrizione), _formati(titolo)
    if formati_l:
        for dim, valori in formati_l.items():
            if dim not in formati_t:
                quota -= 0.1
            elif not any(abs(v - w) <= 0.01 * v for v in valori for w in formati_t[dim]):
                return min(quota, 0.2)
    else:
        numeri = _numeri(descrizione)
        if numeri and not numeri & _numeri(titolo):
            quota -= 0.3
    return max(0.0, quota)


def scegli(descrizione: str, candidati: list[Scheda]) -> Abbinamento:
    if not candidati:
        return Abbinamento(None, "", "nessuna scheda trovata con la descrizione")
    voti = sorted(((punteggio(descrizione, s.titolo), -(s.classifica or 10**9), s) for s in candidati),
                  key=lambda t: (t[0], t[1]), reverse=True)
    voto, _, scheda = voti[0]
    if voto >= 0.85:
        confidenza = "alta"
    elif voto >= 0.6:
        confidenza = "media"
    elif voto >= 0.4:
        confidenza = "bassa"
    else:
        return Abbinamento(None, "", f"candidati troppo diversi (miglior somiglianza {voto:.0%})")
    return Abbinamento(scheda, confidenza, "abbinato per descrizione: da confermare")


def cerca_parole(client, marketplace_id: str, descrizione: str, marca: str = "") -> list[Scheda]:
    params = {"keywords": parole_chiave(f"{marca} {descrizione}"), "marketplaceIds": marketplace_id,
              "pageSize": 10, "includedData": _DATI}
    risposta = client.get(_CATALOGO, params=params)
    return [scheda_da_item(i) for i in risposta.get("items") or []]


def abbina_ean(ean: str, trovate: dict[str, list[Scheda]]) -> Abbinamento:
    schede = trovate.get(ean) or []
    if not schede:
        return Abbinamento(None, "", "EAN non presente su Amazon")
    if len(schede) > 1:
        # Un EAN su più schede: non si sceglie a caso (controllo della scheda idee).
        return Abbinamento(schede[0], "bassa",
                           f"EAN su {len(schede)} schede ({', '.join(s.asin for s in schede)}): da confermare")
    return Abbinamento(schede[0], "EAN")
