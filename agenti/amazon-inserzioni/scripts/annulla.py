from __future__ import annotations
from modifiche import Modifica
import registro
from applica import applica


def pianifica_annullamento(cliente: str) -> dict[str, tuple[str, list[Modifica]]]:
    """Calcola cosa annulla_ultimo_lotto scriverebbe, SENZA scrivere nulla:
    nessuna chiamata ad applica/client. Restituisce {sku: (product_type,
    [Modifica, ...])} per gli SKU che hanno almeno una modifica di ripristino
    da proporre. Ripristina TUTTI gli aggiornamenti accettati registrati OGGI
    per questo cliente (non solo l'ultimo in senso stretto) — un SKU compare
    una sola volta, con la sua ultima riga di oggi. Se l'ultima riga
    accettata di oggi per uno SKU è essa stessa un ripristino (origine=
    "annulla"), quello SKU non compare nel piano: non c'è nulla di nuovo da
    annullare finché non arriva un aggiornamento vero, altrimenti si
    riannullerebbe il ripristino appena fatto."""
    righe = registro.ultimo_lotto(cliente, "aggiornamenti")
    ultima_per_sku: dict[str, dict | None] = {}
    for riga in righe:
        if riga.get("esito") != "ACCEPTED":
            continue
        if riga.get("origine", "aggiornamento") == "annulla":
            # Appena ripristinato: nulla da annullare per questo SKU finché
            # non arriva un nuovo aggiornamento vero (altrimenti si
            # ririannullerebbe il ripristino appena fatto).
            ultima_per_sku[riga["sku"]] = None
        else:
            ultima_per_sku[riga["sku"]] = riga  # l'ultima con quello sku sovrascrive

    piano: dict[str, tuple[str, list[Modifica]]] = {}
    for sku, riga in ultima_per_sku.items():
        if riga is None:
            continue
        modifiche = [
            # Un "prima" vuoto è un campo aggiunto dal nulla: il ripristino è
            # dopo=None, che costruisci_patch traduce in op delete (solo per
            # fonte "annulla" — smoke 2026-09-02).
            Modifica(campo=campo, prima=v["dopo"],
                     dopo=v["prima"] if v["prima"] not in (None, "", [], {}) else None,
                     fonte="annulla")
            for campo, v in riga["campi"].items()
        ]
        if not modifiche:
            continue
        piano[sku] = (riga["product_type"], modifiche)
    return piano


def annulla_ultimo_lotto(client, seller_id: str, marketplace_id: str, cliente: str
                          ) -> dict[str, tuple[str, list[dict]]]:
    """Esegue il piano di pianifica_annullamento: chiamare SOLO dopo che
    Claude ha mostrato l'anteprima (via diff.rendi_tabella per ogni SKU) e
    Mariano ha detto 'vai' — stesso cancello di ogni altra scrittura."""
    piano = pianifica_annullamento(cliente)
    esiti: dict[str, tuple[str, list[dict]]] = {}
    for sku, (product_type, modifiche) in piano.items():
        esito, issues, _ = applica(client, seller_id, marketplace_id, cliente, sku,
                                    product_type, modifiche, origine="annulla")
        esiti[sku] = (esito, issues)
    return esiti
