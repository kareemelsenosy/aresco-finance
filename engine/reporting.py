"""P&L, balance sheet and project reporting.

Two views the requirement asks for explicitly:

  * the summarized BS/IS matrix (Total Assets, Total Liabilities, Working
    Capital, Retained Earnings, Revenues, EBITDA, COGS, Net Profit, No. of
    Shares) at monthly / quarterly / annual granularity, standalone and
    consolidated
  * per-project P&L and cash flow, with actual against budget

Business Plan figures are in KEGP. Everything returned here is scaled to EGP so
it is directly comparable with the cash and receivables modules, which work in
whole pounds.
"""

from collections import defaultdict
from datetime import date
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.models import FinancialLine, Project, ProjectPeriod

KEGP_TO_EGP = 1000.0

# The summary matrix, mapped onto the line items the Business Plan actually uses.
SUMMARY_LINES = [
    ("Total Assets", "BS", ["Total Non-Current Assets", "Total Current Assets"], "sum"),
    ("Total Liabilities", "BS", ["Total Current Liabilities", "Total Non-Current Liabilities"], "sum"),
    ("Working Capital", "BS", ["Working Capital"], "direct"),
    ("Retained Earnings", "BS", ["Retained Earnings"], "direct"),
    ("Revenues", "IS", ["Total Revenues"], "direct"),
    ("EBITDA", "IS", ["EBITDA"], "direct"),
    ("COGS", "IS", ["Cost Of Operations"], "direct"),
    ("Net Profit", "IS", ["EAT"], "direct"),
    ("No. of Shares", "BS", ["No. of Shares", "Number of Shares"], "direct"),
]


def _fetch(db: Session, statement: str, line_items: list[str], period_type: str,
           scope: str) -> dict[tuple[str, str], float]:
    """-> {(period, scenario): value} for the first line item that exists."""
    rows = (
        db.query(FinancialLine)
        .filter(
            FinancialLine.statement == statement,
            FinancialLine.line_item.in_(line_items),
            FinancialLine.period_type == period_type,
            FinancialLine.scope == scope,
        )
        .all()
    )
    out: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        out[(r.period, r.scenario)] += r.value
    return out


# Statements whose rows are not reporting periods: the plan's own integrity
# checkers, and the governmental-dues balances (period='balance').
NON_PERIOD_STATEMENTS = ("CHECK", "DUES")


def available_periods(db: Session, period_type: str = "annual",
                      scope: str = "standalone") -> list[str]:
    rows = (
        db.query(FinancialLine.period)
        .filter(
            FinancialLine.period_type == period_type,
            FinancialLine.scope == scope,
            FinancialLine.statement.notin_(NON_PERIOD_STATEMENTS),
        )
        .distinct()
        .all()
    )

    def key(p):
        # 'Q1-2026' sorts after '2025' and before '2026'
        try:
            if "-" in p:
                q, y = p.split("-")
                return (int(y), int(q[1:]))
            return (int(p), 0)
        except (ValueError, TypeError):
            return (9999, 0)

    # Anything that is not a real period label is dropped rather than sorted to
    # the end, so it cannot become a phantom column in a report.
    return sorted(
        (r[0] for r in rows if r[0] and key(r[0])[0] != 9999), key=key
    )


def period_health(db: Session, period_type: str = "annual") -> dict[str, list[str]]:
    """Periods the Business Plan's own BS/CF checkers report as not balancing.

    The workbook carries checker rows that should read zero. Where they don't —
    or read #REF! — the model is broken for that column, and presenting those
    figures as a plan would be presenting arithmetic the source disowns.
    """
    rows = (
        db.query(FinancialLine)
        .filter(
            FinancialLine.statement == "CHECK",
            FinancialLine.period_type == period_type,
            FinancialLine.value > 0,
        )
        .all()
    )
    out: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        out[r.period].append(r.line_item)
    return dict(out)


