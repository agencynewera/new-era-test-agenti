from unittest.mock import Mock, call
import pytest
from modifiche import Modifica
from applica import costruisci_patch, applica, EsitoVerifica


def test_costruisci_patch_un_replace_per_attributo():
    modifiche = [
        Modifica(campo="list_price", prima=[{"value": 29.9}], dopo=[{"value": 24.9}], fonte="manuale"),
        Modifica(campo="bullet_point", prima=[{"value": "A"}], dopo=[{"value": "A"}, {"value": "B"}], fonte="fornitore"),
    ]
    patch = costruisci_patch(modifiche)
    assert patch == [
        {"op": "replace", "path": "/attributes/list_price", "value": [{"value": 24.9}]},
        {"op": "replace", "path": "/attributes/bullet_point", "value": [{"value": "A"}, {"value": "B"}]},
    ]


def test_costruisci_patch_dopo_vuoto_da_annulla_diventa_delete():
    modifiche = [Modifica(campo="generic_keyword", prima=[{"value": "x"}], dopo=None, fonte="annulla")]
    # Il delete DEVE portare il valore da cancellare: senza, Amazon risponde
    # 400 "Invalid empty value provided in patch" (smoke 2026-09-02).
    assert costruisci_patch(modifiche) == [
        {"op": "delete", "path": "/attributes/generic_keyword", "value": [{"value": "x"}]}
    ]


def test_costruisci_patch_dopo_vuoto_da_altra_fonte_alza_valueerror():
    # La guardia anti-svuotamento: solo l'annulla può riportare un campo ad "assente".
    modifiche = [Modifica(campo="generic_keyword", prima=[{"value": "x"}], dopo=None, fonte="fornitore")]
    with pytest.raises(ValueError, match="dopo vuoto"):
        costruisci_patch(modifiche)


