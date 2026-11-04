# DESIGN — Agente Amazon New Era: creare e aggiornare inserzioni (Seller Central)

**Data**: 2026-08-29 · **Stato**: spec approvata da Mariano il 2026-08-29
**Autonomia**: 🟡 — scrive su account cliente, sempre dopo un "vai" (creazione) o un
permesso esplicito per inserzione (aggiornamento)

---

## 1. Obiettivo

Da una fonte di dati prodotto (file fornitore, link, dati a mano) o da un'inserzione già
online, l'agente crea nuove inserzioni su Amazon Seller Central o aggiorna quelle
esistenti (prezzo, quantità, contenuto), mostrando sempre un'anteprima e scrivendo solo
dopo un permesso esplicito.

È l'equivalente Amazon degli Stadi 5 (crea) e 2 (aggiorna) già costruiti per eBay, ma
**un solo progetto**, non due: qui creare e aggiornare condividono l'autenticazione
SP-API e la lettura dello schema di categoria fin dal primo giorno — separarli avrebbe
duplicato lavoro senza un motivo reale (§3.1).

## 2. Perché ora, e cosa esiste già

- **`multi-marketplace-uploader`** (giugno 2026): modello prodotto interno + exporter
  Amazon flat-file (solo categoria Casa e Cucina), pensato per caricamento manuale.
  Prevedeva "pubblicazione automatica via SP-API" come fase futura — è questa.
- **`marketplace-health-monitor`** (Fase 2, luglio 2026): ha già scritto un client
  SP-API (`spapi.py`, `connectors/amazon.py`) per leggere ordini e inventario, ma è
  fermo sul Task 0 = credenziali da rigenerare. Lo stesso blocco vale qui (§11, §13).
- **eBay Stadio 5 / Stadio 2**: il pattern collaudato — estrarre → validare → anteprima
  a semaforo → "vai" → scrivere uno alla volta → rileggere → registrare → annullare —
  si riusa quasi di peso. Cambia il livello sotto, per via delle differenze API (§3.1).

## 3. Perimetro

**Fa**: crea inserzioni nuove; aggiorna prezzo, quantità, titolo, bullet point,
descrizione, immagini e attributi di inserzioni già online; valida contro lo schema
attributi Amazon della categoria target prima di proporre; mostra sempre un'anteprima;
scrive solo su "vai" (creazione) o permesso esplicito per inserzione (aggiornamento);
rilegge per confermare l'esito; registra; sa annullare l'ultimo lotto.

