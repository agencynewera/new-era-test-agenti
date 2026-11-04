import json
from datetime import datetime, timedelta, timezone

import pytest

import registro
from modello import OpzioneTrasporto, RigaSpedizione, Spedizione


def _pos(riferimento):
    """Il riferimento è "AAAA-MM-GG#N#lotto": la posizione nel file."""
    return int(riferimento.split("#")[1])


def _cartella_registro(tmp_path):
    return tmp_path / ".config" / "newera-amazon" / "spedizioni"


def test_nessun_file_prima_del_primo_tentativo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert registro.ultimo_lotto("Phy to Pet") == []


def test_tentativo_poi_piano_poi_esito(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    righe = [{"sku": "SKU-1", "quantita": 30}]
    rif = registro.registra_tentativo("Phy to Pet", righe)
    riga = registro.ultimo_lotto("Phy to Pet")[_pos(rif)]
    assert riga["righe"] == righe
    assert riga["esito"] == "in corso"
    assert riga["inbound_plan_id"] is None

    spedizioni = [{"shipment_id": "sh1", "centro_logistico": "FC-MXP5"}]
    registro.registra_piano("Phy to Pet", rif, "wf1", spedizioni)
    riga = registro.ultimo_lotto("Phy to Pet")[_pos(rif)]
    assert riga["inbound_plan_id"] == "wf1"
    assert riga["spedizioni"] == spedizioni

    registro.registra_esito("Phy to Pet", rif, "completata")
    assert registro.ultimo_lotto("Phy to Pet")[_pos(rif)]["esito"] == "completata"


def test_trasporto_confermato_aggiorna_la_spedizione_giusta(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    registro.registra_piano("Phy to Pet", rif, "wf1", [
        {"shipment_id": "sh1", "centro_logistico": "FC-MXP5"},
        {"shipment_id": "sh2", "centro_logistico": "FC-BLQ1"},
    ])
    opzione = {"corriere": "UPS", "convenzionato": True, "costo": 42.5}
    registro.registra_trasporto_confermato("Phy to Pet", rif, {"sh2": opzione})
    spedizioni = registro.ultimo_lotto("Phy to Pet")[_pos(rif)]["spedizioni"]
    assert spedizioni[0].get("trasporto_confermato") is None
    assert spedizioni[1]["trasporto_confermato"] == opzione


def test_due_tentativi_non_si_sovrascrivono(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    i2 = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-2", "quantita": 5}])
    assert i1 != i2
    registro.registra_esito("Phy to Pet", i1, "completata")
    righe = registro.ultimo_lotto("Phy to Pet")
    assert righe[_pos(i1)]["esito"] == "completata"
    assert righe[_pos(i2)]["esito"] == "in corso"


def test_clienti_diversi_hanno_file_diversi(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    assert registro.ultimo_lotto("Bedshire") == []


def test_accetta_le_dataclass_del_flusso(tmp_path, monkeypatch):
    """SKILL.md passa gli oggetti tornati dai moduli, non dei dizionari."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [RigaSpedizione(sku="SKU-1", quantita=30)])
    spedizioni = [
        Spedizione(shipment_id="sh1", centro_logistico="FC-MXP5",
                   righe=[RigaSpedizione(sku="SKU-1", quantita=30)]),
    ]
    registro.registra_piano("Phy to Pet", rif, "wf1", spedizioni)
    opzione = OpzioneTrasporto(transportation_option_id="to1", corriere="UPS",
                               convenzionato=True, costo=42.5, shipment_id="sh1")
    registro.registra_trasporto_confermato("Phy to Pet", rif, {"sh1": opzione})
    registro.registra_esito("Phy to Pet", rif, "completata")

    # .glob("*.json"), non .iterdir(): la cartella ha anche il .lock del cliente.
    scritto = json.loads(next(_cartella_registro(tmp_path).glob("*.json")).read_text())
    riga = scritto[_pos(rif)]
    assert riga["righe"] == [{"sku": "SKU-1", "quantita": 30, "scadenza": None}]
    assert riga["esito"] == "completata"
    spedizione = riga["spedizioni"][0]
    assert spedizione["shipment_id"] == "sh1"
    assert spedizione["centro_logistico"] == "FC-MXP5"
    assert spedizione["righe"] == [{"sku": "SKU-1", "quantita": 30, "scadenza": None}]
    assert spedizione["trasporto_confermato"]["corriere"] == "UPS"
    assert spedizione["trasporto_confermato"]["costo"] == 42.5


def test_scrittura_fallita_non_distrugge_le_voci_gia_scritte(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    prima = registro.ultimo_lotto("Phy to Pet")

    with pytest.raises(TypeError):
        registro.registra_esito("Phy to Pet", rif, object())

    assert registro.ultimo_lotto("Phy to Pet") == prima


def test_nome_cliente_con_percorso_non_esce_dalla_cartella(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    desktop = tmp_path / "Desktop"
    desktop.mkdir()

    registro.registra_tentativo("../../../Desktop/report", [{"sku": "SKU-1", "quantita": 30}])

    assert list(desktop.iterdir()) == []
    # .json, non tutti i file: la cartella ha anche il .lock dello stesso cliente.
    assert len(list(_cartella_registro(tmp_path).glob("*.json"))) == 1


def test_nome_cliente_leggibile_resta_leggibile(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    oggi = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert next(_cartella_registro(tmp_path).glob("*.json")).name == f"Phy to Pet-{oggi}.json"


def test_piazzamento_confermato_scritto_subito_col_costo(tmp_path, monkeypatch):
    """La commissione di piazzamento è spesa reale: va tracciata prima che il
    trasporto (passo separato, che può fallire) sia confermato."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    registro.registra_piano("Phy to Pet", rif, "wf1", [{"shipment_id": "sh1"}])

    registro.registra_piazzamento_confermato("Phy to Pet", rif, "po1", commissioni=12.5)

    piazzamento = registro.ultimo_lotto("Phy to Pet")[_pos(rif)]["piazzamento_confermato"]
    assert piazzamento["placement_option_id"] == "po1"
    assert piazzamento["commissioni"] == 12.5
    assert piazzamento["valuta"] == "EUR"


def test_lotto_a_cavallo_di_mezzanotte_aggiorna_il_file_di_ieri(tmp_path, monkeypatch):
    """Il riferimento porta la data del file in cui la voce è nata: dopo
    mezzanotte UTC la scrittura resta su quel file, non su quello di oggi."""
    monkeypatch.setenv("HOME", str(tmp_path))
    cartella = _cartella_registro(tmp_path)
    cartella.mkdir(parents=True)
    ieri = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    file_ieri = cartella / f"Phy to Pet-{ieri}.json"
    file_ieri.write_text(json.dumps([{
        "lotto_id": "vecchio01", "cliente": "Phy to Pet",
        "righe": [{"sku": "SKU-1", "quantita": 30}], "esito": "in corso",
        "inbound_plan_id": None, "spedizioni": [],
    }]))

    registro.registra_esito("Phy to Pet", f"{ieri}#0#vecchio01", "completata")

    assert json.loads(file_ieri.read_text())[0]["esito"] == "completata"
    assert registro.ultimo_lotto("Phy to Pet", ieri)[0]["esito"] == "completata"


def test_lotto_di_ieri_non_finisce_sul_lotto_di_oggi(tmp_path, monkeypatch):
    """Due lotti in posizione 0 di giorni diversi sono voci distinte: la spesa
    dell'uno non deve mai essere scritta sull'altro."""
    monkeypatch.setenv("HOME", str(tmp_path))
    cartella = _cartella_registro(tmp_path)
    cartella.mkdir(parents=True)
    ieri = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    (cartella / f"Phy to Pet-{ieri}.json").write_text(json.dumps([{
        "lotto_id": "vecchio01", "cliente": "Phy to Pet",
        "righe": [{"sku": "VECCHIO", "quantita": 1}], "esito": "in corso",
        "inbound_plan_id": None, "spedizioni": [],
    }]))
    rif_oggi = registro.registra_tentativo("Phy to Pet", [{"sku": "NUOVO", "quantita": 2}])

    registro.registra_piazzamento_confermato("Phy to Pet", f"{ieri}#0#vecchio01", "po-vecchia", commissioni=9.0)

    assert registro.ultimo_lotto("Phy to Pet", ieri)[0]["piazzamento_confermato"]["commissioni"] == 9.0
    assert "piazzamento_confermato" not in registro.ultimo_lotto("Phy to Pet")[_pos(rif_oggi)]


def test_trasporto_su_spedizione_sconosciuta_non_tace(tmp_path, monkeypatch):
    """Il corriere è già prenotato: un no-op silenzioso lascerebbe una spesa
    reale senza traccia."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    registro.registra_piano("Phy to Pet", rif, "wf1", [{"shipment_id": "sh1"}])

    with pytest.raises(ValueError, match="sh9"):
        registro.registra_trasporto_confermato("Phy to Pet", rif, {"sh9": {"corriere": "UPS"}})


def test_voce_inesistente_alza_errore_parlante(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    oggi = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with pytest.raises(ValueError, match="non trovata"):
        registro.registra_esito("Phy to Pet", f"{oggi}#3#qualunque", "completata")


def test_riferimento_malformato_lo_dice(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="riferimento di registro non valido"):
        registro.registra_esito("Phy to Pet", 3, "completata")


def test_riferimento_di_un_altro_cliente_viene_respinto(tmp_path, monkeypatch):
    """Il riferimento è una posizione dentro il file di UN cliente: usarlo con
    un altro scriverebbe una spesa sopra un lotto che non c'entra."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif_a = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-A", "quantita": 1}])
    registro.registra_tentativo("Bedshire", [{"sku": "SKU-B", "quantita": 1}])

    with pytest.raises(ValueError, match="riferimento di un altro lotto"):
        registro.registra_piazzamento_confermato("Bedshire", rif_a, "po1", commissioni=210.0)

    assert "piazzamento_confermato" not in registro.ultimo_lotto("Bedshire")[0]
    assert "piazzamento_confermato" not in registro.ultimo_lotto("Phy to Pet")[0]


def test_due_clienti_col_nome_che_si_sanifica_uguale_non_si_confondono(tmp_path, monkeypatch):
    """"Puraluna (Bee Your Home)" e "Puraluna [Bee Your Home]" producono lo
    stesso nome file: il cliente si controlla sul nome vero nella voce, non sul
    file, altrimenti la spesa dell'uno finisce sul lotto dell'altro."""
    monkeypatch.setenv("HOME", str(tmp_path))
    a, b = "Puraluna (Bee Your Home)", "Puraluna [Bee Your Home]"
    rif_a = registro.registra_tentativo(a, [{"sku": "SKU-A", "quantita": 1}])
    registro.registra_tentativo(b, [{"sku": "SKU-B", "quantita": 1}])

    with pytest.raises(ValueError, match="appartiene a"):
        registro.registra_piazzamento_confermato(b, rif_a, "po-di-B", commissioni=210.0)

    assert "piazzamento_confermato" not in registro.ultimo_lotto(a)[_pos(rif_a)]


def test_due_lotti_stesso_cliente_stesso_giorno_non_si_confondono(tmp_path, monkeypatch):
    """Due lotti nello stesso giorno: i riferimenti sono distinti, una scrittura
    su uno non tocca l'altro, e un riferimento la cui posizione e il cui
    `lotto_id` non concordano viene respinto invece di scriverci sopra.

    Quello che NON è protetto qui: un riferimento intero e valido usato sul
    lotto sbagliato è indistinguibile da quello giusto — nessun controllo può
    vederlo. È per questo che il passo 2 della SKILL dice di tenere UN
    riferimento per lotto e di non ricostruirlo a mano."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif_mattina = registro.registra_tentativo("Phy to Pet", [{"sku": "MATTINA", "quantita": 1}])
    rif_pomeriggio = registro.registra_tentativo("Phy to Pet", [{"sku": "POMERIGGIO", "quantita": 1}])
    assert rif_mattina != rif_pomeriggio

    # una spesa scritta col riferimento del pomeriggio resta sul pomeriggio
    registro.registra_piazzamento_confermato("Phy to Pet", rif_pomeriggio, "po-pom", commissioni=350.0)

    lotti = registro.ultimo_lotto("Phy to Pet")
    assert lotti[_pos(rif_pomeriggio)]["piazzamento_confermato"]["commissioni"] == 350.0
    assert "piazzamento_confermato" not in lotti[_pos(rif_mattina)]

    # un riferimento forgiato, che punta al lotto sbagliato, viene respinto...
    data, _, lotto_mattina = rif_mattina.split("#")
    with pytest.raises(ValueError, match="riferimento di un altro lotto"):
        registro.registra_esito("Phy to Pet", f"{data}#1#{lotto_mattina}", "completata")

    # ...senza aver scritto niente: "respinto" deve valere anche su disco
    dopo = registro.ultimo_lotto("Phy to Pet")
    assert dopo[_pos(rif_pomeriggio)]["esito"] == "in corso"
    assert dopo[_pos(rif_pomeriggio)]["piazzamento_confermato"]["commissioni"] == 350.0


def test_lotto_diviso_registra_tutte_le_spedizioni_in_una_scrittura(tmp_path, monkeypatch):
    """Amazon prenota un lotto diviso con una chiamata sola: le prenotazioni
    già pagate devono finire su disco in UNA scrittura, non una alla volta —
    altrimenti la prima che fallisce fa perdere le sorelle. Contare le
    scritture è l'unico modo di verificarlo: lo stato finale del file è
    identico in entrambi i casi."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    registro.registra_piano("Phy to Pet", rif, "wf1", [
        {"shipment_id": "sh1"}, {"shipment_id": "sh2"},
    ])

    scritture = []
    salva_vero = registro._salva
    monkeypatch.setattr(registro, "_salva",
                        lambda p, log: (scritture.append(p), salva_vero(p, log))[1])

    registro.registra_trasporto_confermato("Phy to Pet", rif, {
        "sh1": {"corriere": "UPS", "costo": 89.5},
        "sh2": {"corriere": "BRT", "costo": 90.0},
    })

    assert len(scritture) == 1
    spedizioni = registro.ultimo_lotto("Phy to Pet")[_pos(rif)]["spedizioni"]
    assert spedizioni[0]["trasporto_confermato"]["costo"] == 89.5
    assert spedizioni[1]["trasporto_confermato"]["costo"] == 90.0


def test_spedizione_ignota_non_fa_perdere_le_altre(tmp_path, monkeypatch):
    """Una spedizione che non si colloca non deve far sparire le prenotazioni
    sorelle, già pagate nella stessa chiamata."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    registro.registra_piano("Phy to Pet", rif, "wf1", [{"shipment_id": "sh1"}])

    with pytest.raises(ValueError, match="sh9"):
        registro.registra_trasporto_confermato("Phy to Pet", rif, {
            "sh1": {"corriere": "UPS", "costo": 89.5},
            "sh9": {"corriere": "BRT", "costo": 90.0},
        })

    voce = registro.ultimo_lotto("Phy to Pet")[_pos(rif)]
    assert voce["spedizioni"][0]["trasporto_confermato"]["costo"] == 89.5
    assert voce["trasporto_non_collocato"]["sh9"]["costo"] == 90.0


def test_etichette_registrate_col_codice_fba_e_il_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 30}])
    registro.registra_piano("Phy to Pet", rif, "wf1", [{"shipment_id": "sh1"}])

    registro.registra_etichette("Phy to Pet", rif, "sh1", "FBA1234ABCD", "/tmp/e.pdf")

    spedizione = registro.ultimo_lotto("Phy to Pet")[_pos(rif)]["spedizioni"][0]
    assert spedizione["shipment_confirmation_id"] == "FBA1234ABCD"
    assert spedizione["etichette_path"] == "/tmp/e.pdf"


def test_scelte_vuote_non_passano_in_silenzio(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 1}])
    with pytest.raises(ValueError, match="nessuna scelta di trasporto"):
        registro.registra_trasporto_confermato("Phy to Pet", rif, {})


def test_trasporto_non_registrabile_lascia_il_dato_nel_messaggio(tmp_path, monkeypatch):
    """Il corriere è già prenotato: se la voce non si trova, il dato pagato
    deve sopravvivere almeno nel messaggio d'errore."""
    monkeypatch.setenv("HOME", str(tmp_path))
    oggi = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with pytest.raises(ValueError, match="89.5"):
        registro.registra_trasporto_confermato(
            "Phy to Pet", f"{oggi}#9#inesistente", {"sh1": {"corriere": "UPS", "costo": 89.5}})


def test_trasporto_su_disco_non_scrivibile_lascia_il_dato_nel_messaggio(tmp_path, monkeypatch):
    """Non solo la voce mancante: qualunque guasto in scrittura non deve far
    sparire una prenotazione già pagata."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 1}])
    registro.registra_piano("Phy to Pet", rif, "wf1", [{"shipment_id": "sh1"}])

    def _rotto(percorso, log):
        raise OSError("disco pieno")
    monkeypatch.setattr(registro, "_salva", _rotto)

    with pytest.raises(ValueError, match="89.5"):
        registro.registra_trasporto_confermato(
            "Phy to Pet", rif, {"sh1": {"corriere": "UPS", "costo": 89.5}})


def test_etichette_su_spedizione_ignota_le_scrive_e_poi_alza(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 1}])
    registro.registra_piano("Phy to Pet", rif, "wf1", [{"shipment_id": "sh1"}])

    with pytest.raises(ValueError, match="etichette_non_collocate"):
        registro.registra_etichette("Phy to Pet", rif, "sh9", "FBA9999ZZZZ", "/tmp/e9.pdf")

    orfane = registro.ultimo_lotto("Phy to Pet")[_pos(rif)]["etichette_non_collocate"]
    assert orfane["sh9"][0]["shipment_confirmation_id"] == "FBA9999ZZZZ"
    assert orfane["sh9"][0]["percorso_pdf"] == "/tmp/e9.pdf"


def test_due_etichette_orfane_non_si_cancellano_a_vicenda(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 1}])
    registro.registra_piano("Phy to Pet", rif, "wf1", [{"shipment_id": "sh1"}])

    for fba, pdf in (("FBA-AAA", "/tmp/primo.pdf"), ("FBA-BBB", "/tmp/secondo.pdf")):
        with pytest.raises(ValueError):
            registro.registra_etichette("Phy to Pet", rif, "sh9", fba, pdf)

    orfane = registro.ultimo_lotto("Phy to Pet")[_pos(rif)]["etichette_non_collocate"]["sh9"]
    assert [o["percorso_pdf"] for o in orfane] == ["/tmp/primo.pdf", "/tmp/secondo.pdf"]


def test_piazzamento_gia_registrato_non_viene_sovrascritto(tmp_path, monkeypatch):
    """La commissione è irreversibile: sovrascriverla cancellerebbe la traccia
    di una spesa reale. Un secondo piazzamento diverso è il segno di un
    riferimento sbagliato."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Phy to Pet", [{"sku": "SKU-1", "quantita": 1}])
    registro.registra_piazzamento_confermato("Phy to Pet", rif, "po-vero", commissioni=350.0)

    with pytest.raises(ValueError, match="già un piazzamento confermato"):
        registro.registra_piazzamento_confermato("Phy to Pet", rif, "po-altro", commissioni=12.5)

    assert registro.ultimo_lotto("Phy to Pet")[_pos(rif)]["piazzamento_confermato"]["commissioni"] == 350.0


def test_ultimo_lotto_non_mescola_clienti_finiti_nello_stesso_file(tmp_path, monkeypatch):
    """Due nomi che si sanificano uguali condividono il file: la lettura non
    deve mai mostrare i lotti di un cliente dentro la vista di un altro."""
    monkeypatch.setenv("HOME", str(tmp_path))
    a, b = "Puraluna (Bee Your Home)", "Puraluna [Bee Your Home]"
    registro.registra_tentativo(a, [{"sku": "SKU-A", "quantita": 1}])
    registro.registra_tentativo(b, [{"sku": "SKU-B", "quantita": 1}])

    assert [v["righe"][0]["sku"] for v in registro.ultimo_lotto(a)] == ["SKU-A"]
    assert [v["righe"][0]["sku"] for v in registro.ultimo_lotto(b)] == ["SKU-B"]


def test_registra_asn_scrive_il_codice_sulla_spedizione(tmp_path, monkeypatch):
    """Con corriere proprio Amazon emette l'ASN qualche minuto DOPO la
    prenotazione: quando arriva, la spedizione è già registrata e va solo
    completata."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Arcipelago", [RigaSpedizione("138_FBA", 108)])
    registro.registra_piano("Arcipelago", rif, "wf1",
                            [Spedizione(shipment_id="sh1", centro_logistico="XLI8")])
    registro.registra_asn("Arcipelago", rif, "sh1", "5FJEWLUY")
    voce = registro.ultimo_lotto("Arcipelago")[0]
    assert voce["spedizioni"][0]["amazon_reference_id"] == "5FJEWLUY"


def test_registra_asn_su_spedizione_sconosciuta_alza(tmp_path, monkeypatch):
    """Un ASN scritto sulla spedizione sbagliata è peggio di un ASN mancante:
    manderebbe il trasportatore a citare un codice che non è il suo."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("Arcipelago", [RigaSpedizione("138_FBA", 108)])
    registro.registra_piano("Arcipelago", rif, "wf1",
                            [Spedizione(shipment_id="sh1", centro_logistico="XLI8")])
    with pytest.raises(ValueError, match="shX"):
        registro.registra_asn("Arcipelago", rif, "shX", "5FJEWLUY")


def test_voce_torna_il_lotto_del_riferimento_anche_se_non_e_il_primo_del_file(tmp_path, monkeypatch):
    """`ultimo_lotto` filtra per cliente, quindi le sue posizioni non sono
    quelle del file: la voce di un riferimento si chiede con `voce`, che
    controlla anche lotto_id e cliente come ogni scrittura."""
    monkeypatch.setenv("HOME", str(tmp_path))
    a, b = "Puraluna (Bee Your Home)", "Puraluna [Bee Your Home]"
    registro.registra_tentativo(b, [{"sku": "SKU-B", "quantita": 1}])
    rif_a = registro.registra_tentativo(a, [{"sku": "SKU-A", "quantita": 1}])
    assert _pos(rif_a) == 1
    assert registro.voce(a, rif_a)["righe"][0]["sku"] == "SKU-A"
    with pytest.raises(ValueError, match="appartiene a"):
        registro.voce(b, rif_a)


def test_venti_scritture_insieme_non_perdono_voci_e_non_corrompono_il_file(tmp_path, monkeypatch):
    """Collaudo del 06/09: venti processi su registra_tentativo perdevano voci
    e al terzo giro corrompevano il JSON (stesso .tmp per tutti). Qui venti
    thread, con una lettura lenta che allarga la finestra della corsa: ognuno
    apre il suo lucchetto, quindi si mettono in fila."""
    import threading
    import time
    monkeypatch.setenv("HOME", str(tmp_path))
    originale = registro._carica

    def carica_lenta(percorso):
        log = originale(percorso)
        time.sleep(0.01)
        return log

    monkeypatch.setattr(registro, "_carica", carica_lenta)
    righe = [{"sku": "SKU-1", "quantita": 1}]
    errori = []

    def uno():
        try:
            registro.registra_tentativo("Phy to Pet", righe)
        except Exception as e:      # il test deve vedere qualunque cosa
            errori.append(e)

    fili = [threading.Thread(target=uno) for _ in range(20)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()
    assert errori == []
    voci = registro.ultimo_lotto("Phy to Pet")
    assert len(voci) == 20
    assert len({v["lotto_id"] for v in voci}) == 20


def test_registra_tracking_scrive_le_lettere_di_vettura_sulla_spedizione(tmp_path, monkeypatch):
    """Col corriere proprio le lettere di vettura sono l'unico modo di seguire
    i colli: senza questa riga vivrebbero solo nella mail del cliente."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("ECM", [RigaSpedizione("51NERO_FBA", 132)])
    registro.registra_piano("ECM", rif, "wf5545452f",
                            [Spedizione(shipment_id="sh1", centro_logistico="MXP5")])
    registro.registra_tracking("ECM", rif, "sh1",
                               {"FBA15MBX78RC000001": "0922943001",
                                "FBA15MBX78RC000002": "0922943002"})
    voce = registro.ultimo_lotto("ECM")[0]
    assert voce["spedizioni"][0]["tracking"] == {
        "FBA15MBX78RC000001": "0922943001", "FBA15MBX78RC000002": "0922943002"}


def test_registra_tracking_su_spedizione_sconosciuta_salva_lo_stesso_e_poi_avvisa(tmp_path, monkeypatch):
    """I codici sono già dichiarati ad Amazon e la spedizione è già passata a
    SHIPPED: il dato deve sopravvivere anche se non si trova dove metterlo.
    Stessa rete di `registra_etichette`."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("ECM", [RigaSpedizione("51NERO_FBA", 132)])
    registro.registra_piano("ECM", rif, "wf5545452f",
                            [Spedizione(shipment_id="sh1", centro_logistico="MXP5")])
    with pytest.raises(ValueError, match="shX"):
        registro.registra_tracking("ECM", rif, "shX", {"B1": "T1"})
    voce = registro.ultimo_lotto("ECM")[0]
    assert voce["tracking_non_collocato"]["shX"] == {"B1": "T1"}


def test_registra_tracking_aggiunge_i_codici_a_quelli_gia_scritti(tmp_path, monkeypatch):
    """Una dichiarazione completata in due riprese non deve cancellare i codici
    della prima: sono già su Amazon."""
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("ECM", [RigaSpedizione("51NERO_FBA", 132)])
    registro.registra_piano("ECM", rif, "wf5545452f",
                            [Spedizione(shipment_id="sh1", centro_logistico="MXP5")])
    registro.registra_tracking("ECM", rif, "sh1", {"B1": "T1"})
    registro.registra_tracking("ECM", rif, "sh1", {"B2": "T2"})
    assert registro.ultimo_lotto("ECM")[0]["spedizioni"][0]["tracking"] == {"B1": "T1", "B2": "T2"}


def test_venti_tracking_insieme_non_perdono_codici(tmp_path, monkeypatch):
    """Il comando tracking è nato (14/09) prima del lucchetto di DESIGN §17 ed è
    stato fuso dopo: era l'unica scrittura del registro senza. Venti
    dichiarazioni sulla stessa spedizione, con la lettura rallentata, si
    sovrascrivevano a vicenda e nel registro restava una manciata di codici
    già dichiarati ad Amazon."""
    import threading
    import time
    monkeypatch.setenv("HOME", str(tmp_path))
    rif = registro.registra_tentativo("ECM", [RigaSpedizione("51NERO_FBA", 132)])
    registro.registra_piano("ECM", rif, "wf5545452f",
                            [Spedizione(shipment_id="sh1", centro_logistico="MXP5")])
    originale = registro._carica

    def carica_lenta(percorso):
        log = originale(percorso)
        time.sleep(0.01)
        return log

    monkeypatch.setattr(registro, "_carica", carica_lenta)
    errori = []

    def uno(i):
        try:
            registro.registra_tracking("ECM", rif, "sh1", {f"B{i:02d}": f"T{i:02d}"})
        except Exception as e:      # il test deve vedere qualunque cosa
            errori.append(e)

    fili = [threading.Thread(target=uno, args=(i,)) for i in range(20)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()
    assert errori == []
    assert len(registro.ultimo_lotto("ECM")[0]["spedizioni"][0]["tracking"]) == 20
