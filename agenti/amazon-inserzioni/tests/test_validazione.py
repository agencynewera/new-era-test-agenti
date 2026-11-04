from modello import Prodotto
from validazione import valuta, valuta_tutti

_SCHEMA = {"required": ["item_name", "brand", "colore"]}


def _base(**override):
    dati = dict(sku="ABC-1", title="Tappeto yoga", price=29.9, quantity=10,
                product_type="EXERCISE_MAT", images=["https://x/1.jpg", "https://x/2.jpg"],
                bullet_points=["Antiscivolo"], description="Un tappeto",
                attributes={"item_name": [{"value": "Tappeto Yoga"}], "brand": [{"value": "YogaBrand"}], "colore": [{"value": "Blu"}]})
    dati.update(override)
    return Prodotto(**dati)


def test_prodotto_completo_e_verde():
    r = valuta(_base(), _SCHEMA)
    assert r.semaforo == "verde"
    assert r.mancanti_obbligatori == []
    assert r.mancanti_opzionali == []


def test_manca_un_campo_base_e_rosso():
    r = valuta(_base(price=0), _SCHEMA)
    assert r.semaforo == "rosso"
    assert "price" in r.mancanti_obbligatori


def test_manca_un_attributo_di_categoria_e_rosso():
    r = valuta(_base(attributes={}), _SCHEMA)
    assert r.semaforo == "rosso"
    assert "colore" in r.mancanti_obbligatori


def test_manca_solo_qualcosa_di_opzionale_e_giallo():
    r = valuta(_base(bullet_points=[]), _SCHEMA)
    assert r.semaforo == "giallo"
    assert "bullet_points" in r.mancanti_opzionali
    assert r.mancanti_obbligatori == []


def test_valuta_tutti_mappa_ogni_prodotto():
    rilievi = valuta_tutti([_base(), _base(sku="ABC-2", price=0)], _SCHEMA)
    assert [r.semaforo for r in rilievi] == ["verde", "rosso"]


def test_quantita_zero_non_e_mancante():
    r = valuta(_base(quantity=0), _SCHEMA)
    assert "quantity" not in r.mancanti_obbligatori
    assert r.semaforo == "verde"


# --- obbligatori dello schema coperti dai campi base del modello (smoke 2026-09-01) ---

_SCHEMA_PET_FOOD = {"required": ["brand", "bullet_point", "item_name", "product_description", "country_of_origin"]}


def test_obbligatorio_di_schema_coperto_da_un_campo_base_non_e_mancante():
    # Amazon chiama item_name/brand/bullet_point/product_description ciò che il
    # modello porta in title/brand/bullet_points/description: non vanno cercati
    # in prodotto.attributes (falsi rossi a dati completi, visto dal vivo).
    p = _base(brand="phy to pet", attributes={"country_of_origin": [{"value": "IT"}]})
    r = valuta(p, _SCHEMA_PET_FOOD)
    assert r.semaforo == "verde", r.mancanti_obbligatori
    assert r.mancanti_obbligatori == []


def test_obbligatorio_di_schema_scoperto_anche_nel_campo_base_e_rosso():
    p = _base(brand="phy to pet", bullet_points=[], attributes={"country_of_origin": [{"value": "IT"}]})
    r = valuta(p, _SCHEMA_PET_FOOD)
    assert r.semaforo == "rosso"
    assert "bullet_point" in r.mancanti_obbligatori
