"""Controllo del listino di un fornitore su Amazon: margine e vendibilità.

    python3 scripts/controllo.py <cliente> <listino> [--fba] [--soglia 15]
        [--spedizione 4.50] [--trasporto 0] [--iva 22] [--resi 5]
        [--marketplace APJ6JRA9NG5V4] [--da 1] [--quante N] [--conferme file.json]

Sola lettura (R0): legge catalogo, offerte, commissioni e restrizioni con
l'account del cliente; l'unico PUT è la prova d'offerta in VALIDATION_PREVIEW,
che non scrive. Non ordina dal fornitore e non crea inserzioni.

Le letture si salvano in ~/.config/newera-amazon/controllo-listino/: un giro
interrotto riparte da dove era, e rilanciarlo con --conferme rifà solo i
conti, senza richiamare Amazon per ciò che ha già letto."""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime

_QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _QUI)
# Client SP-API condiviso con amazon-inserzioni: credenziali per cliente in
# ~/scripts/.env, seller id incluso (serve a restrizioni e prova d'offerta).
sys.path.insert(1, os.path.expanduser("~/.claude/skills/amazon-inserzioni/scripts"))

import abbina
import listino as listino_mod
import margine as margine_mod
import mercato
from spapi_client import SpApiError

CARTELLA_STATO = os.path.expanduser("~/.config/newera-amazon/controllo-listino")
COMMISSIONE_STIMATA = 0.15     # solo per scartare chi è in perdita anche così, mai nel conto finale


class Stato:
    """Cache delle letture su disco, salvata spesso: un giro di migliaia di
    righe dura ore e non deve ripartire da zero."""

    def __init__(self, percorso: str):
        self.percorso = percorso
        self.dati = {"abbinamenti": {}, "candidati": {}, "offerte": {}, "commissioni": {}, "restrizioni": {},
                     "prove": {}}
        if os.path.exists(percorso):
            with open(percorso) as f:
                self.dati.update(json.load(f))

    def __getitem__(self, chiave):
        return self.dati[chiave]

    def salva(self):
        os.makedirs(os.path.dirname(self.percorso), exist_ok=True)
        tmp = self.percorso + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.dati, f, ensure_ascii=False)
        os.replace(tmp, self.percorso)


def _chiave_riga(r) -> str:
    return f"{r.riga_file}:{r.codice}"


def _abb_da_json(d: dict | None) -> abbina.Abbinamento:
    if not d:
        return abbina.Abbinamento(None, "", "")
    s = d.get("scheda")
    return abbina.Abbinamento(abbina.Scheda(**s) if s else None, d["confidenza"], d.get("nota", ""))


def fase_abbinamento(client, mkt: str, righe, stato: Stato, pausa: float = 0.5, log=print):
    fatti = stato["abbinamenti"]
    eans = sorted({r.ean for r in righe if r.ean and _chiave_riga(r) not in fatti})
    if eans:
        trovate = abbina.cerca_ean(client, mkt, eans, pausa=pausa)
        for r in righe:
            if r.ean in eans:
                fatti[_chiave_riga(r)] = asdict(abbina.abbina_ean(r.ean, trovate))
        stato.salva()
    # I candidati restano in cache: se cambia il modo di sceglierli, il giro
    # successivo rifà la scelta senza richiamare Amazon.
    cache = stato["candidati"]
    for r in righe:
        if not r.ean and _chiave_riga(r) in cache:
            schede = [abbina.Scheda(**c) for c in cache[_chiave_riga(r)]]
            fatti[_chiave_riga(r)] = asdict(abbina.scegli(r.descrizione, schede))
    da_cercare = [r for r in righe if not r.ean and _chiave_riga(r) not in cache]
    for n, r in enumerate(da_cercare, 1):
        try:
            candidati = abbina.cerca_parole(client, mkt, r.descrizione, r.marca)
            cache[_chiave_riga(r)] = [asdict(c) for c in candidati]
            fatti[_chiave_riga(r)] = asdict(abbina.scegli(r.descrizione, candidati))
        except SpApiError as e:
            log(f"  riga {r.riga_file}: ricerca non riuscita ({str(e)[:80]}), la riprendo al prossimo giro")
        if n % 50 == 0:
            stato.salva()
            log(f"  abbinamento per descrizione: {n}/{len(da_cercare)}")
        if pausa:
            time.sleep(pausa)
    stato.salva()


