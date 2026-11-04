#!/usr/bin/env python3
"""
Generatore Excel Marginalità Amazon — New Era S.r.l.
Modalità: single (1 prodotto, multi-marketplace, multi-prezzo) o batch (N prodotti)
"""

import argparse
import copy
import math
import json
import os
import sys
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, NamedStyle
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as XLImage

# --- Brand New Era ---
COLOR_VIOLA = "6027F1"
COLOR_BLU = "1C1348"
COLOR_BG_LIGHT = "F4F4F8"
COLOR_BORDER = "E0DDEF"
COLOR_INPUT_YELLOW = "FFF9C4"
COLOR_OUTPUT_VIOLA = "EDE7FE"
COLOR_ALERT_RED = "FEE2E2"
COLOR_ALERT_YELLOW = "FEF3C7"
COLOR_ALERT_GREEN = "D1FAE5"
COLOR_WHITE = "FFFFFF"

FONT_NAME = "Nunito"  # fallback ad Arial se non disponibile sul sistema
FONT_FALLBACK = "Arial"

LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "New_Era-16.png")


# --- Helpers ---

def load_fees(skill_dir: str) -> dict:
    fees_path = os.path.join(skill_dir, "references", "amazon_eu_fees.json")
    with open(fees_path, "r", encoding="utf-8") as f:
        return json.load(f)


def prepare_config(config, mode):
    """Normalize defaults; reject ambiguous business/logistics inputs before output."""
    config = copy.deepcopy(config)
    business = config.get("tipo_attivita")
    if business not in ("reselling", "private_label"):
        raise ValueError("Specificare tipo_attivita: reselling o private_label")
    config.setdefault("adv", {}).setdefault("tacos_target", 0 if business == "reselling" else 0.10)
    returns = config.setdefault("resi", {})
    for key, value in {"tasso_atteso": 0.03, "quota_valore_recuperato": 0,
                       "quota_commissione_restituita": 0, "costo_extra_per_reso": 4.50,
                       "fonte": "Stima iniziale: verificare tasso, recuperi e costi di rientro"}.items():
        returns.setdefault(key, value)
    def number(value, label, maximum=None, positive=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{label}: numero finito richiesto")
        if value < 0 or (positive and value == 0) or (maximum is not None and value > maximum):
            raise ValueError(f"{label}: valore fuori intervallo")
    number(config["adv"]["tacos_target"], "TACoS", 1)
    for key in ("tasso_atteso", "quota_valore_recuperato", "quota_commissione_restituita"):
        number(returns[key], key, 1)
    number(returns["costo_extra_per_reso"], "Costo extra reso")
    fulfillment = config.get("fulfillment")
    fs = [fulfillment] if isinstance(fulfillment, str) else fulfillment
    if not isinstance(fs, list) or not fs or len(fs) != len(set(fs)) or any(f not in ("FBA", "FBM") for f in fs):
        raise ValueError("Scegliere FBA o FBM (entrambi solo per confronto single)")
    if mode == "batch" and (len(fs) != 1 or len(config.get("marketplaces", [])) != 1):
        raise ValueError("Lotto: un solo paese e una sola logistica per file")
    config["fulfillment"] = fs if mode == "single" else fs[0]
    if "FBM" in fs:
        if "fbm_shipping_cost" not in config:
            raise ValueError("FBM: indicare fbm_shipping_cost, incluso imballaggio/preparazione")
        number(config["fbm_shipping_cost"], "Costo FBM")
    products = [config["prodotto"]] if mode == "single" else config["prodotti"]
    if not products:
        raise ValueError("Il lotto deve contenere almeno un prodotto")
    for prod in products:
        for key in ("peso_kg", "dim_l_cm", "dim_w_cm", "dim_h_cm"):
            if "FBA" in fs:
                number(prod.get(key), key, positive=True)
            else:
                prod.setdefault(key, 0)  # non richiesti per FBM; mai usati come tariffa FBA
        costs = config["costi"] if mode == "single" else prod
        if "FBA" in fs and "inbound" not in costs:
            raise ValueError("FBA: indicare costo inbound per pezzo")
        costs.setdefault("inbound", 0)
        costs.setdefault("dazi", 0)
        for key in ("cogs", "inbound", "dazi"):
            number(costs.get(key), key)
        number(config["prezzi"]["base"] if mode == "single" else prod.get("prezzo"), "Prezzo", positive=True)
    return config


def returns_formula(rate, net_price, cogs, referral, recovered, credited, extra):
    # Incremental loss compared with a non-returned order: do not charge COGS twice.
    return f"={rate}*({net_price}-{cogs}*{recovered}-{referral}*{credited}+{extra})"


def thin_border():
    side = Side(border_style="thin", color=COLOR_BORDER)
    return Border(left=side, right=side, top=side, bottom=side)


def style_header_cell(cell, text=None):
    """Header blu scuro, testo bianco bold"""
    if text is not None:
        cell.value = text
    cell.fill = PatternFill("solid", fgColor=COLOR_BLU)
    cell.font = Font(name=FONT_NAME, bold=True, color=COLOR_WHITE, size=11)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = thin_border()


def style_label_cell(cell, text=None, bold=False):
    if text is not None:
        cell.value = text
    cell.fill = PatternFill("solid", fgColor=COLOR_BG_LIGHT)
    cell.font = Font(name=FONT_NAME, bold=bold, color=COLOR_BLU, size=10)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    cell.border = thin_border()


def style_input_cell(cell, value=None, number_format=None):
    if value is not None:
        cell.value = value
    cell.fill = PatternFill("solid", fgColor=COLOR_INPUT_YELLOW)
    cell.font = Font(name=FONT_NAME, color=COLOR_BLU, size=10)
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.border = thin_border()
    if number_format:
        cell.number_format = number_format


def style_formula_cell(cell, formula=None, number_format=None, bold=False):
    if formula is not None:
        cell.value = formula
    cell.fill = PatternFill("solid", fgColor=COLOR_WHITE)
    cell.font = Font(name=FONT_NAME, color="000000", size=10, bold=bold)
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.border = thin_border()
    if number_format:
        cell.number_format = number_format


def style_output_cell(cell, formula=None, number_format=None):
    if formula is not None:
        cell.value = formula
    cell.fill = PatternFill("solid", fgColor=COLOR_OUTPUT_VIOLA)
    cell.font = Font(name=FONT_NAME, color=COLOR_VIOLA, size=11, bold=True)
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.border = thin_border()
    if number_format:
        cell.number_format = number_format


def style_tab_color(sheet):
    sheet.sheet_properties.tabColor = COLOR_VIOLA


def add_logo_to_sheet(sheet, anchor_cell="A1"):
    """Inserisce il logo New Era nella cella specificata se il file esiste."""
    if os.path.exists(LOGO_PATH):
        try:
            img = XLImage(LOGO_PATH)
            # Ridimensiona a ~160px width mantenendo aspect ratio
            img.width = 160
            img.height = 50
            sheet.add_image(img, anchor_cell)
            return True
        except Exception as e:
            print(f"WARN: impossibile inserire logo: {e}", file=sys.stderr)
    return False


def find_referral_fee(fees: dict, category: str) -> dict:
    for f in fees["referral_fees"]:
        if f["category"] == category:
            return f
    return None


def build_referral_formula(referral_def: dict, price_cell: str) -> str:
    """Costruisce la formula Excel per la referral fee, considerando tier."""
    min_fee = referral_def.get("min_fee", 0)
    if not referral_def.get("tiered", False):
        rate = referral_def["rate"]
        # MAX tra fee % e minimum
        return f"=MAX({price_cell}*{rate},{min_fee})"
    # Tier-based
    tiers = referral_def["tiers"]
    # Costruisce un IF annidato
    formula = ""
    closing = 0
    for i, t in enumerate(tiers):
        max_price = t["max_price"]
        rate = t["rate"]
        if max_price is None:
            # ultimo tier (catch-all)
            formula += f"{price_cell}*{rate}"
        else:
            formula += f"IF({price_cell}<={max_price},{price_cell}*{rate},"
            closing += 1
    formula += ")" * closing
    return f"=MAX({formula},{min_fee})"


def build_size_tier_formula(weight_cell: str, l_cell: str, w_cell: str, h_cell: str, fees: dict) -> str:
    """Costruisce la formula IF annidata per determinare il size tier dal peso/dimensioni."""
    # Peso volumetrico: (L*W*H)/5000 in kg → useremo MAX(peso reale, volumetrico)
    vol_weight = f"MAX({weight_cell},({l_cell}*{w_cell}*{h_cell})/5000)"
    tiers = fees["fba_size_tiers"]
    formula = "="
    closing = 0
    for t in tiers[:-1]:  # tutti tranne l'ultimo (SPECIAL_OVERSIZE = catch-all)
        l_max = t["max_l_cm"]
        w_max = t["max_w_cm"]
        h_max = t["max_h_cm"]
        weight_max = t["max_weight_kg"]
        code = t["tier_code"]
        formula += f"IF(AND({l_cell}<={l_max},{w_cell}<={w_max},{h_cell}<={h_max},{vol_weight}<={weight_max}),\"{code}\","
        closing += 1
    formula += "\"SPECIAL_OVERSIZE\""
    formula += ")" * closing
    return formula


# --- Foglio: Cover ---

def create_cover_sheet(wb: Workbook, config: dict, mode: str):
    ws = wb.create_sheet("Cover", 0)
    style_tab_color(ws)
    ws.sheet_view.showGridLines = False

    add_logo_to_sheet(ws, "A1")

    # Spazio per logo (righe 1-3)
    ws.row_dimensions[1].height = 25
    ws.row_dimensions[2].height = 20
    ws.row_dimensions[3].height = 20

    ws.merge_cells("A5:F5")
    c = ws["A5"]
    c.value = "Calcolatore Marginalità Amazon"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=28)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[5].height = 40

    ws.merge_cells("A6:F6")
    c = ws["A6"]
    c.value = "Marketplace Management · AI Driver Services"
    c.font = Font(name=FONT_NAME, color=COLOR_VIOLA, size=12, italic=True)
    c.alignment = Alignment(horizontal="left", vertical="center")

    # Info documento
    info_start = 9
    info = [
        ("Cliente", config.get("cliente", "—")),
        ("Tipo attività", config["tipo_attivita"]),
        ("Modalità", "Single — 1 prodotto multi-scenario" if mode == "single" else f"Batch — {len(config.get('prodotti', []))} prodotti similari"),
        ("Prodotto" if mode == "single" else "Settore", config.get("prodotto", {}).get("nome", "—") if mode == "single" else config.get("settore", "—")),
        ("Categoria Amazon", config.get("prodotto", {}).get("categoria", config.get("categoria", "—"))),
        ("Marketplace", ", ".join(config.get("marketplaces", []))),
        ("Fulfillment", ", ".join(config.get("fulfillment", [])) if isinstance(config.get("fulfillment"), list) else config.get("fulfillment", "—")),
        ("Data generazione", datetime.now().strftime("%d/%m/%Y")),
        ("Validità fee", "Tariffe indicative da verificare (vedi Fee Reference)"),
    ]
    for i, (label, value) in enumerate(info):
        r = info_start + i
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=1).font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=11)
        ws.cell(row=r, column=1).alignment = Alignment(horizontal="left", vertical="center")
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=6)
        ws.cell(row=r, column=2, value=value)
        ws.cell(row=r, column=2).font = Font(name=FONT_NAME, color=COLOR_BLU, size=11)
        ws.cell(row=r, column=2).alignment = Alignment(horizontal="left", vertical="center")

    # Footer
    ws.merge_cells("A22:F22")
    f = ws["A22"]
    f.value = "New Era S.r.l. · Increase your performance"
    f.font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=10)
    f.alignment = Alignment(horizontal="center", vertical="center")

    # Larghezze colonne
    ws.column_dimensions["A"].width = 22
    for col in "BCDEF":
        ws.column_dimensions[col].width = 18


