"""Cash position, cheque register and the post-dated cheque schedule."""

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Check, CheckStatusMap
from engine import cash as cash_engine

router = APIRouter(prefix="/cash", tags=["cash"])


@router.get("/available")
def available(as_of: date | None = None, db: Session = Depends(get_db)):
    """Net available cash: bank balance less delivered and on-hand cheques."""
    return cash_engine.available_cash(db, as_of)


@router.get("/balances")
def balances(as_of: date | None = None, db: Session = Depends(get_db)):
    """Daily balances by bank and currency, consolidated to EGP."""
    return cash_engine.bank_balances(db, as_of)


@router.get("/balances/trend")
def trend(
    days: int = Query(90, ge=1, le=1095),
    currency: str | None = Query(None, description="Omit for consolidated EGP"),
    db: Session = Depends(get_db),
):
    return cash_engine.balance_trend(db, days, currency)


@router.get("/balances/dates")
def balance_dates(db: Session = Depends(get_db)):
    from api.models import BankBalance
    rows = db.query(BankBalance.balance_date).distinct().order_by(
        BankBalance.balance_date.desc()
    ).all()
    return [r[0].isoformat() for r in rows]


@router.get("/checks")
def checks(snapshot: date | None = None, db: Session = Depends(get_db)):
    """Outstanding issued cheques, split delivered vs still on hand."""
    return cash_engine.outstanding_checks(db, snapshot)


@router.get("/checks/aging")
def check_aging(
    snapshot: date | None = None,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    """Aging of cheques issued but not yet delivered to the vendor."""
    return cash_engine.on_hand_aging(db, snapshot, as_of)


@router.get("/checks/schedule")
def check_schedule(
    horizon_days: int = Query(90, ge=1, le=365),
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    """Post-dated cheque calendar with the projected balance after each day."""
    return cash_engine.pdc_schedule(db, horizon_days, as_of)


@router.post("/checks/{check_id}/clear")
def clear_check(
    check_id: int,
    cleared_date: date | None = Body(None, embed=True),
    db: Session = Depends(get_db),
):
    """Mark a cheque as presented and cleared, removing it from the position."""
    row = db.query(Check).filter(Check.id == check_id).first()
    if row is None:
        raise HTTPException(404, f"Cheque {check_id} not found")
    row.cleared = True
    row.cleared_date = cleared_date or date.today()
    db.commit()
    return {
        "id": row.id,
        "check_number": row.check_number,
        "cleared": True,
        "cleared_date": row.cleared_date.isoformat(),
    }


@router.get("/checks/status-map")
def status_map(db: Session = Depends(get_db)):
    """How each raw status the treasury team types maps to delivered / on hand."""
    return [
        {
            "id": s.id,
            "raw_status": s.raw_status,
            "delivered": s.delivered,
            "english_label": s.english_label,
        }
        for s in db.query(CheckStatusMap).all()
    ]


@router.put("/checks/status-map")
def set_status(
    raw_status: str = Body(...),
    delivered: bool = Body(...),
    english_label: str = Body(""),
    reclassify: bool = Body(True, description="Apply to cheques already ingested"),
    db: Session = Depends(get_db),
):
    """Classify a cheque status, and optionally re-tag cheques already loaded."""
    row = db.query(CheckStatusMap).filter(CheckStatusMap.raw_status == raw_status).first()
    if row is None:
        row = CheckStatusMap(raw_status=raw_status)
        db.add(row)
    row.delivered = delivered
    row.english_label = english_label

    updated = 0
    if reclassify:
        for c in db.query(Check).filter(Check.raw_status == raw_status).all():
            c.delivered = delivered
            c.status_known = True
            updated += 1
    db.commit()
    return {
        "raw_status": raw_status,
        "delivered": delivered,
        "checks_reclassified": updated,
    }
