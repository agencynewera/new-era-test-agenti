from modello import Prodotto


def test_prodotto_minimo():
    p = Prodotto(sku="ABC-1", title="Tappeto yoga", price=29.9, quantity=10,
                 product_type="EXERCISE_MAT")
    assert p.ean == ""
    assert p.images == []
    assert p.bullet_points == []
    assert p.attributes == {}
    assert p.condition == "New"
    assert p.weight is None


def test_prodotto_completo():
    p = Prodotto(
        sku="ABC-1", title="Tappeto yoga", price=29.9, quantity=10,
        product_type="EXERCISE_MAT", ean="8001234567890", brand="New Era Test",
        description="Tappeto in TPE antiscivolo", category="Sport",
        images=["https://x/1.jpg", "https://x/2.jpg"], condition="New",
        bullet_points=["Antiscivolo", "Ecologico"],
        attributes={"colore": [{"value": "Blu", "marketplace_id": "APJ6JRA9NG5V4"}]},
        weight=1.2, dimensions={"lunghezza": 183, "larghezza": 61}, mpn="TYM-001",
    )
    assert p.brand == "New Era Test"
    assert len(p.images) == 2
    assert p.attributes["colore"][0]["value"] == "Blu"


def test_due_prodotti_con_liste_di_default_non_condividono_stato():
    a = Prodotto(sku="A", title="A", price=1, quantity=1, product_type="X")
    b = Prodotto(sku="B", title="B", price=1, quantity=1, product_type="X")
    a.images.append("https://x/1.jpg")
    assert b.images == []
