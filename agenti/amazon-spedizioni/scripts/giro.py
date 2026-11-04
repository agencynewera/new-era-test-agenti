from __future__ import annotations
import argparse
from collections import Counter
import contextlib
import os
import sys
import time
from datetime import datetime, timezone
import yaml

import anteprima
import archivio
import avviso_mail
import clienti
import conferma as conferma_mod
import corrieri
import etichette
import giri
import imballaggio
import lotto as lotto_mod
import modelli as modelli_mod
import piano as piano_mod
import piazzamento
import registro
import ricorrenza
import scatole as scatole_mod
import spapi_client
import stadio
import stato_stock
import stima as stima_mod
import stock as stock_mod
import tracking as tracking_mod
from modello import PianoSpedizione, Scatola
from spapi_client import SpApiError

_BASE = "/inbound/fba/2024-03-20/inboundPlans"


# ---------------------------------------------------------------- errori ----

def _fallito(plan_id: str | None, locale: dict | None, errore: Exception) -> None:
    """Ogni errore lascia traccia: nel registro sempre, nel file locale se è
    un esito incerto (§6): da lì in poi riprendi/conferma rifiutano finché
    Mariano non ha guardato Seller Central."""
    if locale:
        registro.registra_esito(locale["cliente"], locale["riferimento"], str(errore))
        if plan_id and "ESITO INCERTO" in str(errore):
            giri.aggiorna(plan_id, bloccato=str(errore))


def _controlla_blocco(plan_id: str, locale: dict, verificato: bool) -> None:
    if not locale.get("bloccato"):
        return
    if not verificato:
        raise ValueError(
            f"piano {plan_id} bloccato da un esito incerto: {locale['bloccato']}. "
            "Verifica su Seller Central com'è finita, poi ripeti con --verificato"
        )
    giri.aggiorna(plan_id, bloccato=None)
    locale["bloccato"] = None


def _nuovo_locale(cliente: str, riferimento: str, lotto: lotto_mod.Lotto) -> dict:
    return {"cliente": cliente, "riferimento": riferimento,
            "lotto": lotto_mod.a_dizionario(lotto), "scelte": {}, "etichette": {}, "bloccato": None}


def _controlla_scatole(lotto: lotto_mod.Lotto, scatole_nuove: bool) -> str:
    """DESIGN §13: PRIMA di registrare il tentativo, del file locale e di ogni
    chiamata ad Amazon, perché una scatola dichiarata non si corregge — resta
    una bozza da abbandonare. Senza istantanea si procede: il controllo è un
    aiuto, non un prerequisito, e un cliente nuovo non ha storia."""
    istantanea = scatole_mod.carica_istantanea(lotto.cliente)
    if istantanea is None:
        return (f"nessuno storico letto per {lotto.cliente}: `giro.py scatole \"{lotto.cliente}\"` "
                "per averlo. Scatole non controllate.\n")
    esito = scatole_mod.confronta(lotto, istantanea)
    if esito.scostamenti and not scatole_nuove:
        raise ValueError(
            "scatole diverse dallo storico del cliente:\n  " + "\n  ".join(esito.scostamenti)
            + "\nSe l'imballo è cambiato davvero, ripeti con --scatole-nuove")
    righe = esito.scostamenti + esito.note
    if not righe:
        return f"Scatole come nello storico del {istantanea['letto_il'][:10]}.\n"
    return (f"Scatole confrontate con lo storico del {istantanea['letto_il'][:10]}:\n  "
            + "\n  ".join(righe) + "\n")


# --------------------------------------------------------------- prepara ----

def prepara(client, lotto: lotto_mod.Lotto, adesso: datetime | None = None,
            scatole_nuove: bool = False) -> tuple[str, str]:
    """Passi 2-10 di SKILL.md: dal lotto al menù. Le scatole si controllano
    contro lo storico prima di tutto; il lotto si registra PRIMA di creare il
    piano; il file locale si scrive appena Amazon torna il plan_id, così anche
    un crash subito dopo lascia un piano riprendibile."""
    nota = _controlla_scatole(lotto, scatole_nuove)
    cfg = clienti.carica_cliente(lotto.cliente)
    riferimento = registro.registra_tentativo(lotto.cliente, lotto.righe)
    try:
        piano = piano_mod.crea_piano(client, lotto.righe, cfg["indirizzo_origine"], cfg["marketplace_id"])
    except SpApiError as e:
        registro.registra_esito(lotto.cliente, riferimento, f"errore in crea_piano: {e}")
        raise
    plan_id = piano.inbound_plan_id
    giri.salva(plan_id, _nuovo_locale(lotto.cliente, riferimento, lotto))
    registro.registra_piano(lotto.cliente, riferimento, plan_id, [])
    return plan_id, nota + riprendi(client, plan_id, adesso=adesso)


# -------------------------------------------------------------- riprendi ----

def riprendi(client, plan_id: str, lotto: lotto_mod.Lotto | None = None,
             packing_option_id: str | None = None, verificato: bool = False,
             adesso: datetime | None = None, scatole_nuove: bool = False) -> str:
    """Legge lo stadio da Amazon e fa solo ciò che manca fino al menù. Le
    opzioni scadute si rigenerano da sole: non costano."""
    locale = giri.carica(plan_id)
    avviso = ""
    if locale is None:
        if lotto is None:
            raise ValueError(
                f"nessun file locale per {plan_id} (piano non creato da giro.py): "
                "passa --lotto lotto.yaml con scatole e data pronta"
            )
        avviso = _controlla_scatole(lotto, scatole_nuove)
        riferimento = registro.registra_tentativo(lotto.cliente, lotto.righe)
        registro.registra_piano(lotto.cliente, riferimento, plan_id, [])
        locale = _nuovo_locale(lotto.cliente, riferimento, lotto)
        giri.salva(plan_id, locale)
    else:
        # Il blocco si controlla prima di toccare il file: un comando respinto
        # non deve lasciare un lotto diverso da quello con cui il piano si è
        # bloccato.
        _controlla_blocco(plan_id, locale, verificato)
        if lotto is not None:
            # Il file locale è solo la copia di lavoro del lotto: la verità è il
            # file che passa Mariano. Ignorarlo in silenzio (perché il piano è già
            # noto) farebbe dichiarare ad Amazon scatole che non sono più quelle.
            avviso = _controlla_scatole(lotto, scatole_nuove)
            locale = giri.aggiorna(plan_id, lotto=lotto_mod.a_dizionario(lotto))
            avviso += (f"Lotto aggiornato dal file: {sum(s.quantita for s in lotto.scatole)} "
                       f"scatole, data pronta {lotto.data_pronta}\n")
    lotto = lotto_mod.da_dizionario(locale["lotto"])
    cfg = clienti.carica_cliente(locale["cliente"])
    adesso = adesso or datetime.now(timezone.utc)
    try:
        return avviso + _fino_al_menu(client, plan_id, locale, lotto, cfg, adesso, packing_option_id)
    except SpApiError as e:
        _fallito(plan_id, locale, e)
        raise


