---
type: reference
title: "Marketplace — Compliance GPSR (strumenti New Era)"
domain: marketplace
created: 2026-09-09
updated: 2026-11-01
tags:
  - reference
  - amazon
  - ebay
  - gpsr
  - compliance
  - new-era
status: current
related:
  - "[[Amazon]]"
  - "[[eBay]]"
  - "[[Agente inserzioni Amazon]]"
  - "[[Agente eBay]]"
sources:
  - "/Users/marianomartinez/.claude/projects/-Users-marianomartinez/memory/project_amazon_gpsr_compliance.md"
  - "/Users/marianomartinez/.claude/projects/-Users-marianomartinez/memory/project_ebay_gpsr_compliance.md"
  - "/Users/marianomartinez/.claude/projects/-Users-marianomartinez/memory/reference_modulo_gpsr_master.md"
---

# Marketplace — Compliance GPSR (strumenti New Era)

Vedi [[Amazon]] e [[eBay]].

Il **GPSR** (General Product Safety Regulation) è obbligatorio da dicembre 2024 per i prodotti non-food venduti in store UE. Qui i tre strumenti New Era per compilarlo e a che punto sono.

## 1. Il modulo master (Google Doc)
`MASTER - Modulo Compliance GPSR (tutti i clienti)`, in Drive → `ONBORDING CLIENTI`: https://docs.google.com/document/d/1LR18S6MD4EzBWgAwXtWFdLBuM57Q2gktoCmU3NdPK_g/edit. Sezioni: A dati cliente, **B produttori per marca** (+ B-bis senza marca, + B-ter varianti di grafia), C responsabile UE di default ed eccezioni, D sicurezza e avvertenze, E etichette e documenti, F note. **Per marca, non produttore unico** (Mariano, 30/08/2026, su dati misurati: Galeone 32/40 articoli a marca propria, Tecno Cold 14/40 "Senza marca/Generico") → marca→produttore come default, override per articolo. Si copia per cliente, si fa compilare, alimenta entrambi i marketplace. Mai collaudato con un cliente reale.

## 2. Amazon — skill separata, da costruire
Non è coperta dall'[[Agente inserzioni Amazon]]: la fonte è anagrafica di conformità, non catalogo. Verificato il 30/08: campi scrivibili via Listings Items API — `gpsr_manufacturer_reference` (livello ASIN), `dsa_responsible_party_address` (responsabile UE, livello SKU per marketplace), `gpsr_safety_attestation`, `compliance_media` — anche su ASIN già live: copre il backlog. Quali product type li richiedono lo dice `getDefinitionsProductType`. Check dal vivo su Phy to Pet: 19 SKU, 6 product type, tutti con i campi nello schema; su 6 SKU campione 4 vuoti, 2 parziali, nessuno col responsabile UE. Azione 🟡. Bloccante: l'anagrafica per cliente non esiste.

**Aggiornamento 01/11/2026 — strada standard attiva.** I campi GPSR sono in `giro.py` della skill amazon-inserzioni (`gpsr_produttore`, `gpsr_responsabile_ue`, `gpsr_attestazione`, `avvertenze`), con anteprima e "vai" come ogni aggiornamento. Prima scrittura dal vivo: ECM, 8 SKU Reladol, tutti ACCEPTED. Prova: lotto ecm-20261101-154509-8766. Regole provate: produttore e responsabile UE accettano **solo e-mail o URL**, mai un nome o un indirizzo postale; `gpsr_safety_attestation` = sì solo se il prodotto non ha avvertenze. Se l'anagrafica del cliente non c'è, la si ricava dagli SKU dello stesso account che hanno già i campi (ECM: `cliente@example.com` sulla maggior parte). L'errore 100632 non sparisce subito dopo la scrittura: Amazon lo ricalcola dopo.

## 3. eBay — estensione costruita, prima scrittura da fare
Estensione di `ebay-aggiorna-inserzione` ([[Agente eBay]]), non skill separata: `regulatory` è un campo come gli altri, con lotto, registro e annulla (464 test verdi). Misurato: `Regulatory` sta in posizione 40 dell'ItemType, dopo `Variations`; **ometterlo da una revisione non lo cancella**; eBay ammette 5 pittogrammi (EBPSP101-104, EBPSP201 = marchio CE) e 12 dichiarazioni, stessi id su IT e DE, mostrati in chiaro nell'anteprima. Tecno Cold: anagrafica ricavata dalle sue 144 inserzioni (29 produttori) → 109 complete, **8 sistemabili dall'agente**, 22 senza marca da decidere per articolo, 5 marche mai compilate. Galeone: 0/66, serve il modulo. Prossimo passo: "vai" sulle 8, solo le mancanti.

## Lezioni apprese
- "- Senza marca/Generico -" non è una marca: un produttore sbagliato è peggio del vuoto, perché il vuoto si vede.
- `ReviseFixedPriceItem` rivalida l'inserzione intera: un rifiuto può non c'entrare col GPSR.