**Non fa**: non tocca Vendor Central (§3.2); non gestisce ordini, spedizioni, resi, Buy
Box o pubblicità (quella è l'hub Amazon Ads, sistema separato); non decide prezzi da
solo; non inventa attributi mancanti; non pubblica o scrive senza permesso esplicito;
non fa ancora caricamento massivo senza anteprima riga per riga.

**Un cliente, un marketplace, alla volta.**

### 3.1 Perché un solo progetto, e cosa cambia rispetto a eBay

Su eBay creare e aggiornare sono stati due progetti perché il secondo è nato mesi dopo,
riusando un nucleo (`ebay-core`) già collaudato in produzione. Qui non esiste un nucleo
precedente: va scritto comunque, e scriverlo due volte — una volta in un progetto
"crea", richiamato da un progetto "aggiorna" — sarebbe un costo inventato. Le due
operazioni entrano dritte in un solo repo, con un solo modulo di autenticazione e un
solo lettore di schema categoria (§4).

**Quello che non cambia rispetto a eBay**: il cancello di conferma, il principio "mai
inventare un campo mancante", la scrittura uno alla volta con log prima/dopo.

**Aggiornamento 2026-08-29 — lezione da `ebay-aggiorna-inserzione`**: la revisione
finale di quel progetto ha bocciato il ramo su tre Critical, tutti rilevanti anche qui:
specifiche multivalore appiattite in scrittura, un campo vuoto proposto in aggiornamento
che ha cancellato il pubblicato, e `annulla` che ricomponeva il ripristino da più righe
di storico invece che da una sola. Il piano (§14 del DESIGN eredita in `modifiche.py`,
`applica.py`, `annulla.py`) codifica esplicitamente le tre protezioni corrispondenti, con
un test per ciascuna: mai un valore "dopo" vuoto nel pacchetto di modifiche, sempre
l'array multi-valore per intero in un'operazione di patch, `annulla` legge sempre e solo
l'ultima riga di registro per quello SKU.

**Quello che cambia (Amazon ≠ eBay)**:

- Niente "sostituzione in blocco" di array interi come su eBay (`Variations`,
  `ItemSpecifics`, `PictureDetails`): la Listings Items API lavora per attributo, con
  operazioni JSON Patch (aggiungi/sostituisci/rimuovi). **Da verificare al piano** se
  questo valga anche dentro un singolo attributo multi-valore (es. `bullet_point`,
  immagini) o se lì corra lo stesso rischio di sostituzione in blocco (§14).
- Ogni scrittura torna con un elenco di **issues** (errori e avvisi) anche a esito
  accettato — concetto che eBay non ha. Diventa la fonte del semaforo 🟡 anche *dopo*
  il "vai", non solo prima (§9).
- Lo schema degli attributi obbligatori **non è fisso**: va richiesto ad Amazon per
  ogni categoria (Product Type Definitions API) e può cambiare nel tempo. Si legge, non
  si hardcoda — con una cache locale per non richiederlo a ogni giro.

### 3.2 Perché non Vendor Central

Deciso il 2026-08-29. Vendor Central lavora a Purchase Order emessi da Amazon, non a un
catalogo che il venditore scrive liberamente: "creare un'inserzione" lì ha un significato
diverso, e in parte non dipende da New Era. Resta un progetto a sé — per Biscottini, se
e quando serve.

## 4. Dove vive

```
~/.claude/skills/amazon-inserzioni/
  SKILL.md
  DESIGN.md · PLAN.md
  scripts/                 file piatti, sys.path.insert nei test — stessa
                            convenzione delle skill eBay (verificato al piano:
                            nessuna skill esistente usa sottocartelle/package)
    spapi_client.py         sessione, auth LWA, retry/backoff, get/put/patch
    catalogo.py             Product Type Definitions API → schema attributi
                            per categoria, con cache su disco
    modello.py              Prodotto (dataclass canonico)
    registro.py             log JSON di ogni tentativo/esito, condiviso da
                            crea.py e applica.py
    estrattore_manuale.py   dati dettati da Mariano → Prodotto
    estrattore_generico.py  pagina prodotto → dati strutturati
                            schema.org/Open Graph → Prodotto
    estrattore_fornitore.py file + mappatura YAML → [Prodotto] (stile
                            multi-marketplace-uploader)
    validazione.py          Prodotto + schema categoria → semaforo, cosa manca
    crea.py                 payload Listings Items API, putListingsItem
                            un'inserzione alla volta
    letto.py                getListingsItem → stato attuale, per intero
    modifiche.py            pacchetto di modifiche (campo, prima, dopo,
                            fonte) + filtro + helper per attributi multi-valore
    diff.py                 anteprima prima → dopo
    applica.py              patchListingsItem con solo i campi approvati,
                            rilettura di verifica
    annulla.py              ripristina dall'ultima riga di registro per SKU,
                            mai da una ricomposizione
  tests/
```

Un solo repo git, come `multi-marketplace-uploader` — non due come
`ebay-core`/`ebay-crea-inserzione` (§3.1). Niente venv dedicata: come le skill
eBay, gira sul python3 di sistema (`requests`, `yaml`, `openpyxl`, `pytest`
già presenti).

## 5. Il modello prodotto — riuso del contratto

Stessi nomi campo di `core/model.py` dell'uploader (`sku`, `ean`, `title`, `brand`,
`description`, `price`, `quantity`, `category`, `images`, `condition`, `bullet_points`,
`attributes`, `weight`, `dimensions`, `mpn`) più un campo nuovo, obbligatorio per Amazon
e assente su eBay: **`product_type`** — il tipo di categoria Amazon (es. `HOME`,
`KITCHEN`), necessario per sapere quale schema attributi interrogare. Un lotto preparato
dall'uploader resta compatibile: si aggiunge solo `product_type`.

## 6. Il flusso — creare

```
fonte (fornitore / link / a mano) → estrattore → Prodotto canonico
      ↓
catalogo.py: schema attributi della categoria (Product Type Definitions API, cache)
      ↓
validazione.py: per ogni prodotto, pubblicabile? semaforo 🟢🟡🔴 — mai inventare un
                 attributo mancante
      ↓
anteprima in chat, tabella con semaforo
      ↓
potatura — Mariano toglie righe, corregge prezzo/quantità
      ↓
"vai" — parola richiesta, non si deduce
      ↓
crea.py: putListingsItem, un'inserzione alla volta, log prima di partire
      ↓
lettura di conferma (getListingsItem) + issues dichiarati (§9)
      ↓
esito: pubblicate / respinte con motivo / in verifica (§9.1)
```

## 7. Il flusso — aggiornare

```
bersaglio (SKU) → letto.py: getListingsItem → stato attuale, per intero
      ↓
contenuto nuovo da una delle fonti del §6, o dettato → pacchetto di modifiche
      ↓
modifiche.py: filtra — scarta valori identici, valori non validi per lo schema
      ↓
diff.py: anteprima prima → dopo, per inserzione
      ↓
permesso — un "vai" per inserzione, mai lotto implicito, mai per iniziativa
      ↓
applica.py: patchListingsItem con SOLO i campi approvati
      ↓
rilettura di verifica + issues dichiarati
      ↓
registro.py: prima/dopo/esito · annulla.py: ripristina, passando dallo stesso cancello
```

