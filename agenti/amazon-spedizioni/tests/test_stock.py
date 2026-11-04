import pytest
from datetime import datetime, timezone

import stock
from cliente_finto import ClienteFinto

ADESSO = datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc)

INTESTAZIONE = ("Country\tProduct Name\tFNSKU\tMerchant SKU\tASIN\tCondition\tSupplier\t"
                "Units Sold Last 30 Days\tTotal Units\tInbound\tAvailable\tAlert\t"
                "Recommended replenishment qty\tRecommended ship date")


def _riga(paese, sku, venduti, inbound, disponibili, alert="", repl="", entro=""):
    return (f"{paese}\tProdotto\tX0\t{sku}\tB0TEST\tNew\t--\t{venduti}\t0\t{inbound}\t"
            f"{disponibili}\t{alert}\t{repl}\t{entro}")


def _tsv(*righe):
    return "\n".join([INTESTAZIONE, *righe]) + "\n"


def test_tiene_solo_la_riga_del_paese_del_cliente():
    """La trappola vera del report (DESIGN §14): una riga per paese, e le righe
    estere dicono out_of_stock su prodotti che in Italia sono pieni."""
    c = ClienteFinto()
    c.prepara_report(_tsv(
        _riga("GB", "Reladol_100", 0, 0, 0, alert="out_of_stock"),
        _riga("IT", "Reladol_100", 55, 60, 47),
    ))
    righe = stock.leggi_stock(c, "IT", ADESSO, marketplace_id="APJ6JRA9NG5V4", pausa=lambda _: None)
    assert [r.sku for r in righe] == ["Reladol_100"]
    assert (righe[0].disponibili, righe[0].in_arrivo, righe[0].venduti_30gg) == (47, 60, 55)
    assert righe[0].alert_amazon is None


def test_copertura_e_infinita_senza_vendite():
    """Uno SKU esaurito vende zero: la copertura non lo vede, e deve dirlo
    invece di dividere per zero."""
    c = ClienteFinto()
    c.prepara_report(_tsv(_riga("IT", "GermGEL_5L_FBA", 0, 0, 0, alert="out_of_stock", repl="5")))
    r = stock.leggi_stock(c, "IT", ADESSO, marketplace_id="APJ6JRA9NG5V4", pausa=lambda _: None)[0]
    assert r.copertura_giorni == float("inf")
    assert (r.consigliati, r.alert_amazon) == (5, "out_of_stock")


def test_copertura_conta_anche_la_merce_in_arrivo():
    c = ClienteFinto()
    c.prepara_report(_tsv(_riga("IT", "Reladol_300", 15, 22, 3, repl="35", entro="09/12/2026")))
    r = stock.leggi_stock(c, "IT", ADESSO, marketplace_id="APJ6JRA9NG5V4", pausa=lambda _: None)[0]
    assert r.copertura_giorni == pytest.approx(50.0)
    assert (r.consigliati, r.entro) == (35, "09/12/2026")


def test_report_fallito_non_diventa_una_lista_vuota():
    """Un report FATAL letto come 'nessuno SKU sotto soglia' sarebbe un
    silenzio bugiardo: deve alzare."""
    c = ClienteFinto()
    c.prepara_report(_tsv(), stato="FATAL")
    with pytest.raises(stock.ReportFallito, match="FATAL"):
        stock.leggi_stock(c, "IT", ADESSO, marketplace_id="APJ6JRA9NG5V4", pausa=lambda _: None)


def test_report_che_non_finisce_scade():
    c = ClienteFinto()
    c.prepara_report(_tsv(), stato="IN_PROGRESS")
    with pytest.raises(stock.ReportFallito, match="non pronto"):
        stock.leggi_stock(c, "IT", ADESSO, marketplace_id="APJ6JRA9NG5V4", attesa_max_s=30, pausa=lambda _: None)


def test_chiede_il_report_giusto_per_il_marketplace_giusto():
    c = ClienteFinto()
    c.prepara_report(_tsv(_riga("IT", "SKU-1", 10, 0, 100)))
    stock.leggi_stock(c, "IT", ADESSO, marketplace_id="APJ6JRA9NG5V4", pausa=lambda _: None)
    assert c.report_creati == [{"reportType": "GET_RESTOCK_INVENTORY_RECOMMENDATIONS_REPORT",
                                "marketplaceIds": ["APJ6JRA9NG5V4"]}]


