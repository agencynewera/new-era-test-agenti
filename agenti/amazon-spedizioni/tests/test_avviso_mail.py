import time
from datetime import date, datetime, timezone

import pytest

import avviso_mail
import stato_stock
import stock

OGGI = date(2026, 9, 5)


def _avviso(sku, motivo="nuovo", zona=stock.RIASSORTIRE, disponibili=3, in_arrivo=22,
            venduti=15, consigliati=35, entro="09/12/2026"):
    riga = stock.RigaStock(sku=sku, asin="B0TEST", disponibili=disponibili, in_arrivo=in_arrivo,
                           venduti_30gg=venduti, consigliati=consigliati, entro=entro,
                           alert_amazon=None)
    return stato_stock.Avviso(sku, zona, motivo, riga)


def test_oggetto_conta_gli_sku_di_un_cliente_solo():
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("Reladol_300"), _avviso("CALMAPROST_FBA")],
                                  bozza=None, candidati=[], saltati=[], errore=None)]
    assert avviso_mail.oggetto(blocchi) == "[STOCK] ECM — 2 SKU da riassortire"


def test_oggetto_con_piu_clienti_li_nomina():
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("A")], None, [], [], None),
               avviso_mail.Blocco("Arcipelago", [_avviso("B")], None, [], [], None)]
    assert avviso_mail.oggetto(blocchi) == "[STOCK] Arcipelago, ECM — 2 SKU da riassortire"


def test_il_corpo_porta_i_numeri_che_servono_a_decidere():
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("Reladol_300")],
                                  bozza="/Users/x/Desktop/Lotto-ECM-2026-09-05.yaml",
                                  candidati=[], saltati=[], errore=None)]
    html = avviso_mail.corpo(blocchi, OGGI)
    for atteso in ("Reladol_300", "3", "22", "15", "50", "35", "12/09/2026",
                   "Lotto-ECM-2026-09-05.yaml"):
        assert atteso in html


def test_lo_sku_esaurito_non_stampa_una_copertura_infinita():
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("GermGEL_5L_FBA", motivo="nuovo",
                                                  zona=stock.CRITICO, disponibili=0, in_arrivo=0,
                                                  venduti=0, consigliati=5)],
                                  None, [], [], None)]
    html = avviso_mail.corpo(blocchi, OGGI)
    assert "inf" not in html.lower()
    assert "esaurito" in html.lower()


def test_il_cliente_vario_riceve_i_candidati_e_il_comando():
    blocchi = [avviso_mail.Blocco("Bedshire", [_avviso("PILLOW-FBA")], bozza=None,
                                  candidati=[("PILLOW-FBA", "PILLOW 10 PZ (6 scatole, 2 piani)")],
                                  saltati=[], errore=None)]
    html = avviso_mail.corpo(blocchi, OGGI)
    assert "PILLOW 10 PZ" in html
    assert "giro.py scatole" in html and "--modello" in html


def test_un_cliente_saltato_e_scritto_non_taciuto():
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("A")], None, [], [], None),
               avviso_mail.Blocco("Arcipelago", [], None, [], [], "report chiuso in stato FATAL")]
    html = avviso_mail.corpo(blocchi, OGGI)
    assert "Arcipelago" in html and "FATAL" in html


def test_oggetto_quando_tutti_i_clienti_falliscono_non_dice_zero():
    """Task 7: 'ECM, Arcipelago' saltati non e' 'niente da fare' — l'oggetto
    deve dirlo, non contare zero SKU su chi non e' stato nemmeno letto."""
    blocchi = [avviso_mail.Blocco("ECM", [], None, [], [], "report chiuso in stato FATAL"),
               avviso_mail.Blocco("Arcipelago", [], None, [], [], "credenziali assenti")]
    oggetto = avviso_mail.oggetto(blocchi)
    assert "0 SKU da riassortire" not in oggetto
    assert "ECM" in oggetto and "Arcipelago" in oggetto


def test_la_riga_della_bozza_nomina_il_modello_scelto():
    """Task 8: §14 promette 'modello scelto o candidati' — chi legge la mail
    deve sapere che scatola e' senza aprire il file."""
    blocchi = [avviso_mail.Blocco("Arcipelago", [_avviso("138_FBA")],
                                  bozza="/Users/x/Desktop/Lotto-Arcipelago-2026-09-05.yaml",
                                  candidati=[], saltati=[], errore=None,
                                  modelli_scelti=[("138_FBA", "PALLET 36 PACCHI")])]
    html = avviso_mail.corpo(blocchi, OGGI)
    assert "PALLET 36 PACCHI" in html


