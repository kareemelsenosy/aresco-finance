"""Letters of guarantee, letters of credit and down payments.

These three belong together because they move as one transaction in ARESCO's
world: a client advance arrives against an advance-payment guarantee we issued,
and the guarantee's cash margin is carved out of the bank balance the moment the
bank issues it.

Three treasury effects the cash position was blind to before:

  restricted cash   an issued facility's margin sits in the bank but cannot be
                    spent. It is deducted from net available cash — but only
                    when the margin account is one the daily bank workbook
                    actually lists, otherwise deducting it double-counts.
  commission        charged periodically on the face value for the life of the
                    facility, a small recurring outflow that adds up.
  expiry            an expiring guarantee is either renewed (commission
                    continues) or released (margin comes back). Both are cash
                    events, and neither happens by itself.
"""

from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.models import DownPayment, TradeFinanceFacility
from engine.fx import FXConverter

LIVE_STATUSES = ("active",)
EXPIRY_WARN_DAYS = 60


def _fx(db: Session, as_of: date | None = None) -> FXConverter:
    return FXConverter(db, as_of or date.today())


def _facility_row(f: TradeFinanceFacility, fx: FXConverter, as_of: date) -> dict:
    face_egp = fx.try_to_egp(f.face_value, f.currency, as_of)
    margin = f.margin_amount or ((f.face_value or 0.0) * (f.margin_pct or 0.0))
    margin_egp = fx.try_to_egp(margin, f.currency, as_of)
    days = (f.expiry_date - as_of).days if f.expiry_date else None
    return {
        "id": f.id,
        "reference": f.reference,
        "instrument": f.instrument,
        "direction": f.direction,
        "counterparty_type": f.counterparty_type,
        "counterparty": f.counterparty,
        "project": f.project_name,
        "purpose": f.purpose,
        "bank": f.bank_name,
        "currency": f.currency,
        "face_value": f.face_value,
        "face_value_egp": round(face_egp, 2) if face_egp is not None else None,
        "margin_pct": f.margin_pct,
        "margin_amount": round(margin, 2),
        "margin_egp": round(margin_egp, 2) if margin_egp is not None else None,
        "margin_in_bank_balance": f.margin_in_bank_balance,
        "margin_account": f.margin_account,
        "commission_rate_pa": f.commission_rate_pa,
        "issue_date": f.issue_date.isoformat() if f.issue_date else None,
        "expiry_date": f.expiry_date.isoformat() if f.expiry_date else None,
        "days_to_expiry": days,
        "expiring_soon": days is not None and 0 <= days <= EXPIRY_WARN_DAYS,
        "expired": days is not None and days < 0 and f.status == "active",
        "auto_extend": f.auto_extend,
        "lc_type": f.lc_type,
        "usance_days": f.usance_days,
        "latest_shipment_date": f.latest_shipment_date.isoformat() if f.latest_shipment_date else None,
        "status": f.status,
        "notes": f.notes,
    }


def facilities(db: Session, instrument: str | None = None,
               direction: str | None = None, counterparty_type: str | None = None,
               status: str | None = None, as_of: date | None = None) -> dict:
    """The L/G and L/C register, filterable by instrument and side."""
    as_of = as_of or date.today()
    q = db.query(TradeFinanceFacility)
    if instrument:
        q = q.filter(TradeFinanceFacility.instrument == instrument.upper())
    if direction:
        q = q.filter(TradeFinanceFacility.direction == direction)
    if counterparty_type:
        q = q.filter(TradeFinanceFacility.counterparty_type == counterparty_type)
    if status:
        q = q.filter(TradeFinanceFacility.status == status)

    fx = _fx(db, as_of)
    rows = [_facility_row(f, fx, as_of) for f in q.order_by(TradeFinanceFacility.expiry_date).all()]

    def total(items, key):
        return round(sum(i[key] or 0.0 for i in items), 2)

    issued = [r for r in rows if r["direction"] == "issued"]
    received = [r for r in rows if r["direction"] == "received"]
    live = [r for r in rows if r["status"] in LIVE_STATUSES]

    return {
        "as_of": as_of.isoformat(),
        "facilities": rows,
        "count": len(rows),
        "issued": {
            "count": len(issued),
            "face_egp": total(issued, "face_value_egp"),
            "margin_egp": total(issued, "margin_egp"),
        },
        "received": {
            "count": len(received),
            "face_egp": total(received, "face_value_egp"),
        },
        "expiring_soon": [r for r in live if r["expiring_soon"]],
        "expired_still_active": [r for r in live if r["expired"]],
        "missing_rates": sorted(fx.missing),
        "empty": not rows,
    }


