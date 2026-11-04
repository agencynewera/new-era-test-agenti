from __future__ import annotations
from modello import OpzioneTrasporto, PianoSpedizione, Spedizione


def _prezzo(opzione: OpzioneTrasporto) -> str:
    if opzione.costo is not None:
        return f"{opzione.costo:.2f} {opzione.valuta}"
    if opzione.convenzionato:
        return "prezzo non ancora disponibile"
    return "nessun costo quotato da Amazon"


def riepilogo(piano: PianoSpedizione) -> str:
    """Vista da sfogliare: tutte le spedizioni con tutte le opzioni. Serve a
    scegliere, non a confermare — per il "vai" si usa `riepilogo_conferma`,
    che nomina una sola spedizione e un solo prezzo."""
    righe_testo = [f"Piano spedizione {piano.inbound_plan_id} — {len(piano.spedizioni)} spedizione/i:"]
    for s in piano.spedizioni:
        pezzi = sum(r.quantita for r in s.righe)
        righe_testo.append(f"\n- {s.shipment_id} → {s.centro_logistico}: {pezzi} pezzi ({len(s.righe)} SKU)")
        if not s.opzioni_trasporto:
            righe_testo.append("  nessuna opzione di trasporto ancora generata")
        for o in s.opzioni_trasporto:
            tipo = "convenzionato Amazon" if o.convenzionato else "corriere proprio"
            righe_testo.append(f"  · {o.corriere} ({tipo}) — {_prezzo(o)}")
    return "\n".join(righe_testo)


def riepilogo_conferma(spedizione: Spedizione, opzione: OpzioneTrasporto) -> str:
    """Il testo del cancello 🟡: una spedizione, un corriere, un prezzo.

    È questo — non il menù di `riepilogo` — che va mostrato subito prima di
    `conferma.conferma_trasporto`, così che il "vai" di Mariano sia legato a
    una scelta precisa e non a un elenco di possibilità."""
    tipo = "convenzionato Amazon" if opzione.convenzionato else "corriere proprio"
    pezzi = sum(r.quantita for r in spedizione.righe)
    righe_testo = [
        f"Sto per prenotare la spedizione {spedizione.shipment_id} "
        f"→ {spedizione.centro_logistico} ({pezzi} pezzi)",
        f"  corriere: {opzione.corriere} ({tipo})",
        f"  costo: {_prezzo(opzione)}",
    ]
    if opzione.richiede_finestra_consegna:
        righe_testo.append("  richiede una finestra di consegna confermata prima della prenotazione")
    righe_testo.append('Confermi? Serve un "vai" esplicito.')
    return "\n".join(righe_testo)
