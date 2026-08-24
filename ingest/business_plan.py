"""Ingest the Business Plan workbook (`Aresco BP - 2026- 2030.xlsx`).

Four sheets carry everything the reporting module needs:

  'Financial Statements'         IS / BS / CF, one column per period, 2022A-2031P
  'Backlog & Expected Turnover'  contract value and revenue phasing per project
  'MacroAssumptions'             FX, inflation, energy prices, interest rates
  'Ratio Analysis'               the ratio set the CFO already reports on

Period headers carry an 'A' (actual) or 'P' (projection) suffix. Where the
suffix is missing — the 2025 and 2026 quarterly columns — the scenario is
inferred from the last year that has an explicit 'A', so a bare '2026 Q1'
lands as budget rather than being silently reported as achieved.
"""

import re
from datetime import date
import openpyxl
from sqlalchemy.orm import Session

from api.models import (
    FinancialLine, IngestLog, MacroAssumption, Project, ProjectPeriod,
)
from engine.fx import normalize_currency, upsert_rate
from ingest.common import clean_str, to_float, sheet_rows, is_total_row

_PERIOD_Q = re.compile(r"Q\s*([1-4])\s*[-–]?\s*(\d{4})", re.I)
_PERIOD_Y = re.compile(r"(\d{4})")

_SECTIONS = {
    "income statement": "IS",
    "balance sheet": "BS",
    "cash flow": "CF",
}


def parse_period(label: str, last_actual_year: int | None):
    """('Q1 - 2024 A', 2024) -> ('Q1-2024', 'quarterly', 'actual')."""
    s = clean_str(label)
    if not s:
        return None
    suffix = None
    tail = s.strip().rsplit(" ", 1)[-1].upper()
    if tail in {"A", "P", "F"}:
        suffix = "actual" if tail == "A" else "budget"

    m = _PERIOD_Q.search(s)
    if m:
        period = f"Q{m.group(1)}-{m.group(2)}"
        ptype, year = "quarterly", int(m.group(2))
    else:
        m = _PERIOD_Y.search(s)
        if not m:
            return None
        period = m.group(1)
        ptype, year = "annual", int(m.group(1))

    if suffix is None:
        suffix = (
            "actual"
            if last_actual_year is not None and year <= last_actual_year
            else "budget"
        )
    return period, ptype, suffix


def _period_columns(header: list, last_actual_year: int | None):
    """Map column index -> (period, period_type, scenario)."""
    cols = {}
    for ci, cell in enumerate(header):
        parsed = parse_period(cell, last_actual_year)
        if parsed:
            cols[ci] = parsed
    return cols


def _detect_last_actual_year(header: list) -> int | None:
    years = []
    for cell in header:
        s = clean_str(cell)
        if s.strip().upper().endswith(" A"):
            m = _PERIOD_Y.search(s)
            if m:
                years.append(int(m.group(1)))
    return max(years) if years else None


