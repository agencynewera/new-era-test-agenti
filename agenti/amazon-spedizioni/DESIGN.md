# DESIGN — Agente Amazon New Era: creare spedizioni verso la logistica Amazon (FBA, Seller Central)

**Data**: 2026-08-29 · **Stato**: spec approvata da Mariano il 2026-08-29
**Autonomia**: 🟡 — crea spedizioni e comporta una spesa reale: sempre anteprima con
prezzo visibile e "vai" esplicito prima di **impegnare denaro o qualcosa di
irreversibile**. Il flusso Amazon è a stadi e non tutti pesano uguale: le scritture
preparatorie (creazione del piano, conferma dell'imballaggio, informazioni sulle scatole)
non prenotano nulla e si annullano buttando via il piano intero — ma **a mano, su Seller
Central** (`cancelInboundPlan`): la skill non ha un passo di annullamento e non lo
automatizza, si limita a nominare il `plan_id` da annullare; il
"vai" copre `confirmPlacementOption` — che accetta la commissione di piazzamento e fissa
per sempre la divisione — e `confirmTransportationOptions`, che prenota il corriere. Il
dettaglio operativo sta in `SKILL.md`, sezione "Dov'è il cancello"

---

## 1. Obiettivo

Da SKU + quantità dettati da Mariano in chat, l'agente crea una spedizione in ingresso
verso un centro logistico Amazon (FBA) per un cliente Seller Central: prepara il piano,
mostra le opzioni di trasporto disponibili con relativo costo quando c'è, e — dopo
conferma esplicita — prenota (se corriere convenzionato Amazon) e genera le etichette da
stampare. Si ferma lì: attaccare le etichette e consegnare i pacchi resta un passo umano.

## 2. Perché ora, e cosa esiste già

Terzo pezzo di un giro di lavoro deciso insieme il 2026-08-29 per il cliente pilota **Phy
to Pet** (account Ads noto come "WOOF ITALIA"): inserire articoli nuovi
([[project_amazon_inserzioni]], skill gemella già completata — 70 test verdi, in attesa
solo di credenziali+collaudo), monitorare la salute dell'account
([[project_marketplace_health_monitor]] Fase 2, spapi.py già scritto e collaudato — 55
test verdi) e — qui — spedire prodotto in FBA. I tre condividono lo stesso account
SP-API Seller di Phy to Pet.

**Riuso deciso**: il client SP-API di questa skill parte come copia adattata dello
`spapi.py` già collaudato di `marketplace-health-monitor` (auth LWA con cache token,
retry/backoff sui 429), non da quello di `amazon-inserzioni` (`spapi_client.py`,
GET/PUT/PATCH — pensato per la Listings Items API). La ragione: creare una spedizione è
un flusso POST-e-attendi-che-Amazon-finisca-di-elaborare, la stessa forma del ciclo
report già scritto nel monitor, non una scrittura diretta stile PUT/PATCH.

## 3. Perimetro

**Fa**: prende SKU+quantità dettati da Mariano; crea il piano di spedizione su Amazon
(destinazione/i, come impacchettare); mostra anteprima con corriere/i disponibili e il
prezzo quotato da Amazon quando c'è — sul corriere proprio di New Era resta comunque una
finestra di consegna da confermare e l'etichetta da generare, mai una prenotazione presso
il corriere; **impegna spesa solo dopo un "vai" esplicito**; genera le etichette
scatola/spedizione pronte da stampare; registra ogni tentativo/esito.

**Non fa**: non decide da solo cosa o quanto spedire (nessun collegamento automatico con
le scorte, per ora — Mariano detta ogni volta); non prenota corrieri propri/non
convenzionati (restano una telefonata di New Era, l'agente prepara solo l'etichetta e i
dati da comunicare); non traccia la spedizione dopo la creazione; non tocca Vendor
Central (Biscottini — stessa esclusione decisa per [[project_amazon_inserzioni]], si
valuta un progetto a sé se e quando serve); non gestisce resi o spedizioni in uscita dal
magazzino Amazon.

**Un cliente, un marketplace, alla volta.**

## 4. Dove vive

```
~/.claude/skills/amazon-spedizioni/
  SKILL.md
  DESIGN.md · PLAN.md
  scripts/                 file piatti, stessa convenzione delle skill eBay/Amazon
    spapi_client.py         vendorizzato da marketplace-health-monitor/spapi.py,
                             adattato al ciclo di attesa della Fulfillment Inbound API
                             (operazioni asincrone, non solo report)
    modello.py               RigaSpedizione (sku, quantita), PianoSpedizione (risultato
                             Amazon: spedizioni, centro logistico destinazione,
                             requisiti di imballaggio)
    piano.py                 SKU+quantità → crea il piano spedizione su Amazon
                             (createInboundPlan). Non torna spedizioni: a questo
                             stadio non esistono ancora
    imballaggio.py           opzioni di imballaggio proposte da Amazon, conferma, e
                             dati delle scatole (setPackingInformation: quante,
                             peso, dimensioni) — dettati da Mariano
    piazzamento.py            come Amazon divide il lotto fra centri logistici: è qui
                             che nascono le spedizioni, con la commissione di
                             piazzamento e — a conferma — gli shipmentConfirmationId
    corrieri.py               opzioni di trasporto (di piano, non di spedizione):
                             convenzionato Amazon o proprio, col prezzo quando
                             Amazon lo quota; più le finestre di consegna, che il
                             corriere proprio pretende
    conferma.py               accetta finestra e corriere scelti — sempre dopo il "vai"
    etichette.py               genera/scarica le etichette pronte da stampare
    anteprima.py               riepilogo prima del "vai": pacchi, destinazione, costo
    registro.py                log JSON di ogni tentativo/esito, stile
                             amazon-inserzioni
  tests/
```

Repo git proprio, come `amazon-inserzioni`. Niente venv dedicata: python3 di sistema.

## 5. Il flusso

Gli stadi sono quelli reali della Fulfillment Inbound API v2024-03-20 (verificata sul
modello ufficiale e sul tutorial Amazon, §10). Ogni POST è asincrona: torna un
`operationId` da interrogare finché non è SUCCESS o FAILED.

```
Mariano detta: SKU + quantità (in chat)
      ↓
registro.py: registra_tentativo — prima di chiamare Amazon
      ↓
piano.py: createInboundPlan → un piano, ancora senza spedizioni
      ↓
imballaggio.py: generatePackingOptions → confirmPackingOption →
                setPackingInformation (scatole, peso, misure: le detta Mariano)
      ↓
piazzamento.py: generatePlacementOptions → è qui che nascono le spedizioni,
                con la commissione di piazzamento e i centri di destinazione;
                getShipment (leggi_spedizioni) le legge una a una
      ↓
registro.py: registra_piano
      ↓
corrieri.py: generateTransportationOptions (di piano) → prezzi quotati;
             generateDeliveryWindowOptions per chi usa corriere proprio
      ↓
anteprima.py: riepilogo (menù) → riepilogo_conferma (una spedizione, un
              corriere, un prezzo)
      ↓
════ "vai" ══ parola richiesta, non si deduce, obbligatoria anche senza costo ════
      ↓        da qui in poi si spende e non si torna indietro
piazzamento.py: confirmPlacementOption → nascono gli shipmentConfirmationId
      ↓
registro.py: registra_piazzamento_confermato — la commissione è già spesa:
             si scrive PRIMA di qualunque altra chiamata di rete, rilettura
             delle spedizioni compresa
      ↓
piazzamento.py: getShipment (leggi_spedizioni, di nuovo) → ora le spedizioni
                portano lo shipmentConfirmationId che serve alle etichette
      ↓
conferma.py: confirmDeliveryWindowOptions (se richiesta) → confirmTransportationOptions
      ↓
registro.py: registra_trasporto_confermato
      ↓
etichette.py: getLabels (operazione v0 conservata), PDF salvato sul Desktop
      ↓
registro.py: registra_esito
```

Il registro non è un passo finale: scrive **prima** (tentativo), **durante** (piano,
piazzamento confermato, trasporto confermato) e **dopo** (esito). Le due spese reali —
commissione di piazzamento e corriere — hanno ciascuna la propria riga: fra loro c'è un
tratto di flusso che può rompersi, e una riga sola lo lascerebbe senza traccia. Sequenza
esatta in `SKILL.md`.

## 6. Errori

Stesso principio ereditato da eBay e da `amazon-inserzioni`: **il giro non si blocca,
l'errore si dichiara.**

- SKU non trovato o non pubblicato sul marketplace del cliente → si ferma e chiede, mai
  indovina
- Quantità mancante o non valida → si ferma prima di chiamare Amazon
- Peso, misure o numero di scatole nulli o negativi → si ferma prima di chiamare Amazon,
  nominando la scatola e il gruppo. Sono dati che detta Mariano e che Amazon non può
  correggere: un peso a zero non è un errore di Amazon da registrare, è un dato sbagliato
  da rifare
- Amazon respinge il piano o l'imballaggio (issue) → si registra il motivo letterale,
  non si inventa una correzione
- Rate limit (429) → attesa progressiva e ritentativo, tentativi dichiarati. Anche sulle
  due POST che spendono: Amazon documenta il 429 come stato ritentabile, e la
  strozzatura scatta prima che l'operazione sia presa in carico. **Rischio residuo
  accettato**: `confirmTransportationOptions` non ha una chiave di idempotenza, quindi
  quella garanzia poggia sul comportamento documentato di Amazon, non su una difesa
  nostra. Toglierla renderebbe la skill fragile al normale rate limiting; da rivedere se
  il collaudo dal vivo (Task 11) mostrasse una doppia prenotazione dopo un 429
- **Errore lato server (5xx) su una POST → ESITO INCERTO.** Un 5xx è proprio il caso in
  cui l'operazione può essere stata presa in carico e la risposta persa; un 4xx invece è
  un rifiuto e non allarma
- **Errore di rete su una POST → ESITO INCERTO, e non si ritenta.** Il socket può cadere
  *dopo* che Amazon ha elaborato la richiesta: su `confirmPlacementOption` e
  `confirmTransportationOptions` un ritentativo automatico pagherebbe due volte. L'errore
  lo dice esplicitamente e manda a verificare su Seller Central prima di riprovare. Le
  GET, che non spendono e non modificano, si ritentano invece con lo stesso backoff dei
  429. La caduta sul token LWA è il caso opposto e lo dichiara: nessuna richiesta è
  arrivata ad Amazon, nulla è stato speso
- **Piano creato ma esito non verificato** → l'errore porta dentro il `plan_id`: il piano
  esiste su Amazon e va annullato (`cancelInboundPlan`) o ripreso, mai duplicato con un
  secondo giro sullo stesso lotto
- Risposta 2xx senza `operationId` → `SpApiError` che nomina l'operazione e avverte che
  può essere partita lo stesso, non un `KeyError` fuori contratto
- Token scaduto → refresh automatico, trasparente
- Credenziali assenti o non valide → si ferma prima di provare, e lo dice

## 7. Le credenziali e l'accesso

Come `amazon-inserzioni` (§11 del suo DESIGN): refresh token legato a un'app (client
ID/secret) e all'account Seller di Phy to Pet. **Le credenziali le inserisce e le
rigenera Mariano** — azione 🔴, non tocca mai le mani dell'agente, lette da un file di
configurazione locale mai committato.

