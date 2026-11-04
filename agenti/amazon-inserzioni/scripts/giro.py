"""Giro operativo degli aggiornamenti: istruzione -> anteprima -> "vai" ->
scrittura in lotto -> verifica differita.

La porta principale e' la chat: Mariano dice "13,90 su questi SKU" e Claude
chiama `da_dettatura`. La tabella (CSV/XLSX con sku, campo, valore) e' solo
l'altra porta, per quando un cliente la manda gia' pronta.

Il lotto vive su file (`~/.config/newera-amazon/lotti/<id>.json`): cio' che
si esegue col "vai" e' ESATTAMENTE cio' che si e' mostrato in anteprima, non
un ricalcolo. Il cancello del "vai" resta procedurale (SKILL.md): questo
modulo espone `anteprima` e `vai` come due chiamate separate, mai una sola.
"""
from __future__ import annotations
import argparse
import csv
import fcntl
import io
import json
import os
import re
import secrets
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

import campi
import catalogo_venditore
import diff
import modifiche as modifiche_mod
import registro
from modifiche import Modifica
from letto import leggi, StatoAttuale
from applica import applica
from spapi_client import SpApiError, crea_client, seller_id_di

MARKETPLACE = {
    "IT": "APJ6JRA9NG5V4", "DE": "A1PA6795UKMFR9", "ES": "A1RKKUPIHCS9HS", "FR": "A13V1IB3VIYZZH",
    "UK": "A1F83G8C2ARO7P", "NL": "A1805IZSGTT6HS", "SE": "A2NODRKZP88ZB9", "PL": "A1C3SOZRARQ6R3",
    "BE": "AMEN7PMS3EDWL",
}


# ---------------------------------------------------------- istruzioni ----

@dataclass
class Istruzione:
    sku: str
    campi: dict  # {nome italiano: valore grezzo}


def da_dettatura(skus: list[str], campi_richiesti: dict) -> list[Istruzione]:
    """Gli stessi campi per tutti gli SKU: "13,90 su tutti gli snack"."""
    return [Istruzione(sku, dict(campi_richiesti)) for sku in skus]


def _righe_tabella(percorso: str) -> list[list]:
    if percorso.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        foglio = load_workbook(percorso, read_only=True, data_only=True).worksheets[0]
        return [list(r) for r in foglio.iter_rows(values_only=True)]
    with open(percorso, newline="", encoding="utf-8-sig") as f:
        testo = f.read()
    try:
        dialetto = csv.Sniffer().sniff(testo[:2048], delimiters=";,\t")
    except csv.Error:
        dialetto = csv.excel
        dialetto.delimiter = ";"
    return list(csv.reader(io.StringIO(testo), dialect=dialetto))


def da_tabella(percorso: str) -> list[Istruzione]:
    """CSV o XLSX con colonne sku, campo, valore (una riga per modifica),
    raggruppate per SKU nell'ordine di prima apparizione."""
    righe = _righe_tabella(percorso)
    if not righe:
        raise ValueError(f"{percorso}: tabella vuota")
    intestazione = [campi.normalizza_nome(str(c or "")) for c in righe[0]]
    if not {"sku", "campo", "valore"} <= set(intestazione):
        raise ValueError(f"{percorso}: servono le colonne sku, campo, valore (trovate: {intestazione})")
    i_sku, i_campo, i_valore = (intestazione.index(c) for c in ("sku", "campo", "valore"))
    per_sku: dict[str, dict] = {}
    in_conflitto: list[Istruzione] = []
    for r in righe[1:]:
        if not any(c not in (None, "") for c in r):
            continue
        sku = str(r[i_sku] or "").strip()
        campo = str(r[i_campo] or "").strip()
        valore = r[i_valore]
        if not sku or not campo:
            continue
        valore = valore.strip() if isinstance(valore, str) else valore
        campi_sku = per_sku.setdefault(sku, {})
        gia = next((k for k in campi_sku if campi.normalizza_nome(k) == campi.normalizza_nome(campo)), None)
        if gia is None:
            campi_sku[campo] = valore
        elif campi_sku[gia] != valore:
            # Stesso campo due volte con valori diversi: un'istruzione a parte,
            # cosi' l'anteprima lo marca come conflitto invece di far vincere
            # l'ultima riga in silenzio (ricollaudo 06/09/2026).
            in_conflitto.append(Istruzione(sku, {campo: valore}))
    return [Istruzione(sku, c) for sku, c in per_sku.items()] + in_conflitto


