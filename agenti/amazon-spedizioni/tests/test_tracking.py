from unittest.mock import Mock
import pytest
from spapi_client import SpApiError
from tracking import abbina, carica_file, da_dichiarare, dichiara, leggi_tracking

# Forma vera, letta dal vivo su ECM l'11/09/2026 (FBA15MBX78RC, 11 cartoni BRT).
def _cartone(numero: int, msku: str = "51NERO_FBA", quantita: int = 12) -> dict:
    return {"boxId": f"FBA15MBX78RC{numero:06d}",
            "items": [{"msku": msku, "quantity": quantita}]}


# ------------------------------------------------------------------ abbina --

def test_abbina_mette_i_codici_sui_cartoni_dello_stesso_sku_nell_ordine_dato():
    cartoni = [_cartone(1), _cartone(2), _cartone(3)]
    assert abbina(cartoni, {"51NERO_FBA": ["0922943001", "0922943002", "0922943003"]}) == {
        "FBA15MBX78RC000001": "0922943001",
        "FBA15MBX78RC000002": "0922943002",
        "FBA15MBX78RC000003": "0922943003",
    }


def test_abbina_tiene_separati_i_gruppi_di_sku_diversi():
    cartoni = [_cartone(1, "51NERO_FBA"), _cartone(2, "52BIANCO_FBA"), _cartone(3, "51NERO_FBA")]
    mappa = abbina(cartoni, {"51NERO_FBA": ["N1", "N2"], "52BIANCO_FBA": ["B1"]})
    assert mappa == {"FBA15MBX78RC000001": "N1", "FBA15MBX78RC000003": "N2",
                     "FBA15MBX78RC000002": "B1"}


def test_abbina_si_ferma_se_i_codici_di_uno_sku_sono_meno_dei_cartoni():
    """Il caso che capita davvero: la mail del cliente è incompleta. Senza
    questo controllo i codici scalerebbero e ogni cartone porterebbe la lettera
    di vettura di un altro."""
    cartoni = [_cartone(1), _cartone(2), _cartone(3)]
    with pytest.raises(ValueError) as e:
        abbina(cartoni, {"51NERO_FBA": ["0922943001", "0922943002"]})
    assert "51NERO_FBA" in str(e.value)
    assert "3" in str(e.value) and "2" in str(e.value)


def test_abbina_si_ferma_se_i_codici_di_uno_sku_sono_piu_dei_cartoni():
    with pytest.raises(ValueError, match="51NERO_FBA"):
        abbina([_cartone(1)], {"51NERO_FBA": ["A", "B"]})


def test_abbina_si_ferma_sui_cartoni_misti():
    """Con due SKU nello stesso cartone i gruppi del cliente non bastano più a
    decidere quale codice va dove: meglio fermarsi che indovinare."""
    misto = {"boxId": "FBA15MBX78RC000001",
             "items": [{"msku": "51NERO_FBA", "quantity": 6},
                       {"msku": "52BIANCO_FBA", "quantity": 6}]}
    with pytest.raises(ValueError) as e:
        abbina([misto], {"51NERO_FBA": ["A"], "52BIANCO_FBA": ["B"]})
    assert "FBA15MBX78RC000001" in str(e.value)
    assert "mist" in str(e.value).lower()


def test_abbina_si_ferma_sul_cartone_di_cui_amazon_non_dice_il_contenuto():
    with pytest.raises(ValueError, match="FBA15MBX78RC000001"):
        abbina([{"boxId": "FBA15MBX78RC000001", "items": []}], {"51NERO_FBA": ["A"]})


def test_abbina_si_ferma_sullo_sku_che_non_sta_in_nessun_cartone():
    """Uno SKU di troppo nella mail vuol dire che il cliente sta parlando di
    un'altra spedizione: nominarlo evita di cercare l'errore fra i codici."""
    with pytest.raises(ValueError) as e:
        abbina([_cartone(1)], {"51NERO_FBA": ["A"], "99FANTASMA_FBA": ["B"]})
    assert "99FANTASMA_FBA" in str(e.value)


def test_abbina_si_ferma_sul_cartone_di_uno_sku_che_la_mail_non_nomina():
    with pytest.raises(ValueError, match="52BIANCO_FBA"):
        abbina([_cartone(1, "51NERO_FBA"), _cartone(2, "52BIANCO_FBA")],
               {"51NERO_FBA": ["A"]})


def test_abbina_si_ferma_sul_codice_ripetuto():
    """Due cartoni con la stessa lettera di vettura sono quasi sempre un
    copia-incolla sbagliato, e Amazon li accetterebbe senza dire niente."""
    with pytest.raises(ValueError, match="0922943001"):
        abbina([_cartone(1), _cartone(2)], {"51NERO_FBA": ["0922943001", "0922943001"]})


