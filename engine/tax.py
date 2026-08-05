"""Tax and governmental dues.

Egypt's filing calendar is what makes this a treasury problem rather than a
compliance one: VAT monthly, withholding quarterly, payroll monthly, corporate
income tax annually, social insurance monthly. Each is a dated outflow, and an
obligation with no due date cannot enter the cash forecast at all — so an
unscheduled liability is reported as a gap rather than quietly ignored.
"""

from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.models import FinancialLine, TaxObligation
from engine.fx import FXConverter

# Egyptian filing cadence, for the calendar view. Deadlines move with finance
# law, so the day is a default the finance team can override per obligation.
FILING_CALENDAR = {
    "vat": {"label": "Value Added Tax", "cadence": "monthly", "day": 30,
            "note": "Return and payment within two months of the tax period for most taxpayers — VERIFY."},
    "wht": {"label": "Withholding Tax", "cadence": "quarterly", "day": 30,
            "note": "Amounts withheld from suppliers, remitted quarterly."},
    "payroll": {"label": "Salary Tax", "cadence": "monthly", "day": 15,
                "note": "Withheld monthly, annual reconciliation."},
    "social_insurance": {"label": "Social Insurance", "cadence": "monthly", "day": 15,
                         "note": "Employer and employee contributions on insured earnings."},
    "corporate_income": {"label": "Corporate Income Tax", "cadence": "annual", "day": 30,
                         "note": "Return within four months of year end — VERIFY."},
    "stamp": {"label": "Stamp Tax", "cadence": "quarterly", "day": 30, "note": ""},
    "property": {"label": "Property Tax", "cadence": "annual", "day": 30, "note": ""},
    "other": {"label": "Other governmental dues", "cadence": "ad hoc", "day": 30, "note": ""},
}

# How the Business Plan's Governmental Dues sheet names things.
DUES_MAP = {
    "withholding tax payable": "wht",
    "withholding tax receivable": "wht",
    "payroll tax": "payroll",
    "income tax": "corporate_income",
    "ضرائب عقارية": "property",
    "قيمة مضافة": "vat",
    "مساهمه تكافليه و صندوق التدريب": "other",
    "project social insurance": "social_insurance",
}


def obligations(db: Session, status: str | None = None, tax_type: str | None = None,
                as_of: date | None = None) -> dict:
    """The obligation register, with ageing against the due date."""
    as_of = as_of or date.today()
    q = db.query(TaxObligation)
    if status:
        q = q.filter(TaxObligation.status == status)
    if tax_type:
        q = q.filter(TaxObligation.tax_type == tax_type)

    fx = FXConverter(db, as_of)
    rows, by_type = [], defaultdict(lambda: {"count": 0, "outstanding_egp": 0.0})
    total = overdue = unscheduled = 0.0

    for t in q.order_by(TaxObligation.due_date.is_(None), TaxObligation.due_date).all():
        egp = fx.try_to_egp(t.outstanding, t.currency, as_of)
        if egp is None:
            egp = 0.0
        days = (t.due_date - as_of).days if t.due_date else None
        is_overdue = days is not None and days < 0 and t.outstanding > 0
        rows.append(
            {
                "id": t.id,
                "tax_type": t.tax_type,
                "label": FILING_CALENDAR.get(t.tax_type, {}).get("label", t.tax_type),
                "description": t.description,
                "period": t.period,
                "authority": t.authority,
                "currency": t.currency,
                "amount_due": t.amount_due,
                "penalty": t.penalty,
                "paid_amount": t.paid_amount,
                "outstanding": round(t.outstanding, 2),
                "outstanding_egp": round(egp, 2),
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "days_to_due": days,
                "overdue": is_overdue,
                "overdue_days": -days if is_overdue else 0,
                "status": t.status,
                "reference": t.reference,
                "scheduled": t.due_date is not None,
            }
        )
        if t.outstanding > 0:
            total += egp
            by_type[t.tax_type]["count"] += 1
            by_type[t.tax_type]["outstanding_egp"] += egp
            if is_overdue:
                overdue += egp
            if t.due_date is None:
                unscheduled += egp

    return {
        "as_of": as_of.isoformat(),
        "obligations": rows,
        "total_outstanding_egp": round(total, 2),
        "overdue_egp": round(overdue, 2),
        "unscheduled_egp": round(unscheduled, 2),
        "by_type": [
            {"tax_type": k, "label": FILING_CALENDAR.get(k, {}).get("label", k),
             "count": v["count"], "outstanding_egp": round(v["outstanding_egp"], 2)}
            for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]["outstanding_egp"])
        ],
        "note": (
            f"{unscheduled:,.0f} EGP of dues carry no settlement date and are "
            f"therefore excluded from the dated cash forecast. Set a due date to "
            f"bring them in."
            if unscheduled else None
        ),
        "empty": not rows,
    }


