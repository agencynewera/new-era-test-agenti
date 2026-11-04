import os
from unittest.mock import Mock
from datetime import date, datetime, timedelta, timezone
import pytest
import giri
import giro
import lotto as lotto_mod
import registro
import stadio
from cliente_finto import ClienteFinto

_ADESSO = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)
_CFG = {"marketplace_id": "APJ6JRA9NG5V4",
        "indirizzo_origine": {"name": "Phy to Pet", "addressLine1": "Via Roma 1", "city": "Prato",
                              "postalCode": "59100", "countryCode": "IT", "phoneNumber": "1"},
        "contatto": {"name": "Phy to Pet", "phoneNumber": "1"}}


def _lotto(pallet=False):
    dati = {"cliente": "Phy to Pet",
            "righe": [{"sku": "SKU-1", "quantita": 30}],
            "scatole": [{"quantita": 1, "peso_kg": 8, "lunghezza_cm": 40, "larghezza_cm": 30, "altezza_cm": 25,
                         "righe": [{"sku": "SKU-1", "quantita": 30}]}],
            "data_pronta": "2026-09-05T08:00Z"}
    if pallet:
        dati["pallet"] = [{"quantity": 1, "stackability": "STACKABLE", "peso_kg": 100,
                           "lunghezza_cm": 120, "larghezza_cm": 80, "altezza_cm": 100}]
    return lotto_mod.da_dizionario(dati)


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(giro.clienti, "carica_cliente", lambda nome, percorso=None: _CFG)


def _voce(plan_id):
    locale = giri.carica(plan_id)
    return registro.voce(locale["cliente"], locale["riferimento"])


def _post_che_spendono(c):
    """Le tre POST che spendono davvero (SKILL.md §14-16): conferma di UNA
    divisione di piazzamento, conferma del trasporto, conferma di una finestra
    di consegna. La conferma dell'imballaggio finisce anch'essa in
    "/confirmation" ma è gratis e lecita prima del "vai" — va esclusa qui."""
    return [p for p, _ in c.post_fatte if p.endswith("/confirmation") and "/packingOptions/" not in p]


def test_prepara_arriva_al_menu_senza_spendere():
    c = ClienteFinto()
    plan_id, testo = giro.prepara(c, _lotto(), adesso=_ADESSO)
    assert plan_id == "wf1"
    assert stadio.riconosci(stadio.leggi(c, plan_id), _ADESSO) == stadio.PIAZZAMENTO_DA_SCEGLIERE
    assert "pl1" in testo and "pl2" in testo               # tutte le divisioni
    assert "0.00 EUR" in testo and "4.00 EUR" in testo     # con la commissione di piazzamento
    assert "UPS" in testo and "7.90 EUR" in testo          # e i preventivi corriere
    assert "corriere proprio" in testo
    assert "dw1" in testo                                  # finestre del corriere proprio già nel menù
    assert _post_che_spendono(c) == []
    locale = giri.carica(plan_id)
    assert locale["cliente"] == "Phy to Pet" and locale["lotto"]["data_pronta"] == "2026-09-05T08:00Z"
    assert _voce(plan_id)["inbound_plan_id"] == "wf1"


def test_prepara_manda_contatto_e_pallet_ai_preventivi():
    c = ClienteFinto()
    giro.prepara(c, _lotto(pallet=True), adesso=_ADESSO)
    corpo = next(b for p, b in c.post_fatte if p.endswith("/transportationOptions"))
    conf = corpo["shipmentTransportationConfigurations"][0]
    assert conf["contactInformation"] == _CFG["contatto"]
    assert conf["pallets"][0]["stackability"] == "STACKABLE"
    assert conf["readyToShipWindow"] == {"start": "2026-09-05T08:00Z"}


def test_prepara_registra_l_errore_di_crea_piano_e_lo_rialza():
    from spapi_client import SpApiError
    class Rotto(ClienteFinto):
        def post(self, path, json_body=None, params=None):
            raise SpApiError("400 su /inboundPlans: WARNING: Address was unable to be verified")
    with pytest.raises(SpApiError, match="Address"):
        giro.prepara(Rotto(), _lotto(), adesso=_ADESSO)
    assert registro.ultimo_lotto("Phy to Pet")[-1]["esito"].startswith("errore in crea_piano: 400")


def test_riprendi_registra_l_errore_e_un_esito_incerto_blocca_il_piano():
    from spapi_client import SpApiError

    class RottoInPiazzamento(ClienteFinto):
        def __init__(self, messaggio, **kwargs):
            super().__init__(**kwargs)
            self._messaggio = messaggio

        def post(self, path, json_body=None, params=None):
            if path.endswith("/placementOptions"):
                raise SpApiError(self._messaggio)
            return super().post(path, json_body, params)

    # caso 1: esito incerto — il piano resta bloccato finché non è verificato
    c = RottoInPiazzamento("ESITO INCERTO sulla generazione: rete caduta")
    plan_id = c.piano_a(stadio.PIAZZAMENTO_DA_GENERARE)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    with pytest.raises(SpApiError, match="ESITO INCERTO"):
        giro.riprendi(c, plan_id, adesso=_ADESSO)
    assert giri.carica(plan_id)["bloccato"].startswith("ESITO INCERTO")
    assert _voce(plan_id)["esito"].startswith("ESITO INCERTO")

    # caso 2: errore ordinario — si registra l'esito ma non si blocca il piano
    c2 = RottoInPiazzamento("400 su /placementOptions: BadRequest")
    plan_id2 = c2.piano_a(stadio.PIAZZAMENTO_DA_GENERARE, plan_id="wf2")
    giri.salva(plan_id2, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                          "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    with pytest.raises(SpApiError, match="400 su"):
        giro.riprendi(c2, plan_id2, adesso=_ADESSO)
    assert _voce(plan_id2)["esito"].startswith("400 su")
    assert giri.carica(plan_id2)["bloccato"] is None


@pytest.mark.parametrize("da", [stadio.IMBALLAGGIO_DA_CONFERMARE, stadio.SCATOLE_DA_DICHIARARE,
                                stadio.PIAZZAMENTO_DA_GENERARE, stadio.PIAZZAMENTO_DA_SCEGLIERE])
def test_riprendi_da_ogni_stadio_prima_del_cancello_arriva_al_menu(da):
    c = ClienteFinto()
    plan_id = c.piano_a(da)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    testo = giro.riprendi(c, plan_id, adesso=_ADESSO)
    assert "UPS" in testo and "7.90 EUR" in testo
    assert stadio.riconosci(stadio.leggi(c, plan_id), _ADESSO) == stadio.PIAZZAMENTO_DA_SCEGLIERE
    assert _post_che_spendono(c) == []


def test_riprendi_rigenera_i_piazzamenti_scaduti_senza_ridichiarare_le_scatole():
    c = ClienteFinto(scadenza_piazzamento="2026-09-01T00:00:00.000Z")
    plan_id = c.piano_a(stadio.PIAZZAMENTO_DA_SCEGLIERE)   # con quella scadenza è DA_GENERARE
    c.scadenza_piazzamento = "2026-09-10T00:00:00.000Z"     # le nuove opzioni saranno valide
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    giro.riprendi(c, plan_id, adesso=_ADESSO)
    percorsi = [p for p, _ in c.post_fatte]
    assert percorsi.count(f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/placementOptions") == 1
    assert not any(p.endswith("/packingInformation") for p in percorsi)


def test_riprendi_senza_file_locale_pretende_il_lotto():
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.SCATOLE_DA_DICHIARARE)
    with pytest.raises(ValueError, match="nessun file locale.*--lotto"):
        giro.riprendi(c, plan_id, adesso=_ADESSO)
    testo = giro.riprendi(c, plan_id, lotto=_lotto(), adesso=_ADESSO)
    assert "UPS" in testo
    assert giri.carica(plan_id)["riferimento"]


def test_riprendi_con_piu_opzioni_di_imballaggio_si_ferma_e_le_elenca():
    class DueOpzioni(ClienteFinto):
        def get(self, path, params=None):
            r = super().get(path, params)
            if path.endswith("/packingOptions") and r["packingOptions"]:
                r["packingOptions"] = r["packingOptions"] + [{"packingOptionId": "po2", "status": "OFFERED", "packingGroups": ["pg1"]}]
            return r
    c = DueOpzioni()
    plan_id = c.piano_a(stadio.IMBALLAGGIO_DA_CONFERMARE)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    with pytest.raises(ValueError, match="po1.*po2.*--imballaggio"):
        giro.riprendi(c, plan_id, adesso=_ADESSO)
    assert "UPS" in giro.riprendi(c, plan_id, packing_option_id="po1", adesso=_ADESSO)


def test_riprendi_bloccato_da_esito_incerto_rifiuta_senza_verificato():
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.PIAZZAMENTO_DA_SCEGLIERE)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {},
                         "bloccato": "ESITO INCERTO sulla conferma del piazzamento pl1"})
    with pytest.raises(ValueError, match="ESITO INCERTO.*--verificato"):
        giro.riprendi(c, plan_id, adesso=_ADESSO)
    giro.riprendi(c, plan_id, verificato=True, adesso=_ADESSO)
    assert giri.carica(plan_id)["bloccato"] is None


def test_riprendi_a_trasporto_prenotato_lo_dice_e_non_scrive():
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.TRASPORTO_PRENOTATO)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    testo = giro.riprendi(c, plan_id, adesso=_ADESSO)
    assert "già prenotato" in testo and "conferma" in testo
    assert c.post_fatte == []


def _pronto(c, da=stadio.PIAZZAMENTO_DA_SCEGLIERE):
    plan_id = c.piano_a(da)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    registro.registra_piano("Phy to Pet", giri.carica(plan_id)["riferimento"], plan_id, [])
    giro.riprendi(c, plan_id, adesso=_ADESSO)      # genera i preventivi
    c.post_fatte.clear()
    return plan_id


def test_conferma_senza_vai_mostra_il_cancello_e_non_fa_nessuna_post():
    c = ClienteFinto()
    plan_id = _pronto(c)
    scelte_prima = giri.carica(plan_id)["scelte"]
    testo = giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, adesso=_ADESSO)
    assert "Sto per prenotare" in testo and "UPS" in testo and "7.90 EUR" in testo
    assert "commissione di piazzamento" in testo.lower()
    assert '"vai"' in testo
    assert c.post_fatte == []
    voce = _voce(plan_id)
    assert "piazzamento_confermato" not in voce
    assert voce["esito"] == "in corso"
    assert giri.carica(plan_id)["scelte"] == scelte_prima


