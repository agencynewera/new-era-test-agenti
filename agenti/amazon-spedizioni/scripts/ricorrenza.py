"""La spedizione tipo di un cliente (DESIGN §14). Alcuni clienti spediscono
sempre la stessa cosa — Arcipelago 108 pezzi in 36 colli, dieci volte su
undici — e per loro la bozza si può proporre da sola. Chi varia va chiesto."""
from __future__ import annotations
import collections
from dataclasses import dataclass

from modelli import PianoSpedito


@dataclass(frozen=True)
class Tipo:
    sku: str
    pezzi_base: int
    colli_base: int
    modello: str
    volte: int


def _spedizioni_mono_sku(piani: list[PianoSpedito], sku: str) -> list[tuple[int, int, str]]:
    """Per ogni piano che contiene solo quello SKU: pezzi totali, colli, modello
    più usato. Un piano con più SKU non dice niente sull'abitudine di uno solo."""
    fuori = []
    for piano in piani:
        pezzi, colli, per_modello = 0, 0, collections.Counter()
        altri = False
        for b in piano.scatole:
            items = b.get("items") or []
            quante = int(b.get("quantity") or 1)
            d, w = b.get("dimensions") or {}, b.get("weight") or {}
            if (len(items) != 1 or items[0]["msku"] != sku
                    or b.get("contentInformationSource") != "BOX_CONTENT_PROVIDED"
                    or not d or not w):
                altri = True
                break
            pezzi += int(items[0]["quantity"]) * quante
            colli += quante
            per_modello[(b.get("templateName") or "—").strip()] += quante
        if altri or not pezzi:
            continue
        fuori.append((pezzi, colli, per_modello.most_common(1)[0][0]))
    return fuori


def spedizione_tipo(piani: list[PianoSpedito], sku: str, minimo: int = 3) -> Tipo | None:
    """La forma che il cliente ripete. Serve che le spedizioni siano almeno
    `minimo`, che ogni quantità sia un multiplo esatto della più piccola (una
    spedizione doppia è la stessa spedizione fatta due volte) e che un modello
    copra la maggioranza."""
    spedizioni = _spedizioni_mono_sku(piani, sku)
    if len(spedizioni) < minimo:
        return None
    base_pezzi, base_colli, _ = min(spedizioni, key=lambda s: s[0])
    if any(p % base_pezzi for p, _, _ in spedizioni):
        return None
    per_modello = collections.Counter(m for _, _, m in spedizioni)
    modello, quante = per_modello.most_common(1)[0]
    if quante * 2 <= len(spedizioni):
        return None
    return Tipo(sku=sku, pezzi_base=base_pezzi, colli_base=base_colli,
                modello=modello, volte=len(spedizioni))