def due_schedule(db: Session, horizon_days: int = 90, as_of: date | None = None) -> list[dict]:
    """Dated tax outflows inside the horizon — the cash forecast's input."""
    as_of = as_of or date.today()
    end = as_of + timedelta(days=horizon_days)
    fx = FXConverter(db, as_of)

    rows = (
        db.query(TaxObligation)
        .filter(
            TaxObligation.due_date.isnot(None),
            TaxObligation.due_date <= end,
            TaxObligation.status.in_(("open", "filed", "disputed")),
        )
        .all()
    )
    out = []
    for t in rows:
        if t.outstanding <= 0:
            continue
        egp = fx.try_to_egp(t.outstanding, t.currency, as_of)
        if not egp:
            continue
        # Anything already past due can land any day — put it at the front.
        when = max(t.due_date, as_of)
        out.append(
            {
                "date": when.isoformat(),
                "tax_type": t.tax_type,
                "label": FILING_CALENDAR.get(t.tax_type, {}).get("label", t.tax_type),
                "period": t.period,
                "amount_egp": round(egp, 2),
                "overdue": t.due_date < as_of,
            }
        )
    return sorted(out, key=lambda x: x["date"])


def filing_calendar(db: Session, months: int = 6, as_of: date | None = None) -> dict:
    """The recurring filing cadence, so a missed return is visible before it's late."""
    as_of = as_of or date.today()
    return {
        "as_of": as_of.isoformat(),
        "calendar": [
            {"tax_type": k, **v} for k, v in FILING_CALENDAR.items()
        ],
        "warning": "Filing deadlines and rates change with each finance law. "
                   "Verify every entry marked VERIFY against the current text "
                   "before relying on it.",
    }


def ingest_governmental_dues(db: Session, as_of: date | None = None,
                             replace: bool = True) -> dict:
    """Load the Business Plan's Governmental Dues balances as obligations.

    The sheet carries balances but leaves every settlement year at zero, so the
    obligations land unscheduled: real money, no date. That is exactly how they
    are reported.
    """
    as_of = as_of or date.today()
    rows = (
        db.query(FinancialLine)
        .filter(FinancialLine.statement == "DUES", FinancialLine.period == "balance")
        .all()
    )
    if not rows:
        return {
            "loaded": 0,
            "note": "No governmental dues found. Re-ingest the Business Plan — the "
                    "'Governmental dues plan' sheet is read into statement='DUES'.",
        }

    if replace:
        db.query(TaxObligation).filter(
            TaxObligation.source_file.like("%BP%")
        ).delete(synchronize_session=False)

    loaded, total = 0, 0.0
    for r in rows:
        label = (r.line_item or "").strip()
        if not label or label.lower() == "total":
            continue
        amount = abs(r.value) * 1000  # sheet is KEGP, stored negative as a liability
        if not amount:
            continue
        tax_type = DUES_MAP.get(label.lower(), "other")
        db.add(
            TaxObligation(
                tax_type=tax_type,
                description=label,
                period=r.period,
                authority="ETA",
                currency="EGP",
                amount_due=amount,
                due_date=None,
                status="unscheduled",
                notes="From the Business Plan Governmental Dues sheet. The sheet "
                      "gives a balance but no settlement date.",
                source_file=r.source_file or "Aresco BP",
            )
        )
        loaded += 1
        total += amount

    db.commit()
    return {
        "loaded": loaded,
        "total_egp": round(total, 2),
        "note": f"{loaded} obligations loaded as unscheduled — the source sheet's "
                f"settlement columns are all zero. They will not appear in the "
                f"dated cash forecast until a due date is set.",
    }