def _fino_al_menu(client, plan_id, locale, lotto, cfg, adesso, packing_option_id) -> str:
    foto = stadio.leggi(client, plan_id)
    st = stadio.riconosci(foto, adesso, etichette_scaricate=bool(locale.get("etichette")))
    gruppi: list[str] = stadio.gruppi_imballaggio_accettati(foto)

    if st == stadio.IMBALLAGGIO_DA_CONFERMARE:
        opzioni = imballaggio.genera_opzioni_imballaggio(client, plan_id)
        scelta = _scegli_imballaggio(opzioni, packing_option_id)
        imballaggio.conferma_imballaggio(client, plan_id, scelta.packing_option_id)
        giri.aggiorna(plan_id, scelte={**locale["scelte"], "packing_option_id": scelta.packing_option_id})
        gruppi = scelta.packing_group_ids
        st = stadio.SCATOLE_DA_DICHIARARE

    if st == stadio.SCATOLE_DA_DICHIARARE:
        per_gruppo = _assegna_scatole(client, plan_id, gruppi, lotto.scatole)
        imballaggio.imposta_informazioni_imballaggio(client, plan_id, per_gruppo, cfg["marketplace_id"])
        st = stadio.PIAZZAMENTO_DA_GENERARE

    if st == stadio.PIAZZAMENTO_DA_GENERARE:
        opzioni_piazzamento = piazzamento.genera_opzioni_piazzamento(client, plan_id)
    elif st == stadio.PIAZZAMENTO_DA_SCEGLIERE:
        opzioni_piazzamento = _da_amazon(stadio.piazzamenti_offerti_validi(foto, adesso))
    elif st == stadio.TRASPORTO_DA_SCEGLIERE:
        opzioni_piazzamento = _da_amazon([stadio.piazzamento_accettato(foto)])
    elif st == stadio.TRASPORTO_PRENOTATO:
        return (f"Piano {plan_id}: corriere già prenotato. Per le etichette: "
                f"giro.py conferma {plan_id} --vai")
    else:
        return f"Piano {plan_id}: completato, etichette in {locale.get('etichette')}"

    # Zero divisioni vuol dire che Amazon non sa cosa spedire (scatole o
    # articoli non validi): senza questo il menù finirebbe su un IndexError
    # che non dice perché, e l'errore non passerebbe da `_fallito`.
    if not opzioni_piazzamento:
        raise SpApiError(
            f"Amazon non ha generato opzioni di piazzamento per il piano {plan_id}: "
            "controlla scatole e articoli su Seller Central"
        )

    return _menu(client, plan_id, opzioni_piazzamento, lotto, cfg, st == stadio.TRASPORTO_DA_SCEGLIERE)


def _scegli_imballaggio(opzioni, packing_option_id):
    if packing_option_id:
        for o in opzioni:
            if o.packing_option_id == packing_option_id:
                return o
        raise ValueError(f"opzione di imballaggio {packing_option_id} non tra quelle offerte")
    if len(opzioni) == 1:
        return opzioni[0]
    elenco = "; ".join(f"{o.packing_option_id} ({o.descrizione})" for o in opzioni)
    raise ValueError(f"Amazon offre più imballaggi: {elenco}. Scegli con --imballaggio <id>")


def _assegna_scatole(client, plan_id, gruppi: list[str], scatole: list[Scatola]) -> dict[str, list[Scatola]]:
    """Con un gruppo solo tutte le scatole vanno lì; con più gruppi si
    assegnano per SKU, e una scatola che mischia gruppi ferma il giro. Anche
    uno SKU che Amazon divide fra più gruppi ferma il giro: le scatole
    standard sono per SKU, non per gruppo, e assegnarle tutte a un gruppo
    dichiarerebbe un contenuto sbagliato (ECM, 15/09/2026)."""
    if not gruppi:
        raise ValueError(f"piano {plan_id}: imballaggio accettato ma senza gruppi")
    if len(gruppi) == 1:
        return {gruppi[0]: list(scatole)}
    articoli = {g: imballaggio.leggi_articoli_gruppo(client, plan_id, g) for g in gruppi}
    gruppi_di: dict[str, list[str]] = {}
    for g in gruppi:
        for sku in articoli[g]:
            gruppi_di.setdefault(sku, []).append(g)
    divisi = [sku for sku, gs in gruppi_di.items() if len(gs) > 1]
    if divisi:
        dettaglio = "; ".join(
            f"{sku} ({', '.join(f'{g}: {articoli[g][sku]}' for g in gruppi_di[sku])})" for sku in divisi)
        raise ValueError(
            f"l'imballaggio scelto divide gli stessi SKU fra più gruppi: {dettaglio}. "
            "Le scatole vanno rifatte per gruppo, oppure scegli con --imballaggio "
            "l'opzione a gruppo unico"
        )
    gruppo_di = {sku: gs[0] for sku, gs in gruppi_di.items()}
    per_gruppo: dict[str, list[Scatola]] = {g: [] for g in gruppi}
    for i, s in enumerate(scatole, start=1):
        destinazioni = {gruppo_di.get(r.sku) for r in s.righe}
        if len(destinazioni) != 1 or None in destinazioni:
            raise ValueError(
                f"la scatola {i} contiene SKU di gruppi di imballaggio diversi "
                f"({', '.join(r.sku for r in s.righe)}): Amazon li vuole in scatole separate"
            )
        per_gruppo[destinazioni.pop()].append(s)
    return per_gruppo


def _da_amazon(opzioni_grezze: list[dict]):
    """Le stesse OpzionePiazzamento di piazzamento.genera_opzioni_piazzamento,
    ma da opzioni già lette (nessuna POST)."""
    return [piazzamento.opzione_da_amazon(o) for o in opzioni_grezze]


def _percorso_elenco_corrieri(cliente: str, plan_id: str) -> str:
    # Il nome cliente lo scrive Mariano: passa dal sanificatore del registro,
    # o un "../" porterebbe il file (e la sua cancellazione) fuori dal Desktop.
    return os.path.expanduser(f"~/Desktop/Corrieri-{registro._nome_cliente(cliente)}-{plan_id}.txt")


def _scrivi_elenco_corrieri(cliente: str, plan_id: str, opzioni) -> str:
    """L'elenco completo dei corrieri propri (id, spedizione, modalità,
    codice), sul Desktop come ogni consegna: Mariano cerca lì il codice del
    suo corriere e passa l'id a `conferma --trasporto`."""
    percorso = _percorso_elenco_corrieri(cliente, plan_id)
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    righe = [f"{o.transportation_option_id}\t{o.shipment_id}\t{o.modalita}\t{o.corriere}" for o in opzioni]
    with open(percorso, "a") as f:
        f.write("\n".join(righe) + "\n")
    return percorso


def _menu(client, plan_id, opzioni_piazzamento, lotto, cfg, piazzamento_gia_confermato: bool) -> str:
    pallet = lotto_mod.pallet_amazon(lotto) or None
    cfg_cliente = lotto.cliente
    blocchi = []
    # Un file nuovo per ogni menù: le opzioni cambiano a ogni rigenerazione.
    elenco = _percorso_elenco_corrieri(cfg_cliente, plan_id)
    if os.path.exists(elenco):
        os.remove(elenco)
    for op in opzioni_piazzamento:
        spedizioni = piazzamento.leggi_spedizioni(client, plan_id, op.shipment_ids)
        opzioni = corrieri.genera_opzioni_trasporto(
            client, plan_id, op.placement_option_id, op.shipment_ids, lotto.data_pronta,
            contatto=cfg["contatto"], pallet=pallet)
        finestre_testo = []
        for s in spedizioni:
            sue = [o for o in opzioni if o.shipment_id == s.shipment_id]
            # In chat solo le convenzionate (hanno un prezzo): dal vivo i corrieri
            # propri erano 273 per spedizione, l'elenco intasava il menù.
            s.opzioni_trasporto = [o for o in sue if o.convenzionato]
            proprie = [o for o in sue if not o.convenzionato]
            if proprie:
                per_modo = Counter(o.modalita or "modalità non indicata" for o in proprie)
                finestre_testo.append(
                    f"  · corriere proprio: {len(proprie)} opzioni ("
                    + ", ".join(f"{n} {modo}" for modo, n in per_modo.items())
                    + f") — id e codici in {_scrivi_elenco_corrieri(cfg_cliente, plan_id, proprie)}")
            if any(o.richiede_finestra_consegna for o in sue):
                # SKILL.md passo 9: le finestre si generano qui, così `conferma`
                # senza --vai non fa nessuna POST
                finestre = corrieri.genera_finestre_consegna(client, plan_id, s.shipment_id)
                finestre_testo.append(f"  finestre di consegna per {s.shipment_id} (corriere proprio): " + ", ".join(
                    f"{f.delivery_window_option_id} {f.inizio}→{f.fine}" for f in finestre))
        commissione = ("nessuna commissione quotata" if op.commissioni is None
                       else f"{op.commissioni:.2f} {op.valuta}")
        stato = "CONFERMATA" if piazzamento_gia_confermato else "offerta"
        blocchi.append(
            f"Divisione {op.placement_option_id} ({stato}) — commissione di piazzamento: {commissione}\n"
            + anteprima.riepilogo(PianoSpedizione(plan_id, spedizioni, placement_option_id=op.placement_option_id))
            + ("\n" + "\n".join(finestre_testo) if finestre_testo else "")
        )
    esempio = opzioni_piazzamento[0]
    sid = esempio.shipment_ids[0] if esempio.shipment_ids else "<shipment_id>"
    return "\n\n".join(blocchi) + (
        f"\n\nNessuna spesa finora. Per prenotare:\n"
        f"  giro.py conferma {plan_id} --piazzamento {esempio.placement_option_id} "
        f"--trasporto {sid}=<transportation_option_id> [--finestra {sid}=<id>] --vai"
    )


