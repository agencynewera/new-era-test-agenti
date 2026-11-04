"""La mail del giro dello stock (DESIGN §14). Mail e non Telegram: la copertura
si misura in giorni, e una mail si inoltra a chi prepara la spedizione."""
from __future__ import annotations
import os
import smtplib
import ssl
import sys
from dataclasses import dataclass, field
from datetime import date, datetime

import yaml

import stock

# Il template unico delle mail degli agenti vive fuori da questa skill: la stessa
# impaginazione la useranno la sentinella Ads e gli agenti che verranno. Ma una
# cartella fuori dal progetto puo' sparire, e un ModuleNotFoundError qui in testa
# spegnerebbe `giro.py` INTERO — non solo il verbo `stock` — e con lui la suite di
# test. Quindi l'assenza si scopre quando si compone la mail, come ValueError che
# `giro.main` sa gia' stampare in chiaro (DESIGN §14).
_DOVE_TEMPLATE = os.path.expanduser("~/.claude/skills/agenti-mail/scripts")


class _TemplateAssente:
    """Sta al posto del template mancante e rimanda l'errore a chi lo usa."""

    def __init__(self, dove: str, errore: object) -> None:
        self._dove, self._errore = dove, errore

    def __getattr__(self, nome: str):
        raise ValueError(
            f"il template delle mail degli agenti non si carica da {self._dove} "
            f"({self._errore}): la mail dello stock non puo' essere composta")


sys.path.insert(0, _DOVE_TEMPLATE)
try:
    import template  # noqa: E402
except ImportError as e:                       # pragma: no cover - dipende dal disco
    template = _TemplateAssente(_DOVE_TEMPLATE, e)

CONFIG_DEFAULT = "~/.config/newera-amazon/spedizioni/mail.yaml"
_CHIAVI_OBBLIGATORIE = ("smtp_host", "smtp_port", "mittente", "password", "destinatario")
# La pastiglia dice da QUANTO, non quanto e' grave: la gravita' la dicono gia' il
# colore e la colonna copertura. "da riassortire" su una mail di riassortimento
# non aggiungeva niente, e su uno SKU esaurito era pure falso.
_MOTIVI = {"nuovo": "nuovo oggi", "peggiorato": "peggiorato",
           "sollecito": "ancora fermo"}


@dataclass
class Blocco:
    cliente: str
    avvisi: list
    bozza: str | None = None
    candidati: list = field(default_factory=list)
    saltati: list = field(default_factory=list)
    errore: str | None = None
    modelli_scelti: list = field(default_factory=list)   # (sku, nome modello)
    dormienti: list = field(default_factory=list)        # taciuti: non vendono da 90 giorni


def oggetto(blocchi: list[Blocco]) -> str:
    con_avvisi = sorted(b.cliente for b in blocchi if b.avvisi)
    if not con_avvisi:
        # Tutti i clienti sono saltati per errore: "0 SKU da riassortire" si
        # legge come "niente da fare", mentre e' il contrario (DESIGN §14).
        saltati = ", ".join(sorted(b.cliente for b in blocchi if b.errore)) or None
        return template.oggetto("STOCK", saltati, "giro fallito")
    quanti = sum(len(b.avvisi) for b in blocchi)
    return template.oggetto("STOCK", ", ".join(con_avvisi), f"{quanti} SKU da riassortire")


def _copertura(riga) -> str:
    """Un esaurito ha copertura infinita per costruzione: scriverlo come 'inf'
    sarebbe una presa in giro (DESIGN §14)."""
    if riga.disponibili + riga.in_arrivo == 0:
        return "esaurito"
    if riga.copertura_giorni == float("inf"):
        return "nessuna vendita"
    return f"{riga.copertura_giorni:.0f} gg"