# --- Foglio: Come leggere ---

def create_legend_sheet(wb: Workbook, fees: dict):
    ws = wb.create_sheet("Come leggere")
    style_tab_color(ws)
    ws.sheet_view.showGridLines = False

    # Titolo
    ws.merge_cells("A1:E1")
    c = ws["A1"]
    c.value = "Come leggere questo calcolatore"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=18)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30

    # Legenda colori
    ws["A3"] = "Legenda colori celle"
    ws["A3"].font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)

    legend = [
        ("Giallo", COLOR_INPUT_YELLOW, "Input modificabile — cambia il valore e tutto si ricalcola"),
        ("Viola chiaro", COLOR_OUTPUT_VIOLA, "Output principale — margine € e %"),
        ("Grigio chiaro", COLOR_BG_LIGHT, "Etichetta descrittiva"),
        ("Rosso", COLOR_ALERT_RED, "Alert: margine negativo o sotto soglia critica"),
        ("Verde", COLOR_ALERT_GREEN, "Margine sano (≥15%)"),
    ]
    for i, (name, color, desc) in enumerate(legend):
        r = 4 + i
        cell_swatch = ws.cell(row=r, column=1)
        cell_swatch.value = name
        cell_swatch.fill = PatternFill("solid", fgColor=color)
        cell_swatch.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=10)
        cell_swatch.alignment = Alignment(horizontal="center")
        cell_swatch.border = thin_border()
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
        ws.cell(row=r, column=2, value=desc).font = Font(name=FONT_NAME, color=COLOR_BLU, size=10)
        ws.cell(row=r, column=2).alignment = Alignment(horizontal="left", vertical="center")

    # Glossario
    ws["A11"] = "Glossario"
    ws["A11"].font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)

    glossary = [
        ("COGS", "Cost of Goods Sold — costo di acquisto/produzione unitario del prodotto"),
        ("Inbound", "Costo logistica per portare la merce al magazzino Amazon"),
        ("Referral fee", "Commissione Amazon sul fatturato (in % del prezzo IVA inclusa, varia per categoria)"),
        ("FBA fee", "Costo per pick, pack & ship Amazon (dipende dal size tier: dimensioni + peso)"),
        ("Peso volumetrico", "(L × W × H) / 5000 → in kg. Amazon usa il MAX tra peso reale e volumetrico"),
        ("FBM", "Fulfillment by Merchant — il seller spedisce dal proprio magazzino"),
        ("ACoS", "Advertising Cost of Sale — spesa ADV / vendite generate dall'ADV (in %)"),
        ("TACoS", "Total ACoS — spesa ADV totale / fatturato totale (in %)"),
        ("Break-even ACoS", "Indicatore legacy non validato: non confrontarlo con la console Ads"),
        ("TACoS sostenibile", "% di spesa ADV totale che mantiene il margine positivo"),
        ("Margine atteso", "Per ordine iniziale, dopo i costi inclusi; esclusi costi generali e imposte sul reddito"),
        ("Resi", "Quota di ordini restituiti. Rettifica = tasso × (rimborso senza IVA − valore merce recuperata − commissioni restituite + costi extra)."),
    ]
    for i, (term, defn) in enumerate(glossary):
        r = 12 + i
        ws.cell(row=r, column=1, value=term)
        ws.cell(row=r, column=1).font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=10)
        ws.cell(row=r, column=1).alignment = Alignment(horizontal="left", vertical="top")
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
        ws.cell(row=r, column=2, value=defn).font = Font(name=FONT_NAME, color=COLOR_BLU, size=10)
        ws.cell(row=r, column=2).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        ws.row_dimensions[r].height = 22

    # Istruzioni
    r_inst = 12 + len(glossary) + 2
    ws.cell(row=r_inst, column=1, value="Come usare il calcolatore").font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)
    instructions = [
        "1. Vai nel foglio 'Input prodotto' (single) o 'Catalogo prodotti' (batch) e modifica le celle gialle",
        "2. Le formule ricalcolano automaticamente margine € e margine %",
        "3. Per cambiare le fee Amazon (se hai dati aggiornati), vai nel foglio 'Fee Reference'",
        "4. Per scenari diversi, modifica i prezzi nelle celle gialle delle colonne scenario",
        "5. Il foglio 'Dashboard' mostra una sintesi visuale dei risultati",
    ]
    for i, inst in enumerate(instructions):
        r = r_inst + 1 + i
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
        ws.cell(row=r, column=1, value=inst).font = Font(name=FONT_NAME, color=COLOR_BLU, size=10)
        ws.cell(row=r, column=1).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # Disclaimer
    r_disc = r_inst + 1 + len(instructions) + 2
    ws.cell(row=r_disc, column=1, value="Disclaimer").font = Font(name=FONT_NAME, bold=True, color="991B1B", size=11)
    ws.merge_cells(start_row=r_disc + 1, start_column=1, end_row=r_disc + 1, end_column=5)
    disc_text = (
        f"Tabelle indicative datate {fees['last_updated']}, non tariffe ufficiali validate per ASIN/paese. "
        "Il calcolatore è uno strumento di supporto decisionale, non una garanzia di redditività. "
        "Variazioni di cambio, promozioni, costi di prep, vouchers e altri costi accessori non sono inclusi nel calcolo base."
    )
    ws.cell(row=r_disc + 1, column=1, value=disc_text).font = Font(name=FONT_NAME, color=COLOR_BLU, size=9, italic=True)
    ws.cell(row=r_disc + 1, column=1).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    ws.row_dimensions[r_disc + 1].height = 60

    # Larghezze
    ws.column_dimensions["A"].width = 22
    for col in "BCDE":
        ws.column_dimensions[col].width = 18


