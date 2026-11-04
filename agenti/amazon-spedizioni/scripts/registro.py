from __future__ import annotations
import dataclasses
import fcntl
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone


def _nome_cliente(cliente: str) -> str:
    """Il nome cliente finisce nel nome del file: tutto ciò che non è lettera,
    numero, spazio, punto, trattino o underscore diventa "_", così un "../" non
    porta il registro a scrivere fuori dalla sua cartella. Resta leggibile:
    "Phy to Pet" non cambia."""
    return re.sub(r"[^\w .\-]", "_", cliente)


def _percorso(cliente: str, data: str | None = None) -> str:
    base = os.path.expanduser("~/.config/newera-amazon/spedizioni")
    os.makedirs(base, exist_ok=True)
    data = data or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(base, f"{_nome_cliente(cliente)}-{data}.json")


@contextmanager
def _lucchetto(cliente: str):
    """Un lucchetto per cliente attorno a ogni lettura-modifica-scrittura del
    registro (DESIGN §17). Senza, due `giro.py` insieme si perdono le voci a
    vicenda e — scrivendo lo stesso file di appoggio — corrompono il JSON.
    `flock` è per descrittore aperto: vale fra processi e fra thread, e cade
    da solo se il processo muore.
    Non è rientrante: un registra_* che ne chiamasse un altro si bloccherebbe da solo."""
    cartella = os.path.dirname(_percorso(cliente))
    with open(os.path.join(cartella, f"{_nome_cliente(cliente)}.lock"), "a") as lucchetto:
        fcntl.flock(lucchetto, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lucchetto, fcntl.LOCK_UN)


def _carica(percorso: str) -> list[dict]:
    if not os.path.exists(percorso):
        return []
    with open(percorso) as f:
        return json.load(f)


def _carica_voce(cliente: str, riferimento: str) -> tuple[str, list[dict], int]:
    """Il riferimento identifica UN lotto: "2026-08-29#0#a1b2c3d4" — giorno,
    posizione e un codice che nasce col lotto.

    Il codice è ciò che rende il riferimento un'identità e non un numero
    d'ordine. Senza, due lotti dello stesso cliente nello stesso giorno hanno
    riferimenti intercambiabili, e riusare per sbaglio quello del lotto del
    mattino scrive la spesa del pomeriggio sopra il lotto sbagliato, in
    silenzio. Il cliente si controlla sul nome vero salvato nella voce, non sul
    nome del file: due nomi diversi possono sanificarsi uguali."""
    try:
        data, posizione, lotto_id = riferimento.split("#")
        indice = int(posizione)
    except (AttributeError, ValueError):
        raise ValueError(
            f"riferimento di registro non valido: {riferimento!r} (atteso il valore "
            "restituito da registra_tentativo, es. '2026-08-29#0#a1b2c3d4')"
        )
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
        raise ValueError(f"data non valida nel riferimento {riferimento!r}")
    percorso = _percorso(cliente, data)
    log = _carica(percorso)
    if not 0 <= indice < len(log):
        raise ValueError(
            f"voce {riferimento} del registro di {cliente} non trovata in {percorso}"
        )
    voce = log[indice]
    if voce.get("lotto_id") != lotto_id:
        raise ValueError(
            f"il riferimento {riferimento} non corrisponde al lotto in posizione "
            f"{indice} di {percorso} (lotto {voce.get('lotto_id')}): è il riferimento "
            "di un altro lotto"
        )
    if voce.get("cliente") != cliente:
        raise ValueError(
            f"riferimento {riferimento} appartiene a {voce.get('cliente')!r}, "
            f"non a {cliente!r}"
        )
    return percorso, log, indice


def voce(cliente: str, riferimento: str) -> dict:
    """La voce di un riferimento, con gli stessi controlli delle scritture
    (posizione nel file, lotto_id, cliente). È la lettura giusta quando si ha
    un riferimento: `ultimo_lotto` filtra per cliente, e le sue posizioni non
    sono quelle del file quando due nomi si sanificano uguali."""
    _, log, indice = _carica_voce(cliente, riferimento)
    return log[indice]


