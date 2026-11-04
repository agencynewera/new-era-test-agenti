"""Spedizione FBA fino al piazzamento confermato + etichette, SENZA trasporto.

Caso ECM 03/09/2026: il trasporto lo organizza New Era col proprio corriere,
quindi niente data di spedizione, niente preventivi, niente prenotazione.
Riusa i moduli della skill amazon-spedizioni; il cancello 🟡 è la conferma
del piazzamento (commissione quotata da Amazon, divisione irreversibile).

    python3 solo_piazzamento.py prepara lotto.yaml
    python3 solo_piazzamento.py conferma <plan_id> --piazzamento P            # anteprima
    python3 solo_piazzamento.py conferma <plan_id> --piazzamento P --vai      # 🟡
    python3 solo_piazzamento.py etichette <plan_id> [--pagina PackageLabel_A4_2]
"""
from __future__ import annotations
import argparse
import contextlib
import os
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml
import clienti
import etichette
import giri
import giro
import imballaggio
import lotto as lotto_mod
import piano as piano_mod
import piazzamento
import registro
import spapi_client
import stadio
from modello import Scatola
from spapi_client import SpApiError


def _carica_lotto(percorso: str):
    with open(percorso) as f:
        dati = yaml.safe_load(f) or {}
    cliente = str(dati.get("cliente") or "").strip()
    righe = lotto_mod._righe(dati.get("righe"), "righe del lotto")
    scatole = []
    for i, s in enumerate(dati.get("scatole") or [], start=1):
        scatole.append(Scatola(
            quantita=int(s["quantita"]), peso_kg=float(s["peso_kg"]),
            lunghezza_cm=float(s["lunghezza_cm"]), larghezza_cm=float(s["larghezza_cm"]),
            altezza_cm=float(s["altezza_cm"]),
            righe=lotto_mod._righe(s.get("righe"), f"scatola {i}"),
        ))
        lotto_mod._controlla_misure(scatole[-1])
    if not (cliente and righe and scatole):
        raise ValueError("lotto: servono cliente, righe e scatole")
    lotto_mod._controlla_contenuto(righe, scatole)
    lotto_mod.propaga_scadenze(righe, scatole)
    return cliente, righe, scatole, dati


def _testo_opzioni(client, plan_id, opzioni):
    blocchi = []
    for op in opzioni:
        spedizioni = piazzamento.leggi_spedizioni(client, plan_id, op.shipment_ids)
        commissione = ("nessuna commissione quotata" if op.commissioni is None
                       else f"{op.commissioni:.2f} {op.valuta}")
        righe = [f"Divisione {op.placement_option_id} — {len(spedizioni)} spedizione/i — "
                 f"commissione di piazzamento: {commissione}"]
        for s in spedizioni:
            righe.append(f"  · {s.shipment_id} → {s.centro_logistico}: "
                         + ", ".join(f"{r.sku}×{r.quantita}" for r in s.righe))
        blocchi.append("\n".join(righe))
    return "\n".join(blocchi)


def _salva_locale(plan_id, cliente, rif, dati):
    lotto_json = json.loads(json.dumps({**dati, "data_pronta": None}, default=str))
    giri.salva(plan_id, {"cliente": cliente, "riferimento": rif, "lotto": lotto_json,
                         "nota": "solo piazzamento: trasporto a cura di New Era, nessuna data",
                         "scelte": {}, "etichette": {}, "bloccato": None})


def prepara(client, percorso_lotto: str) -> str:
    cliente, righe, scatole, dati = _carica_lotto(percorso_lotto)
    cfg = clienti.carica_cliente(cliente)
    rif = registro.registra_tentativo(cliente, righe)
    try:
        piano = piano_mod.crea_piano(client, righe, cfg["indirizzo_origine"], cfg["marketplace_id"])
    except SpApiError as e:
        registro.registra_esito(cliente, rif, f"errore in crea_piano: {e}")
        raise
    plan_id = piano.inbound_plan_id
    _salva_locale(plan_id, cliente, rif, dati)
    registro.registra_piano(cliente, rif, plan_id, [])
    return f"Piano creato: {plan_id}\n" + _fino_al_menu(client, plan_id, cliente, rif, scatole, cfg)


