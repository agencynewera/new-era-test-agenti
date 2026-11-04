"""Scatole dalla storia (DESIGN §13): il collo standard di ogni SKU letto
dai piani SHIPPED del cliente, l'istantanea su disco, la tabella per Mariano,
la bozza di lotto.yaml e il confronto con un lotto dettato a mano."""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from clienti import _chiave
from registro import _nome_cliente
from spapi_client import SpApiError

_BASE = "/inbound/fba/2024-03-20/inboundPlans"
_MISURE = ("pezzi", "lunghezza_cm", "larghezza_cm", "altezza_cm", "peso_kg")
TOLLERANZA_PESO = 0.10


def _istante(testo: str) -> datetime:
    return datetime.fromisoformat(testo.replace("Z", "+00:00"))


def _piani_spediti(client, adesso: datetime, mesi: int) -> list[dict]:
    """`listInboundPlans?status=SHIPPED`, tutte le pagine (max 30 per pagina).
    Senza `status` Amazon torna solo gli ACTIVE, che sono bozze vuote; l'ordine
    della lista non è garantito, quindi il filtro per data vale su ogni voce."""
    soglia = adesso - timedelta(days=30 * mesi)
    piani, token = [], None
    while True:
        params = {"status": "SHIPPED", "pageSize": 30}
        if token:
            params["paginationToken"] = token
        risposta = client.get(_BASE, params=params)
        piani += [p for p in (risposta.get("inboundPlans") or [])
                  if p.get("createdAt") and _istante(p["createdAt"]) >= soglia]
        token = (risposta.get("pagination") or {}).get("nextToken")
        if not token:
            return sorted(piani, key=lambda p: p["createdAt"], reverse=True)


def _scatole_del_piano(client, plan_id: str) -> list[dict]:
    scatole, token = [], None
    while True:
        params = {"pageSize": 100}
        if token:
            params["paginationToken"] = token
        risposta = client.get(f"{_BASE}/{plan_id}/boxes", params=params)
        scatole += risposta.get("boxes") or []
        token = (risposta.get("pagination") or {}).get("nextToken")
        if not token:
            return scatole


def _riferimenti(scatole: list[dict]) -> tuple[dict[str, dict], int]:
    """Per ogni SKU il collo di riferimento del piano: il mono-SKU con più
    pezzi. I mono-SKU più piccoli dello stesso piano sono resti (130 pz =
    2×60 + 1×10), non un imballo diverso. Le scatole con più SKU si contano
    e basta: non dicono quanto pesa un collo di uno SKU solo."""
    riferimenti, misti = {}, 0
    for b in scatole:
        items = b.get("items") or []
        if b.get("contentInformationSource") != "BOX_CONTENT_PROVIDED" or not items:
            continue
        if len(items) > 1:
            misti += int(b.get("quantity") or 1)
            continue
        d, w, it = b.get("dimensions") or {}, b.get("weight") or {}, items[0]
        if not d or not w:
            continue
        collo = {"pezzi": int(it["quantity"]), "lunghezza_cm": float(d["length"]),
                 "larghezza_cm": float(d["width"]), "altezza_cm": float(d["height"]),
                 "peso_kg": float(w["value"]), "modello": b.get("templateName") or None,
                 "asin": it.get("asin")}
        if it["msku"] not in riferimenti or collo["pezzi"] > riferimenti[it["msku"]]["pezzi"]:
            riferimenti[it["msku"]] = collo
    return riferimenti, misti


def _combo(collo: dict) -> tuple:
    return tuple(collo[k] for k in _MISURE)


def leggi_storico(client, cliente: str, adesso: datetime | None = None, mesi: int = 12) -> dict:
    """L'istantanea: per ogni SKU lo standard (il collo di riferimento del
    piano più recente) e le varianti (combinazioni diverse nei piani più
    vecchi, con l'ultima data in cui sono comparse). Un piano le cui scatole
    non si leggono viene saltato e nominato: lo standard deve uscire anche
    da uno storico con buchi."""
    adesso = adesso or datetime.now(timezone.utc)
    piani = _piani_spediti(client, adesso, mesi)
    sku: dict[str, dict] = {}
    misti, saltati = 0, []
    for p in piani:
        pid = p["inboundPlanId"]
        try:
            scatole = _scatole_del_piano(client, pid)
        except SpApiError as e:
            saltati.append(f"{pid}: {e}")
            continue
        riferimenti, m = _riferimenti(scatole)
        misti += m
        data = p["createdAt"][:10]
        for s, collo in riferimenti.items():
            if s not in sku:
                sku[s] = {**collo, "ultimo_piano": pid, "ultima_data": data, "varianti": []}
                continue
            std = sku[s]
            if _combo(collo) == _combo(std) or any(_combo(collo) == _combo(v) for v in std["varianti"]):
                continue
            std["varianti"].append({**{k: collo[k] for k in _MISURE}, "ultima_data": data})
    return {"cliente": cliente, "letto_il": adesso.strftime("%Y-%m-%dT%H:%M:%SZ"), "mesi": mesi,
            "piani_letti": len(piani) - len(saltati), "piani_saltati": saltati,
            "colli_misti_esclusi": misti, "sku": sku}