def test_conferma_con_vai_spende_nell_ordine_di_skill_md_e_scarica_le_etichette(monkeypatch):
    from unittest.mock import Mock
    pdf = Mock(status_code=200, content=b"%PDF-1.4 finto")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)
    c = ClienteFinto()
    plan_id = _pronto(c)
    testo = giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    percorsi = [p for p, _ in c.post_fatte]
    assert percorsi == [f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/placementOptions/pl1/confirmation",
                        f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/transportationOptions/confirmation"]
    voce = _voce(plan_id)
    assert voce["piazzamento_confermato"]["placement_option_id"] == "pl1"
    assert voce["spedizioni"][0]["shipment_confirmation_id"] == "FBA1234sh1"
    assert voce["spedizioni"][0]["trasporto_confermato"]["transportation_option_id"] == "to-ups-sh1"
    assert voce["spedizioni"][0]["etichette_path"].endswith("Etichette-Phy to Pet-FBA1234sh1.pdf")
    assert voce["esito"] == "completato"
    assert giri.carica(plan_id)["etichette"]["sh1"][0].endswith("FBA1234sh1.pdf")
    assert "FBA1234sh1" in testo and "Etichette" in testo
    assert stadio.riconosci(stadio.leggi(c, plan_id), _ADESSO, etichette_scaricate=True) == stadio.COMPLETATO


def test_due_conferme_con_vai_sullo_stesso_piano_non_passano_insieme(monkeypatch):
    """Collaudo del 06/09: due `conferma --vai` insieme leggevano la stessa
    foto e prenotavano entrambe (nessuna chiave di idempotenza). La seconda
    si ferma prima di qualunque POST; senza --vai il cancello si legge lo stesso."""
    _pdf_finto(monkeypatch)
    c = ClienteFinto()
    plan_id = _pronto(c)
    c.get_fatte.clear()
    with giri.lucchetto(plan_id):          # "l'altra" conferma, ancora in corso
        with pytest.raises(ValueError, match="in corso"):
            giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
        assert c.post_fatte == []
        assert c.get_fatte == []   # il lucchetto viene prima della foto, non solo prima delle POST
        assert "Sto per prenotare" in giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, adesso=_ADESSO)


def test_conferma_registra_la_commissione_prima_di_qualunque_altra_lettura(monkeypatch):
    """Passo 13 di SKILL.md: la commissione è già spesa quando si rileggono
    le spedizioni; se la rilettura fallisce, la riga della spesa deve esserci."""
    from spapi_client import SpApiError
    class RiletturaRotta(ClienteFinto):
        def get(self, path, params=None):
            p = self.piani.get("wf1", {})
            if path.endswith("/shipments/sh1") and p.get("spedizioni", {}).get("sh1", {}).get("status") == "WORKING":
                raise SpApiError("errore di rete su GET dopo 5 tentativi")
            return super().get(path, params)
    c = RiletturaRotta()
    plan_id = _pronto(c)
    with pytest.raises(SpApiError, match="errore di rete"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    voce = _voce(plan_id)
    assert voce["piazzamento_confermato"]["placement_option_id"] == "pl1"
    assert voce["esito"].startswith("errore di rete")


def test_conferma_del_corriere_proprio_pretende_la_finestra_e_la_conferma_prima(monkeypatch):
    pdf = __import__("unittest.mock").mock.Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)
    c = ClienteFinto()
    plan_id = _pronto(c)
    with pytest.raises(ValueError, match="finestra di consegna.*sh1.*--finestra"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-proprio-sh1"}, adesso=_ADESSO)
    giro.conferma(c, plan_id, "pl1", {"sh1": "to-proprio-sh1"}, finestre={"sh1": "dw1"}, vai=True, adesso=_ADESSO)
    percorsi = [p for p, _ in c.post_fatte]
    i_finestra = next(i for i, p in enumerate(percorsi) if p.endswith("/deliveryWindowOptions/dw1/confirmation"))
    i_trasporto = next(i for i, p in enumerate(percorsi) if p.endswith("/transportationOptions/confirmation"))
    assert i_finestra < i_trasporto


def test_conferma_rifiuta_un_preventivo_scaduto_e_rimanda_a_riprendi():
    c = ClienteFinto(scadenza_preventivi="2026-09-01T00:00:00.000Z")
    plan_id = _pronto(c)
    with pytest.raises(ValueError, match="to-ups-sh1.*scaduto.*riprendi"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    assert c.post_fatte == []


def test_conferma_rifiuta_un_trasporto_che_non_e_tra_le_opzioni():
    c = ClienteFinto()
    plan_id = _pronto(c)
    with pytest.raises(ValueError, match="to-inventato.*sh1"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-inventato"}, adesso=_ADESSO)


def test_esito_incerto_sul_trasporto_blocca_il_piano(monkeypatch):
    from spapi_client import SpApiError
    class AttesaRotta(ClienteFinto):
        def attendi_operazione(self, operation_id, attesa_max_s=300):
            if self.post_fatte and self.post_fatte[-1][0].endswith("/transportationOptions/confirmation"):
                raise SpApiError("operazione non conclusa dopo 300s")
            return super().attendi_operazione(operation_id, attesa_max_s)
    c = AttesaRotta()
    plan_id = _pronto(c)
    with pytest.raises(SpApiError, match="ESITO INCERTO"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    assert giri.carica(plan_id)["bloccato"].startswith("ESITO INCERTO")
    assert _voce(plan_id)["esito"].startswith("ESITO INCERTO")
    with pytest.raises(ValueError, match="--verificato"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)


def test_conferma_a_trasporto_prenotato_scarica_solo_le_etichette(monkeypatch):
    pdf = __import__("unittest.mock").mock.Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.TRASPORTO_PRENOTATO)
    rif = registro.registra_tentativo("Phy to Pet", _lotto().righe)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": rif, "lotto": lotto_mod.a_dizionario(_lotto()),
                         "scelte": {}, "etichette": {}, "bloccato": None})
    registro.registra_piano("Phy to Pet", rif, plan_id, [])
    testo = giro.conferma(c, plan_id, None, {}, vai=True, pallet_etichette=2, adesso=_ADESSO)
    assert c.post_fatte == []
    assert len(giri.carica(plan_id)["etichette"]["sh1"]) == 2       # cartoni + pallet
    assert "Pallet" in giri.carica(plan_id)["etichette"]["sh1"][1]


def test_conferma_a_trasporto_prenotato_non_cancella_la_prenotazione_gia_registrata(monkeypatch):
    """Il rientro a TRASPORTO_PRENOTATO è il percorso di ripresa che
    _fino_al_menu suggerisce dopo un errore a metà etichette: la prenotazione
    del corriere è già pagata e la sua traccia nel registro non deve sparire."""
    pdf = __import__("unittest.mock").mock.Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.TRASPORTO_PRENOTATO)
    rif = registro.registra_tentativo("Phy to Pet", _lotto().righe)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": rif, "lotto": lotto_mod.a_dizionario(_lotto()),
                         "scelte": {}, "etichette": {}, "bloccato": None})
    registro.registra_piano("Phy to Pet", rif, plan_id, [
        {"shipment_id": "sh1", "centro_logistico": "MXP5", "righe": [{"sku": "SKU-1", "quantita": 30}],
         "opzioni_trasporto": [], "trasporto_confermato": {"transportation_option_id": "to-ups-sh1"},
         "etichette_path": None, "shipment_confirmation_id": "FBA1234sh1"},
    ])
    giro.conferma(c, plan_id, None, {}, vai=True, adesso=_ADESSO)
    voce = _voce(plan_id)
    assert voce["spedizioni"][0]["trasporto_confermato"]["transportation_option_id"] == "to-ups-sh1"
    assert voce["spedizioni"][0]["etichette_path"]


def test_conferma_a_piazzamento_gia_confermato_salta_la_conferma_del_piazzamento(monkeypatch):
    pdf = __import__("unittest.mock").mock.Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)
    c = ClienteFinto()
    plan_id = _pronto(c, da=stadio.TRASPORTO_DA_SCEGLIERE)
    testo = giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    percorsi = [p for p, _ in c.post_fatte]
    assert percorsi == [f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/transportationOptions/confirmation"]
    voce = _voce(plan_id)
    assert voce["spedizioni"][0]["trasporto_confermato"]["transportation_option_id"] == "to-ups-sh1"


def test_cli_prepara_riprendi_conferma(tmp_path, monkeypatch, capsys):
    import yaml
    # main() usa l'orologio vero: le scadenze fisse del finto (10/09/2026) sono
    # esplose da sole l'11/09. Qui devono restare nel futuro.
    fra_una_settimana = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    c = ClienteFinto(scadenza_piazzamento=fra_una_settimana, scadenza_preventivi=fra_una_settimana)
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: c)
    f = tmp_path / "lotto.yaml"
    f.write_text(yaml.safe_dump(lotto_mod.a_dizionario(_lotto())))
    assert giro.main(["prepara", str(f)]) == 0
    out = capsys.readouterr().out
    assert "wf1" in out and "UPS" in out
    assert giro.main(["riprendi", "wf1"]) == 0
    assert "UPS" in capsys.readouterr().out
    assert giro.main(["conferma", "wf1", "--piazzamento", "pl1", "--trasporto", "sh1=to-ups-sh1"]) == 0
    out = capsys.readouterr().out
    assert "Sto per prenotare" in out and "nessuna scrittura" in out
    assert _post_che_spendono(c) == []


def test_cli_errore_va_su_stderr_con_codice_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: ClienteFinto())
    assert giro.main(["riprendi", "wf-ignoto"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERRORE:") and "nessun file locale" in err


def test_cli_conferma_con_vai_passa_vai_e_pallet(monkeypatch):
    chiamate = {}
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: ClienteFinto())
    giri.salva("wf1", {"cliente": "Phy to Pet", "riferimento": "x", "lotto": {}, "scelte": {}, "etichette": {}, "bloccato": None})
    monkeypatch.setattr(giro, "conferma", lambda client, plan_id, po, tr, **k: chiamate.update(po=po, tr=tr, **k) or "ok")
    assert giro.main(["conferma", "wf1", "--piazzamento", "pl1", "--trasporto", "sh1=to1", "--trasporto", "sh2=to2",
                      "--finestra", "sh1=dw1", "--vai", "--pallet", "2", "--verificato"]) == 0
    assert chiamate["po"] == "pl1" and chiamate["tr"] == {"sh1": "to1", "sh2": "to2"}
    assert chiamate["finestre"] == {"sh1": "dw1"} and chiamate["vai"] is True
    assert chiamate["pallet_etichette"] == 2 and chiamate["verificato"] is True


def test_conferma_rifiuta_una_finestra_che_non_esiste_prima_di_spendere():
    """La commissione di piazzamento si paga PRIMA della conferma della
    finestra: un id sbagliato scoperto dopo lascia una spesa fatta e un
    piano a metà. Si controlla contro le finestre vere di Amazon."""
    c = ClienteFinto()
    plan_id = _pronto(c)
    with pytest.raises(ValueError, match="dw-typo.*dw1"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-proprio-sh1"},
                      finestre={"sh1": "dw-typo"}, vai=True, adesso=_ADESSO)
    assert c.post_fatte == []


def test_conferma_rifiuta_una_finestra_per_un_corriere_che_non_la_vuole():
    c = ClienteFinto()
    plan_id = _pronto(c)
    with pytest.raises(ValueError, match="sh1.*non richiede"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"},
                      finestre={"sh1": "dw1"}, vai=True, adesso=_ADESSO)
    assert c.post_fatte == []


def test_conferma_rifiuta_una_finestra_per_una_spedizione_che_non_c_e():
    c = ClienteFinto()
    plan_id = _pronto(c)
    with pytest.raises(ValueError, match="sh9.*non fa parte"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"},
                      finestre={"sh9": "dw1"}, vai=True, adesso=_ADESSO)
    assert c.post_fatte == []


def test_riprendi_con_lotto_su_un_piano_gia_noto_sostituisce_il_lotto_salvato():
    """Il file locale è una copia di lavoro; la verità è il file che passa
    Mariano. Ignorarlo in silenzio farebbe dichiarare ad Amazon scatole che
    non sono più quelle."""
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.SCATOLE_DA_DICHIARARE)
    giro.riprendi(c, plan_id, lotto=_lotto(), adesso=_ADESSO)
    rif = giri.carica(plan_id)["riferimento"]
    altro = lotto_mod.da_dizionario({
        "cliente": "Phy to Pet", "righe": [{"sku": "SKU-1", "quantita": 30}],
        "scatole": [{"quantita": 1, "peso_kg": 12.5, "lunghezza_cm": 40, "larghezza_cm": 30,
                     "altezza_cm": 25, "righe": [{"sku": "SKU-1", "quantita": 30}]}],
        "data_pronta": "2026-09-06T08:00Z"})
    testo = giro.riprendi(c, plan_id, lotto=altro, adesso=_ADESSO)
    assert "Lotto aggiornato" in testo
    locale = giri.carica(plan_id)
    assert locale["lotto"]["scatole"][0]["peso_kg"] == 12.5
    assert locale["lotto"]["data_pronta"] == "2026-09-06T08:00Z"
    assert locale["riferimento"] == rif


