"""Rolling daily cash flow projection (30 / 60 / 90 days).

Combines every committed and expected movement onto one timeline:

  opening bank balance
  + expected customer collections   (probability-weighted, override-aware)
  + forecast inflows                (contracted but not yet invoiced)
  + manual inflows
  - scheduled post-dated cheques    (delivered and on hand)
  - payroll and other planned costs
  - manual outflows
  = projected closing balance, day by day

Each day carries its own line items, so a shortage can be traced to the exact
cheque or payroll run that caused it rather than just flagged.
"""

from collections import defaultdict
from datetime import date, timedelta
from calendar import monthrange
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from api.config import settings
from api.models import (
    CashInForecast, Check, ExpenseForecast, ManualCashFlow, Receivable,
)
from engine.cash import bank_balances, latest_check_snapshot
from engine.fx import FXConverter


def _expense_days(start: date, end: date, expenses: list[ExpenseForecast], fx: FXConverter):
    """Spread monthly expense lines onto their pay day within the horizon."""
    events: dict[date, list] = defaultdict(list)
    for e in expenses:
        if not e.monthly:
            continue
        cursor = date(start.year, start.month, 1)
        while cursor <= end:
            last_day = monthrange(cursor.year, cursor.month)[1]
            pay_day = date(cursor.year, cursor.month, min(e.pay_day_of_month or 28, last_day))
            if start <= pay_day <= end:
                events[pay_day].append(
                    {
                        "type": "expense",
                        "label": e.category,
                        "amount_egp": round(fx.to_egp(e.monthly, e.currency, pay_day), 2),
                    }
                )
            cursor = (
                date(cursor.year + 1, 1, 1)
                if cursor.month == 12
                else date(cursor.year, cursor.month + 1, 1)
            )
    return events