def summary_matrix(db: Session, period_type: str = "annual",
                   scope: str = "standalone", periods: list[str] | None = None) -> dict:
    """The BS/IS summary table, one row per metric, one column per period."""
    periods = periods or available_periods(db, period_type, scope)
    health = period_health(db, period_type)
    rows = []

    for label, statement, line_items, mode in SUMMARY_LINES:
        data = _fetch(db, statement, line_items, period_type, scope)
        cells = {}
        for period in periods:
            actual = data.get((period, "actual"))
            budget = data.get((period, "budget"))
            value = actual if actual is not None else budget
            if value is None:
                cells[period] = None
                continue
            # Balance-sheet liabilities and COGS are stored negative in the
            # source model; report them as magnitudes so the matrix reads
            # naturally, and keep the sign convention documented here.
            if label in {"Total Liabilities", "COGS"}:
                value = abs(value)
            cells[period] = {
                "value_egp": round(value * KEGP_TO_EGP, 2),
                "scenario": "actual" if actual is not None else "budget",
            }
        rows.append({"metric": label, "statement": statement, "periods": cells})

    return {
        "scope": scope,
        "period_type": period_type,
        "periods": periods,
        "unit": "EGP",
        "rows": rows,
        "unbalanced_periods": health,
        "note": (
            "The business plan's own checkers report these periods as not "
            "balancing: " + ", ".join(sorted(health)) + ". Their figures are "
            "shown but should not be relied on."
            if health else None
        ),
    }


def statement(db: Session, which: str = "IS", period_type: str = "annual",
              scope: str = "standalone", periods: list[str] | None = None) -> dict:
    """Full IS / BS / CF, every line item, for the requested periods."""
    which = which.upper()
    periods = periods or available_periods(db, period_type, scope)

    rows = (
        db.query(FinancialLine)
        .filter(
            FinancialLine.statement == which,
            FinancialLine.period_type == period_type,
            FinancialLine.scope == scope,
            FinancialLine.period.in_(periods),
        )
        .all()
    )

    by_item: dict[str, dict] = defaultdict(dict)
    order: list[str] = []
    for r in rows:
        if r.line_item not in by_item:
            order.append(r.line_item)
        by_item[r.line_item][r.period] = {
            "value_egp": round(r.value * KEGP_TO_EGP, 2),
            "scenario": r.scenario,
        }

    return {
        "statement": which,
        "scope": scope,
        "period_type": period_type,
        "periods": periods,
        "unit": "EGP",
        "lines": [
            {"line_item": item, "periods": by_item[item]} for item in order
        ],
    }


# The audited statements and the business plan describe the same things with
# different captions, so a variance report has to be told they are the same.
# Only unambiguous equivalences are mapped. Anything requiring judgement — the
# plan's single 'Provisions & Impairments' against the statements' separate
# provisions formed / ECL charge / provisions released, or 'EBITDA' against an
# 'Operating profit' struck after different costs — is deliberately left
# unmapped and reported, because a wrong pairing is worse than a missing one.
LINE_ALIASES = {
    # Income statement
    "revenues from contracts with customers": "Total Revenues",
    "cost of sales": "Cost Of Operations",
    "gross profit": "Gross Profit",
    "general and administrative expenses": "G&A",
    "selling and marketing expenses": "S&M",
    "net profit for the year before tax": "EBT",
    "profit for the year before tax": "EBT",
    "income tax": "Corporate / Income Tax",
    "net profit for the year": "EAT",
    "total comprehensive income for the year": "EAT",
    # Balance sheet
    "total non-current assets": "Total Non-Current Assets",
    "total current assets": "Total Current Assets",
    "total non-current liabilities": "Total Non-Current Liabilities",
    "total current liabilities": "Total Current Liabilities",
    "fixed assets": "Fixed Assets NET",
    "inventories": "Inventory",
    "trade and notes receivable": "Accounts Receivables",
    "cash on hand and at banks": "Cash & Banks",
    "trade and notes payable": "Accounts Payables",
    "paid-up and issued capital": "Paid In Capital",
    "legal reserve": "Reserves",
    "accumulated losses": "Retained Earnings",
    "investments in subsidiaries": "Investments",
    "bank overdrafts": "Banks Over Draft",
    "deferred income tax liabilities": "Deferred Tax",
}

# Lines the business plan books as negative (costs, liabilities) but the signed
# statements present as positive magnitudes, or vice versa. Compared on
# magnitude so the variance measures size, not bookkeeping convention.
COMPARE_ON_MAGNITUDE = {
    "Cost Of Operations", "G&A", "S&M", "Corporate / Income Tax",
    "Total Current Liabilities", "Total Non-Current Liabilities",
    "Accounts Payables", "Provisions", "Reserves", "Retained Earnings",
    "Banks Over Draft", "Deferred Tax",
}


def canonical_line(label: str) -> str:
    """Map a statement caption onto the business plan's name for the same line."""
    key = (label or "").strip().lower().rstrip(" .:")
    return LINE_ALIASES.get(key, label)


