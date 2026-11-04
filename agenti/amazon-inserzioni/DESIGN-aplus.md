# DESIGN — Estensione: Contenuti A+ (A+ Content)

**Data**: 2026-09-02 · **Stato**: spec approvata da Mariano il 2026-09-02
**Autonomia**: 🟡 — scrive su account cliente, sempre dopo un "vai" esplicito per documento
**Estende**: `DESIGN.md` (creare/aggiornare inserzioni) — stesso repo, stesso client SP-API,
stesso registro, stesso cancello di conferma. Non lo sostituisce.

---

## 1. Obiettivo

Leggere e scrivere i **Contenuti A+** (A+ Content Manager di Seller Central) per gli ASIN
già gestiti da `amazon-inserzioni`: testo e immagini a moduli sotto la scheda prodotto,
con anteprima prima→dopo e scrittura solo dopo un "vai" esplicito — stesso principio delle
inserzioni, applicato a un'API diversa.

## 2. Perché ora, e cosa esiste già

Richiesta di Mariano (2026-09-02): estendere l'agente inserzioni anche a "contenuti +,
storie del marchio, video e store" — in pratica tutto ciò che è modifica grafica su
Amazon. Prima di disegnare, verificato sulla documentazione ufficiale SP-API cosa di
questo sia davvero raggiungibile via API oggi (§3).

Riuso totale di `amazon-inserzioni`: `spapi_client.py` (stesso trasporto, stessa auth LWA
per cliente), `registro.py` (stesso registro, un tipo di riga in più), stesso principio di
cancello 🟡.

## 3. Perimetro

**Verificato sulla documentazione ufficiale (2026-09-02), prima di scrivere una riga di
codice:**

| Contenuto | Via API pubblica? | Nota |
|---|---|---|
| Contenuti A+ (per ASIN) | ✅ Sì | `aplus-content-2020-11-01`, stessa famiglia SP-API |
| Brand Story | ❌ No | nessun endpoint pubblico; solo Seller Central → Stores → Brand Story |
| Video prodotto | ❌ No | confermato da Amazon (SP-API team, ago 2023, riconfermato giu 2026): non supportato via API |
| Store (pagina storefront) | ❌ No | nessuna API pubblica di editing layout/tile |

**Fa**: legge i Contenuti A+ collegati a un ASIN; crea un documento nuovo o modifica uno
esistente (moduli di testo/immagini); carica le immagini nuove; mostra sempre
un'anteprima modulo per modulo; scrive solo su "vai" esplicito per documento; sottomette
per approvazione Amazon; rilegge lo stato; registra.

**Non fa**: Brand Story, video, Store — non per scelta di scope ma perché **oggi non
esiste un'API pubblica** per nessuno dei tre (§3, tabella sopra). Restano segnalati in
`SKILL.md` come "solo modifica manuale da Seller Central". Se Amazon apre un'API in
futuro, si riapre questo design — non si costruisce oggi un'astrazione per un'ipotesi.

Non fa nemmeno, in questa v1: `annulla` automatico (§8), caricamento massivo multi-ASIN
senza anteprima per documento, Vendor Central (stesso limite di `DESIGN.md` §3.2).

**Un cliente, un marketplace, alla volta** — stesso vincolo del resto della skill.

## 4. Dove vive

Stesso repo, stessa cartella piatta `scripts/` (nessuna sottocartella, stessa convenzione):

```
~/.claude/skills/amazon-inserzioni/
  scripts/
    aplus_leggi.py      searchContentDocuments + getContentDocument +
                         listContentDocumentAsinRelations → stato attuale per ASIN
    aplus_immagini.py   Uploads API: createUploadDestinationForResource → upload
                         file → uploadDestinationId, un'immagine alla volta
    aplus_componi.py    pacchetto di modifiche a moduli (crea nuovo o modifica
                         esistente), stesso spirito di modifiche.py
    aplus_diff.py       anteprima prima → dopo, modulo per modulo
    aplus_applica.py    create/updateContentDocument → postContentDocumentAsinRelations
                         → postContentDocumentApprovalSubmission, rilettura, registro
  tests/
    test_aplus_leggi.py
    test_aplus_immagini.py
    test_aplus_componi.py
    test_aplus_diff.py
    test_aplus_applica.py
```