def test_riprendi_senza_nessuna_opzione_di_piazzamento_lo_dice_invece_di_schiantarsi():
    """Amazon puo' tornare zero divisioni (scatole o articoli non validi): il
    menù non ha niente da mostrare, e un IndexError non direbbe perché."""
    from spapi_client import SpApiError

    class SenzaPiazzamenti(ClienteFinto):
        def _genera_piazzamenti(self, p):
            p["piazzamenti"] = []

    c = SenzaPiazzamenti()
    plan_id = c.piano_a(stadio.PIAZZAMENTO_DA_GENERARE)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": registro.registra_tentativo("Phy to Pet", _lotto().righe),
                         "lotto": lotto_mod.a_dizionario(_lotto()), "scelte": {}, "etichette": {}, "bloccato": None})
    with pytest.raises(SpApiError, match="opzioni di piazzamento"):
        giro.riprendi(c, plan_id, adesso=_ADESSO)
    assert _voce(plan_id)["esito"].startswith("Amazon non ha generato")


def test_conferma_a_trasporto_prenotato_senza_vai_non_scrive_niente():
    """Anche il solo scaricare le etichette passa dal cancello: senza --vai
    non si tocca né Amazon né il registro né il file locale."""
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.TRASPORTO_PRENOTATO)
    rif = registro.registra_tentativo("Phy to Pet", _lotto().righe)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": rif, "lotto": lotto_mod.a_dizionario(_lotto()),
                         "scelte": {}, "etichette": {}, "bloccato": None})
    registro.registra_piano("Phy to Pet", rif, plan_id, [])
    testo = giro.conferma(c, plan_id, None, {}, vai=False, adesso=_ADESSO)
    assert "Nessuna scrittura" in testo and "--vai" in testo
    assert c.post_fatte == []
    assert giri.carica(plan_id)["etichette"] == {}
    assert _voce(plan_id)["esito"] == "in corso"


def test_conferma_a_piano_completato_riscarica_le_etichette(monkeypatch):
    """Un PDF cancellato per sbaglio si riprende: a COMPLETATO `conferma --vai`
    riscarica e basta, senza nessuna scrittura su Amazon."""
    scarichi = []
    pdf = __import__("unittest.mock").mock.Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get",
                        lambda url, **_: scarichi.append(url) or pdf)
    c = ClienteFinto()
    plan_id = _pronto(c)
    giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    post_prima = list(c.post_fatte)
    scarichi.clear()
    assert stadio.riconosci(stadio.leggi(c, plan_id), _ADESSO, etichette_scaricate=True) == stadio.COMPLETATO
    giro.conferma(c, plan_id, None, {}, vai=True, adesso=_ADESSO)
    assert scarichi                                    # riscaricate davvero
    assert c.post_fatte == post_prima                  # senza spendere niente
    assert giri.carica(plan_id)["etichette"]["sh1"][0].endswith("FBA1234sh1.pdf")


def test_conferma_senza_piazzamento_dice_che_manca_e_elenca_le_divisioni():
    c = ClienteFinto()
    plan_id = _pronto(c)
    with pytest.raises(ValueError, match=r"--piazzamento mancante.*pl1"):
        giro.conferma(c, plan_id, None, {"sh1": "to-ups-sh1"}, adesso=_ADESSO)


def test_conferma_con_vai_di_una_divisione_a_due_spedizioni(monkeypatch):
    """La divisione in più spedizioni è il caso normale sui lotti grossi: una
    sola conferma di trasporto le prenota tutte, e ognuna ha il suo
    shipmentConfirmationId e le sue etichette."""
    pdf = __import__("unittest.mock").mock.Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)

    class DueSpedizioni(ClienteFinto):
        def _genera_piazzamenti(self, p):
            super()._genera_piazzamenti(p)
            p["piazzamenti"][0]["shipmentIds"] = ["sh1", "sh2"]

    c = DueSpedizioni()
    plan_id = _pronto(c)
    giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1", "sh2": "to-ups-sh2"},
                  vai=True, adesso=_ADESSO)
    assert _post_che_spendono(c) == [
        f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/placementOptions/pl1/confirmation",
        f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/transportationOptions/confirmation"]
    voce = _voce(plan_id)
    assert [s["shipment_id"] for s in voce["spedizioni"]] == ["sh1", "sh2"]
    for s in voce["spedizioni"]:
        assert s["trasporto_confermato"]["transportation_option_id"] == f"to-ups-{s['shipment_id']}"
        assert s["etichette_path"].endswith(f"FBA1234{s['shipment_id']}.pdf")
    assert voce["esito"] == "completato"
    assert set(giri.carica(plan_id)["etichette"]) == {"sh1", "sh2"}


class _DueGruppi(ClienteFinto):
    """Amazon separa gli SKU in due gruppi di imballaggio: SKU-1 in pg1,
    SKU-2 in pg2. Sono due mucchi fisici distinti, e le scatole vanno
    assegnate all'uno o all'altro."""

    def piano_a(self, stadio_, plan_id="wf1"):
        pid = super().piano_a(stadio_, plan_id)
        p = self.piani[pid]
        p["items"] = [{"msku": "SKU-1", "quantity": 10, "prepOwner": "NONE", "labelOwner": "NONE"},
                      {"msku": "SKU-2", "quantity": 10, "prepOwner": "NONE", "labelOwner": "NONE"}]
        p["packing"]["packingGroups"] = ["pg1", "pg2"]
        return pid

    def get(self, path, params=None):
        for gruppo, sku in (("pg1", "SKU-1"), ("pg2", "SKU-2")):
            if path.endswith(f"/packingGroups/{gruppo}/items"):
                return {"items": [{"msku": sku, "quantity": 10, "labelOwner": "NONE"}]}
        return super().get(path, params)


def _lotto_due_sku(scatole):
    return lotto_mod.da_dizionario({
        "cliente": "Phy to Pet",
        "righe": [{"sku": "SKU-1", "quantita": 10}, {"sku": "SKU-2", "quantita": 10}],
        "scatole": scatole, "data_pronta": "2026-09-05T08:00Z"})


_MISURE = {"quantita": 1, "peso_kg": 8, "lunghezza_cm": 40, "larghezza_cm": 30, "altezza_cm": 25}


def test_una_scatola_che_mischia_due_gruppi_di_imballaggio_ferma_il_giro():
    c = _DueGruppi()
    plan_id = c.piano_a(stadio.SCATOLE_DA_DICHIARARE)
    lotto = _lotto_due_sku([dict(_MISURE, righe=[{"sku": "SKU-1", "quantita": 10},
                                                 {"sku": "SKU-2", "quantita": 10}])])
    with pytest.raises(ValueError, match="scatola 1.*gruppi"):
        giro.riprendi(c, plan_id, lotto=lotto, adesso=_ADESSO)


def test_con_due_gruppi_ogni_scatola_va_al_suo_gruppo():
    c = _DueGruppi()
    plan_id = c.piano_a(stadio.SCATOLE_DA_DICHIARARE)
    lotto = _lotto_due_sku([dict(_MISURE, righe=[{"sku": "SKU-1", "quantita": 10}]),
                            dict(_MISURE, peso_kg=5, righe=[{"sku": "SKU-2", "quantita": 10}])])
    giro.riprendi(c, plan_id, lotto=lotto, adesso=_ADESSO)
    corpo = next(b for p, b in c.post_fatte if p.endswith("/packingInformation"))
    gruppi = {g["packingGroupId"]: g["boxes"] for g in corpo["packageGroupings"]}
    assert set(gruppi) == {"pg1", "pg2"}
    assert [b["items"][0]["msku"] for b in gruppi["pg1"]] == ["SKU-1"]
    assert [b["items"][0]["msku"] for b in gruppi["pg2"]] == ["SKU-2"]
    assert gruppi["pg2"][0]["weight"]["value"] == 5


class _GruppiDivisi(ClienteFinto):
    """Dal vivo il 2026-09-15 su ECM (piano wfe91b870f, opzione po528df6af):
    Amazon mette lo stesso SKU in due gruppi di imballaggio, con quantità
    diverse in ciascuno. Solo Reladol_500 sta in un gruppo solo."""

    GRUPPI = {"pg1": {"Reladol_300": 10, "Reladol_100": 61, "ARTINORM_2_FBA": 7},
              "pg2": {"Reladol_500": 44, "Reladol_300": 12, "Reladol_100": 119, "ARTINORM_2_FBA": 11}}
    TOTALI = {"Reladol_500": 44, "Reladol_300": 22, "Reladol_100": 180, "ARTINORM_2_FBA": 18}

    def piano_a(self, stadio_, plan_id="wf1"):
        pid = super().piano_a(stadio_, plan_id)
        p = self.piani[pid]
        p["items"] = [{"msku": sku, "quantity": q, "prepOwner": "NONE", "labelOwner": "NONE"}
                      for sku, q in self.TOTALI.items()]
        p["packing"]["packingGroups"] = list(self.GRUPPI)
        return pid

    def get(self, path, params=None):
        for gruppo, articoli in self.GRUPPI.items():
            if path.endswith(f"/packingGroups/{gruppo}/items"):
                return {"items": [{"msku": sku, "quantity": q, "labelOwner": "NONE"}
                                  for sku, q in articoli.items()]}
        return super().get(path, params)


def test_uno_sku_diviso_fra_due_gruppi_ferma_il_giro_prima_delle_scatole():
    """Con le scatole standard mono-SKU ogni scatola di uno SKU diviso finiva
    nell'ultimo gruppo letto, e Amazon rifiutava (o peggio accettava) un
    contenuto sbagliato. Il giro si ferma prima, dicendo quali SKU e quanti
    pezzi per gruppo."""
    c = _GruppiDivisi()
    plan_id = c.piano_a(stadio.SCATOLE_DA_DICHIARARE)
    lotto = lotto_mod.da_dizionario({
        "cliente": "ECM",
        "righe": [{"sku": sku, "quantita": q} for sku, q in _GruppiDivisi.TOTALI.items()],
        "scatole": [dict(_MISURE, righe=[{"sku": sku, "quantita": q}])
                    for sku, q in _GruppiDivisi.TOTALI.items()],
        "data_pronta": "2026-09-16T08:00Z"})
    with pytest.raises(ValueError) as errore:
        giro.riprendi(c, plan_id, lotto=lotto, adesso=_ADESSO)
    messaggio = str(errore.value)
    for atteso in ("Reladol_300 (pg1: 10, pg2: 12)", "Reladol_100 (pg1: 61, pg2: 119)",
                   "ARTINORM_2_FBA (pg1: 7, pg2: 11)", "per gruppo", "--imballaggio"):
        assert atteso in messaggio
    assert "Reladol_500" not in messaggio
    assert not any(p.endswith("/packingInformation") for p, _ in c.post_fatte)


def test_il_menu_mostra_le_convenzionate_e_riassume_i_corrieri_propri_in_un_file(tmp_path):
    """Dal vivo un piano da 20 pezzi aveva 273 corrieri propri e 1 UPS: in
    chat si leggono le convenzionate col prezzo e un riassunto dei propri per
    modalità; l'elenco completo con gli id va in un file sul Desktop."""
    class MoltiPropri(ClienteFinto):
        def _trasporto(self, sid):
            base = super()._trasporto(sid)
            propri = [dict(base[1], transportationOptionId=f"to-{codice}-{sid}",
                           carrier={"name": codice, "alphaCode": codice}, preconditions=[],
                           shippingMode=modo)
                      for codice, modo in (("CIIQ", "GROUND_SMALL_PARCEL"), ("SQSB", "GROUND_SMALL_PARCEL"),
                                           ("YECD", "FREIGHT_LTL"))]
            return [base[0]] + propri
    c = MoltiPropri()
    plan_id, testo = giro.prepara(c, _lotto(), adesso=_ADESSO)
    assert "UPS" in testo and "7.90 EUR" in testo
    assert "corriere proprio: 3 opzioni" in testo
    assert "2 GROUND_SMALL_PARCEL" in testo and "1 FREIGHT_LTL" in testo
    assert "to-CIIQ-sh1" not in testo                      # gli id dei propri non intasano la chat
    percorso = tmp_path / "Desktop" / f"Corrieri-Phy to Pet-{plan_id}.txt"
    assert percorso.is_file() and str(percorso) in testo
    elenco = percorso.read_text()
    assert "to-CIIQ-sh1" in elenco and "YECD" in elenco and "FREIGHT_LTL" in elenco
    assert "to-ups-sh1" not in elenco                      # le convenzionate stanno in chat


def test_asn_legge_da_amazon_e_registra(monkeypatch, tmp_path):
    """Il verbo `asn` esiste perché col corriere proprio Amazon emette il
    codice dopo la prenotazione: quando esce, la spedizione è già chiusa nel
    registro e mancherebbe solo quel campo."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(giro.giri, "carica", lambda pid: {
        "cliente": "Arcipelago", "riferimento": "2026-09-03#0#8daf9149",
        "scelte": {"trasporto": {"sh1": "to1"}}})
    client = Mock()
    client.get.return_value = {"shipmentId": "sh1", "amazonReferenceId": "5FJEWLUY",
                               "shipmentConfirmationId": "FBA15MBTNVDG",
                               "destination": {"warehouseId": "XLI8"}, "status": "WORKING"}
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda c: client)
    registrati = []
    monkeypatch.setattr(giro.registro, "registra_asn",
                        lambda *a: registrati.append(a))
    assert giro.main(["asn", "wf1"]) == 0
    assert registrati == [("Arcipelago", "2026-09-03#0#8daf9149", "sh1", "5FJEWLUY")]


def test_asn_non_ancora_emesso_non_registra_nulla(monkeypatch, tmp_path):
    """Amazon lo emette con qualche minuto di ritardo: finché non c'è si dice,
    senza scrivere un campo vuoto che poi sembrerebbe un codice."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(giro.giri, "carica", lambda pid: {
        "cliente": "Arcipelago", "riferimento": "2026-09-03#0#8daf9149",
        "scelte": {"trasporto": {"sh1": "to1"}}})
    client = Mock()
    client.get.return_value = {"shipmentId": "sh1", "amazonReferenceId": None,
                               "destination": {"warehouseId": "XLI8"}, "status": "WORKING"}
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda c: client)
    def _mai(*a):
        raise AssertionError("non deve registrare senza ASN")
    monkeypatch.setattr(giro.registro, "registra_asn", _mai)
    assert giro.main(["asn", "wf1"]) == 0