Nota per Mariano: essendo Phy to Pet un cliente terzo (non un account New Era), la
creazione dell'app SP-API richiede il flusso normale di autorizzazione (non la
self-authorization possibile solo sui vostri account, come New Era IYP) — serve accesso
a Seller Central di Phy to Pet per completarlo. Verificare in fase di creazione app che
tra i ruoli richiesti ci sia quello che copre la Fulfillment Inbound API (non solo
Listings/Pricing/Inventory, già verificati per gli altri due progetti).

## 8. Collaudo

- **Unitari**: su risposte SP-API salvate come fixture, nessuna chiamata di rete
- **Prova a vuoto**: l'intera catena fino a un istante prima della chiamata, mostrando
  esattamente il payload che si manderebbe ad Amazon
- **Un lotto vero e piccolo**, su Phy to Pet: piano → corriere convenzionato con importo
  minimo verificabile → etichetta generata, verificata a occhio prima di considerare il
  collaudo riuscito

## 9. Prerequisiti aperti

- **App SP-API con il ruolo giusto** (Fulfillment/logistica) e **credenziali
  rigenerate** per Phy to Pet — in corso da parte di Mariano (§7)
- Le altre due skill gemelle condividono lo stesso account: se le credenziali arrivano
  prima per una delle tre, sbloccano anche le altre

## 10. Punti verificati sull'API reale (2026-08-29)

Verificati sul modello ufficiale `fulfillmentInbound_2024-03-20.json` e
`fulfillmentInboundV0.json` (repo `amzn/selling-partner-api-models`), su *Migrating
Fulfillment Inbound workflows* e sul tutorial *Create a shipment with a non-partnered
carrier*.

- **Forma del flusso**: `createInboundPlan` → `generatePackingOptions` →
  `confirmPackingOption` → `setPackingInformation` → `generatePlacementOptions` →
  `generateTransportationOptions` → `generateDeliveryWindowOptions` →
  `confirmPlacementOption` → `confirmDeliveryWindowOptions` →
  `confirmTransportationOptions` → `getShipment` → `getLabels`. Ogni POST è asincrona.
- **Le spedizioni nascono col piazzamento**, non col piano: un `InboundPlan` appena
  creato non ha `shipments`.
- **Peso e dimensioni li dà il venditore**, non il catalogo: `setPackingInformation` è un
  passo obbligato, e senza di esso non si ottengono né piazzamenti né preventivi.
- **Etichette**: PDF via `getLabels`, che è rimasta in **v0** (`/fba/inbound/v0/shipments/
  {shipmentConfirmationId}/labels`, parametri `PageType`/`LabelType` in PascalCase,
  risposta `payload.DownloadURL` valido pochi secondi). L'identificativo è lo
  `shipmentConfirmationId` generato da `confirmPlacementOption`, non lo `shipmentId`.
  Formati di pagina disponibili anche A4 e termici: nessuna stampante obbligata.
  **L'etichetta del corriere convenzionato esce solo nei formati `*_Carrier*`**: dal
  vivo (03/09/2026, UPS convenzionata) `PackageLabel_A4_4` dava la sola etichetta FBA
  (PDF da 4 KB), `PackageLabel_Plain_Paper_CarrierBottom` il foglio combinato FBA sopra
  e UPS col tracking sotto (123 KB). Per questo il formato dei cartoni segue il corriere
  prenotato (`etichette.formato_cartoni`): combinato col convenzionato, 4 per foglio A4
  col corriere proprio; i pallet sempre 4 per foglio.
- **Il piano può dividersi in più spedizioni**: ogni `PlacementOption` porta la lista
  `shipmentIds`, e l'anteprima le mostra tutte.
- **Corriere proprio**: le opzioni `USE_YOUR_OWN_CARRIER` arrivano con precondizione
  `CONFIRMED_DELIVERY_WINDOW` — serve una finestra di consegna confermata, altrimenti
  Amazon rifiuta la prenotazione. Senza informazioni sui pallet non genera le opzioni LTL.
- **Annullamento**: `cancelInboundPlan` (PUT `/inboundPlans/{id}/cancellation`) butta via
  il piano; annulla anche le commissioni del trasporto se chiamato entro la finestra di
  void (24h small parcel, 1h LTL/FTL). **La skill non lo chiama**: nessun modulo in §4 lo
  implementa, resta un gesto manuale di Mariano su Seller Central e gli errori si limitano
  a nominare il `plan_id` da annullare. Non esiste una modalità "solo verifica": la via
  per provare senza impegnarsi è fermarsi prima delle conferme.
- **Limiti di frequenza**: 2 richieste/s sugli endpoint del flusso, burst 30 sulle
  generazioni e 2 sulle conferme — il backoff attuale sui 429 basta. Il valore non è più
  solo dichiarato: `_request` legge `x-amzn-RateLimit-Limit` e lo logga una volta per path
  (misurato il 05/09/2026: 2,0 req/s sul flusso inbound, 0,0222 su `/reports`). L'header dà
  solo il rate, mai il burst, e sul 429 non c'è: serve a sapere la soglia, non il residuo.

Resta da verificare **solo dal vivo**, con credenziali vere (§9): che il ruolo dell'app
copra davvero queste operazioni, e i valori reali su un lotto piccolo di Phy to Pet.

## 11. Fuori perimetro — cosa viene dopo

- Vendor Central
- Collegamento automatico con le scorte (oggi: sempre dettato da Mariano) — un'eventuale
  integrazione futura con [[project_marketplace_health_monitor]] resta un progetto a sé
