# GPSR su eBay — ricerca tecnica (2026-08-30)

Verifica fatta prima di estendere l'agente ai campi GPSR. Tutto misurato dal
vivo in sola lettura sugli account veri di Galeone e Tecno Cold, non dedotto
dalla documentazione.

## 1. Dove stanno i campi

Container `Item.Regulatory` (`RegulatoryType`), dentro lo stesso `SetItem` che
l'agente già chiama. **Non** sono item specifics / aspects: sono un blocco a sé.
`GetItem` con `DetailLevel=ReturnAll` li **restituisce** (verificato), quindi il
pattern anteprima prima→dopo e la rilettura dopo la scrittura funzionano come
per gli altri campi.

Struttura reale, copiata da un'inserzione Tecno Cold già compilata (item
357839242018):

```xml
<Regulatory>
  <ProductSafety>
    <Pictograms>
      <Pictogram>EBPSP101</Pictogram>   <!-- codici eBay, non testo libero -->
      <Pictogram>EBPSP201</Pictogram>
    </Pictograms>
  </ProductSafety>
  <Manufacturer>
    <CompanyName/><Street1/><CityName/><StateOrProvince/>
    <PostalCode/><Country/><Phone/><Email/>
  </Manufacturer>
  <ResponsiblePersons>
    <ResponsiblePerson>
      <CompanyName/><Street1/><CityName/><StateOrProvince/>
      <PostalCode/><Country/><Phone/><Email/>
      <Types><Type>EUResponsiblePerson</Type></Types>
    </ResponsiblePerson>
  </ResponsiblePersons>
</Regulatory>
```

Visto anche `Documents/Document/DocumentID` (7 inserzioni Tecno Cold su 25).
Massimo 5 `ResponsiblePerson` per inserzione. `ProductSafety` richiede almeno
uno fra `Pictograms` e `Statements`; `Component` è opzionale.

**Correzione a quanto detto a Mariano in chat**: il responsabile UE NON è un
dato di solo account da compilare in Seller Hub — è per-inserzione, dentro
`Item.Regulatory`, quindi scrivibile dall'agente.

## 2. Dipendenza dalla categoria

Fonte: Metadata API REST `getRegulatoryPolicies`
(`/sell/metadata/v1/marketplace/{mk}/get_regulatory_policies?filter=categoryIds:{a|b|c}`),
raggiungibile con `Sessione.rest()` già esistente in `ebay-core`.

Interrogate **tutte** le categorie vive dei due clienti su EBAY_IT:

| Cliente | Categorie distinte | Esito |
|---|---|---|
| Galeone | 21 (66 inserzioni) | `PRODUCT_SAFETY` + `MANUFACTURER_CONTACT` = RECOMMENDED su **tutte** |
| Tecno Cold | 37 (173 inserzioni) | idem, più `HAZMAT` e `ENERGY_EFFICIENCY` = OPTIONAL su alcune |

**Nessuna categoria è REQUIRED e nessuna rifiuta i campi**: si può scrivere
ovunque, l'estensione non ha bisogno di una lista di categorie ammesse. Vale la
pena però leggere `getRegulatoryPolicies` in fase di anteprima per dire a
Mariano se quella categoria abilita anche HAZMAT/ENERGY_EFFICIENCY.

## 3. Stato attuale dei due account (campione 25 inserzioni ciascuno)

- **Galeone**: 0/25 hanno `Regulatory`. Gap totale.
- **Tecno Cold**: 18/25 hanno `Regulatory`, ma quasi sempre parziale — 18 con
  `ProductSafety`, solo **10** con `Manufacturer` + `ResponsiblePersons`
  completi. Dentro la stessa categoria convivono inserzioni compilate e non:
  la differenza è chi le ha caricate, non un limite di eBay.

**Conseguenza utile**: l'anagrafica compliance di Tecno Cold (responsabile UE
`cliente@example.com`, e i vari produttori per marca) **esiste già dentro le sue
inserzioni**. Per Tecno Cold si può ricavare dalle inserzioni compilate invece
di aspettare il modulo dal cliente — resta da farsi confermare che quella sia
l'anagrafica giusta e da chiarire che il produttore cambia per marca
(Pego S.r.l. sull'item campione), quindi non è un valore unico per cliente.
Per Galeone serve il modulo, non c'è nessun dato da cui partire.

## 4. Trappola già pagata

La prima sonda ometteva il prologo `<?xml version="1.0" encoding="utf-8"?>` nel
corpo della richiesta: eBay rispondeva `Ack=Failure` "Errore di analisi XML" e
la sonda leggeva "Regulatory assente" su tutto, **falso negativo su entrambi i
clienti**. Il prologo c'è in `letto.richiesta()` e in `catalogo._richiesta_item()`:
per qualsiasi sonda futura, riusare quelle funzioni invece di riscrivere l'XML.
Controllare sempre `<Ack>` prima di interpretare un'assenza come un dato.

## 5. Come si risolve il produttore (deciso da Mariano, 2026-08-30)

**Ibrido: tabella marca→produttore come default, override per articolo dove
serve.** Non un produttore unico per cliente, e nemmeno uno per articolo.

Misurato su 40 inserzioni arricchite per cliente:

| Cliente | Marche distinte | Nota |
|---|---|---|
| Galeone | 10, ma 32/40 articoli sono a marca propria | per articolo = 32 volte lo stesso dato |
| Tecno Cold | 12, e **14/40 "Senza marca/Generico"** | per marca non basta: quei 14 vanno mappati a mano |

Due trappole da gestire nel codice:
- **Grafie multiple della stessa marca**: Galeone scrive la propria in 4 modi
  ("Galeone Biancheria e Tessuti", "Galeone - Biancheria e Tessu",
  "Galeone BIancheria e Tessuti", "Galeone Biancheria e tessuti"). Un match
  esatto sulla marca fallisce: serve normalizzazione.
- **Articoli senza marca**: non hanno una chiave su cui agganciare la tabella,
  vanno risolti per articolo (sezione B-bis del modulo).

## 6. Cosa manca prima del codice

- Anagrafica compliance per cliente: modulo master v2 in Drive
  (`MASTER - Modulo Compliance GPSR (tutti i clienti)`,
  id `1LR18S6MD4EzBWgAwXtWFdLBuM57Q2gktoCmU3NdPK_g`), già strutturato per marca.
  Per Tecno Cold si può precompilare dai dati già sulle inserzioni (§3).
- Capire i codici `Pictogram` (EBPSP101, EBPSP201...): sono enum eBay, serve la
  lista dei valori ammessi prima di proporli in anteprima.