def riprendi(client, plan_id: str, percorso_lotto: str, riferimento: str) -> str:
    """Piano già creato su Amazon ma senza file locale (crash dopo crea_piano):
    lega il piano alla voce di registro esistente e continua da dove è rimasto."""
    cliente, righe, scatole, dati = _carica_lotto(percorso_lotto)
    cfg = clienti.carica_cliente(cliente)
    if giri.carica(plan_id) is None:
        _salva_locale(plan_id, cliente, riferimento, dati)
    registro.registra_piano(cliente, riferimento, plan_id, [])
    return f"Piano ripreso: {plan_id}\n" + _fino_al_menu(client, plan_id, cliente, riferimento, scatole, cfg)


def _fino_al_menu(client, plan_id, cliente, rif, scatole, cfg) -> str:
    foto = stadio.leggi(client, plan_id)
    st = stadio.riconosci(foto, datetime.now(timezone.utc), etichette_scaricate=False)
    gruppi = stadio.gruppi_imballaggio_accettati(foto)
    try:
      if st == stadio.IMBALLAGGIO_DA_CONFERMARE:
        opzioni = imballaggio.genera_opzioni_imballaggio(client, plan_id)
        if len(opzioni) != 1:
            raise SpApiError("Amazon offre più imballaggi: " + "; ".join(
                f"{o.packing_option_id} ({o.descrizione})" for o in opzioni))
        scelta = opzioni[0]
        imballaggio.conferma_imballaggio(client, plan_id, scelta.packing_option_id)
        giri.aggiorna(plan_id, scelte={"packing_option_id": scelta.packing_option_id})
        gruppi = scelta.packing_group_ids
        st = stadio.SCATOLE_DA_DICHIARARE
      if st == stadio.SCATOLE_DA_DICHIARARE:
        # La stessa guardia del giro: scatole mischiate e SKU divisi fra gruppi
        # fermano prima di dichiarare un contenuto sbagliato (ECM 15/09/2026).
        per_gruppo = giro._assegna_scatole(client, plan_id, gruppi, scatole)
        imballaggio.imposta_informazioni_imballaggio(client, plan_id, per_gruppo, cfg["marketplace_id"])
        st = stadio.PIAZZAMENTO_DA_GENERARE
      if st == stadio.PIAZZAMENTO_DA_SCEGLIERE:
        opzioni_p = [piazzamento.opzione_da_amazon(o)
                     for o in stadio.piazzamenti_offerti_validi(foto, datetime.now(timezone.utc))]
      elif st == stadio.PIAZZAMENTO_DA_GENERARE:
        opzioni_p = piazzamento.genera_opzioni_piazzamento(client, plan_id)
      else:
        raise ValueError(f"piano {plan_id} allo stadio {st}: niente da preparare")
      if not opzioni_p:
          raise SpApiError("Amazon non ha generato opzioni di piazzamento")
    except (SpApiError, ValueError) as e:
        registro.registra_esito(cliente, rif, f"piano {plan_id}: {e}")
        raise
    return (_testo_opzioni(client, plan_id, opzioni_p)
            + "\n\nNessuna spesa finora. Trasporto NON quotato (a cura di New Era).")


def conferma(client, plan_id: str, placement_option_id: str, vai: bool, pagina: str) -> str:
    """Il cancello 🟡. Con `vai` un solo processo per piano alla volta (DESIGN
    §17): lo stesso lucchetto di `giro.py conferma --vai`, così le due CLI si
    escludono sullo stesso piano. Senza `vai` non si spende e non si aspetta
    nessuno."""
    lucchetto = giri.lucchetto(plan_id) if vai else contextlib.nullcontext()
    with lucchetto:
        return _conferma(client, plan_id, placement_option_id, vai, pagina)