def _prezzo_merce(r, abb) -> float:
    return r.costo * (abb.scheda.pezzi if abb.scheda else 1)


def fase_mercato(client, cliente: str, mkt: str, righe, p: margine_mod.Parametri, stato: Stato,
                 pausa_offerte: float = 10.5, pausa: float = 1.1, log=print, seller_id: str | None = None):
    abbinati = [(r, _abb_da_json(stato["abbinamenti"].get(_chiave_riga(r)))) for r in righe]
    abbinati = [(r, a) for r, a in abbinati if a.scheda]
    asins = sorted({a.scheda.asin for _, a in abbinati} - set(stato["offerte"]))
    if asins:
        log(f"  offerte di {len(asins)} schede")
        for i in range(0, len(asins), 100):
            lette = mercato.leggi_offerte(client, mkt, asins[i:i + 100], pausa=pausa_offerte)
            for a in asins[i:i + 100]:
                stato["offerte"][a] = asdict(lette.get(a, mercato.Offerte()))
            stato.salva()
    if seller_id is None:
        from spapi_client import seller_id_di
        seller_id = seller_id_di(cliente)
    candidati = []
    for r, a in abbinati:
        o = stato["offerte"].get(a.scheda.asin) or {}
        if not o.get("prezzo") or not r.a_pezzo:
            continue
        stima = margine_mod.calcola(o["prezzo"], o["prezzo"] * COMMISSIONE_STIMATA,
                                    r.costo, a.scheda.pezzi, p)
        if stima.margine > 0:
            candidati.append((r, a, o["prezzo"]))
    log(f"  commissioni e restrizioni di {len(candidati)} righe in utile alla stima")
    for n, (r, a, prezzo) in enumerate(candidati, 1):
        asin = a.scheda.asin
        chiave_c = f"{asin}|{prezzo}|{'FBA' if p.fba else 'FBM'}"
        try:
            if chiave_c not in stato["commissioni"]:
                stato["commissioni"][chiave_c] = asdict(mercato.stima_commissioni(client, mkt, asin, prezzo, p.fba))
                if pausa:
                    time.sleep(pausa)
            if asin not in stato["restrizioni"]:
                stato["restrizioni"][asin] = asdict(mercato.leggi_restrizioni(client, seller_id, mkt, asin))
        except SpApiError as e:
            log(f"  {asin}: lettura non riuscita ({str(e)[:80]}), la riprendo al prossimo giro")
        if n % 25 == 0:
            stato.salva()
    stato.salva()
    return seller_id