# -------------------------------------------------------------- conferma ----

def _spedizioni_da_registrare(cliente: str, rif: str, spedizioni: list) -> list:
    """Per il registro, non per `_etichette`: il rientro a `TRASPORTO_PRENOTATO`
    rilegge da Amazon `Spedizione` nuove, senza `trasporto_confermato` né
    `etichette_path` — quella prenotazione è già pagata e irreversibile, e
    `registra_piano` sostituisce l'intera lista delle spedizioni della voce.
    Se una spedizione è già nel registro, si tiene la voce esistente (così
    non perde ciò che sa) aggiornando solo `shipment_confirmation_id` se
    mancava; altrimenti si passa la `Spedizione` appena riletta."""
    esistenti = {v["shipment_id"]: v for v in registro.voce(cliente, rif)["spedizioni"]}
    risultato = []
    for s in spedizioni:
        vecchia = esistenti.get(s.shipment_id)
        if vecchia is None:
            risultato.append(s)
            continue
        if not vecchia.get("shipment_confirmation_id"):
            vecchia["shipment_confirmation_id"] = s.shipment_confirmation_id
        if not vecchia.get("amazon_reference_id"):
            vecchia["amazon_reference_id"] = s.amazon_reference_id
        risultato.append(vecchia)
    return risultato


def _ripesca_trasporto_prenotato(client, plan_id, foto, accettato, spedizioni) -> None:
    """Il rientro a `TRASPORTO_PRENOTATO` rilegge `Spedizione` senza
    `trasporto_confermato`, ma il formato delle etichette per cartone dipende
    dal corriere (`etichette.formato_cartoni`). Lo dice Amazon, non la memoria:
    `selectedTransportationOptionId` sulla spedizione, cercato tra le opzioni
    del piazzamento accettato. Se non si ritrova, resta `None` e le etichette
    escono nel formato del corriere proprio."""
    opzioni = {o.transportation_option_id: o
               for o in corrieri.leggi_opzioni_trasporto(client, plan_id, accettato["placementOptionId"])}
    for s in spedizioni:
        scelto = (foto.spedizioni.get(s.shipment_id) or {}).get("selectedTransportationOptionId")
        s.trasporto_confermato = opzioni.get(scelto)


def _controlla_finestra(client, plan_id, shipment_id, candidata, voluta, finestre) -> None:
    """La finestra si controlla PRIMA del "vai": la commissione di piazzamento
    si paga per prima, e un id sbagliato scoperto dopo lascia una spesa fatta
    e un piano a metà. Non basta che una finestra sia stata passata: dev'essere
    una di quelle che Amazon offre davvero per questa spedizione."""
    if not candidata.richiede_finestra_consegna:
        if shipment_id in finestre:
            raise ValueError(
                f"--finestra {shipment_id}={finestre[shipment_id]}: il trasporto scelto "
                f"({voluta}) non richiede una finestra di consegna"
            )
        return
    if shipment_id not in finestre:
        raise ValueError(
            f"{voluta} richiede una finestra di consegna per {shipment_id}: "
            f"scegline una tra quelle del menù con --finestra {shipment_id}=<id>"
        )
    offerte = [f.delivery_window_option_id
               for f in corrieri.leggi_finestre_consegna(client, plan_id, shipment_id)]
    if finestre[shipment_id] not in offerte:
        raise ValueError(
            f"finestra {finestre[shipment_id]} non tra quelle offerte per {shipment_id} "
            f"({', '.join(offerte) or 'nessuna'}): rifai `giro.py riprendi {plan_id}`"
        )


def _pallet_per_spedizione(pallet_etichette, spedizioni) -> dict[str, int | None]:
    """Quanti bancali per spedizione, per le etichette pallet. Un numero solo
    vale per una spedizione sola: su un piano diviso ogni spedizione ha i
    suoi, e lo stesso N per tutte stamperebbe etichette pallet anche per
    quella che viaggia in cartoni. Si controlla prima del "vai", come le
    finestre: un errore scoperto dopo lascerebbe una spesa fatta."""
    ids = [s.shipment_id for s in spedizioni]
    if not pallet_etichette:
        return {sid: None for sid in ids}
    if isinstance(pallet_etichette, dict):
        estranee = sorted(set(pallet_etichette) - set(ids))
        if estranee:
            raise ValueError(
                f"--pallet {', '.join(estranee)}: non fa parte del piano ({', '.join(ids)})"
            )
        return {sid: pallet_etichette.get(sid) for sid in ids}
    if len(ids) > 1:
        raise ValueError(
            f"--pallet {pallet_etichette} vale per una spedizione sola, e il piano è diviso "
            f"in {', '.join(ids)}: usa " + " ".join(f"--pallet {sid}=N" for sid in ids)
        )
    return {ids[0]: pallet_etichette}


def conferma(client, plan_id: str, placement_option_id: str | None, trasporto: dict[str, str],
             finestre: dict[str, str] | None = None, vai: bool = False,
             pallet_etichette: int | dict[str, int] | None = None, verificato: bool = False,
             adesso: datetime | None = None) -> str:
    """Il cancello 🟡. Con `vai` un solo processo per piano alla volta (DESIGN
    §17): due `--vai` insieme leggerebbero la stessa foto e prenoterebbero
    entrambi. Senza `vai` non si spende e non si aspetta nessuno."""
    lucchetto = giri.lucchetto(plan_id) if vai else contextlib.nullcontext()
    with lucchetto:
        return _conferma(client, plan_id, placement_option_id, trasporto, finestre, vai,
                         pallet_etichette, verificato, adesso)


