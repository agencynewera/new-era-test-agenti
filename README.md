# New Era · Test agenti

Repo di lavoro di Mariano ed Edoardo per collaudare e migliorare gli agenti di New Era, **un agente alla volta**.

Gli originali restano dove sono (`~/.claude/skills`, `~/nova`, repo `new-era-agenti`). Qui entra una **copia** dell'agente scelto, senza chiavi né dati dei clienti; le migliorie approvate si riportano poi nell'originale.

## Come funziona

1. **Scegliamo insieme** il prossimo agente e lo segniamo in [REGISTRO.md](REGISTRO.md) come `scelto`.
2. **Mariano lo carica**: `bin/aggiungi-agente.sh <percorso-originale> <nome>`. Lo script copia in `agenti/<nome>/` (esclusi `.env`, token, database, log, ambienti), aggiunge la scheda di test e passa gitleaks.
3. **Edoardo lavora su un ramo** `test/<nome>`: compila `agenti/<nome>/SCHEDA-TEST.md`, apre una issue per ogni problema (modello «Problema agente»), propone le correzioni.
4. **Pull request** verso `main`: Mariano legge e decide se fondere.
5. A fine giro l'agente passa a `rivisto` nel registro e si riporta la versione buona nell'originale.

Quando tutti gli agenti scelti sono `rivisto`, si fa la **revisione finale** insieme.

## Regole

- Mai chiavi, password, token o dati dei clienti nel repo: si usano dati finti o di test.
- Nessuna scrittura sugli account veri (eBay, Amazon, Temu, Ads) durante i test, salvo prova concordata con Mariano.
- `main` si tocca solo con una pull request approvata da Mariano.

---
*Increase your performance* · New Era S.r.l.