def test_abbina_si_ferma_sul_codice_vuoto():
    with pytest.raises(ValueError):
        abbina([_cartone(1), _cartone(2)], {"51NERO_FBA": ["0922943001", "  "]})


def test_abbina_senza_cartoni_si_ferma():
    with pytest.raises(ValueError, match="nessun cartone"):
        abbina([], {"51NERO_FBA": ["A"]})


# ----------------------------------------------------------- leggi_tracking --

def _spedizione(items: list[dict], chiave: str = "spdTrackingDetail") -> dict:
    figlio = {"spdTrackingItems": items} if chiave == "spdTrackingDetail" else items
    return {"trackingDetails": {chiave: figlio}}


def test_leggi_tracking_torna_i_cartoni_col_loro_codice():
    client = Mock()
    client.get.return_value = _spedizione([
        {"boxId": "FBA15MBX78RC000001", "trackingId": "0922943001"},
        {"boxId": "FBA15MBX78RC000002", "trackingId": "0922943002"},
    ])
    assert leggi_tracking(client, "wf1", "sh1") == {
        "FBA15MBX78RC000001": "0922943001", "FBA15MBX78RC000002": "0922943002"}
    assert client.get.call_args.args[0] == "/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1"


def test_leggi_tracking_non_conta_i_cartoni_ancora_senza_codice():
    """Prima che qualcuno li dichiari Amazon elenca i cartoni col solo boxId:
    quelli non sono tracking già presenti."""
    client = Mock()
    client.get.return_value = _spedizione([
        {"boxId": "FBA15MBX78RC000001"},
        {"boxId": "FBA15MBX78RC000002", "trackingId": "0922943002"},
    ])
    assert leggi_tracking(client, "wf1", "sh1") == {"FBA15MBX78RC000002": "0922943002"}


def test_leggi_tracking_senza_dettagli_torna_vuoto():
    client = Mock()
    client.get.return_value = {"shipmentId": "sh1"}
    assert leggi_tracking(client, "wf1", "sh1") == {}


def test_leggi_tracking_su_una_spedizione_a_pallet_si_ferma():
    """`ltlTrackingDetail` ha un'altra forma (lettera di vettura e bolla) e non
    l'abbiamo mai vista dal vivo: scriverci dentro dei codici SPD sarebbe
    inventare."""
    client = Mock()
    client.get.return_value = {"trackingDetails": {"ltlTrackingDetail": {"bol": "X"}}}
    with pytest.raises(SpApiError) as e:
        leggi_tracking(client, "wf1", "sh1")
    assert "pallet" in str(e.value).lower() or "ltl" in str(e.value).lower()


# ----------------------------------------------------------------- dichiara --