def _conferma(client, plan_id: str, placement_option_id: str | None, trasporto: dict[str, str],
             finestre: dict[str, str] | None = None, vai: bool = False,
             pallet_etichette: int | dict[str, int] | None = None, verificato: bool = False,
             adesso: datetime | None = None) -> str:
    """Il cancello 🟡 (SKILL.md passi 10-17). Senza `vai` stampa il testo di
    `riepilogo_conferma` e NON fa nessuna POST. Con `vai` spende, nell'ordine
    fissato: piazzamento → registro → rilettura → finestre → trasporto →
    registro → etichette → esito."""
    finestre = finestre or {}
    locale = giri.carica(plan_id)
    if locale is None:
        raise ValueError(f"nessun file locale per {plan_id}: prima `giro.py riprendi {plan_id} --lotto lotto.yaml`")
    _controlla_blocco(plan_id, locale, verificato)
    cliente, rif = locale["cliente"], locale["riferimento"]
    cfg = clienti.carica_cliente(cliente)
    adesso = adesso or datetime.now(timezone.utc)

    try:
        foto = stadio.leggi(client, plan_id)
        st = stadio.riconosci(foto, adesso, etichette_scaricate=bool(locale.get("etichette")))
        if st in (stadio.TRASPORTO_PRENOTATO, stadio.COMPLETATO):
            # Anche il solo scaricare le etichette passa dal cancello: scrive
            # nel registro e nel file locale, e a COMPLETATO serve a riprendere
            # un PDF perso — non è una lettura innocua da fare per inerzia.
            if not vai:
                return (f"Piano {plan_id}: corriere già prenotato. Nessuna scrittura. "
                        f"Per scaricare le etichette: giro.py conferma {plan_id} --vai")
            accettato = stadio.piazzamento_accettato(foto)
            spedizioni = piazzamento.leggi_spedizioni(client, plan_id, accettato["shipmentIds"])
            pallet = _pallet_per_spedizione(pallet_etichette, spedizioni)
            _ripesca_trasporto_prenotato(client, plan_id, foto, accettato, spedizioni)
            # registra_etichette cerca la spedizione nella voce: le rilette vanno scritte prima.
            # Ma non si sovrascrive: una prenotazione già pagata (trasporto_confermato,
            # shipment_confirmation_id, etichette_path) non deve mai perdere la sua traccia
            # solo perché questo è il percorso di ripresa dopo un errore a metà etichette.
            registro.registra_piano(cliente, rif, plan_id, _spedizioni_da_registrare(cliente, rif, spedizioni))
            return _etichette(client, plan_id, locale, spedizioni, pallet, cfg.get("cartella"), adesso)
        if st not in (stadio.PIAZZAMENTO_DA_SCEGLIERE, stadio.TRASPORTO_DA_SCEGLIERE):
            raise ValueError(f"il piano {plan_id} è allo stadio {st}: prima `giro.py riprendi {plan_id}`")

        # --- l'opzione di piazzamento, valida e coerente con lo stadio ---
        if st == stadio.TRASPORTO_DA_SCEGLIERE:
            grezza = stadio.piazzamento_accettato(foto)
            if placement_option_id and placement_option_id != grezza["placementOptionId"]:
                raise ValueError(f"il piazzamento è già confermato: {grezza['placementOptionId']}, non {placement_option_id}")
        else:
            valide = {o["placementOptionId"]: o for o in stadio.piazzamenti_offerti_validi(foto, adesso)}
            # Un piazzamento dimenticato non è un piazzamento sbagliato: dirlo
            # come "None non è tra le opzioni valide" manda a cercare un id che
            # non è mai stato scritto.
            if placement_option_id is None:
                raise ValueError(f"--piazzamento mancante: scegli tra {', '.join(valide) or 'nessuna'}")
            if placement_option_id not in valide:
                raise ValueError(
                    f"piazzamento {placement_option_id} non tra le opzioni valide "
                    f"({', '.join(valide) or 'nessuna'}): rifai `giro.py riprendi {plan_id}`"
                )
            grezza = valide[placement_option_id]
        op = _da_amazon([grezza])[0]

        # --- le opzioni di trasporto scelte, una per spedizione, non scadute ---
        spedizioni = piazzamento.leggi_spedizioni(client, plan_id, op.shipment_ids)
        pallet = _pallet_per_spedizione(pallet_etichette, spedizioni)
        disponibili = corrieri.leggi_opzioni_trasporto(client, plan_id, op.placement_option_id)
        scelte: dict = {}
        for s in spedizioni:
            voluta = trasporto.get(s.shipment_id)
            candidata = next((o for o in disponibili
                              if o.shipment_id == s.shipment_id and o.transportation_option_id == voluta), None)
            if candidata is None:
                offerte = ", ".join(o.transportation_option_id for o in disponibili if o.shipment_id == s.shipment_id)
                raise ValueError(f"trasporto {voluta!r} non tra le opzioni di {s.shipment_id} ({offerte or 'nessuna'})")
            if stadio.scaduta(candidata.scadenza, adesso):
                raise ValueError(f"il preventivo {voluta} di {s.shipment_id} è scaduto: rifai `giro.py riprendi {plan_id}`")
            _controlla_finestra(client, plan_id, s.shipment_id, candidata, voluta, finestre)
            scelte[s.shipment_id] = candidata
        estranee = set(finestre) - {s.shipment_id for s in spedizioni}
        if estranee:
            raise ValueError(
                f"--finestra {', '.join(sorted(estranee))}: non fa parte della divisione "
                f"{op.placement_option_id} ({', '.join(op.shipment_ids)})"
            )

        commissione = "nessuna commissione quotata" if op.commissioni is None else f"{op.commissioni:.2f} {op.valuta}"
        testo = "\n".join(anteprima.riepilogo_conferma(s, scelte[s.shipment_id]) for s in spedizioni)
        testo += f"\nCommissione di piazzamento ({op.placement_option_id}): {commissione}"
        if not vai:
            return testo + "\n(anteprima: nessuna scrittura fatta; ripeti con --vai per prenotare)"

        # ------------------------------ da qui si spende ------------------------------
        if st == stadio.PIAZZAMENTO_DA_SCEGLIERE:
            piazzamento.conferma_piazzamento(client, plan_id, op.placement_option_id)
            registro.registra_piazzamento_confermato(cliente, rif, op.placement_option_id, op.commissioni, op.valuta)
            giri.aggiorna(plan_id, scelte={**locale["scelte"], "placement_option_id": op.placement_option_id})
        elif not registro.voce(cliente, rif).get("piazzamento_confermato"):
            # Piazzamento confermato fuori da giro.py (Seller Central, un giro
            # non registrato): la commissione è spesa comunque, e senza questa
            # riga il lotto risulterebbe gratis. `ora` è quando lo si è
            # appreso. Se la riga c'è già (ripresa dopo un trasporto fallito)
            # resta quella del giro che ha pagato.
            registro.registra_piazzamento_confermato(cliente, rif, op.placement_option_id, op.commissioni, op.valuta)
        spedizioni = piazzamento.leggi_spedizioni(client, plan_id, op.shipment_ids)
        registro.registra_piano(cliente, rif, plan_id, spedizioni)
        for sid, dw in finestre.items():
            conferma_mod.conferma_finestra_consegna(client, plan_id, sid, dw)
        conferma_mod.conferma_trasporto(client, plan_id, list(scelte.values()), contatto=cfg["contatto"])
        for s in spedizioni:
            s.trasporto_confermato = scelte[s.shipment_id]
        registro.registra_trasporto_confermato(cliente, rif, scelte)
        giri.aggiorna(plan_id, scelte={**giri.carica(plan_id)["scelte"],
                                       "trasporto": {sid: o.transportation_option_id for sid, o in scelte.items()},
                                       "finestre": finestre})
        return testo + "\n" + _etichette(client, plan_id, locale, spedizioni, pallet, cfg.get("cartella"), adesso)
    except SpApiError as e:
        _fallito(plan_id, locale, e)
        raise


