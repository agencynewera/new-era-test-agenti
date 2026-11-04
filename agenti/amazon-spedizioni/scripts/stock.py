"""Lo stock FBA di un cliente dal report Restock di Amazon (DESIGN §14).
Sola lettura: l'unica POST crea il report, non tocca l'account."""
from __future__ import annotations
import csv
import gzip
import io
import os
import time
import urllib.request
import yaml
from clienti import _chiave
from dataclasses import dataclass
from datetime import datetime, timedelta

from spapi_client import SpApiError, _USER_AGENT

TIPO_REPORT = "GET_RESTOCK_INVENTORY_RECOMMENDATIONS_REPORT"
_BASE = "/reports/2021-06-30"
_FINITI = ("DONE", "FATAL", "CANCELLED")

# I quattro marketplace documentati in clienti.yaml (riga 4). Il paese del
# report non si legge dall'indirizzo del cliente ma dal suo marketplace_id:
# un cliente DE con magazzino italiano chiederebbe il report giusto e poi
# terrebbe le righe del paese sbagliato — zero righe, per sempre (DESIGN §14).
PAESE_DEL_MARKETPLACE = {
    "APJ6JRA9NG5V4": "IT",
    "A1PA6795UKMFR9": "DE",
    "A13V1IB3VIYZZH": "FR",
    "A1RKKUPIHCS9HS": "ES",
}


def paese_di(marketplace_id: str) -> str:
    try:
        return PAESE_DEL_MARKETPLACE[marketplace_id]
    except KeyError:
        raise ValueError(
            f"marketplace {marketplace_id!r} sconosciuto: estendi "
            "PAESE_DEL_MARKETPLACE in stock.py") from None


class ReportFallito(SpApiError):
    """Il report non è arrivato: il giro deve nominare il cliente saltato,
    non far finta che non avesse SKU sotto soglia."""


@dataclass(frozen=True)
class RigaStock:
    sku: str
    asin: str
    disponibili: int
    in_arrivo: int
    venduti_30gg: int
    consigliati: int
    entro: str | None
    alert_amazon: str | None
    # Per la stima (DESIGN §19): pezzi già in Amazon ma non ancora vendibili
    # (FC transfer + FC Processing) e la parte di Inbound ancora in WORKING,
    # che Amazon non considera partita.
    in_lavorazione: int = 0
    in_working: int = 0

    @property
    def copertura_giorni(self) -> float:
        """Giorni di vendita davanti, merce in arrivo compresa. Senza vendite
        è infinita per costruzione: è il limite della metrica, non un errore —
        uno SKU esaurito vende zero proprio perché è esaurito (DESIGN §14)."""
        if self.venduti_30gg <= 0:
            return float("inf")
        return (self.disponibili + self.in_arrivo) / (self.venduti_30gg / 30)


def _numero(valore: str | None) -> int:
    testo = (valore or "").strip().replace(",", "")
    try:
        return int(float(testo))
    except ValueError:
        return 0


def _testo(valore: str | None) -> str | None:
    testo = (valore or "").strip()
    return testo or None


def _attendi(client, report_id: str, attesa_max_s: int, pausa) -> str:
    """Amazon prepara il report in una manciata di secondi (30 s su ECM il
    05/09), ma non lo garantisce: si aspetta con un tetto."""
    speso = 0
    while True:
        stato = client.get(f"{_BASE}/reports/{report_id}")
        processing = stato.get("processingStatus")
        if processing in _FINITI:
            if processing != "DONE":
                raise ReportFallito(f"report {TIPO_REPORT} chiuso in stato {processing}")
            return stato["reportDocumentId"]
        if speso >= attesa_max_s:
            raise ReportFallito(
                f"report {TIPO_REPORT} non pronto dopo {attesa_max_s} s (stato {processing})")
        pausa(15)
        speso += 15


def _scarica(client, document_id: str) -> str:
    documento = client.get(f"{_BASE}/documents/{document_id}")
    richiesta = urllib.request.Request(documento["url"], headers={"User-Agent": _USER_AGENT})
    grezzo = urllib.request.urlopen(richiesta, timeout=60).read()
    if documento.get("compressionAlgorithm") == "GZIP":
        grezzo = gzip.decompress(grezzo)
    return grezzo.decode("utf-8", "replace")


TENTATIVI_QUOTA = 3
ATTESA_QUOTA_S = 60


def _chiedi_report(client, marketplace_id: str, pausa) -> dict:
    """`createReport` ha una quota che si ricarica a circa una richiesta al
    minuto, mentre il backoff generale di `spapi_client` arriva a 15 secondi in
    tutto: non ce la fa mai, e il cliente sparisce dal giro senza colpa sua
    (successo il 2026-09-05 su ECM). Qui si aspetta il minuto che serve."""
    for tentativo in range(1, TENTATIVI_QUOTA + 1):
        try:
            return client.post(f"{_BASE}/reports", json_body={
                "reportType": TIPO_REPORT, "marketplaceIds": [marketplace_id]})
        except SpApiError as e:
            if "429" not in str(e) or tentativo == TENTATIVI_QUOTA:
                if "429" in str(e):
                    raise ReportFallito(
                        f"quota dei report esaurita: {TENTATIVI_QUOTA} tentativi a distanza "
                        f"di {ATTESA_QUOTA_S} s non sono bastati ({e})") from None
                raise
            pausa(ATTESA_QUOTA_S)
    raise ReportFallito("quota dei report esaurita")   # irraggiungibile, per il type checker


