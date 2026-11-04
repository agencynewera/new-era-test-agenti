"""Il catalogo del venditore letto con searchListingsItems (Listings Items
API 2021-08-01): tutto in una passata paginata, con stati, issues e attributi.

Perche' non il report GET_MERCHANT_LISTINGS_ALL_DATA: quello arriva in 15-25
minuti, ha la colonna quantity che e' il disponibile e non la giacenza, e non
porta ne' gli issues ne' gli attributi. La ricerca risponde in 1-2 s a pagina
(verificato dal vivo su Phy to Pet 2026-09-03: 27 SKU, 2 pagine), filtra gia'
lato Amazon per padre di variazione, stato e severita' degli avvisi, e porta
gli attributi interi da cui leggere "tempo di gestione 6".
"""
from __future__ import annotations
import re
from dataclasses import dataclass

import campi
from letto import StatoAttuale
from urllib.parse import quote

PAGINA = 20
INCLUDI = "summaries,issues,attributes,fulfillmentAvailability"
_SEVERITA = ("ERROR", "WARNING", "INFO")
_RE_MANCANTI = re.compile(r"attributi chiave:\s*(.+?)(?:\.\s|\.$|$)")


@dataclass
class Voce:
    sku: str
    asin: str
    product_type: str
    stati: list
    titolo: str
    issues: list
    attributi: dict
    canali: list

    @property
    def in_vendita(self) -> bool:
        return "BUYABLE" in self.stati

    def stato(self) -> StatoAttuale:
        return StatoAttuale(sku=self.sku, product_type=self.product_type, attributi=self.attributi)


def _voce(item: dict, marketplace_id: str) -> Voce:
    riassunti = item.get("summaries") or [{}]
    s = next((r for r in riassunti if r.get("marketplaceId") == marketplace_id), riassunti[0])
    return Voce(sku=item.get("sku", ""), asin=s.get("asin", ""), product_type=s.get("productType", ""),
                stati=list(s.get("status") or []), titolo=s.get("itemName", ""),
                issues=list(item.get("issues") or []), attributi=item.get("attributes") or {},
                canali=list(item.get("fulfillmentAvailability") or []))


def cerca(client, seller_id: str, marketplace_id: str, *, padre: str | None = None,
          in_vendita: bool | None = None, severita: str | None = None) -> list[Voce]:
    """Tutto il catalogo (o la parte che passa i filtri di Amazon). Un errore
    a meta' paginazione risale: un catalogo parziale farebbe credere che gli
    SKU mancanti siano a posto."""
    if severita is not None and severita not in _SEVERITA:
        raise ValueError(f"severita '{severita}' sconosciuta: {', '.join(_SEVERITA)}")
    params = {"marketplaceIds": marketplace_id, "pageSize": PAGINA, "includedData": INCLUDI}
    if padre:
        params["variationParentSku"] = padre
    if in_vendita is True:
        params["withStatus"] = "BUYABLE"
    elif in_vendita is False:
        params["withoutStatus"] = "BUYABLE"
    if severita:
        params["withIssueSeverity"] = severita
    percorso = f"/listings/2021-08-01/items/{quote(seller_id, safe='')}"
    voci: list[Voce] = []
    token = None
    while True:
        risposta = client.get(percorso, params={**params, "pageToken": token} if token else dict(params))
        voci += [_voce(i, marketplace_id) for i in risposta.get("items") or []]
        token = (risposta.get("pagination") or {}).get("nextToken")
        if not token:
            return voci


def filtra(voci: list[Voce], *, prefisso: str | None = None, dove: dict | None = None) -> list[Voce]:
    """Filtri locali: prefisso dello SKU e valore ATTUALE di un campo in
    italiano ("tempo di gestione" = 6), letto con campi.valore_attuale."""
    attesi = {nome: campi.interpreta(nome, valore) for nome, valore in (dove or {}).items()}
    out = []
    for v in voci:
        if prefisso and not v.sku.startswith(prefisso):
            continue
        if all(_uguale(campi.valore_attuale(nome, v.stato()), atteso) for nome, atteso in attesi.items()):
            out.append(v)
    return out


def _uguale(attuale, atteso) -> bool:
    if attuale is None:
        return False
    if isinstance(atteso, float) and isinstance(attuale, (int, float)):
        return abs(float(attuale) - atteso) < 0.005
    return attuale == atteso


def attributi_mancanti(voce: Voce) -> list[str]:
    """Gli attributi che Amazon dice mancare nell'avviso 18448 ("mancano
    alcuni attributi chiave: a, b.")."""
    nomi: list[str] = []
    for i in voce.issues:
        if str(i.get("code")) != "18448":
            continue
        m = _RE_MANCANTI.search(i.get("message", ""))
        if m:
            nomi += [n.strip() for n in m.group(1).split(",") if n.strip()]
    return nomi


def tabella(voci: list[Voce], con_issues: bool = False) -> str:
    righe = [f"{len(voci)} SKU"]
    for v in voci:
        conteggi = {}
        for i in v.issues:
            conteggi[i.get("severity", "?")] = conteggi.get(i.get("severity", "?"), 0) + 1
        avvisi = ", ".join(f"{n} {sev}" for sev, n in sorted(conteggi.items())) or "nessun avviso"
        stato = "in vendita" if v.in_vendita else "NON in vendita"
        righe.append(f"{v.sku} | {v.asin} | {stato} | {avvisi} | {v.titolo[:70]}")
        if con_issues:
            for i in v.issues:
                righe.append(f"    {i.get('severity', '?')} {i.get('code', '')}: {i.get('message', '')[:160]}")
            mancanti = attributi_mancanti(v)
            if mancanti:
                righe.append(f"    attributi mancanti: {', '.join(mancanti)}")
    return "\n".join(righe)