# ------------------------------------------------- formato delle etichette ----

def _formati_etichette(c):
    """I PageType chiesti a getLabels, nell'ordine, distinti per tipo."""
    return [(params["LabelType"], params["PageType"])
            for path, params in c.get_fatte if path.endswith("/labels")]


def test_col_corriere_convenzionato_le_etichette_cartone_portano_anche_quella_del_corriere(monkeypatch):
    """Dal vivo il 03/09/2026 (ECM, UPS convenzionata): con PackageLabel_A4_4
    Amazon dà solo l'etichetta FBA, e il cartone resta senza quella UPS. Il
    formato combinato, una pagina per cartone, è l'unico che si può spedire."""
    pdf = Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)
    c = ClienteFinto()
    plan_id = _pronto(c)
    giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    assert _formati_etichette(c) == [("UNIQUE", "PackageLabel_Plain_Paper_CarrierBottom")]


def test_col_corriere_proprio_le_etichette_cartone_restano_quattro_per_foglio(monkeypatch):
    pdf = Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)
    c = ClienteFinto()
    plan_id = _pronto(c)
    giro.conferma(c, plan_id, "pl1", {"sh1": "to-proprio-sh1"}, finestre={"sh1": "dw1"}, vai=True, adesso=_ADESSO)
    assert _formati_etichette(c) == [("UNIQUE", "PackageLabel_A4_4")]


def test_al_rientro_a_trasporto_prenotato_il_formato_segue_il_corriere_prenotato_su_amazon(monkeypatch):
    """Il rientro rilegge le spedizioni senza `trasporto_confermato`: il corriere
    prenotato lo dice Amazon (`selectedTransportationOptionId`), non la memoria.
    Le etichette pallet non hanno un'etichetta corriere: restano 4 per foglio."""
    pdf = Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.TRASPORTO_PRENOTATO)          # prenotata to-ups-sh1, convenzionata
    rif = registro.registra_tentativo("Phy to Pet", _lotto().righe)
    giri.salva(plan_id, {"cliente": "Phy to Pet", "riferimento": rif, "lotto": lotto_mod.a_dizionario(_lotto()),
                         "scelte": {}, "etichette": {}, "bloccato": None})
    registro.registra_piano("Phy to Pet", rif, plan_id, [])
    giro.conferma(c, plan_id, None, {}, vai=True, pallet_etichette=1, adesso=_ADESSO)
    assert c.post_fatte == []
    assert _formati_etichette(c) == [("PALLET", "PackageLabel_A4_4"),
                                     ("UNIQUE", "PackageLabel_Plain_Paper_CarrierBottom")]


# ------------------------------------------- rilievi parcheggiati (03/09) ----

def test_riprendi_bloccato_non_sostituisce_il_lotto_prima_di_rifiutare():
    """Un piano bloccato da un esito incerto rifiuta `riprendi` senza
    --verificato: un comando respinto non deve lasciare nel file locale un
    lotto diverso da quello con cui il piano è stato bloccato."""
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.SCATOLE_DA_DICHIARARE)
    giro.riprendi(c, plan_id, lotto=_lotto(), adesso=_ADESSO)
    giri.aggiorna(plan_id, bloccato="ESITO INCERTO sulla conferma del piazzamento pl1")
    altro = lotto_mod.da_dizionario({**lotto_mod.a_dizionario(_lotto()), "data_pronta": "2026-09-06T08:00Z"})
    with pytest.raises(ValueError, match="ESITO INCERTO.*--verificato"):
        giro.riprendi(c, plan_id, lotto=altro, adesso=_ADESSO)
    assert giri.carica(plan_id)["lotto"]["data_pronta"] == "2026-09-05T08:00Z"


def test_al_rientro_le_spedizioni_note_si_cercano_col_riferimento_non_con_la_posizione(monkeypatch):
    """Due clienti col nome che si sanifica uguale condividono il file del
    registro: la voce del lotto si trova col riferimento (che porta il
    lotto_id), non contando le posizioni nella vista filtrata di
    `ultimo_lotto`, dove la posizione 1 del file può essere la 0."""
    pdf = Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)
    a, b = "Puraluna (Bee Your Home)", "Puraluna [Bee Your Home]"
    registro.registra_tentativo(b, [{"sku": "SKU-B", "quantita": 1}])          # posizione 0 del file
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.TRASPORTO_PRENOTATO)
    lotto = lotto_mod.da_dizionario({**lotto_mod.a_dizionario(_lotto()), "cliente": a})
    rif = registro.registra_tentativo(a, lotto.righe)                            # posizione 1
    giri.salva(plan_id, {"cliente": a, "riferimento": rif, "lotto": lotto_mod.a_dizionario(lotto),
                         "scelte": {}, "etichette": {}, "bloccato": None})
    registro.registra_piano(a, rif, plan_id, [])
    giro.conferma(c, plan_id, None, {}, vai=True, adesso=_ADESSO)
    assert registro.voce(a, rif)["spedizioni"][0]["shipment_id"] == "sh1"
    assert registro.voce(a, rif)["esito"] == "completato"