- Tracciamento della spedizione dopo la creazione — **col corriere proprio non più**:
  dal 14/09/2026 `giro.py tracking` dichiara le lettere di vettura (§18). Resta fuori
  il seguire i colli dopo, che è affare del corriere
- Prenotazione di corrieri non convenzionati Amazon
- **Etichette prodotto (codice a barre Amazon / FNSKU)**, quelle da attaccare sui singoli
  articoli che non hanno un codice a barre proprio. Sono un'altra operazione e un altro
  momento — si stampano sul prodotto prima di imballare, non sulla spedizione. Il giro
  stampa solo le etichette **per cartone** e quelle **per pallet**; dal 03/09/2026 le FNSKU
  le fa `scripts/etichette_fnsku.py`, uno script a parte (§16)
- Altri marketplace Amazon oltre a quello del cliente pilota

## 12. Orchestratore `giro.py` (deciso 2026-09-02)

**Perché.** Fino a oggi il flusso di §5 si eseguiva chiamando a mano i 10 moduli in
chat, 18 passi con dati dettati uno per uno. Il 31/08 tre tentativi su quattro sono
falliti per dati passati male, e le bozze del 29/08 sono rimaste appese su Amazon
perché nessuno sapeva riprenderle. L'orchestratore è un comando solo, con i dati in un
file, che sa a che punto è un piano guardando Amazon.

**Dove.** `scripts/giro.py` (CLI, argparse) più due moduli puri: `scripts/lotto.py`
(legge e valida `lotto.yaml`) e `scripts/stadio.py` (riconosce lo stadio di un piano
dalle risposte di Amazon). I 10 moduli esistenti non cambiano interfaccia; si aggiunge
solo `imballaggio.leggi_articoli_gruppo` (GET `packingGroups/{id}/items`) e
`corrieri.leggi_finestre_consegna` (GET `listDeliveryWindowOptions`), letture.

**Il file di lotto** (`lotto.yaml`, lo scrive Mariano o NOVA):

```yaml
cliente: Phy to Pet
righe:
  - {sku: DENTIFRICIO_SPRAY_DUE, quantita: 20}
scatole:                       # cosa c'è dentro ogni scatola, misure, peso
  - {quantita: 2, peso_kg: 0.8, lunghezza_cm: 10, larghezza_cm: 25, altezza_cm: 15,
     righe: [{sku: DENTIFRICIO_SPRAY_DUE, quantita: 10}]}
data_pronta: 2026-09-05T08:00Z # istante ISO, non un giorno
pallet:                        # facoltativo; senza, niente preventivi LTL
  - {quantity: 2, stackability: NON_STACKABLE, peso_kg: 300,
     lunghezza_cm: 120, larghezza_cm: 80, altezza_cm: 150}
```

`quantita` di una scatola è quante scatole identiche; la somma delle righe di tutte le
scatole deve dare esattamente le righe del lotto, altrimenti `lotto.py` si ferma
nominando lo SKU che non torna. Indirizzo, marketplace e contatto vengono da
`clienti.yaml` (§7), non dal lotto.

**I tre verbi.**

- `giro.py prepara lotto.yaml` — passi 2-10 di SKILL.md: registro, piano, opzioni di
  imballaggio (se più d'una si ferma e le elenca: la scelta è di Mariano), conferma,
  scatole, piazzamenti, preventivi corriere. Stampa `anteprima.riepilogo` **con le
  commissioni di piazzamento** per opzione e si ferma. Subito dopo `createInboundPlan`
  scrive il file locale del piano (sotto).
- `giro.py riprendi <plan_id>` — chiede a `stadio.py` a che punto è il piano e rifà
  solo ciò che manca fino allo stesso menù. Opzioni scadute (piazzamento o preventivi)
  le rigenera da sé: non costano. Se il file locale del piano manca (piano fatto a
  mano), pretende `--lotto lotto.yaml` per avere scatole e data pronta; se il file
  c'è già, un `--lotto` **sostituisce** il lotto salvato (e lo dice), perché la
  verità sono le scatole che passa Mariano, non la copia del primo giro.
- `giro.py conferma <plan_id> --piazzamento P --trasporto T [--finestra F]` — stampa
  `anteprima.riepilogo_conferma` ed esce **senza scrivere**. Con `--vai` esegue i passi
  12-17 nell'ordine di SKILL.md (piazzamento → registro → rilettura → finestra →
  trasporto → etichette → esito). Se un preventivo è scaduto rifiuta e rimanda a
  `riprendi`. A `TRASPORTO_PRENOTATO` e a `COMPLETATO` non resta niente da prenotare:
  scarica solo le etichette, e vuole `--vai` anche per quello (scrive registro e file
  locale). Il `--vai` lo passa NOVA solo dopo il "vai" di Mariano in chat; Mariano
  può usarlo anche da solo. `--pallet N` (o `--pallet SID=N` per spedizione, su un piano diviso) aggiunge le etichette pallet.

**Il file locale del piano** — `~/.config/newera-amazon/spedizioni/giri/<plan_id>.json`:
cliente, riferimento di registro, il lotto così com'è stato dato, le scelte fatte
(packing_option_id, placement_option_id, transportation_option_id per spedizione,
finestra), il percorso delle etichette, e `bloccato` (sotto). Contiene solo ciò che
Amazon non sa; **lo stadio non si salva mai qui**, si rilegge da Amazon ogni volta.

**Gli stadi** (`stadio.riconosci`, funzione pura su piano + scatole + piazzamenti +
spedizioni):

| Stadio | Come si riconosce | `riprendi` fa |
|---|---|---|
| `IMBALLAGGIO_DA_CONFERMARE` | nessuna packingOption ACCEPTED | passi 4-10 |
| `SCATOLE_DA_DICHIARARE` | imballaggio ACCEPTED, `boxes` vuoto | passi 5-10 |
| `PIAZZAMENTO_DA_SCEGLIERE` | placementOption OFFERED non scadute | rigenera preventivi e finestre (non costano), poi menù |
| `PIAZZAMENTO_DA_GENERARE` | nessuna placementOption OFFERED valida (scadute o mai generate) | rigenera piazzamenti e preventivi |
| `TRASPORTO_DA_SCEGLIERE` | placement ACCEPTED, spedizioni con confirmation id, nessun `selectedTransportationOptionId` | preventivi, poi menù |
| `TRASPORTO_PRENOTATO` | `selectedTransportationOptionId` presente | solo etichette (passo 17) |
| `COMPLETATO` | etichette nel file locale | niente, lo dice |

Le scatole si assegnano ai gruppi di imballaggio per SKU (`leggi_articoli_gruppo`);
con un gruppo solo vanno tutte lì. Una scatola con SKU di gruppi diversi ferma il giro.
Anche uno **SKU che Amazon divide fra più gruppi** ferma il giro prima di
`setPackingInformation`, dicendo i pezzi per gruppo (ECM 15/09/2026, piano `wfe91b870f`,
opzione `po528df6af`): le scatole standard sono per SKU, e assegnarle tutte a un gruppo
dichiarerebbe un contenuto sbagliato. Si sceglie con `--imballaggio` un'opzione a gruppo
unico, oppure si rifanno le scatole per gruppo. `solo_piazzamento.py` passa dalla stessa
funzione (`giro._assegna_scatole`).

**Errori.** Ogni `SpApiError` → `registra_esito` col motivo letterale, stampa, uscita 1.
Un errore "ESITO INCERTO" (§6) scrive `bloccato` nel file locale: `riprendi` e
`conferma` rifiutano finché non si passa `--verificato`, che vuol dire "ho guardato
Seller Central". Non si ritenta mai da soli un passo che spende.

**Collaudo.** `stadio.riconosci` su fixture prese dai piani veri letti il 02/09:
`wf570551a7` → IMBALLAGGIO_DA_CONFERMARE, `wf0dae3b7c` → PIAZZAMENTO_DA_GENERARE,
`wf017a1157` (Bedshire) → TRASPORTO_PRENOTATO. `lotto.py` sui casi che non tornano.
`giro.py` end-to-end col client finto di `test_prova_a_vuoto` per ogni verbo e per ogni
stadio di ripresa, incluso `conferma` senza `--vai` che non scrive. Dal vivo:
`riprendi wf0dae3b7c` su Phy to Pet — rigenera le opzioni scadute senza spendere e
riporta al cancello il piano che serve per collaudare il corriere convenzionato.

