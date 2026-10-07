"""Excel report. The Summary and Ageing sheets use live COUNTIFS / SUMIFS formulas over the
'All Breaks' sheet, so an analyst can filter, edit or resolve rows and the totals update."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .config import DEFAULT, TEAMS, Config
from .recon import COLUMNS, ReconResult

NAVY, GREY = "1F3864", "F2F2F2"
HDR_FILL = PatternFill("solid", start_color=NAVY)
HDR_FONT = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
SEV_FILL = {"HIGH": "F8CBAD", "MEDIUM": "FFE699", "LOW": "C6E0B4"}
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WIDTHS = {"break_id": 18, "category": 11, "break_type": 36, "owner_team": 28, "account": 13, "security_id": 12,
          "reference": 24, "internal_value": 22, "custodian_value": 22, "difference": 14, "notional_usd": 16,
          "reference_date": 14, "age_days": 9, "age_bucket": 11, "severity": 10, "explanation": 70, "suggested_action": 70}
COL = {name: get_column_letter(i + 1) for i, name in enumerate(COLUMNS)}
SHEETS = {"TRADE": "Trade Breaks", "POSITION": "Position Breaks", "CASH": "Cash Breaks", "MARGIN": "Margin Breaks", "PRICING": "Pricing Breaks"}


def _cell_value(v):
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if pd.isna(v) if not isinstance(v, (str, list)) else False:
        return None
    return v.item() if hasattr(v, "item") else v


def _write_table(ws, df: pd.DataFrame) -> None:
    for j, c in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=j, value=c.replace("_", " ").title())
        cell.fill, cell.font, cell.border = HDR_FILL, HDR_FONT, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(j)].width = WIDTHS[c]
    for i, row in enumerate(df[COLUMNS].itertuples(index=False), 2):
        for j, v in enumerate(row, 1):
            cell = ws.cell(row=i, column=j, value=_cell_value(v))
            cell.border = BORDER
            name = COLUMNS[j - 1]
            if name == "notional_usd" or (name in ("internal_value", "custodian_value", "difference") and isinstance(cell.value, float)):
                cell.number_format = "#,##0.00"
            if name == "reference_date":
                cell.number_format = "yyyy-mm-dd"
            if name in ("explanation", "suggested_action"):
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(len(df) + 1, 2)}"
    ws.row_dimensions[1].height = 30
    sev = f"{COL['severity']}2:{COL['severity']}{max(len(df) + 1, 2)}"
    for k, color in SEV_FILL.items():
        ws.conditional_formatting.add(sev, CellIsRule(operator="equal", formula=[f'"{k}"'], fill=PatternFill("solid", start_color=color, end_color=color)))
    age = f"{COL['age_days']}2:{COL['age_days']}{max(len(df) + 1, 2)}"
    ws.conditional_formatting.add(age, CellIsRule(operator="greaterThan", formula=["5"], font=Font(bold=True, color="C00000")))


def _title(ws, text: str, sub: str) -> None:
    ws["A1"], ws["A2"] = text, sub
    ws["A1"].font = Font(bold=True, size=16, color=NAVY)
    ws["A2"].font = Font(italic=True, color="595959")


def _hdr(ws, row: int, labels: list[str], col: int = 1) -> None:
    for k, lab in enumerate(labels):
        c = ws.cell(row=row, column=col + k, value=lab)
        c.fill, c.font, c.border = HDR_FILL, HDR_FONT, BORDER
        c.alignment = Alignment(horizontal="center", wrap_text=True)


def build_report(result: ReconResult, as_of: str | pd.Timestamp, out_path: str | Path, cfg: Config = DEFAULT) -> Path:
    as_of = pd.Timestamp(as_of)
    br = result.breaks
    n = max(len(br) + 1, 2)
    rng = lambda name: f"'All Breaks'!${COL[name]}$2:${COL[name]}${n}"  # noqa: E731

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    _title(ws, "Post-Trade Reconciliation Report", f"As-of business date {as_of:%Y-%m-%d}  |  all values USD  |  synthetic demo data")

    # --- headline KPIs (live formulas) ---
    _hdr(ws, 4, ["Headline", "Value"])
    kpis = [
        ("Total break lines", f"=COUNTA({rng('break_id')})", "0"),
        ("Root-cause breaks (excl. knock-on position/cash)", f'=COUNTIF({rng("break_type")},"<>*DRIVEN*")-(ROWS({rng("break_id")})-COUNTA({rng("break_id")}))', "0"),
        ("High-severity root-cause breaks", f'=COUNTIFS({rng("severity")},"HIGH",{rng("break_type")},"<>*DRIVEN*")', "0"),
        ("Root-cause breaks older than 5 business days", f'=COUNTIFS({rng("age_days")},">5",{rng("break_type")},"<>*DRIVEN*")', "0"),
        ("Notional at risk, root-cause only (USD)", f'=SUMIFS({rng("notional_usd")},{rng("break_type")},"<>*DRIVEN*")', "#,##0.00"),
    ]
    for i, (lab, f, fmt) in enumerate(kpis, 5):
        ws.cell(row=i, column=1, value=lab).border = BORDER
        c = ws.cell(row=i, column=2, value=f)
        c.border, c.number_format, c.font = BORDER, fmt, Font(bold=True)

    # --- breaks by owner team x severity ---
    _hdr(ws, 11, ["Owner team", "HIGH", "MEDIUM", "LOW", "Total", "Root-cause notional (USD)"])
    for i, team in enumerate(TEAMS, 12):
        ws.cell(row=i, column=1, value=team).border = BORDER
        for j, sev in enumerate(["HIGH", "MEDIUM", "LOW"], 2):
            ws.cell(row=i, column=j, value=f'=COUNTIFS({rng("owner_team")},$A{i},{rng("severity")},"{sev}")').border = BORDER
        ws.cell(row=i, column=5, value=f"=SUM(B{i}:D{i})").border = BORDER
        c = ws.cell(row=i, column=6, value=f'=SUMIFS({rng("notional_usd")},{rng("owner_team")},$A{i},{rng("break_type")},"<>*DRIVEN*")')
        c.border, c.number_format = BORDER, "#,##0.00"
    tr = 12 + len(TEAMS)
    ws.cell(row=tr, column=1, value="Total").font = Font(bold=True)
    for j in range(2, 7):
        col = get_column_letter(j)
        c = ws.cell(row=tr, column=j, value=f"=SUM({col}12:{col}{tr - 1})")
        c.font, c.border = Font(bold=True), BORDER
        if j == 6:
            c.number_format = "#,##0.00"

    # --- match statistics from the reconciliation run (static, computed in Python) ---
    s0 = tr + 3
    _hdr(ws, s0, ["Reconciliation", "Items compared", "Items with breaks", "Items matched", "Match rate"])
    for i, r in enumerate(result.stats.itertuples(index=False), s0 + 1):
        for j, v in enumerate([r.category.title(), r.items_compared, r.items_with_breaks, r.items_matched, r.match_rate], 1):
            c = ws.cell(row=i, column=j, value=v)
            c.border = BORDER
            if j == 5:
                c.number_format = "0.0%"

    # --- breaks by type ---
    t0 = s0 + len(result.stats) + 3
    _hdr(ws, t0, ["Break type", "Count", "Notional (USD)"])
    types = sorted(br["break_type"].unique()) if len(br) else []
    for i, bt in enumerate(types, t0 + 1):
        ws.cell(row=i, column=1, value=bt).border = BORDER
        ws.cell(row=i, column=2, value=f'=COUNTIF({rng("break_type")},$A{i})').border = BORDER
        c = ws.cell(row=i, column=3, value=f'=SUMIFS({rng("notional_usd")},{rng("break_type")},$A{i})')
        c.border, c.number_format = BORDER, "#,##0.00"

    for col, w in zip("ABCDEF", [48, 16, 18, 14, 14, 26]):
        ws.column_dimensions[col].width = w

    chart = BarChart()
    chart.type, chart.grouping, chart.overlap = "col", "stacked", 100
    chart.title, chart.y_axis.title = "Open breaks by owner team and severity", "Breaks"
    chart.add_data(Reference(ws, min_col=2, max_col=4, min_row=11, max_row=11 + len(TEAMS)), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=12, max_row=11 + len(TEAMS)))
    chart.height, chart.width = 8.5, 17
    ws.add_chart(chart, "H4")

    # --- All Breaks + category sheets ---
    all_ws = wb.create_sheet("All Breaks")
    _write_table(all_ws, br)
    for cat, name in SHEETS.items():
        _write_table(wb.create_sheet(name), br[br["category"] == cat] if len(br) else br)

    # --- Ageing matrix (live formulas) ---
    ag = wb.create_sheet("Ageing", 1)
    _title(ag, "Ageing of open breaks (business days)", "Counts update when 'All Breaks' is filtered or edited")
    buckets = [b for _, b in cfg.age_buckets] + [cfg.age_overflow_label]
    _hdr(ag, 4, ["Owner team"] + buckets + ["Total"])
    for i, team in enumerate(TEAMS, 5):
        ag.cell(row=i, column=1, value=team).border = BORDER
        for j, b in enumerate(buckets, 2):
            ag.cell(row=i, column=j, value=f'=COUNTIFS({rng("owner_team")},$A{i},{rng("age_bucket")},"="&{get_column_letter(j)}$4)').border = BORDER
        ag.cell(row=i, column=len(buckets) + 2, value=f"=SUM(B{i}:{get_column_letter(len(buckets) + 1)}{i})").border = BORDER
    ag.column_dimensions["A"].width = 32
    for j in range(2, len(buckets) + 3):
        ag.column_dimensions[get_column_letter(j)].width = 12
    ag.conditional_formatting.add(f"B5:{get_column_letter(len(buckets) + 1)}{4 + len(TEAMS)}",
                                  CellIsRule(operator="greaterThan", formula=["0"], fill=PatternFill("solid", start_color="FCE4D6", end_color="FCE4D6")))

    # --- Tolerances sheet: documents every rule that defines a break ---
    tol = wb.create_sheet("Tolerances")
    _title(tol, "Tolerances and thresholds", "Defined in config.py; shown here so every break is auditable")
    _hdr(tol, 4, ["Rule", "Value"])
    rules = [("Trade price tolerance (bps)", cfg.trade_price_bps), ("Position quantity tolerance", cfg.position_qty),
             ("Cash tolerance (USD)", cfg.cash_abs), ("Margin tolerance (USD)", cfg.margin_abs)]
    rules += [(f"Pricing tolerance, {k} (bps)", v) for k, v in cfg.pricing_bps.items()]
    rules += [("HIGH severity at notional >=", cfg.high_notional), ("MEDIUM severity at notional >=", cfg.medium_notional)]
    for i, (k, v) in enumerate(rules, 5):
        tol.cell(row=i, column=1, value=k).border = BORDER
        tol.cell(row=i, column=2, value=v).border = BORDER
    tol.column_dimensions["A"].width = 42
    tol.column_dimensions["B"].width = 16

    for sheet in wb.worksheets:
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
        sheet.sheet_properties.pageSetUpPr.fitToPage = True

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
