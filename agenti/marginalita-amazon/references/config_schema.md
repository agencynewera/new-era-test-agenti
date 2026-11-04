# Schema di configurazione per `generate_calculator.py`

Lo script accetta un JSON di config in input (`--config /path/to/config.json`).
Costruisci il config dopo aver raccolto tutte le info dall'AM, prima di lanciare lo script.

## Schema modalità SINGLE

```json
{
  "mode": "single",
  "tipo_attivita": "private_label",
  "cliente": "Nome Cliente S.r.l.",
  "prodotto": {
    "nome": "Cuscino memory foam ortocervicale",
    "categoria": "Home Products",
    "peso_kg": 1.2,
    "dim_l_cm": 35,
    "dim_w_cm": 35,
    "dim_h_cm": 10
  },
  "marketplaces": ["IT", "DE"],
  "fulfillment": ["FBA"],
  "fbm_shipping_cost": 5.00,
  "costi": {
    "cogs": 12.00,
    "inbound": 1.50,
    "dazi": 0.00
  },
  "prezzi": {
    "base": 39.90,
    "scenari": "rialzo"
  },
  "adv": {
    "tacos_target": 0.10
  },
  "resi": {
    "tasso_atteso": 0.03,
    "quota_valore_recuperato": 0.0,
    "quota_commissione_restituita": 0.0,
    "costo_extra_per_reso": 4.50,
    "fonte": "Stima iniziale: tasso 3%, recuperi ignoti, costo extra stimato"
  }
}
```

### Campi

- `mode`: sempre `"single"` per questa modalità
- `cliente`: stringa libera
- `prodotto.nome`: stringa libera
- `prodotto.categoria`: deve corrispondere ESATTAMENTE al campo `category` di `amazon_eu_fees.json`
- `prodotto.peso_kg`: obbligatorio solo FBA; numero, peso effettivo in kg
- `prodotto.dim_*_cm`: obbligatori solo FBA; numero, dimensioni in cm
- `marketplaces`: array di codici IT/DE/FR/ES (almeno 1)
- `fulfillment`: array con `"FBA"`, `"FBM"`, o entrambi
- `fbm_shipping_cost`: numero, costo FBM per pezzo, inclusi preparazione/imballaggio (obbligatorio se FBM presente; nessun default)
- `costi.cogs`, `costi.inbound`, `costi.dazi`: numeri in €
- `prezzi.base`: numero, prezzo IVA inclusa in €
- `prezzi.scenari`: una di [`"nessuno"`, `"ribasso"`, `"rialzo"`, `"entrambi"`]
  - `"nessuno"` → 1 scenario (solo base)
  - `"ribasso"` → 3 scenari: −20%, −10%, base
  - `"rialzo"` → 3 scenari: base, +10%, +20%
  - `"entrambi"` → 5 scenari: −20%, −10%, base, +10%, +20%
- `adv.tacos_target`: numero decimale (es. 0.10 per 10%)
- `tipo_attivita`: obbligatorio, `reselling` o `private_label`; non dedurlo dalla presenza di ADV.
- `adv.tacos_target`: se omesso, 0 per reselling o 0.10 per private label; un valore esplicito prevale. Non legato dinamicamente al testo del tipo attività nell'Excel.
- `resi.tasso_atteso`: quota ordini restituiti, 0–1; default 0.03 (stima), non limite 2–5%.
- `resi.quota_valore_recuperato`: quota media del COGS recuperata per reso, 0–1; default 0 prudenziale.
- `resi.quota_commissione_restituita`: quota della referral fee riaccreditata per reso, al netto delle trattenute, 0–1; default 0 prudenziale.
- `resi.costo_extra_per_reso`: spese aggiuntive del reso, default 4.50 stimato; esclude costi di andata e COGS già conteggiati.
- `resi.fonte`: nota che distingue dati storici e ipotesi; sempre visibile nell'Excel.
- In FBM `inbound` verso Amazon non entra nel margine; se omesso viene normalizzato a 0. Per FBA è richiesto. Altri costi di approvvigionamento reali vanno in `dazi` (dazi/altri costi).
- Nei lotti gli stessi campi `resi` e `tipo_attivita` sono comuni; separare file se i parametri divergono.

## Schema modalità BATCH

```json
{
  "mode": "batch",
  "tipo_attivita": "reselling",
  "cliente": "Nome Cliente S.r.l.",
  "settore": "Casalinghi e cucina",
  "categoria": "Home Products",
  "marketplaces": ["IT"],
  "fulfillment": "FBA",
  "fbm_shipping_cost": 5.00,
  "adv": {
    "tacos_target": 0.00
  },
  "resi": {
    "tasso_atteso": 0.03,
    "quota_valore_recuperato": 0.0,
    "quota_commissione_restituita": 0.0,
    "costo_extra_per_reso": 4.50,
    "fonte": "Stima iniziale: tasso 3%, recuperi ignoti, costo extra stimato"
  },
  "prodotti": [
    {
      "nome": "Cuscino memory foam ortocervicale",
      "cogs": 12.00,
      "inbound": 1.50,
      "dazi": 0.00,
      "peso_kg": 1.2,
      "dim_l_cm": 35,
      "dim_w_cm": 35,
      "dim_h_cm": 10,
      "prezzo": 39.90
    },
    {
      "nome": "Topper materasso 160x190",
      "cogs": 28.00,
      "inbound": 4.50,
      "dazi": 0.00,
      "peso_kg": 6.5,
      "dim_l_cm": 50,
      "dim_w_cm": 40,
      "dim_h_cm": 30,
      "prezzo": 119.00
    }
  ]
}
```

### Note batch

- `fulfillment` è una stringa, non array (per batch si fa solo FBA o solo FBM, non entrambi)
- `prodotti` è array di N elementi, ogni elemento è un prodotto completo
- Parametri comuni (categoria, marketplaces, tacos, resi) si applicano a TUTTI i prodotti
- Per ora il batch supporta 1 marketplace alla volta; se l'AM chiede più marketplace in batch, generare 1 file per marketplace o avvisare che non è supportato

## Validazione prima di lanciare lo script

- Verifica che `prodotto.categoria` (single) o `categoria` (batch) sia presente nella lista delle referral fees
- Prezzo, peso e misure FBA devono essere positivi; costi non negativi; percentuali comprese fra 0 e 1; rifiutare NaN/Infinity e booleani.
- Non bloccare prodotti in perdita: il calcolo serve anche a riconoscerli. Prezzo deve essere maggiore di zero.
- Verifica che almeno un marketplace sia specificato

Se la validazione fallisce, NON lanciare lo script: chiedi all'AM di correggere.