# --------------------------------------------------------------- lotto ----

@dataclass
class Lotto:
    id: str
    cliente: str
    marketplace_id: str
    fonte: str
    pacchetti: dict = field(default_factory=dict)      # sku -> {product_type, modifiche: [Modifica]}
    senza_modifiche: list = field(default_factory=list)  # SKU gia' come richiesto
    errori: dict = field(default_factory=dict)          # sku o sku#campo -> perche'
    eseguito: bool = False

    def tabella(self) -> str:
        righe = [f"Lotto {self.id} — {self.cliente} su {self.marketplace_id}: "
                 f"{len(self.pacchetti)} SKU da scrivere, {len(self.senza_modifiche)} gia' a posto, "
                 f"{len(self.errori)} errori"]
        for sku, p in self.pacchetti.items():
            righe.append(diff.rendi_tabella(sku, p["modifiche"]))
        for sku in self.senza_modifiche:
            righe.append(diff.rendi_tabella(sku, []))
        for chiave, perche in self.errori.items():
            righe.append(f"{chiave}: ERRORE {perche}")
        return "\n".join(righe)


_ID_LOTTO = re.compile(r"^[a-z0-9-]+$")


def percorso_lotto(lotto_id: str) -> str:
    # L'id arriva da una riga di comando composta da testo libero: solo la
    # forma che _nuovo_id produce, mai un pezzo di percorso.
    if not _ID_LOTTO.fullmatch(lotto_id):
        raise ValueError(f"id di lotto non valido: {lotto_id!r}")
    base = os.path.expanduser("~/.config/newera-amazon/lotti")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{lotto_id}.json")


def salva(lotto: Lotto) -> None:
    """Scrittura atomica: chi legge trova sempre il file precedente o quello
    nuovo per intero, mai una meta' (JSONDecodeError fra due vai, collaudo
    06/09/2026)."""
    percorso = percorso_lotto(lotto.id)
    provvisorio = percorso + ".tmp"
    try:
        with open(provvisorio, "w") as f:
            json.dump(asdict(lotto), f, indent=2, ensure_ascii=False)
        os.replace(provvisorio, percorso)
    except BaseException:
        if os.path.exists(provvisorio):
            os.remove(provvisorio)
        raise


def carica(lotto_id: str) -> Lotto:
    with open(percorso_lotto(lotto_id)) as f:
        dati = json.load(f)
    # eseguito=True si salva sotto l'id interno: una copia con un altro nome
    # resterebbe rieseguibile per sempre (collaudo 06/09/2026).
    if dati.get("id") != lotto_id:
        raise ValueError(f"il file {lotto_id}.json contiene il lotto {dati.get('id')!r}: "
                         "e' una copia o un file manomesso, non si esegue")
    for sku, p in dati["pacchetti"].items():
        p["modifiche"] = [Modifica(**m) for m in p["modifiche"]]
        for m in p["modifiche"]:
            # La guardia anti-svuotamento vale anche per il file su disco: un
            # dopo=null con fonte "annulla" passava da vai come op delete.
            if m.dopo in (None, "", [], {}):
                raise ValueError(f"lotto {lotto_id}: dopo vuoto su {sku} {m.campo}, "
                                 "uno svuotamento non passa mai da un lotto")
    return Lotto(**dati)