def _serializzabile(oggetto):
    """Punto unico di conversione: i moduli del flusso passano le @dataclass di
    modello.py, il registro le scrive come dizionari senza che il chiamante
    debba ricordarsene a ogni chiamata."""
    if dataclasses.is_dataclass(oggetto):
        return dataclasses.asdict(oggetto)
    raise TypeError(f"non registrabile: {type(oggetto).__name__}")


def _salva(percorso: str, log: list[dict]) -> None:
    """Scrittura atomica: si serializza tutto in memoria, si scrive su un file
    di appoggio e solo alla fine lo si sostituisce al buono. Un errore a metà
    strada non lascia più un JSON troncato al posto della cronologia."""
    testo = json.dumps(log, indent=2, ensure_ascii=False, default=_serializzabile)
    temporaneo = f"{percorso}.tmp"
    with open(temporaneo, "w") as f:
        f.write(testo)
    os.replace(temporaneo, percorso)


def registra_tentativo(cliente: str, righe: list) -> str:
    """righe: le `RigaSpedizione` (o i dizionari equivalenti) del lotto
    richiesto da Mariano, prima di sapere come Amazon lo dividerà in
    spedizioni.

    Torna il riferimento della voce ("2026-08-29#0#a1b2c3d4") da passare a
    tutte le scritture successive di questo lotto."""
    data = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    percorso = _percorso(cliente, data)
    with _lucchetto(cliente):
        log = _carica(percorso)
        lotto_id = uuid.uuid4().hex[:8]
        log.append({
            "lotto_id": lotto_id, "cliente": cliente, "righe": righe,
            "ora": datetime.now(timezone.utc).isoformat(),
            "esito": "in corso", "inbound_plan_id": None, "spedizioni": [],
        })
        _salva(percorso, log)
    return f"{data}#{len(log) - 1}#{lotto_id}"


def registra_piano(cliente: str, riferimento: str, inbound_plan_id: str, spedizioni: list) -> None:
    with _lucchetto(cliente):
        percorso, log, indice = _carica_voce(cliente, riferimento)
        log[indice]["inbound_plan_id"] = inbound_plan_id
        log[indice]["spedizioni"] = spedizioni
        _salva(percorso, log)


def registra_piazzamento_confermato(cliente: str, riferimento: str, placement_option_id: str,
                                    commissioni: float | None = None, valuta: str = "EUR") -> None:
    """La commissione di piazzamento è già spesa reale e irreversibile: si
    scrive appena `conferma_piazzamento` è andata a buon fine, senza aspettare
    il trasporto — che è un passo a sé e può fallire per conto suo."""
    with _lucchetto(cliente):
        percorso, log, indice = _carica_voce(cliente, riferimento)
        gia_scritto = log[indice].get("piazzamento_confermato")
        if gia_scritto and gia_scritto.get("placement_option_id") != placement_option_id:
            # Sovrascrivere cancellerebbe la traccia di una commissione già
            # addebitata e irreversibile. Se capita, è il segno di un riferimento
            # sbagliato: meglio un errore che una riga persa.
            raise ValueError(
                f"la voce {riferimento} di {cliente} ha già un piazzamento confermato "
                f"({gia_scritto.get('placement_option_id')}, "
                f"{gia_scritto.get('commissioni')} {gia_scritto.get('valuta')}): "
                f"rifiuto di sovrascriverlo con {placement_option_id} "
                f"({commissioni} {valuta}) — verifica di non aver usato il riferimento "
                "di un altro lotto"
            )
        log[indice]["piazzamento_confermato"] = {
            "placement_option_id": placement_option_id,
            "commissioni": commissioni,
            "valuta": valuta,
            "ora": datetime.now(timezone.utc).isoformat(),
        }
        _salva(percorso, log)


