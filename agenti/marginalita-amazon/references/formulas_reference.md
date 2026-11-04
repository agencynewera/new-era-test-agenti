# Riferimento Formule Excel — Marginalità Amazon

## Logica di calcolo

Il margine atteso per ordine iniziale (prima dei costi generali e imposte sul reddito) è:

```
Margine = Prezzo Netto IVA esclusa
        − Referral Fee
        − Fulfillment Fee (FBA o FBM)
        − COGS
        − Inbound (solo FBA)
        − Dazi
        − Costo ADV
        − Rettifica Resi
```

## Formule Excel

Le formule devono riferirsi a celle, mai a valori hardcoded.

Assumiamo questa struttura del foglio "Confronto Marketplace" (modalità SINGLE):
- Colonna A: label
- Colonna B,C,D,E,F: scenari prezzo (fino a 5)
- Le fee sono in foglio `Fee Reference`

### 1. Prezzo netto IVA esclusa

```excel
= Prezzo_IVA_inclusa / (1 + IVA_marketplace)
```

dove `IVA_marketplace` è VLOOKUP nel foglio `Fee Reference`:

```excel
= B5 / (1 + VLOOKUP("IT", FeeReference!IVA_Table, 2, FALSE))
```

### 2. Referral fee (con tier se applicabile)

Per categorie con tier (es. Clothing 5/10/15%):

```excel
= B5 * IF(B5<=15, 0.05, IF(B5<=20, 0.10, 0.15))
```

Per categorie a fee fissa:

```excel
= B5 * VLOOKUP(Categoria, FeeReference!Referral_Table, 2, FALSE)
```

Applicare il minimum fee:

```excel
= MAX(B5 * %_fee, Minimum_fee)
```

### 3. FBA fee

Il size tier va determinato in una cella separata, poi applicare la tariffa.

**Calcolo peso fatturabile (peso reale vs volumetrico):**

```excel
PesoVolumetrico = (L * W * H) / 5000   [risultato in kg]
PesoFatturabile = MAX(PesoReale, PesoVolumetrico)
```

**Determinazione size tier** (foglio Input prodotto, cella separata):

```excel
= IF(AND(L<=33, W<=23, H<=2.5, Peso<=0.08), "ENV_S",
  IF(AND(L<=33, W<=23, H<=2.5, Peso<=0.46), "ENV_M",
  IF(AND(L<=33, W<=23, H<=4, Peso<=0.96), "ENV_L",
  IF(AND(L<=33, W<=23, H<=6, Peso<=0.96), "ENV_XL",
  IF(AND(L<=35, W<=25, H<=12, Peso<=3.9), "PARCEL_S",
  IF(AND(L<=45, W<=34, H<=26, Peso<=12), "PARCEL_STD",
  IF(AND(L<=61, W<=46, H<=46, Peso<=23), "PARCEL_L",
  IF(AND(L<=120, W<=60, H<=60, Peso<=30), "OVERSIZE_S",
  IF(AND(L<=175, W<=120, H<=120, Peso<=50), "OVERSIZE_L",
  "SPECIAL_OVERSIZE")))))))))
```

**Lookup tariffa base + extra peso:**

```excel
FBA_base = VLOOKUP(SizeTier, FeeReference!FBA_Table, 2, FALSE)
Peso_eccedente_kg = MAX(0, PesoFatturabile - Peso_limite_tier)
Extra = Peso_eccedente_kg * VLOOKUP(SizeTier, FeeReference!FBA_Table, 4, FALSE)
FBA_totale = FBA_base + Extra
```

In modalità semplificata (senza calcolo eccedenza precisa), per la maggior parte dei prodotti standard:

```excel
= VLOOKUP(SizeTier, FeeReference!FBA_Table, 2, FALSE)
```

### 4. Fulfillment FBM

```excel
= InputProdotto!FBM_shipping_cost
```

(costo fornito direttamente dall'AM)

### 5. Costo ADV

Reselling: default 0%; private label: default 10%. Espliciti override sempre rispettati. Base: fatturato lordo prima dei resi.

```excel
CostoADV = Prezzo_IVA_inclusa * TACoS_target
```

```excel
= B5 * Input!TACoS_target
```

### 6. Rettifica economica dei resi

Tasso = ordini restituiti / ordini iniziali. Intervallo usuale 2–5%, default stimato 3%; valori reali 0–100% ammessi. Rimborsi integrali, prezzi omogenei entro ogni scenario; per lotti si calcola riga per riga.

```
Rimborsi_lordi_attesi_per_ordine = Tasso * Prezzo_IVA_inclusa
Recupero_merce_per_reso = COGS * Quota_valore_recuperato
Commissione_restituita_per_reso = ReferralFee * Quota_commissione_restituita
RettificaResi = Tasso * (Prezzo_netto_IVA - Recupero_merce_per_reso
                      - Commissione_restituita_per_reso + Costo_extra_per_reso)
```

La rettifica si sottrae dal margine base, che include già COGS, logistica di andata e ADV su tutti gli ordini. Non sottrarre di nuovo la merce irrecuperabile; il suo costo è già nel COGS iniziale. Non sottrarre anche i rimborsi lordi: il ricavo rimborsato è già incluso nella rettifica senza IVA.

Quote recuperate iniziali 0% se ignote (prudenziale), costo extra iniziale 4,50 € stimato e modificabile. Il valore recuperato riguarda il solo COGS. Resi con rimborsi parziali/cambi o recuperi della logistica non sono modellati.

### 7. Margine netto unitario

```excel
Margine = Prezzo_netto_IVA - ReferralFee - FBA_fee - COGS - Inbound_solo_FBA - Dazi - CostoADV - RettificaResi
```

### 8. Margine percentuale

```excel
Margine_% = Margine / Prezzo_IVA_inclusa
```

Da formattare come percentuale.

### 9. Break-even ACoS

```excel
Margine_pre_ADV = Margine + CostoADV
BreakEvenACoS = Margine_pre_ADV / Prezzo_netto_IVA
```

Indicatore preesistente non validato per confronto con console Ads: il denominatore senza IVA non basta a definire ACoS. Non usarlo come soglia operativa.

### 10. TACoS sostenibile (target margine)

Per ottenere un margine target del X%:

```excel
TACoS_sostenibile = (Margine_pre_ADV - Margine_target) / Prezzo_IVA_inclusa
```

## Conditional formatting

Applicare a celle margine_%:

- Se `< 0`: fill `#FEE2E2`, font `#991B1B` bold
- Se `>= 0` e `< 0.10`: fill `#FEF3C7`
- Se `>= 0.10` e `< 0.15`: fill `#FFFBEB`
- Se `>= 0.15`: fill `#D1FAE5`, font `#065F46` bold

## Format numerici

- Prezzi e fee in €: `#,##0.00 "€"`
- Percentuali: `0.0%`
- Pesi: `0.000 "kg"`
- Dimensioni: `0 "cm"`

## Comments / note nelle celle

Aggiungere commenti Excel alle celle output principali per spiegare la formula:
- Cella Margine: "Margine netto unitario dopo tutte le commissioni e costi"
- Cella Break-even ACoS: "% massima di ACoS oltre la quale vai in perdita su quella vendita"
- Cella TACoS sostenibile: "% di spesa ADV totale sostenibile mantenendo profitto positivo"