# --- Foglio: Fee Reference ---

def create_fee_reference_sheet(wb: Workbook, fees: dict, categoria: str):
    ws = wb.create_sheet("Fee Reference")
    style_tab_color(ws)
    ws.sheet_view.showGridLines = False

    # Titolo
    ws.merge_cells("A1:F1")
    c = ws["A1"]
    c.value = "Fee Reference Amazon EU"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=18)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30

    ws.merge_cells("A2:F2")
    c2 = ws["A2"]
    c2.value = f"Valori aggiornati al {fees['last_updated']} — modificabili se hai dati più precisi"
    c2.font = Font(name=FONT_NAME, color=COLOR_VIOLA, size=10, italic=True)

    # ============ IVA Table ============
    r = 4
    ws.cell(row=r, column=1, value="IVA per marketplace").font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)
    r += 1
    style_header_cell(ws.cell(row=r, column=1), "Marketplace")
    style_header_cell(ws.cell(row=r, column=2), "Aliquota IVA")
    iva_start_row = r + 1
    for i, (code, info) in enumerate(fees["vat_rates"].items()):
        rr = iva_start_row + i
        style_label_cell(ws.cell(row=rr, column=1), code)
        style_input_cell(ws.cell(row=rr, column=2), info["rate"], number_format="0.00%")
    iva_end_row = iva_start_row + len(fees["vat_rates"]) - 1

    # Named range per IVA (utilizzabile via VLOOKUP)
    from openpyxl.workbook.defined_name import DefinedName
    iva_range = f"'Fee Reference'!$A${iva_start_row}:$B${iva_end_row}"
    wb.defined_names["IVA_Table"] = DefinedName("IVA_Table", attr_text=iva_range)

    # ============ Referral Fees ============
    r = iva_end_row + 3
    ws.cell(row=r, column=1, value="Referral fees per categoria").font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)
    r += 1
    style_header_cell(ws.cell(row=r, column=1), "Categoria")
    style_header_cell(ws.cell(row=r, column=2), "Aliquota base")
    style_header_cell(ws.cell(row=r, column=3), "Min fee €")
    style_header_cell(ws.cell(row=r, column=4), "Note tier")
    referral_start_row = r + 1
    for i, fee in enumerate(fees["referral_fees"]):
        rr = referral_start_row + i
        style_label_cell(ws.cell(row=rr, column=1), fee["category"])
        style_input_cell(ws.cell(row=rr, column=2), fee["rate"], number_format="0.00%")
        style_input_cell(ws.cell(row=rr, column=3), fee["min_fee"], number_format='#,##0.00 "€"')
        note = ""
        if fee.get("tiered", False):
            note_parts = []
            for t in fee["tiers"]:
                if t["max_price"] is None:
                    note_parts.append(f">€... → {t['rate']*100:.0f}%")
                else:
                    note_parts.append(f"≤€{t['max_price']} → {t['rate']*100:.0f}%")
            note = " | ".join(note_parts)
        style_label_cell(ws.cell(row=rr, column=4), note)
        # Highlight categoria attiva
        if fee["category"] == categoria:
            for col in range(1, 5):
                ws.cell(row=rr, column=col).fill = PatternFill("solid", fgColor=COLOR_OUTPUT_VIOLA)
                ws.cell(row=rr, column=col).font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=10)
    referral_end_row = referral_start_row + len(fees["referral_fees"]) - 1

    # ============ FBA Size Tiers ============
    r = referral_end_row + 3
    ws.cell(row=r, column=1, value="FBA Fulfillment fees per size tier").font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)
    r += 1
    style_header_cell(ws.cell(row=r, column=1), "Codice tier")
    style_header_cell(ws.cell(row=r, column=2), "Tariffa base €")
    style_header_cell(ws.cell(row=r, column=3), "Nome tier")
    style_header_cell(ws.cell(row=r, column=4), "Extra €/100g o kg")
    style_header_cell(ws.cell(row=r, column=5), "Max L×W×H cm")
    style_header_cell(ws.cell(row=r, column=6), "Max peso kg")
    fba_start_row = r + 1
    for i, t in enumerate(fees["fba_size_tiers"]):
        rr = fba_start_row + i
        style_label_cell(ws.cell(row=rr, column=1), t["tier_code"], bold=True)
        style_input_cell(ws.cell(row=rr, column=2), t["fee_eur"], number_format='#,##0.00 "€"')
        style_label_cell(ws.cell(row=rr, column=3), t["tier_name"])
        extra = t.get("fee_per_extra_100g", t.get("fee_per_extra_kg", 0))
        unit = "/100g" if "fee_per_extra_100g" in t else "/kg"
        style_label_cell(ws.cell(row=rr, column=4), f"{extra} {unit}")
        dims = "—"
        if t["max_l_cm"]:
            dims = f"{t['max_l_cm']}×{t['max_w_cm']}×{t['max_h_cm']}"
        style_label_cell(ws.cell(row=rr, column=5), dims)
        style_label_cell(ws.cell(row=rr, column=6), str(t["max_weight_kg"]) if t["max_weight_kg"] else "—")
    fba_end_row = fba_start_row + len(fees["fba_size_tiers"]) - 1

    # Named range FBA (tier_code → tariffa base in col B)
    fba_range = f"'Fee Reference'!$A${fba_start_row}:$F${fba_end_row}"
    wb.defined_names["FBA_Table"] = DefinedName("FBA_Table", attr_text=fba_range)

    # ============ Note ============
    r = fba_end_row + 3
    ws.cell(row=r, column=1, value="Note operative").font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)
    notes = [
        "• Peso fatturabile = MAX(peso reale, peso volumetrico). Peso volumetrico = (L×W×H)/5000 in kg.",
        "• Per prodotti Low-Price FBA (≤€20), Amazon applica sconto medio di €0.45/unità. Da applicare manualmente.",
        "• Dal 17 aprile 2026: surcharge carburante/logistica del 1.5% sulle FBA fees in IT/DE/FR/ES.",
        "• Le fee qui sono medie EU-5; possono variare di ±5-10% per marketplace specifico.",
    ]
    for i, n in enumerate(notes):
        rr = r + 1 + i
        ws.merge_cells(start_row=rr, start_column=1, end_row=rr, end_column=6)
        ws.cell(row=rr, column=1, value=n).font = Font(name=FONT_NAME, color=COLOR_BLU, size=9, italic=True)
        ws.cell(row=rr, column=1).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # Larghezze
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 32
    ws.column_dimensions["E"].width = 18
    ws.column_dimensions["F"].width = 14

    return {
        "iva_start": iva_start_row,
        "iva_end": iva_end_row,
        "referral_start": referral_start_row,
        "referral_end": referral_end_row,
        "fba_start": fba_start_row,
        "fba_end": fba_end_row,
    }


# --- Foglio: Input prodotto (modalità single) ---

