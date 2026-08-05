"""Receivables aging, collection forecasting and RDOH.

Forecast logic, in the order the requirement lists the inputs:

  customer payment terms   -> due date = invoice date + terms
  due dates                -> the base expected collection date
  collection trends        -> shifted by the customer's average days late
  historical behaviour     -> weighted by the customer's collection rate,
                              further discounted by how far past due it already is

Every computed date can be overridden by the receivables team. Overrides are
stored separately from the computed value so a re-run of the model never
silently discards a human judgement.
"""

from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from api.models import (
    CashInForecast, CollectionForecast, Customer, FinancialLine, Receivable,
)

DEFAULT_TERMS_DAYS = 60

# How much of a balance we expect to actually collect, by how overdue it is.
# Applied on top of the customer's own historical collection rate.
BUCKET_PROBABILITY = {
    "current": 0.95,
    "1m": 0.90,
    "2m": 0.75,
    "3m+": 0.50,
    "dormant": 0.10,
    "": 0.80,
}

# Where an overdue balance is expected to land when its due date has passed
OVERDUE_LAG_DAYS = {
    "current": 0, "1m": 15, "2m": 30, "3m+": 60, "dormant": 180, "": 30,
}


def latest_ar_snapshot(db: Session) -> date | None:
    return db.query(func.max(Receivable.snapshot_date)).scalar()


def bucket_for(receivable: Receivable, as_of: date) -> str:
    """Age a receivable from its invoice date when the source didn't label it."""
    if receivable.aging_bucket:
        return receivable.aging_bucket
    if receivable.invoice_date is None:
        return ""
    age = (as_of - receivable.invoice_date).days
    if age <= 30:
        return "current"
    if age <= 60:
        return "1m"
    if age <= 90:
        return "2m"
    return "3m+"


def _customers(db: Session) -> dict[str, Customer]:
    return {c.name: c for c in db.query(Customer).all()}


def compute_forecasts(db: Session, snapshot: date | None = None, recompute: bool = False) -> dict:
    """Derive an expected collection date for every open receivable."""
    snapshot = snapshot or latest_ar_snapshot(db)
    if snapshot is None:
        return {"snapshot": None, "computed": 0, "preserved_overrides": 0}

    customers = _customers(db)
    rows = (
        db.query(Receivable)
        .options(joinedload(Receivable.forecast))
        .filter(Receivable.snapshot_date == snapshot)
        .all()
    )

    computed = preserved = 0
    for r in rows:
        cust = customers.get(r.customer_name)
        terms = (cust.payment_terms_days if cust else None) or DEFAULT_TERMS_DAYS
        days_late = (cust.avg_days_late if cust else 0.0) or 0.0
        cust_rate = (cust.collection_rate if cust else 1.0) or 1.0

        bucket = bucket_for(r, snapshot)
        due = (r.invoice_date + timedelta(days=terms)) if r.invoice_date else None

        if due is None:
            expected = snapshot + timedelta(days=OVERDUE_LAG_DAYS.get(bucket, 30))
            basis = f"no invoice date; {bucket or 'unaged'} bucket lag"
        else:
            expected = due + timedelta(days=round(days_late))
            if expected < snapshot:
                # Already past due — push out by the bucket's typical slip
                expected = snapshot + timedelta(days=OVERDUE_LAG_DAYS.get(bucket, 30))
                basis = (
                    f"due {due.isoformat()} (invoice + {terms}d) already passed; "
                    f"re-based on {bucket or 'unaged'} bucket lag"
                )
            else:
                basis = (
                    f"invoice + {terms}d terms"
                    + (f" + {round(days_late)}d avg slip" if days_late else "")
                )

        probability = round(
            min(1.0, max(0.0, BUCKET_PROBABILITY.get(bucket, 0.8) * cust_rate)), 4
        )

        r.due_date = due
        r.aging_bucket = bucket

        fc = r.forecast
        if fc is None:
            fc = CollectionForecast(receivable_id=r.id)
            db.add(fc)
            r.forecast = fc
        elif fc.override_date and not recompute:
            preserved += 1

        fc.expected_date = expected
        fc.probability = probability
        fc.basis = basis
        computed += 1

    db.commit()
    return {
        "snapshot": snapshot.isoformat(),
        "computed": computed,
        "preserved_overrides": preserved,
    }