@contextmanager
def _esclusiva(lotto_id: str):
    """Un solo processo alla volta fra "e' gia' eseguito?" e "lo marco": due
    vai concorrenti scrivevano entrambi (59 giri su 60, collaudo 06/09/2026).
    Il lucchetto e' un file a parte perche' il lotto viene sostituito
    atomicamente e un lock sul suo inode non sopravvivrebbe."""
    with open(percorso_lotto(lotto_id) + ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _nuovo_id(cliente: str) -> str:
    return f"{registro.slug_cliente(cliente)}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def _controlla_appartenenza(lotto: Lotto, cliente: str, marketplace_id: str) -> None:
    """Il "vai" di Mariano copre il lotto mostrato: cliente e marketplace
    sono quelli dell'anteprima, non quelli della riga di comando di oggi.
    Senza questo confronto un lotto Phy to Pet/IT lanciato con le credenziali
    di Bedshire su DE scriveva la' e registrava sotto Phy to Pet (collaudo
    06/09/2026)."""
    if registro.slug_cliente(lotto.cliente) != registro.slug_cliente(cliente) \
            or lotto.marketplace_id != marketplace_id:
        raise ValueError(f"il lotto {lotto.id} appartiene a {lotto.cliente!r} su {lotto.marketplace_id}, "
                         f"non a {cliente!r} su {marketplace_id}: rifai l'anteprima")


# ----------------------------------------------------------- anteprima ----

def _unisci_per_sku(istruzioni: list[Istruzione]) -> tuple[dict[str, dict], dict[str, str]]:
    """Un solo dizionario di campi (nomi normalizzati) per SKU, nell'ordine di
    prima apparizione. Lo stesso campo chiesto due volte con valori diversi
    e' un'ambiguita', non una scelta: va negli errori e non si propone."""
    per_sku: dict[str, dict] = {}
    errori: dict[str, str] = {}
    for istr in istruzioni:
        richiesti = per_sku.setdefault(istr.sku, {})
        for nome, valore in istr.campi.items():
            chiave = campi.normalizza_nome(nome)
            if chiave in richiesti and richiesti[chiave] != valore:
                errori[f"{istr.sku}#{chiave}"] = f"valori in conflitto: {richiesti[chiave]!r} e {valore!r}"
            richiesti.setdefault(chiave, valore)
    for chiave in errori:
        sku, campo = chiave.split("#", 1)
        per_sku[sku].pop(campo, None)
    return per_sku, errori


def anteprima(client, seller_id: str, marketplace_id: str, cliente: str,
              istruzioni: list[Istruzione], *, fonte: str) -> Lotto:
    """Legge lo stato vivo di ogni SKU, traduce i campi in italiano in
    attributi completi, propone solo cio' che cambia. Non scrive nulla su
    Amazon. Un errore su uno SKU o su un campo resta confinato li'."""
    lotto = Lotto(id=_nuovo_id(cliente), cliente=cliente, marketplace_id=marketplace_id, fonte=fonte)
    per_sku, lotto.errori = _unisci_per_sku(istruzioni)
    for sku, richiesti in per_sku.items():
        if not richiesti:
            continue
        try:
            stato = leggi(client, seller_id, marketplace_id, sku)
        except SpApiError as e:
            lotto.errori[sku] = str(e)
            continue
        if not stato.product_type:
            lotto.errori[sku] = "product_type non leggibile dall'inserzione: la PATCH lo esige"
            continue
        proposte = {}
        for nome, valore in richiesti.items():
            # Due campi sullo stesso attributo (quantita e tempo di gestione):
            # il secondo riparte dal "dopo" del primo, non dal vivo, altrimenti
            # lo cancella in silenzio (collaudo 06/09/2026).
            parziale = StatoAttuale(stato.sku, stato.product_type, {**stato.attributi, **proposte})
            try:
                attributo, dopo = campi.proponi(nome, valore, parziale, marketplace_id)
            except ValueError as e:
                lotto.errori[f"{sku}#{nome}"] = str(e)
                continue
            proposte[attributo] = dopo
        mods = modifiche_mod.componi(stato, proposte, fonte)
        if mods:
            lotto.pacchetti[sku] = {"product_type": stato.product_type, "modifiche": mods}
    lotto.senza_modifiche = [
        s for s in per_sku
        if s not in lotto.pacchetti and not any(k == s or k.startswith(s + "#") for k in lotto.errori)
    ]
    salva(lotto)
    return lotto


# ------------------------------------------------------------------ vai ----

@dataclass
class Esito:
    lotto_id: str
    attesa: int
    scritti: dict = field(default_factory=dict)    # sku -> (esito Amazon, issues)
    saltati: dict = field(default_factory=dict)    # sku -> perche' non si e' scritto
    verifiche: dict = field(default_factory=dict)  # sku -> {campo: stato}

    def riassunto(self) -> str:
        accettati = [s for s, (e, _) in self.scritti.items() if e == "ACCEPTED"]
        invalidi = {s: iss for s, (e, iss) in self.scritti.items() if e != "ACCEPTED"}
        righe = [f"Lotto {self.lotto_id}: {len(accettati)} scritti ACCEPTED, "
                 f"{len(invalidi)} respinti, {len(self.saltati)} saltati."]
        if accettati:
            righe.append("  ACCEPTED: " + ", ".join(accettati))
        for sku, issues in invalidi.items():
            messaggi = "; ".join(i.get("message", str(i)) for i in issues) or "senza dettaglio"
            righe.append(f"  {sku}: respinto — {messaggi}")
        for sku, perche in self.saltati.items():
            righe.append(f"  {sku}: saltato — {perche}")
        storti = [(s, c, st) for s, v in self.verifiche.items() for c, st in v.items() if st != "applicato"]
        if self.verifiche and not storti:
            righe.append(f"Verifica a {self.attesa}s: tutti i campi risultano applicati.")
        for sku, campo, stato in storti:
            righe.append(f"  verifica {sku} {campo}: {stato}")
        return "\n".join(righe)


def _contiene(trovato, atteso) -> bool:
    """Tutto cio' che abbiamo scritto c'e' col valore giusto. Amazon
    arricchisce (language_tag e altro): le chiavi in piu' non contano."""
    if isinstance(atteso, dict):
        return isinstance(trovato, dict) and all(k in trovato and _contiene(trovato[k], v) for k, v in atteso.items())
    if isinstance(atteso, list):
        return isinstance(trovato, list) and len(trovato) == len(atteso) and \
            all(_contiene(t, a) for t, a in zip(trovato, atteso))
    return trovato == atteso


def verifica_lotto(client, seller_id: str, marketplace_id: str, lotto: Lotto,
                   solo: dict | None = None) -> dict[str, dict[str, str]]:
    """Rilegge ogni SKU del lotto e confronta col "dopo" proposto. Da chiamare
    dopo un'attesa: la rilettura immediata restituisce snapshot fantasma."""
    verifiche: dict[str, dict[str, str]] = {}
    for sku, p in (solo if solo is not None else lotto.pacchetti).items():
        try:
            vivo = leggi(client, seller_id, marketplace_id, sku)
        except SpApiError as e:
            # Le scritture sono gia' avvenute: una rilettura fallita si
            # riporta, non fa perdere l'esito del lotto (collaudo 06/09/2026).
            verifiche[sku] = {m.campo: f"non verificabile: {e}" for m in p["modifiche"]}
            continue
        verifiche[sku] = {}
        for m in p["modifiche"]:
            trovato = vivo.attributi.get(m.campo)
            if _contiene(trovato, m.dopo):
                verifiche[sku][m.campo] = "applicato"
            elif trovato == m.prima:
                verifiche[sku][m.campo] = "non applicato"
            else:
                verifiche[sku][m.campo] = "applicato diversamente"
    return verifiche


def vai(client, seller_id: str, marketplace_id: str, lotto_id: str, *,
        cliente: str, attesa: int = 60, dormi=time.sleep) -> Esito:
    """Esegue un lotto gia' mostrato e approvato. Chiamare SOLO dopo il "vai"
    di Mariano. Scrive tutto, aspetta, poi verifica il lotto intero: le
    verifiche riga per riga di applica sono falsi allarmi a raffica."""
    if attesa < 0:
        raise ValueError(f"attesa {attesa}: non puo' essere negativa")
    with _esclusiva(lotto_id):
        lotto = carica(lotto_id)
        _controlla_appartenenza(lotto, cliente, marketplace_id)
        if lotto.eseguito:
            raise ValueError(f"lotto {lotto_id} gia' eseguito: per riscrivere rifai l'anteprima")
        # Marcato PRIMA di scrivere: un crash a meta' non deve permettere un
        # secondo "vai" alla cieca sullo stesso lotto.
        lotto.eseguito = True
        salva(lotto)

    esito = Esito(lotto_id=lotto_id, attesa=attesa)
    for sku, p in lotto.pacchetti.items():
        try:
            vivo = leggi(client, seller_id, marketplace_id, sku)
        except SpApiError as e:
            esito.saltati[sku] = f"illeggibile prima della scrittura: {e}"
            continue
        # Il "dopo" e' l'array intero costruito sul vivo dell'anteprima: se
        # quel vivo e' cambiato, riscriverlo cancellerebbe la modifica altrui.
        cambiati = [m.campo for m in p["modifiche"] if vivo.attributi.get(m.campo) != m.prima]
        if cambiati:
            esito.saltati[sku] = f"cambiato nel frattempo ({', '.join(cambiati)}): rifai l'anteprima"
            continue
        try:
            stato, issues, _ = applica(client, seller_id, marketplace_id, lotto.cliente, sku,
                                       p["product_type"], p["modifiche"])
        except SpApiError as e:
            esito.saltati[sku] = f"errore di trasporto: {e}"
            continue
        esito.scritti[sku] = (stato, issues)

    accettati = {sku: lotto.pacchetti[sku] for sku, (st, _) in esito.scritti.items() if st == "ACCEPTED"}
    if accettati:
        dormi(attesa)
        esito.verifiche = verifica_lotto(client, seller_id, marketplace_id, lotto, solo=accettati)
    return esito


# ------------------------------------------------------------ selezione ----

def _voci_da_criteri(client, seller_id: str, marketplace_id: str, ns: argparse.Namespace):
    in_vendita = {"in-vendita": True, "non-in-vendita": False}.get(ns.stato)
    voci = catalogo_venditore.cerca(client, seller_id, marketplace_id,
                                    padre=ns.padre, in_vendita=in_vendita, severita=ns.con_issue)
    return catalogo_venditore.filtra(voci, prefisso=ns.prefisso, dove=_campi_da_argomenti(ns.dove))


def _ha_criteri(ns: argparse.Namespace) -> bool:
    return any([ns.padre, ns.prefisso, ns.stato, ns.con_issue, ns.dove])


def seleziona(client, seller_id: str, marketplace_id: str, ns: argparse.Namespace) -> list[str]:
    """Gli SKU bersaglio: quelli detti per nome (--sku) piu' quelli trovati
    dai criteri (--padre, --prefisso, --stato, --con-issue, --dove), senza
    doppioni e in quest'ordine. Il catalogo si interroga solo se serve."""
    espliciti = list(ns.sku or [])
    if not espliciti and not _ha_criteri(ns):
        raise ValueError("serve almeno un --sku o un criterio (--padre, --prefisso, --stato, --con-issue, --dove)")
    if not _ha_criteri(ns):
        return espliciti
    trovati = [v.sku for v in _voci_da_criteri(client, seller_id, marketplace_id, ns)]
    return espliciti + [sku for sku in trovati if sku not in espliciti]


# ------------------------------------------------------------------ cli ----

def _attesa(testo: str) -> int:
    # La rilettura immediata restituisce snapshot fantasma (Bedshire 03/09):
    # sotto i 20 s la verifica e' un falso allarme, e un valore negativo
    # faceva crollare vai DOPO la scrittura.
    valore = int(testo)
    if valore < 20:
        raise argparse.ArgumentTypeError(f"--attesa {valore}: servono almeno 20 secondi")
    return valore


def _campi_da_argomenti(coppie: list[str] | None) -> dict:
    campi_richiesti = {}
    for coppia in coppie or []:
        if "=" not in coppia:
            raise ValueError(f"--campo vuole nome=valore, non '{coppia}'")
        nome, valore = coppia.split("=", 1)
        campi_richiesti[nome.strip()] = valore.strip()
    return campi_richiesti


def _aggiungi_selezione(p: argparse.ArgumentParser) -> None:
    p.add_argument("--sku", action="append", help="ripetibile")
    p.add_argument("--padre", help="tutti i figli di questo SKU padre")
    p.add_argument("--prefisso", help="tutti gli SKU che iniziano cosi'")
    p.add_argument("--stato", choices=["in-vendita", "non-in-vendita"])
    p.add_argument("--con-issue", choices=["ERROR", "WARNING"], help="solo SKU con avvisi di questa severita'")
    p.add_argument("--dove", action="append", help="campo=valore attuale, ripetibile (es. \"tempo di gestione=6\")")


def _argomenti(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="giro.py", description=__doc__)
    sub = p.add_subparsers(dest="comando", required=True)
    c = sub.add_parser("cerca", help="elenca gli SKU che passano i criteri, con stato e avvisi; non scrive")
    c.add_argument("--cliente", required=True)
    c.add_argument("--marketplace", default="IT", choices=sorted(MARKETPLACE))
    _aggiungi_selezione(c)
    c.add_argument("--issues", action="store_true", help="mostra il testo degli avvisi e gli attributi mancanti")
    a = sub.add_parser("anteprima", help="legge il vivo e mostra prima -> dopo; non scrive")
    a.add_argument("--cliente", required=True)
    a.add_argument("--marketplace", default="IT", choices=sorted(MARKETPLACE))
    _aggiungi_selezione(a)
    a.add_argument("--campo", action="append", help="nome=valore, ripetibile (es. prezzo=13,90)")
    a.add_argument("--tabella", help="CSV/XLSX con colonne sku, campo, valore")
    a.add_argument("--fonte", default="chat")
    v = sub.add_parser("vai", help="esegue un lotto gia' approvato da Mariano")
    v.add_argument("--cliente", required=True)
    v.add_argument("--marketplace", default="IT", choices=sorted(MARKETPLACE))
    v.add_argument("--lotto", required=True)
    v.add_argument("--attesa", type=_attesa, default=60, help="secondi prima della verifica, minimo 20")
    r = sub.add_parser("verifica", help="rilegge un lotto gia' eseguito e confronta")
    r.add_argument("--cliente", required=True)
    r.add_argument("--marketplace", default="IT", choices=sorted(MARKETPLACE))
    r.add_argument("--lotto", required=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = _argomenti(sys.argv[1:] if argv is None else argv)
    client = crea_client(ns.cliente)
    seller = seller_id_di(ns.cliente)
    mkt = MARKETPLACE[ns.marketplace]
    if ns.comando == "cerca":
        voci = _voci_da_criteri(client, seller, mkt, ns)
        if ns.sku:
            voci = [v for v in voci if v.sku in set(ns.sku)]
        print(catalogo_venditore.tabella(voci, con_issues=ns.issues))
    elif ns.comando == "anteprima":
        if ns.tabella:
            istruzioni = da_tabella(ns.tabella)
        elif ns.campo:
            istruzioni = da_dettatura(seleziona(client, seller, mkt, ns), _campi_da_argomenti(ns.campo))
        else:
            raise SystemExit("anteprima: servono --tabella oppure --campo nome=valore con --sku o un criterio")
        print(anteprima(client, seller, mkt, ns.cliente, istruzioni, fonte=ns.fonte).tabella())
    elif ns.comando == "vai":
        print(vai(client, seller, mkt, ns.lotto, cliente=ns.cliente, attesa=ns.attesa).riassunto())
    else:
        lotto = carica(ns.lotto)
        _controlla_appartenenza(lotto, ns.cliente, mkt)
        for sku, v in verifica_lotto(client, seller, mkt, lotto).items():
            for campo, stato in v.items():
                print(f"{sku} {campo}: {stato}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
