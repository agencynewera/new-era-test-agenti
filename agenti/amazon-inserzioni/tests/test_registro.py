import os
import registro


def test_creazione_nessun_file_prima_del_primo_tentativo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert registro.ultimo_lotto("Bedshire", "pubblicazioni") == []


def test_creazione_tentativo_poi_esito(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    indice = registro.registra_tentativo_creazione("Bedshire", "SKU-1")
    assert registro.ultimo_lotto("Bedshire", "pubblicazioni")[indice]["esito"] == "in corso"
    registro.registra_esito_creazione("Bedshire", indice, "ACCEPTED", [])
    riga = registro.ultimo_lotto("Bedshire", "pubblicazioni")[indice]
    assert riga["esito"] == "ACCEPTED"
    assert riga["sku"] == "SKU-1"


def test_due_tentativi_di_creazione_non_si_sovrascrivono(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_creazione("Bedshire", "SKU-1")
    i2 = registro.registra_tentativo_creazione("Bedshire", "SKU-2")
    assert i1 != i2
    registro.registra_esito_creazione("Bedshire", i1, "ACCEPTED", [])
    registro.registra_esito_creazione("Bedshire", i2, "INVALID", [{"message": "manca il colore"}])
    righe = registro.ultimo_lotto("Bedshire", "pubblicazioni")
    assert righe[i1]["esito"] == "ACCEPTED"
    assert righe[i2]["esito"] == "INVALID"


def test_aggiornamento_tentativo_poi_esito(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    campi = {"list_price": {"prima": 29.9, "dopo": 24.9}}
    indice = registro.registra_tentativo_aggiornamento("Bedshire", "SKU-1", "EXERCISE_MAT", campi)
    riga = registro.ultimo_lotto("Bedshire", "aggiornamenti")[indice]
    assert riga["campi"] == campi
    assert riga["product_type"] == "EXERCISE_MAT"
    assert riga["esito"] == "in corso"
    registro.registra_esito_aggiornamento("Bedshire", indice, "ACCEPTED", [])
    assert registro.ultimo_lotto("Bedshire", "aggiornamenti")[indice]["esito"] == "ACCEPTED"


def test_aggiornamento_origine_annulla_o_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    campi = {"list_price": {"prima": 29.9, "dopo": 24.9}}
    i_default = registro.registra_tentativo_aggiornamento("Bedshire", "SKU-1", "EXERCISE_MAT", campi)
    i_annulla = registro.registra_tentativo_aggiornamento(
        "Bedshire", "SKU-1", "EXERCISE_MAT", campi, origine="annulla")
    righe = registro.ultimo_lotto("Bedshire", "aggiornamenti")
    assert righe[i_default]["origine"] == "aggiornamento"
    assert righe[i_annulla]["origine"] == "annulla"


def test_clienti_diversi_hanno_file_diversi(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    registro.registra_tentativo_creazione("Bedshire", "SKU-1")
    assert registro.ultimo_lotto("Tecno Cold", "pubblicazioni") == []


def test_aplus_tentativo_poi_esito(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    moduli = {"nome": "Storia del marchio", "moduli_prima": [], "moduli_dopo": [{"tipo": "STANDARD_TEXT"}]}
    indice = registro.registra_tentativo_aplus("Bedshire", "amzn1.aplus.cr.abc", "B0EXAMPLE1", moduli)
    riga = registro.ultimo_lotto("Bedshire", "contenuti-aplus")[indice]
    assert riga["content_reference_key"] == "amzn1.aplus.cr.abc"
    assert riga["asin"] == "B0EXAMPLE1"
    assert riga["moduli"] == moduli
    assert riga["esito"] == "in corso"
    registro.registra_esito_aplus("Bedshire", indice, "ACCEPTED", [])
    assert registro.ultimo_lotto("Bedshire", "contenuti-aplus")[indice]["esito"] == "ACCEPTED"


def test_aplus_non_si_mischia_con_gli_aggiornamenti_dei_listing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    registro.registra_tentativo_aplus("Bedshire", "amzn1.aplus.cr.abc", "B0EXAMPLE1", {})
    assert registro.ultimo_lotto("Bedshire", "aggiornamenti") == []
    assert registro.ultimo_lotto("Bedshire", "pubblicazioni") == []


def test_slug_cliente_e_lo_stesso_delle_credenziali():
    # crea_client risolve "Phy to Pet", "PHYTOPET" e "phy-to-pet" sullo stesso
    # prefisso: il registro deve usare la stessa classe di equivalenza.
    assert registro.slug_cliente("Phy to Pet") == "phytopet"
    assert registro.slug_cliente("PHYTOPET") == registro.slug_cliente("phy-to-pet") == "phytopet"


def test_grafie_diverse_dello_stesso_cliente_condividono_lo_stesso_registro(tmp_path, monkeypatch):
    # Collaudo 06/09/2026: col nome grezzo nel path, "PHYTOPET" apriva un
    # registro a parte (annulla non trovava nulla) e "../Phy to Pet" scriveva
    # fuori dalla cartella del registro.
    monkeypatch.setenv("HOME", str(tmp_path))
    registro.registra_tentativo_creazione("Phy to Pet", "SKU-1")
    assert len(registro.ultimo_lotto("PHYTOPET", "pubblicazioni")) == 1
    assert len(registro.ultimo_lotto("../Phy to Pet", "pubblicazioni")) == 1
    cartella = tmp_path / ".config/newera-amazon/pubblicazioni"
    file = [p.name for p in cartella.iterdir()]
    assert len(file) == 1 and file[0].startswith("phytopet-")
    assert list((tmp_path / ".config/newera-amazon").glob("Phy to Pet-*")) == []


def test_slug_cliente_e_solo_ascii_come_l_id_del_lotto():
    assert registro.slug_cliente("Café Noir") == "cafnoir"