def test_il_documento_del_report_si_scarica_con_agent_nello_user_agent(monkeypatch):
    """Agent Policy Amazon (BSA sez. 19): l'URL del documento non passa da
    spapi_client, e urlopen senza Request uscirebbe come Python-urllib."""
    import urllib.request
    import spapi_client
    viste = []
    vero_urlopen = urllib.request.urlopen

    def spia(richiesta, timeout):
        viste.append(richiesta)
        return vero_urlopen(richiesta, timeout=timeout)

    monkeypatch.setattr(stock.urllib.request, "urlopen", spia)
    c = ClienteFinto()
    c.prepara_report(_tsv(_riga("IT", "SKU-1", 10, 0, 100)))
    stock.leggi_stock(c, "IT", ADESSO, marketplace_id="APJ6JRA9NG5V4", pausa=lambda _: None)

    assert len(viste) == 1
    assert isinstance(viste[0], urllib.request.Request)
    assert viste[0].get_header("User-agent") == spapi_client._USER_AGENT
    assert "Agent/" in viste[0].get_header("User-agent")


def _r(sku="SKU-1", disponibili=0, in_arrivo=0, venduti=0, consigliati=0):
    return stock.RigaStock(sku=sku, asin="B0TEST", disponibili=disponibili, in_arrivo=in_arrivo,
                           venduti_30gg=venduti, consigliati=consigliati, entro=None, alert_amazon=None)


def test_il_consiglio_di_amazon_e_il_segnale_primario():
    """Reladol_300 il 05/09: 3 pezzi, 22 in arrivo, 15 vendite → 50 giorni di
    copertura, sopra soglia. Amazon chiede 35 pezzi perché conosce i tempi di
    trasporto, che noi non possiamo calcolare (DESIGN §14)."""
    riga = _r(disponibili=3, in_arrivo=22, venduti=15, consigliati=35)
    assert riga.copertura_giorni > 30
    assert stock.zona(riga, soglia_giorni=30) == stock.RIASSORTIRE


def test_esaurito_con_consiglio_aperto_e_critico():
    assert stock.zona(_r(consigliati=5), soglia_giorni=30) == stock.CRITICO


def test_esaurito_senza_vendite_e_senza_consiglio_tace():
    """Uno SKU che non vende e che nessuno rifornisce non si riassortisce."""
    assert stock.zona(_r(), soglia_giorni=30) == stock.OK


def test_copertura_e_la_rete_di_sicurezza():
    assert stock.zona(_r(disponibili=6, venduti=7), soglia_giorni=30) == stock.RIASSORTIRE
    assert stock.zona(_r(disponibili=100, venduti=7), soglia_giorni=30) == stock.OK


def test_sotto_la_soglia_dura_e_critico():
    assert stock.zona(_r(disponibili=1, venduti=30), soglia_giorni=30) == stock.CRITICO


def test_paese_di_i_quattro_marketplace_noti():
    assert stock.paese_di("APJ6JRA9NG5V4") == "IT"
    assert stock.paese_di("A1PA6795UKMFR9") == "DE"
    assert stock.paese_di("A13V1IB3VIYZZH") == "FR"
    assert stock.paese_di("A1RKKUPIHCS9HS") == "ES"


def test_paese_di_un_marketplace_sconosciuto_alza_errore_chiaro():
    with pytest.raises(ValueError, match="A1F83G8C2ARO7P"):
        stock.paese_di("A1F83G8C2ARO7P")


def test_la_soglia_per_sku_batte_quella_del_cliente_che_batte_il_default():
    soglie = {"default_giorni": 30, "clienti": {"ECM": {"default_giorni": 45,
                                                        "sku": {"Reladol_100": 60}}}}
    assert stock.soglia_per(soglie, "ECM", "Reladol_100") == 60
    assert stock.soglia_per(soglie, "ECM", "Altro") == 45
    assert stock.soglia_per(soglie, "Bedshire", "Altro") == 30
    assert stock.soglia_per(soglie, "ecm", "Reladol_100") == 60  # come clienti._chiave


def _rz(sku, asin, venduti, consigliati=5, disponibili=0, in_arrivo=0):
    return stock.RigaStock(sku=sku, asin=asin, disponibili=disponibili, in_arrivo=in_arrivo,
                           venduti_30gg=venduti, consigliati=consigliati, entro=None, alert_amazon=None)


