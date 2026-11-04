"""Porta le offerte di un cliente da un marketplace a un altro, sulle schede
che a catalogo esistono gia' (offerta_estera). Quattro passi, un marketplace
alla volta, come giro.py:

  python3 scripts/estendi.py prepara  --cliente "Tecno Cold" --da IT --a FR
  python3 scripts/estendi.py prova    --lotto <file>          # VALIDATION_PREVIEW, nulla scritto
  python3 scripts/estendi.py vai      --lotto <file> --a FR   # scrive, solo dopo il "vai" di Mariano
  python3 scripts/estendi.py verifica --lotto <file> [--attesa 60]

Il lotto e' un file in ~/.config/newera-amazon/lotti/: `vai` esegue ESATTAMENTE
quello, rifiuta un marketplace diverso da quello del lotto e un lotto gia'
eseguito. Gli SKU scartati (padri, schede assenti, EAN doppi, prezzo mancante)
stanno nel file con il motivo: non spariscono.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spapi_client, catalogo_venditore, registro
from offerta_estera import (RigaOfferta, pianifica, converti_riga, pubblica_offerta, pubblica_lotto_offerte,
                            cerca_per_identificativi, ean_delle_voci)
from scheda_estera import pubblica_scheda, pubblica_lotto_schede, patch_ops
from modifiche import Modifica
from campi import _VALUTA
from applica import applica
import letto

MARKETPLACE = {"IT": "APJ6JRA9NG5V4", "DE": "A1PA6795UKMFR9", "FR": "A13V1IB3VIYZZH",
               "ES": "A1RKKUPIHCS9HS", "NL": "A1805IZSGTT6HS", "PL": "A1C3SOZRARQ6R3",
               "SE": "A2NODRKZP88ZB9", "BE": "AMEN7PMS3EDWL", "IE": "A28R8C7NBKEWEA", "UK": "A1F83G8C2ARO7P"}
LOTTI = os.path.expanduser("~/.config/newera-amazon/lotti")


def _voce_dict(v):
    a = v.attributi
    par = (a.get("parentage_level") or [{}])[0].get("value")
    ean = next((e.get("value") for e in a.get("externally_assigned_product_identifier") or []
                if e.get("type", "").lower() == "ean"), None)
    return {"sku": v.sku, "asin": v.asin, "product_type": v.product_type, "parentage": par,
            "ean": ean, "attributi": a}


def prepara(args):
    client = spapi_client.crea_client(args.cliente)
    seller = spapi_client.seller_id_di(args.cliente)
    origine = catalogo_venditore.cerca(client, seller, MARKETPLACE[args.da])
    voci = [_voce_dict(v) for v in origine]
    print(f"{args.da}: {len(voci)} SKU di origine")
    asins = [v["asin"] for v in voci if v["asin"]]
    eans = sorted({v["ean"] for v in voci if v["ean"]})
    dest = MARKETPLACE[args.a]
    per_asin_voci = cerca_per_identificativi(client, dest, asins, "ASIN")
    per_ean = ean_delle_voci(cerca_per_identificativi(client, dest, eans, "EAN"))
    ean_doppi = {e for e, n in Counter(v["ean"] for v in voci if v["ean"]).items() if n > 1}
    righe, saltati = pianifica(voci, set(per_asin_voci), per_ean, ean_doppi)
    valuta_da, valuta_a = _VALUTA[MARKETPLACE[args.da]], _VALUTA[dest]
    if valuta_da != valuta_a:
        if not args.cambio:
            raise SystemExit(f"{args.da} vende in {valuta_da}, {args.a} in {valuta_a}: serve --cambio")
        righe = [converti_riga(r, args.cambio) for r in righe]
    lotto = {
        "cliente": args.cliente, "da": args.da, "a": args.a, "marketplace_id": dest,
        "creato": datetime.now(timezone.utc).isoformat(), "eseguito": None,
        "valuta": valuta_a, "cambio": args.cambio if valuta_da != valuta_a else None,
        "righe": [asdict(r) for r in righe], "saltati": saltati,
    }
    os.makedirs(LOTTI, exist_ok=True)
    nome = f"{registro.slug_cliente(args.cliente)}-offerte-{args.da}-{args.a}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    percorso = os.path.join(LOTTI, nome)
    with open(percorso, "w") as f:
        json.dump(lotto, f, indent=1, ensure_ascii=False)
    print(f"{args.a}: {len(righe)} offerte da fare, {len(saltati)} SKU scartati")
    for sku, motivo in saltati.items():
        print(f"   scartato {sku:<20} {motivo}")
    print(f"lotto salvato: {percorso}")
    _tabella(righe)


def _tabella(righe, esiti=None):
    print(f"{'SKU':<20} {'tipo':<26} {'scheda':<11} {'prezzo':>8} {'qta':>5}  esito")
    for i, r in enumerate(righe):
        e = ""
        if esiti:
            x = esiti[i]
            codici = ",".join(str(k.get("code") or k.get("message", "")[:40]) for k in x.issues)
            e = f"{x.stato} {codici}"
        print(f"{r.sku:<20} {r.product_type:<26} {r.asin:<11} {r.prezzo:>8.2f} {r.quantita:>5}  {e}")


def _carica(percorso):
    """Due tipi di lotto: offerte (righe RigaOfferta) e schede (righe con il
    payload intero, padri prima dei figli)."""
    with open(percorso) as f:
        lotto = json.load(f)
    if lotto.get("tipo") in ("schede", "patch"):
        return lotto, lotto["righe"]
    righe = [RigaOfferta(**r) for r in lotto["righe"]]
    return lotto, righe


def _schede(lotto):
    return lotto.get("tipo") == "schede"


def _patch(lotto):
    """Lotto di aggiornamenti su SKU gia' online sulla destinazione: una
    riga = {sku, product_type, asin, attributi}; vai scrive con applica.applica
    (registro aggiornamenti, origine 'estensione'), come giro.py."""
    return lotto.get("tipo") == "patch"


def _anteprima_patch(client, seller, marketplace_id, r):
    from crea import EsitoCreazione
    body = {"productType": r["product_type"], "patches": patch_ops(r["attributi"])}
    x = client.patch(spapi_client.percorso_listing(seller, r["sku"]), json_body=body,
                     params={"marketplaceIds": marketplace_id, "mode": "VALIDATION_PREVIEW"})
    return EsitoCreazione(r["sku"], x.get("status", "INVALID"), x.get("issues", []))


def _esegui_patch(client, seller, marketplace_id, cliente, righe):
    from crea import EsitoCreazione
    from spapi_client import SpApiError
    esiti = []
    for r in righe:
        try:
            vivo = letto.leggi(client, seller, marketplace_id, r["sku"])
            mods = [Modifica(campo=k, prima=vivo.attributi.get(k), dopo=v, fonte="estensione")
                    for k, v in r["attributi"].items()]
            esito, issues, _ = applica(client, seller, marketplace_id, cliente, r["sku"],
                                       r["product_type"], mods, origine="estensione")
            esiti.append(EsitoCreazione(r["sku"], esito, issues))
        except SpApiError as e:
            esiti.append(EsitoCreazione(r["sku"], "errore", [{"message": str(e)}]))
    return esiti


def _tabella_schede(righe, esiti=None):
    print(f"{'SKU':<20} {'ruolo':<7} {'tipo':<26} {'ASIN atteso':<12}  esito")
    for i, r in enumerate(righe):
        e = ""
        if esiti:
            x = esiti[i]
            codici = ",".join(str(k.get("code") or k.get("message", "")[:40]) for k in x.issues)
            e = f"{x.stato} {codici}"
        print(f"{r['sku']:<20} {r.get('ruolo', 'patch'):<7} {r['product_type']:<26} {r.get('asin') or '-':<12}  {e}")


def prova(args):
    lotto, righe = _carica(args.lotto)
    client = spapi_client.crea_client(lotto["cliente"])
    seller = spapi_client.seller_id_di(lotto["cliente"])
    esiti = []
    for r in righe:
        if _schede(lotto):
            esiti.append(pubblica_scheda(client, seller, lotto["marketplace_id"], r["sku"], r["payload"], anteprima=True))
        elif _patch(lotto):
            esiti.append(_anteprima_patch(client, seller, lotto["marketplace_id"], r))
        else:
            esiti.append(pubblica_offerta(client, seller, lotto["marketplace_id"], r, anteprima=True))
        time.sleep(0.3)
    (_tabella_schede if (_schede(lotto) or _patch(lotto)) else _tabella)(righe, esiti)
    conta = Counter(e.stato for e in esiti)
    print("\nprova a vuoto:", dict(conta))
    for e in esiti:
        for i in e.issues:
            if i.get("severity") == "ERROR":
                print(f"   ERRORE {e.sku}: {i.get('code')} {(i.get('message') or '')[:160]}")
    ignorati = {}
    for e in esiti:
        for i in e.issues:
            if str(i.get("code")) == "90000900":
                ignorati.setdefault(e.sku, []).extend(i.get("attributeNames") or ["?"])
    if ignorati:
        print("\nattributi che Amazon ignora (90000900), per SKU:")
        for sku, nomi in ignorati.items():
            print(f"   {sku}: {', '.join(nomi)}")
    return 0 if conta.get("VALID", 0) == len(esiti) else 1


def vai(args):
    lotto, righe = _carica(args.lotto)
    if lotto["a"] != args.a:
        sys.exit(f"il lotto e' per {lotto['a']}, non per {args.a}: rifiutato")
    if lotto.get("eseguito"):
        sys.exit(f"lotto gia' eseguito il {lotto['eseguito']}: un secondo vai non si esegue")
    if not righe:
        sys.exit("lotto vuoto")
    client = spapi_client.crea_client(lotto["cliente"])
    seller = spapi_client.seller_id_di(lotto["cliente"])
    lotto["eseguito"] = datetime.now(timezone.utc).isoformat()
    with open(args.lotto, "w") as f:      # marcato PRIMA di scrivere: un crash non lascia un lotto rieseguibile
        json.dump(lotto, f, indent=1, ensure_ascii=False)
    if _schede(lotto):
        esiti = pubblica_lotto_schede(client, seller, lotto["marketplace_id"], lotto["cliente"],
                                      [(r["sku"], r["payload"]) for r in righe])
    elif _patch(lotto):
        esiti = _esegui_patch(client, seller, lotto["marketplace_id"], lotto["cliente"], righe)
    else:
        esiti = pubblica_lotto_offerte(client, seller, lotto["marketplace_id"], lotto["cliente"], righe)
    lotto["esiti"] = [{"sku": e.sku, "stato": e.stato, "issues": e.issues} for e in esiti]
    with open(args.lotto, "w") as f:
        json.dump(lotto, f, indent=1, ensure_ascii=False)
    (_tabella_schede if (_schede(lotto) or _patch(lotto)) else _tabella)(righe, esiti)
    print("\nesito:", dict(Counter(e.stato for e in esiti)))
    for e in esiti:
        for i in e.issues:
            if i.get("severity") == "ERROR" or e.stato == "errore":
                print(f"   {e.sku}: {i.get('code', '')} {(i.get('message') or '')[:160]}")


def verifica(args):
    lotto, righe = _carica(args.lotto)
    if args.attesa:
        print(f"attendo {args.attesa} s prima di rileggere (la rilettura immediata non e' affidabile)")
        time.sleep(args.attesa)
    client = spapi_client.crea_client(lotto["cliente"])
    seller = spapi_client.seller_id_di(lotto["cliente"])
    vive = {v.sku: v for v in catalogo_venditore.cerca(client, seller, lotto["marketplace_id"])}
    conta = Counter()
    print(f"{'SKU':<20} {'ASIN atteso':<12} {'ASIN vivo':<12} stati | avvisi")
    for r in righe:
        sku, asin_atteso = (r["sku"], r.get("asin") or "") if isinstance(r, dict) else (r.sku, r.asin)
        v = vive.get(sku)
        if not v:
            conta["assente"] += 1
            print(f"{sku:<20} {asin_atteso:<12} {'-':<12} ASSENTE su {lotto['a']}")
            continue
        stato = "in vendita" if v.in_vendita else ",".join(v.stati) or "nessuno stato"
        conta[stato] += 1
        asin_ok = "" if (not asin_atteso or v.asin == asin_atteso) else "  <-- ASIN DIVERSO"
        errori = [f"{i.get('code')}" for i in v.issues if i.get("severity") == "ERROR"]
        print(f"{sku:<20} {asin_atteso:<12} {v.asin:<12} {stato} | {len(v.issues)} avvisi{(' ERRORI ' + ','.join(errori)) if errori else ''}{asin_ok}")
    print("\nverifica:", dict(conta), f"| SKU vivi su {lotto['a']}: {len(vive)}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prepara"); s.add_argument("--cliente", required=True); s.add_argument("--da", default="IT"); s.add_argument("--a", required=True, choices=MARKETPLACE); s.add_argument("--cambio", type=float, help="unita' della valuta di destinazione per 1 unita' di origine"); s.set_defaults(fn=prepara)
    s = sub.add_parser("prova"); s.add_argument("--lotto", required=True); s.set_defaults(fn=prova)
    s = sub.add_parser("vai"); s.add_argument("--lotto", required=True); s.add_argument("--a", required=True, choices=MARKETPLACE); s.set_defaults(fn=vai)
    s = sub.add_parser("verifica"); s.add_argument("--lotto", required=True); s.add_argument("--attesa", type=int, default=60); s.set_defaults(fn=verifica)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