def _ingest_financial_statements(db: Session, ws, fname: str, replace: bool, warnings: list):
    rows = sheet_rows(ws)

    # The period header is the first row that yields 3+ parseable periods.
    hdr_idx, period_cols, last_actual = None, {}, None
    for i, row in enumerate(rows[:12]):
        last_actual = _detect_last_actual_year(row)
        cand = _period_columns(row, last_actual)
        if len(cand) >= 3:
            hdr_idx, period_cols = i, cand
            break
    if hdr_idx is None:
        warnings.append("Financial Statements: no period header row found, skipped")
        return 0

    if replace:
        db.query(FinancialLine).filter(
            FinancialLine.source_file == fname
        ).delete(synchronize_session=False)

    statement = None
    seen: set[tuple] = set()
    count = 0

    for row in rows[hdr_idx + 1:]:
        if not row:
            continue
        label = clean_str(row[0])
        if not label:
            continue

        low = label.lower()
        matched = next((v for k, v in _SECTIONS.items() if low.startswith(k)), None)
        if matched:
            statement = matched
            # A section header row can also be a period header — re-read it so
            # the BS and CF blocks pick up their own column layout.
            cand = _period_columns(row, last_actual)
            if len(cand) >= 3:
                period_cols = cand
            continue
        # The workbook's own integrity checkers: a column reading 0 means the
        # model balances for that period, anything else (a value or #REF!) means
        # it does not. Capturing them lets the reports refuse to present a
        # period the plan itself says is broken.
        if low.startswith("bs checker") or low.startswith("cf checker"):
            for ci, (period, ptype, scenario) in period_cols.items():
                if ci >= len(row):
                    continue
                raw = row[ci]
                broken = (
                    (isinstance(raw, str) and "#REF" in raw)
                    or (isinstance(raw, (int, float)) and abs(raw) > 0.5)
                )
                key = ("CHECK", label, period, scenario, "standalone")
                if key in seen:
                    continue
                seen.add(key)
                db.add(
                    FinancialLine(
                        statement="CHECK", line_item=label, period=period,
                        period_type=ptype, scenario=scenario, scope="standalone",
                        value=1.0 if broken else 0.0, currency="",
                        source_file=fname,
                    )
                )
            continue

        if statement is None:
            continue

        for ci, (period, ptype, scenario) in period_cols.items():
            if ci >= len(row):
                continue
            raw = row[ci]
            if raw is None:
                continue
            if isinstance(raw, str) and "#REF" in raw:
                continue  # broken formula in the source workbook
            value = to_float(raw, default=None)
            if value is None:
                continue
            key = (statement, label, period, scenario, "standalone")
            if key in seen:
                continue
            seen.add(key)
            db.add(
                FinancialLine(
                    statement=statement,
                    line_item=label,
                    period=period,
                    period_type=ptype,
                    scenario=scenario,
                    scope="standalone",
                    value=value,
                    currency="KEGP",
                    source_file=fname,
                )
            )
            count += 1
    return count


def _ingest_ratios(db: Session, ws, fname: str, warnings: list):
    rows = sheet_rows(ws)
    hdr_idx, period_cols = None, {}
    for i, row in enumerate(rows[:10]):
        cand = _period_columns(row, _detect_last_actual_year(row))
        if len(cand) >= 3:
            hdr_idx, period_cols = i, cand
            break
    if hdr_idx is None:
        return 0

    count = 0
    seen = set()
    for row in rows[hdr_idx + 1:]:
        if not row:
            continue
        label = clean_str(row[0])
        if not label or label.lower().endswith("ratio") and len(clean_str(row[1] or "")) == 0 and all(
            r is None for r in row[2:]
        ):
            continue
        for ci, (period, ptype, scenario) in period_cols.items():
            if ci >= len(row) or row[ci] is None:
                continue
            value = to_float(row[ci], default=None)
            if value is None:
                continue
            key = (label, period, scenario)
            if key in seen:
                continue
            seen.add(key)
            db.add(
                FinancialLine(
                    statement="RATIO", line_item=label, period=period,
                    period_type=ptype, scenario=scenario, scope="standalone",
                    value=value, currency="", source_file=fname,
                )
            )
            count += 1
    return count