def _etichette(client, plan_id, locale, spedizioni, pallet: dict[str, int | None],
               cartella_cliente: str | None, adesso: datetime) -> str:
    cliente, rif = locale["cliente"], locale["riferimento"]
    scaricate: dict[str, list[str]] = {}
    avvisi = []
    for s in spedizioni:
        # Le etichette vanno nella cartella PRIME del cliente (deciso 07/09):
        # Mariano le riprende da lì. Se la cartella non c'è la spedizione è
        # comunque già pagata: restano sul Desktop e il giro dice perché.
        try:
            cartella = archivio.cartella_spedizione(cartella_cliente, s.shipment_confirmation_id, adesso)
        except ValueError as e:
            cartella = None
            avvisi.append(f"Etichette di {s.shipment_confirmation_id} solo sul Desktop: {e}")
        percorso_pallet = None
        if pallet.get(s.shipment_id):
            # scarica_etichette salva sempre con lo stesso nome: il PDF pallet si
            # scarica PRIMA e si rinomina subito, così i cartoni non lo sovrascrivono
            temporaneo = etichette.scarica_etichette(client, s.shipment_confirmation_id, cliente,
                                                     tipo_etichetta="PALLET", numero_pallet=pallet[s.shipment_id],
                                                     cartella=cartella)
            percorso_pallet = temporaneo.replace(".pdf", "-Pallet.pdf")
            os.replace(temporaneo, percorso_pallet)
        cartoni = etichette.leggi_cartoni(client, plan_id, s.shipment_id)
        convenzionato = bool(s.trasporto_confermato and s.trasporto_confermato.convenzionato)
        percorso = etichette.scarica_etichette(client, s.shipment_confirmation_id, cliente, cartoni=cartoni,
                                               tipo_pagina=etichette.formato_cartoni(convenzionato),
                                               cartella=cartella)
        registro.registra_etichette(cliente, rif, s.shipment_id, s.shipment_confirmation_id, percorso)
        scaricate[s.shipment_id] = [percorso] + ([percorso_pallet] if percorso_pallet else [])
    giri.aggiorna(plan_id, etichette=scaricate)
    registro.registra_esito(cliente, rif, "completato")
    righe = [f"Prenotato. Spedizione {s.shipment_id} → {s.centro_logistico}: {s.shipment_confirmation_id}"
             + (f" (ASN {s.amazon_reference_id})" if s.amazon_reference_id else "")
             for s in spedizioni]
    righe += [f"Etichette: {p}" for elenco in scaricate.values() for p in elenco]
    righe += avvisi
    # Chi imballa deve sapere il giorno dichiarato ad Amazon (è il
    # readyToShipWindow.start mandato coi preventivi) e, col convenzionato a
    # pacchi, che Amazon ha venduto le etichette prepagate e NON il ritiro:
    # Maricart 08/09/2026, UPS non è passato. Vedi DESIGN.md §16.
    righe.append(f"Data pronta dichiarata ad Amazon: {_data_pronta_locale(locale['lotto'].get('data_pronta'))}")
    for s in spedizioni:
        t = s.trasporto_confermato
        if t and t.convenzionato and t.modalita == "GROUND_SMALL_PARCEL":
            righe.append(f"Ritiro NON prenotato per {s.shipment_confirmation_id}: col convenzionato a pacchi "
                         f"le etichette {t.corriere} sono prepagate, ma il ritiro va chiesto a {t.corriere} "
                         f"citando il tracking (o i colli portati a un punto {t.corriere}). "
                         "I colli non partono da soli.")
    return "\n".join(righe)


def _data_pronta_locale(testo) -> str:
    """`2026-09-08T08:00Z` (l'istante UTC del lotto) → `08/09/2026 10:00`, l'ora
    che legge il magazziniere. Se non si riconosce, resta com'è."""
    for formato in ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            istante = datetime.strptime(str(testo), formato).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return f"{istante.astimezone():%d/%m/%Y %H:%M}"
    return str(testo)


def asn(client, plan_id: str, cliente: str, rif: str, shipment_ids: list[str]) -> str:
    """Rilegge da Amazon il codice ASN delle spedizioni di un piano e lo scrive
    nel registro.

    Serve un passo a sé perché col corriere proprio Amazon non lo emette
    insieme al codice FBA ma qualche minuto dopo, quando `conferma --vai` ha
    già finito. Chi prenota il ritiro lo deve dare al trasportatore, quindi
    deve poterlo ripescare senza rifare il giro."""
    righe = []
    for sid in shipment_ids:
        dettaglio = client.get(f"{_BASE}/{plan_id}/shipments/{sid}")
        codice = dettaglio.get("amazonReferenceId") or None
        fba = dettaglio.get("shipmentConfirmationId") or "codice FBA non ancora assegnato"
        if codice:
            registro.registra_asn(cliente, rif, sid, codice)
            righe.append(f"{fba}: ASN {codice}")
        else:
            righe.append(f"{fba}: ASN non ancora emesso da Amazon (stato {dettaglio.get('status')})"
                          " — riprova fra qualche minuto")
    return "\n".join(righe)


def _una_spedizione(shipment_ids: list[str], shipment_id: str | None, plan_id: str) -> str:
    """Quale spedizione tracciare. Le lettere di vettura arrivano raggruppate
    per prodotto, non per spedizione: su un piano diviso non c'è modo di
    indovinare a quale metà appartengano, e si chiede."""
    if shipment_id:
        if shipment_id not in shipment_ids:
            raise ValueError(
                f"la spedizione {shipment_id} non è del piano {plan_id} "
                f"(note: {', '.join(shipment_ids) or 'nessuna'})"
            )
        return shipment_id
    if not shipment_ids:
        raise ValueError(
            f"nessuna spedizione nota per {plan_id}: prima `giro.py riprendi {plan_id}`"
        )
    if len(shipment_ids) > 1:
        raise ValueError(
            f"il piano {plan_id} è diviso in più spedizioni ({', '.join(shipment_ids)}): "
            "dimmi con --spedizione a quale appartengono queste lettere di vettura"
        )
    return shipment_ids[0]


def tracking(client, plan_id: str, cliente: str, rif: str, shipment_ids: list[str],
             codici: dict[str, list[str]], shipment_id: str | None = None,
             vai: bool = False) -> str:
    """Il cancello 🟡 delle lettere di vettura (SKILL.md passo 19). Senza `vai`
    mostra l'abbinamento cartone → codice e NON scrive niente.

    Serve solo col corriere proprio: il convenzionato Amazon il tracking se lo
    emette da sé e lo stampa sotto l'etichetta FBA. E non è una scrittura
    tecnica — dichiararle porta la spedizione a SHIPPED."""
    sid = _una_spedizione(shipment_ids, shipment_id, plan_id)
    spedizione = tracking_mod.leggi_spedizione(client, plan_id, sid)
    gia_tracciati = tracking_mod.tracking_dichiarati(spedizione)
    fba = spedizione.get("shipmentConfirmationId") or sid
    cartoni = etichette.leggi_cartoni_completi(client, plan_id, sid)
    da_fare = tracking_mod.da_dichiarare(tracking_mod.abbina(cartoni, codici), gia_tracciati)

    sku_del_cartone = {c["boxId"]: c["items"][0]["msku"] for c in cartoni}
    righe = [f"Spedizione {fba} ({sid}) — {len(cartoni)} cartoni, stato "
             f"{spedizione.get('status') or 'ignoto'}"]
    righe += [f"  {box}  {sku_del_cartone[box]}  →  {codice}"
              for box, codice in da_fare.items()]
    testo = "\n".join(righe)
    if not vai:
        return (f"{testo}\n{len(da_fare)} lettere di vettura da dichiarare. Dopo il "
                f'"vai" la spedizione passa a SHIPPED: falle partire davvero prima.'
                "\n(anteprima: nessuna scrittura fatta; ripeti con --vai per dichiararle)")

    # ------------------------------ da qui si dichiara ------------------------------
    tracking_mod.dichiara(client, plan_id, sid, da_fare)
    # Prima si registra, poi si verifica: i codici sono già su Amazon, e un
    # errore nella rilettura non deve farli sparire dal registro.
    registro.registra_tracking(cliente, rif, sid, da_fare)
    confermati = tracking_mod.leggi_tracking(client, plan_id, sid)
    mancano = [box for box in da_fare if box not in confermati]
    testo += (f"\nDichiarate: Amazon ne riporta {len(da_fare) - len(mancano)} "
              f"su {len(da_fare)}.")
    if mancano:
        return testo + (f" NON risultano su {', '.join(mancano)}: rileggi la spedizione "
                        "su Seller Central prima di considerarla fatta")
    return testo + f" La spedizione {fba} è ora SHIPPED."