def test_pallet_di_un_piano_diviso_si_danno_per_spedizione(monkeypatch):
    """`--pallet N` vale per una spedizione sola: su un piano diviso ogni
    spedizione ha i suoi bancali, e un numero unico stamperebbe N etichette
    pallet anche per la spedizione che viaggia in cartoni. Il rifiuto arriva
    prima di spendere, come per le finestre."""
    pdf = Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)

    class DueSpedizioni(ClienteFinto):
        def _genera_piazzamenti(self, p):
            super()._genera_piazzamenti(p)
            p["piazzamenti"][0]["shipmentIds"] = ["sh1", "sh2"]

    c = DueSpedizioni()
    plan_id = _pronto(c)
    trasporti = {"sh1": "to-ups-sh1", "sh2": "to-ups-sh2"}
    with pytest.raises(ValueError, match=r"--pallet 2.*sh1.*sh2"):
        giro.conferma(c, plan_id, "pl1", trasporti, vai=True, pallet_etichette=2, adesso=_ADESSO)
    assert _post_che_spendono(c) == []
    with pytest.raises(ValueError, match=r"--pallet sh9.*non fa parte"):
        giro.conferma(c, plan_id, "pl1", trasporti, vai=True, pallet_etichette={"sh9": 1}, adesso=_ADESSO)
    assert _post_che_spendono(c) == []
    giro.conferma(c, plan_id, "pl1", trasporti, vai=True, pallet_etichette={"sh1": 2}, adesso=_ADESSO)
    pallet = [(path, params["NumberOfPallets"]) for path, params in c.get_fatte
              if path.endswith("/labels") and params["LabelType"] == "PALLET"]
    assert pallet == [("/fba/inbound/v0/shipments/FBA1234sh1/labels", 2)]
    etichette = giri.carica(plan_id)["etichette"]
    assert len(etichette["sh1"]) == 2 and len(etichette["sh2"]) == 1


def test_cli_pallet_per_spedizione(monkeypatch):
    chiamate = {}
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: ClienteFinto())
    giri.salva("wf1", {"cliente": "Phy to Pet", "riferimento": "x", "lotto": {}, "scelte": {}, "etichette": {}, "bloccato": None})
    monkeypatch.setattr(giro, "conferma", lambda client, plan_id, po, tr, **k: chiamate.update(**k) or "ok")
    assert giro.main(["conferma", "wf1", "--vai", "--pallet", "sh1=2", "--pallet", "sh2=1"]) == 0
    assert chiamate["pallet_etichette"] == {"sh1": 2, "sh2": 1}
    assert giro.main(["conferma", "wf1", "--vai", "--pallet", "2", "--pallet", "sh2=1"]) == 1
    assert giro.main(["conferma", "wf1", "--vai", "--pallet", "sh1=due"]) == 1


def test_un_piano_adottato_a_piazzamento_gia_confermato_registra_la_commissione(monkeypatch):
    """Il piazzamento l'ha confermato qualcun altro (Seller Central, un giro
    non registrato): la commissione è spesa comunque e la voce del registro
    deve dirlo, altrimenti il lotto risulta gratis."""
    pdf = Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)
    c = ClienteFinto()
    plan_id = c.piano_a(stadio.PIAZZAMENTO_DA_SCEGLIERE)
    c.post(f"/inbound/fba/2024-03-20/inboundPlans/{plan_id}/placementOptions/pl2/confirmation")  # fuori da giro.py: pl2 costa 4 EUR
    giro.riprendi(c, plan_id, lotto=_lotto(), adesso=_ADESSO)                                    # adozione
    c.post_fatte.clear()
    giro.conferma(c, plan_id, "pl2", {"sh2": "to-ups-sh2"}, vai=True, adesso=_ADESSO)
    assert not any("/placementOptions/" in p for p in _post_che_spendono(c))
    confermato = _voce(plan_id)["piazzamento_confermato"]
    assert (confermato["placement_option_id"], confermato["commissioni"], confermato["valuta"]) == ("pl2", 4.0, "EUR")


def test_il_secondo_giro_dopo_un_trasporto_fallito_non_riscrive_la_commissione(monkeypatch):
    """La commissione registrata al primo giro è la traccia della spesa vera,
    con la sua ora: il giro di ripresa a piazzamento già confermato non deve
    sovrascriverla con un'ora nuova."""
    from spapi_client import SpApiError
    pdf = Mock(status_code=200, content=b"%PDF")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)

    class TrasportoRottoUnaVolta(ClienteFinto):
        rotto = True
        def post(self, path, json_body=None, params=None):
            if path.endswith("/transportationOptions/confirmation") and self.rotto:
                self.rotto = False
                raise SpApiError("500 su /transportationOptions/confirmation")
            return super().post(path, json_body, params)

    c = TrasportoRottoUnaVolta()
    plan_id = _pronto(c)
    with pytest.raises(SpApiError, match="500"):
        giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    prima = _voce(plan_id)["piazzamento_confermato"]
    giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    assert _voce(plan_id)["piazzamento_confermato"] == prima
    assert _voce(plan_id)["esito"] == "completato"


import scatole as scatole_mod
from cliente_finto import scatola_finta


def _spedito_ecm(c):
    c.spedito("wf-ago", "2026-08-12T11:55:22Z", [
        scatola_finta("Reladol_100", 60, 9.0, 30, 40, 31, quantita=2, modello="P1 - B4", asin="B0CB4DCJPZ")])
    return c


def test_scatole_stampa_la_tabella_e_salva_l_istantanea(tmp_path):
    c = _spedito_ecm(ClienteFinto())
    testo = giro.scatole(c, "ECM", {}, adesso=_ADESSO)
    assert "Reladol_100 (B0CB4DCJPZ): 60 pz/collo" in testo
    assert "Istantanea: " in testo and scatole_mod.carica_istantanea("ECM")["sku"]["Reladol_100"]["pezzi"] == 60
    assert c.post_fatte == []
    assert not (tmp_path / "Desktop").exists()                 # senza quantità niente bozza


def test_scatole_con_storico_scrive_la_bozza_col_collo_parziale(tmp_path):
    c = _spedito_ecm(ClienteFinto())
    testo = giro.scatole(c, "ECM", {"Reladol_100": 130}, adesso=_ADESSO, storico=True)
    percorso = tmp_path / "Desktop/Lotto-ECM-2026-09-02.yaml"
    assert f"Bozza del lotto: {percorso}" in testo and "da pesare" in testo
    assert "quantita: 130" in percorso.read_text()


def test_cli_scatole(monkeypatch, capsys, tmp_path):
    """`adesso=None` fa usare a giro.scatole l'orologio di sistema: senza
    bloccarlo la finestra dei --mesi e il nome della bozza scivolano col
    calendario e la prova smette di essere quella descritta qui."""
    class _OrologioFermo(datetime):
        @classmethod
        def now(cls, tz=None):
            return _ADESSO

    c = _spedito_ecm(ClienteFinto())
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: c)
    monkeypatch.setattr(giro, "datetime", _OrologioFermo)
    assert giro.main(["scatole", "ECM", "Reladol_100=130", "--mesi", "6"]) == 0
    out = capsys.readouterr().out
    assert "60 pz/collo" in out and "6 mesi" in out and "Lotto-ECM-2026" in out
    assert giro.main(["scatole", "ECM", "Reladol_100=tanti"]) == 1
    assert "quantità" in capsys.readouterr().err
    assert giro.main(["scatole", "ECM", "Reladol_100"]) == 1
    assert "SKU=QTA" in capsys.readouterr().err


def test_cli_scatole_mesi_zero_o_negativo_e_rifiutato(monkeypatch, capsys):
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: ClienteFinto())
    assert giro.main(["scatole", "ECM", "--mesi", "0"]) == 1
    assert "--mesi" in capsys.readouterr().err
    assert giro.main(["scatole", "ECM", "--mesi", "-3"]) == 1
    assert "--mesi" in capsys.readouterr().err


def _istantanea_phytopet(peso=12.0, pezzi=30):
    """Lo standard di SKU-1 nel formato di scatole.leggi_storico; _lotto()
    dichiara 30 pz 40×30×25 8 kg."""
    scatole_mod.salva_istantanea({
        "cliente": "Phy to Pet", "letto_il": "2026-09-01T10:00:00Z", "mesi": 12, "piani_letti": 1,
        "piani_saltati": [], "colli_misti_esclusi": 0,
        "sku": {"SKU-1": {"pezzi": pezzi, "lunghezza_cm": 40, "larghezza_cm": 30, "altezza_cm": 25,
                          "peso_kg": peso, "modello": "P1 - B1", "asin": "B0TEST",
                          "ultimo_piano": "wf-old", "ultima_data": "2026-07-01", "varianti": []}}})


def test_prepara_si_ferma_su_uno_scostamento_senza_scrivere_niente():
    c = ClienteFinto()
    _istantanea_phytopet(peso=12.0)                                      # 8 kg dichiarati contro 12: -33 %
    with pytest.raises(ValueError, match=r"(?s)scatole diverse dallo storico.*SKU-1.*8 kg dichiarati.*12 kg.*--scatole-nuove"):
        giro.prepara(c, _lotto(), adesso=_ADESSO)
    assert c.post_fatte == [] and c.get_fatte == []
    assert registro.ultimo_lotto("Phy to Pet") == []
    assert giri.carica("wf1") is None


def test_prepara_con_scatole_nuove_procede_e_stampa_il_confronto():
    c = ClienteFinto()
    _istantanea_phytopet(peso=12.0)
    plan_id, testo = giro.prepara(c, _lotto(), adesso=_ADESSO, scatole_nuove=True)
    assert plan_id == "wf1" and "8 kg dichiarati" in testo and "UPS" in testo


def test_prepara_con_scatole_come_lo_storico_lo_dice():
    c = ClienteFinto()
    _istantanea_phytopet(peso=8.0)
    _, testo = giro.prepara(c, _lotto(), adesso=_ADESSO)
    assert "come nello storico del 2026-09-01" in testo


def test_prepara_senza_istantanea_lo_dice_e_procede():
    c = ClienteFinto()
    _, testo = giro.prepara(c, _lotto(), adesso=_ADESSO)
    assert "nessuno storico letto per Phy to Pet" in testo and "giro.py scatole" in testo and "UPS" in testo


def test_riprendi_con_lotto_passa_dal_controllo_prima_di_scrivere_il_file():
    c = ClienteFinto()
    _istantanea_phytopet(peso=12.0)
    plan_id = c.piano_a(stadio.SCATOLE_DA_DICHIARARE)
    with pytest.raises(ValueError, match="scatole diverse"):
        giro.riprendi(c, plan_id, lotto=_lotto(), adesso=_ADESSO)
    assert giri.carica(plan_id) is None and registro.ultimo_lotto("Phy to Pet") == []
    testo = giro.riprendi(c, plan_id, lotto=_lotto(), adesso=_ADESSO, scatole_nuove=True)
    assert "8 kg dichiarati" in testo and giri.carica(plan_id)["lotto"]["scatole"][0]["peso_kg"] == 8


def test_cli_scatole_nuove(monkeypatch, tmp_path):
    import yaml
    chiamate = {}
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda cliente: ClienteFinto())
    monkeypatch.setattr(giro, "prepara", lambda client, lotto, **k: chiamate.update(k) or ("wf1", "ok"))
    f = tmp_path / "lotto.yaml"
    f.write_text(yaml.safe_dump(lotto_mod.a_dizionario(_lotto())))
    assert giro.main(["prepara", str(f), "--scatole-nuove"]) == 0
    assert chiamate["scatole_nuove"] is True
    monkeypatch.setattr(giro, "riprendi", lambda client, plan_id, **k: chiamate.update(k) or "ok")
    assert giro.main(["riprendi", "wf1", "--lotto", str(f), "--scatole-nuove"]) == 0
    assert chiamate["scatole_nuove"] is True