def test_manda_usa_il_ripiego_quando_il_destinatario_rifiuta():
    import smtplib
    tentativi = []

    def spedisci(config, destinatario, msg):
        tentativi.append(destinatario)
        if destinatario == "agenti@agencynewera.it":
            raise smtplib.SMTPRecipientsRefused({destinatario: (550, b"no such user")})

    config = {"smtp_host": "smtp.ionos.it", "smtp_port": 587, "mittente": "promozione@x.it",
              "password": "segreta", "destinatario": "agenti@agencynewera.it",
              "ripiego": "promozione@agencynewera.it"}
    usato = avviso_mail.manda(config, "oggetto", "<p>ciao</p>", spedisci=spedisci)
    assert tentativi == ["agenti@agencynewera.it", "promozione@agencynewera.it"]
    assert usato == "promozione@agencynewera.it"


def test_manda_ricostruisce_il_messaggio_per_ogni_destinatario():
    """Il messaggio non si ricicla fra un tentativo e l'altro: passa dal template
    condiviso degli agenti — che allega il logo, quindi e' multipart — e ogni copia
    porta il SUO destinatario. Riciclando un MIMEText solo, il ripiego riceveva un
    messaggio col To gia' riscritto e senza logo."""
    import smtplib
    messaggi = []

    def spedisci(config, destinatario, msg):
        messaggi.append((destinatario, msg))
        if destinatario == "agenti@agencynewera.it":
            raise smtplib.SMTPRecipientsRefused({destinatario: (550, b"no such user")})

    config = {"smtp_host": "smtp.ionos.it", "smtp_port": 587, "mittente": "promozione@x.it",
              "password": "segreta", "destinatario": "agenti@agencynewera.it",
              "ripiego": "promozione@agencynewera.it"}
    avviso_mail.manda(config, "[STOCK] oggetto", "<p>ciao</p>", spedisci=spedisci)

    assert [d for d, _ in messaggi] == ["agenti@agencynewera.it",
                                        "promozione@agencynewera.it"]
    for destinatario, msg in messaggi:
        assert msg["To"] == destinatario
        assert msg["From"] == "promozione@x.it"
        assert msg["Subject"] == "[STOCK] oggetto"
        assert msg.is_multipart(), "senza il template il logo non viaggia"


def test_config_assente_lo_dice_invece_di_esplodere(tmp_path):
    assert avviso_mail.carica_config(str(tmp_path / "manca.yaml")) == {}


def test_config_vuota_nomina_le_chiavi_mancanti_e_il_file():
    """Task 6: mail.yaml non esiste ancora, carica_config() torna {}, e senza
    controllo manda() esplodeva con KeyError('mittente') non catturato da
    giro.main (che cattura ValueError)."""
    with pytest.raises(ValueError) as errore:
        avviso_mail.manda({}, "oggetto", "<p>ciao</p>")
    testo = str(errore.value)
    for atteso in ("smtp_host", "smtp_port", "mittente", "password", "destinatario",
                   "mail.yaml"):
        assert atteso in testo


def test_senza_il_template_condiviso_la_mail_fallisce_in_chiaro():
    """La cartella del template sta fuori da questa skill e puo' sparire: deve
    dirlo quando si compone la mail, non spegnere giro.py al caricamento."""
    finto = avviso_mail._TemplateAssente("/nessuna/cartella", "No module named 'template'")
    with pytest.raises(ValueError, match="template delle mail degli agenti"):
        finto.oggetto("STOCK", "ECM", "7 SKU")


def test_la_pastiglia_dice_da_quanto_non_quanto_e_grave():
    """Gravita' e storia sono informazioni diverse: il colore e la copertura dicono
    la prima, la pastiglia la seconda. Un esaurito segnalato oggi legge "nuovo oggi",
    non "da riassortire" (che in questa mail vale per tutte le righe)."""
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("GermGEL_5L_FBA", motivo="nuovo",
                                                  zona=stock.CRITICO, disponibili=0, in_arrivo=0),
                                          _avviso("Reladol_300", motivo="sollecito")],
                                  None, [], [], None)]
    html = avviso_mail.corpo(blocchi, OGGI)
    assert "nuovo oggi" in html and "ancora fermo" in html
    assert "da riassortire" not in html



def test_la_password_si_prende_dove_gia_sta(tmp_path):
    """Una credenziale sola, in un posto solo: `password_da` punta al file che
    ce l'ha invece di duplicarla."""
    sorgente = tmp_path / "sentinella.yaml"
    sorgente.write_text('mittente: "x@y.it"\npassword: "segreta"\n')
    mio = tmp_path / "mail.yaml"
    mio.write_text(f'mittente: "promozione@agencynewera.it"\npassword_da: "{sorgente}"\n')
    config = avviso_mail.carica_config(str(mio))
    assert config["password"] == "segreta"
    assert "password_da" not in config