def _sintesi(blocchi: list[Blocco]) -> str:
    """La frase che si legge nella notifica del telefono, prima di aprire."""
    quanti = sum(len(b.avvisi) for b in blocchi)
    clienti = [b.cliente for b in blocchi if b.avvisi]
    saltati = [b.cliente for b in blocchi if b.errore]
    if not quanti:
        testa = "Nessuno SKU sotto soglia"
    else:
        sku = "1 SKU è sceso" if quanti == 1 else f"{quanti} SKU sono scesi"
        dove = clienti[0] if len(clienti) == 1 else f"{len(clienti)} clienti"
        testa = f"{sku} sotto la soglia di copertura su {dove}"
    if not saltati:
        coda = ""
    elif len(saltati) == 1:
        coda = f"; {saltati[0]} non è stato controllato"
    else:
        coda = f"; {len(saltati)} clienti non controllati"
    return f"{testa}{coda}."


def azioni_richieste(blocchi: list[Blocco]) -> list[str]:
    """Cosa deve fare chi legge. Poche righe o il riquadro perde valore: una per
    bozza pronta, una per SKU rimasto senza modello di spedizione."""
    righe = []
    for b in blocchi:
        nome = template.escape(b.cliente)
        if b.bozza:
            righe.append(f"<b>{nome}</b>: completa " + template.codice("data_pronta") + " in "
                         f"{template.codice(b.bozza)} e lancia "
                         f"{template.codice('giro.py prepara <file>')}")
        for sku, _ in b.candidati:
            righe.append(f"<b>{nome}</b>: scegli il modello di spedizione per "
                         f"{template.codice(sku)} (vedi i candidati sotto)")
    return righe


def _entro_italiano(testo: str | None) -> str:
    """Il report Restock scrive la data consigliata all'americana (09/23/2026);
    chi legge la mail la vuole in gg/mm/aaaa. Se non si riconosce, resta com'è."""
    try:
        return f"{datetime.strptime(testo or '', '%m/%d/%Y'):%d/%m/%Y}"
    except ValueError:
        return testo or "—"


def _tabella_cliente(avvisi) -> str:
    righe = []
    for a in avvisi:
        r = a.riga
        semaforo = "rosso" if a.zona == stock.CRITICO else "giallo"
        stato = template.pastiglia(_MOTIVI.get(a.motivo, a.motivo), semaforo)
        righe.append([
            f'<div style="margin-bottom:4px">{template.escape(a.sku)}</div>{stato}',
            str(r.disponibili), str(r.in_arrivo), str(r.venduti_30gg), _copertura(r),
            str(r.consigliati or "—"), template.escape(_entro_italiano(r.entro)),
        ])
    return template.tabella(
        ["SKU", "disp.", "arrivo", "vend. 30gg", "copertura", "inviare", "entro"],
        righe, numeriche={1, 2, 3, 4, 5})


def corpo(blocchi: list[Blocco], oggi: date, quando: datetime | None = None,
          esposti=()) -> str:
    """`esposti`: i file della password leggibili da altri utenti (da
    `carica_config`); finiscono in "Serve da te", perche' chi legge li stringa."""
    sezioni, saltati = [], []
    for b in blocchi:
        if b.errore:
            saltati.append(f"{b.cliente}: {b.errore}")
            continue
        pezzi = [_tabella_cliente(b.avvisi)]
        for sku, testo in b.candidati:
            pezzi.append(template.paragrafo(
                f"{sku}: nessuna spedizione tipo. Modelli possibili — {testo}."))
            pezzi.append('<p style="margin:0 0 14px">Per la bozza: ' + template.codice(
                f'giro.py scatole "{b.cliente}" {sku}=<qta> --modello "<nome>"') + "</p>")
        for s in b.saltati:
            saltati.append(f"{b.cliente}: piano non letto — {s}")
        if b.bozza:
            modelli_testo = ", ".join(f"{sku}: {nome}" for sku, nome in b.modelli_scelti)
            extra = f" (modello {template.escape(modelli_testo)})" if modelli_testo else ""
            pezzi.append(f'<p style="margin:0 0 14px">Bozza pronta{extra}: '
                         + template.codice(b.bozza) + "</p>")
        sezioni.append(template.sezione(b.cliente, "".join(pezzi)))
    azioni = azioni_richieste(blocchi)
    azioni += [f"Il file della password {template.codice(f)} è leggibile da altri utenti: lancia "
               + template.codice(f'chmod 600 "{f}"') for f in esposti]
    return template.pagina(
        agente="Stock FBA",
        # `adesso` arriva UTC dal giro: la banda dice l'ora di chi legge, come
        # "Data pronta dichiarata ad Amazon" (giro._data_pronta_locale).
        quando=(quando or datetime(oggi.year, oggi.month, oggi.day)).astimezone(),
        sintesi=_sintesi(blocchi),
        azioni=azioni,
        corpo=sezioni,
        saltati=saltati,
        log="~/.claude/skills/amazon-spedizioni/logs/stock.out.log",
    )