def test_scatole_con_modello_scelto_scrive_la_bozza_a_colli_pieni(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.spedito("wf1", "2026-05-14T10:00:00Z",
              [scatola_finta("Reladol_300", 22, 9.5, 38, 51, 47, quantita=2, modello="Reladol 300")])
    c.spedito("wf2", "2026-09-03T10:00:00Z",
              [scatola_finta("Reladol_300", 22, 9.5, 38, 51, 47, modello="P1 - B2")])
    adesso = datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc)
    testo = giro.scatole(c, "ECM", {"Reladol_300": 35}, adesso=adesso, modello="Reladol 300")
    assert "Reladol 300" in testo and "44 pz" in testo
    bozza = (tmp_path / "Desktop" / "Lotto-ECM-2026-09-05.yaml").read_text()
    assert "quantita: 2, peso_kg: 9.5" in bozza and "da pesare" not in bozza


def test_scatole_con_le_quantita_parte_dal_modello_creato_nell_account(tmp_path, monkeypatch):
    """Regola del 2026-10-14: affidata una spedizione, il modello col nome vero
    vince sull'ultima scatola automatica, anche se questa è più recente."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.spedito("wf1", "2026-05-14T10:00:00Z",
              [scatola_finta("Reladol_300", 22, 9.5, 38, 51, 47, quantita=2, modello="Reladol 300")])
    c.spedito("wf2", "2026-09-03T10:00:00Z",
              [scatola_finta("Reladol_300", 30, 12, 40, 50, 50, modello="P1 - B2")])
    c.spedito("wf0", "2026-03-02T10:00:00Z",
              [scatola_finta("Reladol_300", 11, 5, 38, 51, 25, modello="Reladol 300 mezzo")])
    adesso = datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc)
    testo = giro.scatole(c, "ECM", {"Reladol_300": 35}, adesso=adesso)
    assert "35 pz richiesti → 44 pz in 2 colli, modello Reladol 300 (22 pz" in testo
    assert "altri modelli: Reladol 300 mezzo (11 pz)" in testo and "P1 - B2" not in testo and "--modello" in testo
    bozza = (tmp_path / "Desktop" / "Lotto-ECM-2026-09-05.yaml").read_text()
    assert "quantita: 2, peso_kg: 9.5" in bozza and "da pesare" not in bozza
    assert scatole_mod.carica_istantanea("ECM")["sku"]["Reladol_300"]["pezzi"] == 22
    assert c.post_fatte == []


def test_scatole_senza_modelli_per_uno_sku_lo_dice(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="nessun modello d'imballaggio per NUOVO"):
        giro.scatole(_spedito_ecm(ClienteFinto()), "ECM", {"NUOVO": 10}, adesso=_ADESSO)


def test_scatole_modello_e_storico_insieme_sono_rifiutati():
    with pytest.raises(ValueError, match="si escludono"):
        giro.scatole(ClienteFinto(), "ECM", {"X": 1}, adesso=_ADESSO, modello="A", storico=True)


def test_scatole_con_un_modello_che_non_esiste_lo_dice(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.spedito("wf1", "2026-05-14T10:00:00Z",
              [scatola_finta("Reladol_300", 22, 9.5, 38, 51, 47, modello="Reladol 300")])
    with pytest.raises(ValueError, match="Reladol 300"):
        giro.scatole(c, "ECM", {"Reladol_300": 35},
                     adesso=datetime(2026, 9, 5, tzinfo=timezone.utc), modello="INVENTATO")


def test_stock_avvisa_una_volta_e_scrive_la_bozza_al_cliente_regolare(tmp_path, monkeypatch):
    """Arcipelago: tre spedizioni identiche → spedizione tipo → bozza da sola."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(
        "Country\tProduct Name\tFNSKU\tMerchant SKU\tASIN\tCondition\tSupplier\t"
        "Units Sold Last 30 Days\tTotal Units\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tGin\tX0\t138_FBA\tB0F94GXPN5\tNew\t--\t40\t0\t0\t12\t\t100\t09/12/2026\n")
    for i, data in enumerate(["2026-07-16", "2026-06-11", "2026-04-24"]):
        c.spedito(f"wf{i}", f"{data}T10:00:00Z",
                  [scatola_finta("138_FBA", 3, 2.22, 32, 42.7, 25.3, quantita=36,
                                 modello="PALLET 36 PACCHI")])
    mandate = []
    testo = giro.stock(lambda _: c, ["Arcipelago"], oggi=date(2026, 9, 5),
                       adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc),
                       manda=lambda ogg, html: mandate.append(ogg) or "agenti@agencynewera.it")
    assert mandate == ["[STOCK] Arcipelago — 1 SKU da riassortire"]
    bozza = (tmp_path / "Desktop" / "Lotto-Arcipelago-2026-09-05.yaml").read_text()
    assert "quantita: 34" in bozza      # 100 pz arrotondati: 34 colli da 3 = 102
    assert "PALLET 36 PACCHI" in bozza
    assert "Bozza" in testo


def test_stock_modello_di_ricorrenza_assente_dai_noti_cade_su_candidati(tmp_path, monkeypatch):
    """Task 2: ricorrenza puo' eleggere un modello che modelli ha scartato (per
    esempio prima della correzione del Task 3). Prima della correzione,
    modelli.scegli alzava ValueError non catturato qui, e il blocco esterno di
    giro.stock buttava via TUTTI gli avvisi del cliente, non solo la bozza."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\t138_FBA\tB0F94GXPN5\t40\t0\t12\t\t100\t09/12/2026\n")
    for i, data in enumerate(["2026-07-16", "2026-06-11", "2026-04-24"]):
        c.spedito(f"wf{i}", f"{data}T10:00:00Z",
                  [scatola_finta("138_FBA", 3, 2.22, 32, 42.7, 25.3, quantita=36,
                                 modello="PALLET 36 PACCHI")])
    monkeypatch.setattr(giro.ricorrenza, "spedizione_tipo",
                        lambda piani, sku, minimo=3: giro.ricorrenza.Tipo(
                            sku=sku, pezzi_base=108, colli_base=36,
                            modello="MODELLO INESISTENTE", volte=3))
    corpi = []
    giro.stock(lambda _: c, ["Arcipelago"], oggi=date(2026, 9, 5),
              adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc),
              manda=lambda ogg, html: corpi.append(html))
    assert len(corpi) == 1
    assert "138_FBA" in corpi[0]                    # l'avviso resta, non e' stato buttato
    assert "nessuna spedizione tipo" in corpi[0]     # e' finito nel ramo candidati...
    assert "PALLET 36 PACCHI" in corpi[0]            # ...che elenca il modello noto, non lo sceglie
    assert "ValueError" not in corpi[0]              # niente errore che inghiotte il blocco


def test_stock_secondo_giro_non_manda_niente(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\t138_FBA\tB0F94GXPN5\t40\t0\t12\t\t100\t09/12/2026\n")
    for i, data in enumerate(["2026-07-16", "2026-06-11", "2026-04-24"]):
        c.spedito(f"wf{i}", f"{data}T10:00:00Z",
                  [scatola_finta("138_FBA", 3, 2.22, 32, 42.7, 25.3, quantita=36,
                                 modello="PALLET 36 PACCHI")])
    argomenti = dict(oggi=date(2026, 9, 5),
                     adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc))
    mandate = []
    giro.stock(lambda _: c, ["Arcipelago"], manda=lambda o, h: mandate.append(o), **argomenti)
    giro.stock(lambda _: c, ["Arcipelago"], oggi=date(2026, 9, 6),
               adesso=datetime(2026, 9, 6, 11, 0, tzinfo=timezone.utc),
               manda=lambda o, h: mandate.append(o))
    assert len(mandate) == 1


def test_stock_in_prova_non_manda_e_non_salva(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tSKU-1\tB0TEST\t30\t0\t2\t\t50\t09/09/2026\n")
    import stato_stock
    testo = giro.stock(lambda _: c, ["ECM"], oggi=date(2026, 9, 5),
                       adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc), prova=True,
                       manda=lambda o, h: pytest.fail("in prova non si manda niente"))
    assert "PROVA" in testo and "SKU-1" in testo
    assert stato_stock.carica("ECM") == {}      # niente stato: domani riavvisa


def test_stock_nomina_il_cliente_saltato_e_va_avanti(tmp_path, monkeypatch):
    """Un report FATAL su un cliente non deve far saltare il giro degli altri,
    e deve comparire nella mail: il silenzio sarebbe una bugia."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    rotto, sano = ClienteFinto(), ClienteFinto()
    rotto.prepara_report("Country\tMerchant SKU\n", stato="FATAL")
    sano.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tSKU-1\tB0TEST\t30\t0\t2\t\t50\t09/09/2026\n")
    corpi = []
    giro.stock(lambda nome: rotto if nome == "Arcipelago" else sano, ["Arcipelago", "ECM"],
               oggi=date(2026, 9, 5), adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc),
               manda=lambda o, html: corpi.append(html))
    assert len(corpi) == 1 and "FATAL" in corpi[0] and "SKU-1" in corpi[0]


def test_stock_senza_niente_da_dire_non_manda_niente(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tSKU-1\tB0TEST\t10\t0\t500\t\t0\t\n")
    testo = giro.stock(lambda _: c, ["ECM"], oggi=date(2026, 9, 5),
                       adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc),
                       manda=lambda o, h: pytest.fail("niente da dire, niente mail"))
    assert "niente da segnalare" in testo.lower()


def test_stock_cliente_senza_marketplace_id_e_saltato_non_uccide_gli_altri(tmp_path, monkeypatch):
    """clienti.carica_cliente accede a voce['marketplace_id'] direttamente: un
    cliente aggiunto senza quel campo alza KeyError, non catturato prima della
    correzione — e il giro moriva per tutti i clienti (Task 1, DESIGN §14)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tSKU-1\tB0TEST\t30\t0\t2\t\t50\t09/09/2026\n")

    def carica_cliente(nome, percorso=None):
        if nome == "SenzaMarketplace":
            raise KeyError("marketplace_id")
        return _CFG

    monkeypatch.setattr(giro.clienti, "carica_cliente", carica_cliente)
    corpi = []
    giro.stock(lambda _: c, ["SenzaMarketplace", "ECM"], oggi=date(2026, 9, 5),
               adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc),
               manda=lambda o, html: corpi.append(html))
    assert len(corpi) == 1
    assert "SenzaMarketplace" in corpi[0] and "KeyError" in corpi[0]
    assert "SKU-1" in corpi[0]


def test_stock_giorno_silenzioso_salva_lo_stato_e_cancella_le_voci_risolte(tmp_path, monkeypatch):
    """Task 12: prima della correzione un giorno senza avvisi tornava senza
    salvare, e uno SKU risalito sopra soglia non vedeva mai cancellata la sua
    voce — con la conseguenza che un calo successivo entro 7 giorni sarebbe
    stato letto come 'sollecito' invece che come 'nuovo' (DESIGN §14)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    import stato_stock

    c1 = ClienteFinto()
    c1.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tSKU-1\tB0TEST\t30\t0\t2\t\t50\t09/09/2026\n")
    giro.stock(lambda _: c1, ["ECM"], oggi=date(2026, 9, 5),
              adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc),
              manda=lambda o, h: "agenti@agencynewera.it")
    assert stato_stock.carica("ECM") != {}          # SKU-1 sotto soglia: in stato

    c2 = ClienteFinto()
    c2.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tSKU-1\tB0TEST\t10\t0\t500\t\t0\t\n")
    testo = giro.stock(lambda _: c2, ["ECM"], oggi=date(2026, 9, 6),
                       adesso=datetime(2026, 9, 6, 11, 0, tzinfo=timezone.utc),
                       manda=lambda o, h: pytest.fail("niente da dire, niente mail"))
    assert "niente da segnalare" in testo.lower()
    assert stato_stock.carica("ECM") == {}          # tornato sopra soglia: la voce sparisce