**Fuori da questa v1.** Scelta interattiva (si passa tutto da riga di comando), memoria
delle scatole per SKU (punto 2 della roadmap, poi fatta in §13), proposta di cosa spedire
(punto 3), tracciamento (punto 4), annullamento del piano (punto 5).

## 13. Scatole dalla storia (deciso 2026-09-03)

**Perché.** Ogni errore vero dei collaudi dal vivo aveva la stessa forma: un dato
scritto a mano. Le scatole sono il dato a mano più grosso di ogni lotto — pezzi per collo,
misure, peso — e Amazon le conserva tutte: su ECM le misure dei colli si sono dovute
scavare a mano dai piani SHIPPED (13 spedizioni, colli identici da un anno), su
Arcipelago il peso dichiarato (2,22 kg per 3 bottiglie da 700 ml) è probabilmente
sbagliato e nessuno l'ha visto. La skill può leggere lo storico, proporre il lotto già
compilato e fermare un lotto dettato che si scosta da ciò che il cliente ha sempre fatto.

**Dove.** Un modulo nuovo, `scripts/scatole.py`, e un verbo nuovo di `giro.py`. I moduli
esistenti non cambiano interfaccia: `giro.prepara` e `giro.riprendi` prendono un
parametro `scatole_nuove` (flag CLI `--scatole-nuove`) e chiamano `scatole.confronta`
prima di scrivere qualunque cosa. Sola lettura verso Amazon: nessun registro coinvolto.

**Sorgente.** `GET /inboundPlans?status=SHIPPED&pageSize=30`, tutte le pagine
(`pagination.nextToken`); l'ordine della lista non è garantito, quindi il filtro per data
si applica a tutte le voci: si tengono i piani con `createdAt` negli ultimi `mesi` (12 di
default, 30 giorni per mese). Per ogni piano tenuto, `GET /inboundPlans/{id}/boxes?pageSize=100`
(paginato come `etichette.leggi_cartoni`): ogni scatola porta `dimensions` (cm), `weight`
(kg), `quantity` (quante scatole uguali), `templateName` (nome del modello su Seller
Central) e `items[]` con `msku`, `quantity`, `asin`, `fnsku`. Gli articoli stanno dentro le
scatole: `/items` del piano non serve. Costo tipico: ECM ha ~13 piani in 12 mesi su 100
totali → 4 pagine di lista + 13 letture di scatole, sotto i 30 secondi ai 2 rps del
backoff.

**Lo standard.** Fanno standard solo le scatole con `contentInformationSource ==
BOX_CONTENT_PROVIDED` e **una sola riga** in `items` (un SKU per collo). Le scatole con
più SKU si contano per piano ("N colli misti non usati") e basta. Per ogni SKU:

- in ogni piano il **collo di riferimento** dello SKU è il mono-SKU con più pezzi; i
  mono-SKU più piccoli dello stesso piano sono resti (un lotto da 130 pz è 2×60 + 1×10) e
  non contano né come standard né come variante;
- lo standard è il collo di riferimento del **piano più recente** (per `createdAt`) in
  cui lo SKU compare in un collo mono-SKU: `pezzi` (quantità della riga), `lunghezza_cm`,
  `larghezza_cm`, `altezza_cm`, `peso_kg`, `modello` (`templateName`, può mancare),
  `asin`, `ultimo_piano`, `ultima_data`;
- ogni combinazione diversa (pezzi, misure, peso) tra i colli di riferimento dei piani
  più vecchi resta come variante con l'ultima data in cui è comparsa: è la riga "9 kg
  (9,4 fino a lug 2025)" della tabella ECM, che oggi si scrive a mano.

Il risultato è un'**istantanea datata**, in
`~/.config/newera-amazon/spedizioni/scatole/<cliente>.json` (nome file col criterio di
`registro._nome_cliente`):

```json
{"cliente": "ECM", "letto_il": "2026-09-03T21:40:00Z", "mesi": 12, "piani_letti": 13,
 "sku": {"Reladol_100": {"asin": "B0CB4DCJPZ", "pezzi": 60, "lunghezza_cm": 30,
                         "larghezza_cm": 40, "altezza_cm": 31, "peso_kg": 9.0,
                         "modello": "P1 - B4", "ultimo_piano": "wf6d283e48-…",
                         "ultima_data": "2026-08-12",
                         "varianti": [{"pezzi": 60, "lunghezza_cm": 30, "larghezza_cm": 40,
                                       "altezza_cm": 31, "peso_kg": 9.4,
                                       "ultima_data": "2025-07-28"}]}},
 "colli_misti_esclusi": 0}
```

**Il verbo.** `giro.py scatole <cliente> [SKU=QTA ...] [--mesi N]`:

1. `spapi_client.crea_client(cliente)`, `scatole.leggi_storico(client, cliente, adesso, mesi)`.
2. Stampa la tabella — SKU, ASIN, pz/collo, misure, peso, modello, varianti — più
   "N colli misti esclusi" e "letti K piani dal AAAA-MM-GG". Salva l'istantanea.
3. Con le quantità (`SKU=QTA`, stessa forma di `--trasporto`, letta con `giro._coppie`)
   scrive `~/Desktop/Lotto-<cliente>-<AAAA-MM-GG>.yaml` come **testo**, non con
   `yaml.safe_dump` (che non scrive commenti):

   ```yaml
   cliente: ECM
   righe:
     - {sku: Reladol_100, quantita: 130}
   scatole:
     - {quantita: 2, peso_kg: 9.0, lunghezza_cm: 30, larghezza_cm: 40, altezza_cm: 31,
        righe: [{sku: Reladol_100, quantita: 60}]}          # standard: P1 - B4, ultimo 2026-08-12
     - {quantita: 1, peso_kg: ,   lunghezza_cm: 30, larghezza_cm: 40, altezza_cm: 31,
        righe: [{sku: Reladol_100, quantita: 10}]}          # parziale, da pesare
   data_pronta:                                            # istante UTC, es. 2026-09-08T08:00Z
   ```

   I colli pieni vengono dallo standard; il resto della quantità va in **un** collo
   parziale con le misure dello standard e `peso_kg` vuoto: il peso di un collo parziale
   non si stima (la tara non è proporzionale), lo pesa chi imballa. `lotto.carica` rifiuta
   già peso e `data_pronta` mancanti, quindi la bozza non parte finché non è completa.
   Scadenze e pallet non sono nello storico: li aggiunge Mariano.
4. Uno SKU chiesto che non è nell'istantanea ferma il comando nominando quelli noti: la
   scatola di un articolo mai spedito la detta Mariano, non la inventa la skill.

**Il controllo.** `scatole.confronta(lotto, istantanea) -> list[str]` è pura. Per ogni
scatola del lotto con una sola riga confronta con lo standard del suo SKU:

- più `pezzi` dello standard: scostamento;
- gli stessi `pezzi`: misure identiche a meno dell'ordine dei lati (terne ordinate,
  arrotondate al decimo di cm) e `peso_kg` entro ±10 % dello standard (9,4 contro 9
  passa; 2,22 contro 7 no);
- meno `pezzi`: è un collo parziale, come quello della bozza — le misure devono
  coincidere, il peso non si confronta perché non è proporzionale.

Ogni scostamento è una riga parlante: "scatola 2 (138_FBA): 2,22 kg dichiarati, lo storico
dice 7 kg (piano wf…, 2026-07-16)". Scatole miste e SKU senza standard producono una
nota, non uno scostamento. Il controllo gira in `giro.prepara` e in `giro.riprendi` quando
riceve un lotto (`--lotto`), **prima** di `registra_tentativo`, del file locale e di
qualunque chiamata ad Amazon, perché una scatola dichiarata non si corregge: resta una
bozza da abbandonare. Con scostamenti e senza `--scatole-nuove` alza `ValueError` col
confronto; con la flag procede e stampa lo stesso confronto come nota. Senza istantanea
stampa "nessuno storico letto per <cliente>: giro.py scatole <cliente>" e procede: il
controllo è un aiuto, non un prerequisito, e un cliente nuovo non ha storia.

**Errori.** Il verbo è sola lettura: un `SpApiError` risale com'è, senza registro. Un
piano SHIPPED le cui scatole non si leggono (403 o 404 su `/boxes`) viene saltato e
nominato nell'output, non ferma il giro: lo standard deve poter uscire anche da uno
storico con buchi. Zero piani nella finestra → tabella vuota, istantanea con `sku: {}`,
e il messaggio dice di allargare `--mesi`.

