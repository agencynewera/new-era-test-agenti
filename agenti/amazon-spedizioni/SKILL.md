---
name: amazon-spedizioni
description: Crea spedizioni verso la logistica Amazon (FBA) per i clienti Seller Central di New Era - dal piano spedizione fino alle etichette da stampare, con anteprima di corriere e prezzo e conferma esplicita prima di ogni prenotazione. Usare quando Mariano chiede di spedire prodotto in FBA, creare una spedizione Amazon, mandare stock al magazzino Amazon per un cliente. Trigger tipici: "spedisci questi articoli ad Amazon per X", "crea una spedizione FBA per X", "manda queste unità al magazzino Amazon di X", "spedizione logistica Amazon per X". NON è amazon-inserzioni (quella crea/aggiorna le inserzioni, non spedisce stock) né il monitor salute account (quello legge, non spedisce). NON tocca Vendor Central. Autonomia gialla - corriere e prezzo sempre mostrati prima, mai una prenotazione senza un "vai" esplicito. Un cliente, un marketplace, alla volta.
---

# Spedizioni Amazon FBA — Seller Central

> **Learnings prima di proporre.** Prima di ogni proposta operativa consulta `~/claude-obsidian/Resources/Learnings/` come dice il suo `AGENTI.md`: cita le card validate per id nel punto in cui incidono, mai quelle in `inbox/`, e se nessuna card copre il caso dillo. Le regole del marketplace e delle API stanno nella wiki: `~/claude-obsidian/bin/wiki-cerca "domanda"`.

Vedi `DESIGN.md` per l'architettura completa e `PLAN.md` per i task di implementazione.

## Dov'è il cancello 🟡

Il flusso Amazon è a stadi, e non tutti gli stadi sono uguali:

- **Prima del "vai"** si può preparare: creare il piano, confermare l'imballaggio,
  dichiarare le scatole, farsi generare le divisioni possibili e i preventivi dei
  corrieri. Sono scritture su Amazon, ma non prenotano niente e non impegnano spesa: il
  piano intero si annulla finché non è spedito — ma **a mano, su Seller Central**
  (`cancelInboundPlan`): la skill non ha un passo di annullamento.
- **Dopo il "vai"** si spende e non si torna indietro: `conferma_piazzamento` accetta la
  commissione di piazzamento quotata da Amazon e fissa per sempre la divisione del piano;
  `conferma_trasporto` prenota il corriere. Sono i passi 12-16 qui sotto.

Perciò il "vai" è **il passo 11**, e va chiesto sul testo di
`anteprima.riepilogo_conferma` — che nomina una spedizione, un corriere e un prezzo soli
— non sul menù di `anteprima.riepilogo`, che elenca alternative e non lega il consenso a
nulla. Obbligatorio anche quando il costo è assente: "nessun costo quotato" non è "gratis".

## Come si usa

I comandi, da `scripts/`, con il client preso da `crea_client` e indirizzo/marketplace/
contatto da `clienti.yaml`:

    python3 giro.py scatole <cliente> [SKU=QTA ...] [--mesi N] [--modello NOME | --storico]  # bozza lotto dai modelli d'imballaggio, lettura
    python3 giro.py prepara lotto.yaml [--scatole-nuove]    # → menù corrieri, nessuna spesa
    python3 giro.py riprendi <plan_id> [--lotto lotto.yaml] [--scatole-nuove] # piano esistente → stesso menù
    python3 giro.py conferma <plan_id> --piazzamento P --trasporto SID=TOID [--finestra SID=DW]
    python3 giro.py conferma ... --vai [--pallet N | --pallet SID=N ...]  # 🟡 prenota e salva le etichette in 10 LOGISTICA/PRIME del cliente
    python3 giro.py stima <cliente> --giorni N | --fino AAAA-MM-GG [--transito 10] [--scorta 14] [--sku S ...]  # quanto mandare per coprire il periodo, lettura
    python3 giro.py asn <plan_id>                           # ripesca il codice ASN, lettura
    python3 giro.py tracking <plan_id> --tracking SKU=COD1,COD2 [--spedizione SID]
    python3 giro.py tracking <plan_id> --file tracking.yaml --vai   # 🟡 corriere proprio: dichiara le lettere di vettura, la spedizione passa a SHIPPED