def test_stock_in_prova_su_giorno_silenzioso_non_salva(tmp_path, monkeypatch):
    """--prova non deve scrivere niente su disco, nemmeno nel ramo silenzioso."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    import os
    import stato_stock
    c = ClienteFinto()
    c.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tSKU-1\tB0TEST\t10\t0\t500\t\t0\t\n")
    giro.stock(lambda _: c, ["ECM"], oggi=date(2026, 9, 5),
              adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc), prova=True,
              manda=lambda o, h: pytest.fail("in prova non si manda niente"))
    assert not os.path.exists(stato_stock.percorso("ECM"))


def test_stock_non_propone_uno_sku_che_non_vende_da_novanta_giorni(tmp_path, monkeypatch):
    """Il rilievo di Mariano sulla mail vera di ECM (2026-09-05): Amazon
    consigliava di rifornire prodotti che non vendiamo più."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tGermGEL_5L_FBA\tB0MORTO\t0\t0\t0\tout_of_stock\t5\t09/05/2026\n"
        "IT\tCALMAPROST_FBA\tB0VIVO\t7\t0\t6\t\t18\t09/12/2026\n")
    c.vendite = {"B0MORTO": 0, "B0VIVO": 22}
    mandate = []
    testo = giro.stock(lambda _: c, ["ECM"], oggi=date(2026, 9, 5),
                       adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc),
                       manda=lambda o, html: mandate.append(html) or "agenti@agencynewera.it")
    assert len(mandate) == 1
    assert "CALMAPROST_FBA" in mandate[0]
    assert "GermGEL_5L_FBA" not in mandate[0]
    assert "non vendono da 90 giorni" in testo and "GermGEL_5L_FBA" in testo

    # anche in prova, dove la mail non parte: il filtro non deve essere una scatola nera
    in_prova = giro.stock(lambda _: c, ["ECM"], oggi=date(2026, 9, 5),
                          adesso=datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc), prova=True,
                          manda=lambda o, h: pytest.fail("in prova non si manda"))
    assert "non vendono da 90 giorni" in in_prova and "GermGEL_5L_FBA" in in_prova


# ------------------------------------------------ etichette nella cartella PRIME ----

def _pdf_finto(monkeypatch):
    pdf = Mock(status_code=200, content=b"%PDF-1.4 finto")
    monkeypatch.setattr(giro.etichette.requests, "get", lambda url, **_: pdf)


def test_le_etichette_finiscono_nella_cartella_prime_del_cliente(tmp_path, monkeypatch):
    """Mariano le riprende da lì, non dal Desktop (deciso 2026-09-07)."""
    _pdf_finto(monkeypatch)
    monkeypatch.setattr(giro.clienti, "carica_cliente",
                        lambda nome, percorso=None: {**_CFG, "cartella": "Phy to pet"})
    prime = (tmp_path / "Desktop" / "LAVORO" / "AZIENDE NEW ERA" / "PERFORMANCE"
             / "Phy to pet" / "10 LOGISTICA" / "PRIME")
    prime.mkdir(parents=True)
    c = ClienteFinto()
    plan_id = _pronto(c)

    testo = giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, pallet_etichette=1, adesso=_ADESSO)

    cartella = prime / "2026" / "09 SETTEMBRE" / "2026-09-02 FBA1234sh1"
    atteso = str(cartella / "Etichette-Phy to Pet-FBA1234sh1.pdf")
    assert _voce(plan_id)["spedizioni"][0]["etichette_path"] == atteso
    assert os.path.exists(atteso)
    assert os.path.exists(cartella / "Etichette-Phy to Pet-FBA1234sh1-Pallet.pdf")   # anche i pallet, stessa cartella
    assert atteso in testo
    assert not os.path.exists(tmp_path / "Desktop" / "Etichette-Phy to Pet-FBA1234sh1.pdf")


def test_senza_cartella_cliente_le_etichette_restano_sul_desktop_e_il_giro_lo_dice(tmp_path, monkeypatch):
    """La spedizione è già pagata: una cartella mancante non deve fermare le
    etichette, ma il giro deve dire dove sono e perché."""
    _pdf_finto(monkeypatch)
    monkeypatch.setattr(giro.clienti, "carica_cliente",
                        lambda nome, percorso=None: {**_CFG, "cartella": "Phy to pet"})
    c = ClienteFinto()
    plan_id = _pronto(c)

    testo = giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)

    atteso = str(tmp_path / "Desktop" / "Etichette-Phy to Pet-FBA1234sh1.pdf")
    assert _voce(plan_id)["spedizioni"][0]["etichette_path"] == atteso
    assert "solo sul Desktop" in testo and "Phy to pet" in testo


# ------------------------------------------------ ritiro e data pronta ----

def test_col_convenzionato_a_pacchi_il_giro_avverte_che_il_ritiro_non_e_prenotato(monkeypatch):
    """Maricart 08/09/2026: UPS non è passato. Col convenzionato in modalità
    pacchi Amazon vende le etichette prepagate, non il ritiro."""
    _pdf_finto(monkeypatch)
    c = ClienteFinto()
    plan_id = _pronto(c)
    testo = giro.conferma(c, plan_id, "pl1", {"sh1": "to-ups-sh1"}, vai=True, adesso=_ADESSO)
    assert "Ritiro NON prenotato" in testo and "UPS" in testo and "FBA1234sh1" in testo
    assert "Data pronta dichiarata ad Amazon: 05/09/2026" in testo


def test_col_corriere_proprio_nessun_avviso_sul_ritiro_ma_la_data_pronta_si(monkeypatch):
    _pdf_finto(monkeypatch)
    c = ClienteFinto()
    plan_id = _pronto(c)
    testo = giro.conferma(c, plan_id, "pl1", {"sh1": "to-proprio-sh1"}, finestre={"sh1": "dw1"},
                          vai=True, adesso=_ADESSO)
    assert "Ritiro NON prenotato" not in testo
    assert "Data pronta dichiarata ad Amazon: 05/09/2026" in testo


def test_stock_manda_lo_stesso_se_il_file_della_password_e_leggibile_da_altri(tmp_path, monkeypatch):
    """Opzione B (Mariano, 2026-09-11): un file della password con i permessi
    larghi non ferma la mail delle 11:00 — partirebbe un silenzio che nessuno
    nota. La mail parte e chiede a chi legge di stringerli."""
    (tmp_path / "Desktop").mkdir()
    sentinella = tmp_path / "sentinella.yaml"
    sentinella.write_text('password: "segreta"\n')
    sentinella.chmod(0o644)
    cartella = tmp_path / ".config" / "newera-amazon" / "spedizioni"
    cartella.mkdir(parents=True)
    (cartella / "mail.yaml").write_text(
        'smtp_host: "smtp.x.it"\nsmtp_port: 587\nmittente: "a@x.it"\n'
        f'destinatario: "agenti@x.it"\npassword_da: "{sentinella}"\n')
    mandate = []
    monkeypatch.setattr(giro.avviso_mail, "manda",
                        lambda config, ogg, html: mandate.append((config, html)) or "agenti@x.it")
    c = ClienteFinto()
    c.prepara_report(
        "Country\tMerchant SKU\tASIN\tUnits Sold Last 30 Days\tInbound\tAvailable\tAlert\t"
        "Recommended replenishment qty\tRecommended ship date\n"
        "IT\tCALMAPROST_FBA\tB0VIVO\t7\t0\t6\t\t18\t09/12/2026\n")
    testo = giro.stock(lambda _: c, ["ECM"], oggi=date(2026, 9, 11),
                       adesso=datetime(2026, 9, 11, 11, 0, tzinfo=timezone.utc))
    assert len(mandate) == 1, "la mail deve partire lo stesso"
    config, html = mandate[0]
    assert config["password"] == "segreta"
    assert "chmod 600" in html and str(sentinella) in html
    assert "mail a agenti@x.it" in testo


def test_un_soglie_yaml_rotto_non_uccide_il_giro_dello_stock(tmp_path, monkeypatch):
    """Collaudo del 06/09: yaml.YAMLError non è ValueError e non era fra le
    eccezioni del ciclo per cliente: traceback grezzo, niente mail per nessuno."""
    import yaml
    (tmp_path / "Desktop").mkdir()
    monkeypatch.setattr(giro.stock_mod, "carica_soglie", lambda: yaml.safe_load("clienti: ["))
    mandate = []
    giro.stock(lambda _: ClienteFinto(), ["ECM"], oggi=date(2026, 9, 11),
               adesso=datetime(2026, 9, 11, 11, 0, tzinfo=timezone.utc),
               manda=lambda o, html: mandate.append(html) or "agenti@x.it")
    assert len(mandate) == 1
    assert "ECM" in mandate[0] and "ParserError" in mandate[0]


def test_un_clienti_yaml_rotto_esce_con_errore_parlante_non_traceback(monkeypatch, capsys):
    import yaml
    monkeypatch.setattr(giro.clienti, "elenco", lambda: yaml.safe_load("a: ["))
    assert giro.main(["stock"]) == 1
    assert "ERRORE:" in capsys.readouterr().err


def test_l_elenco_corrieri_resta_sul_desktop_anche_con_un_nome_cliente_strano(tmp_path):
    """Collaudo del 06/09: il file finiva in $HOME, e _menu lo cancella pure."""
    percorso = giro._scrivi_elenco_corrieri("Phy to Pet/../../", "wf1", [])
    assert os.path.dirname(percorso) == str(tmp_path / "Desktop")
    assert os.path.isfile(percorso)


# ------------------------------------------------------------------ tracking ----

_SID = "sh5692f5c9"
_PIANO = "wf5545452f"


def _cartoni_ecm(quanti: int = 3, msku: str = "Germ_10x200_FBA") -> list[dict]:
    return [{"boxId": f"FBA15MBX78RC{n:06d}", "items": [{"msku": msku, "quantity": 6}]}
            for n in range(1, quanti + 1)]