def _percorso_istantanea(cliente: str) -> str:
    return os.path.expanduser(
        f"~/.config/newera-amazon/spedizioni/scatole/{_nome_cliente(cliente)}.json")


def salva_istantanea(istantanea: dict) -> str:
    percorso = _percorso_istantanea(istantanea["cliente"])
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    temporaneo = f"{percorso}.tmp"
    with open(temporaneo, "w") as f:
        f.write(json.dumps(istantanea, indent=2, ensure_ascii=False))
    os.replace(temporaneo, percorso)
    return percorso


def carica_istantanea(cliente: str) -> dict | None:
    """Come clienti.carica_cliente e spapi_client.crea_client, il cliente si
    riconosce a prescindere da maiuscole e spazi: un lotto con `cliente: ecm`
    è un lotto ECM valido, e deve trovare lo storico salvato come ECM.json."""
    percorso = _percorso_istantanea(cliente)
    if os.path.isfile(percorso):
        with open(percorso) as f:
            return json.load(f)
    cartella = os.path.dirname(percorso)
    if not os.path.isdir(cartella):
        return None
    chiave = _chiave(cliente)
    for nome in os.listdir(cartella):
        stem, ext = os.path.splitext(nome)
        if ext == ".json" and _chiave(stem) == chiave:
            with open(os.path.join(cartella, nome)) as f:
                return json.load(f)
    return None


def _misure(c: dict) -> str:
    return f"{c['lunghezza_cm']:g}×{c['larghezza_cm']:g}×{c['altezza_cm']:g}"


def tabella(istantanea: dict) -> str:
    """La tabella dei colli standard, nella forma della scheda cliente in
    ~/nova/CLIENTI/: SKU, ASIN, pezzi per collo, misure, peso, modello,
    ultimo piano, e le varianti viste prima."""
    righe = [f"Colli standard di {istantanea['cliente']} — letti {istantanea['piani_letti']} piani "
             f"SHIPPED negli ultimi {istantanea['mesi']} mesi (istantanea del {istantanea['letto_il'][:10]})"]
    if not istantanea["sku"]:
        righe.append("  nessun collo mono-SKU trovato: prova con --mesi più alto")
    for s, c in sorted(istantanea["sku"].items()):
        varianti = "; ".join(f"{v['pezzi']} pz {_misure(v)} cm {v['peso_kg']:g} kg fino al {v['ultima_data']}"
                             for v in c["varianti"])
        righe.append(f"  {s} ({c['asin'] or 'ASIN ?'}): {c['pezzi']} pz/collo, {_misure(c)} cm, "
                     f"{c['peso_kg']:g} kg, modello {c['modello'] or '—'}, ultimo piano {c['ultima_data']}"
                     + (f" | prima: {varianti}" if varianti else ""))
    if istantanea["colli_misti_esclusi"]:
        righe.append(f"  {istantanea['colli_misti_esclusi']} colli misti esclusi dallo standard")
    righe += [f"  piano non letto: {s}" for s in istantanea["piani_saltati"]]
    return "\n".join(righe)


