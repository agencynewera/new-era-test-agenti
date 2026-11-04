"""Compone l'anagrafica GPSR di un cliente a partire da cio' che e' gia'
scritto nelle sue inserzioni. Nessun dato inventato: per ogni marca si prende
la grafia PIU' FREQUENTE fra quelle trovate, e le marche su cui il catalogo si
contraddice escono segnalate invece che risolte a caso.
"""
import json
from collections import Counter, defaultdict

SORGENTE = "gpsr_tecnocold.json"
FUORI = "tecno-cold.yaml"
# Albinea (RE) ha CAP 42020: il 42024 trovato su un'inserzione e' Castelnovo
# di Sotto. Si corregge qui, dove il dato diventa canonico.
CAP_ALBINEA = "42020"
# "Senza marca/Generico" NON e' una marca: e' l'assenza di marca, e sotto quella
# voce Tecno Cold tiene 14 articoli che non hanno niente in comune. Il primo
# giro del generatore ci aveva agganciato il produttore piu' frequente fra
# quelli - un fabbricante cinese di ventole - che sarebbe finito su tutti e 14.
# Un produttore sbagliato scritto in un campo di conformita' e' peggio di un
# campo vuoto: questi vanno risolti per articolo, non per marca.
NON_SONO_MARCHE = ("- senza marca/generico -", "senza marca/generico",
                   "senza marca", "generico", "unbranded", "")


def piu_frequente(contatore):
    return json.loads(contatore.most_common(1)[0][0])


def riga_yaml(dati, indent):
    sp = " " * indent
    return "\n".join('%s%s: "%s"' % (sp, k, str(v).replace('"', "'"))
                     for k, v in dati.items() if v and k != "Types")


def main():
    lette = json.load(open(SORGENTE))["lette"]

    responsabili = Counter(json.dumps(r, sort_keys=True, ensure_ascii=False)
                           for x in lette for r in x["responsabili"])
    responsabile = piu_frequente(responsabili)
    responsabile.pop("Types", None)
    if responsabile.get("CityName", "").lower() == "albinea":
        responsabile["PostalCode"] = CAP_ALBINEA

    per_marca = defaultdict(Counter)
    senza_marca = 0
    for x in lette:
        if not x["produttore"]:
            continue
        if (x["marca"] or "").strip().lower() in NON_SONO_MARCHE:
            senza_marca += 1
            continue
        per_marca[x["marca"]][
            json.dumps(x["produttore"], sort_keys=True, ensure_ascii=False)] += 1

    righe = [
        "# Anagrafica GPSR — Tecno Cold",
        "# Ricavata il 2026-09-02 da cio' che era gia' scritto nelle 144",
        "# inserzioni EBAY_IT del cliente, non da un modulo compilato.",
        "# La grafia scelta per ogni produttore e' la piu' frequente.",
        "",
        "responsabile_ue:",
        riga_yaml(responsabile, 2),
        "",
        "# Le due avvertenze che il cliente usa gia' su 126 inserzioni.",
        "avvertenze_default:",
        "  - EBPSP101   # Non adatto ai bambini piccoli",
        "  - EBPSP201   # Marchio CE",
        "",
        "produttori:",
    ]

    incerte = []
    for marca in sorted(per_marca, key=str.lower):
        varianti = per_marca[marca]
        nomi = {json.loads(k)["CompanyName"] for k in varianti}
        if len(nomi) > 1:
            incerte.append((marca, sorted(nomi)))
            continue
        righe.append('  "%s":' % marca.replace('"', "'"))
        righe.append(riga_yaml(piu_frequente(varianti), 4))

    if incerte:
        righe += ["", "# DA DECIDERE — su queste marche il catalogo si contraddice:",
                  "# piu' produttori diversi sotto la stessa marca. Finche' restano",
                  "# qui, l'agente non le tocca."]
        for marca, nomi in incerte:
            righe.append("#   %s -> %s" % (marca, " | ".join(nomi)))

    righe += ["",
              "# Gli articoli marcati 'Senza marca/Generico' non stanno qui di",
              "# proposito: non hanno una chiave a cui agganciare un produttore",
              "# e vanno decisi uno per uno (%d con un produttore gia' scritto,"
              % senza_marca,
              "# piu' quelli ancora vuoti)."]
    open(FUORI, "w").write("\n".join(righe) + "\n")
    print("scritto %s" % FUORI)
    print("  articoli senza marca lasciati fuori: %d" % senza_marca)
    print("  responsabile UE: %s (CAP %s)"
          % (responsabile["CompanyName"], responsabile["PostalCode"]))
    print("  produttori certi: %d" % (len(per_marca) - len(incerte)))
    print("  marche contraddittorie lasciate fuori: %d" % len(incerte))
    for marca, nomi in incerte:
        print("     %s -> %s" % (marca, " | ".join(nomi)))


if __name__ == "__main__":
    main()