def _client_tracking(cartoni, gia_tracciati=None, dopo=None):
    """Client finto che risponde per path: getShipment e listShipmentBoxes.
    `dopo` sono i tracking che Amazon riporta alla rilettura di verifica."""
    def _spd(mappa):
        return [{"boxId": c["boxId"], **({"trackingId": mappa[c["boxId"]]} if c["boxId"] in mappa else {})}
                for c in cartoni]
    client = Mock()
    letture = {"n": 0}

    def _get(path, params=None):
        if path.endswith("/boxes"):
            return {"boxes": cartoni}
        letture["n"] += 1
        mappa = gia_tracciati or {}
        if letture["n"] > 1 and dopo is not None:
            mappa = dopo
        return {"shipmentId": _SID, "shipmentConfirmationId": "FBA15MBX78RC",
                "status": "READY_TO_SHIP",
                "trackingDetails": {"spdTrackingDetail": {"spdTrackingItems": _spd(mappa)}}}

    client.get.side_effect = _get
    client.put.return_value = {"operationId": "ca54a5fd"}
    client.attendi_operazione.return_value = {"operationStatus": "SUCCESS"}
    return client


def test_tracking_anteprima_non_scrive_e_mostra_ogni_cartone_col_suo_codice(monkeypatch, tmp_path):
    """Il cancello 🟡: senza `--vai` nessuna PUT, e il testo deve nominare i
    cartoni uno per uno — è l'unico punto in cui ci si accorge che un codice è
    finito sul collo sbagliato."""
    monkeypatch.setenv("HOME", str(tmp_path))
    client = _client_tracking(_cartoni_ecm(3))
    testo = giro.tracking(client, _PIANO, "ECM", "2026-09-03#2#06ebd113", [_SID],
                          {"Germ_10x200_FBA": ["0922943001", "0922943002", "0922943003"]})
    client.put.assert_not_called()
    assert "FBA15MBX78RC000001" in testo and "0922943001" in testo
    assert "FBA15MBX78RC000003" in testo and "0922943003" in testo
    assert "--vai" in testo


def test_tracking_anteprima_avvisa_che_la_spedizione_passera_a_spedita(monkeypatch, tmp_path):
    """Dichiarare i tracking NON è una scrittura tecnica: porta la spedizione
    da READY_TO_SHIP a SHIPPED. Va fatto quando i colli sono davvero usciti."""
    monkeypatch.setenv("HOME", str(tmp_path))
    client = _client_tracking(_cartoni_ecm(1))
    testo = giro.tracking(client, _PIANO, "ECM", "2026-09-03#2#06ebd113", [_SID],
                          {"Germ_10x200_FBA": ["0922943001"]})
    assert "SHIPPED" in testo


def test_tracking_con_vai_dichiara_verifica_e_registra(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    cartoni = _cartoni_ecm(2)
    codici = {"FBA15MBX78RC000001": "0922943001", "FBA15MBX78RC000002": "0922943002"}
    client = _client_tracking(cartoni, dopo=codici)
    registrati = []
    monkeypatch.setattr(giro.registro, "registra_tracking",
                        lambda *a: registrati.append(a))
    testo = giro.tracking(client, _PIANO, "ECM", "2026-09-03#2#06ebd113", [_SID],
                          {"Germ_10x200_FBA": ["0922943001", "0922943002"]}, vai=True)
    assert client.put.call_count == 1
    assert client.put.call_args.kwargs["json_body"]["trackingDetails"]["spdTrackingDetail"][
        "spdTrackingItems"] == [{"boxId": "FBA15MBX78RC000001", "trackingId": "0922943001"},
                                {"boxId": "FBA15MBX78RC000002", "trackingId": "0922943002"}]
    assert registrati == [("ECM", "2026-09-03#2#06ebd113", _SID, codici)]
    assert "2 su 2" in testo


def test_tracking_dice_quando_amazon_non_li_riporta_tutti(monkeypatch, tmp_path):
    """La PUT può tornare SUCCESS e Amazon riportarne meno: la verifica serve
    a non dichiarare fatto un lavoro a metà."""
    monkeypatch.setenv("HOME", str(tmp_path))
    cartoni = _cartoni_ecm(2)
    client = _client_tracking(cartoni, dopo={"FBA15MBX78RC000001": "0922943001"})
    monkeypatch.setattr(giro.registro, "registra_tracking", lambda *a: None)
    testo = giro.tracking(client, _PIANO, "ECM", "2026-09-03#2#06ebd113", [_SID],
                          {"Germ_10x200_FBA": ["0922943001", "0922943002"]}, vai=True)
    assert "1 su 2" in testo
    assert "FBA15MBX78RC000002" in testo


def test_tracking_su_piano_diviso_senza_spedizione_si_ferma(monkeypatch, tmp_path):
    """Le lettere di vettura arrivano per prodotto, non per spedizione: su un
    piano diviso non c'è modo di indovinare a quale metà appartengono."""
    monkeypatch.setenv("HOME", str(tmp_path))
    client = _client_tracking(_cartoni_ecm(1))
    with pytest.raises(ValueError) as e:
        giro.tracking(client, _PIANO, "ECM", "2026-09-03#2#06ebd113", ["shA", "shB"],
                      {"Germ_10x200_FBA": ["0922943001"]})
    assert "shA" in str(e.value) and "shB" in str(e.value)
    client.get.assert_not_called()


def test_tracking_spedizione_estranea_al_piano_si_ferma(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    client = _client_tracking(_cartoni_ecm(1))
    with pytest.raises(ValueError, match="shX"):
        giro.tracking(client, _PIANO, "ECM", "2026-09-03#2#06ebd113", [_SID],
                      {"Germ_10x200_FBA": ["A"]}, shipment_id="shX")
    client.get.assert_not_called()


def test_cli_tracking_dalla_riga_di_comando(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(giro.giri, "carica", lambda pid: {
        "cliente": "ECM", "riferimento": "2026-09-03#2#06ebd113",
        "scelte": {"trasporto": {_SID: "to1"}}})
    client = _client_tracking(_cartoni_ecm(2))
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda c: client)
    visti = {}
    monkeypatch.setattr(giro, "tracking",
                        lambda *a, **k: visti.update(args=a, kwargs=k) or "ok")
    assert giro.main(["tracking", _PIANO, "--tracking", "Germ_10x200_FBA=0922943001,0922943002"]) == 0
    assert visti["args"][5] == {"Germ_10x200_FBA": ["0922943001", "0922943002"]}
    assert visti["kwargs"]["vai"] is False


def test_cli_tracking_da_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / "tracking.yaml"
    f.write_text("Germ_10x200_FBA:\n  - 0922943001\n  - 0922943002\n")
    monkeypatch.setattr(giro.giri, "carica", lambda pid: {
        "cliente": "ECM", "riferimento": "2026-09-03#2#06ebd113",
        "scelte": {"trasporto": {_SID: "to1"}}})
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda c: Mock())
    visti = {}
    monkeypatch.setattr(giro, "tracking",
                        lambda *a, **k: visti.update(args=a, kwargs=k) or "ok")
    assert giro.main(["tracking", _PIANO, "--file", str(f), "--vai"]) == 0
    assert visti["args"][5] == {"Germ_10x200_FBA": ["0922943001", "0922943002"]}
    assert visti["kwargs"]["vai"] is True


def test_cli_tracking_riga_e_file_insieme_si_ferma(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(giro.giri, "carica", lambda pid: {
        "cliente": "ECM", "riferimento": "2026-09-03#2#06ebd113", "scelte": {}})
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda c: Mock())
    assert giro.main(["tracking", _PIANO, "--tracking", "A=1", "--file", "x.yaml"]) == 1
    assert "--file" in capsys.readouterr().err


def test_cli_tracking_lo_stesso_sku_due_volte_si_ferma(monkeypatch, tmp_path, capsys):
    """Due `--tracking` sullo stesso SKU: silenziosamente il secondo vincerebbe
    e metà dei codici sparirebbe senza che nessuno se ne accorga."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(giro.giri, "carica", lambda pid: {
        "cliente": "ECM", "riferimento": "2026-09-03#2#06ebd113", "scelte": {}})
    monkeypatch.setattr(giro.spapi_client, "crea_client", lambda c: Mock())
    assert giro.main(["tracking", _PIANO, "--tracking", "A=1", "--tracking", "A=2"]) == 1
    assert "A" in capsys.readouterr().err


# ----------------------------------------------------------------- stima ----

_TESTA_RESTOCK = ("Country\tProduct Name\tFNSKU\tMerchant SKU\tASIN\tCondition\tSupplier\t"
                  "Units Sold Last 30 Days\tTotal Units\tInbound\tAvailable\tAlert\t"
                  "Recommended replenishment qty\tRecommended ship date")


def _restock(*righe):
    return "\n".join([_TESTA_RESTOCK] + [
        f"IT\tP\tX0\t{sku}\tB0{sku}\tNew\t--\t{v}\t0\t{inb}\t{disp}\t\t{repl}\t" for sku, v, inb, disp, repl in righe
    ]) + "\n"


def test_stima_scrive_excel_e_bozza_a_colli_pieni(tmp_path, monkeypatch):
    (tmp_path / "Desktop").mkdir()
    c = ClienteFinto()
    c.prepara_report(_restock(("Reladol_300", 0, 0, 10, 0), ("FERMO", 0, 0, 5, 0), ("NUOVO", 20, 0, 0, 30)))
    c.spedito("wf1", "2026-05-14T10:00:00Z",
              [scatola_finta("Reladol_300", 22, 9.5, 38, 51, 47, modello="Reladol 300")])
    serie = {"Reladol_300": [14] * 13, "FERMO": [0] * 13, "NUOVO": [7] * 13}
    monkeypatch.setattr(giro.stima_mod, "settimane_vendute", lambda client, sku, *a, **k: serie[sku])
    monkeypatch.setattr(giro.stock_mod, "_attendi", lambda client, rid, attesa, pausa: "doc-1")
    adesso = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
    testo = giro.stima(c, "ECM", giorni=30, transito=10, scorta=14, adesso=adesso, pausa=lambda _: None)
    # Reladol: 2/gg × 54 = 108 − 10 = 98 → 5 colli da 22 = 110
    assert "Reladol_300: 2.00 pz/gg" in testo and "5 × 22 pz (Reladol 300) = 110 pz" in testo
    assert "NUOVO" in testo and "fuori: NUOVO, senza modello" in testo
    assert "FERMO:" not in testo
    assert (tmp_path / "Desktop" / "Stima-FBA-ECM-2026-11-04.xlsx").exists()
    bozza = (tmp_path / "Desktop" / "Lotto-ECM-2026-11-04.yaml").read_text()
    assert "quantita: 110" in bozza and "NUOVO" not in bozza
    assert all(path == "/reports/2021-06-30/reports" for path, _ in c.post_fatte)   # solo il report


def test_stima_sku_sconosciuto_lo_dice(monkeypatch):
    c = ClienteFinto()
    c.prepara_report(_restock(("A", 1, 0, 1, 0)))
    monkeypatch.setattr(giro.stock_mod, "_attendi", lambda client, rid, attesa, pausa: "doc-1")
    with pytest.raises(ValueError, match="INVENTATO"):
        giro.stima(c, "ECM", 30, solo=["INVENTATO"], adesso=datetime(2026, 11, 4, tzinfo=timezone.utc))