def test_applica_delete_usa_il_valore_vivo_non_quello_registrato(tmp_path, monkeypatch):
    # Scoperto dal vivo (smoke 2026-09-02): il delete passa solo se il value
    # combacia con l'attributo VIVO (Amazon arricchisce, es. language_tag);
    # col valore registrato risponde "errore interno" 4000000. Quindi applica
    # rilegge prima di costruire un patch che contiene delete.
    monkeypatch.setenv("HOME", str(tmp_path))
    vivo = [{"value": "x", "language_tag": "it_IT"}]
    client = Mock()
    client.get.return_value = {"attributes": {"generic_keyword": vivo}, "summaries": []}
    client.patch.return_value = {"status": "ACCEPTED", "issues": []}
    modifiche = [Modifica(campo="generic_keyword", prima=[{"value": "x"}], dopo=None, fonte="annulla")]

    applica(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire", "ABC-1", "EXERCISE_MAT", modifiche)

    patch_inviato = client.patch.call_args.kwargs["json_body"]["patches"]
    assert patch_inviato == [{"op": "delete", "path": "/attributes/generic_keyword", "value": vivo}]


def test_applica_niente_modifiche_alza_valueerror():
    with pytest.raises(ValueError, match="nessuna modifica"):
        applica(Mock(), "SELLER1", "APJ6JRA9NG5V4", "Bedshire", "ABC-1", "EXERCISE_MAT", [])


def test_applica_registra_prima_di_scrivere_e_rilegge_se_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.patch.return_value = {"status": "ACCEPTED", "issues": []}
    client.get.return_value = {"attributes": {"list_price": [{"value": 24.9}]}, "summaries": []}
    modifiche = [Modifica(campo="list_price", prima=[{"value": 29.9}], dopo=[{"value": 24.9}], fonte="manuale")]

    esito, issues, verifiche = applica(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire",
                                        "ABC-1", "EXERCISE_MAT", modifiche)

    assert esito == "ACCEPTED"
    assert issues == []
    assert verifiche == [EsitoVerifica(campo="list_price", stato="applicato",
                                        atteso=[{"value": 24.9}], trovato=[{"value": 24.9}])]
    # il tentativo è registrato PRIMA della chiamata patch
    import registro
    riga = registro.ultimo_lotto("Bedshire", "aggiornamenti")[0]
    assert riga["campi"]["list_price"] == {"prima": [{"value": 29.9}], "dopo": [{"value": 24.9}]}
    assert riga["esito"] == "ACCEPTED"


def test_applica_marca_applicato_diversamente_quando_amazon_normalizza(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.patch.return_value = {"status": "ACCEPTED", "issues": []}
    client.get.return_value = {"attributes": {"bullet_point": [{"value": "Antiscivolo (TPE)"}]}, "summaries": []}
    modifiche = [Modifica(campo="bullet_point", prima=[{"value": "vecchio"}],
                           dopo=[{"value": "Antiscivolo"}], fonte="manuale")]
    _, _, verifiche = applica(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire",
                               "ABC-1", "EXERCISE_MAT", modifiche)
    assert verifiche[0].stato == "applicato diversamente"


def test_applica_marca_non_applicato_quando_amazon_non_cambia_nulla(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.patch.return_value = {"status": "ACCEPTED", "issues": []}
    client.get.return_value = {"attributes": {"list_price": [{"value": 29.9}]}, "summaries": []}
    modifiche = [Modifica(campo="list_price", prima=[{"value": 29.9}], dopo=[{"value": 24.9}], fonte="manuale")]
    _, _, verifiche = applica(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire",
                               "ABC-1", "EXERCISE_MAT", modifiche)
    assert verifiche[0].stato == "non applicato"


def test_applica_non_rilegge_se_invalid(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.patch.return_value = {"status": "INVALID", "issues": [{"message": "prezzo negativo"}]}
    modifiche = [Modifica(campo="list_price", prima=[{"value": 29.9}], dopo=[{"value": -1}], fonte="manuale")]
    esito, issues, verifiche = applica(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire",
                                        "ABC-1", "EXERCISE_MAT", modifiche)
    assert esito == "INVALID"
    assert verifiche == []
    client.get.assert_not_called()


def test_applica_codifica_la_barra_nello_sku(tmp_path, monkeypatch):
    # Stessa trappola di letto.leggi, ma su una PATCH: qui il 403 fermava un
    # aggiornamento gia' autorizzato da Mariano (Tecno Cold, 2026-09-03).
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"attributes": {}, "summaries": []}
    client.patch.return_value = {"status": "ACCEPTED", "issues": []}
    modifiche = [Modifica(campo="list_price", prima=[{"value": 29.9}], dopo=[{"value": 24.9}], fonte="manuale")]

    applica(client, "SELLER1", "APJ6JRA9NG5V4", "Tecno Cold", "MA1/4SX-R", "EXERCISE_MAT", modifiche)

    assert client.patch.call_args.args[0] == "/listings/2021-08-01/items/SELLER1/MA1%2F4SX-R"


def test_applica_su_errore_di_trasporto_registra_l_esito_e_rialza(tmp_path, monkeypatch):
    # Se la PATCH cade a meta' (rete, 500, token), il registro non deve
    # restare su "in corso": annulla e le verifiche leggono di li'.
    import json, os
    from datetime import datetime, timezone
    from spapi_client import SpApiError
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.patch.side_effect = SpApiError("500 su /listings: gateway")
    modifiche = [Modifica(campo="list_price", prima=[{"value": 1}], dopo=[{"value": 2}], fonte="manuale")]

    with pytest.raises(SpApiError, match="500"):
        applica(client, "SELLER1", "APJ6JRA9NG5V4", "Cliente", "SKU-1", "PT", modifiche)

    data = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    righe = json.load(open(os.path.join(str(tmp_path), ".config/newera-amazon/aggiornamenti", f"cliente-{data}.json")))
    assert righe[0]["esito"] == "ERRORE TRASPORTO"
    assert "500" in righe[0]["issues"][0]["message"]


def test_applica_su_rilettura_fallita_dopo_accepted_torna_accepted_e_non_alza(tmp_path, monkeypatch):
    # Collaudo 06/09/2026: la PATCH era ACCEPTED e registrata, ma la rilettura
    # immediata falliva e l'eccezione faceva dire a vai "saltato, errore di
    # trasporto" su uno SKU in realta' scritto. Il riassunto diceva il
    # contrario del vero.
    from spapi_client import SpApiError
    import registro
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.patch.return_value = {"status": "ACCEPTED", "issues": []}
    client.get.side_effect = SpApiError("503 su /listings: gateway")
    modifiche = [Modifica(campo="list_price", prima=[{"value": 29.9}], dopo=[{"value": 24.9}], fonte="manuale")]

    esito, issues, verifiche = applica(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire",
                                        "ABC-1", "EXERCISE_MAT", modifiche)

    assert esito == "ACCEPTED" and issues == []
    assert verifiche[0].campo == "list_price" and verifiche[0].stato.startswith("non verificabile")
    assert verifiche[0].trovato is None
    assert registro.ultimo_lotto("Bedshire", "aggiornamenti")[0]["esito"] == "ACCEPTED"


def test_applica_con_dopo_vuoto_non_lascia_una_riga_in_corso_nel_registro(tmp_path, monkeypatch):
    # Collaudo 06/09/2026: il tentativo veniva registrato PRIMA di costruire
    # la patch, cosi' la guardia anti-svuotamento alzava ValueError lasciando
    # il registro su "in corso" senza che nulla fosse stato scritto.
    import registro
    monkeypatch.setenv("HOME", str(tmp_path))
    client = Mock()
    client.get.return_value = {"attributes": {"generic_keyword": [{"value": "x"}]}, "summaries": []}
    modifiche = [Modifica(campo="generic_keyword", prima=[{"value": "x"}], dopo=None, fonte="chat")]

    with pytest.raises(ValueError, match="dopo vuoto"):
        applica(client, "SELLER1", "APJ6JRA9NG5V4", "Bedshire", "ABC-1", "PT", modifiche)

    assert registro.ultimo_lotto("Bedshire", "aggiornamenti") == []
    client.patch.assert_not_called()