`scatole` legge dai piani SHIPPED del cliente il collo standard di ogni SKU (pezzi, misure,
peso, modello Amazon), salva un'istantanea datata e, con le quantità, scrive sul Desktop
`Lotto-<cliente>-<data>.yaml` già compilato. **Dal 14/10/2026, con le quantità si parte dai
modelli d'imballaggio creati nell'account** (regola di Mariano: "molto spesso usiamo quelli"):
per ogni SKU il più adatto (nome vero prima dei "P1 - B1" automatici, poi il più recente, poi il
più usato), quantità arrotondata a colli pieni e mostrata ("100 pz richiesti → 102"), gli altri
modelli con nome elencati sotto. `--modello NOME` ne impone uno; `--storico` torna al vecchio
modo (ultima scatola vista, resto in un collo parziale da pesare) — si usa solo se Mariano dice
di cambiare modalità. `data_pronta` resta vuota. `prepara` e `riprendi --lotto`
confrontano ogni scatola mono-SKU con l'istantanea (pezzi, misure, peso entro ±10 %) e si
fermano sullo scostamento **prima** di toccare Amazon: `--scatole-nuove` fa passare un
imballo cambiato davvero. Senza istantanea lo dicono e procedono. Vedi `DESIGN.md` §13.

Su un piano già noto, `riprendi --lotto` **sostituisce** il lotto salvato: le scatole
buone sono quelle del file, non quelle del primo giro. A corriere già prenotato (o a
piano completato) `conferma --vai` scarica soltanto le etichette — ma il `--vai` serve
anche lì.

`lotto.yaml` (forma completa in `DESIGN.md` §12): cliente, righe SKU+quantità, scatole con
contenuto/misure/peso, `data_pronta` come istante UTC, pallet facoltativi. Il `--vai` lo
passa NOVA **solo** dopo il "vai" di Mariano sul testo stampato da `conferma` senza `--vai`.
Un "ESITO INCERTO" blocca il piano finché non si ripete con `--verificato`.

La procedura qui sotto è ciò che `giro.py` esegue: resta la referenza per i singoli passi.

## Procedura

1. Mariano detta SKU + quantità da spedire (mai dedotti da soli).
2. `registro.registra_tentativo(cliente, righe)` → tieni il **riferimento** tornato (es.
   `"2026-08-29#0#a1b2c3d4"`): lo vogliono tutte le scritture successive di questo lotto.
   Identifica UN lotto, non una posizione — così un lotto che attraversa la mezzanotte UTC
   resta il proprio, e un riferimento passato al lotto o al cliente sbagliato viene
   respinto invece di scriverci sopra una spesa reale.
   Si registra il lotto **prima** di creare il piano, cioè prima di sapere come Amazon lo dividerà. Al
   registro si passano sempre gli **oggetti veri** degli stadi (`RigaSpedizione`,
   `Spedizione`, `OpzioneTrasporto`): li converte lui in JSON, non serve rifarli a mano.
3. `piano.crea_piano(client, righe, indirizzo_origine, marketplace_id)`.
   `indirizzo_origine`, `marketplace_id` e `contatto` (passo 8) vengono da
   `clienti.carica_cliente(cliente)`, che legge `clienti.yaml` nella cartella della skill:
   non si inventano e non si dettano in chat. Un cliente assente dal file si aggiunge
   prima di partire, copiando `sourceAddress` da un suo piano esistente. Torna un `PianoSpedizione` **senza spedizioni**: a questo punto non
   esistono ancora.
   Chi prepara ed etichetta ogni articolo (`prepOwner`/`labelOwner`) **lo decide Amazon,
   per SKU**: `crea_piano` lo chiede da sé a `prepDetails`. Se per uno SKU Amazon non
   dichiara nulla (`prepCategory: UNKNOWN`) si ferma e lo nomina, invece di indovinare.
   **Articoli con scadenza** (`is_expiration_dated_product` sul listing, tipico di
   liquidi e presidi medici): Amazon vuole `expiration` (AAAA-MM-GG) per riga, altrimenti
   crea il piano e lo manda in **ERRORED** con "Expiration date required" (misurato il
   03/09/2026 su ECM: piano wffe754095). La data la detta Mariano nel lotto
   (`righe: - {sku, quantita, scadenza: 2028-03-14}`); le scatole con lo stesso SKU la
   ereditano. Un piano ERRORED non è una bozza: non va annullato e non si riprende.
   Misure e pesi delle scatole di lotti passati li legge `giro.py scatole <cliente>` dai
   piani **SHIPPED** (il default di `listInboundPlans` mostra solo gli ACTIVE, bozze vuote).