def restricted_cash(db: Session, as_of: date | None = None) -> dict:
    """Cash margin tied up against issued facilities.

    Only margins flagged as sitting inside an account the daily bank workbook
    reports are deducted from the cash position. A margin held in an account the
    workbook does not list was never in the balance to begin with, so deducting
    it would understate available cash by exactly the same amount that ignoring
    a listed one overstates it.
    """
    as_of = as_of or date.today()
    fx = _fx(db, as_of)
    rows = (
        db.query(TradeFinanceFacility)
        .filter(
            TradeFinanceFacility.direction == "issued",
            TradeFinanceFacility.status.in_(LIVE_STATUSES),
        )
        .all()
    )

    deductible = 0.0
    off_balance = 0.0
    by_instrument: dict[str, float] = defaultdict(float)
    by_bank: dict[str, float] = defaultdict(float)
    unflagged: list[dict] = []

    for f in rows:
        margin = f.margin_amount or ((f.face_value or 0.0) * (f.margin_pct or 0.0))
        if not margin:
            continue
        egp = fx.try_to_egp(margin, f.currency, as_of)
        if egp is None:
            continue
        by_instrument[f.instrument] += egp
        by_bank[f.bank_name or "unallocated"] += egp
        if f.margin_in_bank_balance:
            deductible += egp
        else:
            off_balance += egp
            unflagged.append(
                {
                    "reference": f.reference,
                    "counterparty": f.counterparty,
                    "bank": f.bank_name,
                    "margin_egp": round(egp, 2),
                }
            )

    return {
        "as_of": as_of.isoformat(),
        "total_margin_egp": round(deductible + off_balance, 2),
        "deducted_from_cash_egp": round(deductible, 2),
        "held_outside_reported_balances_egp": round(off_balance, 2),
        "by_instrument": {k: round(v, 2) for k, v in by_instrument.items()},
        "by_bank": {k: round(v, 2) for k, v in by_bank.items()},
        "unflagged": unflagged,
        "note": (
            f"{len(unflagged)} margin(s) totalling {off_balance:,.0f} EGP are not "
            f"flagged as sitting inside a reported bank balance, so they are not "
            f"deducted. If the margin account is one the daily workbook lists, set "
            f"margin_in_bank_balance on those facilities."
            if unflagged else None
        ),
        "missing_rates": sorted(fx.missing),
    }


def commission_schedule(db: Session, horizon_days: int = 90,
                        as_of: date | None = None) -> list[dict]:
    """Commission charges falling due inside the horizon.

    Charged per period on the face value at the annual rate, for as long as the
    facility is live, stopping at expiry unless it auto-extends.
    """
    as_of = as_of or date.today()
    end = as_of + timedelta(days=horizon_days)
    fx = _fx(db, as_of)

    out: list[dict] = []
    rows = (
        db.query(TradeFinanceFacility)
        .filter(
            TradeFinanceFacility.direction == "issued",
            TradeFinanceFacility.status.in_(LIVE_STATUSES),
            TradeFinanceFacility.commission_rate_pa > 0,
        )
        .all()
    )
    for f in rows:
        months = f.commission_period_months or 3
        if months <= 0:
            continue
        charge = (f.face_value or 0.0) * (f.commission_rate_pa or 0.0) * months / 12.0
        charge_egp = fx.try_to_egp(charge, f.currency, as_of)
        if not charge_egp:
            continue
        # Walk forward from the issue date on the commission cycle.
        cursor = f.issue_date or as_of
        guard = 0
        while cursor <= end and guard < 200:
            guard += 1
            if cursor >= as_of and (
                f.expiry_date is None or cursor <= f.expiry_date or f.auto_extend
            ):
                out.append(
                    {
                        "date": cursor.isoformat(),
                        "reference": f.reference,
                        "instrument": f.instrument,
                        "counterparty": f.counterparty,
                        "bank": f.bank_name,
                        "amount_egp": round(charge_egp, 2),
                    }
                )
            # advance `months` calendar months
            y, m = cursor.year, cursor.month + months
            y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
            day = min(cursor.day, [31, 29 if y % 4 == 0 else 28, 31, 30, 31, 30,
                                   31, 31, 30, 31, 30, 31][m - 1])
            cursor = date(y, m, day)
    return sorted(out, key=lambda x: x["date"])