def test_dichiara_manda_una_sola_put_con_tutti_i_cartoni():
    client = Mock()
    client.put.return_value = {"operationId": "ca54a5fd"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    dichiara(client, "wf1", "sh1", {"B1": "T1", "B2": "T2"})
    assert client.put.call_count == 1
    assert client.put.call_args.args[0] == (
        "/inbound/fba/2024-03-20/inboundPlans/wf1/shipments/sh1/trackingDetails")
    assert client.put.call_args.kwargs["json_body"] == {"trackingDetails": {"spdTrackingDetail": {
        "spdTrackingItems": [{"boxId": "B1", "trackingId": "T1"},
                             {"boxId": "B2", "trackingId": "T2"}]}}}
    client.attendi_operazione.assert_called_with("ca54a5fd")


def test_dichiara_su_operazione_fallita_alza_col_motivo_letterale():
    client = Mock()
    client.put.return_value = {"operationId": "op1"}
    client.attendi_operazione.return_value = {
        "operationStatus": "FAILED",
        "operationProblems": [{"message": "Box FBA1U000001 does not belong to this shipment"}]}
    with pytest.raises(SpApiError, match="does not belong"):
        dichiara(client, "wf1", "sh1", {"B1": "T1"})


def test_dichiara_se_l_attesa_cade_dice_che_l_esito_e_incerto():
    """La PUT è partita: la spedizione può essere già passata a SHIPPED. Un
    messaggio che parlasse solo di errore di lettura farebbe rifare la
    dichiarazione su una spedizione che Amazon ha già accettato."""
    client = Mock()
    client.put.return_value = {"operationId": "op1"}
    client.attendi_operazione.side_effect = SpApiError("operazione op1 non conclusa dopo 300s")
    with pytest.raises(SpApiError) as e:
        dichiara(client, "wf1", "sh1", {"B1": "T1"})
    assert "INCERTO" in str(e.value).upper()
    assert "op1" in str(e.value)


def test_dichiara_senza_cartoni_non_chiama_amazon():
    client = Mock()
    with pytest.raises(ValueError):
        dichiara(client, "wf1", "sh1", {})
    client.put.assert_not_called()


def test_abbina_torna_i_cartoni_nell_ordine_in_cui_li_elenca_amazon():
    """L'anteprima si legge accanto alla pila dei colli: se i cartoni escono
    raggruppati per SKU invece che nel loro ordine, chi controlla deve saltare
    avanti e indietro. L'ordine di Amazon è anche quello delle etichette."""
    cartoni = [_cartone(1, "51NERO_FBA"), _cartone(2, "52BIANCO_FBA"),
               _cartone(3, "51NERO_FBA"), _cartone(4, "52BIANCO_FBA")]
    mappa = abbina(cartoni, {"51NERO_FBA": ["N1", "N2"], "52BIANCO_FBA": ["B1", "B2"]})
    assert list(mappa) == ["FBA15MBX78RC000001", "FBA15MBX78RC000002",
                           "FBA15MBX78RC000003", "FBA15MBX78RC000004"]
    assert list(mappa.values()) == ["N1", "B1", "N2", "B2"]


def test_da_dichiarare_senza_cartoni_gia_tracciati_passa_tutto():
    """L'invariante che vale comunque si decida il caso "alcuni già tracciati":
    quando Amazon non ha ancora nessun codice, si dichiara tutto quello che si
    è abbinato, senza toccarlo."""
    mappa = {"FBA15MBX78RC000001": "0922943001", "FBA15MBX78RC000002": "0922943002"}
    assert da_dichiarare(mappa, {}) == mappa


# ------------------------------------------------------------- carica_file --

def test_carica_file_legge_i_codici_per_sku(tmp_path):
    f = tmp_path / "tracking.yaml"
    f.write_text("51NERO_FBA:\n  - 0922943001\n  - 0922943002\n52BIANCO_FBA:\n  - 0922943003\n")
    assert carica_file(str(f)) == {"51NERO_FBA": ["0922943001", "0922943002"],
                                   "52BIANCO_FBA": ["0922943003"]}


def test_carica_file_non_perde_lo_zero_davanti(tmp_path):
    """PyYAML legge `0123` come il numero ottale 83. Una lettera di vettura è
    testo: perdere lo zero iniziale la rende irrintracciabile, e il giro
    scriverebbe su Amazon un codice che non esiste."""
    f = tmp_path / "tracking.yaml"
    f.write_text("51NERO_FBA:\n  - 0123456\n")
    assert carica_file(str(f)) == {"51NERO_FBA": ["0123456"]}


def test_carica_file_su_un_codice_solo_senza_elenco_lo_dice(tmp_path):
    f = tmp_path / "tracking.yaml"
    f.write_text("51NERO_FBA: 0922943001\n")
    with pytest.raises(ValueError, match="51NERO_FBA"):
        carica_file(str(f))


def test_carica_file_su_yaml_rotto_alza_valueerror_col_percorso(tmp_path):
    """Un YAMLError grezzo non è né ValueError né SpApiError: sfuggirebbe al
    contratto d'errore di giro.main e uscirebbe come traceback."""
    f = tmp_path / "tracking.yaml"
    f.write_text("51NERO_FBA: [\n  - non chiuso\n")
    with pytest.raises(ValueError, match="tracking.yaml"):
        carica_file(str(f))


def test_carica_file_vuoto_lo_dice(tmp_path):
    f = tmp_path / "tracking.yaml"
    f.write_text("")
    with pytest.raises(ValueError, match="nessuna lettera di vettura"):
        carica_file(str(f))


def test_da_dichiarare_si_ferma_se_anche_un_solo_cartone_ha_gia_un_codice():
    """Scelta di Mariano del 14/09/2026 (opzione A). Dei cartoni già tracciati
    vogliono dire quasi sempre spedizione sbagliata, e su una spedizione
    sbagliata scrivere sui cartoni ancora liberi mette lettere di vettura
    errate su Amazon e la manda in SHIPPED — e non sappiamo se si corregge.
    Il caso buono (dichiarazione andata a metà) costa solo il fastidio di
    togliere quei codici dalla riga di comando."""
    mappa = {"FBA15MBX78RC000001": "0922943001", "FBA15MBX78RC000002": "0922943002"}
    with pytest.raises(ValueError) as e:
        da_dichiarare(mappa, {"FBA15MBX78RC000001": "0922943999"})
    messaggio = str(e.value)
    # Il codice già presente va detto: è così che ci si accorge che è di un'altra mail.
    assert "FBA15MBX78RC000001" in messaggio and "0922943999" in messaggio
    assert "FBA15MBX78RC000002" not in messaggio
