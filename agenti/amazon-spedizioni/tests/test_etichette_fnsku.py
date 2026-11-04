import os
import giri
import etichette_fnsku


def test_il_pdf_fnsku_va_nella_cartella_prime_delle_etichette_cartone(tmp_path, monkeypatch):
    """Deciso l'11/09/2026: finora Mariano lo spostava a mano dal Desktop."""
    monkeypatch.setenv("HOME", str(tmp_path))
    prime = tmp_path / "PRIME" / "2026-09-07 FBA1234ABCD"
    prime.mkdir(parents=True)
    giri.salva("wf1", {"etichette": {"sh1": [str(prime / "Etichette-ECM-FBA1234ABCD.pdf")]}})

    base = etichette_fnsku.destinazione("ECM", "wf1", "-x24")

    assert base == str(prime / "Etichette-FNSKU-ECM-wf1-x24")


def test_senza_etichette_cartone_il_pdf_fnsku_resta_sul_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert etichette_fnsku.destinazione("ECM", "wf1", "") == str(tmp_path / "Desktop" / "Etichette-FNSKU-ECM-wf1")


def test_una_cartella_prime_sparita_riporta_sul_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    giri.salva("wf1", {"etichette": {"sh1": [str(tmp_path / "sparita" / "Etichette-ECM-FBA1234ABCD.pdf")]}})
    assert etichette_fnsku.destinazione("ECM", "wf1", "") == str(tmp_path / "Desktop" / "Etichette-FNSKU-ECM-wf1")


def test_un_nome_cliente_con_percorso_dentro_resta_sul_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    base = etichette_fnsku.destinazione("ECM/../../", "wf1", "")
    assert os.path.dirname(base) == str(tmp_path / "Desktop")


def test_chrome_non_eredita_i_segreti_dell_ambiente(monkeypatch):
    """Collaudo del 06/09: le 49 variabili di ~/scripts/.env passavano a Chrome."""
    monkeypatch.setenv("ECM_SPAPI_LWA_CLIENT_SECRET", "segreto")
    monkeypatch.setenv("NEWERA_QUALCOSA", "riservato")
    chiamate = []
    monkeypatch.setattr(etichette_fnsku.subprocess, "run", lambda *a, **k: chiamate.append((a, k)))
    etichette_fnsku._stampa_pdf("/tmp/x/etichette")
    (argomenti,), opzioni = chiamate[0]
    assert "--print-to-pdf=/tmp/x/etichette.pdf" in argomenti and opzioni["check"] is True
    ambiente = opzioni["env"]
    assert "ECM_SPAPI_LWA_CLIENT_SECRET" not in ambiente and "NEWERA_QUALCOSA" not in ambiente
    assert ambiente["HOME"] == os.environ["HOME"] and "PATH" in ambiente


def test_titolo_lungo_tiene_la_fine_dove_sta_la_taglia():
    # Galeone 18/09/2026: 4 taglie con lo stesso inizio di titolo, la taglia in
    # coda. Tagliato solo in fondo, M e XXL uscivano identiche sullo stesso foglio.
    import etichette_fnsku
    base = ("GALEONE Slip Uomo Cotone - Set 6 Pezzi Slip | 100% Cotone Morbido Traspirante | "
            "vestibilità Regular, Intimo Uomo, Cuciture Rinforzate | Set Elastico Interno 2 Neri 2 Grigi 2 Blu | Taglia ")
    html_m = etichette_fnsku.html_fogli([("X002OXAADV", base + "M")], per_foglio=24)
    html_xxl = etichette_fnsku.html_fogli([("X002OX4YGZ", base + "XXL")], per_foglio=24)
    assert "Taglia M</div>" in html_m and "Taglia XXL</div>" in html_xxl
    assert "GALEONE Slip" in html_m and "…" in html_m


def test_titolo_corto_resta_intero():
    import etichette_fnsku
    assert "Cuscino memory 40x60</div>" in etichette_fnsku.html_fogli([("X001", "Cuscino memory 40x60")], per_foglio=24)