# --- down payments -------------------------------------------------------

def down_payments(db: Session, direction: str | None = None,
                  as_of: date | None = None) -> dict:
    """Advances received from clients and paid to suppliers."""
    as_of = as_of or date.today()
    q = db.query(DownPayment)
    if direction:
        q = q.filter(DownPayment.direction == direction)
    fx = _fx(db, as_of)

    rows = []
    for d in q.order_by(DownPayment.expected_date).all():
        egp = d.amount_egp or fx.try_to_egp(d.amount, d.currency, as_of) or 0.0
        outstanding_egp = egp * (
            d.outstanding / d.amount if d.amount else 0.0
        )
        rows.append(
            {
                "id": d.id,
                "direction": d.direction,
                "counterparty": d.counterparty,
                "counterparty_type": d.counterparty_type,
                "project": d.project_name,
                "reference": d.reference,
                "currency": d.currency,
                "amount": d.amount,
                "amount_egp": round(egp, 2),
                "pct_of_contract": d.pct_of_contract,
                "expected_date": d.expected_date.isoformat() if d.expected_date else None,
                "received_date": d.received_date.isoformat() if d.received_date else None,
                "status": d.status,
                "recovery_pct": d.recovery_pct,
                "recovered_amount": d.recovered_amount,
                "outstanding": round(d.outstanding, 2),
                "outstanding_egp": round(outstanding_egp, 2),
                "guarantee_id": d.guarantee_id,
                "guarantee_ref": d.guarantee.reference if d.guarantee else None,
                "unsecured": d.direction == "received" and d.guarantee_id is None,
            }
        )

    received = [r for r in rows if r["direction"] == "received"]
    paid = [r for r in rows if r["direction"] == "paid"]
    expected = [r for r in rows if r["status"] == "expected"]

    return {
        "as_of": as_of.isoformat(),
        "down_payments": rows,
        "received": {
            "count": len(received),
            "total_egp": round(sum(r["amount_egp"] for r in received), 2),
            "outstanding_egp": round(sum(r["outstanding_egp"] for r in received), 2),
        },
        "paid": {
            "count": len(paid),
            "total_egp": round(sum(r["amount_egp"] for r in paid), 2),
        },
        "expected_inflow_egp": round(
            sum(r["amount_egp"] for r in expected if r["direction"] == "received"), 2
        ),
        "unsecured": [r for r in received if r["unsecured"]],
        "note": (
            "Advances received are cash today but unwind against future progress "
            "invoices — the outstanding column is what is still to be recovered, "
            "and the collection forecast should not also expect it at full value."
        ),
        "empty": not rows,
    }


def summary(db: Session, as_of: date | None = None) -> dict:
    """Headline numbers for the trade finance screen."""
    as_of = as_of or date.today()
    fac = facilities(db, as_of=as_of)
    rc = restricted_cash(db, as_of)
    dp = down_payments(db, as_of=as_of)
    comm = commission_schedule(db, 90, as_of)

    lg = [f for f in fac["facilities"] if f["instrument"] == "LG"]
    lc = [f for f in fac["facilities"] if f["instrument"] == "LC"]

    return {
        "as_of": as_of.isoformat(),
        "lg": {
            "count": len(lg),
            "issued_face_egp": round(
                sum(f["face_value_egp"] or 0 for f in lg if f["direction"] == "issued"), 2),
            "received_face_egp": round(
                sum(f["face_value_egp"] or 0 for f in lg if f["direction"] == "received"), 2),
        },
        "lc": {
            "count": len(lc),
            "import_face_egp": round(
                sum(f["face_value_egp"] or 0 for f in lc if f["direction"] == "issued"), 2),
            "export_face_egp": round(
                sum(f["face_value_egp"] or 0 for f in lc if f["direction"] == "received"), 2),
        },
        "restricted_cash": rc,
        "commission_90d_egp": round(sum(c["amount_egp"] for c in comm), 2),
        "expiring_soon": fac["expiring_soon"],
        "expired_still_active": fac["expired_still_active"],
        "down_payments": {
            "received_outstanding_egp": dp["received"]["outstanding_egp"],
            "expected_inflow_egp": dp["expected_inflow_egp"],
            "unsecured_count": len(dp["unsecured"]),
        },
        "empty": fac["empty"] and dp["empty"],
    }