## 8. Le immagini

Diversamente da eBay — che richiede di scaricare e ricaricare le foto sul proprio
servizio immagini (EPS) — l'attributo immagine di Amazon accetta un URL che Amazon
recupera da sé al momento della pubblicazione (`main_product_image_locator` e simili).
**Da verificare al piano** su una categoria vera: se non fosse così, l'agente avrebbe
bisogno di un passaggio di upload oggi non previsto.

## 9. Gli issues di Amazon (§3.1)

Ogni risposta di `putListingsItem`/`patchListingsItem` porta un elenco di *issues* con
una severità (`ERROR`, `WARNING`, `INFO`) anche quando lo stato è `ACCEPTED`. Vanno
sempre mostrati dopo il "vai", non solo sui fallimenti: è il semaforo giallo che su eBay
si esauriva tutto in fase di validazione, e che qui vive anche dopo la scrittura.

### 9.1 Sincrono o no

Da verificare al piano: se `putListingsItem`/`patchListingsItem` confermano lo stato
subito o solo dopo una propagazione (con un `submissionId` da interrogare a parte). Se è
asincrono, "rilettura di conferma" nei flussi (§6, §7) significa attendere e ripetere la
lettura finché lo stato non si assesta — non una singola `getListingsItem` immediata.
Punto da chiarire prima di scrivere `crea.py` e `applica.py`.

## 10. Errori

Principio unico, ereditato da eBay: **il giro non si blocca, l'errore si dichiara.**

- Categoria/`product_type` sconosciuto o non trovato → si ferma e chiede, mai indovina
- Attributo obbligatorio mancante → riga 🔴, esclusa dal "vai" finché non completata
- Amazon respinge (issue `ERROR`) → si registra il motivo letterale, si continua, il
  resoconto finale dice quante e perché
- Rate limit (429) → attesa progressiva e ritentativo, con i tentativi dichiarati
- Token scaduto → refresh automatico, trasparente
- Credenziali assenti o non valide → si ferma prima di provare, e lo dice (§11)

## 11. Le credenziali e l'accesso

Diversamente da eBay (consenso OAuth per cliente, dal browser), SP-API usa un refresh
token legato a un'app (client ID/secret) e all'account Seller collegato — un giro di
autorizzazione per account cliente, non per ogni operazione. **Le credenziali le
inserisce e le rigenera Mariano**: è un'azione 🔴 secondo la matrice di autonomia, non
tocca mai le mani dell'agente. Vanno lette da un file di configurazione locale (stile
`.env`, mai committato), non chieste in chat.

## 12. Collaudo

- **Unitari**: su risposte SP-API salvate come fixture, nessuna chiamata di rete
- **Prova a vuoto**: l'intera catena fino a un istante prima della chiamata, mostrando
  esattamente il payload JSON che si manderebbe ad Amazon
- **Un prodotto vero**, sul cliente/categoria pilota (§13): creazione di un'inserzione,
  poi un aggiornamento reversibile (es. quantità) verificato con rilettura, poi
  "annulla"

### 12.1 Sandbox

Da verificare al piano se le credenziali SP-API di New Era funzionino in sandbox: su
eBay non funzionavano, e se vale lo stesso qui la prima scrittura vera sarà su un
account vero, con le stesse mitigazioni (prova a vuoto + singolo prodotto ritirabile).

## 13. Prerequisiti aperti

- **Cliente e categoria pilota**: da confermare da Mariano prima del Task 1 — dimensiona
  lo schema minimo da mappare e il primo collaudo reale
- **Credenziali SP-API**: da rigenerare (§11) — blocca il collaudo dal vivo ma non il
  design né la maggior parte dell'implementazione (tutto ciò che è testabile su fixture
  procede)

## 14. Punti da verificare al piano

- Se il JSON Patch della Listings Items API valga a livello di intero attributo (es.
  sostituire tutti i `bullet_point` per cambiarne uno) o campo per campo — determina se
  serve la stessa cautela del "prima" di eBay per gli array
- Se `putListingsItem`/`patchListingsItem` sia sincrono o richieda di interrogare un
  `submissionId` (§9.1)
- Il formato esatto dell'attributo immagine per la categoria pilota, e se accetti URL
  esterni senza upload (§8)
- I limiti di frequenza reali (richieste/secondo) sull'endpoint Listings Items, per
  calibrare il backoff
- Se esista una modalità "solo verifica" (dry-run) equivalente a
  `VerifyAddFixedPriceItem` di eBay

## 15. Fuori perimetro — cosa viene dopo

- Vendor Central (§3.2)
- Caricamento massivo senza anteprima riga per riga
- Altri marketplace Amazon oltre a quello del cliente pilota — il modello prodotto
  condiviso con l'uploader è già pensato per non rifare questo lavoro
- Collegamento con l'hub Amazon Ads (New-to-Brand, audit) — sistemi separati, nessun
  piano di integrazione oggi
