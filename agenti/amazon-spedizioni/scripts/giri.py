from __future__ import annotations
from contextlib import contextmanager
import fcntl
import json
import os
import re


def _percorso(plan_id: str) -> str:
    # Il plan_id arriva dalla riga di comando e diventa un nome di file: un
    # "../" scriverebbe fuori dalla cartella dei giri. Qui si rifiuta invece
    # di sostituire come registro._nome_cliente — un id di Amazon è sempre
    # solo lettere, cifre e trattini, e un id storpiato aprirebbe il piano
    # sbagliato in silenzio.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", plan_id or ""):
        raise ValueError(f"plan_id non valido: {plan_id!r}")
    # Stessa radice del registro (registro._percorso), sottocartella per piano:
    # qui vive solo ciò che Amazon non sa (scatole, data pronta, riferimento
    # di registro, scelte fatte), mai lo stadio (DESIGN §12).
    return os.path.expanduser(f"~/.config/newera-amazon/spedizioni/giri/{plan_id}.json")


@contextmanager
def lucchetto(plan_id: str):
    """Un solo `conferma --vai` per piano alla volta (DESIGN §17). Non aspetta:
    se un altro processo lo tiene alza subito — la seconda conferma dello
    stesso piano non ha niente di buono da fare, e
    `confirmTransportationOptions` non ha chiave di idempotenza."""
    percorso = _percorso(plan_id) + ".lock"
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"un'altra conferma di {plan_id} è in corso: aspetta che finisca") from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def salva(plan_id: str, dati: dict) -> None:
    percorso = _percorso(plan_id)
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    temporaneo = f"{percorso}.tmp"
    with open(temporaneo, "w") as f:
        f.write(json.dumps(dati, indent=2, ensure_ascii=False))
    os.replace(temporaneo, percorso)


def carica(plan_id: str) -> dict | None:
    percorso = _percorso(plan_id)
    if not os.path.isfile(percorso):
        return None
    with open(percorso) as f:
        return json.load(f)


def aggiorna(plan_id: str, **campi) -> dict:
    dati = carica(plan_id) or {}
    dati.update(campi)
    salva(plan_id, dati)
    return dati