def actual_vs_budget(db: Session, period: str, statement_type: str = "IS",
                     scope: str = "standalone") -> dict:
    """Variance report for one period.

    Where a period has only a budget figure the variance is reported as null
    rather than zero — an unreported actual is not a nil variance.
    """
    rows = (
        db.query(FinancialLine)
        .filter(
            FinancialLine.statement == statement_type.upper(),
            FinancialLine.period == period,
            FinancialLine.scope == scope,
        )
        .all()
    )

    by_item: dict[str, dict] = defaultdict(dict)
    sources: dict[str, dict] = defaultdict(dict)
    order: list[str] = []
    for r in rows:
        item = canonical_line(r.line_item)
        if item not in by_item:
            order.append(item)
        # A caption already present under its canonical name wins over an alias,
        # so the plan's own wording anchors the row.
        by_item[item][r.scenario] = r.value * KEGP_TO_EGP
        sources[item][r.scenario] = r.line_item

    lines = []
    for item in order:
        actual = by_item[item].get("actual")
        budget = by_item[item].get("budget")
        variance = pct = None
        if actual is not None and budget is not None:
            a, b = actual, budget
            if item in COMPARE_ON_MAGNITUDE:
                a, b = abs(a), abs(b)
            variance = a - b
            pct = (variance / abs(b)) if b else None
        lines.append(
            {
                "line_item": item,
                "actual_egp": round(actual, 2) if actual is not None else None,
                "budget_egp": round(budget, 2) if budget is not None else None,
                "variance_egp": round(variance, 2) if variance is not None else None,
                "variance_pct": round(pct, 4) if pct is not None else None,
                "compared_on_magnitude": item in COMPARE_ON_MAGNITUDE,
                "actual_caption": sources[item].get("actual"),
                "budget_caption": sources[item].get("budget"),
            }
        )

    have_both = [l for l in lines if l["variance_egp"] is not None]
    has_actual = any(l["actual_egp"] is not None for l in lines)
    has_budget = any(l["budget_egp"] is not None for l in lines)

    if have_both:
        note = None
    elif not lines:
        note = f"No {statement_type.upper()} data loaded for {period}."
    elif has_actual and not has_budget:
        note = (
            f"{period} is held as actual only — the Business Plan carries no "
            f"budget column for it, so there is nothing to compare against."
        )
    elif has_budget and not has_actual:
        note = (
            f"{period} is held as a projection only. Variance needs reported "
            f"actuals — upload them, or pick a period the plan marks 'A'."
        )
    else:
        note = f"No comparable actual/budget pairs for {period}."

    return {
        "period": period,
        "statement": statement_type.upper(),
        "scope": scope,
        "unit": "EGP",
        "lines": lines,
        "comparable_lines": len(have_both),
        "has_actual": has_actual,
        "has_budget": has_budget,
        "unmatched_actual": sorted(
            l["line_item"] for l in lines
            if l["actual_egp"] is not None and l["budget_egp"] is None
        ),
        "unmatched_budget": sorted(
            l["line_item"] for l in lines
            if l["budget_egp"] is not None and l["actual_egp"] is None
        ),
        "note": note,
    }


def project_list(db: Session) -> list[dict]:
    projects = db.query(Project).all()
    totals = dict(
        db.query(ProjectPeriod.project_id, func.sum(ProjectPeriod.revenue))
        .filter(ProjectPeriod.scenario == "budget")
        .group_by(ProjectPeriod.project_id)
        .all()
    )
    return sorted(
        (
            {
                "id": p.id,
                "name": p.name,
                "client": p.client,
                "segment": p.segment,
                "currency": p.currency,
                "contract_value": p.contract_value,
                "planned_revenue": round(totals.get(p.id, 0.0), 2),
                "status": p.status,
            }
            for p in projects
        ),
        key=lambda x: -(x["contract_value"] or 0),
    )