`registro.py` non si riscrive: si aggiunge una terza coppia `registra_tentativo_aplus`/
`registra_esito_aplus` con una cartella propria (`contenuti-aplus`), stessa forma delle
due coppie già esistenti per creazione/aggiornamento — non un campo `tipo` bolted-on su
funzioni condivise, per restare sullo stesso pattern "una cartella per tipo di
operazione" già in uso.

## 5. Il modello contenuto

Un `ContentDocument` è una lista di **moduli** (fino a 15 tipi: logo, testo+immagine,
tabella comparativa, specifiche tecniche, immagini multiple...), ciascuno con i propri
campi testo/immagine. A differenza del modello Prodotto di `DESIGN.md` (attributi piatti
per SKU), qui l'unità è il modulo dentro un documento, e un documento si collega a **uno o
più ASIN** (non necessariamente 1:1).

Niente modello canonico proprietario in v1: si lavora sulla struttura moduli così come la
restituisce/accetta l'API, senza un livello di traduzione in più — years di YAGNI, non
sappiamo ancora quali moduli userà davvero Mariano.

## 6. Il flusso — creare o aggiornare un documento

```
bersaglio: ASIN (+ cliente, marketplace)
      ↓
aplus_leggi.py: documenti già collegati all'ASIN, se esistono — stato attuale completo
      ↓
contenuto nuovo: testo dettato da Mariano, e/o immagini da file locale
      ↓
aplus_immagini.py: per ogni immagine nuova, upload esplicito → uploadDestinationId
      ↓
aplus_componi.py: nuova versione dei moduli (crea documento se non esiste, altrimenti
                   modifica quello esistente) — mai un modulo inventato, mai un campo
                   "dopo" vuoto (stessa regola di modifiche.py, DESIGN.md §3.1)
      ↓
aplus_diff.py: anteprima modulo per modulo, prima → dopo
      ↓
"vai" — un permesso per documento, mai per iniziativa, mai lotto implicito
      ↓
aplus_applica.py: create/updateContentDocument → postContentDocumentAsinRelations
                   → postContentDocumentApprovalSubmission
      ↓
rilettura (badge ASIN: CONTENT_PUBLISHED / CONTENT_NOT_PUBLISHED) + issues di validazione
      ↓
registro.py: riga tipo="aplus", prima/dopo/esito
```

## 7. Le immagini

