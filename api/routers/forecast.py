"""Rolling daily cash flow projection and manual cash flow entries."""

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import ExpenseForecast, ManualCashFlow
from engine import forecast as fc

router = APIRouter(prefix="/forecast", tags=["forecast"])


@router.get("/daily")
def daily(
    horizon_days: int = Query(90, ge=1, le=365),
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    """Day-by-day projection with the line items driving each day."""
    return fc.daily_forecast(db, horizon_days, as_of)


@router.get("/summary")
def summary(
    horizon_days: int = Query(90, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Rolling 30 / 60 / 90 totals, shortage warnings and the balance curve."""
    return fc.summary(db, horizon_days)


@router.get("/expenses")
def expenses(db: Session = Depends(get_db)):
    from sqlalchemy import func

    snapshot = db.query(func.max(ExpenseForecast.snapshot_date)).scalar()
    if snapshot is None:
        return {"snapshot": None, "lines": []}
    rows = db.query(ExpenseForecast).filter(
        ExpenseForecast.snapshot_date == snapshot
    ).all()
    return {
        "snapshot": snapshot.isoformat(),
        "lines": [
            {
                "id": r.id,
                "category": r.category,
                "monthly": r.monthly,
                "quarterly": r.quarterly,
                "annual": r.annual,
                "currency": r.currency,
                "pay_day_of_month": r.pay_day_of_month,
                "in_projection": bool(r.monthly),
            }
            for r in rows
        ],
        "note": "Lines with no monthly amount are excluded from the projection.",
    }


@router.put("/expenses/{expense_id}")
def update_expense(
    expense_id: int,
    monthly: float | None = Body(None),
    pay_day_of_month: int | None = Body(None, ge=1, le=31),
    db: Session = Depends(get_db),
):
    """Quantify or re-time a planned cost so it enters the projection."""
    row = db.query(ExpenseForecast).filter(ExpenseForecast.id == expense_id).first()
    if row is None:
        raise HTTPException(404, f"Expense line {expense_id} not found")
    if monthly is not None:
        row.monthly = monthly
    if pay_day_of_month is not None:
        row.pay_day_of_month = pay_day_of_month
    db.commit()
    return {
        "id": row.id,
        "category": row.category,
        "monthly": row.monthly,
        "pay_day_of_month": row.pay_day_of_month,
    }


@router.get("/manual")
def list_manual(db: Session = Depends(get_db)):
    rows = db.query(ManualCashFlow).order_by(ManualCashFlow.flow_date).all()
    return [
        {
            "id": r.id,
            "date": r.flow_date.isoformat(),
            "description": r.description,
            "direction": r.direction,
            "currency": r.currency,
            "amount": r.amount,
            "probability": r.probability,
            "created_by": r.created_by,
        }
        for r in rows
    ]


@router.post("/manual")
def add_manual(
    flow_date: date = Body(...),
    description: str = Body(...),
    direction: str = Body(..., description="in | out"),
    amount: float = Body(..., gt=0),
    currency: str = Body("EGP"),
    probability: float = Body(1.0, ge=0.0, le=1.0),
    created_by: str = Body(""),
    db: Session = Depends(get_db),
):
    """Add a planned movement the workbooks don't carry — a loan drawdown,
    a tax settlement, an equipment purchase."""
    if direction not in {"in", "out"}:
        raise HTTPException(400, "direction must be 'in' or 'out'")
    row = ManualCashFlow(
        flow_date=flow_date, description=description, direction=direction,
        amount=amount, currency=currency, probability=probability,
        created_by=created_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "date": row.flow_date.isoformat(), "description": row.description}


@router.delete("/manual/{flow_id}")
def delete_manual(flow_id: int, db: Session = Depends(get_db)):
    row = db.query(ManualCashFlow).filter(ManualCashFlow.id == flow_id).first()
    if row is None:
        raise HTTPException(404, f"Manual flow {flow_id} not found")
    db.delete(row)
    db.commit()
    return {"deleted": flow_id}
