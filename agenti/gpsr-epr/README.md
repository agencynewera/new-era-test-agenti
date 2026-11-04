# Agente GPSR ed EPR

**GPSR (sicurezza prodotto UE).** Su Amazon non è una skill a parte: i campi stanno nel `giro.py` di **amazon-inserzioni** (`gpsr_produttore`, `gpsr_responsabile_ue`, `gpsr_attestazione`, `avvertenze`), con anteprima e "vai" come ogni aggiornamento. Prima scrittura dal vivo: ECM, 8 SKU, il 01/11/2026. Il codice sta in [`../amazon-inserzioni`](../amazon-inserzioni).

Dati di partenza: il modulo master «MASTER - Modulo Compliance GPSR (tutti i clienti)» su Drive (cartella ONBORDING CLIENTI), una riga di produttore per marca. Su eBay c'è un'estensione costruita, non ancora usata dal vivo.

**EPR (responsabilità estesa del produttore: RAEE, imballaggi, pile…).** Non c'è ancora nessun agente. Da progettare insieme: cosa serve per marketplace e paese, da dove si prendono i numeri di registrazione, cosa si può scrivere via API.

In questa cartella: la scheda di test per la parte GPSR e, quando si decide, il progetto della parte EPR.
