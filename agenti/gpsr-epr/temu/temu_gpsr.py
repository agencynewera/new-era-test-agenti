"""L'anagrafica GPSR per marca gia' usata da eBay (DESIGN §2): si legge, non si
raccoglie una seconda volta. I dati restano nel file del cliente: qui non si
copiano in anteprima ne' nel registro, si dice solo se ci sono."""
from __future__ import annotations
import os

import yaml

NON_SONO_MARCHE = {"- senza marca/generico -", "senza marca/generico", "senza marca",
                   "generico", "unbranded", ""}


def per_marca(percorso, marca) -> dict:
    with open(os.path.expanduser(percorso)) as f:
        dati = yaml.safe_load(f) or {}
    responsabile = dati.get("responsabile_ue")
    m = (marca or "").strip().lower()
    if m in NON_SONO_MARCHE:
        return {"produttore": None, "responsabile": responsabile}
    for nome, produttore in (dati.get("produttori") or {}).items():
        if nome.strip().lower() == m:
            return {"produttore": produttore, "responsabile": responsabile}
    return {"produttore": None, "responsabile": responsabile}
