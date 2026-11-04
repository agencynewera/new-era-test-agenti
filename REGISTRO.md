# Registro degli agenti in collaudo

Stati: `scelto` → `in test` → `da migliorare` → `rivisto`

| Agente | Cartella | Stato | Caricato il | Ramo | Note |
|---|---|---|---|---|---|
| Caricatore e aggiornatore annunci Amazon | `agenti/amazon-inserzioni` | scelto | 04/11/2026 | `test/amazon-inserzioni` | 307 test verdi; include A+ e campi GPSR |
| Spedizioniere alla logistica Amazon (FBA) | `agenti/amazon-spedizioni` | scelto | 04/11/2026 | `test/amazon-spedizioni` | 524 test verdi; `clienti.example.yaml` con indirizzi e telefoni finti |
| Stima rifornimento logistica Amazon | `agenti/amazon-stima-rifornimento` | scelto | 04/11/2026 | `test/amazon-stima-rifornimento` | è il comando `stima` di amazon-spedizioni |
| GPSR ed EPR | `agenti/gpsr-epr` | scelto | 04/11/2026 | `test/gpsr-epr` | GPSR dentro amazon-inserzioni; EPR da progettare |
| Listini reselling (controllo listino fornitore) | `agenti/amazon-controllo-listino` | scelto | 04/11/2026 | `test/amazon-controllo-listino` | 20 test verdi |
| Marginalità Amazon | `agenti/marginalita-amazon` | scelto | 04/11/2026 | `test/marginalita-amazon` | versione del repo del team (26/09), nessun test automatico |
