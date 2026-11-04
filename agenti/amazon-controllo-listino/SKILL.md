---
name: amazon-controllo-listino
description: Controlla il listino di un fornitore su Amazon e dice quali prodotti conviene vendere - per ogni riga trova la scheda (ASIN) da EAN o descrizione, legge buy box, offerte, classifica, commissioni di Amazon e restrizioni dell'account, calcola margine, ROI e prezzo di pareggio e dà un semaforo verde/giallo/rosso, con un foglio "Da comprare". Excel brandizzato sul Desktop. SEMPRE usare quando Mariano chiede di controllare o analizzare un listino su Amazon, cosa conviene prendere da un fornitore, la marginalità o vendibilità di un elenco di prodotti. Trigger tipici - "controlla questo listino su Amazon", "cosa conviene prendere da questo fornitore", "analizza il listino di X", "marginalità e vendibilità di questi prodotti", "quali di questi articoli possiamo vendere su Amazon". NON è marginalita-amazon-new-era (calcolatore per uno o pochi prodotti già scelti, senza ricerca su Amazon), NON crea inserzioni (amazon-inserzioni), NON ordina dal fornitore. Sola lettura, un cliente e un marketplace alla volta.
---

# Controllo listino fornitore su Amazon

## Quando attivarla
Un listino (Excel, CSV) di un fornitore da valutare per la vendita su Amazon: reselling di un cliente, nuovo fornitore, elenco di prodotti candidati.

## Input
- **cliente**: l'account SP-API con cui leggere (restrizioni e prova d'offerta valgono per QUEL venditore). Prefisso in `~/scripts/.env`, serve anche `<CLIENTE>_SPAPI_SELLER_ID`.
- **file del listino**: `.xlsx`, `.xls`, `.csv`. Colonne riconosciute dall'intestazione (EAN, codice, descrizione, marca, U.M., costo/prezzo netto), anche se l'intestazione non è nella prima riga e anche con codice e descrizione nella stessa cella (formato Cambioni). Un PDF va prima convertito in Excel.
- Facoltativi, con i default: `--fba` (default spedizione propria FBM), `--soglia 15` (margine minimo %), `--spedizione 4.50` (€ a collo FBM, **stima**: chiedere al cliente il costo vero), `--trasporto 0` (€ a pezzo dal fornitore), `--iva 22`, `--resi 5` (% del prezzo senza IVA), `--marketplace APJ6JRA9NG5V4` (Amazon.it), `--da N --quante M` per un campione.

## Procedura
1. **Campione prima del giro intero** se il listino è grande (oltre ~300 righe): `--quante 100`, per giudicare la resa (quanti trovati, quanti bloccati) prima di un giro di ore.
   ```
   python3 ~/.claude/skills/amazon-controllo-listino/scripts/controllo.py <cliente> "<listino>" [opzioni]
   ```