4. `imballaggio.genera_opzioni_imballaggio` → mostra le opzioni a Mariano →
   `imballaggio.conferma_imballaggio` sull'opzione scelta → assegna
   `piano.packing_option_id = opzione.packing_option_id`.
5. `imballaggio.imposta_informazioni_imballaggio(client, plan_id, {gruppo_id: [Scatola,
   ...]}, marketplace_id)` — quante scatole, quanto pesano, quanto misurano. I
   `gruppo_id` sono i `packing_group_ids` dell'opzione confermata; i dati delle scatole
   li detta Mariano. Senza questo passo Amazon non sa dividere il piano né quotare un
   corriere. Il `marketplace_id` serve a rileggere i vincoli di preparazione: il
   contenuto dichiarato qui deve ripetere **identici** gli owner mandati col piano,
   altrimenti Amazon rifiuta con "did not contain expected items and/or quantities".
   Se l'opzione confermata mette lo **stesso SKU in più gruppi** (ECM, 15/09/2026), il giro
   si ferma prima di questo passo e dice quanti pezzi vanno in ciascun gruppo: o si
   rifanno le scatole per gruppo, o si sceglie con `--imballaggio` un'opzione a gruppo unico.
6. `piazzamento.genera_opzioni_piazzamento` — ogni opzione dice in quante spedizioni
   Amazon dividerebbe il lotto e quanto costa quella divisione (`commissioni`). Mostra le
   alternative **con la commissione**: è una spesa, va vista prima del "vai".
7. `piazzamento.leggi_spedizioni(client, plan_id, opzione.shipment_ids)` → assegna
   `piano.spedizioni`. Poi `registro.registra_piano(cliente, riferimento,
   piano.inbound_plan_id, piano.spedizioni)`.
8. `corrieri.genera_opzioni_trasporto(client, plan_id, placement_option_id, shipment_ids,
   data_pronta, contatto=..., pallet=...)` — una sola chiamata per tutte le spedizioni
   dell'opzione. `data_pronta` è la data in cui la merce è pronta al ritiro (ISO 8601):
   senza, Amazon non quota. Senza `pallet` non genera le opzioni LTL: `pallet` come lista
   vale per **tutte** le spedizioni, e se il lotto è diviso si passa
   `{shipment_id: [pallet]}` — altrimenti a ogni spedizione viene quotato il carico
   intero. Ogni pallet è `{"quantity", "stackability", "weight": {"value", "unit": "KG"},
   "dimensions": {"length", "width", "height", "unitOfMeasurement": "CM"}}`;
   `stackability` vale `STACKABLE` o `NON_STACKABLE` (non `NOT_`: respinto dal vivo il 31/08). Assegna il risultato a `spedizione.opzioni_trasporto`, filtrando per
   `opzione.shipment_id`.
9. Se l'opzione che interessa ha `richiede_finestra_consegna` (tipico del corriere
   proprio): `corrieri.genera_finestre_consegna(client, plan_id, shipment_id)` e fatti
   scegliere la finestra da Mariano insieme al corriere.
   **Il flag non basta**: misurato il 03/09/2026 su Arcipelago, l'opzione
   `USE_YOUR_OWN_CARRIER`/`FREIGHT_LTL` del carrier "Other" lo dichiara `false`, e
   `giro.py` salta il passo — ma senza finestra confermata Amazon lascia la spedizione
   senza data e **non emette l'ASN**. Col corriere proprio la finestra si genera e si
   conferma comunque, chiedendo a Mariano quale.
10. `anteprima.riepilogo(piano)` — il menù: spedizioni, destinazioni, corrieri, prezzi.
    Poi, per la spedizione e l'opzione scelte,
    `anteprima.riepilogo_conferma(spedizione, opzione)`.
11. **"vai" esplicito di Mariano** sul testo del passo 10 — obbligatorio, non si deduce,
    anche quando il costo è assente. Da qui in poi si spende.
12. `piazzamento.conferma_piazzamento(client, plan_id, placement_option_id)` → assegna
    `piano.placement_option_id`. Qui la commissione è addebitata: la chiamata seguente è
    il registro, non Amazon.
