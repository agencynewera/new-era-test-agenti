---
name: marginalita-amazon-new-era
description: "Calcola la marginalità Amazon in Excel per prodotti reselling/arbitraggio o private label, singoli o in lotto. Gestisce scenari prezzo IT/DE/FR/ES, FBA o FBM, pubblicità e resi come quota degli ordini con recupero merce e commissioni. Usare per richieste su margine prodotto/ASIN, convenienza di vendita, scenari prezzo e profitto unitario Amazon. Raccoglie solo i dati mancanti, una domanda alla volta, e dichiara le ipotesi e i limiti delle tariffe."
---

# Marginalità Amazon — New Era S.r.l.

Skill per generare un calcolatore Excel brandizzato New Era che calcola la marginalità stimata di prodotti Amazon, con formule dinamiche modificabili e visualizzazione multi-scenario.

## Scelte iniziali e raccolta dati

Prima estrai i dati già forniti, senza chiederli di nuovo. Una domanda alla volta.

1. **Tipo di attività:** reselling/arbitraggio oppure private label. È indipendente da numero di prodotti e logistica; salva `tipo_attivita`.
   - Reselling: pubblicità iniziale **0%**, modificabile se richiesta. Non imporre pubblicità né vietarla.
   - Private label: pubblicità iniziale **10% del fatturato lordo prima dei resi**, modificabile come prima.
   - Un valore pubblicitario esplicito prevale sempre sul default, incluso zero.
2. **Un prodotto o lotto:** singolo con scenari prezzo/paese; lotto di prodotti similari con parametri condivisi. Se il lotto mescola attività, separalo in due file. Per lotto usa un solo paese e FBA oppure FBM per file.
3. **Anagrafica e paese:** cliente, prodotto/categoria; Italia se non specificato. Restano IT, DE, FR, ES. Se categoria ignota, proponi quelle pertinenti.
4. **Logistica — scegli FBA o FBM.** Il confronto di entrambi resta disponibile se richiesto per un singolo prodotto.
   - FBA: peso e misure del pacco, costo per portare ogni pezzo ad Amazon.
   - FBM: costo per spedire al cliente, comprendendo preparazione/imballaggio. Non inventare il costo del corriere. Peso e misure non sono obbligatori. Non sottrarre il trasporto verso Amazon nelle righe FBM; eventuali altri costi reali di approvvigionamento vanno dichiarati negli altri costi.
5. **Costi e prezzi:** acquisto/produzione unitario, eventuali dazi/altri costi, prezzo IVA inclusa. Costi economici al netto dell'IVA recuperabile. Per single mantieni scenari base, ribasso, rialzo o entrambi (±10%, ±20%). Per lotto accetta tabella, CSV o testo libero; chiedi solo i dati mancanti.
6. **Pubblicità e resi:** applica le regole sotto e mostra le ipotesi nel riepilogo e nell'Excel.
7. **Genera:** per una richiesta esplicita di calcolo procedi dopo il riepilogo, senza una seconda conferma. Scrivi il JSON secondo `references/config_schema.md` e lancia `scripts/generate_calculator.py` dalla directory reale della skill; usa percorsi assoluti autorizzati per config e output, non i vecchi percorsi `/home/claude`. Esempio: `python3 <skill>/scripts/generate_calculator.py --mode single --config <config.json> --output <output.xlsx>`. Per lotto: `--mode batch`.
8. **Verifica:** ricalcola con `scripts/recalc.py` se LibreOffice è disponibile. Se non lo è, controlla struttura e formule, ma dichiara che manca il ricalcolo in un'applicazione foglio di calcolo. Non presentare celle senza cache come risultati verificati.

## Resi: percentuale degli ordini, non accantonamento sul fatturato

- Il tasso è `ordini restituiti / ordini iniziali`, relativo a un periodo/coorte coerente. Preferisci lo storico quando esiste; per un prodotto nuovo usa **3% stimato** se manca un valore, rendendolo visibile. L'intervallo normale indicato da Mariano è **2–5%**, non un limite: accetta tassi effettivi diversi, compreso 0%.
- Esempio: 100 ordini da 10 € → 1.000 € lordi; 2% → 2 resi e 20 € di rimborsi lordi. Non confondere questi rimborsi con la perdita economica.
- Modello: ogni ordine restituito è rimborsato interamente. Il margine è medio **per ordine iniziale**, non per ordine trattenuto. IVA stornata sul ricavo rimborsato.
- Rettifica media = tasso resi × (prezzo senza IVA − valore merce recuperata − commissione restituita + costo extra di rientro/gestione).
- Valore merce recuperata = COGS × quota recuperata: valore medio recuperabile di tutti i resi, inclusi quelli danneggiati, senza riconoscere ricavi futuri.
- Commissione restituita = referral fee × quota restituita, al netto degli importi trattenuti. Non assumere automaticamente un rimborso Amazon.
- Costo extra comprende solo spese aggiuntive del reso, non COGS o spedizione di andata già sottratti. Default preesistente **4,50 €**, ora esplicitamente stima modificabile delle sole spese extra, non costo complessivo del reso.
- Se recuperi ignoti: inizializza entrambe le quote a **0%**, ipotesi prudenziale dichiarata, non fatto osservato. Per un conto rapido non obbligare a stimarli: sono modificabili nell'Excel. Dati reali forniti sostituiscono le stime.
- Non sottrarre una seconda volta il costo della merce persa: il COGS di tutti gli ordini è già nel conto base. Sui resi recuperabili si riaccredita il solo valore recuperato.
- Pubblicità e spedizione di andata restano costi sostenuti; il modello non presume il loro rimborso. Rimborsi parziali, cambi e recuperi ulteriori richiedono un calcolo specifico, senza forzarli in questo modello.