**Collaudo.** Il cliente finto riceve piani SHIPPED con scatole nella forma vera di
`tests/fixtures/stadio/bedshire_trasporto_prenotato.json`. Test su: standard dal piano più
recente, varianti con data, misti esclusi, finestra temporale e paginazione (lista e
scatole), piano illeggibile saltato; `confronta` su ordine dei lati, tolleranza peso,
pezzi diversi, scatola mista, SKU senza storico; bozza YAML rifiutata da `lotto.carica`
finché incompleta e accettata una volta completata; `prepara` che si ferma senza
registrare né chiamare Amazon, passa con `--scatole-nuove`, procede con la nota se manca
l'istantanea; il CLI. Dal vivo: `giro.py scatole ECM` deve riprodurre la tabella scritta a
mano in `~/nova/CLIENTI/ecm.md`, e `giro.py scatole Arcipelago` deve far emergere il
peso di `138_FBA`.

**Fuori perimetro.** Pallet (non sono nelle scatole), scadenze, correzione delle scatole
di un piano già dichiarato, standard da scatole miste, tolleranze configurabili.

---

## 14. Stock e riassortimento (deciso 2026-09-05)

> **Aggiunta del 2026-10-14.** Mariano: affidata una spedizione, l'agente guarda i modelli
> d'imballaggio creati nell'account, "molto spesso usiamo quelli; se dobbiamo cambiare
> modalità te lo dico io". Quindi `giro.py scatole <cliente> SKU=QTA` parte **di default** da
> `modelli.il_piu_adatto` (stesso ordine della mail di stock), a colli pieni, e salva
> l'istantanea del modello scelto perché `prepara` confronti il lotto con quello.
> `--storico` è il vecchio comportamento §13. Prova dal vivo: Arcipelago `138_FBA=100` →
> modello `PALLET 36 PACCHI`, 102 pz in 34 colli.

**Perché.** Oggi che un cliente stia finendo lo stock lo si scopre guardando, e si guarda
quando qualcuno se lo ricorda. Il conto della dimenticanza è alto: la scheda esaurita
perde posizionamento e nel frattempo le campagne continuano a spendere su un prodotto che
non si può comprare. Il dato per accorgersene c'è già ed è di Amazon, aggiornato ogni
giorno. Su ECM, il 05/09, `Reladol_300` aveva 3 pezzi disponibili, 22 in arrivo e 15
vendite al mese: Amazon consigliava 35 pezzi entro il 12/09, e nessuno lo sapeva.

**Cosa non è.** Non è un riepilogo giornaliero — quello sarebbe rumore. Non è un pannello
da andare a guardare. Non scrive niente su Amazon: si ferma alla bozza, come tutto il
resto della skill.

**Dove.** Dentro `amazon-spedizioni`: moduli nuovi `scripts/stock.py`, `scripts/modelli.py`,
`scripts/ricorrenza.py`, un verbo `giro.py stock` e un LaunchAgent. Non una skill separata:
ciò che produce è l'input di `giro.py prepara`, e riusa `spapi_client`, `clienti`, `scatole`
e `lotto`. Una skill a parte dovrebbe importare dalla cartella di un'altra skill.

> **Un'eccezione, decisa il 2026-09-05.** L'impaginazione della mail è l'unica cosa che questa
> skill prende da fuori: `~/.claude/skills/agenti-mail/scripts/template.py`, condiviso con gli
> altri agenti perché tutte le mail arrivino nella stessa casella con lo stesso aspetto. È una
> dipendenza verso una cartella che può sparire, quindi l'import è protetto: se il template non
> si carica, `giro.py` parte lo stesso e l'errore compare — leggibile — nel momento in cui si
> compone la mail, non al caricamento del modulo. Senza quella rete un `ModuleNotFoundError`
> spegnerebbe ogni verbo di `giro.py`, non solo `stock`, e anche la suite di test.

**Sorgente.** Reports API `2021-06-30`, tipo `GET_RESTOCK_INVENTORY_RECOMMENDATIONS_REPORT`:
`POST /reports` con i `marketplaceIds` del cliente, polling di `processingStatus` finché
`DONE`, `GET /reports/documents/{id}` → URL con TSV gzip. Verificato dal vivo su ECM il
2026-09-05: `DONE` in circa 30 secondi, 115 righe. Colonne usate: `Country`,
`Merchant SKU`, `ASIN`, `Units Sold Last 30 Days`, `Available`, `Inbound`,
`Days of Supply at Amazon Fulfillment Network`, `Alert`,
`Recommended replenishment qty`, `Recommended ship date`. La Fulfillment Inventory API
(`/fba/inventory/v1/summaries`) è raggiungibile con le stesse credenziali ed è il piano B
se il report cambia forma, ma non porta le vendite: da sola non basta.

**La trappola del paese.** Il report ha **una riga per paese** (GB/DE/FR/ES/IT): 115 righe
per 33 SKU su ECM. Si tiene solo la riga del `marketplace_id` del cliente da `clienti.yaml`.
Senza quel filtro il rumore seppellisce il segnale: dei 40+ `out_of_stock` di ECM quasi
tutti sono mercati esteri dove il prodotto non è mai stato stoccato. Per lo stesso motivo
la colonna `Alert` di Amazon **non è la verità**: `Reladol_100` risulta `out_of_stock`
(riga GB) mentre in IT ha 47 pezzi e 51 giorni di copertura. Il segnale lo calcoliamo noi.

> **Correzione 2026-09-05 (revisione finale).** Il paese non si legge più da
> `indirizzo_origine.countryCode` del cliente ma da `stock.paese_di(marketplace_id)`: coincidevano
> finché tutti i clienti erano IT/IT, ma il primo cliente con `marketplace_id` DE e magazzino
> italiano avrebbe chiesto il report giusto (DE) e poi tenuto le righe del paese sbagliato (IT) —
> zero righe, "niente da segnalare", per sempre. I marketplace supportati sono i quattro di
> `clienti.yaml` riga 4 (`PAESE_DEL_MARKETPLACE` in `stock.py`): IT, DE, FR, ES. Aggiungerne uno
> nuovo richiede di estendere quella mappa, non di toccare `clienti.yaml`.

**La soglia.** Il segnale primario è **Amazon che consiglia un riassortimento**: sulla
riga del paese del cliente, `Recommended replenishment qty > 0`, con `Recommended ship date`
a dare l'urgenza. È l'unico numero che conosce i tempi di spedizione e di ricezione, che noi
non possiamo calcolare. La copertura che calcoliamo noi —
`(Available + Inbound) / (venduti_30gg / 30)` — resta come rete di sicurezza e come manopola:
30 giorni di default, eccezioni per cliente e per SKU in `soglie.yaml` dove la vuoi più
nervosa. Terzo segnale, tenuto separato perché è quello che costa di più: **esaurito**,
`Available + Inbound == 0` con un consiglio di riassortimento aperto.

Misurato su ECM il 2026-09-05 (30 righe IT): la copertura da sola avrebbe pescato **un solo
SKU** (`CALMAPROST_FBA`), il consiglio di Amazon ne pesca **sette**. E la copertura da sola
sbaglia due volte. `Reladol_300` ha 3 pezzi a magazzino ma 22 in arrivo, che la portano a 50
giorni: sopra soglia, invisibile, mentre Amazon chiede 35 pezzi entro il 12/09. Soprattutto,
**uno SKU esaurito vende zero e quindi ha copertura infinita**: `GermGEL_5L_FBA`, `Int_Q10`
e `EN-GYM0-KBDL_FBA` hanno 0 disponibili e 0 in arrivo e sarebbero muti proprio nel caso più
grave. Con `venduti_30gg == 0` e nessun consiglio aperto, invece, non c'è segnale: uno SKU
che non vende e che nessuno rifornisce non si riassortisce.