def create_input_sheet_single(wb: Workbook, config: dict, fees: dict, fee_ranges: dict):
    ws = wb.create_sheet("Input prodotto")
    style_tab_color(ws)
    ws.sheet_view.showGridLines = False

    # Titolo
    ws.merge_cells("A1:D1")
    c = ws["A1"]
    c.value = "Input prodotto — modifica le celle gialle"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=18)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30

    prod = config["prodotto"]
    cliente = config.get("cliente", "—")

    # Anagrafica
    r = 3
    ws.cell(row=r, column=1, value="Anagrafica").font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)
    r += 1
    rows_anagrafica = [
        ("Cliente", cliente, "text"),
        ("Nome prodotto", prod["nome"], "text"),
        ("Categoria Amazon", prod["categoria"], "text"),
        ("Peso reale (kg)", prod["peso_kg"], '0.000 "kg"'),
        ("Lunghezza L (cm)", prod["dim_l_cm"], '0 "cm"'),
        ("Larghezza W (cm)", prod["dim_w_cm"], '0 "cm"'),
        ("Altezza H (cm)", prod["dim_h_cm"], '0 "cm"'),
    ]
    anagrafica_cells = {}
    for i, (label, value, fmt) in enumerate(rows_anagrafica):
        rr = r + i
        style_label_cell(ws.cell(row=rr, column=1), label)
        if fmt == "text":
            cell = ws.cell(row=rr, column=2, value=value)
            cell.fill = PatternFill("solid", fgColor=COLOR_INPUT_YELLOW)
            cell.font = Font(name=FONT_NAME, color=COLOR_BLU, size=10)
            cell.alignment = Alignment(horizontal="left", vertical="center")
            cell.border = thin_border()
        else:
            style_input_cell(ws.cell(row=rr, column=2), value, number_format=fmt)
        anagrafica_cells[label] = f"B{rr}"
    r += len(rows_anagrafica)

    # Cella peso volumetrico (calcolato)
    r += 1
    style_label_cell(ws.cell(row=r, column=1), "Peso volumetrico (kg)")
    style_formula_cell(
        ws.cell(row=r, column=2),
        formula=f"=({anagrafica_cells['Lunghezza L (cm)']}*{anagrafica_cells['Larghezza W (cm)']}*{anagrafica_cells['Altezza H (cm)']})/5000",
        number_format='0.000 "kg"',
    )
    peso_vol_cell = f"B{r}"

    r += 1
    style_label_cell(ws.cell(row=r, column=1), "Peso fatturabile FBA (kg)")
    style_formula_cell(
        ws.cell(row=r, column=2),
        formula=f"=MAX({anagrafica_cells['Peso reale (kg)']},{peso_vol_cell})",
        number_format='0.000 "kg"',
        bold=True,
    )
    peso_fatt_cell = f"B{r}"

    r += 1
    style_label_cell(ws.cell(row=r, column=1), "Size tier FBA")
    size_tier_formula = build_size_tier_formula(
        anagrafica_cells["Peso reale (kg)"],
        anagrafica_cells["Lunghezza L (cm)"],
        anagrafica_cells["Larghezza W (cm)"],
        anagrafica_cells["Altezza H (cm)"],
        fees,
    )
    style_formula_cell(ws.cell(row=r, column=2), formula=size_tier_formula if "FBA" in config["fulfillment"] else "N/A — FBM", bold=True)
    ws.cell(row=r, column=2).fill = PatternFill("solid", fgColor=COLOR_OUTPUT_VIOLA)
    ws.cell(row=r, column=2).font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=10)
    size_tier_cell = f"B{r}"

    # Costi
    r += 3
    ws.cell(row=r, column=1, value="Costi").font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)
    r += 1
    costi = config["costi"]
    rows_costi = [
        ("COGS (costo acquisto) €", costi["cogs"]),
        ("Inbound (logistica verso FBA) €", costi["inbound"]),
        ("Dazi / altri costi €", costi.get("dazi", 0)),
    ]
    if "FBM" in config["fulfillment"]:
        rows_costi.append(("Costo spedizione FBM €", config.get("fbm_shipping_cost", 5.0)))
    cost_cells = {}
    for i, (label, value) in enumerate(rows_costi):
        rr = r + i
        style_label_cell(ws.cell(row=rr, column=1), label)
        style_input_cell(ws.cell(row=rr, column=2), value, number_format='#,##0.00 "€"')
        cost_cells[label] = f"B{rr}"
    r += len(rows_costi)

    # Assumptions ADV e resi
    r += 2
    ws.cell(row=r, column=1, value="ADV e Resi").font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)
    r += 1
    rows_adv = [
        ("TACoS target (%)", config["adv"]["tacos_target"], "0.0%"),
        ("Tasso resi atteso (%)", config["resi"]["tasso_atteso"], "0.0%"),
        ("Costo extra per reso €", config["resi"]["costo_extra_per_reso"], '#,##0.00 "€"'),
        ("Valore merce recuperato (% COGS)", config["resi"]["quota_valore_recuperato"], "0.0%"),
        ("Commissione restituita (%)", config["resi"]["quota_commissione_restituita"], "0.0%"),
    ]
    adv_cells = {}
    for i, (label, value, fmt) in enumerate(rows_adv):
        rr = r + i
        style_label_cell(ws.cell(row=rr, column=1), label)
        style_input_cell(ws.cell(row=rr, column=2), value, number_format=fmt)
        adv_cells[label] = f"B{rr}"

    note_row = r + len(rows_adv) + 1
    ws.cell(note_row, 1, "Tipo attività")
    ws.cell(note_row, 2, config["tipo_attivita"])
    ws.cell(note_row + 1, 1, "Ipotesi resi")
    ws.cell(note_row + 1, 2, config["resi"]["fonte"])
    ws.cell(note_row + 1, 2).alignment = Alignment(wrap_text=True)
    ws.row_dimensions[note_row + 1].height = 65
    # Larghezze
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 32

    return {
        "anagrafica": anagrafica_cells,
        "peso_fatt": peso_fatt_cell,
        "size_tier": size_tier_cell,
        "costi": cost_cells,
        "adv": adv_cells,
    }


# --- Foglio: Confronto Marketplace (single) ---