def registra_trasporto_confermato(cliente: str, riferimento: str, scelte: dict) -> None:
    """`scelte`: {shipment_id: opzione} — TUTTE le spedizioni prenotate dalla
    stessa `conferma_trasporto`, in una scrittura sola.

    Amazon prenota un lotto diviso con una chiamata unica: registrarlo una
    spedizione alla volta significa che, se la prima scrittura fallisce, le
    altre prenotazioni — già pagate — non vengono mai scritte. Le spedizioni
    ignote non fermano la scrittura di quelle note: prima si salva ciò che si
    sa, poi si segnala il resto."""
    if not scelte:
        raise ValueError(
            f"nessuna scelta di trasporto da registrare per {riferimento}: una "
            "prenotazione già pagata non deve passare in silenzio"
        )
    # Il corriere è già prenotato: QUALUNQUE cosa vada storta da qui in poi —
    # voce non trovata, disco pieno, voce malformata — il dato pagato deve
    # sopravvivere almeno nel messaggio, non sparire con l'eccezione.
    try:
        with _lucchetto(cliente):
            percorso, log, indice = _carica_voce(cliente, riferimento)
            spedizioni = log[indice]["spedizioni"]
            note = {s["shipment_id"] for s in spedizioni}
            for s in spedizioni:
                if s["shipment_id"] in scelte:
                    s["trasporto_confermato"] = scelte[s["shipment_id"]]
            ignote = [sid for sid in scelte if sid not in note]
            if ignote:
                # Fuori dalla voce non c'è posto dove scriverle: almeno restano qui.
                log[indice].setdefault("trasporto_non_collocato", {}).update(
                    {sid: scelte[sid] for sid in ignote}
                )
            _salva(percorso, log)
    except Exception as e:
        raise ValueError(
            f"{e}. Trasporto già confermato su Amazon, NON registrato: {scelte}"
        ) from e
    if ignote:
        raise ValueError(
            f"spedizioni {', '.join(ignote)} non presenti nella voce {riferimento} "
            f"di {cliente} (note: {', '.join(sorted(note)) or 'nessuna'}): trasporto "
            "confermato su Amazon, scritto sotto 'trasporto_non_collocato'"
        )


def registra_etichette(cliente: str, riferimento: str, shipment_id: str,
                        shipment_confirmation_id: str, percorso_pdf: str) -> None:
    """Il codice FBA della spedizione e il percorso del PDF sono i due
    riferimenti che servono dopo — ristampe, assistenza Amazon, riconciliazione
    — e senza questa riga vivrebbero solo nel contesto della chat."""
    # Stessa rete di sicurezza del trasporto: la spedizione è già pagata e le
    # etichette già scaricate, il riferimento non deve perdersi.
    try:
        with _lucchetto(cliente):
            percorso, log, indice = _carica_voce(cliente, riferimento)
            trovate = [s for s in log[indice]["spedizioni"] if s["shipment_id"] == shipment_id]
            for s in trovate:
                s["shipment_confirmation_id"] = shipment_confirmation_id
                s["etichette_path"] = percorso_pdf
            if not trovate:
                # Prima si salva il dato, poi si segnala che non si è trovato dove
                # metterlo. Una lista: due chiamate non si cancellano a vicenda.
                log[indice].setdefault("etichette_non_collocate", {}).setdefault(
                    shipment_id, []
                ).append({
                    "shipment_confirmation_id": shipment_confirmation_id,
                    "percorso_pdf": percorso_pdf,
                })
            _salva(percorso, log)
    except Exception as e:
        raise ValueError(
            f"{e}. Etichette già scaricate ({shipment_confirmation_id} → "
            f"{percorso_pdf}), NON registrate"
        ) from e
    if not trovate:
        raise ValueError(
            f"spedizione {shipment_id} non presente nella voce {riferimento} di "
            f"{cliente}: etichette scaricate in {percorso_pdf}, scritte sotto "
            "'etichette_non_collocate'"
        )