def bozza_lotto(istantanea: dict, quantita: dict[str, int]) -> str:
    """Il lotto.yaml già compilato, come TESTO: yaml.safe_dump non scrive
    commenti, e qui i commenti sono il punto (da dove viene ogni collo, cosa
    manca). Colli pieni dallo standard; il resto in un collo parziale con le
    misure dello standard e il peso vuoto — la tara non è proporzionale, lo
    pesa chi imballa. `lotto.carica` rifiuta la bozza finché non è completa."""
    ignoti = sorted(set(quantita) - set(istantanea["sku"]))
    if ignoti:
        raise ValueError(
            f"nessuno storico per {', '.join(ignoti)}: SKU noti "
            f"{', '.join(sorted(istantanea['sku'])) or 'nessuno'}. La scatola di un articolo "
            "mai spedito la detti tu nel lotto.yaml")
    righe = [f"cliente: {json.dumps(istantanea['cliente'])}", "righe:"]
    righe += [f"  - {{sku: {json.dumps(s)}, quantita: {q}}}" for s, q in quantita.items()]
    righe.append("scatole:")
    for s, q in quantita.items():
        c = istantanea["sku"][s]
        pieni, resto = divmod(q, c["pezzi"])
        misure = (f"lunghezza_cm: {c['lunghezza_cm']:g}, larghezza_cm: {c['larghezza_cm']:g}, "
                  f"altezza_cm: {c['altezza_cm']:g}")
        if pieni:
            # Il nome del modello viene da Amazon: su una riga sola, o un a-capo
            # chiude il commento e la riga dopo diventa una chiave del YAML (DESIGN §17).
            modello = " ".join(str(c["modello"]).split()) if c["modello"] else "senza modello"
            righe.append(f"  - {{quantita: {pieni}, peso_kg: {c['peso_kg']:g}, {misure},")
            righe.append(f"     righe: [{{sku: {json.dumps(s)}, quantita: {c['pezzi']}}}]}}"
                         f"   # standard: {modello}, ultimo piano {c['ultima_data']}")
        if resto:
            righe.append(f"  - {{quantita: 1, peso_kg: , {misure},")
            righe.append(f"     righe: [{{sku: {json.dumps(s)}, quantita: {resto}}}]}}   # parziale: {resto} pz, da pesare")
    righe.append("data_pronta:   # istante UTC in cui la merce è pronta al ritiro, es. 2026-09-08T08:00Z")
    return "\n".join(righe) + "\n"


def scrivi_bozza(cliente: str, testo: str, adesso: datetime) -> str:
    """Sul Desktop come ogni consegna; mai sopra una bozza dello stesso giorno,
    che Mariano potrebbe aver già completato a mano."""
    base = os.path.expanduser(f"~/Desktop/Lotto-{_nome_cliente(cliente)}-{adesso:%Y-%m-%d}")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    percorso, n = f"{base}.yaml", 1
    while os.path.exists(percorso):
        n += 1
        percorso = f"{base}-{n}.yaml"
    with open(percorso, "w") as f:
        f.write(testo)
    return percorso


@dataclass
class Confronto:
    """Scostamenti fermano `prepara`; le note si stampano e basta."""
    scostamenti: list[str] = field(default_factory=list)
    note: list[str] = field(default_factory=list)


def _lati(l, w, h) -> tuple:
    return tuple(sorted(round(float(x), 1) for x in (l, w, h)))


def confronta(lotto, istantanea: dict) -> Confronto:
    """DESIGN §13: per ogni scatola mono-SKU del lotto, contro lo standard del
    suo SKU. Più pezzi dello standard: scostamento. Stessi pezzi: misure
    identiche (a meno dell'ordine dei lati) e peso entro ±10 %. Meno pezzi:
    collo parziale, come quello della bozza — misure identiche, peso non
    confrontato perché la tara non è proporzionale."""
    esito = Confronto()
    for i, s in enumerate(lotto.scatole, start=1):
        if not s.righe:
            esito.note.append(f"scatola {i}: senza contenuto dichiarato, non confrontata")
            continue
        if len(s.righe) != 1:
            esito.note.append(f"scatola {i}: mista ({', '.join(r.sku for r in s.righe)}), non confrontata")
            continue
        r = s.righe[0]
        std = istantanea["sku"].get(r.sku)
        if std is None:
            esito.note.append(f"scatola {i} ({r.sku}): nessuno storico, non confrontata")
            continue
        storico = (f"lo storico dice {std['pezzi']} pz {_misure(std)} cm {std['peso_kg']:g} kg "
                   f"(piano {std['ultimo_piano']}, {std['ultima_data']})")
        if r.quantita > std["pezzi"]:
            esito.scostamenti.append(f"scatola {i} ({r.sku}): {r.quantita} pz per collo, {storico}")
        elif _lati(s.lunghezza_cm, s.larghezza_cm, s.altezza_cm) != _lati(std["lunghezza_cm"], std["larghezza_cm"], std["altezza_cm"]):
            esito.scostamenti.append(
                f"scatola {i} ({r.sku}): misure {s.lunghezza_cm:g}×{s.larghezza_cm:g}×{s.altezza_cm:g} cm, {storico}")
        elif r.quantita < std["pezzi"]:
            esito.note.append(f"scatola {i} ({r.sku}): collo parziale da {r.quantita} pz, peso non confrontato")
        elif abs(s.peso_kg - std["peso_kg"]) > TOLLERANZA_PESO * std["peso_kg"] + 1e-9:
            esito.scostamenti.append(f"scatola {i} ({r.sku}): {s.peso_kg:g} kg dichiarati, {storico}")
    return esito