def carica_config(percorso: str | None = None) -> dict:
    """La configurazione dell'invio. La password non si scrive qui: con
    `password_da` si punta al file che ce l'ha gia' (quello della sentinella
    Ads), cosi' la credenziale resta in un posto solo. Copiarla vorrebbe dire
    averne due da cambiare il giorno che scade, e una in piu' da dimenticare."""
    p = os.path.expanduser(percorso or CONFIG_DEFAULT)
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        config = yaml.safe_load(f) or {}
    altrove = config.pop("password_da", None)
    dove_sta = p if config.get("password") else None      # il file che contiene la password
    if altrove and not dove_sta:
        sorgente = os.path.expanduser(altrove)
        if not os.path.exists(sorgente):
            raise ValueError(f"password_da punta a {sorgente}, che non esiste")
        with open(sorgente) as f:
            presa = (yaml.safe_load(f) or {}).get("password")
        if not presa:
            raise ValueError(f"in {sorgente} non c'e' nessuna password")
        config["password"] = presa
        dove_sta = sorgente
    # Permessi larghi sul file della password: la mail parte lo stesso e lo dice
    # a chi legge (opzione B, Mariano 2026-09-11). Fermarla sarebbe un avviso
    # che non parte in silenzio, e nessuno se ne accorgerebbe.
    if dove_sta and os.stat(dove_sta).st_mode & 0o077:
        config["esposti"] = [dove_sta]
    return config


def _spedisci(config, destinatario, msg) -> None:
    with smtplib.SMTP(config["smtp_host"], int(config["smtp_port"]), timeout=30) as server:
        server.starttls(context=ssl.create_default_context())  # verifica il certificato: senza, su Python 3.9 e' CERT_NONE
        server.login(config["mittente"], config["password"])
        server.sendmail(config["mittente"], [destinatario], msg.as_string())


def manda(config: dict, oggetto_testo: str, corpo_html: str, spedisci=None) -> str:
    """Torna il destinatario che ha accettato. Se la casella degli agenti non
    esiste ancora si ripiega su quella nota, e chi chiama lo scrive nel log."""
    mancanti = [k for k in _CHIAVI_OBBLIGATORIE if not config.get(k)]
    if mancanti:
        raise ValueError(
            f"configurazione mail incompleta: manca {', '.join(mancanti)} in "
            f"{CONFIG_DEFAULT} (il file non esiste ancora se e' vuota)")
    spedisci = spedisci or _spedisci
    for destinatario in (config["destinatario"], config.get("ripiego")):
        if not destinatario:
            continue
        # il messaggio si ricostruisce per destinatario: porta dentro il logo allegato
        msg = template.messaggio_mime(config["mittente"], destinatario, oggetto_testo, corpo_html)
        try:
            spedisci(config, destinatario, msg)
            return destinatario
        except smtplib.SMTPRecipientsRefused:
            continue
    raise RuntimeError(f"nessun destinatario ha accettato la mail: {oggetto_testo!r}")
