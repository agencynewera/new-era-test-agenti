from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import yaml
from modello import RigaSpedizione, Scatola

# Stesso vincolo di corrieri.genera_opzioni_trasporto: un istante, non un giorno.
_RE_ISTANTE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{3})?)?Z")
_STACKABILITY = ("STACKABLE", "NON_STACKABLE")
# Forma `expiration` di ItemInput (AAAA-MM-GG), dal modello SP-API 2024-03-20.
_RE_SCADENZA = re.compile(r"\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")
_CAMPI_PALLET = ("quantity", "stackability", "peso_kg", "lunghezza_cm", "larghezza_cm", "altezza_cm")


@dataclass
class Lotto:
    """Il lotto come lo detta Mariano, letto da `lotto.yaml`. `pallet` resta
    nella forma del file: la forma di Amazon la dà `pallet_amazon`."""
    cliente: str
    righe: list[RigaSpedizione]
    scatole: list[Scatola]
    data_pronta: str
    pallet: list[dict] = field(default_factory=list)


def carica(percorso: str) -> Lotto:
    with open(percorso) as f:
        return da_dizionario(yaml.safe_load(f) or {})


def _scadenza(valore) -> str | None:
    # PyYAML legge `2028-03-14` come date: si riporta a testo AAAA-MM-GG.
    if valore in (None, ""):
        return None
    testo = valore.strftime("%Y-%m-%d") if isinstance(valore, date) else str(valore).strip()
    if not _RE_SCADENZA.fullmatch(testo):
        raise ValueError(f"scadenza non valida: {testo!r}. Amazon vuole AAAA-MM-GG, es. '2028-03-14'")
    return testo


def _righe(elenco, dove: str) -> list[RigaSpedizione]:
    righe = []
    for i, r in enumerate(elenco or [], start=1):
        try:
            righe.append(RigaSpedizione(sku=str(r["sku"]).strip(), quantita=int(r["quantita"]),
                                        scadenza=_scadenza(r.get("scadenza"))))
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"{dove}, riga {i}: servono sku e quantita ({e})") from e
    return righe


def propaga_scadenze(righe: list[RigaSpedizione], scatole: list[Scatola]) -> None:
    """La scadenza si dichiara una volta, nelle righe del lotto: il contenuto
    delle scatole con lo stesso SKU la eredita, perché Amazon vuole nelle
    scatole gli stessi articoli del piano (owner e scadenza compresi)."""
    scadenze = {r.sku: r.scadenza for r in righe if r.scadenza}
    for s in scatole:
        for r in s.righe:
            if r.scadenza is None:
                r.scadenza = scadenze.get(r.sku)


def _istante(valore) -> str:
    # PyYAML legge `2026-09-05T08:00:00Z` come datetime; senza secondi resta testo.
    if isinstance(valore, datetime):
        if valore.tzinfo is not None:
            valore = valore.astimezone(timezone.utc)
        return valore.strftime("%Y-%m-%dT%H:%M:%SZ")
    testo = str(valore or "").strip()
    if not _RE_ISTANTE.fullmatch(testo):
        raise ValueError(
            f"data_pronta non valida: {testo!r}. Amazon vuole un istante UTC, "
            "non un giorno — es. '2026-09-05T08:00Z'"
        )
    return testo


def _controlla_contenuto(righe: list[RigaSpedizione], scatole: list[Scatola]) -> None:
    """La somma di ciò che c'è nelle scatole deve dare esattamente il lotto:
    Amazon confronta le due dichiarazioni e rifiuta se non combaciano
    (misurato il 29/08), ma lo fa solo dopo aver creato il piano."""
    dichiarato = {r.sku: r.quantita for r in righe}
    nelle_scatole: dict[str, int] = {}
    for s in scatole:
        for r in s.righe:
            nelle_scatole[r.sku] = nelle_scatole.get(r.sku, 0) + r.quantita * s.quantita
    for sku in sorted(set(dichiarato) | set(nelle_scatole)):
        if dichiarato.get(sku, 0) != nelle_scatole.get(sku, 0):
            raise ValueError(
                f"le scatole non tornano per {sku}: il lotto ne dichiara "
                f"{dichiarato.get(sku, 0)}, nelle scatole ce ne sono {nelle_scatole.get(sku, 0)}"
            )