def create_marketplace_comparison_single(wb: Workbook, config: dict, fees: dict, input_refs: dict):
    ws = wb.create_sheet("Confronto Marketplace")
    style_tab_color(ws)
    ws.sheet_view.showGridLines = False

    # Titolo
    ws.merge_cells("A1:H1")
    c = ws["A1"]
    c.value = "Confronto Marketplace × Scenari Prezzo"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=18)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30

    marketplaces = config["marketplaces"]
    fulfillments = config["fulfillment"]
    base_price = config["prezzi"]["base"]
    scenari = config["prezzi"]["scenari"]

    # Definizione scenari prezzo (moltiplicatori)
    if scenari == "nessuno":
        scenarios = [("Prezzo base", 1.0)]
    elif scenari == "ribasso":
        scenarios = [("−20%", 0.80), ("−10%", 0.90), ("Prezzo base", 1.0)]
    elif scenari == "rialzo":
        scenarios = [("Prezzo base", 1.0), ("+10%", 1.10), ("+20%", 1.20)]
    elif scenari == "entrambi":
        scenarios = [("−20%", 0.80), ("−10%", 0.90), ("Prezzo base", 1.0), ("+10%", 1.10), ("+20%", 1.20)]
    else:
        scenarios = [("Prezzo base", 1.0)]
    n_scenarios = len(scenarios)

    # Per ogni marketplace × fulfillment costruiamo una sezione
    referral_def = find_referral_fee(fees, config["prodotto"]["categoria"])
    iva_rates = fees["vat_rates"]

    current_row = 3
    for mp in marketplaces:
        for ful in fulfillments:
            # Sezione header
            section_title = f"{mp} — {ful}"
            ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=1 + n_scenarios)
            c = ws.cell(row=current_row, column=1, value=section_title)
            c.font = Font(name=FONT_NAME, bold=True, color=COLOR_WHITE, size=12)
            c.fill = PatternFill("solid", fgColor=COLOR_VIOLA)
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = thin_border()
            ws.row_dimensions[current_row].height = 22
            current_row += 1

            # Header colonne scenari
            style_header_cell(ws.cell(row=current_row, column=1), "Voce")
            scenario_price_cells = []
            for i, (sname, mult) in enumerate(scenarios):
                style_header_cell(ws.cell(row=current_row, column=2 + i), sname)
            current_row += 1

            # Riga: prezzo IVA inclusa (input)
            style_label_cell(ws.cell(row=current_row, column=1), "Prezzo IVA inclusa €")
            price_row = current_row
            for i, (sname, mult) in enumerate(scenarios):
                col = 2 + i
                price_value = round(base_price * mult, 2)
                style_input_cell(ws.cell(row=current_row, column=col), price_value, number_format='#,##0.00 "€"')
                scenario_price_cells.append(f"{get_column_letter(col)}{current_row}")
            current_row += 1

            # IVA marketplace (formula riferita a Fee Reference)
            style_label_cell(ws.cell(row=current_row, column=1), f"Aliquota IVA ({mp})")
            iva_row = current_row
            for i in range(n_scenarios):
                col = 2 + i
                formula = f'=VLOOKUP("{mp}",IVA_Table,2,FALSE)'
                style_formula_cell(ws.cell(row=current_row, column=col), formula=formula, number_format="0.00%")
            current_row += 1

            # Prezzo netto IVA esclusa
            style_label_cell(ws.cell(row=current_row, column=1), "Prezzo netto IVA escl. €")
            net_price_row = current_row
            for i, price_cell in enumerate(scenario_price_cells):
                col = 2 + i
                col_letter = get_column_letter(col)
                formula = f"={price_cell}/(1+{col_letter}{iva_row})"
                style_formula_cell(ws.cell(row=current_row, column=col), formula=formula, number_format='#,##0.00 "€"')
            current_row += 1

            # Referral fee
            style_label_cell(ws.cell(row=current_row, column=1), "Referral fee €")
            referral_row = current_row
            for i, price_cell in enumerate(scenario_price_cells):
                col = 2 + i
                formula = build_referral_formula(referral_def, price_cell)
                style_formula_cell(ws.cell(row=current_row, column=col), formula=formula, number_format='#,##0.00 "€"')
            current_row += 1

            # Fulfillment fee
            if ful == "FBA":
                style_label_cell(ws.cell(row=current_row, column=1), "FBA fee €")
                ful_row = current_row
                for i in range(n_scenarios):
                    col = 2 + i
                    # VLOOKUP del size tier nella tabella FBA → colonna 2 (tariffa base)
                    formula = f"=VLOOKUP('Input prodotto'!{input_refs['size_tier']},FBA_Table,2,FALSE)"
                    style_formula_cell(ws.cell(row=current_row, column=col), formula=formula, number_format='#,##0.00 "€"')
            else:  # FBM
                style_label_cell(ws.cell(row=current_row, column=1), "Spedizione FBM €")
                ful_row = current_row
                fbm_cell = input_refs["costi"].get("Costo spedizione FBM €", None)
                for i in range(n_scenarios):
                    col = 2 + i
                    if fbm_cell:
                        formula = f"='Input prodotto'!{fbm_cell}"
                    else:
                        formula = f"={fees['fbm_assumptions']['default_shipping_cost_eur']}"
                    style_formula_cell(ws.cell(row=current_row, column=col), formula=formula, number_format='#,##0.00 "€"')
            current_row += 1

            # COGS
            style_label_cell(ws.cell(row=current_row, column=1), "COGS €")
            cogs_row = current_row
            for i in range(n_scenarios):
                col = 2 + i
                formula = f"='Input prodotto'!{input_refs['costi']['COGS (costo acquisto) €']}"
                style_formula_cell(ws.cell(row=current_row, column=col), formula=formula, number_format='#,##0.00 "€"')
            current_row += 1

            # Inbound
            style_label_cell(ws.cell(row=current_row, column=1), "Inbound €")
            inbound_row = current_row
            for i in range(n_scenarios):
                col = 2 + i
                formula = f"='Input prodotto'!{input_refs['costi']['Inbound (logistica verso FBA) €']}" if ful == "FBA" else "=0"
                style_formula_cell(ws.cell(row=current_row, column=col), formula=formula, number_format='#,##0.00 "€"')
            current_row += 1

            # Dazi
            style_label_cell(ws.cell(row=current_row, column=1), "Dazi / altri €")
            dazi_row = current_row
            for i in range(n_scenarios):
                col = 2 + i
                formula = f"='Input prodotto'!{input_refs['costi']['Dazi / altri costi €']}"
                style_formula_cell(ws.cell(row=current_row, column=col), formula=formula, number_format='#,##0.00 "€"')
            current_row += 1

            # Costo ADV
            style_label_cell(ws.cell(row=current_row, column=1), "Costo ADV €")
            adv_row = current_row
            for i, price_cell in enumerate(scenario_price_cells):
                col = 2 + i
                formula = f"={price_cell}*'Input prodotto'!{input_refs['adv']['TACoS target (%)']}"
                style_formula_cell(ws.cell(row=current_row, column=col), formula=formula, number_format='#,##0.00 "€"')
            current_row += 1

            # Costo resi
            style_label_cell(ws.cell(row=current_row, column=1), "Rettifica resi €")
            resi_row = current_row
            for i in range(n_scenarios):
                col = 2 + i
                letter = get_column_letter(col)
                a = input_refs['adv']
                formula = returns_formula(
                    f"'Input prodotto'!{a['Tasso resi atteso (%)']}",
                    f"{letter}{net_price_row}", f"{letter}{cogs_row}", f"{letter}{referral_row}",
                    f"'Input prodotto'!{a['Valore merce recuperato (% COGS)']}",
                    f"'Input prodotto'!{a['Commissione restituita (%)']}",
                    f"'Input prodotto'!{a['Costo extra per reso €']}")
                style_formula_cell(ws.cell(row=current_row, column=col), formula=formula, number_format='#,##0.00 "€"')
            current_row += 1

            # Spaziatura
            current_row += 1

            # === Output ===
            # Margine pre-ADV (per break-even ACoS)
            style_label_cell(ws.cell(row=current_row, column=1), "Margine pre-ADV €", bold=True)
            pre_adv_row = current_row
            for i in range(n_scenarios):
                col = 2 + i
                col_letter = get_column_letter(col)
                formula = (
                    f"={col_letter}{net_price_row}"
                    f"-{col_letter}{referral_row}"
                    f"-{col_letter}{ful_row}"
                    f"-{col_letter}{cogs_row}"
                    f"-{col_letter}{inbound_row}"
                    f"-{col_letter}{dazi_row}"
                    f"-{col_letter}{resi_row}"
                )
                style_formula_cell(ws.cell(row=current_row, column=col), formula=formula, number_format='#,##0.00 "€"', bold=True)
            current_row += 1

            # Margine netto
            style_label_cell(ws.cell(row=current_row, column=1), "Margine atteso / ordine €", bold=True)
            margin_row = current_row
            for i in range(n_scenarios):
                col = 2 + i
                col_letter = get_column_letter(col)
                formula = f"={col_letter}{pre_adv_row}-{col_letter}{adv_row}"
                style_output_cell(ws.cell(row=current_row, column=col), formula=formula, number_format='#,##0.00 "€"')
            current_row += 1

            # Margine %
            style_label_cell(ws.cell(row=current_row, column=1), "Margine %", bold=True)
            margin_pct_row = current_row
            for i, price_cell in enumerate(scenario_price_cells):
                col = 2 + i
                col_letter = get_column_letter(col)
                formula = f"={col_letter}{margin_row}/{price_cell}"
                style_output_cell(ws.cell(row=current_row, column=col), formula=formula, number_format="0.0%")
            current_row += 1

            # Conditional formatting su margine %
            margin_pct_range = f"{get_column_letter(2)}{margin_pct_row}:{get_column_letter(1 + n_scenarios)}{margin_pct_row}"
            red_fill = PatternFill("solid", fgColor=COLOR_ALERT_RED)
            yellow_fill = PatternFill("solid", fgColor=COLOR_ALERT_YELLOW)
            green_fill = PatternFill("solid", fgColor=COLOR_ALERT_GREEN)
            ws.conditional_formatting.add(margin_pct_range, CellIsRule(operator="lessThan", formula=["0"], fill=red_fill, font=Font(name=FONT_NAME, bold=True, color="991B1B")))
            ws.conditional_formatting.add(margin_pct_range, CellIsRule(operator="between", formula=["0", "0.1"], fill=yellow_fill))
            ws.conditional_formatting.add(margin_pct_range, CellIsRule(operator="greaterThanOrEqual", formula=["0.15"], fill=green_fill, font=Font(name=FONT_NAME, bold=True, color="065F46")))

            # Break-even ACoS
            style_label_cell(ws.cell(row=current_row, column=1), "ACoS legacy — non validato")
            for i in range(n_scenarios):
                col = 2 + i
                col_letter = get_column_letter(col)
                formula = f"=IF({col_letter}{net_price_row}=0,0,{col_letter}{pre_adv_row}/{col_letter}{net_price_row})"
                cell = ws.cell(row=current_row, column=col, value=formula)
                cell.fill = PatternFill("solid", fgColor=COLOR_WHITE)
                cell.font = Font(name=FONT_NAME, color=COLOR_VIOLA, size=10, italic=True)
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.border = thin_border()
                cell.number_format = "0.0%"
            current_row += 1

            # TACoS sostenibile (pareggio)
            style_label_cell(ws.cell(row=current_row, column=1), "TACoS sostenibile")
            for i, price_cell in enumerate(scenario_price_cells):
                col = 2 + i
                col_letter = get_column_letter(col)
                formula = f"=IF({price_cell}=0,0,{col_letter}{pre_adv_row}/{price_cell})"
                cell = ws.cell(row=current_row, column=col, value=formula)
                cell.fill = PatternFill("solid", fgColor=COLOR_WHITE)
                cell.font = Font(name=FONT_NAME, color=COLOR_VIOLA, size=10, italic=True)
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.border = thin_border()
                cell.number_format = "0.0%"
            current_row += 3  # spazio tra sezioni

    # Larghezze colonne
    ws.column_dimensions["A"].width = 30
    for i in range(n_scenarios):
        ws.column_dimensions[get_column_letter(2 + i)].width = 16

    return {"margin_row_count": current_row}


# --- Foglio: Dashboard (single) ---