# --------------------------------------------------------------- scatole ----

def scatole(client, cliente: str, quantita: dict[str, int], adesso: datetime | None = None,
            mesi: int = 12, modello: str | None = None, storico: bool = False) -> str:
    """DESIGN §13-14: i colli standard del cliente dai piani spediti, l'istantanea
    per il controllo di `prepara`, e con le quantità la bozza del lotto. Con le
    quantità la bozza esce dai modelli d'imballaggio dell'account, a colli pieni:
    `modello` ne sceglie uno per nome, altrimenti il più adatto per SKU (regola
    del 2026-10-14). `storico` torna all'ultima scatola vista con il collo
    parziale da pesare. Sola lettura: nessun registro, nessuna POST."""
    adesso = adesso or datetime.now(timezone.utc)
    if modello and storico:
        raise ValueError("--modello e --storico si escludono: scegline uno")
    if modello and not quantita:
        raise ValueError("--modello serve a scrivere una bozza: passa almeno uno SKU=QTA")
    if quantita and not storico:
        piani, saltati = modelli_mod.leggi_piani(client, adesso, mesi)
        per_sku = modelli_mod.modelli_per_sku(piani)
        scelte = {sku: modelli_mod.scegli(per_sku, sku, modello) if modello
                  else modelli_mod.il_piu_adatto(per_sku, sku) for sku in quantita}
        pieni = {sku: modelli_mod.a_colli_pieni(q, scelte[sku].pezzi) for sku, q in quantita.items()}
        istantanea = modelli_mod.istantanea_da(cliente, scelte, adesso)
        # Salvata perché `prepara` confronti il lotto col modello scelto, non
        # con un'istantanea vecchia di un altro giro.
        scatole_mod.salva_istantanea(istantanea)
        percorso = scatole_mod.scrivi_bozza(cliente, scatole_mod.bozza_lotto(istantanea, pieni), adesso)
        righe = [f"Bozza di {cliente} dai modelli d'imballaggio dell'account (ultimi {mesi} mesi):"]
        for sku, q in quantita.items():
            m = scelte[sku]
            arrotondato = f"{q} pz richiesti → " if pieni[sku] != q else ""
            righe.append(f"  {sku}: {arrotondato}{pieni[sku]} pz in {pieni[sku] // m.pezzi} colli, "
                         f"modello {m.nome} ({m.pezzi} pz/collo, {m.lunghezza_cm:g}×{m.larghezza_cm:g}×"
                         f"{m.altezza_cm:g} cm, {m.peso_kg:g} kg, {m.scatole} scatole in {m.piani} piani)")
            # Solo i modelli creati da una persona: i "P1 - B7" di Amazon sono decine e non si scelgono.
            altri = [f"{a.nome} ({a.pezzi} pz)" for a in per_sku[sku] if a is not m and not a.automatico]
            if altri and not modello:
                righe.append(f"    altri modelli: {', '.join(altri)} — per cambiarlo: --modello NOME")
        righe += [f"  piano non letto: {s}" for s in saltati]
        return "\n".join(righe) + f"\nBozza del lotto: {percorso} — completa data_pronta, poi giro.py prepara"
    istantanea = scatole_mod.leggi_storico(client, cliente, adesso, mesi)
    percorso = scatole_mod.salva_istantanea(istantanea)
    testo = scatole_mod.tabella(istantanea) + f"\nIstantanea: {percorso}"
    if quantita:
        bozza = scatole_mod.scrivi_bozza(cliente, scatole_mod.bozza_lotto(istantanea, quantita), adesso)
        testo += (f"\nBozza del lotto: {bozza} — completa il peso dei colli parziali (da pesare) e "
                  f"data_pronta, poi giro.py prepara")
    return testo


# --------------------------------------------------------------- stima -----

def stima(client, cliente: str, giorni: int, transito: int = 10, scorta: int = 14,
          solo: list[str] | None = None, adesso: datetime | None = None, pausa=None) -> str:
    """DESIGN §19: quanto mandare per coprire `giorni` più transito e scorta,
    dal ritmo delle settimane a stock. Consegna l'Excel e, per gli SKU con un
    modello d'imballaggio, la bozza del lotto a colli pieni. Sola lettura."""
    adesso = adesso or datetime.now(timezone.utc)
    cfg = clienti.carica_cliente(cliente)
    paese = stock_mod.paese_di(cfg["marketplace_id"])
    righe = stock_mod.leggi_stock(client, paese, adesso, marketplace_id=cfg["marketplace_id"])
    if solo:
        ignoti = sorted(set(solo) - {r.sku for r in righe})
        if ignoti:
            raise ValueError(f"SKU non presenti nel report FBA di {cliente}: {', '.join(ignoti)}")
        righe = [r for r in righe if r.sku in solo]
    # orderMetrics regge 0,5 richieste al secondo: a raffica, sul collaudo del
    # 01/11 su ECM (40 SKU), tre SKU sono finiti in 429 a ripetizione.
    pausa = pausa or time.sleep
    stime = []
    for i, r in enumerate(righe):
        if i:
            pausa(2)
        serie = stima_mod.settimane_vendute(client, r.sku, cfg["marketplace_id"], adesso)
        stime.append(stima_mod.stima_sku(r, serie, giorni, transito, scorta))
    da_mandare = {s.sku: s.da_inviare for s in stime if s.da_inviare > 0}
    colli, scelte, quantita, senza_modello, saltati = {}, {}, {}, [], []
    if da_mandare:
        piani, saltati = modelli_mod.leggi_piani(client, adesso)
        per_sku = modelli_mod.modelli_per_sku(piani)
        for sku, q in da_mandare.items():
            if not per_sku.get(sku):
                senza_modello.append(sku)
                colli[sku] = "nessun modello: scatola da dettare"
                continue
            m = modelli_mod.il_piu_adatto(per_sku, sku)
            scelte[sku], quantita[sku] = m, modelli_mod.a_colli_pieni(q, m.pezzi)
            colli[sku] = f"{quantita[sku] // m.pezzi} × {m.pezzi} pz ({m.nome}) = {quantita[sku]} pz"
    intestazione = (f"Periodo {giorni} gg + transito {transito} gg + scorta {scorta} gg · "
                    f"ritmo dalle settimane a stock delle ultime {stima_mod.SETTIMANE} · {adesso:%d/%m/%Y}")
    excel = stima_mod.scrivi_excel(cliente, stime, colli, {r.sku: r.consigliati for r in righe},
                                   intestazione, adesso)
    righe_testo = [f"Stima di {cliente}: {len(da_mandare)} SKU da mandare su {len(stime)}, "
                   f"{sum(quantita.values()) + sum(da_mandare[s] for s in senza_modello)} pz in tutto.",
                   f"  {intestazione}"]
    for s in sorted(stime, key=lambda s: -s.da_inviare):
        if s.da_inviare:
            righe_testo.append(f"  {s.sku}: {s.al_giorno:.2f} pz/gg, copertura "
                               f"{s.copertura_giorni:.0f} gg → {colli[s.sku]}")
    deboli = [s.sku for s in stime if s.debole]
    if deboli:
        righe_testo.append(f"  stima debole: {', '.join(deboli)}")
    righe_testo += [f"  piano non letto: {x}" for x in saltati]
    righe_testo.append(f"Excel: {excel}")
    if scelte:
        istantanea = modelli_mod.istantanea_da(cliente, scelte, adesso)
        scatole_mod.salva_istantanea(istantanea)
        bozza = scatole_mod.scrivi_bozza(cliente, scatole_mod.bozza_lotto(istantanea, quantita), adesso)
        righe_testo.append(f"Bozza del lotto: {bozza} — completa data_pronta, poi giro.py prepara"
                           + (f" (fuori: {', '.join(senza_modello)}, senza modello)" if senza_modello else ""))
    return "\n".join(righe_testo)