def _pallet(p: dict, i: int) -> dict:
    mancanti = [c for c in _CAMPI_PALLET if p.get(c) in (None, "")]
    if mancanti:
        raise ValueError(f"pallet {i}: manca {', '.join(mancanti)}")
    if p["stackability"] not in _STACKABILITY:
        raise ValueError(
            f"pallet {i}: stackability {p['stackability']!r} non valida, "
            f"Amazon accetta {', '.join(_STACKABILITY)}"
        )
    for campo in ("quantity", "peso_kg", "lunghezza_cm", "larghezza_cm", "altezza_cm"):
        if float(p[campo]) <= 0:
            raise ValueError(f"pallet {i}: {campo} non valido ({p[campo]}): Amazon lo respinge, ma dopo il vai")
    return {k: (str(v) if k == "stackability" else v) for k, v in p.items()}


def _controlla_misure(s: Scatola) -> None:
    """Specchio di imballaggio._scatola_a_box, ma qui il piano non esiste
    ancora: peso e misure li dà il venditore, e uno zero scoperto solo al
    momento di dichiarare le scatole lascia una bozza appesa su Amazon
    (DESIGN §6: ci si ferma prima di chiamarlo)."""
    for campo in ("quantita", "peso_kg", "lunghezza_cm", "larghezza_cm", "altezza_cm"):
        valore = getattr(s, campo)
        if valore <= 0:
            raise ValueError(f"{campo} non valido ({valore})")


def da_dizionario(dati: dict) -> Lotto:
    cliente = str(dati.get("cliente") or "").strip()
    if not cliente:
        raise ValueError("lotto senza cliente")
    righe = _righe(dati.get("righe"), "righe del lotto")
    if not righe:
        raise ValueError("lotto senza righe: servono SKU e quantita")
    scatole = []
    for i, s in enumerate(dati.get("scatole") or [], start=1):
        try:
            mancanti = [c for c in ("quantita", "peso_kg", "lunghezza_cm", "larghezza_cm", "altezza_cm")
                        if s.get(c) in (None, "")]
            if mancanti:
                raise ValueError(f"manca {', '.join(mancanti)}")
            scatole.append(Scatola(
                quantita=int(s["quantita"]), peso_kg=float(s["peso_kg"]),
                lunghezza_cm=float(s["lunghezza_cm"]), larghezza_cm=float(s["larghezza_cm"]),
                altezza_cm=float(s["altezza_cm"]),
                righe=_righe(s.get("righe"), f"scatola {i}"),
            ))
            _controlla_misure(scatole[-1])
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ValueError(f"scatola {i}: {e}") from e
    if not scatole:
        raise ValueError("lotto senza scatole: Amazon non divide il piano né quota senza misure e pesi")
    _controlla_contenuto(righe, scatole)
    propaga_scadenze(righe, scatole)
    pallet = [_pallet(p, i) for i, p in enumerate(dati.get("pallet") or [], start=1)]
    return Lotto(cliente=cliente, righe=righe, scatole=scatole,
                 data_pronta=_istante(dati.get("data_pronta")), pallet=pallet)


def _riga_dizionario(r: RigaSpedizione) -> dict:
    d = {"sku": r.sku, "quantita": r.quantita}
    if r.scadenza:
        d["scadenza"] = r.scadenza
    return d


def a_dizionario(lotto: Lotto) -> dict:
    """Inverso esatto di `da_dizionario`: è ciò che finisce nel file locale del
    piano, così `riprendi` rilegge le scatole senza il file di lotto."""
    return {
        "cliente": lotto.cliente,
        "righe": [_riga_dizionario(r) for r in lotto.righe],
        "scatole": [{
            "quantita": s.quantita, "peso_kg": s.peso_kg, "lunghezza_cm": s.lunghezza_cm,
            "larghezza_cm": s.larghezza_cm, "altezza_cm": s.altezza_cm,
            "righe": [_riga_dizionario(r) for r in s.righe],
        } for s in lotto.scatole],
        "data_pronta": lotto.data_pronta,
        "pallet": list(lotto.pallet),
    }


def pallet_amazon(lotto: Lotto) -> list[dict]:
    """La forma `pallets` di generateTransportationOptions. Senza pallet Amazon
    non quota le opzioni LTL (DESIGN §5)."""
    return [{
        "quantity": int(p["quantity"]),
        "stackability": p["stackability"],
        "weight": {"value": float(p["peso_kg"]), "unit": "KG"},
        "dimensions": {"length": float(p["lunghezza_cm"]), "width": float(p["larghezza_cm"]),
                       "height": float(p["altezza_cm"]), "unitOfMeasurement": "CM"},
    } for p in lotto.pallet]