**Il filtro dei dormienti** (deciso da Mariano il 2026-09-05, dopo la prima mail vera). Il
consiglio di Amazon guarda una domanda storica che può non esistere più: sulla mail di ECM
proponeva `GermGEL_5L_FBA`, `Int_Q10` e `EN-GYM0-KBDL_FBA`, tre prodotti che l'agenzia non vende
più. Quindi, prima di segnalare uno SKU **fermo da 30 giorni**, si chiede a
`GET /sales/v1/orderMetrics` quante unità ha fatto negli ultimi **90**: se sono zero, silenzio.
Verificato sui quattro SKU del caso: `Reladol_300` 36 unità in 90 giorni, gli altri tre zero
spaccato. Costa una chiamata solo per gli SKU dubbi — chi ha venduto nell'ultimo mese è già vivo
e non si controlla. Se le vendite non si leggono l'avviso passa lo stesso: uno zero inventato
zittirebbe un allarme vero, e una rottura di stock persa costa più di un avviso di troppo. Chi è
stato taciuto non compare nella mail (il punto è il silenzio) ma sì nell'uscita del comando, o il
filtro diventerebbe una scatola nera.

**Quando parla: la transizione, non lo stato.** Uno SKU sotto soglia resta sotto soglia
anche domani; ripeterlo ogni giorno sarebbe il riepilogo che non vogliamo. Stato per
cliente in `~/.config/newera-amazon/spedizioni/stock/<cliente>.json`, per SKU: zona,
data di ingresso, copertura, ultimo avviso, `Inbound` visto. Le regole:

- da sopra a sotto soglia → **un** avviso;
- resta sotto → silenzio;
- scende sotto la **soglia dura** — esaurito, o copertura sotto 7 giorni → un secondo
  avviso, una volta sola;
- 7 giorni senza che `Inbound` cresca → un sollecito, una volta sola;
- `Inbound` cresce (la spedizione è partita) o la copertura risale sopra soglia → lo stato
  si azzera da sé. Nessuno stato da chiudere a mano: il segnale di "fatto" è lo stesso
  report.

**I modelli d'imballaggio** (regola di Mariano, 2026-09-05: consultare sempre i modelli
creati negli account). Si leggono dai piani SHIPPED come le scatole di §13, ma indicizzati
**per modello e non per SKU**: `templateName` → contenuto (SKU e pezzi), misure, peso,
quante scatole, quanti piani, prima e ultima data. Su ECM: 15 modelli in 12 mesi e
**zero scatole senza modello**. I nomi della forma `P<n> - B<m>` sono generati da Amazon
nel flusso Send-to-Amazon e valgono meno di un nome scritto da una persona: si riconoscono
con una regex e finiscono in fondo alla graduatoria. Un modello non garantisce una scatola
sola — `Reladol 100` è stato usato sia da 30 sia da 60 pezzi — quindi il modello propone,
le misure restano quelle dell'uso più recente.

**Il verso che mancava: scriverli.** La spedizione Arcipelago del 03/09, fatta da noi via
API, ha prodotto 36 modelli usa e getta (`P1 - B1` … `P1 - B36`) al posto dell'unico
`PALLET 36 PACCHI` che il cliente usava da 10 spedizioni. Non mandiamo `templateName`
quando creiamo le scatole, e Amazon se lo inventa: **stiamo sporcando lo storico dei
modelli a ogni spedizione**. Se `BoxInput` accetta il campo — la risposta lo espone,
l'input non è mai stato provato — `imballaggio._scatola_a_box` lo manda. Se lo rifiuta si
dichiara e si rinuncia, ma il tentativo è un passo esplicito del piano.

> **Verificato il 2026-09-05 sul modello ufficiale dell'API: non si può.** `templateName`
> compare **solo** in `Box`, la forma di lettura; `BoxInput` — quello che
> `setPackingInformation` manda davvero — ha `contentInformationSource`, `dimensions`,
> `items`, `quantity`, `weight` e basta. Nell'intero modello `fulfillmentInbound_2024-03-20`
> non esiste nessun percorso né nessuna definizione che riguardi i modelli d'imballaggio.
> Quindi **ogni spedizione fatta via API continuerà a generare nomi usa e getta** `P<n> - B<m>`,
> e non è in nostro potere evitarlo: si può solo crearli a mano su Seller Central.
> Il danno però è contenuto, perché la lettura si difende da sola: la graduatoria di
> §14 mette i nomi automatici in fondo, quindi `PALLET 36 PACCHI` continua a vincere su
> `P1 - B1` finché resta nello storico dei 12 mesi. Da rivalutare se Amazon aggiungerà il
> campo in scrittura.

**La spedizione tipo.** Firma di un piano SHIPPED: SKU con i pezzi totali, numero di colli,
modelli usati. Un cliente è **regolare** su uno SKU quando le ultime spedizioni hanno la
stessa firma, o firme che sono multipli di una base. Misurato il 05/09: Arcipelago ha 10
firme identiche su 11 (108 pezzi, 36 colli, `PALLET 36 PACCHI`) più una doppia (216);
Bedshire spedisce `XJ-YOJW-FUSZ` a 60, 120 o 240 coppie, sempre col modello
`COPPIA CUSCINI 60 PEZZI`, 12 colli ogni 60 pezzi; le altre spedizioni di Bedshire sono
assortimenti che non si ripetono mai. Regolare → si propone quella forma. Vario →
l'avviso elenca i modelli candidati con quante volte sono stati usati, e la bozza si chiede
a mano con `giro.py scatole <cliente> <SKU>=<qta> --modello "<nome>"`.

**La bozza.** Colli **pieni, arrotondati in su**: 35 pezzi consigliati su colli da 22
diventano 2 colli, 44 pezzi. Nessun parziale da pesare, quindi il file è utilizzabile nel
momento in cui arriva. Riusa `scatole.scrivi_bozza`; `data_pronta` resta vuota, perché è
l'unica cosa che deve decidere una persona.

**L'avviso.** Mail, non Telegram: la copertura si misura in giorni, e una mail si inoltra a
chi prepara la spedizione mentre un messaggio sulla chat personale no. Mittente
`promozione@agencynewera.it` (credenziali SMTP IONOS già in casa, `invia_mail.py` della
sentinella Ads), destinatario **`agenti@agencynewera.it`**: la casella unica di tutti gli
agenti, separata per oggetto e non per indirizzo. Oggetto
`[STOCK] <cliente> — N SKU da riassortire`. Corpo: una riga per SKU con disponibili, in
arrivo, venduti negli ultimi 30 giorni, copertura, quantità consigliata, modello scelto o
candidati, e il comando da lanciare. Finché la casella non esiste l'avviso va a
`promozione@` con una nota nel log: mai un invio fallito in silenzio.