def test_chiede_le_vendite_di_novanta_giorni_sull_intervallo_giusto():
    c = ClienteFinto()
    c.vendite = {"B0VIVO": 36}
    assert stock.unita_vendute(c, "B0VIVO", "APJ6JRA9NG5V4", ADESSO) == 36
    chiesto = c.vendite_chieste[0]
    assert chiesto["interval"] == "2026-06-07T00:00:00Z--2026-09-05T00:00:00Z"
    assert chiesto["granularity"] == "Total"
    assert chiesto["marketplaceIds"] == "APJ6JRA9NG5V4"


def test_se_le_vendite_non_si_leggono_non_si_inventa_uno_zero():
    """Uno zero inventato zittirebbe un avviso vero: meglio dire 'non lo so'."""
    class Muto:
        def get(self, path, params=None):
            raise stock.SpApiError("503 dalle vendite")
    assert stock.unita_vendute(Muto(), "B0X", "APJ6JRA9NG5V4", ADESSO) is None


def test_tace_sullo_sku_che_non_vende_da_novanta_giorni():
    """Il caso vero di ECM: Amazon consiglia di rifornire GermGEL_5L_FBA, ma
    non ne vende un pezzo da tre mesi (Mariano, 2026-09-05)."""
    c = ClienteFinto()
    c.vendite = {"B0MORTO": 0, "B0VIVO": 36}
    zone = [(_rz("GermGEL_5L_FBA", "B0MORTO", 0), stock.CRITICO),
            (_rz("Reladol_300", "B0VIVO", 0), stock.CRITICO)]
    filtrate, dormienti = stock.filtra_dormienti(c, zone, "APJ6JRA9NG5V4", ADESSO)
    assert dict((r.sku, z) for r, z in filtrate) == {"GermGEL_5L_FBA": stock.OK,
                                                     "Reladol_300": stock.CRITICO}
    assert dormienti == ["GermGEL_5L_FBA"]


def test_chi_ha_venduto_nell_ultimo_mese_non_si_controlla_nemmeno():
    """Una chiamata in piu' solo per i dubbi: chi vende e' gia' vivo."""
    c = ClienteFinto()
    zone = [(_rz("Reladol_300", "B0VIVO", 15), stock.RIASSORTIRE)]
    filtrate, dormienti = stock.filtra_dormienti(c, zone, "APJ6JRA9NG5V4", ADESSO)
    assert filtrate == zone and dormienti == []
    assert c.vendite_chieste == []


def test_vendite_illeggibili_lasciano_passare_l_avviso():
    class Muto(ClienteFinto):
        def get(self, path, params=None):
            if path == "/sales/v1/orderMetrics":
                raise stock.SpApiError("503")
            return super().get(path, params)
    zone = [(_rz("Dubbio", "B0X", 0), stock.CRITICO)]
    filtrate, dormienti = stock.filtra_dormienti(Muto(), zone, "APJ6JRA9NG5V4", ADESSO)
    assert filtrate == zone and dormienti == []


def test_il_429_sui_report_aspetta_il_minuto_che_serve():
    """La quota di createReport si ricarica a circa una richiesta al minuto: il
    backoff generico (15 s in tutto) non ce la fa mai, e il cliente sparisce dal
    giro. Qui si aspetta di piu', e solo per i report."""
    attese = []

    class Stufo(ClienteFinto):
        def __init__(self):
            super().__init__()
            self.tentativi = 0

        def post(self, path, json_body=None, params=None):
            if path.endswith("/reports"):
                self.tentativi += 1
                if self.tentativi < 3:
                    raise stock.SpApiError("429 su /reports/2021-06-30/reports: rate limit")
            return super().post(path, json_body, params)

    c = Stufo()
    c.prepara_report(_tsv(_riga("IT", "SKU-1", 10, 0, 100)))
    righe = stock.leggi_stock(c, "IT", ADESSO, marketplace_id="APJ6JRA9NG5V4",
                              pausa=attese.append)
    assert [r.sku for r in righe] == ["SKU-1"]
    assert c.tentativi == 3
    assert 60 in attese, f"non ha aspettato un minuto fra i tentativi: {attese}"


def test_un_429_che_non_passa_mai_si_arrende_dicendolo():
    class Sempre(ClienteFinto):
        def post(self, path, json_body=None, params=None):
            raise stock.SpApiError("429 su /reports/2021-06-30/reports: rate limit")

    with pytest.raises(stock.ReportFallito, match="quota"):
        stock.leggi_stock(Sempre(), "IT", ADESSO, marketplace_id="APJ6JRA9NG5V4",
                          pausa=lambda _: None)