def aging_report(db: Session, snapshot: date | None = None, currency: str | None = None) -> dict:
    """AR aging by bucket and by customer, in both original and EGP terms."""
    snapshot = snapshot or latest_ar_snapshot(db)
    if snapshot is None:
        return {"snapshot": None, "buckets": [], "customers": [], "total_egp": 0.0}

    q = db.query(Receivable).filter(Receivable.snapshot_date == snapshot)
    if currency:
        q = q.filter(Receivable.currency == currency)
    rows = q.all()

    buckets: dict[str, dict] = defaultdict(
        lambda: {"total_egp": 0.0, "count": 0, "by_currency": defaultdict(float)}
    )
    customers: dict[str, dict] = defaultdict(
        lambda: {"total_egp": 0.0, "by_currency": defaultdict(float),
                 "by_bucket": defaultdict(float), "category": ""}
    )
    by_currency: dict[str, float] = defaultdict(float)
    total = 0.0

    for r in rows:
        bucket = r.aging_bucket or bucket_for(r, snapshot) or "unaged"
        b = buckets[bucket]
        b["total_egp"] += r.amount_egp
        b["count"] += 1
        b["by_currency"][r.currency] += r.amount

        c = customers[r.customer_name]
        c["total_egp"] += r.amount_egp
        c["by_currency"][r.currency] += r.amount
        c["by_bucket"][bucket] += r.amount_egp
        c["category"] = r.category or c["category"]

        by_currency[r.currency] += r.amount
        total += r.amount_egp

    order = ["current", "1m", "2m", "3m+", "dormant", "unaged", ""]
    bucket_list = sorted(
        (
            {
                "bucket": k,
                "total_egp": round(v["total_egp"], 2),
                "count": v["count"],
                "by_currency": dict(v["by_currency"]),
                "share": round(v["total_egp"] / total, 4) if total else 0.0,
            }
            for k, v in buckets.items()
        ),
        key=lambda x: order.index(x["bucket"]) if x["bucket"] in order else 99,
    )

    customer_list = sorted(
        (
            {
                "customer": k,
                "category": v["category"],
                "total_egp": round(v["total_egp"], 2),
                "by_currency": dict(v["by_currency"]),
                "by_bucket": {bk: round(bv, 2) for bk, bv in v["by_bucket"].items()},
            }
            for k, v in customers.items()
        ),
        key=lambda x: -x["total_egp"],
    )

    return {
        "snapshot": snapshot.isoformat(),
        "buckets": bucket_list,
        "customers": customer_list,
        "by_currency": dict(by_currency),
        "total_egp": round(total, 2),
    }