def daily_forecast(db: Session, horizon_days: int = 90, as_of: date | None = None,
                   include_unweighted: bool = False) -> dict:
    """Build the day-by-day projection."""
    balances = bank_balances(db, as_of)
    if balances["as_of"] is None:
        return {"as_of": None, "days": [], "warnings": ["No bank balances ingested"]}

    start = date.fromisoformat(balances["as_of"])
    end = start + timedelta(days=horizon_days)
    fx = FXConverter(db, start)
    warnings: list[str] = []

    events: dict[date, list] = defaultdict(list)

    # --- Outflows: post-dated cheques -----------------------------------
    snapshot = latest_check_snapshot(db)
    if snapshot:
        if snapshot < start - timedelta(days=7):
            warnings.append(
                f"Cheque list is from {snapshot.isoformat()} but balances are from "
                f"{start.isoformat()} — the cheque data may be stale."
            )
        checks = (
            db.query(Check)
            .filter(
                Check.snapshot_date == snapshot,
                Check.cleared.is_(False),
                Check.check_date.isnot(None),
            )
            .all()
        )
        for c in checks:
            amount = fx.to_egp(c.value, c.currency, start)
            # Cheques already past their date can present at any moment, so
            # they sit on day one rather than being excluded as historic.
            when = max(c.check_date, start)
            if when > end:
                continue
            events[when].append(
                {
                    "type": "check",
                    "label": f"{c.supplier_name or 'cheque'} #{c.check_number}",
                    "amount_egp": -round(amount, 2),
                    "delivered": c.delivered,
                    "overdue": c.check_date < start,
                }
            )
    else:
        warnings.append("No cheque list ingested — outflows exclude issued cheques.")

    # --- Inflows: expected AR collections --------------------------------
    ar_snapshot = db.query(func.max(Receivable.snapshot_date)).scalar()
    if ar_snapshot:
        receivables = (
            db.query(Receivable)
            .options(joinedload(Receivable.forecast))
            .filter(Receivable.snapshot_date == ar_snapshot)
            .all()
        )
        missing_forecast = 0
        for r in receivables:
            fc = r.forecast
            if fc is None:
                missing_forecast += 1
                continue
            when = fc.effective_date
            if when < start:
                when = start
            if when > end:
                continue
            prob = fc.effective_probability
            events[when].append(
                {
                    "type": "collection",
                    "label": r.customer_name,
                    "amount_egp": round(r.amount_egp * prob, 2),
                    "gross_egp": round(r.amount_egp, 2),
                    "probability": round(prob, 4),
                    "is_override": fc.override_date is not None,
                }
            )
        if missing_forecast:
            warnings.append(
                f"{missing_forecast} receivables have no collection forecast — "
                f"run POST /receivables/forecast/compute."
            )
    else:
        warnings.append("No receivables ingested — inflows exclude AR collections.")

    # --- Inflows: contracted but not yet invoiced ------------------------
    fc_snapshot = db.query(func.max(CashInForecast.snapshot_date)).scalar()
    if fc_snapshot:
        rows = db.query(CashInForecast).filter(
            CashInForecast.snapshot_date == fc_snapshot
        ).all()
        clustered: dict[date, float] = defaultdict(float)
        for f in rows:
            when = f.expected_date or start
            if when < start:
                when = start
            if when > end:
                continue
            amount = f.amount_egp * (f.probability or 1.0)
            clustered[when] += amount
            events[when].append(
                {
                    "type": "forecast_inflow",
                    "label": f.project_name,
                    "amount_egp": round(amount, 2),
                    "gross_egp": round(f.amount_egp, 2),
                    "probability": f.probability or 1.0,
                }
            )
        # A single date carrying most of the forecast inflow is a placeholder
        # the finance team hasn't phased yet, not a real expectation. Treating
        # it as one is how a projection ends up saying the company is flush on
        # a day it isn't.
        for when, amount in clustered.items():
            count = sum(
                1 for e in events[when] if e.get("type") == "forecast_inflow"
            )
            total_forecast = sum(
                f.amount_egp * (f.probability or 1.0) for f in rows
            )
            if count >= 3 and total_forecast and amount / total_forecast > 0.5:
                warnings.append(
                    f"{count} forecast inflows totalling {amount:,.0f} EGP all "
                    f"share the date {when.isoformat()} — this looks like an "
                    f"unphased placeholder. Set real expected dates before "
                    f"relying on the projection past that day."
                )
                break

    # --- Outflows: planned operating costs -------------------------------
    exp_snapshot = db.query(func.max(ExpenseForecast.snapshot_date)).scalar()
    if exp_snapshot:
        expenses = db.query(ExpenseForecast).filter(
            ExpenseForecast.snapshot_date == exp_snapshot
        ).all()
        unpriced = [e.category for e in expenses if not e.monthly]
        if unpriced:
            warnings.append(
                "Expense lines with no monthly amount are excluded from the "
                "projection: " + ", ".join(unpriced)
            )
        for when, items in _expense_days(start, end, expenses, fx).items():
            for item in items:
                item["amount_egp"] = -item["amount_egp"]
                events[when].append(item)

    # --- Outflows: purchase orders, tax, trade-finance commission ---------
    # These three were the forecast's blind spot: committed money that has not
    # reached the payables ledger, so a payables-driven projection cannot see it.
    from engine import procurement, tax as tax_engine, trade_finance

    try:
        for s_ in procurement.payment_schedule(db, horizon_days, start):
            when = date.fromisoformat(s_["date"])
            events[when].append({
                "type": "purchase_order",
                "label": f"{s_['supplier']} · {s_['po_number']}",
                "amount_egp": -s_["amount_egp"],
                "estimated": s_.get("estimated", False),
            })
    except Exception as exc:
        warnings.append(f"Purchase orders excluded from the projection: {exc}")

    try:
        for s_ in tax_engine.due_schedule(db, horizon_days, start):
            when = date.fromisoformat(s_["date"])
            events[when].append({
                "type": "tax",
                "label": s_["label"] + (f" · {s_['period']}" if s_["period"] else ""),
                "amount_egp": -s_["amount_egp"],
                "overdue": s_.get("overdue", False),
            })
    except Exception as exc:
        warnings.append(f"Tax obligations excluded from the projection: {exc}")

    try:
        for c in trade_finance.commission_schedule(db, horizon_days, start):
            when = date.fromisoformat(c["date"])
            events[when].append({
                "type": "commission",
                "label": f"{c['instrument']} commission · {c['counterparty']}",
                "amount_egp": -c["amount_egp"],
            })
    except Exception as exc:
        warnings.append(f"Trade-finance commission excluded from the projection: {exc}")

    # --- Inflows: client advances falling due -----------------------------
    try:
        dp = trade_finance.down_payments(db, direction="received", as_of=start)
        for d in dp.get("down_payments", []):
            if d["status"] != "expected" or not d["expected_date"]:
                continue
            when = date.fromisoformat(d["expected_date"])
            if when < start:
                when = start
            if when > end:
                continue
            events[when].append({
                "type": "down_payment",
                "label": f"advance · {d['counterparty']}",
                "amount_egp": d["amount_egp"],
            })
    except Exception as exc:
        warnings.append(f"Client advances excluded from the projection: {exc}")

    # An unscheduled obligation is real money with no date, so it cannot enter a
    # dated projection — but leaving it unsaid would make the position look safer
    # than it is.
    try:
        tx = tax_engine.obligations(db, as_of=start)
        if tx.get("unscheduled_egp"):
            warnings.append(
                f"{tx['unscheduled_egp']:,.0f} EGP of tax and governmental dues have "
                f"no settlement date and are NOT in this projection — it is "
                f"optimistic by at least that amount."
            )
        if tx.get("overdue_egp"):
            warnings.append(
                f"{tx['overdue_egp']:,.0f} EGP of tax is already past due and is "
                f"placed on day one."
            )
    except Exception:
        pass

    # --- Manual entries ---------------------------------------------------
    for m in db.query(ManualCashFlow).filter(
        ManualCashFlow.flow_date.between(start, end)
    ).all():
        amount = fx.to_egp(m.amount, m.currency, m.flow_date) * (m.probability or 1.0)
        events[m.flow_date].append(
            {
                "type": "manual",
                "label": m.description,
                "amount_egp": round(amount if m.direction == "in" else -amount, 2),
            }
        )

    # --- Roll the balance forward ----------------------------------------
    running = balances["total_egp"]
    threshold = settings.shortage_buffer_egp
    days, shortages = [], []
    cursor = start

    while cursor <= end:
        items = events.get(cursor, [])
        inflow = sum(i["amount_egp"] for i in items if i["amount_egp"] > 0)
        outflow = sum(-i["amount_egp"] for i in items if i["amount_egp"] < 0)
        opening = running
        running += inflow - outflow
        short = running < threshold

        day = {
            "date": cursor.isoformat(),
            "opening_egp": round(opening, 2),
            "inflow_egp": round(inflow, 2),
            "outflow_egp": round(outflow, 2),
            "net_egp": round(inflow - outflow, 2),
            "closing_egp": round(running, 2),
            "shortage": short,
            "items": sorted(items, key=lambda i: i["amount_egp"]),
        }
        if short:
            shortages.append(
                {
                    "date": cursor.isoformat(),
                    "closing_egp": round(running, 2),
                    "shortfall_egp": round(threshold - running, 2),
                    "driven_by": [
                        i["label"] for i in sorted(items, key=lambda i: i["amount_egp"])[:3]
                        if i["amount_egp"] < 0
                    ],
                }
            )
        days.append(day)
        cursor += timedelta(days=1)

    windows = {}
    for w in (30, 60, 90):
        if w > horizon_days:
            continue
        window = days[:w]
        windows[f"{w}d"] = {
            "inflow_egp": round(sum(d["inflow_egp"] for d in window), 2),
            "outflow_egp": round(sum(d["outflow_egp"] for d in window), 2),
            "net_egp": round(sum(d["net_egp"] for d in window), 2),
            "closing_egp": window[-1]["closing_egp"] if window else None,
            "min_balance_egp": min((d["closing_egp"] for d in window), default=None),
            "shortage_days": sum(1 for d in window if d["shortage"]),
        }

    return {
        "as_of": start.isoformat(),
        "horizon_days": horizon_days,
        "opening_balance_egp": balances["total_egp"],
        "closing_balance_egp": round(running, 2),
        "min_balance_egp": min((d["closing_egp"] for d in days), default=None),
        "first_shortage": shortages[0]["date"] if shortages else None,
        "shortage_days": len(shortages),
        "shortages": shortages,
        "windows": windows,
        "days": days if include_unweighted else [
            {k: v for k, v in d.items() if k != "items"} | {"items": d["items"]}
            for d in days
        ],
        "warnings": warnings,
    }


def summary(db: Session, horizon_days: int = 90) -> dict:
    """Compact version for the dashboard header — no per-day line items."""
    full = daily_forecast(db, horizon_days)
    return {
        k: v for k, v in full.items()
        if k not in {"days", "shortages"}
    } | {
        "shortages": full.get("shortages", [])[:10],
        "curve": [
            {"date": d["date"], "closing_egp": d["closing_egp"], "shortage": d["shortage"]}
            for d in full.get("days", [])
        ],
    }