def test_password_da_che_punta_a_niente_lo_dice(tmp_path):
    mio = tmp_path / "mail.yaml"
    mio.write_text('password_da: "/nessun/file.yaml"\n')
    with pytest.raises(ValueError, match="non esiste"):
        avviso_mail.carica_config(str(mio))


def test_starttls_verifica_il_certificato_del_server(monkeypatch):
    """Senza un contesto esplicito, smtplib su Python 3.9 accetta qualsiasi
    certificato (CERT_NONE): la password IONOS viaggerebbe su un canale
    cifrato ma non autenticato."""
    import smtplib
    import ssl
    from email.mime.text import MIMEText
    catturato = {}

    class SMTPFinto:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self, context=None):
            catturato["context"] = context

        def login(self, *_):
            pass

        def sendmail(self, *_):
            pass

    monkeypatch.setattr(smtplib, "SMTP", SMTPFinto)
    avviso_mail._spedisci({"smtp_host": "h", "smtp_port": "587", "mittente": "a@x.it",
                           "password": "s"}, "b@x.it", MIMEText("x"))
    contesto = catturato["context"]
    assert contesto is not None
    assert contesto.verify_mode == ssl.CERT_REQUIRED
    assert contesto.check_hostname is True


def test_il_file_della_password_leggibile_da_altri_si_segnala_ma_non_ferma(tmp_path):
    """Opzione B (Mariano, 2026-09-11): permessi larghi sul file della password
    non fermano la mail — partirebbe un silenzio che nessuno nota. Si carica
    lo stesso e si segna il file, perche' la mail lo dica a chi legge."""
    sorgente = tmp_path / "sentinella.yaml"
    sorgente.write_text('password: "segreta"\n')
    sorgente.chmod(0o644)
    mio = tmp_path / "mail.yaml"
    mio.write_text(f'mittente: "x@y.it"\npassword_da: "{sorgente}"\n')
    config = avviso_mail.carica_config(str(mio))
    assert config["password"] == "segreta"
    assert config["esposti"] == [str(sorgente)]

    sorgente.chmod(0o600)
    assert "esposti" not in avviso_mail.carica_config(str(mio))


def test_anche_la_password_scritta_nel_file_stesso_va_protetta(tmp_path):
    mio = tmp_path / "mail.yaml"
    mio.write_text('mittente: "x@y.it"\npassword: "segreta"\n')
    mio.chmod(0o644)
    assert avviso_mail.carica_config(str(mio))["esposti"] == [str(mio)]


def test_la_mail_chiede_di_stringere_i_permessi_del_file_della_password():
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("A")], None, [], [], None)]
    percorso = "/Users/x/.claude/skills/amazon-ads-sentinel/mail.yaml"
    html = avviso_mail.corpo(blocchi, OGGI, esposti=[percorso])
    assert "Serve da te" in html
    assert "leggibile da altri" in html and "chmod 600" in html and percorso in html
    assert "chmod" not in avviso_mail.corpo(blocchi, OGGI)


def test_la_data_entro_si_legge_all_italiana():
    """Il report Restock scrive la data consigliata all'americana (09/23/2026):
    letta in Italia è il 9 del mese 23. Come "Data pronta dichiarata ad Amazon"
    va in gg/mm/aaaa (visto dal vivo su ECM il 16/09/2026)."""
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("Reladol_300", entro="09/23/2026")],
                                  None, [], [], None)]
    html = avviso_mail.corpo(blocchi, OGGI)
    assert "23/09/2026" in html
    assert "09/23/2026" not in html


@pytest.fixture
def fuso_roma():
    """Il test non deve dipendere dal fuso del Mac che lo esegue."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TZ", "Europe/Rome")
        time.tzset()
        yield
    time.tzset()


def test_l_ora_nell_intestazione_e_locale_non_utc(fuso_roma):
    """Il giro passa `adesso` in UTC: alle 23:53 italiane la banda viola diceva
    21:53, mentre "Data pronta dichiarata ad Amazon" era già in ora locale."""
    blocchi = [avviso_mail.Blocco("ECM", [_avviso("A")], None, [], [], None)]
    html = avviso_mail.corpo(blocchi, date(2026, 9, 16),
                             quando=datetime(2026, 9, 16, 21, 53, tzinfo=timezone.utc))
    assert "16/09/2026 · 23:53" in html
    assert "21:53" not in html
