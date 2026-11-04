# Stima rifornimento logistica Amazon (FBA)

Non è una skill a parte: è il comando `stima` della skill **amazon-spedizioni**. Il codice sta in [`../amazon-spedizioni/scripts/stima.py`](../amazon-spedizioni/scripts/stima.py), i test in `../amazon-spedizioni/tests/test_stima.py`.

```bash
python3 giro.py stima <cliente> --giorni N | --fino AAAA-MM-GG [--transito 10] [--scorta 14] [--sku S ...]
```

Dice quante unità mandare in FBA per coprire un periodo. Legge soltanto, non prenota nulla. Collaudata dal vivo su ECM il 01/11/2026.

Questa cartella esiste per avere una **scheda di test separata**: le correzioni al codice si fanno in `amazon-spedizioni`.