# --------------------------------------------------------------- stock -----

def _blocco_cliente(client, cliente: str, soglie: dict, oggi, adesso, prova: bool):
    """Un cliente: report, zone, transizioni, e — se il cliente ripete sempre la
    stessa spedizione — la bozza già scritta."""
    cfg = clienti.carica_cliente(cliente)
    paese = stock_mod.paese_di(cfg["marketplace_id"])
    righe = stock_mod.leggi_stock(client, paese, adesso, marketplace_id=cfg["marketplace_id"])
    zone = [(r, stock_mod.zona(r, stock_mod.soglia_per(soglie, cliente, r.sku))) for r in righe]
    zone, dormienti = stock_mod.filtra_dormienti(client, zone, cfg["marketplace_id"], adesso)
    stato = {} if prova else stato_stock.carica(cliente)
    avvisi, nuovo = stato_stock.da_avvisare([(r, z) for r, z in zone if z != stock_mod.OK], stato, oggi)
    blocco = avviso_mail.Blocco(cliente, avvisi)
    blocco.dormienti = dormienti
    if not avvisi:
        return blocco, nuovo
    piani, blocco.saltati = modelli_mod.leggi_piani(client, adesso)
    per_sku = modelli_mod.modelli_per_sku(piani)
    scelte, quantita = {}, {}
    for a in avvisi:
        tipo = ricorrenza.spedizione_tipo(piani, a.sku)
        se_noti = per_sku.get(a.sku, [])
        modello = next((m for m in se_noti if m.nome == tipo.modello), None) if tipo else None
        if modello:
            scelte[a.sku] = modello
            quantita[a.sku] = modelli_mod.a_colli_pieni(max(a.riga.consigliati, 1), modello.pezzi)
        else:
            blocco.candidati.append((a.sku, "; ".join(
                f"{m.nome} ({m.scatole} scatole, {m.piani} piani)" for m in se_noti[:3])
                or "nessun modello in storico"))
    if scelte:
        blocco.modelli_scelti = [(sku, m.nome) for sku, m in scelte.items()]
        istantanea = modelli_mod.istantanea_da(cliente, scelte, adesso)
        blocco.bozza = scatole_mod.scrivi_bozza(
            cliente, scatole_mod.bozza_lotto(istantanea, quantita), adesso)
    return blocco, nuovo


def _righe_taciuti(blocchi) -> str:
    """Chi non e' stato segnalato perche' non vende da 90 giorni: nella mail non
    ci va (il punto e' il silenzio), ma chi lancia il giro a mano deve poterlo
    vedere, o il filtro diventa una scatola nera."""
    righe = [f"  {b.cliente}: taciuti perché non vendono da 90 giorni — {', '.join(b.dormienti)}"
             for b in blocchi if b.dormienti]
    return "\n".join(righe) + "\n" if righe else ""


