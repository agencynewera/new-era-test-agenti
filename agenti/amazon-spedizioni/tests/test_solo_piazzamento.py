from unittest.mock import Mock
import pytest
import etichette
import giri
import registro
import solo_piazzamento
import stadio
from cliente_finto import ClienteFinto
from modello import RigaSpedizione, Scatola

_CLIENTE = "Phy to Pet"


def _pdf_finto(monkeypatch):
    pdf = Mock(status_code=200, content=b"%PDF-1.4 finto")
    monkeypatch.setattr(etichette.requests, "get", lambda url, **_: pdf)


def _pronto(c):
    """Piano allo stadio PIAZZAMENTO_DA_SCEGLIERE con la voce di registro e il
    file locale che solo_piazzamento.conferma si aspetta (imita _pronto di
    tests/test_giro.py)."""
    plan_id = c.piano_a(stadio.PIAZZAMENTO_DA_SCEGLIERE)
    rif = registro.registra_tentativo(_CLIENTE, [{"sku": "SKU-1", "quantita": 30}])
    registro.registra_piano(_CLIENTE, rif, plan_id, [])
    giri.salva(plan_id, {"cliente": _CLIENTE, "riferimento": rif, "lotto": {},
                         "scelte": {}, "etichette": {}, "bloccato": None})
    return plan_id


def test_conferma_vai_prende_il_lucchetto_del_piano(monkeypatch, tmp_path):
    """Revisione finale dell'11/09: anche questa CLI spende (conferma_piazzamento) e deve escludersi da giro.py conferma --vai sullo stesso piano (DESIGN §17)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _pdf_finto(monkeypatch)
    c = ClienteFinto(scadenza_piazzamento="2099-01-01T00:00:00.000Z")
    plan_id = _pronto(c)
    with giri.lucchetto(plan_id):          # "l'altra" conferma, ancora in corso
        with pytest.raises(ValueError, match="in corso"):
            solo_piazzamento.conferma(c, plan_id, "pl1", True, "PackageLabel_A4_4")
        assert c.post_fatte == []
        testo = solo_piazzamento.conferma(c, plan_id, "pl1", False, "PackageLabel_A4_4")
        assert isinstance(testo, str)
        assert c.post_fatte == []


class _GruppiDivisi(ClienteFinto):
    """Come in tests/test_giro.py (ECM 15/09/2026, opzione po528df6af): lo
    stesso SKU in due gruppi di imballaggio, con pezzi diversi in ciascuno."""
    GRUPPI = {"pg1": {"Reladol_300": 10}, "pg2": {"Reladol_300": 12}}

    def piano_a(self, stadio_, plan_id="wf1"):
        pid = super().piano_a(stadio_, plan_id)
        self.piani[pid]["packing"]["packingGroups"] = list(self.GRUPPI)
        return pid

    def get(self, path, params=None):
        for gruppo, articoli in self.GRUPPI.items():
            if path.endswith(f"/packingGroups/{gruppo}/items"):
                return {"items": [{"msku": sku, "quantity": q} for sku, q in articoli.items()]}
        return super().get(path, params)


def test_uno_sku_diviso_fra_due_gruppi_ferma_anche_solo_piazzamento(monkeypatch, tmp_path):
    """La mappa locale di _fino_al_menu teneva l'ultimo gruppo letto e avrebbe
    dichiarato un contenuto sbagliato: ora passa da giro._assegna_scatole, la
    stessa guardia del giro (ECM 15/09/2026)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    c = _GruppiDivisi()
    plan_id = c.piano_a(stadio.SCATOLE_DA_DICHIARARE)
    rif = registro.registra_tentativo("ECM", [{"sku": "Reladol_300", "quantita": 22}])
    registro.registra_piano("ECM", rif, plan_id, [])
    scatole = [Scatola(quantita=22, peso_kg=8, lunghezza_cm=40, larghezza_cm=30, altezza_cm=25,
                       righe=[RigaSpedizione("Reladol_300", 1)])]
    with pytest.raises(ValueError, match="divide gli stessi SKU"):
        solo_piazzamento._fino_al_menu(c, plan_id, "ECM", rif, scatole,
                                       {"marketplace_id": "APJ6JRA9NG5V4"})
    assert not any(p.endswith("/packingInformation") for p, _ in c.post_fatte)