def project_pnl(db: Session, project_id: int) -> dict:
    """Per-project P&L and cash flow, budget against actual."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    rows = (
        db.query(ProjectPeriod)
        .filter(ProjectPeriod.project_id == project_id)
        .all()
    )
    by_period: dict[str, dict] = defaultdict(dict)
    for r in rows:
        by_period[r.period][r.scenario] = {
            "revenue": r.revenue,
            "cogs": r.cogs,
            "gross_profit": r.gross_profit or (r.revenue - r.cogs),
            "overheads": r.overheads,
            "ebitda": r.ebitda,
            "net_profit": r.net_profit,
            "cash_in": r.cash_in,
            "cash_out": r.cash_out,
            "net_cash_flow": r.net_cash_flow or (r.cash_in - r.cash_out),
        }

    periods = []
    for period in sorted(by_period):
        actual = by_period[period].get("actual")
        budget = by_period[period].get("budget")
        entry = {"period": period, "actual": actual, "budget": budget}
        if actual and budget:
            entry["variance"] = {
                k: round(actual[k] - budget[k], 2) for k in budget
            }
        periods.append(entry)

    return {
        "project": {
            "id": project.id,
            "name": project.name,
            "client": project.client,
            "currency": project.currency,
            "contract_value": project.contract_value,
        },
        "periods": periods,
        "has_actuals": any(p.get("actual") for p in periods),
        "note": (
            None if any(p.get("actual") for p in periods) else
            "Only budget phasing is loaded for this project. Per-project actual "
            "costs are not in any of the source workbooks — upload them via "
            "POST /reporting/projects/{id}/actuals to enable variance reporting."
        ),
    }


# The P&L as the business plan lays it out. `kind` drives presentation:
#   em  — a subtotal the eye should land on
#   pct — a margin, rendered as a percentage rather than a figure
#   n   — an ordinary line
BP_PL_LINES = [
    ("3rd Party Revenues", "n"),
    ("Related Party Revenues", "n"),
    ("Total Revenues", "em"),
    ("Cost Of Operations", "n"),
    ("Gross Profit", "em"),
    ("Gross Profit %", "pct"),
    ("G&A", "n"),
    ("S&M", "n"),
    ("Other Revenues / Costs", "n"),
    ("EBITDA", "em"),
    ("EBITDA %", "pct"),
    ("Depreciation & Amortization", "n"),
    ("EBIT", "em"),
    ("Net Financing Costs / Interest", "n"),
    ("Provisions & Impairments", "n"),
    ("FOREX", "n"),
    ("EBT", "em"),
    ("Corporate / Income Tax", "n"),
    ("EAT", "em"),
    ("EAT %", "pct"),
]


def business_plan(db: Session, plan_year: str | None = None,
                  scope: str = "standalone") -> dict:
    """Business plan actual against plan.

    Figures stay in KEGP, the unit the plan is written and reviewed in —
    converting to whole pounds here would only make the table harder to read
    against the source workbook.
    """
    rows = (
        db.query(FinancialLine)
        .filter(
            FinancialLine.statement == "IS",
            FinancialLine.period_type == "annual",
            FinancialLine.scope == scope,
        )
        .all()
    )
    if not rows:
        return {"error": "No business plan loaded. Ingest Aresco BP - 2026-2030.xlsx."}

    # Both scenarios are kept per line so the view can show the reported actual
    # where one exists and the plan where it does not — and the variance between
    # them where both do. Captions are canonicalised first, because the signed
    # statements and the plan name the same lines differently.
    data: dict[tuple[str, str], dict] = defaultdict(dict)
    for r in rows:
        data[(canonical_line(r.line_item), r.period)][r.scenario] = r.value

    years = sorted({r.period for r in rows}, key=int)
    unbalanced = period_health(db, "annual")
    # Drop years the plan's own checkers disown — a chart that plots a column
    # the model says doesn't balance is a chart that misleads.
    years = [y for y in years if y not in unbalanced]

    scenario_of = {}
    for y in years:
        cell = data.get(("Total Revenues", y), {})
        scenario_of[y] = "actual" if "actual" in cell else "budget"

    # The plan year is the one being managed: the current calendar year where
    # the plan covers it, otherwise the first year still on projection.
    if plan_year is None:
        this_year = str(date.today().year)
        plan_year = (
            this_year if this_year in years
            else next((y for y in years if scenario_of[y] == "budget"), years[-1])
        )
    if plan_year not in years:
        raise ValueError(
            f"No plan data for {plan_year}. Available: {', '.join(years)}"
        )
    prior_year = str(int(plan_year) - 1)

    def val(item: str, period: str, scenario: str | None = None):
        """Reported actual where there is one, otherwise the plan."""
        cell = data.get((item, period), {})
        if scenario:
            return cell.get(scenario)
        return cell.get("actual", cell.get("budget"))

    # --- headline tiles -------------------------------------------------
    rev = val("Total Revenues", plan_year, "budget") or val("Total Revenues", plan_year)
    ebitda = val("EBITDA", plan_year, "budget") or val("EBITDA", plan_year)
    eat = val("EAT", plan_year, "budget") or val("EAT", plan_year)
    prior_rev = val("Total Revenues", prior_year)
    prior_scenario = scenario_of.get(prior_year, "budget")

    kpis = {
        "plan_year": plan_year,
        "revenue": rev,
        "ebitda": ebitda,
        "ebitda_margin": (ebitda / rev) if rev else None,
        "eat": eat,
        "eat_margin": (eat / rev) if rev else None,
        "step_up": (rev / prior_rev) if rev and prior_rev else None,
        "prior_year": prior_year,
        "prior_revenue": prior_rev,
        "prior_scenario": prior_scenario,
    }

    # --- revenue by year, actual vs plan --------------------------------
    chart = [
        {
            "period": y,
            "value": val("Total Revenues", y),
            "scenario": scenario_of[y],
            "tag": "A" if scenario_of[y] == "actual" else "P",
        }
        for y in years
        if val("Total Revenues", y) is not None
    ]

    # --- quarterly phasing of the plan year ------------------------------
    q_rows = (
        db.query(FinancialLine)
        .filter(
            FinancialLine.statement == "IS",
            FinancialLine.period_type == "quarterly",
            FinancialLine.scope == scope,
            FinancialLine.period.like(f"%-{plan_year}"),
        )
        .all()
    )
    q_data: dict[tuple[str, str], dict] = defaultdict(dict)
    q_scen: dict[str, str] = {}
    for r in q_rows:
        q_data[(canonical_line(r.line_item), r.period)][r.scenario] = r.value
    for period, cell in list(q_data.items()):
        if period[0] == "Total Revenues":
            q_scen[period[1]] = "actual" if "actual" in cell else "budget"

    def qval(item, period):
        cell = q_data.get((item, period), {})
        return cell.get("actual", cell.get("budget"))

    quarters = []
    for q in ("Q1", "Q2", "Q3", "Q4"):
        period = f"{q}-{plan_year}"
        q_rev = qval("Total Revenues", period)
        if q_rev is None:
            continue
        q_ebitda = qval("EBITDA", period)
        quarters.append(
            {
                "quarter": q,
                "period": period,
                "revenue": q_rev,
                "ebitda": q_ebitda,
                "margin": (q_ebitda / q_rev) if q_rev and q_ebitda is not None else None,
                "eat": qval("EAT", period),
                "scenario": q_scen.get(period, "budget"),
            }
        )

    h2_share = None
    if len(quarters) == 4:
        total = sum(q["revenue"] for q in quarters)
        if total:
            h2_share = (quarters[2]["revenue"] + quarters[3]["revenue"]) / total

    # --- the full P&L ----------------------------------------------------
    pl = []
    for item, kind in BP_PL_LINES:
        cells = []
        for y in years:
            cell = data.get((item, y), {})
            actual, budget = cell.get("actual"), cell.get("budget")
            value = actual if actual is not None else budget
            variance = None
            if actual is not None and budget is not None:
                a, b = (abs(actual), abs(budget)) if item in COMPARE_ON_MAGNITUDE else (actual, budget)
                variance = a - b
            cells.append(
                {
                    "period": y,
                    "value": value,
                    "actual": actual,
                    "budget": budget,
                    "variance": variance,
                    "variance_pct": (variance / abs(budget)) if variance is not None and budget else None,
                    "scenario": "actual" if actual is not None else "budget",
                }
            )
        if all(c["value"] is None for c in cells):
            continue
        pl.append({"line_item": item, "kind": kind, "cells": cells})

    last_actual = next(
        (y for y in reversed(years) if scenario_of[y] == "actual"), None
    )
    # Default column window: the last reported actual as the anchor, then the
    # plan horizon. The workbook runs to 2033; showing all of it buries the
    # years anyone is accountable for.
    anchor = years.index(last_actual) if last_actual in years else 0
    window = years[anchor:anchor + 7]

    return {
        "scope": scope,
        "unit": "KEGP",
        "years": years,
        "default_columns": window,
        "scenario_by_year": scenario_of,
        "last_actual_year": last_actual,
        "kpis": kpis,
        "revenue_chart": chart,
        "quarters": quarters,
        "h2_share": h2_share,
        "pl": pl,
        "excluded_years": sorted(unbalanced),
        "note": (
            "Years excluded because the plan's own BS/CF checkers report them as "
            "not balancing: " + ", ".join(sorted(unbalanced))
            if unbalanced else None
        ),
    }


def ratios(db: Session, period_type: str = "annual") -> dict:
    rows = (
        db.query(FinancialLine)
        .filter(FinancialLine.statement == "RATIO",
                FinancialLine.period_type == period_type)
        .all()
    )
    by_item: dict[str, dict] = defaultdict(dict)
    order: list[str] = []
    for r in rows:
        if r.line_item not in by_item:
            order.append(r.line_item)
        by_item[r.line_item][r.period] = round(r.value, 4)
    periods = sorted({r.period for r in rows})
    return {
        "period_type": period_type,
        "periods": periods,
        "lines": [{"ratio": item, "periods": by_item[item]} for item in order],
    }