def _conferma(client, plan_id: str, placement_option_id: str, vai: bool, pagina: str) -> str:
    locale = giri.carica(plan_id)
    if locale is None:
        raise ValueError(f"nessun file locale per {plan_id}")
    cliente, rif = locale["cliente"], locale["riferimento"]
    adesso = datetime.now(timezone.utc)
    foto = stadio.leggi(client, plan_id)
    valide = {o["placementOptionId"]: o for o in stadio.piazzamenti_offerti_validi(foto, adesso)}
    if placement_option_id not in valide:
        raise ValueError(f"piazzamento {placement_option_id} non tra le opzioni valide "
                         f"({', '.join(valide) or 'nessuna'})")
    op = piazzamento.opzione_da_amazon(valide[placement_option_id])
    testo = _testo_opzioni(client, plan_id, [op])
    testo += "\nTrasporto: NON prenotato, nessuna data (a cura di New Era)."
    if not vai:
        return testo + "\n(anteprima: nessuna scrittura fatta; ripeti con --vai per confermare il piazzamento)"
    try:
        piazzamento.conferma_piazzamento(client, plan_id, op.placement_option_id)
        registro.registra_piazzamento_confermato(cliente, rif, op.placement_option_id, op.commissioni, op.valuta)
        giri.aggiorna(plan_id, scelte={**locale["scelte"], "placement_option_id": op.placement_option_id})
        spedizioni = piazzamento.leggi_spedizioni(client, plan_id, op.shipment_ids)
        registro.registra_piano(cliente, rif, plan_id, spedizioni)
    except SpApiError as e:
        registro.registra_esito(cliente, rif, str(e))
        if "ESITO INCERTO" in str(e):
            giri.aggiorna(plan_id, bloccato=str(e))
        raise
    righe = [testo, "Piazzamento CONFERMATO."]
    for s in spedizioni:
        righe.append(f"  {s.shipment_id} → {s.centro_logistico}: codice FBA {s.shipment_confirmation_id}"
                     + (f" · ASN {s.amazon_reference_id}" if s.amazon_reference_id else ""))
    righe.append(scarica(client, plan_id, pagina))
    return "\n".join(righe)


def scarica(client, plan_id: str, pagina: str) -> str:
    locale = giri.carica(plan_id)
    cliente, rif = locale["cliente"], locale["riferimento"]
    foto = stadio.leggi(client, plan_id)
    accettato = stadio.piazzamento_accettato(foto)
    if not accettato:
        raise ValueError("piazzamento non ancora confermato: niente etichette")
    spedizioni = piazzamento.leggi_spedizioni(client, plan_id, accettato["shipmentIds"])
    righe, scaricate = [], {}
    for s in spedizioni:
        try:
            cartoni = etichette.leggi_cartoni(client, plan_id, s.shipment_id)
            percorso = etichette.scarica_etichette(client, s.shipment_confirmation_id, cliente,
                                                   tipo_pagina=pagina, cartoni=cartoni)
        except SpApiError as e:
            registro.registra_esito(cliente, rif, f"piazzamento confermato, etichette non scaricate: {e}")
            righe.append(f"  etichette {s.shipment_confirmation_id}: NON scaricate — {e}")
            continue
        registro.registra_etichette(cliente, rif, s.shipment_id, s.shipment_confirmation_id, percorso)
        scaricate[s.shipment_id] = [percorso]
        righe.append(f"  etichette {s.shipment_confirmation_id} ({len(cartoni)} cartoni, {pagina}): {percorso}")
    if scaricate:
        giri.aggiorna(plan_id, etichette=scaricate)
        registro.registra_esito(cliente, rif, "piazzamento confermato, etichette scaricate; trasporto a cura di New Era")
    return "\n".join(righe)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="verbo", required=True)
    a = sub.add_parser("prepara"); a.add_argument("lotto")
    r = sub.add_parser("riprendi"); r.add_argument("plan_id"); r.add_argument("lotto"); r.add_argument("--riferimento", required=True)
    c = sub.add_parser("conferma"); c.add_argument("plan_id"); c.add_argument("--piazzamento", required=True)
    c.add_argument("--vai", action="store_true"); c.add_argument("--pagina", default="PackageLabel_A4_4")
    e = sub.add_parser("etichette"); e.add_argument("plan_id"); e.add_argument("--pagina", default="PackageLabel_A4_4")
    args = p.parse_args(argv)
    try:
        if args.verbo == "prepara":
            with open(args.lotto) as f:
                cliente = (yaml.safe_load(f) or {}).get("cliente")
            print(prepara(spapi_client.crea_client(cliente), args.lotto))
        elif args.verbo == "riprendi":
            with open(args.lotto) as f:
                cliente = (yaml.safe_load(f) or {}).get("cliente")
            print(riprendi(spapi_client.crea_client(cliente), args.plan_id, args.lotto, args.riferimento))
        else:
            cliente = giri.carica(args.plan_id)["cliente"]
            client = spapi_client.crea_client(cliente)
            if args.verbo == "conferma":
                print(conferma(client, args.plan_id, args.piazzamento, args.vai, args.pagina))
            else:
                print(scarica(client, args.plan_id, args.pagina))
        return 0
    except (ValueError, SpApiError) as e:
        print(f"ERRORE: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