def costruisci_righe(righe, p: margine_mod.Parametri, stato: Stato, conferme: dict[str, str]):
    """Una riga del foglio per articolo del listino, col semaforo. `conferme`
    = {codice: "si"|"no"} dalla revisione degli abbinamenti per descrizione."""
    out = []
    for r in righe:
        abb = _abb_da_json(stato["abbinamenti"].get(_chiave_riga(r)))
        esito_conf = (conferme.get(r.codice) or "").strip().lower()
        if abb.scheda and esito_conf == "no":
            abb = abbina.Abbinamento(None, "", "abbinamento scartato alla revisione")
        sicuro = abb.sicuro or (abb.scheda is not None and esito_conf in ("si", "sì"))
        if sicuro and not abb.sicuro:
            abb = abbina.Abbinamento(abb.scheda, abb.confidenza, "abbinamento confermato in revisione")
        riga = {"r": r, "abb": abb, "sicuro": sicuro, "off": None, "comm": None, "restr": None,
                "prova": None, "conto": None}
        if abb.scheda:
            asin = abb.scheda.asin
            o = stato["offerte"].get(asin)
            riga["off"] = mercato.Offerte(**o) if o else None
            if riga["off"] and riga["off"].prezzo:
                chiave_c = f"{asin}|{riga['off'].prezzo}|{'FBA' if p.fba else 'FBM'}"
                c = stato["commissioni"].get(chiave_c)
                riga["comm"] = mercato.Commissioni(**c) if c else None
            rs = stato["restrizioni"].get(asin)
            riga["restr"] = mercato.Restrizioni(**rs) if rs else None
            pv = stato["prove"].get(asin)
            riga["prova"] = pv
            if riga["comm"] and riga["comm"].commissione is not None and r.a_pezzo \
                    and not (p.fba and riga["comm"].logistica is None):
                riga["conto"] = margine_mod.calcola(riga["off"].prezzo, riga["comm"].commissione, r.costo,
                                                    abb.scheda.pezzi, p, riga["comm"].logistica)
            elif riga["off"] and riga["off"].prezzo and r.a_pezzo and riga["comm"] is None:
                # Fuori dai candidati: in perdita già con la commissione stimata.
                riga["conto"] = margine_mod.calcola(riga["off"].prezzo, riga["off"].prezzo * COMMISSIONE_STIMATA,
                                                    r.costo, abb.scheda.pezzi, p)
                riga["conto_stimato"] = True
        out.append(riga)
    for riga in out:
        riga["esito"] = _esito(riga, p)
    return out


def _esito(riga, p) -> margine_mod.Esito:
    abb, off, restr = riga["abb"], riga["off"], riga["restr"]
    prova = (riga["prova"] or {}).get("stato") if riga["prova"] else None
    esito = margine_mod.semaforo(
        trovato=abb.scheda is not None, sicuro=riga["sicuro"], conto=riga["conto"],
        classifica=abb.scheda.classifica if abb.scheda else None,
        n_offerte=off.n_offerte if off else 0, amazon_vende=bool(off and off.amazon_vende),
        blocchi=restr.blocchi if restr else [], approvazione=bool(restr and restr.approvazione),
        prova=prova, a_pezzo=riga["r"].a_pezzo, p=p)
    if riga.get("conto_stimato"):
        esito.motivi.append("commissione stimata al 15%")
    if riga["comm"] and riga["comm"].errore:
        esito.motivi.append(riga["comm"].errore)
    if abb.nota and abb.nota not in esito.motivi:
        esito.motivi.append(abb.nota)
    return esito


def fase_prova(client, seller_id: str, mkt: str, righe_foglio, stato: Stato, pausa: float = 0.3, log=print):
    """Prova d'offerta solo dove il resto non ha già dato rosso o grigio."""
    da_provare = {}
    for riga in righe_foglio:
        if riga["esito"].colore in ("verde", "giallo") and riga["abb"].scheda:
            asin = riga["abb"].scheda.asin
            if asin not in stato["prove"]:
                da_provare[asin] = (riga["abb"].scheda.product_type, riga["off"].prezzo)
    log(f"  prova d'offerta su {len(da_provare)} schede (VALIDATION_PREVIEW, nulla scritto)")
    for asin, (pt, prezzo) in da_provare.items():
        try:
            esito, errori = mercato.prova_offerta(client, seller_id, mkt, asin, pt, prezzo)
            stato["prove"][asin] = {"stato": esito, "errori": errori}
        except SpApiError as e:
            log(f"  {asin}: prova non riuscita ({str(e)[:80]})")
        if pausa:
            time.sleep(pausa)
    stato.salva()


