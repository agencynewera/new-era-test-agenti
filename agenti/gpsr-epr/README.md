# Agente GPSR ed EPR

## GPSR (sicurezza prodotto UE): caricato

Il GPSR non è un agente a sé: è un pezzo di tre agenti diversi. Qui ci sono le **copie dei file GPSR** di ciascuno, divise per marketplace, per leggerle e collaudarle tutte insieme.

| Marketplace | File qui | Agente completo | Stato |
|---|---|---|---|
| Amazon | `amazon/campi.py` (campi `gpsr_produttore`, `gpsr_responsabile_ue`, `gpsr_attestazione`, `avvertenze`) | `../amazon-inserzioni` (già in questa repo, coi test che girano) | attivo: prima scrittura dal vivo su ECM, 8 SKU, 01/11/2026 |
| eBay | `ebay/gpsr.py`, `ebay/genera_anagrafica.py`, `ebay/RICERCA-GPSR.md`, `ebay/anagrafica.example.yaml` | `ebay-aggiorna-inserzione` + `ebay-core` (non in questa repo) | costruito, prima scrittura dal vivo da fare |
| Temu | `temu/temu_gpsr.py` | `temu-crea-inserzione` (non in questa repo) | in costruzione, manca l'accesso API |

- `REGOLE-GPSR-wiki.md`: le regole e lo stato dalla wiki di New Era (copia del 04/11/2026).
- `ebay/test-originali/`: i test GPSR della skill eBay, da leggere: qui non girano da soli perché chiamano altri moduli della skill.
- Le anagrafiche vere dei clienti (produttori e responsabile UE per marca) **non sono qui**: c'è solo la struttura con valori finti.
- Dati di partenza per un cliente nuovo: il modulo «MASTER - Modulo Compliance GPSR (tutti i clienti)» su Drive (cartella ONBORDING CLIENTI).

Le correzioni al codice vanno fatte nell'agente completo: questa cartella serve per la scheda di test e la visione d'insieme. Se serve collaudare eBay o Temu dal vivo, si caricano le due skill intere.

## EPR (responsabilità estesa del produttore: RAEE, imballaggi, pile…): da fare più avanti

Nessun agente ancora. Da progettare insieme con Mariano.