13. `registro.registra_piazzamento_confermato(cliente, riferimento, placement_option_id,
    opzione.commissioni, opzione.valuta)` — **subito**, prima di qualunque altra chiamata
    di rete: la commissione di piazzamento è già spesa e irreversibile, e tutto ciò che
    viene dopo — la rilettura delle spedizioni compresa — può fallire per conto suo. Se
    la traccia arrivasse dopo, un errore di rete al passo 14 lascerebbe nel registro solo
    "lotto morto in lettura", senza riga della spesa già fatta.
14. Di nuovo `piazzamento.leggi_spedizioni(client, plan_id, opzione.shipment_ids)` →
    riassegna `piano.spedizioni`: la conferma ha appena generato gli
    `shipment_confirmation_id`, che servono alle etichette. Tieni da parte l'opzione di
    trasporto scelta al passo 10: la rilettura costruisce `Spedizione` nuove, con
    `opzioni_trasporto` di nuovo vuota.
15. Per ogni spedizione che lo richiede: `conferma.conferma_finestra_consegna(client,
    plan_id, shipment_id, delivery_window_option_id)`.
16. `conferma.conferma_trasporto(client, plan_id, [opzioni scelte], contatto=...)` — una
    chiamata sola, una scelta per spedizione. Assegna
    `spedizione.trasporto_confermato = opzione` e poi
    `registro.registra_trasporto_confermato(cliente, riferimento, {shipment_id: opzione, ...})`
    — **tutte** le spedizioni prenotate da quella chiamata insieme: Amazon le paga con
    una chiamata sola, registrarle una alla volta perderebbe le altre se la prima
    scrittura fallisce.