def create_dashboard_single(wb: Workbook, config: dict):
    ws = wb.create_sheet("Dashboard")
    style_tab_color(ws)
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:F1")
    c = ws["A1"]
    c.value = "Dashboard KPI"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=18)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30

    ws.merge_cells("A3:F3")
    note = ws["A3"]
    note.value = "I valori KPI sono calcolati dal foglio 'Confronto Marketplace'. Cambia i prezzi lì per vedere l'impatto qui."
    note.font = Font(name=FONT_NAME, color=COLOR_VIOLA, size=10, italic=True)

    # Per ora, dashboard semplificata che indica dove trovare l'output principale
    # In una versione più sofisticata si possono inserire grafici e formule riassuntive
    ws.merge_cells("A5:F5")
    c = ws["A5"]
    c.value = "🎯 Cosa guardare nel foglio Confronto Marketplace:"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=12)

    items = [
        "1. Margine netto € (cella viola) → guadagno per ogni unità venduta",
        "2. Margine % → margine relativo al prezzo. Sotto il 10% è critico, sotto 0% è perdita certa.",
        "3. ACoS legacy: formula preesistente non validata, non utilizzarla come soglia nella console Ads.",
        "4. TACoS sostenibile → quanta spesa ADV totale (su tutto il fatturato) puoi permetterti mantenendo margine positivo.",
    ]
    for i, item in enumerate(items):
        rr = 6 + i
        ws.merge_cells(start_row=rr, start_column=1, end_row=rr, end_column=6)
        c = ws.cell(row=rr, column=1, value=item)
        c.font = Font(name=FONT_NAME, color=COLOR_BLU, size=10)
        c.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        ws.row_dimensions[rr].height = 30

    # Action items
    ws.merge_cells("A12:F12")
    c = ws["A12"]
    c.value = "🚦 Semaforo decisionale"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=12)

    semaforo = [
        ("🟢 Verde (Margine ≥15%)", "Prodotto profittevole. Puoi spingere con campagne ADV aggressive."),
        ("🟡 Giallo (Margine 0-15%)", "Profittabilità marginale. Valuta aumento prezzo o riduzione costi. ADV solo se ACoS molto basso."),
        ("🔴 Rosso (Margine <0%)", "Perdita per unità. NON listare a questo prezzo. Rivedi pricing o cambia fornitore."),
    ]
    for i, (label, desc) in enumerate(semaforo):
        rr = 13 + i
        c1 = ws.cell(row=rr, column=1, value=label)
        c1.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=10)
        c1.alignment = Alignment(horizontal="left", vertical="center")
        ws.merge_cells(start_row=rr, start_column=2, end_row=rr, end_column=6)
        c2 = ws.cell(row=rr, column=2, value=desc)
        c2.font = Font(name=FONT_NAME, color=COLOR_BLU, size=10)
        c2.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.row_dimensions[rr].height = 24

    # Footer
    ws.merge_cells("A22:F22")
    f = ws["A22"]
    f.value = "New Era S.r.l. · Increase your performance"
    f.font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=10)
    f.alignment = Alignment(horizontal="center", vertical="center")

    ws.column_dimensions["A"].width = 30
    for col in "BCDEF":
        ws.column_dimensions[col].width = 18


# --- Foglio: Catalogo prodotti (batch) ---

def create_catalogo_batch(wb: Workbook, config: dict, fees: dict, fee_ranges: dict):
    ws = wb.create_sheet("Catalogo prodotti")
    style_tab_color(ws)
    ws.sheet_view.showGridLines = False

    # Titolo
    ws.merge_cells("A1:N1")
    c = ws["A1"]
    c.value = "Catalogo prodotti — calcolo margine per ASIN"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=18)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30

    # Header colonne
    headers = [
        "Prodotto",
        "COGS €",
        "Inbound €",
        "Dazi €",
        "Peso kg",
        "L cm",
        "W cm",
        "H cm",
        "Size tier",
        "Prezzo IVA €",
        "Referral €",
        "FBA / FBM €",
        "Costo ADV €",
        "Rettifica resi €",
        "Margine €",
        "Margine %",
    ]
    header_row = 3
    for i, h in enumerate(headers):
        style_header_cell(ws.cell(row=header_row, column=1 + i), h)
    ws.row_dimensions[header_row].height = 28

    # Dati
    referral_def = find_referral_fee(fees, config["categoria"])
    mp = config["marketplaces"][0]  # batch: 1 marketplace
    iva_rate = fees["vat_rates"][mp]["rate"]
    tacos = config["adv"]["tacos_target"]
    tasso_resi = config["resi"]["tasso_atteso"]

    fulfillment = config["fulfillment"] if isinstance(config["fulfillment"], str) else config["fulfillment"][0]

    for i, prod in enumerate(config["prodotti"]):
        r = header_row + 1 + i
        # Colonne input (gialle)
        ws.cell(row=r, column=1, value=prod["nome"])
        ws.cell(row=r, column=1).fill = PatternFill("solid", fgColor=COLOR_INPUT_YELLOW)
        ws.cell(row=r, column=1).font = Font(name=FONT_NAME, color=COLOR_BLU, size=10)
        ws.cell(row=r, column=1).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.cell(row=r, column=1).border = thin_border()
        style_input_cell(ws.cell(row=r, column=2), prod["cogs"], number_format='#,##0.00 "€"')
        style_input_cell(ws.cell(row=r, column=3), prod["inbound"], number_format='#,##0.00 "€"')
        style_input_cell(ws.cell(row=r, column=4), prod.get("dazi", 0), number_format='#,##0.00 "€"')
        style_input_cell(ws.cell(row=r, column=5), prod["peso_kg"], number_format='0.000 "kg"')
        style_input_cell(ws.cell(row=r, column=6), prod["dim_l_cm"], number_format='0 "cm"')
        style_input_cell(ws.cell(row=r, column=7), prod["dim_w_cm"], number_format='0 "cm"')
        style_input_cell(ws.cell(row=r, column=8), prod["dim_h_cm"], number_format='0 "cm"')

        # Size tier (formula)
        size_formula = build_size_tier_formula(f"E{r}", f"F{r}", f"G{r}", f"H{r}", fees)
        cell_st = ws.cell(row=r, column=9, value=size_formula if fulfillment == "FBA" else "N/A — FBM")
        cell_st.fill = PatternFill("solid", fgColor=COLOR_BG_LIGHT)
        cell_st.font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=10)
        cell_st.alignment = Alignment(horizontal="center", vertical="center")
        cell_st.border = thin_border()

        # Prezzo (input)
        style_input_cell(ws.cell(row=r, column=10), prod["prezzo"], number_format='#,##0.00 "€"')

        # Referral fee (formula)
        ref_formula = build_referral_formula(referral_def, f"J{r}")
        style_formula_cell(ws.cell(row=r, column=11), formula=ref_formula, number_format='#,##0.00 "€"')

        # FBA fee (formula via lookup size tier)
        # Riferimenti Setup comune: B8=IVA, B9=TACoS, B10=Tasso resi, B11=Costo reso, B13=FBM shipping
        if fulfillment == "FBA":
            fba_formula = f"=VLOOKUP(I{r},FBA_Table,2,FALSE)"
        else:
            fba_formula = "='Setup comune'!B13"  # FBM shipping cost
        style_formula_cell(ws.cell(row=r, column=12), formula=fba_formula, number_format='#,##0.00 "€"')

        # Costo ADV (= Prezzo IVA × TACoS)
        adv_formula = f"=J{r}*'Setup comune'!B9"
        style_formula_cell(ws.cell(row=r, column=13), formula=adv_formula, number_format='#,##0.00 "€"')

        # Rettifica economica dei resi, inclusi ricavi stornati e recuperi
        resi_formula = returns_formula(
            "'Setup comune'!B10", f"J{r}/(1+'Setup comune'!B8)", f"B{r}", f"K{r}",
            "'Setup comune'!B14", "'Setup comune'!B15", "'Setup comune'!B11")
        style_formula_cell(ws.cell(row=r, column=14), formula=resi_formula, number_format='#,##0.00 "€"')

        # Margine €
        # Prezzo netto = J/(1+IVA). Margine = Prezzo netto - Referral - FBA - COGS - Inbound - Dazi - ADV - Resi
        margin_formula = (
            f"=J{r}/(1+'Setup comune'!B8)"
            f"-K{r}"
            f"-L{r}"
            f"-B{r}"
            + (f"-C{r}" if fulfillment == "FBA" else "") +
            f"-D{r}"
            f"-M{r}"
            f"-N{r}"
        )
        cell_margin = ws.cell(row=r, column=15, value=margin_formula)
        cell_margin.fill = PatternFill("solid", fgColor=COLOR_OUTPUT_VIOLA)
        cell_margin.font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=11)
        cell_margin.alignment = Alignment(horizontal="right", vertical="center")
        cell_margin.border = thin_border()
        cell_margin.number_format = '#,##0.00 "€"'

        # Margine %
        margin_pct_formula = f"=IF(J{r}=0,0,O{r}/J{r})"
        cell_margin_pct = ws.cell(row=r, column=16, value=margin_pct_formula)
        cell_margin_pct.fill = PatternFill("solid", fgColor=COLOR_OUTPUT_VIOLA)
        cell_margin_pct.font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=11)
        cell_margin_pct.alignment = Alignment(horizontal="right", vertical="center")
        cell_margin_pct.border = thin_border()
        cell_margin_pct.number_format = "0.0%"

        ws.row_dimensions[r].height = 22

    # Conditional formatting su margine %
    last_data_row = header_row + len(config["prodotti"])
    margin_pct_range = f"P{header_row + 1}:P{last_data_row}"
    red_fill = PatternFill("solid", fgColor=COLOR_ALERT_RED)
    yellow_fill = PatternFill("solid", fgColor=COLOR_ALERT_YELLOW)
    green_fill = PatternFill("solid", fgColor=COLOR_ALERT_GREEN)
    ws.conditional_formatting.add(margin_pct_range, CellIsRule(operator="lessThan", formula=["0"], fill=red_fill, font=Font(name=FONT_NAME, bold=True, color="991B1B")))
    ws.conditional_formatting.add(margin_pct_range, CellIsRule(operator="between", formula=["0", "0.1"], fill=yellow_fill))
    ws.conditional_formatting.add(margin_pct_range, CellIsRule(operator="greaterThanOrEqual", formula=["0.15"], fill=green_fill, font=Font(name=FONT_NAME, bold=True, color="065F46")))

    # Larghezze colonne
    ws.column_dimensions["A"].width = 36
    for col_letter in "BCDEFGHIJKLMNOP":
        ws.column_dimensions[col_letter].width = 13

    # Freeze panes per scorrere
    ws.freeze_panes = "B4"

    return {"data_start_row": header_row + 1, "data_end_row": last_data_row}