def registra_tracking(cliente: str, riferimento: str, shipment_id: str,
                       tracking: dict[str, str]) -> None:
    """Le lettere di vettura del corriere proprio, {boxId: trackingId}.

    Arrivano dopo tutto il resto — a spedizione prenotata, etichette stampate e
    colli partiti — e sono l'unico modo di seguire quei colli: col corriere
    proprio Amazon non emette nessun tracking suo. Si aggiungono a quelle già
    scritte, non le sostituiscono: una dichiarazione completata in due riprese
    non deve cancellare i codici della prima, che sono già su Amazon.

    Stessa rete di `registra_etichette`: quando questa riga viene scritta i
    codici sono già dichiarati e la spedizione è già passata a SHIPPED, quindi
    il dato deve sopravvivere al messaggio d'errore invece di sparire con
    l'eccezione."""
    if not tracking:
        raise ValueError(
            f"nessuna lettera di vettura da registrare per {riferimento}: una "
            "dichiarazione già accettata da Amazon non deve passare in silenzio"
        )
    try:
        with _lucchetto(cliente):
            percorso, log, indice = _carica_voce(cliente, riferimento)
            trovate = [s for s in log[indice]["spedizioni"] if s["shipment_id"] == shipment_id]
            for s in trovate:
                s.setdefault("tracking", {}).update(tracking)
            if not trovate:
                log[indice].setdefault("tracking_non_collocato", {}).setdefault(
                    shipment_id, {}).update(tracking)
            _salva(percorso, log)
    except Exception as e:
        raise ValueError(
            f"{e}. Lettere di vettura già dichiarate ad Amazon, NON registrate: {tracking}"
        ) from e
    if not trovate:
        raise ValueError(
            f"spedizione {shipment_id} non presente nella voce {riferimento} di "
            f"{cliente}: lettere di vettura dichiarate su Amazon, scritte sotto "
            "'tracking_non_collocato'"
        )


def registra_asn(cliente: str, riferimento: str, shipment_id: str, asn: str) -> None:
    """Il codice ASN (`amazonReferenceId`) di una spedizione già prenotata.

    Sta a parte da `registra_etichette` perché arriva in un altro momento: col
    corriere convenzionato Amazon lo emette insieme al codice FBA, col corriere
    proprio qualche minuto più tardi — quando la spedizione è già registrata e
    le etichette già scaricate. Va scritto sulla spedizione giusta o non
    scritto affatto: un ASN sotto la spedizione sbagliata manderebbe il
    trasportatore a citare un codice che non è il suo."""
    with _lucchetto(cliente):
        percorso, log, indice = _carica_voce(cliente, riferimento)
        trovate = [s for s in log[indice]["spedizioni"] if s["shipment_id"] == shipment_id]
        if not trovate:
            raise ValueError(
                f"spedizione {shipment_id} non presente nella voce {riferimento} di "
                f"{cliente}: ASN {asn} NON registrato"
            )
        for s in trovate:
            s["amazon_reference_id"] = asn
        _salva(percorso, log)


def registra_esito(cliente: str, riferimento: str, esito: str) -> None:
    with _lucchetto(cliente):
        percorso, log, indice = _carica_voce(cliente, riferimento)
        log[indice]["esito"] = esito
        _salva(percorso, log)


def ultimo_lotto(cliente: str, data: str | None = None) -> list[dict]:
    """Le voci di un giorno (di default oggi). Con `data` si rilegge un giorno
    passato — serve quando un lotto ha attraversato la mezzanotte UTC.

    Filtra per cliente: due nomi diversi possono sanificarsi nello stesso nome
    file (e su macOS anche solo le maiuscole bastano), e i dati di un cliente
    non devono mai comparire nella vista di un altro. Il confronto è esatto:
    una voce senza `cliente` non è di nessuno e non compare da nessuna parte —
    tollerarla la farebbe comparire in tutte le viste."""
    return [v for v in _carica(_percorso(cliente, data))
             if v.get("cliente") == cliente]