Diversamente dai Listing (dove l'attributo immagine accetta direttamente un URL, §8 di
`DESIGN.md`), i moduli A+ richiedono un **upload esplicito**: `createUploadDestinationForResource`
(Uploads API) restituisce una destinazione e un URL firmato; si carica il file lì; l'URL
risultante (o l'`uploadDestinationId`, da confermare al piano su una risposta reale) va
nel campo immagine del modulo. Un'immagine alla volta, mai un caricamento implicito.

## 8. L'approvazione — perché "vai" non vuol dire "pubblicato"

Ogni submit (`postContentDocumentApprovalSubmission`) va in coda di revisione Amazon,
tempi non documentati e non istantanei. Conseguenze dirette sul resto del design:

- Dopo "vai" e la scrittura, si dichiara **"sottomesso per approvazione"**, mai
  "pubblicato" — anche se la chiamata torna 200. Il badge ASIN (`CONTENT_PUBLISHED`) è la
  sola fonte di verità sull'esito reale, e va riletto in un giro successivo, non subito.
- **Niente `aplus_annulla.py` in v1.** Su un listing, annullare vuol dire riscrivere il
  valore precedente e la Listings Items API lo applica subito. Qui "annullare" vorrebbe
  dire risottomettere la versione precedente — che rientra in coda di approvazione come
  qualunque altra modifica: non è un rollback, è una nuova richiesta. Costruire
  un'automazione per qualcosa che non è comunque istantaneo è un costo senza un
  beneficio reale oggi; se serve, si ripresenta a mano la versione precedente registrata
  (il "prima" resta comunque in `registro.py`).

## 9. Gli issues di Amazon

Stesso principio di `DESIGN.md` §9: ogni risposta di scrittura porta un elenco di
*issues* (errori/avvisi) anche ad esito accettato. Si mostrano sempre, non solo sui
fallimenti.

## 10. Errori

Stesso principio unico di `DESIGN.md` §10: il giro non si blocca, l'errore si dichiara.

- ASIN senza documento esistente e senza contenuto sufficiente per crearne uno → si ferma
  e chiede, mai inventa un modulo
- Amazon respinge (issue `ERROR`) → si registra il motivo letterale, si continua
- Rate limit (429) → stesso backoff già in `spapi_client.py`, nessuna modifica lì
- Ruolo SP-API insufficiente (403 su `Product Listing`/`Brand Analytics`) → si ferma
  prima di ritentare, dice esattamente quale ruolo manca e su quale cliente (§11)

## 11. Credenziali e ruoli

Stesso client per cliente di `DESIGN.md` §11 (`spapi_client.crea_client("Cliente")`),
**nessuna nuova credenziale**: l'A+ Content API usa lo stesso token LWA della Listings
Items API già in uso. Richiede però il ruolo SP-API "Product Listing" o "Brand Analytics"
sull'app del cliente — lo stesso ruolo che probabilmente serve già oggi per scrivere sui
listing. **Da verificare al piano con una lettura di prova** (`searchContentDocuments` su
un cliente attivo) prima di costruire le scritture: se il ruolo manca, è Mariano a
doverlo autorizzare di persona (🔴, stesso principio di `DESIGN.md` §11).

## 12. Collaudo

- **Unitari**: risposte A+ Content API salvate come fixture, nessuna chiamata di rete —
  stesso schema di `tests/` esistente
- **Prova a vuoto**: catena completa fino a un istante prima della chiamata, mostrando il
  payload esatto
- **Un ASIN vero**, sul cliente pilota: lettura, poi una modifica reale minima (es. un
  modulo di testo) con "vai", poi rilettura del badge in un giro successivo — nessun
  collaudo di `annulla` (non costruito, §8)

## 13. Prerequisiti aperti

- **Cliente e ASIN pilota**: da confermare da Mariano prima del Task 1
- **Ruolo "Product Listing"/"Brand Analytics"** sull'app SP-API del cliente pilota: da
  verificare con una lettura prima di scrivere (§11) — se manca, blocca solo il collaudo
  dal vivo, non l'implementazione testabile su fixture

## 14. Punti da verificare al piano

- Se `ImageComponent` vuole l'`uploadDestinationId` o l'URL restituito dall'upload (§7) —
  lo schema pubblico non lo specifica in modo univoco
- Se `searchContentPublishRecords` dia uno stato di approvazione interrogabile o se il
  solo segnale osservabile sia il badge ASIN (§8)
- Il limite reale di moduli per documento lato Seller (lo schema dichiara 100, ma la UI
  storicamente ne accetta ~7 per i piani non Premium) — non assumere, verificare
  sull'errore che Amazon restituisce
- Rate limit dichiarato: 10 richieste/secondo — da confermare non cambi per regione

## 15. Fuori perimetro — cosa viene dopo

- Brand Story, video prodotto, Store: nessuna API pubblica oggi (§3) — si riapre questo
  design se e quando Amazon la rilascia, non prima
- `annulla` automatico per i Contenuti A+ (§8)
- Caricamento massivo multi-ASIN senza anteprima per documento
- Collegamento con l'hub Amazon Ads — sistemi separati, nessun piano di integrazione oggi
