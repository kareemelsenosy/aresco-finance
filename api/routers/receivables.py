"""AR aging, the collection forecast and its overrides, and RDOH."""

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Customer
from engine import receivables as ar

router = APIRouter(prefix="/receivables", tags=["receivables"])


@router.get("/aging")
def aging(
    snapshot: date | None = None,
    currency: str | None = None,
    db: Session = Depends(get_db),
):
    return ar.aging_report(db, snapshot, currency)


@router.get("/rdoh")
def rdoh(
    snapshot: date | None = None,
    annual_revenue_egp: float | None = Query(
        None, description="Override the revenue used; defaults to the Business Plan"
    ),
    db: Session = Depends(get_db),
):
    """Receivables Days on Hand."""
    return ar.rdoh(db, snapshot, annual_revenue_egp)


@router.post("/forecast/compute")
def compute(
    snapshot: date | None = Body(None, embed=True),
    recompute: bool = Body(
        False, embed=True,
        description="Recompute even where the team has set an override "
                    "(the override itself is never deleted)",
    ),
    db: Session = Depends(get_db),
):
    """Derive expected collection dates from terms, due dates and history."""
    return ar.compute_forecasts(db, snapshot, recompute)


@router.get("/forecast")
def forecast(
    snapshot: date | None = None,
    horizon_days: int = Query(90, ge=1, le=730),
    db: Session = Depends(get_db),
):
    """Expected collections by date, with team overrides applied."""
    return ar.collection_forecast(db, snapshot, horizon_days)


@router.put("/forecast/{receivable_id}/override")
def override(
    receivable_id: int,
    expected_date: date | None = Body(None),
    probability: float | None = Body(None, ge=0.0, le=1.0),
    user: str = Body("", description="Who is making the change"),
    note: str = Body(""),
    db: Session = Depends(get_db),
):
    """Adjust a forecast collection date or confidence.

    The computed value is retained alongside, so recomputing the model never
    destroys the team's judgement.
    """
    try:
        return ar.set_override(db, receivable_id, expected_date, probability, user, note)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.delete("/forecast/{receivable_id}/override")
def clear_override(receivable_id: int, db: Session = Depends(get_db)):
    try:
        return ar.clear_override(db, receivable_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/forecast/inflows")
def inflows(snapshot: date | None = None, db: Session = Depends(get_db)):
    """Contracted inflows that are not yet invoiced receivables."""
    return ar.forecast_inflows(db, snapshot)


@router.get("/customers")
def customers(db: Session = Depends(get_db)):
    return [
        {
            "id": c.id,
            "name": c.name,
            "category": c.category,
            "payment_terms_days": c.payment_terms_days,
            "avg_days_late": c.avg_days_late,
            "collection_rate": c.collection_rate,
            "notes": c.notes,
        }
        for c in db.query(Customer).order_by(Customer.name).all()
    ]


@router.put("/customers/{customer_id}")
def update_customer(
    customer_id: int,
    payment_terms_days: int | None = Body(None),
    avg_days_late: float | None = Body(None),
    collection_rate: float | None = Body(None, ge=0.0, le=1.0),
    notes: str | None = Body(None),
    db: Session = Depends(get_db),
):
    """Set a customer's terms and payment behaviour — these drive the forecast."""
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if c is None:
        raise HTTPException(404, f"Customer {customer_id} not found")
    if payment_terms_days is not None:
        c.payment_terms_days = payment_terms_days
    if avg_days_late is not None:
        c.avg_days_late = avg_days_late
    if collection_rate is not None:
        c.collection_rate = collection_rate
    if notes is not None:
        c.notes = notes
    db.commit()
    return {
        "id": c.id,
        "name": c.name,
        "payment_terms_days": c.payment_terms_days,
        "avg_days_late": c.avg_days_late,
        "collection_rate": c.collection_rate,
        "note": "Run POST /receivables/forecast/compute to apply this.",
    }
