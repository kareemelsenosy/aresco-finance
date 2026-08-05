"""Cash position, outstanding cheques and the post-dated cheque schedule.

The central figure is Net Available Cash:

    Bank balance
      less  cheques delivered to vendors and not yet cleared
      less  cheques issued but still on hand in treasury
    = net available cash

Both cheque classes are deducted because both are committed money — the
difference is only how soon they hit. They are reported separately so the
treasury manager can see how much headroom is genuinely discretionary.
"""

from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.models import BankBalance, Check
from engine.fx import FXConverter

# Aging buckets for cheques sitting on hand, measured from the cheque date
ON_HAND_BUCKETS = [
    ("0-30", 0, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("91-180", 91, 180),
    ("180+", 181, 10_000),
]


def latest_balance_date(db: Session) -> date | None:
    return db.query(func.max(BankBalance.balance_date)).scalar()


def latest_check_snapshot(db: Session) -> date | None:
    return db.query(func.max(Check.snapshot_date)).scalar()


def bank_balances(db: Session, as_of: date | None = None) -> dict:
    """Balances for one day, by bank and currency, plus consolidated EGP.

    Currencies with no rate on file are excluded from `total_egp` and reported
    in `unconverted` — the consolidated figure stays truthful about what it
    covers rather than either guessing a rate or dropping the balance silently.
    """
    as_of = as_of or latest_balance_date(db)
    if as_of is None:
        return {
            "as_of": None, "banks": [], "by_currency": {}, "total_egp": 0.0,
            "unconverted": {}, "missing_rates": [],
        }

    rows = db.query(BankBalance).filter(BankBalance.balance_date == as_of).all()
    fx = FXConverter(db, as_of)

    by_bank: dict[str, dict] = defaultdict(lambda: {"currencies": {}, "total_egp": 0.0})
    by_currency: dict[str, float] = defaultdict(float)
    unconverted: dict[str, float] = defaultdict(float)
    total_egp = 0.0

    for r in rows:
        egp = fx.try_to_egp(r.amount, r.currency, as_of)
        by_bank[r.bank_name]["currencies"][r.currency] = r.amount
        by_currency[r.currency] += r.amount
        if egp is None:
            unconverted[r.currency] += r.amount
        else:
            by_bank[r.bank_name]["total_egp"] += egp
            total_egp += egp

    banks = [
        {"bank": name, **data}
        for name, data in sorted(by_bank.items(), key=lambda kv: -kv[1]["total_egp"])
    ]
    return {
        "as_of": as_of.isoformat(),
        "banks": banks,
        "by_currency": dict(by_currency),
        "total_egp": round(total_egp, 2),
        "unconverted": {k: round(v, 2) for k, v in unconverted.items() if v},
        "missing_rates": sorted(fx.missing),
    }


def balance_trend(db: Session, days: int = 90, currency: str | None = None) -> list[dict]:
    """Daily consolidated balance for the trailing `days` days."""
    end = latest_balance_date(db)
    if end is None:
        return []
    start = end - timedelta(days=days)

    q = db.query(BankBalance).filter(BankBalance.balance_date.between(start, end))
    if currency:
        q = q.filter(BankBalance.currency == currency)
    rows = q.all()

    fx = FXConverter(db, end)
    per_day: dict[date, float] = defaultdict(float)
    for r in rows:
        value = (
            r.amount if currency
            else fx.try_to_egp(r.amount, r.currency, r.balance_date)
        )
        if value is not None:
            per_day[r.balance_date] += value

    return [
        {"date": d.isoformat(), "balance": round(v, 2)}
        for d, v in sorted(per_day.items())
    ]


def outstanding_checks(db: Session, snapshot: date | None = None) -> dict:
    """Every issued cheque not yet cleared, split delivered / on hand."""
    snapshot = snapshot or latest_check_snapshot(db)
    if snapshot is None:
        return {
            "snapshot": None, "delivered": [], "on_hand": [],
            "delivered_total_egp": 0.0, "on_hand_total_egp": 0.0,
            "unmapped_statuses": [],
        }

    rows = (
        db.query(Check)
        .filter(Check.snapshot_date == snapshot, Check.cleared.is_(False))
        .all()
    )
    fx = FXConverter(db, snapshot)

    delivered, on_hand = [], []
    d_total = h_total = 0.0
    unmapped = set()

    for c in rows:
        egp = fx.try_to_egp(c.value, c.currency, snapshot)
        item = {
            "id": c.id,
            "check_number": c.check_number,
            "supplier": c.supplier_name,
            "bank": c.bank_name,
            "currency": c.currency,
            "value": c.value,
            "value_egp": round(egp, 2) if egp is not None else None,
            "check_date": c.check_date.isoformat() if c.check_date else None,
            "status": c.raw_status,
            "status_known": c.status_known,
        }
        if not c.status_known:
            unmapped.add(c.raw_status)
        if c.delivered:
            delivered.append(item)
            d_total += egp or 0.0
        else:
            on_hand.append(item)
            h_total += egp or 0.0

    delivered.sort(key=lambda x: x["check_date"] or "9999")
    on_hand.sort(key=lambda x: x["check_date"] or "9999")

    return {
        "snapshot": snapshot.isoformat(),
        "delivered": delivered,
        "on_hand": on_hand,
        "delivered_total_egp": round(d_total, 2),
        "on_hand_total_egp": round(h_total, 2),
        "unmapped_statuses": sorted(unmapped),
        "missing_rates": sorted(fx.missing),
    }


def on_hand_aging(db: Session, snapshot: date | None = None, as_of: date | None = None) -> dict:
    """Aging of cheques issued but not yet handed to the vendor.

    A cheque sitting on hand well past its own date is a red flag: the vendor
    relationship, not just the cash, is exposed.
    """
    snapshot = snapshot or latest_check_snapshot(db)
    if snapshot is None:
        return {"snapshot": None, "buckets": [], "total_egp": 0.0}
    ref = as_of or snapshot

    rows = (
        db.query(Check)
        .filter(
            Check.snapshot_date == snapshot,
            Check.cleared.is_(False),
            Check.delivered.is_(False),
        )
        .all()
    )
    fx = FXConverter(db, snapshot)

    buckets = {name: {"bucket": name, "count": 0, "total_egp": 0.0, "checks": []}
               for name, _, _ in ON_HAND_BUCKETS}
    buckets["undated"] = {"bucket": "undated", "count": 0, "total_egp": 0.0, "checks": []}
    total = 0.0

    for c in rows:
        egp = fx.try_to_egp(c.value, c.currency, snapshot) or 0.0
        total += egp
        if c.check_date is None:
            key = "undated"
            age = None
        else:
            age = (ref - c.check_date).days
            key = next(
                (n for n, lo, hi in ON_HAND_BUCKETS if lo <= max(age, 0) <= hi),
                "180+",
            )
        b = buckets[key]
        b["count"] += 1
        b["total_egp"] = round(b["total_egp"] + egp, 2)
        b["checks"].append(
            {
                "check_number": c.check_number,
                "supplier": c.supplier_name,
                "value_egp": round(egp, 2),
                "check_date": c.check_date.isoformat() if c.check_date else None,
                "age_days": age,
            }
        )

    ordered = [buckets[n] for n, _, _ in ON_HAND_BUCKETS] + [buckets["undated"]]
    return {
        "snapshot": snapshot.isoformat(),
        "as_of": ref.isoformat(),
        "buckets": [b for b in ordered if b["count"]],
        "total_egp": round(total, 2),
        "missing_rates": sorted(fx.missing),
    }


def available_cash(db: Session, as_of: date | None = None) -> dict:
    """The headline figure: bank balance less every claim already on it.

    Three deductions, not two. Cash collateral pledged against an issued letter
    of guarantee or credit sits in the bank and shows up in the daily balance,
    but the bank will not release it — so a position that stops at cheques
    reports money the company cannot actually spend.
    """
    from engine.trade_finance import restricted_cash

    balances = bank_balances(db, as_of)
    checks = outstanding_checks(db)
    as_of_d = date.fromisoformat(balances["as_of"]) if balances["as_of"] else (as_of or date.today())
    try:
        restricted = restricted_cash(db, as_of_d)
    except Exception:
        restricted = {"deducted_from_cash_egp": 0.0, "total_margin_egp": 0.0,
                      "held_outside_reported_balances_egp": 0.0, "unflagged": [], "note": None}

    bank_total = balances["total_egp"]
    delivered = checks["delivered_total_egp"]
    on_hand = checks["on_hand_total_egp"]
    margins = restricted.get("deducted_from_cash_egp", 0.0)
    net = bank_total - delivered - on_hand - margins

    return {
        "as_of": balances["as_of"],
        "check_snapshot": checks["snapshot"],
        "bank_balance_egp": round(bank_total, 2),
        "less_delivered_checks_egp": round(delivered, 2),
        "less_on_hand_checks_egp": round(on_hand, 2),
        "less_restricted_margins_egp": round(margins, 2),
        "net_available_egp": round(net, 2),
        "net_of_delivered_only_egp": round(bank_total - delivered, 2),
        "restricted": {
            "total_margin_egp": restricted.get("total_margin_egp", 0.0),
            "deducted_egp": round(margins, 2),
            "not_deducted_egp": restricted.get("held_outside_reported_balances_egp", 0.0),
            "note": restricted.get("note"),
        },
        "by_currency": balances["by_currency"],
        "counts": {
            "delivered": len(checks["delivered"]),
            "on_hand": len(checks["on_hand"]),
        },
        "unmapped_statuses": checks["unmapped_statuses"],
        "unconverted": balances["unconverted"],
        "missing_rates": sorted(
            set(balances["missing_rates"]) | set(checks.get("missing_rates", []))
        ),
        "coverage_note": (
            "Balances held in "
            + ", ".join(sorted(set(balances["missing_rates"])
                               | set(checks.get("missing_rates", []))))
            + " are excluded from the EGP total — no exchange rate is on file. "
              "Add one via POST /fx/rates."
            if balances["missing_rates"] or checks.get("missing_rates")
            else None
        ),
    }


def pdc_schedule(db: Session, horizon_days: int = 90, as_of: date | None = None) -> dict:
    """Calendar of future-dated cheques with the projected balance after each day.

    This answers the question the treasury manager actually asks: on which day
    does the bank balance go negative if every cheque presents on its date?
    """
    balances = bank_balances(db, as_of)
    if balances["as_of"] is None:
        return {"as_of": None, "days": [], "first_shortage": None}

    start = date.fromisoformat(balances["as_of"])
    end = start + timedelta(days=horizon_days)
    snapshot = latest_check_snapshot(db)

    rows = (
        db.query(Check)
        .filter(
            Check.snapshot_date == snapshot,
            Check.cleared.is_(False),
            Check.check_date.isnot(None),
        )
        .all()
    )
    fx = FXConverter(db, start)

    per_day: dict[date, list] = defaultdict(list)
    overdue: list = []
    for c in rows:
        egp = fx.try_to_egp(c.value, c.currency, start) or 0.0
        item = {
            "check_number": c.check_number,
            "supplier": c.supplier_name,
            "bank": c.bank_name,
            "amount_egp": round(egp, 2),
            "delivered": c.delivered,
        }
        if c.check_date < start:
            # Already due but uncleared — it can present any day, so it lands
            # on day zero rather than dropping out of the schedule.
            overdue.append(item)
        elif c.check_date <= end:
            per_day[c.check_date].append(item)

    running = balances["total_egp"] - sum(i["amount_egp"] for i in overdue)
    days = []
    if overdue:
        days.append(
            {
                "date": start.isoformat(),
                "checks": overdue,
                "outflow_egp": round(sum(i["amount_egp"] for i in overdue), 2),
                "closing_balance_egp": round(running, 2),
                "shortage": running < 0,
                "note": "cheques already past their date and not yet cleared",
            }
        )

    first_shortage = start.isoformat() if running < 0 else None
    cursor = start + timedelta(days=1)
    while cursor <= end:
        items = per_day.get(cursor, [])
        if items:
            outflow = sum(i["amount_egp"] for i in items)
            running -= outflow
            if running < 0 and first_shortage is None:
                first_shortage = cursor.isoformat()
            days.append(
                {
                    "date": cursor.isoformat(),
                    "checks": items,
                    "outflow_egp": round(outflow, 2),
                    "closing_balance_egp": round(running, 2),
                    "shortage": running < 0,
                }
            )
        cursor += timedelta(days=1)

    return {
        "as_of": start.isoformat(),
        "horizon_days": horizon_days,
        "opening_balance_egp": balances["total_egp"],
        "days": days,
        "total_scheduled_egp": round(balances["total_egp"] - running, 2),
        "closing_balance_egp": round(running, 2),
        "first_shortage": first_shortage,
        "missing_rates": sorted(fx.missing),
    }