2. Il comando fa quattro fasi: abbinamento delle schede → offerte, commissioni e restrizioni (commissioni e restrizioni solo sulle righe in utile con una commissione stimata al 15%) → prova d'offerta in VALIDATION_PREVIEW sulle righe non già rosse → Excel. Le letture si salvano in `~/.config/newera-amazon/controllo-listino/<cliente>-<listino>-<data>.json`: se il giro si interrompe, rilanciare lo stesso comando riparte da dove era.
3. **Revisione degli abbinamenti per descrizione** (obbligatoria se il listino non ha EAN). Esporta i gialli e i verdi «da confermare» in un JSON e fai giudicare a un subagent **avversario** (parte dal presupposto che l'abbinamento sia sbagliato; conferma solo stesso prodotto, marca, formato e confezione; niente pagine Amazon, solo i dati del file). Sul reselling Cambioni un primo revisore lasciava passare errori grossolani, il secondo avversario ne scartava ~70%. Il revisore scrive `{"<codice>": "si"|"no"}`; poi:
   ```
   python3 .../controllo.py <cliente> "<listino>" [stesse opzioni] --conferme conferme.json
   ```
   Il rilancio usa la cache: rifà i conti e prova solo le righe diventate idonee.
4. In chat: righe controllate, verdi/gialli/rossi/grigi, i 5 verdi migliori per margine €, il percorso del file.

## Output
`~/Desktop/Controllo-listino-<cliente>-<data>.xlsx` (mai sopra un file dello stesso giorno): **Riepilogo** (conteggi, parametri, come leggere), **Controllo** (una riga per articolo, ordinate verde → grigio e per margine €), **Da comprare** (i verdi).

## Semaforo
- **Verde**: abbinamento per EAN (o confermato in revisione), margine ≥ soglia + 5 punti, classifica categoria principale ≤ 100.000, nessuna restrizione, prova d'offerta VALID, Amazon non vende, meno di 10 offerte.
- **Giallo**: margine al limite (soglia … soglia + 5), vende poco (100-250k), serve approvazione di marca/categoria, Amazon vende, 10+ offerte, abbinamento da confermare.
- **Rosso**: margine sotto soglia, quasi ferma o senza classifica, non vendibile, offerta rifiutata alla prova (errore 5886/5885, scheda generica con restrizioni).
- **Grigio**: non trovato, oppure costo a KG/LT/MT/MQ (serve il prezzo a confezione), oppure prezzo/commissione non disponibili.

## Autonomia
**R0, sola lettura.** L'unico PUT è la prova d'offerta con `mode=VALIDATION_PREVIEW` e SKU fittizio `PROVA-LISTINO-<ASIN>`: Amazon valida e non scrive nulla. Non ordina dal fornitore (R3), non crea inserzioni (amazon-inserzioni col suo "vai"). Nessun dato di un cliente usato per il lavoro di un altro (Acceptable Use Policy SP-API).

## Calcolo
`margine = prezzo/(1+IVA) − commissione (Fees API) − tariffa FBA o spedizione FBM − costo × pezzi della scheda − trasporto − resi`. Pubblicità esclusa (reselling: niente campagne, regola Cambioni 26/09/2026). ROI = margine / (merce + trasporto). Pareggio = prezzo IVA inclusa a margine zero.

## Controlli di qualità
- Commissioni sempre dalla stima di Amazon sul prezzo, mai da tabella; «commissione stimata al 15%» compare solo sulle righe già in perdita, che non vengono chieste ad Amazon.
- Vendite = **fascia** dalla classifica (≤20k vende bene, ≤100k vende, ≤250k poco): non sono vendite al mese e il foglio non le spaccia per tali. Nessuna quantità d'acquisto suggerita (servirebbe uno strumento esterno, Keepa: scelta di Mariano del 02/11/2026, solo dati Amazon).
- EAN su più schede → «da confermare», mai scelto a caso.
- Formato: se listino e titolo dichiarano la stessa grandezza con valori diversi (150 ml / 750 ml) la scheda è scartata; multipack letti da attributi o dal titolo («12 X 1 LT», «4pz») moltiplicano il costo.
- Restrizioni **e** prova d'offerta: `getListingsRestrictions` non vede l'errore 5886 (wiki: Offerta su ASIN generico soggetto a restrizioni).

## Errori noti
- Listino senza EAN: recall dell'abbinamento per parole ~80% e molti falsi positivi → la revisione del punto 3 non è facoltativa. Chiedere al fornitore l'export **con EAN e marca**.
- Listino a KG/LT/MT: prezzo per unità di misura, non per confezione → riga grigia.
- `feesEstimate` FBA senza misure sulla scheda → riga grigia con il motivo, nessun gemello inventato.
- Limiti: ricerca catalogo ~2/s, offerte 20 ASIN ogni 10 s, commissioni ~1/s. 100 righe ≈ 3 minuti; 6.000 righe ≈ 1-2 ore.
- Cache candidati: se cambia il modo di leggere le schede (`abbina.scheda_da_item`), cancellare il file di stato per rileggerle.

## File
`scripts/controllo.py` (comando e fasi), `listino.py` (lettura), `abbina.py` (EAN e descrizione), `mercato.py` (offerte, commissioni, restrizioni, prova), `margine.py` (conti e semaforo), `foglio.py` (Excel). Client SP-API condiviso con `amazon-inserzioni/scripts/spapi_client.py`. Test: `python3 -m pytest -q tests`.