def stock(fabbrica, clienti_da_fare: list[str], oggi, adesso,
          prova: bool = False, manda=None) -> str:
    """DESIGN §14. Un report per cliente, una mail sola per tutti, e solo se
    qualcosa è cambiato. Un cliente che fallisce non ferma gli altri: viene
    nominato nella mail."""
    blocchi, stati = [], {}
    for cliente in clienti_da_fare:
        try:
            blocco, nuovo = _blocco_cliente(fabbrica(cliente), cliente, stock_mod.carica_soglie(),
                                            oggi, adesso, prova)
            stati[cliente] = nuovo
        except (SpApiError, ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as e:
            blocchi.append(avviso_mail.Blocco(cliente, [], errore=f"{type(e).__name__}: {e}"))
            continue
        if blocco.avvisi:
            blocchi.append(blocco)
    da_dire = [b for b in blocchi if b.avvisi or b.errore]
    if not da_dire:
        # Giorno silenzioso: nessun avviso non mandato, quindi salvare qui non
        # sopprime niente. Senza questo salvataggio uno SKU tornato sopra
        # soglia non vedrebbe mai cancellata la sua voce (Task 12, DESIGN §14).
        if not prova:
            for cliente, nuovo in stati.items():
                stato_stock.salva(cliente, nuovo)
        return "Giro stock: niente da segnalare."
    # Il config si legge prima di comporre: se il file della password ha i
    # permessi larghi, la mail deve dirlo a chi legge (opzione B, 2026-09-11).
    config = {} if prova or manda else avviso_mail.carica_config()
    testo = avviso_mail.corpo(da_dire, oggi, quando=adesso, esposti=config.get("esposti", ()))
    if prova:
        return ("PROVA — mail non inviata, stato non salvato:\n"
                + _righe_taciuti(blocchi) + testo)
    manda = manda or (lambda ogg, html: avviso_mail.manda(config, ogg, html))
    destinatario = manda(avviso_mail.oggetto(da_dire), testo)
    for cliente, nuovo in stati.items():
        stato_stock.salva(cliente, nuovo)
    righe = [f"Giro stock: mail a {destinatario} — " +
             ", ".join(f"{b.cliente} {len(b.avvisi)} SKU" for b in da_dire if b.avvisi)]
    righe += [f"  Bozza: {b.bozza}" for b in da_dire if b.bozza]
    righe += [f"  saltato: {b.cliente} — {b.errore}" for b in da_dire if b.errore]
    righe += _righe_taciuti(blocchi).splitlines()
    return "\n".join(righe)


# ------------------------------------------------------------------- CLI ----

def _coppie(valori: list[str] | None, nome: str, forma: str = "SHIPMENT_ID=ID") -> dict[str, str]:
    coppie = {}
    for v in valori or []:
        if "=" not in v:
            raise ValueError(f"--{nome} vuole {forma}, ricevuto {v!r}")
        k, val = v.split("=", 1)
        coppie[k.strip()] = val.strip()
    return coppie


def _codici(valori: list[str] | None, percorso: str | None) -> dict[str, list[str]]:
    """Le lettere di vettura dalla riga di comando o dal file, {SKU: [codici]}.
    Le due forme non si mischiano: capire quale ha vinto sarebbe impossibile."""
    if valori and percorso:
        raise ValueError("--tracking e --file non si mischiano: o i codici sulla riga o il file")
    if percorso:
        return tracking_mod.carica_file(percorso)
    if not valori:
        raise ValueError(
            "servono le lettere di vettura: --tracking SKU=COD1,COD2 (ripetibile) "
            "oppure --file tracking.yaml"
        )
    codici: dict[str, list[str]] = {}
    for v in valori:
        if "=" not in v:
            raise ValueError(f"--tracking vuole SKU=COD1,COD2, ricevuto {v!r}")
        sku, elenco = v.split("=", 1)
        sku = sku.strip()
        if sku in codici:
            # Sovrascrivere in silenzio farebbe sparire metà dei codici, e il
            # controllo dei conteggi non se ne accorgerebbe.
            raise ValueError(
                f"--tracking {sku} passato due volte: mettili in un elenco solo, "
                "separati da virgola"
            )
        codici[sku] = [c.strip() for c in elenco.split(",") if c.strip()]
    return codici


def _quantita(valori: list[str] | None) -> dict[str, int]:
    quantita = {}
    for sku, q in _coppie(valori, "quantita", forma="SKU=QTA").items():
        try:
            quantita[sku] = int(q)
        except ValueError:
            raise ValueError(f"{sku}={q}: {q!r} non è una quantità") from None
        if quantita[sku] < 1:
            raise ValueError(f"{sku}={q}: la quantità deve essere almeno 1")
    return quantita


def _numero_pallet(testo: str, nome: str) -> int:
    try:
        n = int(testo)
    except ValueError:
        raise ValueError(f"{nome}: {testo!r} non è un numero di pallet") from None
    if n < 1:
        raise ValueError(f"{nome}: {n} non è un numero di pallet")
    return n


def _pallet_arg(valori: list[str] | None) -> int | dict[str, int] | None:
    """`--pallet N` con una spedizione sola, `--pallet SHIPMENT_ID=N` (ripetibile)
    su un piano diviso. Le due forme non si mischiano."""
    if not valori:
        return None
    if len(valori) == 1 and "=" not in valori[0]:
        return _numero_pallet(valori[0], "--pallet")
    if any("=" not in v for v in valori):
        raise ValueError(
            f"--pallet: o un numero solo o SHIPMENT_ID=N per spedizione, non insieme ({', '.join(valori)})"
        )
    return {sid: _numero_pallet(n, f"--pallet {sid}") for sid, n in _coppie(valori, "pallet").items()}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="giro.py", description="Spedizioni FBA: prepara / riprendi / conferma")
    sub = p.add_subparsers(dest="verbo", required=True)
    a = sub.add_parser("prepara", help="dal lotto.yaml al menù corrieri, senza spendere")
    a.add_argument("lotto")
    a.add_argument("--scatole-nuove", action="store_true",
                   help="accetta scatole diverse dallo storico del cliente (imballo cambiato)")
    b = sub.add_parser("riprendi", help="riconosce lo stadio su Amazon e arriva al menù")
    b.add_argument("plan_id")
    b.add_argument("--lotto", help="lotto.yaml, se il piano non è stato creato da giro.py")
    b.add_argument("--imballaggio", help="packing option da usare se Amazon ne offre più d'una")
    b.add_argument("--verificato", action="store_true", help="sblocca un esito incerto già controllato su Seller Central")
    b.add_argument("--scatole-nuove", action="store_true",
                   help="accetta scatole diverse dallo storico del cliente (imballo cambiato)")
    c = sub.add_parser("conferma", help="mostra il cancello; con --vai prenota e scarica le etichette")
    c.add_argument("plan_id")
    c.add_argument("--piazzamento")
    c.add_argument("--trasporto", action="append", metavar="SHIPMENT_ID=TRANSPORTATION_OPTION_ID")
    c.add_argument("--finestra", action="append", metavar="SHIPMENT_ID=DELIVERY_WINDOW_OPTION_ID")
    c.add_argument("--vai", action="store_true", help="da qui si spende")
    c.add_argument("--pallet", action="append", metavar="N | SHIPMENT_ID=N",
                   help="quante etichette pallet stampare: N con una spedizione sola, SHIPMENT_ID=N per spedizione")
    c.add_argument("--verificato", action="store_true")
    d = sub.add_parser("asn", help="rilegge il codice ASN di un piano già prenotato e lo registra")
    d.add_argument("plan_id")
    t = sub.add_parser("tracking", help="dichiara ad Amazon le lettere di vettura del corriere proprio")
    t.add_argument("plan_id")
    t.add_argument("--spedizione", metavar="SHIPMENT_ID", help="su un piano diviso, quale")
    t.add_argument("--tracking", action="append", metavar="SKU=COD1,COD2",
                   help="i codici raggruppati per prodotto, come li manda il cliente")
    t.add_argument("--file", metavar="tracking.yaml", help="gli stessi codici da un file")
    t.add_argument("--vai", action="store_true", help="da qui la spedizione passa a SHIPPED")
    e = sub.add_parser("scatole", help="colli standard per SKU dai piani spediti; con SKU=QTA scrive la bozza del lotto")
    e.add_argument("cliente")
    e.add_argument("quantita", nargs="*", metavar="SKU=QTA")
    e.add_argument("--mesi", type=int, default=12, help="quanti mesi di piani spediti leggere (12)")
    e.add_argument("--modello", help='usa questo modello d\'imballaggio dell\'account (default: il più adatto per SKU)')
    e.add_argument("--storico", action="store_true",
                   help="niente modelli: l'ultima scatola vista, con il collo parziale da pesare")
    g = sub.add_parser("stima", help="quanto mandare in FBA per coprire un periodo (sola lettura)")
    g.add_argument("cliente")
    g.add_argument("--giorni", type=int, help="giorni da coprire")
    g.add_argument("--fino", metavar="AAAA-MM-GG", help="oppure: data fino a cui coprire")
    g.add_argument("--transito", type=int, default=10, help="giorni fino all'arrivo in magazzino (10)")
    g.add_argument("--scorta", type=int, default=14, help="giorni di scorta di sicurezza (14)")
    g.add_argument("--sku", action="append", help="solo questi SKU (ripetibile)")
    f = sub.add_parser("stock", help="avvisa per mail gli SKU da riassortire (sola lettura)")
    f.add_argument("clienti", nargs="*", metavar="CLIENTE",
                   help="quali clienti; senza argomenti, tutti quelli di clienti.yaml")
    f.add_argument("--prova", action="store_true",
                   help="stampa la mail invece di mandarla, e non salva lo stato")
    args = p.parse_args(argv)
    try:
        if args.verbo == "prepara":
            lotto = lotto_mod.carica(args.lotto)
            plan_id, testo = prepara(spapi_client.crea_client(lotto.cliente), lotto, scatole_nuove=args.scatole_nuove)
            print(f"Piano creato: {plan_id}\n{testo}")
            return 0
        if args.verbo == "scatole":
            if args.mesi < 1:
                raise ValueError("--mesi deve essere almeno 1")
            print(scatole(spapi_client.crea_client(args.cliente), args.cliente, _quantita(args.quantita),
                          mesi=args.mesi, modello=args.modello, storico=args.storico))
            return 0
        if args.verbo == "stima":
            adesso = datetime.now(timezone.utc)
            giorni = stima_mod.giorni_periodo(adesso, args.giorni, args.fino)
            if args.transito < 0 or args.scorta < 0:
                raise ValueError("--transito e --scorta non possono essere negativi")
            print(stima(spapi_client.crea_client(args.cliente), args.cliente, giorni,
                        args.transito, args.scorta, args.sku, adesso))
            return 0
        if args.verbo == "stock":
            adesso = datetime.now(timezone.utc)
            print(stock(spapi_client.crea_client, args.clienti or clienti.elenco(),
                        oggi=adesso.date(), adesso=adesso, prova=args.prova))
            return 0
        locale = giri.carica(args.plan_id)
        lotto = lotto_mod.carica(args.lotto) if getattr(args, "lotto", None) else None
        cliente = locale["cliente"] if locale else (lotto.cliente if lotto else None)
        if not cliente:
            raise ValueError(f"nessun file locale per {args.plan_id}: passa --lotto lotto.yaml")
        client = spapi_client.crea_client(cliente)
        if args.verbo == "asn":
            print(asn(client, args.plan_id, cliente, locale["riferimento"],
                      list((locale.get("scelte") or {}).get("trasporto", {}))))
        elif args.verbo == "tracking":
            # I codici si leggono per primi: un errore sulla riga di comando
            # deve fermare tutto prima di qualunque lettura verso Amazon.
            codici = _codici(args.tracking, args.file)
            print(tracking(client, args.plan_id, cliente, locale["riferimento"],
                           list((locale.get("scelte") or {}).get("trasporto", {})),
                           codici, shipment_id=args.spedizione, vai=args.vai))
        elif args.verbo == "riprendi":
            print(riprendi(client, args.plan_id, lotto=lotto, packing_option_id=args.imballaggio,
                           verificato=args.verificato, scatole_nuove=args.scatole_nuove))
        else:
            print(conferma(client, args.plan_id, args.piazzamento, _coppie(args.trasporto, "trasporto"),
                           finestre=_coppie(args.finestra, "finestra"), vai=args.vai,
                           pallet_etichette=_pallet_arg(args.pallet), verificato=args.verificato))
        return 0
    except (ValueError, SpApiError, yaml.YAMLError) as e:
        print(f"ERRORE: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