def _ingest_projects(db: Session, ws, fname: str, warnings: list):
    """Read 'Backlog & Expected Turnover' into Project + budget ProjectPeriod rows."""
    rows = sheet_rows(ws)
    hdr_idx = None
    for i, row in enumerate(rows[:12]):
        cells = [clean_str(c).lower() for c in row]
        joined = " | ".join(cells)
        if "contract" in joined and "currency" in joined:
            hdr_idx = i
            break
    if hdr_idx is None:
        warnings.append("Backlog sheet: no 'Contract / Currency' header, skipped")
        return 0, 0

    header = rows[hdr_idx]
    c_name, c_contract, c_ccy = 0, None, None
    revenue_cols: dict[int, str] = {}
    for ci, cell in enumerate(header):
        label = clean_str(cell).lower()
        if not label:
            continue
        if "contract" in label and c_contract is None:
            c_contract = ci
        elif "currency" in label and c_ccy is None:
            c_ccy = ci
        elif "revenue" in label:
            m = _PERIOD_Y.search(label)
            if m:
                revenue_cols[ci] = m.group(1)
        else:
            m = _PERIOD_Q.search(label)
            if m:
                revenue_cols[ci] = f"Q{m.group(1)}-{m.group(2)}"

    # FX rows sit above the header (EGP / USD / Euro per forecast year).
    proj_count = period_count = 0
    for row in rows[hdr_idx + 1:]:
        if not row:
            continue
        name = clean_str(row[c_name])
        if not name or is_total_row(name):
            continue
        ccy = normalize_currency(row[c_ccy]) if c_ccy is not None and c_ccy < len(row) else "EGP"
        contract = to_float(row[c_contract]) if c_contract is not None and c_contract < len(row) else 0.0

        periods = {}
        for ci, period in revenue_cols.items():
            if ci >= len(row):
                continue
            v = to_float(row[ci])
            if v:
                periods[period] = v
        if not contract and not periods:
            continue

        project = db.query(Project).filter(Project.name == name).first()
        if project is None:
            project = Project(name=name, currency=ccy, contract_value=contract)
            db.add(project)
            db.flush()
            proj_count += 1
        else:
            project.currency = ccy
            if contract:
                project.contract_value = contract

        for period, revenue in periods.items():
            existing = (
                db.query(ProjectPeriod)
                .filter(
                    ProjectPeriod.project_id == project.id,
                    ProjectPeriod.period == period,
                    ProjectPeriod.scenario == "budget",
                )
                .first()
            )
            if existing:
                existing.revenue = revenue
                continue
            db.add(
                ProjectPeriod(
                    project_id=project.id, period=period, scenario="budget",
                    revenue=revenue, currency=ccy, source_file=fname,
                )
            )
            period_count += 1
    return proj_count, period_count


def _ingest_macro(db: Session, ws, fname: str, warnings: list):
    rows = sheet_rows(ws)
    hdr_idx, period_cols = None, {}
    for i, row in enumerate(rows[:8]):
        cand = _period_columns(row, None)
        if len(cand) >= 3:
            hdr_idx, period_cols = i, cand
            break
    if hdr_idx is None:
        return 0

    # The sheet repeats some forecast years in a second scenario block
    # (2031F..2033F appear twice with different rates). Flag it, and let the
    # rightmost column win — that is the block the finance team maintains.
    periods = [p for p, _, _ in period_cols.values()]
    dupes = sorted({p for p in periods if periods.count(p) > 1})
    if dupes:
        warnings.append(
            "MacroAssumptions: duplicate period columns for "
            + ", ".join(dupes)
            + " — the rightmost column was used"
        )

    count = 0
    for row in rows[hdr_idx + 1:]:
        if not row:
            continue
        label = clean_str(row[0])
        if not label:
            continue
        is_fx = label.upper().replace(" ", "").startswith(("USD/", "EURO/", "EUR/"))
        by_period: dict[str, float] = {}
        for ci in sorted(period_cols):
            period = period_cols[ci][0]
            if ci >= len(row) or row[ci] is None:
                continue
            value = to_float(row[ci], default=None)
            if value is None:
                continue
            by_period[period] = value  # later column overwrites earlier

        for period, value in by_period.items():
            db.add(MacroAssumption(name=label, period=period, value=value))
            count += 1
            # Annual planning FX becomes a dated rate so long-dated forecast
            # inflows convert at the planned rate, not today's.
            if is_fx and value > 1 and len(period) == 4:
                ccy = "USD" if label.upper().startswith("USD") else "EUR"
                upsert_rate(db, date(int(period), 1, 1), ccy, value, "business-plan")
    return count


