# REVIEW — Estensione Contenuti A+: revisione finale e decisioni prese

**Data**: 2026-09-03 · **Stato**: chiusa — tutti i finding Critical/Important corretti,
collaudo dal vivo ancora da fare
**Perché questo file**: il report della revisione finale e le decisioni prese durante
l'esecuzione a sottoagenti vivevano solo nella sessione di chat e nel ledger temporaneo
(cancellato a fine lavoro). Le cose che devono sopravvivere alla sessione stanno su file
committato, non in chat — lezione già pagata una volta su questo repo.

Spec: `DESIGN-aplus.md` · Piano: `PLAN-aplus.md` · Commit: `4e37668..fdde077` su `main`.

---

## 1. Cosa ha trovato la revisione finale (eseguendo il codice, non solo leggendolo)

### Critical — corretti (commit `b2f12d8`)

- **C1 — `applica()` lasciava passare una scrittura vuota.** Un `PacchettoAplus` con
  `moduli_dopo=[]` costruito a mano (saltando `componi()`) arrivava ad Amazon come
  `contentModuleList: []` e tornava "ACCEPTED": cancellazione totale dei Contenuti A+
  dell'ASIN dichiarata riuscita. Ora alza `ValueError("nessun modulo da scrivere: uno
  svuotamento non si propone mai")` — stessa guardia anti-svuotamento che
  `applica.costruisci_patch` ha già per i listing.
- **C2 — Gli errori del collegamento ASIN e della sottomissione venivano buttati.**
  Solo la risposta di create/update passava da `_issues()`; le due chiamate successive
  no. Un documento mai entrato in coda di approvazione veniva riportato "ACCEPTED,
  issues: []" — anche nel registro, l'unica traccia superstite. Ora ogni chiamata
  effettuata accumula i suoi issues, ogni passo è gated sul precedente (link solo se
  create/update è pulito, submit solo se link è pulito, rilettura badge solo se submit
  è pulito), un solo ERROR degrada l'esito a INVALID, i soli WARNING non bloccano mai.

### Important — corretti (commit `008830f`, `081fd2e`, `d93c12a`, `fdde077`)

- **I1 — `leggi()` sceglieva `contentMetadataRecords[0]` senza guardia.** Con più
  documenti sullo stesso ASIN (tipico: un APPROVED vivo + una DRAFT) sceglieva a caso,
  e quello era il documento che `applica()` avrebbe sovrascritto. Ora con più di un
  record alza `ValueError` nominando l'ASIN: "si ferma e chiede, mai inventa".
- **I2 — `locale="it_IT"` cablato in `applica()`.** Su Tecno Cold DE o Lelang DE ogni
  aggiornamento avrebbe riscritto il locale a it_IT. Ora `StatoAplus.locale` legge il
  valore vivo da `contentDocument.locale`, `PacchettoAplus.locale` lo porta fino alla
  scrittura, e in `componi()` il valore vivo vince SEMPRE su uno passato a mano; per un
  documento nuovo il locale è un parametro obbligatorio (mai indovinato dal
  marketplace). `contentType="EBC"` resta cablato di proposito: EMC è solo Vendor
  Central, che questa skill esclude in modo permanente.
- **I3 — L'anteprima non mostrava lo stato di pubblicazione.** `stato_documento` e
  `badge_asin` erano letti da `leggi()` e mai mostrati: Mariano dava il "vai" senza
  sapere se stava toccando un documento già pubblicato o già in coda di revisione. Ora
  `PacchettoAplus` li porta e `rendi_tabella()` li stampa nella riga "stato attuale".
- **I4 — Il frontmatter di `SKILL.md` non menzionava i Contenuti A+.** La frase con
  cui Mariano ha chiesto questa feature non avrebbe attivato la skill. Aggiunti i
  trigger nella `description`, senza toccare nome, esclusione Vendor Central e
  autonomia.
- **I5 — `DESIGN-aplus.md` e `PLAN-aplus.md` non erano committati** (commit `5aa3224`).
- **I6 — `SKILL.md` non diceva dove va l'`uploadDestinationId`** restituito da
  `carica_immagine`. Ora dice: in `image.uploadDestinationId` dentro il dict del modulo,
  prima di passarlo a `componi()`.

### Minor — uno corretto, tre rimandati

- **M1 — estrazione del badge duplicata** fra `aplus_leggi.py` e `aplus_applica.py` →
  corretto, ora `aplus_leggi.badge_da_relazioni()` è condivisa (commit `008830f`).