---

## Output Excel — Struttura

### Modalità SINGLE — 6 fogli
1. **Cover** — logo, nome prodotto, cliente, data, posizionamento
2. **Come leggere** — legenda celle (giallo input / viola output), glossario, istruzioni
3. **Input prodotto** — anagrafica + costi (editabile)
4. **Confronto Marketplace** — tabella principale: una sezione per marketplace, con righe FBA/FBM se entrambi, colonne per ogni scenario prezzo
5. **Fee Reference** — tabelle referral fee + FBA fee + IVA, modificabili
6. **Dashboard** — istruzioni e semaforo descrittivo; non determina un prezzo ottimale

### Modalità BATCH — 7 fogli
1. **Cover**
2. **Come leggere**
3. **Setup comune** — parametri condivisi (categoria, marketplace, fulfillment, TACoS, resi e recuperi)
4. **Catalogo prodotti** — tabella principale: 1 riga per prodotto, colonne input + output (margine € e %, break-even ACoS)
5. **Ranking** — top/flop per margine €, margine %, alert prodotti in perdita
6. **Fee Reference**
7. **Dashboard KPI** — margine medio, % prodotti in perdita, distribuzione fasce margine, scatter prezzo vs margine

---

## Branding obbligatorio

Applica sempre l'identità New Era (vedi skill `new-era-brand` per dettagli completi):
- Colori: viola `#6027F1`, blu scuro `#1C1348`
- Font Excel: Nunito se disponibile, altrimenti Arial (font system-safe)
- Header fogli: sfondo `#1C1348`, testo bianco
- KPI / totali: viola `#6027F1`
- Bordi: `#E0DDEF`
- Righe alternate: bianco / `#F4F4F8`
- Logo: cella A1 del foglio Cover, da `assets/New_Era-16.png` della skill
- Footer ogni foglio: "New Era S.r.l. · Increase your performance · Calcolo indicativo, verificare fee aggiornate su Seller Central"

---

## Convenzioni celle (color coding finanziario)

- **Sfondo giallo `#FFF9C4`** → input modificabile dal cliente/AM
- **Sfondo viola chiaro `#EDE7FE`** → output principale (margine €, margine %)
- **Testo nero** → formula
- **Sfondo grigio chiaro `#F4F4F8`** → label/descrittivo
- **Sfondo rosso chiaro `#FEE2E2`** → alert (margine negativo o sotto soglia 10%)

---

## Formule Excel — usare SEMPRE riferimenti, MAI valori hardcoded

Vedi `references/formulas_reference.md` per le formule complete da inserire. Principi chiave:

- Tutte le fee Amazon → riferimenti al foglio `Fee Reference` (es. `=VLOOKUP(...)`)
- IVA per marketplace → riferimento a tabella `Fee Reference!IVA_Table`
- FBA fee → calcolo size tier in cella separata + lookup tariffa
- Margine atteso per ordine iniziale = Prezzo netto IVA − Referral fee − FBA/FBM fee − COGS − Inbound (solo FBA) − Dazi/altri costi − Costo ADV − Rettifica resi
- Break-even ACoS = (Margine prima di ADV / Prezzo netto IVA) × 100
- TACoS sostenibile = (Margine prima di ADV / Prezzo IVA inclusa) × 100

**Mai** calcolare valori in Python e scriverli hardcoded. Sempre formule.

---

## Conditional formatting obbligatorio

- Margine % < 0 → cella rosso `#FEE2E2`, testo rosso scuro
- Margine % tra 0% e 10% → giallo `#FFF9C4`
- Margine % ≥ 15% → verde chiaro `#D1FAE5`

---

## Disclaimer obbligatorio

In fondo al foglio "Come leggere" e al footer di "Fee Reference":

> "Le tabelle fee sono indicative, datate [data fee dal JSON], non validate per ASIN/paese. Verificare sempre su Seller Central per il proprio ASIN. Il calcolatore è uno strumento di supporto decisionale, non una garanzia di redditività. Variazioni di cambio, promozioni, costi di prep, vouchers e altri costi accessori non sono inclusi nel calcolo base."

---

## Riferimenti

- `references/amazon_eu_fees.json` — tabelle fee Amazon EU 2026 (referral, FBA, storage, IVA, size tier, default resi)
- `references/formulas_reference.md` — tutte le formule Excel da inserire
- `references/config_schema.md` — schema JSON di configurazione per lo script
- `scripts/generate_calculator.py` — generatore principale
- `scripts/recalc.py` — ricalcolo formule via LibreOffice (copiato da skill xlsx)
- `assets/logo_path.txt` — path al logo New Era

## Limiti preesistenti, non corretti da questa revisione

Le fee FBA sono indicative e non specifiche per paese; il motore non integra tutte le maggiorazioni, lo stoccaggio o i servizi accessori. Il precedente indicatore denominato break-even ACoS usa il ricavo senza IVA: non utilizzarlo come soglia operativa nella console Ads. Il cambiamento riguarda attività, logistica e resi; non dichiarare validate le tariffe o tutte le altre metriche.

## Note operative

- Mariano lavora in italiano: tutta la UI dell'Excel e tutti i dialoghi sono in italiano
- Se Mariano lavora per cliente non italiano (es. cliente DE), chiedi se vuole l'Excel anche in inglese
- Il file generato è sia uso interno che cliente-facing: tono divulgativo ma con tutti i dati tecnici visibili
- Per categorie a fee variabile (es. Clothing 5%/10%/15% in base al prezzo), inserisci formula `IF` annidata che applica la fee corretta in funzione del prezzo dello scenario
