"""FX rates, banks, and the consolidated dashboard payload."""

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Bank, FXRate
from engine import cash as cash_engine
from engine import forecast as fc
from engine import receivables as ar
from engine.fx import upsert_rate

router = APIRouter(tags=["admin"])


@router.get("/dashboard")
def dashboard(horizon_days: int = Query(90, ge=1, le=365), db: Session = Depends(get_db)):
    """Everything the landing page needs, in one round trip."""
    payload: dict = {}
    warnings: list[str] = []

    def attempt(key, fn):
        try:
            payload[key] = fn()
        except Exception as exc:
            payload[key] = None
            warnings.append(f"{key}: {type(exc).__name__}: {exc}")

    attempt("available_cash", lambda: cash_engine.available_cash(db))
    attempt("balances", lambda: cash_engine.bank_balances(db))
    attempt("balance_trend", lambda: cash_engine.balance_trend(db, 90))
    attempt("check_aging", lambda: cash_engine.on_hand_aging(db))
    attempt("pdc", lambda: cash_engine.pdc_schedule(db, horizon_days))
    attempt("ar_aging", lambda: ar.aging_report(db))
    attempt("rdoh", lambda: ar.rdoh(db))
    attempt("forecast", lambda: fc.summary(db, horizon_days))

    payload["warnings"] = warnings
    return payload


@router.get("/fx/rates")
def rates(currency: str | None = None, db: Session = Depends(get_db)):
    q = db.query(FXRate)
    if currency:
        q = q.filter(FXRate.currency == currency.upper())
    return [
        {
            "id": r.id,
            "date": r.rate_date.isoformat(),
            "currency": r.currency,
            "rate_to_egp": r.rate_to_egp,
            "source": r.source,
        }
        for r in q.order_by(FXRate.rate_date.desc(), FXRate.currency).all()
    ]


@router.get("/fx/current")
def current_rates(as_of: date | None = None, db: Session = Depends(get_db)):
    """The rates actually in force on a date — the latest on or before it.

    Distinct from /fx/rates, which lists every rate on file including the
    business plan's long-dated projections. Reading the newest row out of that
    list gives you the 2035 planning rate, not today's.
    """
    from engine.fx import FXConverter

    when = as_of or date.today()
    fx = FXConverter(db, when)
    out = {"as_of": when.isoformat(), "base": "EGP", "rates": {"EGP": 1.0}, "missing": []}
    for ccy in ("USD", "EUR", "AED"):
        rate = fx.find_rate(ccy, when)
        if rate is None:
            out["missing"].append(ccy)
        else:
            out["rates"][ccy] = rate
    return out


@router.post("/fx/rates")
def add_rate(
    rate_date: date = Body(...),
    currency: str = Body(...),
    rate_to_egp: float = Body(..., gt=0),
    db: Session = Depends(get_db),
):
    """Set a rate. Conversions use the latest rate on or before the value date."""
    row = upsert_rate(db, rate_date, currency, rate_to_egp, source="manual")
    if row is None:
        raise HTTPException(400, "EGP is the base currency and needs no rate")
    db.commit()
    return {
        "date": row.rate_date.isoformat(),
        "currency": row.currency,
        "rate_to_egp": row.rate_to_egp,
    }


@router.get("/banks")
def banks(db: Session = Depends(get_db)):
    return [
        {"id": b.id, "name": b.name, "aliases": b.aliases, "active": b.active}
        for b in db.query(Bank).order_by(Bank.name).all()
    ]


@router.post("/banks")
def add_bank(
    name: str = Body(...),
    aliases: str = Body("", description="Comma-separated spellings used in the workbooks"),
    db: Session = Depends(get_db),
):
    if db.query(Bank).filter(Bank.name == name).first():
        raise HTTPException(409, f"Bank '{name}' already exists")
    row = Bank(name=name, aliases=aliases)
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.get("/data-status")
def data_status(db: Session = Depends(get_db)):
    """What has been loaded, and how fresh it is.

    Worth checking before trusting any figure: a projection built on a
    three-week-old cheque list is a projection built on a three-week-old
    cheque list.
    """
    from sqlalchemy import func
    from api.models import (
        BankBalance, CashInForecast, Check, ExpenseForecast, FinancialLine,
        Receivable,
    )

    def latest(model, column):
        return db.query(func.max(column)).scalar()

    balance_date = latest(BankBalance, BankBalance.balance_date)
    check_date = latest(Check, Check.snapshot_date)
    ar_date = latest(Receivable, Receivable.snapshot_date)

    dates = [d for d in (balance_date, check_date, ar_date) if d]
    spread = (max(dates) - min(dates)).days if len(dates) > 1 else 0

    return {
        "bank_balances": {
            "latest": balance_date.isoformat() if balance_date else None,
            "days_loaded": db.query(BankBalance.balance_date).distinct().count(),
            "rows": db.query(BankBalance).count(),
        },
        "checks": {
            "snapshot": check_date.isoformat() if check_date else None,
            "open": db.query(Check).filter(Check.cleared.is_(False)).count(),
            "unmapped_status": db.query(Check).filter(
                Check.status_known.is_(False)
            ).count(),
        },
        "receivables": {
            "snapshot": ar_date.isoformat() if ar_date else None,
            "rows": db.query(Receivable).count(),
            "forecast_inflows": db.query(CashInForecast).count(),
        },
        "expenses": {"lines": db.query(ExpenseForecast).count()},
        "business_plan": {"lines": db.query(FinancialLine).count()},
        "fx_rates": db.query(FXRate).count(),
        "snapshot_spread_days": spread,
        "note": (
            f"The three cash inputs span {spread} days. Figures combining them "
            f"are only as current as the oldest."
            if spread > 3 else "All cash inputs are from within three days of each other."
        ),
    }