def collection_forecast(db: Session, snapshot: date | None = None,
                        horizon_days: int = 90) -> dict:
    """Expected collections by date, honouring team overrides."""
    snapshot = snapshot or latest_ar_snapshot(db)
    if snapshot is None:
        return {"snapshot": None, "items": [], "by_date": [], "total_expected_egp": 0.0}

    rows = (
        db.query(Receivable)
        .options(joinedload(Receivable.forecast))
        .filter(Receivable.snapshot_date == snapshot)
        .all()
    )
    horizon_end = snapshot + timedelta(days=horizon_days)

    items = []
    per_date: dict[date, dict] = defaultdict(
        lambda: {"gross_egp": 0.0, "weighted_egp": 0.0, "count": 0}
    )
    total_weighted = 0.0

    for r in rows:
        fc = r.forecast
        if fc is None:
            continue
        expected = fc.effective_date
        prob = fc.effective_probability
        weighted = r.amount_egp * prob

        items.append(
            {
                "receivable_id": r.id,
                "customer": r.customer_name,
                "category": r.category,
                "currency": r.currency,
                "amount": r.amount,
                "amount_egp": round(r.amount_egp, 2),
                "invoice_date": r.invoice_date.isoformat() if r.invoice_date else None,
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "bucket": r.aging_bucket,
                "expected_date": expected.isoformat(),
                "computed_date": fc.expected_date.isoformat(),
                "probability": round(prob, 4),
                "weighted_egp": round(weighted, 2),
                "basis": fc.basis,
                "is_override": fc.override_date is not None
                or fc.override_probability is not None,
                "override_by": fc.override_by,
                "override_note": fc.override_note,
                "override_at": fc.override_at.isoformat() if fc.override_at else None,
            }
        )

        if expected <= horizon_end:
            d = per_date[expected]
            d["gross_egp"] += r.amount_egp
            d["weighted_egp"] += weighted
            d["count"] += 1
            total_weighted += weighted

    items.sort(key=lambda x: x["expected_date"])
    by_date = [
        {
            "date": d.isoformat(),
            "gross_egp": round(v["gross_egp"], 2),
            "weighted_egp": round(v["weighted_egp"], 2),
            "count": v["count"],
        }
        for d, v in sorted(per_date.items())
    ]

    return {
        "snapshot": snapshot.isoformat(),
        "horizon_days": horizon_days,
        "items": items,
        "by_date": by_date,
        "total_expected_egp": round(total_weighted, 2),
        "override_count": sum(1 for i in items if i["is_override"]),
    }


def set_override(db: Session, receivable_id: int, expected_date: date | None,
                 probability: float | None, user: str, note: str) -> dict:
    from datetime import datetime

    r = db.query(Receivable).filter(Receivable.id == receivable_id).first()
    if r is None:
        raise ValueError(f"Receivable {receivable_id} not found")
    fc = r.forecast
    if fc is None:
        fc = CollectionForecast(
            receivable_id=r.id, expected_date=expected_date or r.snapshot_date
        )
        db.add(fc)
        r.forecast = fc

    fc.override_date = expected_date
    fc.override_probability = probability
    fc.override_by = user
    fc.override_note = note
    fc.override_at = datetime.utcnow()
    db.commit()

    return {
        "receivable_id": r.id,
        "customer": r.customer_name,
        "computed_date": fc.expected_date.isoformat(),
        "effective_date": fc.effective_date.isoformat(),
        "effective_probability": round(fc.effective_probability, 4),
        "override_by": user,
    }


def clear_override(db: Session, receivable_id: int) -> dict:
    r = db.query(Receivable).filter(Receivable.id == receivable_id).first()
    if r is None or r.forecast is None:
        raise ValueError(f"Receivable {receivable_id} has no forecast")
    fc = r.forecast
    fc.override_date = None
    fc.override_probability = None
    fc.override_by = ""
    fc.override_note = ""
    fc.override_at = None
    db.commit()
    return {"receivable_id": r.id, "effective_date": fc.effective_date.isoformat()}


