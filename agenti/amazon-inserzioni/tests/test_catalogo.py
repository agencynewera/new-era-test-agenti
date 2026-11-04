import json
import os
from unittest.mock import Mock, patch
import pytest
from catalogo import cerca_product_type, schema_categoria, CatalogoError


def test_cerca_product_type_estrae_i_nomi():
    client = Mock()
    client.get.return_value = {"productTypes": [{"name": "EXERCISE_MAT"}, {"name": "LUGGAGE"}]}
    risultato = cerca_product_type(client, "APJ6JRA9NG5V4", "tappeto")
    assert risultato == ["EXERCISE_MAT", "LUGGAGE"]
    client.get.assert_called_once_with(
        "/definitions/2020-09-01/productTypes",
        params={"marketplaceIds": "APJ6JRA9NG5V4", "keywords": "tappeto"},
    )


def test_schema_categoria_scarica_definizione_e_schema(tmp_path):
    client = Mock()
    client.get.return_value = {
        "schema": {"link": {"resource": "https://cache.amazon.it/schema/exercise_mat.json", "verb": "GET"}}
    }
    with open(os.path.join(os.path.dirname(__file__), "fixtures", "schema_categoria_test.json")) as f:
        schema_atteso = json.load(f)

    risposta_download = Mock()
    risposta_download.status_code = 200
    risposta_download.json.return_value = schema_atteso

    with patch("catalogo.requests.get", return_value=risposta_download) as mock_get:
        schema = schema_categoria(client, "APJ6JRA9NG5V4", "SELLER1", "EXERCISE_MAT",
                                   cache_dir=str(tmp_path))
    assert schema == schema_atteso
    assert mock_get.call_count == 1
    assert mock_get.call_args.args == ("https://cache.amazon.it/schema/exercise_mat.json",)
    assert mock_get.call_args.kwargs["timeout"] == 60
    client.get.assert_called_once_with(
        "/definitions/2020-09-01/productTypes/EXERCISE_MAT",
        params={"marketplaceIds": "APJ6JRA9NG5V4", "sellerId": "SELLER1", "requirements": "LISTING"},
    )


def test_schema_categoria_scarica_lo_schema_con_agent_nello_user_agent(tmp_path):
    """Agent Policy Amazon (BSA sez. 19): il link dello schema non passa da
    spapi_client, e senza header uscirebbe come python-requests."""
    import spapi_client
    client = Mock()
    client.get.return_value = {"schema": {"link": {"resource": "https://x/schema.json"}}}
    risposta_download = Mock(status_code=200)
    risposta_download.json.return_value = {"required": []}

    with patch("catalogo.requests.get", return_value=risposta_download) as mock_get:
        schema_categoria(client, "APJ6JRA9NG5V4", "SELLER1", "EXERCISE_MAT", cache_dir=str(tmp_path))

    user_agent = mock_get.call_args.kwargs["headers"]["User-Agent"]
    assert user_agent == spapi_client._USER_AGENT
    assert "Agent/" in user_agent


def test_schema_categoria_usa_la_cache_al_secondo_giro(tmp_path):
    client = Mock()
    client.get.return_value = {"schema": {"link": {"resource": "https://x/schema.json"}}}
    risposta_download = Mock()
    risposta_download.status_code = 200
    risposta_download.json.return_value = {"required": ["item_name"]}

    with patch("catalogo.requests.get", return_value=risposta_download):
        schema_categoria(client, "APJ6JRA9NG5V4", "SELLER1", "EXERCISE_MAT", cache_dir=str(tmp_path))
        schema_categoria(client, "APJ6JRA9NG5V4", "SELLER1", "EXERCISE_MAT", cache_dir=str(tmp_path))
    assert client.get.call_count == 1


def test_schema_categoria_senza_link_alza_catalogoerror(tmp_path):
    client = Mock()
    client.get.return_value = {"schema": {}}
    with pytest.raises(CatalogoError, match="nessuno schema disponibile"):
        schema_categoria(client, "APJ6JRA9NG5V4", "SELLER1", "SCONOSCIUTO", cache_dir=str(tmp_path))


def test_schema_categoria_download_fallito_alza_catalogoerror(tmp_path):
    client = Mock()
    client.get.return_value = {"schema": {"link": {"resource": "https://x/schema.json"}}}
    risposta_fallita = Mock()
    risposta_fallita.status_code = 404
    with patch("catalogo.requests.get", return_value=risposta_fallita):
        with pytest.raises(CatalogoError, match="404"):
            schema_categoria(client, "APJ6JRA9NG5V4", "SELLER1", "EXERCISE_MAT", cache_dir=str(tmp_path))