def _ingest_gov_dues(db: Session, ws, fname: str, warnings: list):
    """Read 'Governmental dues plan' into statement='DUES'.

    The sheet gives an outstanding balance per due plus a settlement plan across
    years. On the current file every settlement year is zero, so only the
    balances carry information — which the tax module surfaces as unscheduled.
    """
    rows = sheet_rows(ws)
    hdr_idx = None
    for i, row in enumerate(rows[:8]):
        joined = " | ".join(clean_str(c).lower() for c in row)
        if "description" in joined:
            hdr_idx = i
            break
    if hdr_idx is None:
        warnings.append("Governmental dues plan: no 'Description' header, skipped")
        return 0

    header = rows[hdr_idx]
    total_col = next(
        (ci for ci, c in enumerate(header) if "total" in clean_str(c).lower()), None
    )
    label_col = next(
        (ci for ci, c in enumerate(header) if "description" in clean_str(c).lower()), 1
    )
    if total_col is None:
        warnings.append("Governmental dues plan: no 'Total' column, skipped")
        return 0

    year_cols = {}
    for ci, cell in enumerate(header):
        label = clean_str(cell)
        m = _PERIOD_Y.search(label)
        if not m or ci == total_col:
            continue
        # "Balance as of 31-10-2024" carries a year but is a snapshot column,
        # not a settlement plan year. A real year header is just the year.
        stripped = label.replace(m.group(1), "").strip(" \n\t-–—,.")
        if stripped:
            continue
        year_cols[ci] = m.group(1)

    count = 0
    for row in rows[hdr_idx + 1:]:
        if not row or label_col >= len(row):
            continue
        label = clean_str(row[label_col])
        if not label:
            continue
        balance = to_float(row[total_col]) if total_col < len(row) else 0.0
        if not balance:
            continue
        db.add(
            FinancialLine(
                statement="DUES", line_item=label, period="balance",
                period_type="annual", scenario="actual", scope="standalone",
                value=balance, currency="KEGP", source_file=fname,
            )
        )
        count += 1
        for ci, year in year_cols.items():
            if ci >= len(row):
                continue
            v = to_float(row[ci], default=None)
            if v:
                db.add(
                    FinancialLine(
                        statement="DUES", line_item=label, period=year,
                        period_type="annual", scenario="budget", scope="standalone",
                        value=v, currency="KEGP", source_file=fname,
                    )
                )
                count += 1

    scheduled = sum(1 for r in db.new if getattr(r, "statement", "") == "DUES"
                    and getattr(r, "period", "") != "balance")
    if count and not scheduled:
        warnings.append(
            "Governmental dues plan: balances loaded but every settlement year is "
            "zero — the dues have no payment schedule in the source."
        )
    return count


def ingest_business_plan(db: Session, path: str, replace: bool = True,
                         commit: bool = True) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    fname = path.rsplit("/", 1)[-1]
    warnings: list[str] = []
    result = {"kind": "business_plan"}

    def sheet(*names):
        for n in names:
            for ws in wb.worksheets:
                if clean_str(ws.title).lower() == n.lower():
                    return ws
        return None

    ws = sheet("Financial Statements")
    result["financial_lines"] = (
        _ingest_financial_statements(db, ws, fname, replace, warnings) if ws else 0
    )
    if ws is None:
        warnings.append("No 'Financial Statements' sheet found")

    ws = sheet("Ratio Analysis")
    result["ratio_lines"] = _ingest_ratios(db, ws, fname, warnings) if ws else 0

    ws = sheet("Backlog & Expected Turnover")
    if ws:
        p, pp = _ingest_projects(db, ws, fname, warnings)
        result["projects"], result["project_periods"] = p, pp
    else:
        result["projects"] = result["project_periods"] = 0
        warnings.append("No 'Backlog & Expected Turnover' sheet found")

    ws = sheet("Governmental dues plan")
    result["governmental_dues"] = _ingest_gov_dues(db, ws, fname, warnings) if ws else 0

    ws = sheet("MacroAssumptions")
    if ws and replace:
        db.query(MacroAssumption).delete(synchronize_session=False)
    result["macro_assumptions"] = _ingest_macro(db, ws, fname, warnings) if ws else 0

    wb.close()
    db.add(
        IngestLog(
            file_name=fname, kind="business_plan", as_of=date.today(),
            rows_in=0, rows_out=result.get("financial_lines", 0),
            warnings="\n".join(warnings), ok=True,
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()
    result["warnings"] = warnings
    return result