def leggi_stock(client, paese: str, adesso: datetime, marketplace_id: str,
                attesa_max_s: int = 600, pausa=time.sleep) -> list[RigaStock]:
    """Le righe del report che riguardano il paese del cliente. Il report ne
    porta una per paese (115 righe per 33 SKU su ECM): senza il filtro, gli
    out_of_stock dei mercati esteri seppelliscono il segnale (DESIGN §14)."""
    creato = _chiedi_report(client, marketplace_id, pausa)
    documento = _attendi(client, creato["reportId"], attesa_max_s, pausa)
    lettore = csv.DictReader(io.StringIO(_scarica(client, documento)), delimiter="\t")
    righe = []
    for r in lettore:
        if (r.get("Country") or "").strip().upper() != paese.upper():
            continue
        righe.append(RigaStock(
            sku=(r.get("Merchant SKU") or "").strip(),
            asin=(r.get("ASIN") or "").strip(),
            disponibili=_numero(r.get("Available")),
            in_arrivo=_numero(r.get("Inbound")),
            venduti_30gg=_numero(r.get("Units Sold Last 30 Days")),
            consigliati=_numero(r.get("Recommended replenishment qty")),
            entro=_testo(r.get("Recommended ship date")),
            alert_amazon=_testo(r.get("Alert")),
            in_lavorazione=_numero(r.get("FC transfer")) + _numero(r.get("FC Processing")),
            in_working=_numero(r.get("Working")),
        ))
    return righe

OK, RIASSORTIRE, CRITICO = "ok", "riassortire", "critico"
SOGLIA_DURA_DEFAULT = 7.0
_SOGLIE_DEFAULT = os.path.join(os.path.dirname(__file__), "..", "soglie.yaml")


def zona(riga: RigaStock, soglia_giorni: float, soglia_dura: float = SOGLIA_DURA_DEFAULT) -> str:
    """Tre segnali, in ordine di gravità (DESIGN §14): esaurito con un consiglio
    aperto, copertura sotto la soglia dura, poi il consiglio di Amazon o la
    copertura sotto la soglia del cliente."""
    if riga.disponibili + riga.in_arrivo == 0 and riga.consigliati > 0:
        return CRITICO
    if riga.copertura_giorni < soglia_dura:
        return CRITICO
    if riga.consigliati > 0 or riga.copertura_giorni < soglia_giorni:
        return RIASSORTIRE
    return OK


def carica_soglie(percorso: str | None = None) -> dict:
    """Assente vale come vuoto: il default di 30 giorni sta nel codice, il file
    serve solo a scostarsene."""
    percorso = percorso or _SOGLIE_DEFAULT
    if not os.path.exists(percorso):
        return {}
    with open(percorso) as f:
        return yaml.safe_load(f) or {}


def soglia_per(soglie: dict, cliente: str, sku: str) -> float:
    """Dalla più specifica alla più generale: SKU, cliente, default."""
    per_cliente = {_chiave(k): v for k, v in (soglie.get("clienti") or {}).items()}
    conf = per_cliente.get(_chiave(cliente)) or {}
    per_sku = (conf.get("sku") or {}).get(sku)
    if per_sku is not None:
        return float(per_sku)
    if conf.get("default_giorni") is not None:
        return float(conf["default_giorni"])
    return float(soglie.get("default_giorni", 30))


_VENDITE = "/sales/v1/orderMetrics"
GIORNI_DORMIENTE = 90


def unita_vendute(client, asin: str, marketplace_id: str, adesso: datetime,
                  giorni: int = GIORNI_DORMIENTE) -> int | None:
    """Le unità vendute di un ASIN nell'ultima finestra. `None` se Amazon non
    risponde: uno zero inventato zittirebbe un avviso vero."""
    inizio = adesso - timedelta(days=giorni)
    try:
        risposta = client.get(_VENDITE, params={
            "marketplaceIds": marketplace_id,
            "interval": f"{inizio:%Y-%m-%dT00:00:00Z}--{adesso:%Y-%m-%dT00:00:00Z}",
            "granularity": "Total", "asin": asin})
    except SpApiError:
        return None
    voci = risposta.get("payload") or []
    if not voci:
        return None
    return int(voci[0].get("unitCount") or 0)


def filtra_dormienti(client, zone: list, marketplace_id: str, adesso: datetime,
                     giorni: int = GIORNI_DORMIENTE) -> tuple[list, list[str]]:
    """DESIGN §14: uno SKU che non vende un pezzo da `giorni` non si riassortisce,
    per quanto Amazon lo consigli — il suo consiglio guarda una domanda storica
    che può non esistere più (Mariano, 2026-09-05: su ECM tre SKU proposti erano
    prodotti che non vendiamo più). Si chiede solo per gli SKU dubbi, cioè quelli
    fermi da 30 giorni: chi ha venduto nell'ultimo mese è già vivo. Se le vendite
    non si leggono l'avviso passa: meglio uno di troppo che una rottura persa."""
    filtrate, dormienti = [], []
    for riga, z in zone:
        if z == OK or riga.venduti_30gg > 0 or not riga.asin:
            filtrate.append((riga, z))
            continue
        vendute = unita_vendute(client, riga.asin, marketplace_id, adesso, giorni)
        if vendute == 0:
            filtrate.append((riga, OK))
            dormienti.append(riga.sku)
        else:
            filtrate.append((riga, z))
    return filtrate, dormienti