def da_rivedere(righe_foglio) -> list[dict]:
    """Le righe che diventerebbero verdi o gialle se l'abbinamento per
    descrizione fosse giusto: il materiale per il revisore (SKILL.md, punto 3)."""
    out = []
    for riga in righe_foglio:
        r, abb, off = riga["r"], riga["abb"], riga["off"]
        if abb.scheda and not riga["sicuro"] and riga["esito"].colore in ("verde", "giallo"):
            out.append({"codice": r.codice, "descrizione": r.descrizione, "um": r.um, "costo": r.costo,
                        "asin": abb.scheda.asin, "titolo": abb.scheda.titolo, "marca": abb.scheda.marca,
                        "pezzi": abb.scheda.pezzi, "prezzo": off.prezzo if off else None})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cliente")
    ap.add_argument("listino")
    ap.add_argument("--fba", action="store_true", help="logistica Amazon (default: spedizione propria)")
    ap.add_argument("--soglia", type=float, default=15.0)
    ap.add_argument("--spedizione", type=float, default=4.50)
    ap.add_argument("--trasporto", type=float, default=0.0)
    ap.add_argument("--iva", type=float, default=22.0)
    ap.add_argument("--resi", type=float, default=5.0)
    ap.add_argument("--marketplace", default="APJ6JRA9NG5V4")
    ap.add_argument("--da", type=int, default=1, help="primo articolo (1 = il primo del file)")
    ap.add_argument("--quante", type=int, default=None, help="quanti articoli controllare")
    ap.add_argument("--conferme", default=None, help='JSON {codice: "si"|"no"} dalla revisione')
    a = ap.parse_args(argv)

    p = margine_mod.Parametri(a.fba, a.soglia, a.spedizione, a.trasporto, a.iva, a.resi)
    tutte = listino_mod.leggi_listino(a.listino)
    righe = tutte[a.da - 1:(a.da - 1 + a.quante) if a.quante else None]
    print(f"Listino: {len(tutte)} articoli, ne controllo {len(righe)}")
    adesso = datetime.now()
    nome = os.path.splitext(os.path.basename(a.listino))[0]
    stato = Stato(os.path.join(CARTELLA_STATO, f"{a.cliente}-{nome}-{adesso:%Y-%m-%d}.json".replace(" ", "_")))
    conferme = {}
    if a.conferme:
        with open(a.conferme) as f:
            conferme = {str(k): str(v) for k, v in json.load(f).items()}

    from spapi_client import crea_client
    client = crea_client(a.cliente)
    print("1/4 abbinamento delle schede")
    fase_abbinamento(client, a.marketplace, righe, stato)
    print("2/4 offerte, commissioni e restrizioni")
    seller_id = fase_mercato(client, a.cliente, a.marketplace, righe, p, stato)
    righe_foglio = costruisci_righe(righe, p, stato, conferme)
    print("3/4 prova d'offerta")
    fase_prova(client, seller_id, a.marketplace, righe_foglio, stato)
    righe_foglio = costruisci_righe(righe, p, stato, conferme)
    print("4/4 foglio Excel")
    import foglio
    percorso = foglio.scrivi(a.cliente, nome, righe_foglio, p, adesso, len(tutte), a.da)
    conta = {}
    for riga in righe_foglio:
        conta[riga["esito"].colore] = conta.get(riga["esito"].colore, 0) + 1
    print(f"\nFatto: {percorso}")
    print("  " + " · ".join(f"{k} {conta.get(k, 0)}" for k in ("verde", "giallo", "rosso", "grigio")))
    da_confermare = da_rivedere(righe_foglio)
    if da_confermare:
        file_rev = stato.percorso.replace(".json", "-da-rivedere.json")
        with open(file_rev, "w") as f:
            json.dump(da_confermare, f, ensure_ascii=False, indent=1)
        print(f"  {len(da_confermare)} abbinamenti per descrizione da confermare: {file_rev}")
    print(f"  letture salvate in {stato.percorso}")


if __name__ == "__main__":
    main()