# --- Foglio: Setup comune (batch) ---

def create_setup_comune_batch(wb: Workbook, config: dict, fees: dict):
    ws = wb.create_sheet("Setup comune")
    style_tab_color(ws)
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:D1")
    c = ws["A1"]
    c.value = "Setup comune — parametri condivisi"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=18)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30

    mp = config["marketplaces"][0]
    iva_rate = fees["vat_rates"][mp]["rate"]
    fulfillment = config["fulfillment"] if isinstance(config["fulfillment"], str) else config["fulfillment"][0]

    rows = [
        ("Cliente", config.get("cliente", "—"), "text"),
        ("Settore", config.get("settore", "—"), "text"),
        ("Categoria Amazon", config["categoria"], "text"),
        ("Marketplace", mp, "text"),
        ("Fulfillment", fulfillment, "text"),
        ("Aliquota IVA marketplace", iva_rate, "0.00%"),
        ("TACoS target (%)", config["adv"]["tacos_target"], "0.0%"),
        ("Tasso resi atteso (%)", config["resi"]["tasso_atteso"], "0.0%"),
        ("Costo extra per reso €", config["resi"]["costo_extra_per_reso"], '#,##0.00 "€"'),
        ("Numero prodotti", len(config["prodotti"]), "0"),
        ("Costo spedizione FBM €", config.get("fbm_shipping_cost", 0), '#,##0.00 "€"'),
        ("Valore merce recuperato (% COGS)", config["resi"]["quota_valore_recuperato"], "0.0%"),
        ("Commissione restituita (%)", config["resi"]["quota_commissione_restituita"], "0.0%"),
        ("Tipo attività", config["tipo_attivita"], "text"),
        ("Ipotesi resi", config["resi"]["fonte"], "text"),
    ]
    for i, (label, value, fmt) in enumerate(rows):
        r = 3 + i
        style_label_cell(ws.cell(row=r, column=1), label, bold=True)
        if fmt == "text":
            cell = ws.cell(row=r, column=2, value=value)
            cell.fill = PatternFill("solid", fgColor=COLOR_INPUT_YELLOW)
            cell.font = Font(name=FONT_NAME, color=COLOR_BLU, size=10)
            cell.alignment = Alignment(horizontal="left", vertical="center")
            cell.border = thin_border()
        else:
            style_input_cell(ws.cell(row=r, column=2), value, number_format=fmt)

    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 48
    ws["B17"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[17].height = 60


# --- Foglio: Ranking (batch) ---

def create_ranking_batch(wb: Workbook, config: dict, data_range: dict):
    ws = wb.create_sheet("Ranking")
    style_tab_color(ws)
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:D1")
    c = ws["A1"]
    c.value = "Ranking prodotti — top performer"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=18)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30

    n_prod = len(config["prodotti"])
    start = data_range["data_start_row"]
    end = data_range["data_end_row"]

    # Statistiche aggregate
    r = 3
    ws.cell(row=r, column=1, value="Statistiche complessive").font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)
    stats = [
        ("Numero prodotti", f"='Catalogo prodotti'!A{start}:A{end}", "=COUNTA('Catalogo prodotti'!A{}:A{})".format(start, end), "0"),
        ("Margine medio €", None, "=AVERAGE('Catalogo prodotti'!O{}:O{})".format(start, end), '#,##0.00 "€"'),
        ("Margine medio %", None, "=AVERAGE('Catalogo prodotti'!P{}:P{})".format(start, end), "0.0%"),
        ("Margine minimo €", None, "=MIN('Catalogo prodotti'!O{}:O{})".format(start, end), '#,##0.00 "€"'),
        ("Margine massimo €", None, "=MAX('Catalogo prodotti'!O{}:O{})".format(start, end), '#,##0.00 "€"'),
        ("Prodotti in perdita (#)", None, "=COUNTIF('Catalogo prodotti'!P{}:P{},\"<0\")".format(start, end), "0"),
        ("Prodotti margine <10% (#)", None, "=COUNTIFS('Catalogo prodotti'!P{}:P{},\">=0\",'Catalogo prodotti'!P{}:P{},\"<0.1\")".format(start, end, start, end), "0"),
        ("Prodotti margine ≥15% (#)", None, "=COUNTIF('Catalogo prodotti'!P{}:P{},\">=0.15\")".format(start, end), "0"),
    ]
    for i, (label, _, formula, fmt) in enumerate(stats):
        rr = r + 1 + i
        style_label_cell(ws.cell(row=rr, column=1), label, bold=True)
        cell = ws.cell(row=rr, column=2, value=formula)
        cell.fill = PatternFill("solid", fgColor=COLOR_OUTPUT_VIOLA)
        cell.font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=11)
        cell.alignment = Alignment(horizontal="right", vertical="center")
        cell.border = thin_border()
        cell.number_format = fmt

    # Top 3 e Bottom 3 (via LARGE / SMALL)
    r2 = r + 1 + len(stats) + 2
    ws.cell(row=r2, column=1, value="Top 3 prodotti per margine €").font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=12)
    style_header_cell(ws.cell(row=r2 + 1, column=1), "Posizione")
    style_header_cell(ws.cell(row=r2 + 1, column=2), "Prodotto")
    style_header_cell(ws.cell(row=r2 + 1, column=3), "Margine €")
    for i in range(min(3, n_prod)):
        rr = r2 + 2 + i
        style_label_cell(ws.cell(row=rr, column=1), f"#{i+1}")
        # INDEX-MATCH per recuperare il nome prodotto del margine i-esimo più alto
        idx_formula = f"=INDEX('Catalogo prodotti'!A{start}:A{end},MATCH(LARGE('Catalogo prodotti'!O{start}:O{end},{i+1}),'Catalogo prodotti'!O{start}:O{end},0))"
        cell_name = ws.cell(row=rr, column=2, value=idx_formula)
        cell_name.fill = PatternFill("solid", fgColor=COLOR_BG_LIGHT)
        cell_name.font = Font(name=FONT_NAME, color=COLOR_BLU, size=10)
        cell_name.border = thin_border()
        cell_name.alignment = Alignment(horizontal="left", vertical="center")

        large_formula = f"=LARGE('Catalogo prodotti'!O{start}:O{end},{i+1})"
        cell_val = ws.cell(row=rr, column=3, value=large_formula)
        cell_val.fill = PatternFill("solid", fgColor=COLOR_OUTPUT_VIOLA)
        cell_val.font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=10)
        cell_val.alignment = Alignment(horizontal="right", vertical="center")
        cell_val.border = thin_border()
        cell_val.number_format = '#,##0.00 "€"'

    r3 = r2 + 2 + min(3, n_prod) + 2
    ws.cell(row=r3, column=1, value="Bottom 3 prodotti per margine €").font = Font(name=FONT_NAME, bold=True, color="991B1B", size=12)
    style_header_cell(ws.cell(row=r3 + 1, column=1), "Posizione")
    style_header_cell(ws.cell(row=r3 + 1, column=2), "Prodotto")
    style_header_cell(ws.cell(row=r3 + 1, column=3), "Margine €")
    for i in range(min(3, n_prod)):
        rr = r3 + 2 + i
        style_label_cell(ws.cell(row=rr, column=1), f"#{i+1}")
        idx_formula = f"=INDEX('Catalogo prodotti'!A{start}:A{end},MATCH(SMALL('Catalogo prodotti'!O{start}:O{end},{i+1}),'Catalogo prodotti'!O{start}:O{end},0))"
        cell_name = ws.cell(row=rr, column=2, value=idx_formula)
        cell_name.fill = PatternFill("solid", fgColor=COLOR_BG_LIGHT)
        cell_name.font = Font(name=FONT_NAME, color=COLOR_BLU, size=10)
        cell_name.border = thin_border()
        cell_name.alignment = Alignment(horizontal="left", vertical="center")

        small_formula = f"=SMALL('Catalogo prodotti'!O{start}:O{end},{i+1})"
        cell_val = ws.cell(row=rr, column=3, value=small_formula)
        cell_val.fill = PatternFill("solid", fgColor=COLOR_ALERT_RED)
        cell_val.font = Font(name=FONT_NAME, bold=True, color="991B1B", size=10)
        cell_val.alignment = Alignment(horizontal="right", vertical="center")
        cell_val.border = thin_border()
        cell_val.number_format = '#,##0.00 "€"'

    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 18


