"""I modelli d'imballaggio del cliente, letti dai piani spediti (DESIGN §14).
Regola di Mariano (2026-09-05): prima di proporre una scatola si guarda che
modelli il cliente ha davvero creato nel suo account. Indicizzati per modello
e non per SKU come in scatole.py, perché è il modello a portare con sé quante
volte è stato usato — il dato che decide quale proporre."""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime

from scatole import _piani_spediti, _scatole_del_piano
from spapi_client import SpApiError

_AUTOMATICO = re.compile(r"^P\d+ - B\d+$")


def e_automatico(nome: str | None) -> bool:
    """`P1 - B1`: il nome che Amazon si inventa quando la scatola arriva senza
    modello. Vale meno di un nome scritto da una persona."""
    return not nome or bool(_AUTOMATICO.match(nome.strip()))


@dataclass(frozen=True)
class PianoSpedito:
    plan_id: str
    data: str
    scatole: tuple[dict, ...]


@dataclass(frozen=True)
class Modello:
    nome: str
    sku: str
    pezzi: int
    lunghezza_cm: float
    larghezza_cm: float
    altezza_cm: float
    peso_kg: float
    scatole: int
    piani: int
    ultima_data: str
    automatico: bool


def leggi_piani(client, adesso: datetime, mesi: int = 12) -> tuple[list[PianoSpedito], list[str]]:
    """Un giro solo delle API per tutto ciò che serve allo stock: i modelli
    (qui) e le spedizioni ricorrenti (ricorrenza.py). Un piano le cui scatole
    non si leggono viene saltato e nominato."""
    piani, saltati = [], []
    for p in _piani_spediti(client, adesso, mesi):
        pid = p["inboundPlanId"]
        try:
            scatole = _scatole_del_piano(client, pid)
        except SpApiError as e:
            saltati.append(f"{pid}: {e}")
            continue
        piani.append(PianoSpedito(pid, p["createdAt"][:10], tuple(scatole)))
    return piani, saltati


def _colli_mono_sku(piano: PianoSpedito):
    for b in piano.scatole:
        items = b.get("items") or []
        d, w = b.get("dimensions") or {}, b.get("weight") or {}
        if (b.get("contentInformationSource") != "BOX_CONTENT_PROVIDED"
                or len(items) != 1 or not d or not w):
            continue
        yield b, items[0], d, w


def modelli_per_sku(piani: list[PianoSpedito]) -> dict[str, list[Modello]]:
    """Per ogni SKU i suoi modelli, dal più adatto da proporre al meno: prima
    quelli con un nome vero, poi i più recenti, poi i più usati."""
    conta: dict[tuple, dict] = {}
    for piano in piani:
        for b, it, d, w in _colli_mono_sku(piano):
            grezzo = b.get("templateName")
            nome = (grezzo or "").strip() or "—"
            chiave = (it["msku"], nome, int(it["quantity"]), float(d["length"]),
                      float(d["width"]), float(d["height"]), float(w["value"]))
            voce = conta.setdefault(chiave, {"scatole": 0, "piani": set(), "ultima": "",
                                              "automatico": e_automatico(grezzo)})
            voce["scatole"] += int(b.get("quantity") or 1)
            voce["piani"].add(piano.plan_id)
            voce["ultima"] = max(voce["ultima"], piano.data)
    per_sku: dict[str, list[Modello]] = {}
    for (sku, nome, pezzi, lung, larg, alt, peso), voce in conta.items():
        per_sku.setdefault(sku, []).append(Modello(
            nome=nome, sku=sku, pezzi=pezzi, lunghezza_cm=lung, larghezza_cm=larg,
            altezza_cm=alt, peso_kg=peso, scatole=voce["scatole"], piani=len(voce["piani"]),
            ultima_data=voce["ultima"], automatico=voce["automatico"]))
    for lista in per_sku.values():
        lista.sort(key=lambda m: (m.automatico, _negativo(m.ultima_data), -m.scatole))
    return per_sku


def _negativo(data: str) -> tuple:
    """Ordine decrescente su una data ISO senza convertirla: le cifre invertite."""
    return tuple(-ord(c) for c in data)


def a_colli_pieni(quantita: int, pezzi_per_collo: int) -> int:
    """Si spedisce a colli interi: 35 pezzi su colli da 22 diventano 44. Un
    parziale costerebbe una pesata prima di poter usare il file."""
    if quantita <= 0:
        return 0
    return -(-quantita // pezzi_per_collo) * pezzi_per_collo


def istantanea_da(cliente: str, scelte: dict[str, Modello], adesso: datetime) -> dict:
    """Un'istantanea sintetica nella forma che `scatole.bozza_lotto` si aspetta,
    costruita dai modelli scelti invece che dall'ultima scatola vista. Così la
    bozza resta scritta dalla funzione già collaudata dal vivo."""
    return {
        "cliente": cliente,
        "letto_il": adesso.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mesi": 12, "piani_letti": 0, "piani_saltati": [], "colli_misti_esclusi": 0,
        "sku": {sku: {"pezzi": m.pezzi, "lunghezza_cm": m.lunghezza_cm,
                      "larghezza_cm": m.larghezza_cm, "altezza_cm": m.altezza_cm,
                      "peso_kg": m.peso_kg, "modello": m.nome, "asin": None,
                      "ultimo_piano": f"modello {m.nome}", "ultima_data": m.ultima_data, "varianti": []}
                for sku, m in scelte.items()},
    }


def il_piu_adatto(per_sku: dict[str, list[Modello]], sku: str) -> Modello:
    """Regola di Mariano (2026-10-14): affidata una spedizione, si parte dai
    modelli creati nell'account, che sono quelli che si usano quasi sempre.
    È il primo nell'ordine di `modelli_per_sku`: nome vero, più recente, più usato."""
    if not per_sku.get(sku):
        raise ValueError(f"nessun modello d'imballaggio per {sku} nei piani spediti: "
                         "la scatola di un articolo mai spedito la detti tu nel lotto.yaml")
    return per_sku[sku][0]


def scegli(per_sku: dict[str, list[Modello]], sku: str, nome: str) -> Modello:
    """Il modello chiamato per nome, come lo scrive Mariano nel comando: senza
    badare a maiuscole e spazi, come `clienti._chiave`."""
    voluto = " ".join(nome.split()).casefold()
    for m in per_sku.get(sku, []):
        if " ".join(m.nome.split()).casefold() == voluto:
            return m
    noti = ", ".join(sorted({m.nome for m in per_sku.get(sku, [])})) or "nessuno"
    raise ValueError(f"nessun modello {nome!r} per {sku}: modelli noti {noti}")