- **M2 — `rendi_tabella` stampa il `repr` intero del modulo.** Sui dict di test è
  leggibile, su un `STANDARD_COMPARISON_TABLE` vero potrebbe essere un muro di JSON. Da
  giudicare al primo collaudo dal vivo: se l'anteprima è illeggibile va sistemata prima
  dell'uso reale, perché è la base del cancello 🟡.
- **M3 — `applica()` non ha docstring** e "ACCEPTED" non è glossato nel codice come
  "accodato, non pubblicato" (vive solo in `SKILL.md`). Rimandato.
- **M4 — copertura test**: le risposte a soli warning sono testate solo sul passo
  create/update, non su link/submit. La logica è identica, non è un buco funzionale.

## 2. Decisioni prese durante l'esecuzione (rulings), col costo se sbagliate

1. **Lavorato direttamente su `main`, nessun worktree.** Ogni commit precedente del
   repo è su `main`, nessun collaboratore. Costo se sbagliata: nessuno osservabile.
2. **Soglia errore upload immagini `>= 300` invece di `>= 400`** (come in
   `spapi_client.py`). Gli upload S3 attesi tornano 200/204, un 3xx terminale non è un
   esito normale (`requests` segue già i redirect): più prudente, non sbagliato. Costo se
   sbagliata: da rivedere al collaudo se Amazon rispondesse con un 3xx legittimo.
3. **Nessun try/except attorno alle chiamate di scrittura in `aplus_applica.py`.** Un
   errore di trasporto a metà catena lascerebbe la riga di registro su "in corso".
   Verificato che `applica.py` (listing) ha esattamente la stessa lacuna: è il pattern
   di tutto il repo per le scritture a singolo elemento, correggerlo solo qui sarebbe
   asimmetrico. **Candidato a un giro di irrobustimento futuro che tocchi
   `applica.py` E `aplus_applica.py` insieme.** Costo se sbagliata: un documento
   collegato ma non sottomesso senza traccia nel registro — visibile comunque
   rileggendo Seller Central, non un dato perso.
4. **Nessuna guardia su `contentReferenceKey` vuoto con risposta ACCEPTED.** Sarebbe
   una rottura del contratto API di Amazon; se succedesse, il path malformato darebbe
   comunque un `SpApiError` visibile. Costo se sbagliata: un errore con un messaggio
   meno chiaro al primo collaudo.
5. **Niente `annulla` automatico per i Contenuti A+ (v1).** Risottomettere la versione
   precedente è una nuova richiesta di approvazione, non un rollback: costruire
   un'automazione per qualcosa che non è istantaneo non vale il costo oggi. Il "prima"
   resta in `registro.ultimo_lotto(cliente, "contenuti-aplus")`.

## 3. Cosa il debugger ha confermato (2026-09-03, dopo il fix wave)

Tutte e 10 le affermazioni verificate codice alla mano: 131 test verdi senza rete, le
due guardie in `applica()`, il gating della catena in ogni combinazione, la guardia su
più documenti in `leggi()`, il locale vivo che vince, lo stato attuale in anteprima, il
frontmatter di `SKILL.md` intatto, spec/piano committati, nessun `aplus_annulla.py`,
nessuna nuova credenziale (`spapi_client.py` non toccato, zero `os.environ` negli script
A+). Un solo dettaglio nuovo, minore: con `moduli_prima=[]` e `moduli_dopo=[]` la prima
guardia scattava prima della seconda e dava il messaggio "sbagliato" (la scrittura era
comunque bloccata) — corretto invertendo l'ordine dei due controlli.

## 4. Cosa resta aperto prima di dichiarare la skill pronta

Tutto quello che non si può verificare senza Amazon vero (`DESIGN-aplus.md` §11-14):

- **Cliente + ASIN pilota** — da Mariano
- **Ruolo SP-API "Product Listing"/"Brand Analytics"** già autorizzato sull'app del
  cliente pilota — da confermare con una lettura (`aplus_leggi.leggi`) prima di
  qualunque scrittura; se manca, si ferma lì
- Se `image.uploadDestinationId` sia davvero il campo atteso (lo schema pubblico lo
  indica, nessuna risposta reale lo conferma ancora)
- Il limite reale di moduli per documento lato Seller (schema: 100; UI: ~7 fuori dal
  Premium) — non assumere, leggere l'errore di Amazon
- La leggibilità dell'anteprima su un modulo vero (M2)

Sequenza del collaudo (`DESIGN-aplus.md` §12): lettura → una modifica minima di un
modulo di testo con "vai" → rilettura del badge in un giro successivo. Nessun collaudo
di `annulla`: non esiste.
