"""IFRS 9 expected credit loss."""

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import ECLPolicy
from engine import ecl as ecl_engine

router = APIRouter(prefix="/ecl", tags=["ecl"])


@router.post("/run")
def run(
    as_of: date | None = Body(None, embed=True),
    overlay: float | None = Body(
        None, embed=True,
        description="Override the forward-looking factor; omit to derive it "
                    "from the Business Plan macro assumptions",
    ),
    user: str = Body("", embed=True),
    persist: bool = Body(True, embed=True),
    db: Session = Depends(get_db),
):
    """Compute lifetime ECL on the open receivables (IFRS 9 simplified approach)."""
    try:
        return ecl_engine.run_ecl(db, as_of, overlay, user, persist)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/preview")
def preview(
    as_of: date | None = None,
    overlay: float | None = None,
    db: Session = Depends(get_db),
):
    """Same calculation without writing a run record — for what-if work."""
    try:
        return ecl_engine.run_ecl(db, as_of, overlay, persist=False)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/runs")
def runs(limit: int = Query(24, ge=1, le=200), db: Session = Depends(get_db)):
    return ecl_engine.run_history(db, limit)


@router.get("/movement")
def movement(db: Session = Depends(get_db)):
    """Period-on-period movement in the loss allowance — the IFRS 7.35 note."""
    return ecl_engine.movement(db)


@router.get("/policy")
def policy(db: Session = Depends(get_db)):
    """The provision matrix: loss rate per aging bucket per segment."""
    ecl_engine.seed_policy(db)
    rows = db.query(ECLPolicy).order_by(ECLPolicy.segment, ECLPolicy.bucket).all()
    return [
        {
            "id": p.id,
            "segment": p.segment,
            "bucket": p.bucket,
            "loss_rate": p.loss_rate,
            "effective_from": p.effective_from.isoformat() if p.effective_from else None,
            "note": p.note,
        }
        for p in rows
    ]


@router.put("/policy")
def set_policy(
    segment: str = Body(..., description="'default' or a receivable category"),
    bucket: str = Body(..., description="current | 1m | 2m | 3m+ | dormant | unaged"),
    loss_rate: float = Body(..., ge=0.0, le=1.0),
    note: str = Body(""),
    db: Session = Depends(get_db),
):
    """Set a loss rate. Calibrate these against actual write-off history."""
    return ecl_engine.set_policy(db, segment, bucket, loss_rate, note)


@router.post("/policy/reset")
def reset_policy(db: Session = Depends(get_db)):
    """Restore the seeded default matrix, discarding calibrations."""
    count = ecl_engine.seed_policy(db, force=True)
    return {"reset": True, "rows": count}
