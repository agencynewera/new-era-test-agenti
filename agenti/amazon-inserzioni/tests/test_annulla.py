from unittest.mock import patch
import registro
from annulla import annulla_ultimo_lotto, pianifica_annullamento


def test_annulla_usa_solo_lultima_riga_per_sku(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT", {"list_price": {"prima": [{"value": 29.9}], "dopo": [{"value": 24.9}]}})
    registro.registra_esito_aggiornamento("Bedshire", i1, "ACCEPTED", [])
    i2 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT", {"list_price": {"prima": [{"value": 24.9}], "dopo": [{"value": 19.9}]}})
    registro.registra_esito_aggiornamento("Bedshire", i2, "ACCEPTED", [])

    with patch("annulla.applica") as mock_applica:
        mock_applica.return_value = ("ACCEPTED", [], [])
        annulla_ultimo_lotto(object(), "SELLER1", "APJ6JRA9NG5V4", "Bedshire")

    modifiche_passate = mock_applica.call_args.args[6]
    assert len(modifiche_passate) == 1
    m = modifiche_passate[0]
    # si torna al 'prima' della SECONDA riga (24.9), non della prima (29.9)
    assert m.dopo == [{"value": 24.9}]
    assert m.prima == [{"value": 19.9}]


def test_annulla_ignora_righe_non_accettate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT", {"list_price": {"prima": [{"value": 29.9}], "dopo": [{"value": -1}]}})
    registro.registra_esito_aggiornamento("Bedshire", i1, "INVALID", [{"message": "prezzo negativo"}])

    with patch("annulla.applica") as mock_applica:
        esiti = annulla_ultimo_lotto(object(), "SELLER1", "APJ6JRA9NG5V4", "Bedshire")

    mock_applica.assert_not_called()
    assert esiti == {}


def test_annulla_un_campo_aggiunto_dal_nulla_lo_cancella(tmp_path, monkeypatch):
    # Scoperto dal vivo (smoke 2026-09-02): se l'aggiornamento ha AGGIUNTO un
    # campo che prima non c'era, annullarlo significa cancellarlo. Il vecchio
    # comportamento (scartare i 'prima' vuoti) rendeva l'aggiunta inannullabile.
    # La guardia anti-svuotamento resta per gli aggiornamenti normali: vive in
    # modifiche.componi e in costruisci_patch (fonte != "annulla").
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT",
        {"generic_keyword": {"prima": None, "dopo": [{"value": "tappeto yoga"}]}})
    registro.registra_esito_aggiornamento("Bedshire", i1, "ACCEPTED", [])

    with patch("annulla.applica") as mock_applica:
        mock_applica.return_value = ("ACCEPTED", [], [])
        annulla_ultimo_lotto(object(), "SELLER1", "APJ6JRA9NG5V4", "Bedshire")

    modifiche_passate = mock_applica.call_args.args[6]
    assert len(modifiche_passate) == 1
    m = modifiche_passate[0]
    assert m.prima == [{"value": "tappeto yoga"}]
    assert m.dopo is None  # ripristino ad "assente": costruisci_patch lo traduce in op delete
    assert m.fonte == "annulla"


def test_annulla_passa_dallo_stesso_cancello(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT", {"list_price": {"prima": [{"value": 29.9}], "dopo": [{"value": 24.9}]}})
    registro.registra_esito_aggiornamento("Bedshire", i1, "ACCEPTED", [])

    with patch("annulla.applica") as mock_applica:
        mock_applica.return_value = ("ACCEPTED", [], [])
        esiti = annulla_ultimo_lotto("client-finto", "SELLER1", "APJ6JRA9NG5V4", "Bedshire")

    mock_applica.assert_called_once()
    args = mock_applica.call_args.args
    assert args[:6] == ("client-finto", "SELLER1", "APJ6JRA9NG5V4", "Bedshire", "ABC-1", "EXERCISE_MAT")
    assert mock_applica.call_args.kwargs == {"origine": "annulla"}
    assert esiti == {"ABC-1": ("ACCEPTED", [])}


def test_pianifica_annullamento_non_scrive_nulla(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT", {"list_price": {"prima": [{"value": 29.9}], "dopo": [{"value": 24.9}]}})
    registro.registra_esito_aggiornamento("Bedshire", i1, "ACCEPTED", [])

    with patch("annulla.applica") as mock_applica:
        piano = pianifica_annullamento("Bedshire")

    mock_applica.assert_not_called()
    assert list(piano.keys()) == ["ABC-1"]
    product_type, modifiche = piano["ABC-1"]
    assert product_type == "EXERCISE_MAT"
    assert len(modifiche) == 1
    assert modifiche[0].dopo == [{"value": 29.9}]
    assert modifiche[0].prima == [{"value": 24.9}]


def test_un_secondo_annulla_non_riannulla_il_ripristino_appena_fatto(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    i1 = registro.registra_tentativo_aggiornamento(
        "Bedshire", "ABC-1", "EXERCISE_MAT", {"list_price": {"prima": [{"value": 29.9}], "dopo": [{"value": 24.9}]}})
    registro.registra_esito_aggiornamento("Bedshire", i1, "ACCEPTED", [])

    def _simula_ripristino(*args, **kwargs):
        # Mima cio' che il vero applica() farebbe: registra una riga con
        # origine="annulla" e prima/dopo invertiti rispetto all'originale.
        i = registro.registra_tentativo_aggiornamento(
            "Bedshire", "ABC-1", "EXERCISE_MAT",
            {"list_price": {"prima": [{"value": 24.9}], "dopo": [{"value": 29.9}]}},
            origine="annulla")
        registro.registra_esito_aggiornamento("Bedshire", i, "ACCEPTED", [])
        return ("ACCEPTED", [], [])

    with patch("annulla.applica", side_effect=_simula_ripristino) as mock_applica:
        esiti_primo = annulla_ultimo_lotto("client-finto", "SELLER1", "APJ6JRA9NG5V4", "Bedshire")
    assert mock_applica.call_count == 1
    assert esiti_primo == {"ABC-1": ("ACCEPTED", [])}

    with patch("annulla.applica") as mock_applica_secondo:
        esiti_secondo = annulla_ultimo_lotto("client-finto", "SELLER1", "APJ6JRA9NG5V4", "Bedshire")

    mock_applica_secondo.assert_not_called()
    assert esiti_secondo == {}
