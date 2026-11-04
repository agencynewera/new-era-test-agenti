from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RigaSpedizione:
    sku: str
    quantita: int
    # AAAA-MM-GG, solo per gli articoli con data di scadenza
    # (`is_expiration_dated_product`): senza, Amazon crea il piano e lo manda
    # in ERRORED con "Expiration date required" (misurato il 2026-09-03 su ECM).
    scadenza: str | None = None


@dataclass
class Scatola:
    """Una scatola (o N scatole identiche) del lotto, come la descrive
    Mariano: quante ce ne sono, quanto pesa, quanto misura. `righe` è il
    contenuto dichiarato — se assente, Amazon conta a mano al ricevimento."""
    quantita: int
    peso_kg: float
    lunghezza_cm: float
    larghezza_cm: float
    altezza_cm: float
    righe: list[RigaSpedizione] = field(default_factory=list)


@dataclass
class OpzioneImballaggio:
    packing_option_id: str
    descrizione: str
    packing_group_ids: list[str] = field(default_factory=list)


@dataclass
class OpzionePiazzamento:
    """Un modo in cui Amazon propone di dividere il piano fra centri
    logistici. `commissioni` è la commissione di servizio di piazzamento
    quotata da Amazon per questa divisione (None = nessuna commissione
    quotata)."""
    placement_option_id: str
    shipment_ids: list[str] = field(default_factory=list)
    commissioni: float | None = None
    valuta: str = "EUR"


@dataclass
class FinestraConsegna:
    delivery_window_option_id: str
    inizio: str
    fine: str
    disponibilita: str = ""


@dataclass
class OpzioneTrasporto:
    transportation_option_id: str
    corriere: str
    convenzionato: bool
    costo: float | None = None
    valuta: str = "EUR"
    shipment_id: str = ""
    modalita: str = ""
    richiede_finestra_consegna: bool = False
    # `quote.expiration`: un preventivo scaduto non si può più confermare.
    scadenza: str | None = None


@dataclass
class Spedizione:
    shipment_id: str
    centro_logistico: str
    righe: list[RigaSpedizione] = field(default_factory=list)
    opzioni_trasporto: list[OpzioneTrasporto] = field(default_factory=list)
    trasporto_confermato: OpzioneTrasporto | None = None
    etichette_path: str | None = None
    # Generato da Amazon solo a piazzamento confermato: è l'identificativo
    # in stile FBA1234ABCD che le operazioni v0 superstiti (getLabels,
    # getBillOfLading) vogliono al posto dello shipmentId di v2024-03-20.
    shipment_confirmation_id: str | None = None
    # Anch'esso generato solo a piazzamento confermato: è il codice che il
    # trasportatore cita ad Amazon per essere accettato alla consegna (in
    # agenzia lo si chiama ASN). Diverso dal codice FBA: quello identifica la
    # spedizione, questo autorizza chi la porta.
    amazon_reference_id: str | None = None


@dataclass
class PianoSpedizione:
    inbound_plan_id: str
    spedizioni: list[Spedizione] = field(default_factory=list)
    packing_option_id: str | None = None
    placement_option_id: str | None = None