**Quando gira.** LaunchAgent una volta al giorno alle **11:00** (scelto da Mariano il
2026-09-05: a quell'ora è sempre alla scrivania), silenzioso. Un report per ogni cliente
di `clienti.yaml` (quattro oggi). Se non attraversa niente, non parte nessuna mail.

**Errori.** Report `FATAL`/`CANCELLED` o oltre il timeout → il cliente è saltato e
**nominato** nella mail degli altri; credenziali assenti o `marketplace_id` mancante →
saltato e nominato; SKU sotto soglia senza modelli in storico → avviso senza bozza, col
motivo scritto; stato illeggibile → si riparte da zero avvisando che si riparte (un
doppione è meglio di un silenzio); nessun cliente in allarme → nessuna mail.

**Fuori perimetro.** Scrivere su Amazon, creare piani, Telegram, ClickUp, il magazzino del
cliente (Amazon non lo conosce), le previsioni stagionali, più marketplace per lo stesso
cliente, e il riassortimento dei clienti senza credenziali SP-API.

**Collaudo.** ECM — al 05/09 il primo giro deve pescare 7 SKU: `CALMAPROST_FBA` (l'unico
che esce anche dalla sola copertura), `Reladol_300`, `PROBIOLAX_FBA`, `ARTINORM_2_FBA`, e i
tre esauriti `GermGEL_5L_FBA`, `Int_Q10`, `EN-GYM0-KBDL_FBA`; una mail sola, non sette. E
Arcipelago (cliente regolare: la bozza deve uscire da sola, 108 pezzi, 36 colli,
`PALLET 36 PACCHI`), in sola lettura, con la mail intercettata prima dell'invio.

**Limite noto: due oracoli sulla stessa domanda.** La bozza del giro dello stock sceglie la
scatola dai *modelli dell'account* (questa sezione), mentre il controllo di `giro.py prepara`
la confronta con l'*istantanea dell'ultimo piano* (§13). Quando divergono — caso reale: il
modello `Reladol 100` è stato usato sia da 30 sia da 60 pezzi — `prepara` rifiuta la bozza
finché non si passa `--scatole-nuove`, che semanticamente è la bandiera sbagliata perché
l'imballo non è cambiato, solo la fonte che ha scelto la scatola è un'altra. In più il
percorso `giro.py stock` non salva mai un'istantanea (quella la scrive solo `giro.py scatole`):
senza un giro precedente il controllo di §13 stampa "Scatole non controllate" e salta del
tutto. Da riconciliare: i due oracoli dovrebbero convergere su una sola fonte di verità, o
`prepara` dovrebbe sapere distinguere "imballo cambiato" da "scatola scelta da un altro
oracolo con lo stesso storico".

## 15. Etichette nella cartella del cliente (deciso 2026-09-07)

**Perché.** Fino a oggi ogni PDF di etichette finiva su `~/Desktop/Etichette-<cliente>-<FBA>.pdf`
e Mariano lo spostava a mano in `10 LOGISTICA/PRIME` della cartella cliente, dove archivia
già tutte le spedizioni. Da oggi ci va direttamente: Mariano le riprende da lì.

**Dove.** `scripts/archivio.py`, una funzione sola: `cartella_spedizione(cartella_cliente,
codice_fba, adesso)` torna (e crea) `~/Desktop/LAVORO/AZIENDE NEW ERA/PERFORMANCE/<cartella>/
10 LOGISTICA/PRIME/<AAAA>/<MM MESE>/<AAAA-MM-GG> <codice FBA>/`. Una cartella per spedizione,
così un piano diviso in due ne ha due; mese numerato e data ISO perché in Finder si ordinino
da sole (la struttura a mano usava `MARZO/12:03:2026`). La data è quella locale, non l'UTC del
registro. `etichette.scarica_etichette` prende `cartella=` e ci salva; senza, resta il Desktop.

**Il nome della cartella** è il campo `cartella` di `clienti.yaml`: le chiavi del file
("Arcipelago", "Phy to Pet") non coincidono con le cartelle sul disco ("Arcipelago Gin",
"Phy to pet"), quindi si scrive, non si deduce. `clienti.py` rifiuta un valore con barre.

**Cosa non fa.** Non crea mai la cartella del cliente (la fa l'onboarding): se manca, o se il
campo non c'è, le etichette restano sul Desktop e `conferma --vai` lo dice in coda al testo.
La spedizione a quel punto è già pagata: una cartella assente non deve fermare le etichette.
Il `lotto.yaml` e l'elenco corrieri restano sul Desktop come prima; le etichette FNSKU dal
11/09 seguono le cartone (§16).

**Collaudo.** `test_archivio.py` (struttura, cartella cliente mai creata, campo assente),
`test_etichette.py` (`cartella=`), `test_clienti.py` (campo letto, assente, con barre),
`test_giro.py` (giro completo con e senza cartella, pallet nella stessa cartella). In sola
lettura le 4 cartelle di `clienti.yaml` risolvono sul disco con la loro `PRIME`.

## 16. Il ritiro non è nel giro, e le FNSKU seguono le cartone (deciso 2026-09-11)

**Il ritiro.** Il giro finiva alle etichette e taceva su chi fa passare il camion. Maricart,
08/09/2026: UPS convenzionata prenotata e pagata (13,78 EUR), tre tracking reali, ASN emesso
— e nessun ritiro, perché col convenzionato in modalità pacchi (`GROUND_SMALL_PARCEL`)
`confirmTransportationOptions` compra le **etichette prepagate**, non un passaggio a
domicilio: `dates` sulla spedizione resta vuoto. Il ritiro lo prenota il venditore su UPS
citando il tracking `1Z…`, oppure porta i colli a un punto UPS (fonte: forum venditori, da
confermare con UPS se è incluso nel prepagato). In più la `data_pronta` del lotto è il
`readyToShipWindow.start` mandato ad Amazon: se è domani, oggi nessuno deve aspettare
nessuno.

Perciò `conferma --vai` chiude con due righe: la data pronta dichiarata, in ora locale, e —
solo col convenzionato a pacchi — l'avviso che il ritiro non è prenotato, col nome del
corriere. Col corriere proprio non c'è niente da dire: il ritiro è del cliente per
definizione. Col convenzionato **pallet** (LTL) non si è ancora visto un caso: nessuna riga,
per non inventare. La riga è testo, non un'azione: prenotare il ritiro resta fuori
perimetro (§11), come lo era.

**Le FNSKU.** `etichette_fnsku.py` scriveva sul Desktop e Mariano spostava il PDF a mano
nella cartella PRIME della spedizione. Ora `destinazione()` legge dal file locale del piano
i percorsi delle etichette cartone e mette il PDF nella stessa cartella (la prima che esiste,
su un piano diviso). Senza etichette cartone — piano non ancora confermato, o cartella
sparita — resta il Desktop: lo script va lanciato dopo `conferma --vai`.

**Collaudo.** `test_giro.py`: col convenzionato a pacchi la riga c'è e nomina corriere e
codice FBA, col corriere proprio no, la data pronta c'è in entrambi i casi (`05/09/2026`).
`test_etichette_fnsku.py`: cartella PRIME dal file locale, Desktop senza file, Desktop se la
cartella è sparita. Lo stesso giorno la prova della CLI (`test_cli_prepara_riprendi_conferma`)
è risultata rossa da sola: usa l'orologio vero e le scadenze fisse del cliente finto
(10/09/2026) erano passate — ora sono relative a oggi.

## 17. Sicurezza del giro (deciso 2026-09-11)

La security review del 06/09 (tre rilievi) e il collaudo serale dello stesso giorno (sette, con
prove) hanno trovato punti in cui il giro poteva perdere un dato pagato, fidarsi di un orologio
fermo, o morire per un file scritto male. Decisioni, in ordine di peso:

- **Un lucchetto per cliente sul registro** (`registro._lucchetto`, `fcntl.flock` su
  `<cliente>.lock` nella cartella del registro). Ogni `registra_*` legge, modifica e scrive
  dentro il lucchetto: venti processi insieme non perdono voci e non corrompono il file. Le
  letture restano senza lucchetto: `os.replace` garantisce che leggano sempre un file intero.
- **Una sola `conferma --vai` per piano alla volta** (`giri.lucchetto(plan_id)`, non bloccante):
  la seconda si ferma subito con "un'altra conferma di <piano> è in corso", prima di leggere la
  foto e di qualunque POST. Senza `--vai` non serve: non si spende. `confirmTransportationOptions`
  non ha chiave di idempotenza (§6): il lucchetto è l'unica difesa dalla doppia prenotazione.
- **Scadenza del token LWA sull'orologio da parete** (`time.time()`, non `time.monotonic()`):
  su macOS `mach_absolute_time()` si ferma nel sonno, un'ora dormita vale due minuti, e il token
  scaduto veniva creduto buono — Amazon rispondeva 403 a giro iniziato. Il polling di
  `attendi_operazione` resta su `monotonic`: lì sotto-contare il sonno è desiderabile.
- **`yaml.YAMLError` dentro il contratto**: `giro.main` lo stampa come `ERRORE:` e torna 1; il
  ciclo per cliente di `giro.stock` lo tratta come gli altri errori del cliente e lo nomina nella
  mail. Prima un `soglie.yaml` con un errore di sintassi uccideva il giro delle 11:00 per tutti i
  clienti, senza mail. PyYAML nomina file e riga nel messaggio.
- **Il nome cliente non compone mai da solo un percorso**: passa da `registro._nome_cliente`
  anche in `etichette.scarica_etichette`, `etichette_fnsku.destinazione` e nell'elenco corrieri
  di `giro` (che quel file lo cancella pure). `"Phy to Pet"` resta uguale; `"X/../../"` resta sul
  Desktop.
- **Chrome non eredita i segreti**: `etichette_fnsku._stampa_pdf` lancia Chrome con un ambiente
  di sola lista bianca (PATH, HOME, TMPDIR, USER, LANG). `spapi_client` carica ancora
  `~/scripts/.env` in `os.environ` a ogni import: cambiarlo tocca cinque agenti gemelli e va
  fatto insieme, non qui.
- **Tre difese piccole**: il nome del modello d'imballaggio va nel commento della bozza su una
  riga sola (un a-capo di origine Amazon aggiungeva chiavi al YAML e svuotava `righe`);
  `lotto._pallet` rifiuta quantità sotto 1 e peso o misure non positive, come già le scatole;
  `stato_stock.salva` scrive su file di appoggio e sostituisce, come il registro.
- **I corpi d'errore di Amazon sono troncati** a 500 caratteri in `spapi_client._dettaglio`:
  finiscono nelle mail e nello stdout che NOVA legge. Il rischio residuo — testo di origine
  Amazon (corrieri, modelli) nello stdout senza marcatura — resta accettato: NOVA tratta ogni
  output di tool come dato (§10 del suo prompt), e marcare ogni stringa renderebbe il giro
  illeggibile.

Fuori da questa sezione, con le prove del 06/09: `conferma` senza `--vai` non fa POST, i file
dei giri e del registro sono atomici, il TLS di `requests`/`urllib` è verificato, `starttls`
verifica il certificato dal 06/09 sera (`51110da`), e dall'11/09 un file della password
leggibile da altri non ferma la mail ma la fa parlare (§14, `31e9b1b`). La storia git contiene
un'anteprima della mail con SKU veri (`cbdfdc2`, poi rimossa): va riscritta prima di aggiungere
un remote.

## 18. Le lettere di vettura del corriere proprio (deciso 2026-09-14)

**Il buco.** Col corriere convenzionato Amazon emette il tracking da sé e lo stampa sotto
l'etichetta FBA (§15, formato `*_CarrierBottom`). Col corriere proprio
(`USE_YOUR_OWN_CARRIER`) non c'è nessun tracking: i codici li ha il cliente, e vanno
dichiarati ad Amazon a mano con `updateShipmentTrackingDetails`. Il giro finiva alle
etichette e all'ASN, e `SKILL.md` lo diceva in "Cosa non fa" — «non traccia la spedizione
dopo la creazione». L'11/09/2026 è servito farlo per ECM (`FBA15MBX78RC`, piano
`wf5545452f`, 11 cartoni BRT) con uno script usa e getta.

**La chiamata.** `PUT /inbound/fba/2024-03-20/inboundPlans/{planId}/shipments/{shipmentId}/trackingDetails`,
corpo `{"trackingDetails": {"spdTrackingDetail": {"spdTrackingItems": [{"boxId", "trackingId"}]}}}`,
asincrona come ogni scrittura della Fulfillment Inbound API. Due cose l'hanno resa un passo
e non una riga: `SpApiClient` mandava POST per qualunque metodo diverso da GET (ora ha un
ramo PUT, con la stessa regola di esito incerto: una risposta persa non significa che Amazon
non abbia accettato), e la **GET** sullo stesso path risponde 403 — i tracking si rileggono
da `getShipment`. Regola durevole nella wiki: `Amazon SP-API - Tracking col corriere proprio`.

**Il ponte fra i due linguaggi.** Il cliente manda i codici raggruppati **per prodotto**
(«ecco gli 11 BRT: 4 del Germ 10x200, 5 del 5x200…»); Amazon li vuole **per cartone**
(`FBA15MBX78RC000001`). Il ponte è lo SKU, letto da `listShipmentBoxes`. Regge solo con
cartoni **mono-SKU**: dentro un gruppo di cartoni identici l'abbinamento è arbitrario e
senza conseguenze, ma con un cartone misto non c'è modo di sapere quale codice gli tocchi,
e si preferisce fermarsi a indovinare.

**Perché è un cancello 🟡 e non una lettura.** Dichiarare le lettere di vettura porta la
spedizione da `READY_TO_SHIP` a **`SHIPPED`**: col corriere proprio è questo — e non una
chiamata a parte — il modo in cui si dice ad Amazon che la merce è partita. Quindi si fa
quando i colli sono davvero usciti, non appena arrivano i codici, e l'anteprima lo scrive.

**Sette guardie, tutte prima della scrittura**, perché Amazon accetta senza discutere anche
un abbinamento sbagliato:

| Caso | Cosa fa |
|---|---|
| Piano diviso senza `--spedizione` | Si ferma ed elenca le spedizioni: i codici arrivano per prodotto, non per spedizione |
| Cartone con più SKU dentro | Si ferma: i gruppi del cliente non bastano a decidere |
| Cartone di cui Amazon non dichiara il contenuto | Si ferma: senza SKU non c'è ponte |
| Codici di uno SKU ≠ suoi cartoni | Si ferma coi due numeri: è il caso che capita davvero, la mail incompleta |
| SKU che non sta in nessun cartone, o cartone che la mail non nomina | Si ferma nominandolo |
| Codice ripetuto o vuoto | Si ferma: un copia-incolla sbagliato Amazon lo accetterebbe in silenzio |
| Cartoni **già** tracciati | Si ferma sempre, anche per uno solo (vedi sotto) |

**La guardia sui cartoni già tracciati (scelta di Mariano, 14/09/2026).** Dei cartoni con
già un codice vogliono dire quasi sempre spedizione sbagliata, più di rado una PUT andata a
metà. L'alternativa — dichiarare solo i mancanti — avrebbe completato da sola il secondo
caso, ma nel primo avrebbe scritto lettere di vettura errate sui cartoni ancora liberi e
mandato in SHIPPED la spedizione sbagliata; e **non sappiamo se un tracking sbagliato si
corregge**, non è mai stato provato. Fermarsi costa solo il fastidio di togliere dalla riga
di comando i codici già fatti. Copre anche, senza una regola in più, il caso del corriere
convenzionato: lì i tracking ci sono tutti e il comando si ferma da sé.

**Dopo la scrittura** si rilegge `getShipment` e si conta: «Amazon ne riporta 11 su 11». Il
registro si scrive **prima** della rilettura — i codici sono già su Amazon, e un errore
nella verifica non deve farli sparire dal registro (`registro.registra_tracking`, che li
somma ai già scritti invece di sostituirli: una dichiarazione completata in due riprese non
deve cancellare la prima).

**Fuori.** Il trasporto a **pallet** (`ltlTrackingDetail`: `freightBillNumber`, `bol`) non
è mai stato visto dal vivo: `leggi_tracking` lo riconosce e si ferma invece di inventare.
E la mail al cliente resta fuori dal giro.

**Collaudo.** `test_tracking.py` (28 casi: abbinamento, ordine dei cartoni, le guardie, il
loader YAML che non trasforma `0123456` nell'ottale 83), `test_giro.py` (anteprima senza
scritture, `--vai` che dichiara-registra-verifica, la verifica che conta i mancanti, piano
diviso, CLI riga e file). Dal vivo: da fare.


## 19. La stima del rifornimento per un periodo (deciso 2026-11-01)

Mariano chiede "quanto mandare per coprire N giorni". Il consiglio del report Restock non è una
quantità da spedire (cieco sugli esauriti, gonfiato dagli eventi), quindi `stima.py` ricalcola il
ritmo da `/sales/v1/orderMetrics` settimanale, AFN, per SKU, 13 settimane intere (quella in corso
fuori). Scelte, tutte nel codice come costanti:

- zeri tolti solo se la mediana delle settimane piene è almeno 5 pz (a 5 di media uno zero per caso
  è sotto l'1%): sotto, lo zero è una vendita vera, e si conta dalla prima settimana venduta;
- picco oltre 3 volte la mediana, e comunque sopra 10 pz, contato come la mediana, con nota;
- in magazzino Available + FC transfer + FC Processing; in arrivo Inbound meno Working (i piani in
  WORKING si segnalano in nota, non si contano);
- intervallo con l'offset di Roma del giorno (estate +02:00, inverno +01:00);
- 0,6 sulle ultime 4 settimane valide, 0,4 su tutte: segue il trend senza inseguire una settimana;
- default transito 10 gg e scorta 14 gg; nessuna stagionalità nella v1 (si aggiunge se serve, con
  lo stesso periodo dell'anno prima);
- vendite non lette → ripiego sul venduto a 30 giorni del report, marcato "stima debole";
- pausa di 2 s fra le richieste: orderMetrics regge 0,5 al secondo, e il 01/11 su ECM tre SKU sono
  andati in 429 a raffica.

Collaudo dal vivo 01/11/2026, ECM, 60 giorni: 40 SKU letti, 8 da mandare, 423 pz, bozza del lotto
scritta per 7 (uno senza modello d'imballaggio). Verifica a mano lo stesso giorno: Germ_5x200_FBA,
O3-JR0U-FCEM e Reladol_300 ricalcolati dalla serie settimanale letta a parte (somma delle settimane =
totale di Amazon), tornano al centesimo. Da lì: soglia dell'esaurito 2 → 5, picco minimo 10,
FC transfer/Processing contati, WORKING esclusi. Secondo giro: 7 SKU, 376 pz, nessun 429.