17. **Etichette per cartone** (quelle che si attaccano sulle scatole):
    `cartoni = etichette.leggi_cartoni(client, plan_id, spedizione.shipment_id)`, poi
    `etichette.scarica_etichette(client, spedizione.shipment_confirmation_id, cliente,
    cartoni=cartoni)` → assegna `spedizione.etichette_path`. Senza l'elenco dei cartoni
    Amazon respinge con 400: la skill si ferma prima e lo dice.
    Il formato delle etichette cartone lo decide il corriere prenotato
    (`etichette.formato_cartoni`). **Col convenzionato Amazon** escono in
    `PackageLabel_Plain_Paper_CarrierBottom`: una pagina per cartone, etichetta FBA
    sopra e quella del corriere col tracking sotto. Misurato dal vivo il 03/09/2026
    (ECM, UPS convenzionata): con `PackageLabel_A4_4` Amazon torna la sola etichetta
    FBA, senza quella UPS, e il cartone non parte. **Col corriere proprio** restano
    **4 per foglio A4** (`PackageLabel_A4_4`), come le stampa il cliente: 36 cartoni in
    9 fogli invece di 36. Le etichette **pallet** escono sempre 4 per foglio, le 4 di un
    bancale in un foglio solo da tagliare in quattro. Al rientro a
    `TRASPORTO_PRENOTATO` il corriere prenotato si rilegge da Amazon
    (`selectedTransportationOptionId`), non dal registro.
    **Se la merce va su pallet** servono anche quelle:
    `scarica_etichette(..., tipo_etichetta="PALLET", numero_pallet=N)`, con N dettato da
    Mariano — quanti bancali siano lo sa solo chi ha bancalizzato. Su un piano diviso N è
    per spedizione (`--pallet SID=N`, ripetibile): un numero solo vale per una spedizione
    sola, e `giro.py` lo rifiuta prima del "vai".
    Le etichette **prodotto** (codice a barre Amazon/FNSKU, per gli articoli senza codice
    proprio) sono un'altra cosa, fuori dal giro: le stampa `scripts/etichette_fnsku.py
    <cliente> <plan_id> [--per-foglio 21|24]` (Chrome headless, sandbox Bash disattivato),
    da lanciare **dopo** `conferma --vai` perché il PDF va nella stessa cartella PRIME
    delle etichette cartone; prima, resta sul Desktop. Vedi `DESIGN.md` §11 e §16.
18-bis. **Codice ASN** (`amazonReferenceId`): è il codice che il trasportatore cita ad
    Amazon per essere accettato allo scarico — col corriere proprio, senza quello il
    camion non entra. Col corriere convenzionato Amazon lo emette insieme al codice FBA;
    col corriere proprio **qualche minuto dopo**, quando `conferma --vai` ha già finito.
    Perciò si ripesca con `giro.py asn <plan_id>`, che lo rilegge e lo registra. Se dice
    "non ancora emesso", si riprova: non è un errore.
    I PDF (cartoni e pallet) finiscono nella cartella PRIME del cliente:
    `~/Desktop/LAVORO/AZIENDE NEW ERA/PERFORMANCE/<cartella>/10 LOGISTICA/PRIME/<AAAA>/
    <MM MESE>/<AAAA-MM-GG> <codice FBA>/` — una cartella per spedizione, `<cartella>`
    è il campo `cartella` del cliente in `clienti.yaml` (deciso il 07/09/2026: Mariano
    le riprende da lì). Se il campo manca o la cartella cliente non esiste, restano sul
    Desktop e il giro lo scrive in coda ("solo sul Desktop: ..."): la spedizione è già
    pagata, una cartella assente non la ferma. In coda il giro ripete la `data_pronta`
    dichiarata ad Amazon (ora italiana) e, **col corriere convenzionato a pacchi**, avverte
    che il ritiro **non** è prenotato: Amazon vende le etichette prepagate, il passaggio
    del corriere lo chiede il cliente citando il tracking (Maricart 08/09/2026, UPS non è
    passato). Questa riga va riportata a chi imballa. Vedi `DESIGN.md` §16. Poi
    `registro.registra_etichette(cliente, riferimento, shipment_id,
    shipment_confirmation_id, etichette_path)`: il codice FBA e il percorso del PDF sono i
    riferimenti che servono dopo — ristampe, assistenza Amazon, riconciliazione — e senza
    questa riga resterebbero solo nella chat.
18. `registro.registra_esito(cliente, riferimento, esito)` — sempre, anche quando è andata
    male, col motivo letterale.
19. **Solo col corriere proprio, e solo quando i colli sono davvero usciti**: le lettere di
    vettura. Il cliente le manda per mail raggruppate per prodotto; Amazon le vuole per
    cartone. `giro.py tracking <plan_id> --tracking SKU=COD1,COD2` (ripetibile, o
    `--file tracking.yaml`) mostra l'abbinamento cartone → codice e non scrive niente;
    col `--vai` lo dichiara (`updateShipmentTrackingDetails`, PUT), rilegge per contare
    quanti Amazon ne riporta, e scrive `registro.registra_tracking`. **È il passo che porta
    la spedizione da READY_TO_SHIP a SHIPPED**: col corriere proprio è così che si dice ad
    Amazon che la merce è partita, quindi non si fa appena arrivano i codici. Si ferma da
    sé — prima di scrivere — sui cartoni misti, sui conteggi che non tornano (la mail è
    incompleta), sui codici ripetuti, su un piano diviso senza `--spedizione` e su
    qualunque cartone che abbia **già** un tracking (che è anche il caso del corriere
    convenzionato: lì il tracking lo mette Amazon e non c'è niente da fare). Il trasporto a
    pallet non è coperto. Vedi `DESIGN.md` §18.

Se uno stadio alza `SpApiError`, il giro si ferma lì: si riporta il motivo letterale e si
registra l'esito (passo 18). Non si inventa una correzione e non si ritenta da soli.
Quando l'errore dice **ESITO INCERTO** (rete caduta su una POST che spende) o nomina un
`plan_id` già creato, l'esito registrato lo ripete alla lettera e si verifica su Seller
Central prima di rifare qualunque cosa: non si ritenta lo stesso passo.

## Un piano creato via API si chiude solo via API

Misurato il 03/09/2026 su ECM (wf5545452f): a piazzamento confermato senza trasporto,
Seller Central mostra il flusso ma **blocca il Passaggio 2** ("Hai completato
parzialmente l'equivalente del passaggio 2 sul tuo software... è necessario richiamare
generatePlacementOptions, generateTransportationOptions, confirmPlacementOption e
confirmTransportationOptions"). Il cliente non può scegliere corriere e data dal sito:
anche col corriere proprio il trasporto va **dichiarato via API** (opzione
`USE_YOUR_OWN_CARRIER`, non convenzionata: nessuna prenotazione, nessun ritiro), e per
farlo servono da Mariano corriere, modalità (pacchi SPD / pallet LTL) e data pronta merce.
`solo_piazzamento.py` quindi non basta da solo: dopo il piazzamento si passa a
`giro.py riprendi` + `conferma --trasporto` con l'opzione del corriere proprio.
Chiuso così il 03/09 su ECM (SPD, opzione "Other" = BRT non in lista): per i pacchi
SPD l'opzione non ha la precondizione `CONFIRMED_DELIVERY_WINDOW`, `giro.py` rifiuta
`--finestra` ed è giusto così; l'ASN (32IQRVWF) è arrivato **subito** con la conferma,
non minuti dopo. Il classificatore di sicurezza può bloccare `giro.py conferma` anche
senza `--vai`: l'anteprima si ricostruisce con `anteprima.riepilogo_conferma` sulle sole
letture.

## Stock e riassortimento

    python3 giro.py stock [CLIENTE ...] [--prova]   # avvisa per mail gli SKU da riassortire, sola lettura

Un giro quotidiano (LaunchAgent alle 11:00, `launchd/it.agencynewera.amazon-spedizioni-stock.plist`)
che legge per ogni cliente di `clienti.yaml` il report Restock di Amazon, lo confronta con
la soglia di copertura e il consiglio di riassortimento di Amazon, e manda **una** mail
sola a `agenti@agencynewera.it` con gli SKU sotto soglia — solo se qualcosa è cambiato
dall'ultimo giro, non ogni giorno. Se il cliente ripete sempre la stessa spedizione
(`ricorrenza.spedizione_tipo`), la mail porta già la bozza del lotto scritta sul Desktop;
altrimenti elenca i modelli d'imballaggio noti e il comando per farla a mano. Un cliente il
cui report fallisce non ferma gli altri: viene nominato nella mail. Senza argomenti gira su
tutti i clienti di `clienti.yaml`; `--prova` stampa la mail invece di mandarla e non salva
lo stato. Sola lettura: l'unica scrittura verso Amazon è la richiesta del report.

Lo stato (cosa è già stato avvisato, per non ripetere il segnale ogni giorno) sta in
`~/.config/newera-amazon/spedizioni/stock/<cliente>.json`; le soglie di copertura (default
e per cliente/SKU) in `soglie.yaml` alla radice della skill. Vedi `DESIGN.md` §14.

## Stima del rifornimento per un periodo

"Quanto devo mandare in FBA per X per i prossimi 2 mesi / fino a Natale" → `giro.py stima`. Sola
lettura, R0: nessun piano, nessuna prenotazione. Per ogni SKU del report Restock:

    pz al giorno = 0,6 × media delle ultime 4 settimane a stock + 0,4 × media delle settimane a stock (ultime 13)
    fabbisogno   = pz al giorno × (periodo + transito + scorta)
    da inviare   = fabbisogno − disponibili − in arrivo, poi a colli pieni col modello d'imballaggio

Le settimane a zero di un articolo che vende almeno 5 pz/settimana sono esauriti e si tolgono; una
settimana oltre 3 volte la mediana (e oltre 10 pz) è un evento e conta come la mediana (wiki: *Report Restock e
consiglio di riassortimento*). Consegna sul Desktop `Stima-FBA-<cliente>-<data>.xlsx` (con il
consiglio di Amazon accanto, per confronto) e `Lotto-<cliente>-<data>.yaml` per gli SKU che hanno un
modello; gli altri restano nel foglio con "scatola da dettare". Se dice "stima debole" o "WORKING
non contati", leggere la nota prima di spedire. In magazzino contano anche FC transfer e FC Processing;
gli "in arrivo" ancora in WORKING no. Poi, se Mariano decide di mandare: `giro.py prepara`.

## Cosa non fa

Non decide da sola cosa o quanto spedire; non prenota corrieri non convenzionati (li
chiama New Era: l'agente prepara etichetta e finestra di consegna); non segue i colli
dopo la partenza — le lettere di vettura del corriere proprio le **dichiara** (passo 19),
ma dove sono arrivati lo dice il corriere; non dichiara i tracking delle spedizioni a
pallet (LTL); non manda mail al cliente; non tocca Vendor Central. Vedi `DESIGN.md` §3,
§11, §18.

## Credenziali

Dal 31/08/2026 sono **per cliente** in `~/scripts/.env`: `PHYTOPET_SPAPI_LWA_CLIENT_ID`,
`_LWA_CLIENT_SECRET`, `_REFRESH_TOKEN`, `_REGION` (stessa convenzione di
`amazon-inserzioni`). Il client si costruisce con `spapi_client.crea_client("Phy to Pet")`
— mai `SpApiClient(...)` a mano: ogni cliente ha la sua app LWA e un refresh token vale
solo con l'app che lo ha emesso. Le inserisce e le rigenera solo Mariano — vedi
`DESIGN.md` §7.