def rdoh(db: Session, snapshot: date | None = None, annual_revenue_egp: float | None = None,
         period_days: int = 365) -> dict:
    """Receivables Days on Hand = AR / revenue * days.

    Revenue defaults to the most recent annual figure in the Business Plan.
    A lower RDOH means faster collection; a rising RDOH means either slower
    payers or looser credit control.
    """
    snapshot = snapshot or latest_ar_snapshot(db)
    if snapshot is None:
        return {"snapshot": None, "rdoh_days": None}

    ar_total = (
        db.query(func.sum(Receivable.amount_egp))
        .filter(Receivable.snapshot_date == snapshot)
        .scalar()
    ) or 0.0

    caveats: list[str] = []

    revenue_source = "supplied"
    revenue_scenario = None
    if annual_revenue_egp is None:
        # Prefer a reported actual over a projection; a ratio built on a
        # forecast revenue measures the plan, not the business.
        row = (
            db.query(FinancialLine)
            .filter(
                FinancialLine.statement == "IS",
                FinancialLine.line_item == "Total Revenues",
                FinancialLine.period_type == "annual",
                FinancialLine.scenario == "actual",
            )
            .order_by(FinancialLine.period.desc())
            .first()
        )
        if row is None:
            row = (
                db.query(FinancialLine)
                .filter(
                    FinancialLine.statement == "IS",
                    FinancialLine.line_item == "Total Revenues",
                    FinancialLine.period_type == "annual",
                    FinancialLine.period <= str(snapshot.year),
                )
                .order_by(FinancialLine.period.desc())
                .first()
            )
        if row:
            # Business plan figures are in KEGP; scale to EGP to match AR.
            annual_revenue_egp = row.value * 1000
            revenue_scenario = row.scenario
            revenue_source = f"business plan {row.period} ({row.scenario})"
            if row.scenario == "budget":
                caveats.append(
                    f"Revenue is the {row.period} projection, not a reported "
                    f"actual — the ratio measures the plan, not performance."
                )
            elif int(row.period) < snapshot.year - 1:
                caveats.append(
                    f"Latest reported actual revenue is {row.period}, "
                    f"{snapshot.year - int(row.period)} years before the AR "
                    f"snapshot."
                )

    if not annual_revenue_egp:
        return {
            "snapshot": snapshot.isoformat(),
            "ar_total_egp": round(ar_total, 2),
            "rdoh_days": None,
            "note": "No revenue figure available — ingest the Business Plan or "
                    "pass annual_revenue_egp.",
        }

    # Cross-check the AR base against the balance sheet. The Cash-In workbook is
    # a collections working list, not the AR ledger, so it can be materially
    # smaller than reported AR — and RDOH computed on it would flatter the
    # company badly. Report the gap rather than the ratio alone.
    bs_ar = (
        db.query(FinancialLine)
        .filter(
            FinancialLine.statement == "BS",
            FinancialLine.line_item == "Accounts Receivables",
            FinancialLine.period_type == "annual",
            FinancialLine.period == str(snapshot.year),
        )
        .first()
    )
    bs_ar_egp = bs_ar.value * 1000 if bs_ar else None
    if bs_ar_egp and ar_total and bs_ar_egp > ar_total * 1.25:
        caveats.append(
            f"The AR base is {ar_total:,.0f} EGP from the Cash-In working list, "
            f"but the balance sheet reports {bs_ar_egp:,.0f} EGP of receivables "
            f"for {snapshot.year}. RDOH on the working list understates days "
            f"outstanding by roughly {bs_ar_egp / ar_total:.1f}x. Load the full "
            f"AR ledger for a reportable figure."
        )

    days = ar_total / annual_revenue_egp * period_days
    result = {
        "snapshot": snapshot.isoformat(),
        "ar_total_egp": round(ar_total, 2),
        "annual_revenue_egp": round(annual_revenue_egp, 2),
        "revenue_source": revenue_source,
        "revenue_scenario": revenue_scenario,
        "balance_sheet_ar_egp": round(bs_ar_egp, 2) if bs_ar_egp else None,
        "rdoh_days": round(days, 1),
        "caveats": caveats,
        "reportable": not caveats,
    }
    if bs_ar_egp and annual_revenue_egp:
        result["rdoh_days_on_balance_sheet_ar"] = round(
            bs_ar_egp / annual_revenue_egp * period_days, 1
        )
    if not caveats:
        result["interpretation"] = (
            "faster collection than terms" if days < DEFAULT_TERMS_DAYS
            else "collections slower than standard terms"
        )
    return result


def forecast_inflows(db: Session, snapshot: date | None = None) -> list[dict]:
    """Expected-but-not-yet-invoiced inflows (the Cash-In 'Forecast' rows)."""
    snapshot = snapshot or db.query(func.max(CashInForecast.snapshot_date)).scalar()
    if snapshot is None:
        return []
    rows = (
        db.query(CashInForecast)
        .filter(CashInForecast.snapshot_date == snapshot)
        .order_by(CashInForecast.expected_date)
        .all()
    )
    return [
        {
            "id": r.id,
            "project": r.project_name,
            "category": r.category,
            "currency": r.currency,
            "amount": r.amount,
            "amount_egp": round(r.amount_egp, 2),
            "expected_date": r.expected_date.isoformat() if r.expected_date else None,
            "probability": r.probability,
        }
        for r in rows
    ]
