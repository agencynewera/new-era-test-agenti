from __future__ import annotations
import hashlib
import base64
from urllib.parse import urlsplit, parse_qsl
import requests
from spapi_client import SpApiError, _USER_AGENT

_RESOURCE_APLUS = "aplus/2020-11-01/contentDocuments"


def carica_immagine(client, marketplace_id: str, percorso_file: str, content_type: str) -> str:
    """Carica un'immagine per i Contenuti A+ in due passi: 1) createUploadDestination
    ForResource (via SP-API, stesso client autenticato) per ottenere una destinazione;
    2) upload del file vero al risultato — non allo stesso host SP-API. La destinazione
    per il bucket aplus-media torna come policy S3 firmata nella query string dell'url,
    da rispedire come campi form (mai come query string, altrimenti Amazon risponde
    "Conflicting query string parameters"); se un giorno Amazon tornasse un url +
    headers da usare con un PUT diretto (come per altre risorse Uploads API), si sceglie
    in base a cosa è stato effettivamente restituito, non per assunzione."""
    with open(percorso_file, "rb") as f:
        dati = f.read()
    md5 = base64.b64encode(hashlib.md5(dati).digest()).decode()

    risposta = client.post(
        f"/uploads/2020-11-01/uploadDestinations/{_RESOURCE_APLUS}",
        params={"marketplaceIds": marketplace_id, "contentMD5": md5, "contentType": content_type},
    )
    destinazione = risposta["payload"]
    _carica_su_destinazione(destinazione["url"], dati, destinazione.get("headers"))
    return destinazione["uploadDestinationId"]


def _carica_su_destinazione(url: str, dati: bytes, headers: dict | None) -> None:
    scomposto = urlsplit(url)
    campi_policy = dict(parse_qsl(scomposto.query))
    base_url = f"{scomposto.scheme}://{scomposto.netloc}{scomposto.path}"
    if campi_policy:
        r = requests.post(base_url, data=campi_policy, files={"file": dati},
                          headers={"User-Agent": _USER_AGENT}, timeout=60)
    else:
        r = requests.put(url, data=dati, headers={**(headers or {}), "User-Agent": _USER_AGENT},
                         timeout=60)
    if r.status_code >= 300:
        raise SpApiError(f"upload immagine A+ fallito ({r.status_code}): {r.text[:300]}")