# --- Foglio: Dashboard (batch) ---

def create_dashboard_batch(wb: Workbook, config: dict, data_range: dict):
    ws = wb.create_sheet("Dashboard")
    style_tab_color(ws)
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:E1")
    c = ws["A1"]
    c.value = "Dashboard KPI batch"
    c.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=18)
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 30

    ws.merge_cells("A3:E3")
    c = ws["A3"]
    c.value = "Tutti i KPI fanno riferimento al foglio 'Catalogo prodotti'. Modifica lì gli input."
    c.font = Font(name=FONT_NAME, color=COLOR_VIOLA, size=10, italic=True)

    start = data_range["data_start_row"]
    end = data_range["data_end_row"]

    # KPI principali in card
    r = 5
    ws.cell(row=r, column=1, value="📊 KPI riassuntivi").font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=14)

    kpis = [
        ("Margine medio %", "=AVERAGE('Catalogo prodotti'!P{}:P{})".format(start, end), "0.0%"),
        ("Margine medio € unitario", "=AVERAGE('Catalogo prodotti'!O{}:O{})".format(start, end), '#,##0.00 "€"'),
        ("Prodotti in perdita", "=COUNTIF('Catalogo prodotti'!P{}:P{},\"<0\")".format(start, end), "0"),
        ("% catalogo in perdita", "=COUNTIF('Catalogo prodotti'!P{}:P{},\"<0\")/COUNTA('Catalogo prodotti'!A{}:A{})".format(start, end, start, end), "0.0%"),
    ]
    for i, (label, formula, fmt) in enumerate(kpis):
        col = 1 + (i * 2)
        # Card 2-column layout
        if col > 4:
            col = 1
            r_card = r + 4
        else:
            r_card = r + 1

        # Riga 1: label
        c_label = ws.cell(row=r_card, column=col, value=label)
        c_label.fill = PatternFill("solid", fgColor=COLOR_BLU)
        c_label.font = Font(name=FONT_NAME, bold=True, color=COLOR_WHITE, size=10)
        c_label.alignment = Alignment(horizontal="center", vertical="center")
        c_label.border = thin_border()
        # Span 2 colonne se possibile
        if col + 1 <= 5:
            ws.merge_cells(start_row=r_card, start_column=col, end_row=r_card, end_column=col + 1)

        # Riga 2: valore
        c_val = ws.cell(row=r_card + 1, column=col, value=formula)
        c_val.fill = PatternFill("solid", fgColor=COLOR_OUTPUT_VIOLA)
        c_val.font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=16)
        c_val.alignment = Alignment(horizontal="center", vertical="center")
        c_val.border = thin_border()
        c_val.number_format = fmt
        if col + 1 <= 5:
            ws.merge_cells(start_row=r_card + 1, start_column=col, end_row=r_card + 1, end_column=col + 1)
            ws.row_dimensions[r_card + 1].height = 32

    # Distribuzione fasce margine
    r_dist = 14
    ws.cell(row=r_dist, column=1, value="📈 Distribuzione fasce margine").font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=14)
    bands = [
        ("Margine ≥ 20%", "=COUNTIF('Catalogo prodotti'!P{}:P{},\">=0.2\")".format(start, end), COLOR_ALERT_GREEN),
        ("Margine 15% – 20%", "=COUNTIFS('Catalogo prodotti'!P{}:P{},\">=0.15\",'Catalogo prodotti'!P{}:P{},\"<0.2\")".format(start, end, start, end), COLOR_ALERT_GREEN),
        ("Margine 10% – 15%", "=COUNTIFS('Catalogo prodotti'!P{}:P{},\">=0.1\",'Catalogo prodotti'!P{}:P{},\"<0.15\")".format(start, end, start, end), COLOR_ALERT_YELLOW),
        ("Margine 0% – 10%", "=COUNTIFS('Catalogo prodotti'!P{}:P{},\">=0\",'Catalogo prodotti'!P{}:P{},\"<0.1\")".format(start, end, start, end), COLOR_ALERT_YELLOW),
        ("Margine negativo", "=COUNTIF('Catalogo prodotti'!P{}:P{},\"<0\")".format(start, end), COLOR_ALERT_RED),
    ]
    for i, (label, formula, color) in enumerate(bands):
        rr = r_dist + 1 + i
        c1 = ws.cell(row=rr, column=1, value=label)
        c1.fill = PatternFill("solid", fgColor=color)
        c1.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=10)
        c1.alignment = Alignment(horizontal="left", vertical="center")
        c1.border = thin_border()
        ws.merge_cells(start_row=rr, start_column=1, end_row=rr, end_column=3)

        c2 = ws.cell(row=rr, column=4, value=formula)
        c2.fill = PatternFill("solid", fgColor=COLOR_WHITE)
        c2.font = Font(name=FONT_NAME, bold=True, color=COLOR_BLU, size=11)
        c2.alignment = Alignment(horizontal="center", vertical="center")
        c2.border = thin_border()
        c2.number_format = "0"
        c2.value = formula

        c3 = ws.cell(row=rr, column=5, value=f"=D{rr}/COUNTA('Catalogo prodotti'!A{start}:A{end})")
        c3.fill = PatternFill("solid", fgColor=COLOR_WHITE)
        c3.font = Font(name=FONT_NAME, color=COLOR_VIOLA, size=11, italic=True)
        c3.alignment = Alignment(horizontal="center", vertical="center")
        c3.border = thin_border()
        c3.number_format = "0.0%"

    # Footer
    ws.merge_cells("A23:E23")
    f = ws["A23"]
    f.value = "New Era S.r.l. · Increase your performance"
    f.font = Font(name=FONT_NAME, bold=True, color=COLOR_VIOLA, size=10)
    f.alignment = Alignment(horizontal="center", vertical="center")

    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 12


# --- Main ---

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["single", "batch"])
    parser.add_argument("--config", required=True, help="Path al config JSON")
    parser.add_argument("--output", required=True, help="Path output .xlsx")
    parser.add_argument("--skill-dir", default=None, help="Path alla skill (default: parent of this script)")
    args = parser.parse_args()

    if args.skill_dir:
        skill_dir = args.skill_dir
    else:
        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    config = prepare_config(config, args.mode)
    fees = load_fees(skill_dir)

    wb = Workbook()
    # Rimuovi foglio default
    if "Sheet" in wb.sheetnames:
        wb.remove(wb["Sheet"])

    if args.mode == "single":
        create_cover_sheet(wb, config, "single")
        create_legend_sheet(wb, fees)
        fee_ranges = create_fee_reference_sheet(wb, fees, config["prodotto"]["categoria"])
        input_refs = create_input_sheet_single(wb, config, fees, fee_ranges)
        create_marketplace_comparison_single(wb, config, fees, input_refs)
        create_dashboard_single(wb, config)
        # Riordina: Cover, Come leggere, Input, Confronto, Dashboard, Fee Reference
        order = ["Cover", "Come leggere", "Input prodotto", "Confronto Marketplace", "Dashboard", "Fee Reference"]
        wb._sheets = [wb[name] for name in order if name in wb.sheetnames]
    else:
        create_cover_sheet(wb, config, "batch")
        create_legend_sheet(wb, fees)
        create_setup_comune_batch(wb, config, fees)
        fee_ranges = create_fee_reference_sheet(wb, fees, config["categoria"])
        data_range = create_catalogo_batch(wb, config, fees, fee_ranges)
        create_ranking_batch(wb, config, data_range)
        create_dashboard_batch(wb, config, data_range)
        order = ["Cover", "Come leggere", "Setup comune", "Catalogo prodotti", "Ranking", "Dashboard", "Fee Reference"]
        wb._sheets = [wb[name] for name in order if name in wb.sheetnames]

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    wb.save(args.output)
    print(f"OK — salvato in {args.output}")


if __name__ == "__main__":
    main()
