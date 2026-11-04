from __future__ import annotations
from dataclasses import dataclass
from letto import StatoAttuale
from modifiche import Modifica, componi
from applica import applica


@dataclass
class PianoUnificazione:
    """Aggiornamenti proposti per rendere sku_padre il parent e ogni sku in
    sku_figli un child della relazione di variazione. Stessa forma di
    annulla.PianificaAnnullamento: una coppia (product_type, [Modifica, ...])
    per SKU, pronta per applica.applica. Non inventa nessun valore — gli
    attributi (parentage_level, variation_theme, child_parent_sku_relationship)
    li porta chi chiama pianifica(), letti dallo schema reale della categoria
    (catalogo.schema_categoria), mai hardcoded qui: la forma di questi
    attributi non è fissa e cambia per product_type (DESIGN §3.1)."""
    padre: tuple[str, list[Modifica]] | None
    figli: dict[str, tuple[str, list[Modifica]]]

    def vuoto(self) -> bool:
        return self.padre is None and not self.figli


def pianifica(stato_padre: StatoAttuale | None, attributi_padre: dict,
              stati_figli: dict[str, StatoAttuale],
              attributi_figli: dict[str, dict]) -> PianoUnificazione:
    """`attributi_padre` va sull'unico SKU padre; `attributi_figli` è
    {sku: attributi} e deve avere una voce per ogni sku in `stati_figli`
    (stessi attributi child_parent_sku_relationship/parentage_level, un
    parent_sku diverso per ciascuno se necessario). Confronta con lo stato
    attuale via modifiche.componi: se un SKU è già configurato come voluto,
    non compare nel piano — nessuna scrittura a vuoto."""
    padre = None
    if stato_padre is not None:
        mod = componi(stato_padre, attributi_padre, fonte="unificazione")
        if mod:
            padre = (stato_padre.product_type, mod)

    figli: dict[str, tuple[str, list[Modifica]]] = {}
    for sku, stato in stati_figli.items():
        if sku not in attributi_figli:
            raise ValueError(f"nessun attributo proposto per il figlio {sku}")
        mod = componi(stato, attributi_figli[sku], fonte="unificazione")
        if mod:
            figli[sku] = (stato.product_type, mod)
    return PianoUnificazione(padre=padre, figli=figli)


def esegui(client, seller_id: str, marketplace_id: str, cliente: str,
           piano: PianoUnificazione, *, sku_padre: str | None = None
           ) -> dict[str, tuple[str, list[dict]]]:
    """Applica il piano SKU per SKU (padre compreso, se presente) — chiamare
    SOLO dopo che ogni riga del piano è stata mostrata con diff.rendi_tabella
    e Mariano ha detto 'vai' per quello SKU, stesso cancello di ogni altra
    scrittura di questa skill."""
    esiti: dict[str, tuple[str, list[dict]]] = {}
    if piano.padre is not None:
        if not sku_padre:
            raise ValueError("sku_padre richiesto quando il piano include un padre")
        product_type, modifiche = piano.padre
        esito, issues, _ = applica(client, seller_id, marketplace_id, cliente, sku_padre,
                                    product_type, modifiche, origine="unificazione")
        esiti[sku_padre] = (esito, issues)

    for sku, (product_type, modifiche) in piano.figli.items():
        esito, issues, _ = applica(client, seller_id, marketplace_id, cliente, sku,
                                    product_type, modifiche, origine="unificazione")
        esiti[sku] = (esito, issues)
    return esiti
