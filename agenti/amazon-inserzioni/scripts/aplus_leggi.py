from __future__ import annotations
from dataclasses import dataclass


@dataclass
class StatoAplus:
    content_reference_key: str | None
    asin: str
    nome: str | None
    stato_documento: str | None
    locale: str | None
    moduli: list[dict]
    badge_asin: str | None  # "CONTENT_PUBLISHED" | "CONTENT_NOT_PUBLISHED" | None


def badge_da_relazioni(relazioni: dict, asin: str) -> str | None:
    """Trova l'ASIN dentro asinMetadataSet (listContentDocumentAsinRelations) e ne
    estrae il primo badge, se presente. Stessa logica usata sia dopo la lettura
    iniziale che dopo la rilettura post-sottomissione: estratta una volta sola."""
    asin_metadata = next(
        (m for m in relazioni.get("asinMetadataSet", []) if m.get("asin") == asin), {})
    badge_set = asin_metadata.get("badgeSet") or []
    return badge_set[0] if badge_set else None


def leggi(client, marketplace_id: str, asin: str) -> StatoAplus:
    """Cerca il documento A+ collegato all'ASIN (searchContentDocuments); se non
    esiste, non prova a leggerne il contenuto — nessun documento è uno stato
    legittimo, non un errore. Se esiste, legge moduli (getContentDocument) e
    badge di pubblicazione dell'ASIN (listContentDocumentAsinRelations)."""
    ricerca = client.get(
        "/aplus/2020-11-01/contentDocuments",
        params={"marketplaceId": marketplace_id, "asin": asin},
    )
    record_trovati = ricerca.get("contentMetadataRecords") or []
    if len(record_trovati) > 1:
        raise ValueError(f"più di un documento A+ trovato per l'ASIN {asin}: serve scegliere quale a mano")
    record = record_trovati[0] if record_trovati else None
    if not record:
        return StatoAplus(content_reference_key=None, asin=asin, nome=None,
                           stato_documento=None, locale=None, moduli=[], badge_asin=None)

    content_reference_key = record["contentReferenceKey"]
    metadata = record.get("contentMetadata", {})

    documento = client.get(
        f"/aplus/2020-11-01/contentDocuments/{content_reference_key}",
        params={"marketplaceId": marketplace_id, "includedDataSet": "CONTENTS"},
    )
    corpo_documento = documento.get("contentDocument", {})
    moduli = corpo_documento.get("contentModuleList", [])
    locale = corpo_documento.get("locale")

    relazioni = client.get(
        f"/aplus/2020-11-01/contentDocuments/{content_reference_key}/asins",
        params={"marketplaceId": marketplace_id, "asinSet": asin, "includedDataSet": "METADATA"},
    )

    return StatoAplus(
        content_reference_key=content_reference_key, asin=asin,
        nome=metadata.get("name"), stato_documento=metadata.get("status"), locale=locale,
        moduli=moduli, badge_asin=badge_da_relazioni(relazioni, asin),
    )
