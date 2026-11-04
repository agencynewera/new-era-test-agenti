# tests/test_clienti.py
import pytest
import clienti


_YAML = """
Phy to Pet:
  marketplace_id: APJ6JRA9NG5V4
  indirizzo_origine:
    name: DATO FINTO 14
    addressLine1: DATO FINTO 4
    city: GOITO
    stateOrProvinceCode: MN
    postalCode: "46044"
    countryCode: IT
    phoneNumber: "3330000000"
  contatto:
    name: DATO FINTO 14
    phoneNumber: "3330000000"
"""


@pytest.fixture
def percorso(tmp_path):
    p = tmp_path / "clienti.yaml"
    p.write_text(_YAML)
    return str(p)


def test_carica_cliente_torna_marketplace_indirizzo_e_contatto(percorso):
    c = clienti.carica_cliente("Phy to Pet", percorso)
    assert c["marketplace_id"] == "APJ6JRA9NG5V4"
    assert c["indirizzo_origine"]["addressLine1"] == "DATO FINTO 4"
    assert c["indirizzo_origine"]["postalCode"] == "46044"
    assert c["contatto"] == {"name": "DATO FINTO 14", "phoneNumber": "3330000000"}


def test_il_nome_cliente_ignora_maiuscole_e_spazi(percorso):
    """Stessa normalizzazione del prefisso di crea_client: "phy-to-pet" e
    "PHYTOPET" sono lo stesso cliente."""
    assert clienti.carica_cliente("phy-to-pet", percorso)["marketplace_id"] == "APJ6JRA9NG5V4"


def test_cliente_assente_alza_valueerror_che_nomina_il_file(percorso):
    with pytest.raises(ValueError, match="Rivextauto.*clienti.yaml"):
        clienti.carica_cliente("Rivextauto", percorso)


def test_indirizzo_senza_telefono_viene_rifiutato_prima_di_amazon(tmp_path):
    """Il 31/08 Amazon ha respinto createInboundPlan per phoneNumber vuoto:
    un giro perso per un campo che si può controllare in locale."""
    p = tmp_path / "clienti.yaml"
    p.write_text(_YAML.replace('    phoneNumber: "3330000000"\n  contatto', "  contatto"))
    with pytest.raises(ValueError, match="Phy to Pet.*phoneNumber"):
        clienti.carica_cliente("Phy to Pet", str(p))


def test_la_cartella_locale_del_cliente_si_legge_dal_file(tmp_path):
    """Il nome della cartella sul Desktop non coincide con la chiave del file
    ("Arcipelago" / "Arcipelago Gin"): va scritto, non dedotto."""
    p = tmp_path / "clienti.yaml"
    p.write_text(_YAML.replace("  marketplace_id:", "  cartella: Phy to pet\n  marketplace_id:"))
    assert clienti.carica_cliente("Phy to Pet", str(p))["cartella"] == "Phy to pet"


def test_senza_cartella_il_campo_e_none(percorso):
    assert clienti.carica_cliente("Phy to Pet", percorso)["cartella"] is None


def test_una_cartella_con_barre_viene_rifiutata(tmp_path):
    """`cartella` diventa un pezzo di percorso: un "../" porterebbe le etichette
    fuori dalle cartelle clienti."""
    p = tmp_path / "clienti.yaml"
    p.write_text(_YAML.replace("  marketplace_id:", "  cartella: ../altrove\n  marketplace_id:"))
    with pytest.raises(ValueError, match="cartella"):
        clienti.carica_cliente("Phy to Pet", str(p))
