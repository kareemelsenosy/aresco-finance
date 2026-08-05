"""Trade finance, down payments, tax, procurement, contribution margin, funding.

Every register here ships empty and is filled by the finance team — none of it
is in the five source workbooks, except the governmental dues which the Business
Plan carries as balances with no settlement dates.
"""

import csv
import io
from datetime import date

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import (
    DownPayment, ProjectCost, PurchaseOrder, PurchaseRequisition,
    TaxObligation, TradeFinanceFacility,
)
from engine import funding, margin, procurement
from engine import tax as tax_engine
from engine import trade_finance
from engine.fx import FXConverter

router = APIRouter(tags=["commitments"])


def _date(v):
    return date.fromisoformat(v) if v else None


# =========================================================== trade finance ===

tf = APIRouter(prefix="/trade-finance", tags=["trade finance"])


@tf.get("/summary")
def tf_summary(as_of: date | None = None, db: Session = Depends(get_db)):
    """Headline L/G, L/C, restricted cash and advance figures."""
    return trade_finance.summary(db, as_of)


@tf.get("/facilities")
def tf_facilities(
    instrument: str | None = Query(None, description="LG | LC"),
    direction: str | None = Query(None, description="issued | received"),
    counterparty_type: str | None = Query(None, description="client | supplier"),
    status: str | None = None,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    """The guarantee and credit register, either side."""
    return trade_finance.facilities(db, instrument, direction, counterparty_type, status, as_of)


@tf.get("/restricted-cash")
def tf_restricted(as_of: date | None = None, db: Session = Depends(get_db)):
    """Cash collateral tied up against issued facilities."""
    return trade_finance.restricted_cash(db, as_of)


@tf.get("/commissions")
def tf_commissions(
    horizon_days: int = Query(90, ge=1, le=730),
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    return trade_finance.commission_schedule(db, horizon_days, as_of)


@tf.post("/facilities")
def tf_create(
    instrument: str = Body(..., description="LG | LC"),
    direction: str = Body(..., description="issued | received"),
    counterparty_type: str = Body(..., description="client | supplier"),
    counterparty: str = Body(...),
    face_value: float = Body(..., gt=0),
    currency: str = Body("EGP"),
    reference: str = Body(""),
    purpose: str = Body("", description="bid|advance_payment|performance|retention|maintenance|import|export"),
    bank_name: str = Body(""),
    project_name: str = Body(""),
    margin_pct: float = Body(0.0, ge=0, le=1),
    margin_amount: float = Body(0.0, ge=0),
    margin_in_bank_balance: bool = Body(False),
    margin_account: str = Body(""),
    commission_rate_pa: float = Body(0.0, ge=0, le=1),
    commission_period_months: int = Body(3, ge=1, le=12),
    issue_date: str | None = Body(None),
    expiry_date: str | None = Body(None),
    auto_extend: bool = Body(False),
    lc_type: str = Body("", description="sight | usance"),
    usance_days: int = Body(0),
    latest_shipment_date: str | None = Body(None),
    notes: str = Body(""),
    db: Session = Depends(get_db),
):
    """Register a letter of guarantee or letter of credit.

    `margin_in_bank_balance` matters: set it only when the margin account is one
    the daily bank workbook reports, or the cash position will deduct money it
    never counted.
    """
    if instrument.upper() not in {"LG", "LC"}:
        raise HTTPException(400, "instrument must be LG or LC")
    if direction not in {"issued", "received"}:
        raise HTTPException(400, "direction must be 'issued' or 'received'")

    row = TradeFinanceFacility(
        instrument=instrument.upper(), direction=direction,
        counterparty_type=counterparty_type, counterparty=counterparty,
        reference=reference, purpose=purpose, bank_name=bank_name,
        project_name=project_name, currency=currency, face_value=face_value,
        margin_pct=margin_pct,
        margin_amount=margin_amount or face_value * margin_pct,
        margin_in_bank_balance=margin_in_bank_balance, margin_account=margin_account,
        commission_rate_pa=commission_rate_pa,
        commission_period_months=commission_period_months,
        issue_date=_date(issue_date), expiry_date=_date(expiry_date),
        auto_extend=auto_extend, lc_type=lc_type, usance_days=usance_days,
        latest_shipment_date=_date(latest_shipment_date), notes=notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "reference": row.reference, "instrument": row.instrument}


@tf.put("/facilities/{facility_id}")
def tf_update(
    facility_id: int,
    status: str | None = Body(None),
    margin_in_bank_balance: bool | None = Body(None),
    expiry_date: str | None = Body(None),
    released_date: str | None = Body(None),
    notes: str | None = Body(None),
    db: Session = Depends(get_db),
):
    row = db.query(TradeFinanceFacility).filter(TradeFinanceFacility.id == facility_id).first()
    if row is None:
        raise HTTPException(404, f"Facility {facility_id} not found")
    if status is not None:
        row.status = status
    if margin_in_bank_balance is not None:
        row.margin_in_bank_balance = margin_in_bank_balance
    if expiry_date is not None:
        row.expiry_date = _date(expiry_date)
    if released_date is not None:
        row.released_date = _date(released_date)
    if notes is not None:
        row.notes = notes
    db.commit()
    return {"id": row.id, "status": row.status}


@tf.delete("/facilities/{facility_id}")
def tf_delete(facility_id: int, db: Session = Depends(get_db)):
    row = db.query(TradeFinanceFacility).filter(TradeFinanceFacility.id == facility_id).first()
    if row is None:
        raise HTTPException(404, f"Facility {facility_id} not found")
    db.delete(row)
    db.commit()
    return {"deleted": facility_id}


# ============================================================ down payments ==

dp = APIRouter(prefix="/down-payments", tags=["down payments"])


@dp.get("")
def dp_list(
    direction: str | None = Query(None, description="received | paid"),
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    """Advances received from clients and paid to suppliers."""
    return trade_finance.down_payments(db, direction, as_of)


@dp.post("")
def dp_create(
    direction: str = Body(..., description="received | paid"),
    counterparty: str = Body(...),
    amount: float = Body(..., gt=0),
    currency: str = Body("EGP"),
    counterparty_type: str = Body(""),
    project_name: str = Body(""),
    reference: str = Body(""),
    pct_of_contract: float = Body(0.0, ge=0, le=1),
    expected_date: str | None = Body(None),
    received_date: str | None = Body(None),
    status: str = Body("expected"),
    recovery_pct: float = Body(0.0, ge=0, le=1),
    guarantee_id: int | None = Body(None),
    notes: str = Body(""),
    db: Session = Depends(get_db),
):
    if direction not in {"received", "paid"}:
        raise HTTPException(400, "direction must be 'received' or 'paid'")
    fx = FXConverter(db, _date(received_date) or _date(expected_date) or date.today())
    row = DownPayment(
        direction=direction, counterparty=counterparty,
        counterparty_type=counterparty_type or ("client" if direction == "received" else "supplier"),
        project_name=project_name, reference=reference, currency=currency,
        amount=amount, amount_egp=fx.try_to_egp(amount, currency) or 0.0,
        pct_of_contract=pct_of_contract, expected_date=_date(expected_date),
        received_date=_date(received_date), status=status,
        recovery_pct=recovery_pct, guarantee_id=guarantee_id, notes=notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "counterparty": row.counterparty, "amount_egp": row.amount_egp}


@dp.put("/{dp_id}")
def dp_update(
    dp_id: int,
    status: str | None = Body(None),
    received_date: str | None = Body(None),
    recovered_amount: float | None = Body(None),
    guarantee_id: int | None = Body(None),
    db: Session = Depends(get_db),
):
    """Record receipt, or the recovery of an advance against progress invoices."""
    row = db.query(DownPayment).filter(DownPayment.id == dp_id).first()
    if row is None:
        raise HTTPException(404, f"Down payment {dp_id} not found")
    if status is not None:
        row.status = status
    if received_date is not None:
        row.received_date = _date(received_date)
    if recovered_amount is not None:
        row.recovered_amount = recovered_amount
        if recovered_amount >= (row.amount or 0):
            row.status = "recovered"
            row.fully_recovered_date = date.today()
    if guarantee_id is not None:
        row.guarantee_id = guarantee_id
    db.commit()
    return {"id": row.id, "status": row.status, "outstanding": round(row.outstanding, 2)}


# ======================================================================= tax ==

tx = APIRouter(prefix="/tax", tags=["tax"])


@tx.get("/obligations")
def tx_list(
    status: str | None = None,
    tax_type: str | None = None,
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    return tax_engine.obligations(db, status, tax_type, as_of)


@tx.get("/schedule")
def tx_schedule(
    horizon_days: int = Query(90, ge=1, le=730),
    as_of: date | None = None,
    db: Session = Depends(get_db),
):
    """Dated tax outflows — what the cash forecast consumes."""
    return tax_engine.due_schedule(db, horizon_days, as_of)


@tx.get("/calendar")
def tx_calendar(db: Session = Depends(get_db)):
    """The Egyptian filing cadence per tax type."""
    return tax_engine.filing_calendar(db)


@tx.post("/obligations")
def tx_create(
    tax_type: str = Body(..., description="corporate_income|vat|wht|payroll|social_insurance|stamp|property|other"),
    amount_due: float = Body(..., gt=0),
    period: str = Body(""),
    description: str = Body(""),
    currency: str = Body("EGP"),
    base_amount: float = Body(0.0),
    rate: float = Body(0.0),
    due_date: str | None = Body(None),
    authority: str = Body("ETA"),
    reference: str = Body(""),
    notes: str = Body(""),
    db: Session = Depends(get_db),
):
    row = TaxObligation(
        tax_type=tax_type, amount_due=amount_due, period=period,
        description=description, currency=currency, base_amount=base_amount,
        rate=rate, due_date=_date(due_date), authority=authority,
        reference=reference, notes=notes,
        status="open" if due_date else "unscheduled",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "tax_type": row.tax_type, "status": row.status}


@tx.put("/obligations/{ob_id}")
def tx_update(
    ob_id: int,
    due_date: str | None = Body(None),
    paid_amount: float | None = Body(None),
    paid_date: str | None = Body(None),
    penalty: float | None = Body(None),
    status: str | None = Body(None),
    db: Session = Depends(get_db),
):
    """Set a due date — which is what brings an unscheduled due into the forecast."""
    row = db.query(TaxObligation).filter(TaxObligation.id == ob_id).first()
    if row is None:
        raise HTTPException(404, f"Obligation {ob_id} not found")
    if due_date is not None:
        row.due_date = _date(due_date)
        if row.status == "unscheduled" and row.due_date:
            row.status = "open"
    if paid_amount is not None:
        row.paid_amount = paid_amount
        if row.outstanding <= 0:
            row.status = "paid"
    if paid_date is not None:
        row.paid_date = _date(paid_date)
    if penalty is not None:
        row.penalty = penalty
    if status is not None:
        row.status = status
    db.commit()
    return {"id": row.id, "status": row.status, "outstanding": round(row.outstanding, 2)}


@tx.post("/ingest-governmental-dues")
def tx_ingest(replace: bool = Query(True), db: Session = Depends(get_db)):
    """Load the Business Plan's Governmental Dues balances as obligations."""
    return tax_engine.ingest_governmental_dues(db, replace=replace)


# =============================================================== procurement ==

pc = APIRouter(prefix="/procurement", tags=["procurement"])


@pc.get("/summary")
def pc_summary(as_of: date | None = None, db: Session = Depends(get_db)):
    return procurement.summary(db, as_of)


@pc.get("/requisitions")
def pc_prs(status: str | None = None, project_id: int | None = None,
           db: Session = Depends(get_db)):
    return procurement.requisitions(db, status, project_id)


@pc.get("/orders")
def pc_pos(status: str | None = None, supplier: str | None = None,
           project_id: int | None = None, as_of: date | None = None,
           db: Session = Depends(get_db)):
    return procurement.orders(db, status, supplier, project_id, as_of)


@pc.get("/payment-schedule")
def pc_schedule(horizon_days: int = Query(90, ge=1, le=730),
                as_of: date | None = None, db: Session = Depends(get_db)):
    """When open POs turn into cash out."""
    return procurement.payment_schedule(db, horizon_days, as_of)


@pc.post("/requisitions")
def pc_create_pr(
    pr_number: str = Body(...),
    description: str = Body(""),
    estimated_value: float = Body(0.0, ge=0),
    currency: str = Body("EGP"),
    project_name: str = Body(""),
    category: str = Body(""),
    requested_by: str = Body(""),
    department: str = Body(""),
    required_by: str | None = Body(None),
    db: Session = Depends(get_db),
):
    if db.query(PurchaseRequisition).filter(PurchaseRequisition.pr_number == pr_number).first():
        raise HTTPException(409, f"PR {pr_number} already exists")
    row = PurchaseRequisition(
        pr_number=pr_number, description=description, estimated_value=estimated_value,
        currency=currency, project_name=project_name, category=category,
        requested_by=requested_by, department=department,
        required_by=_date(required_by), raised_date=date.today(), status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "pr_number": row.pr_number, "status": row.status}


@pc.put("/requisitions/{pr_id}")
def pc_update_pr(
    pr_id: int,
    status: str | None = Body(None, description="approved | rejected | cancelled"),
    approved_by: str | None = Body(None),
    db: Session = Depends(get_db),
):
    row = db.query(PurchaseRequisition).filter(PurchaseRequisition.id == pr_id).first()
    if row is None:
        raise HTTPException(404, f"PR {pr_id} not found")
    if status is not None:
        row.status = status
        if status == "approved":
            row.approved_date = date.today()
    if approved_by is not None:
        row.approved_by = approved_by
    db.commit()
    return {"id": row.id, "pr_number": row.pr_number, "status": row.status}


@pc.post("/orders")
def pc_create_po(
    po_number: str = Body(...),
    supplier: str = Body(...),
    order_value: float = Body(..., gt=0),
    currency: str = Body("EGP"),
    pr_id: int | None = Body(None),
    project_name: str = Body(""),
    category: str = Body(""),
    description: str = Body(""),
    delivery_date: str | None = Body(None),
    payment_terms_days: int = Body(30, ge=0, le=365),
    db: Session = Depends(get_db),
):
    """Place an order. From here it is committed spend the cash forecast counts."""
    if db.query(PurchaseOrder).filter(PurchaseOrder.po_number == po_number).first():
        raise HTTPException(409, f"PO {po_number} already exists")
    row = PurchaseOrder(
        po_number=po_number, supplier=supplier, order_value=order_value,
        currency=currency, pr_id=pr_id, project_name=project_name,
        category=category, description=description,
        order_date=date.today(), delivery_date=_date(delivery_date),
        payment_terms_days=payment_terms_days, status="open",
    )
    db.add(row)
    if pr_id:
        pr = db.query(PurchaseRequisition).filter(PurchaseRequisition.id == pr_id).first()
        if pr:
            pr.status = "converted"
    db.commit()
    db.refresh(row)
    return {"id": row.id, "po_number": row.po_number, "status": row.status}


@pc.put("/orders/{po_id}")
def pc_update_po(
    po_id: int,
    received_value: float | None = Body(None),
    invoiced_value: float | None = Body(None),
    paid_value: float | None = Body(None),
    status: str | None = Body(None),
    delivery_date: str | None = Body(None),
    db: Session = Depends(get_db),
):
    """Record receipt, invoicing and payment against an order."""
    row = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if row is None:
        raise HTTPException(404, f"PO {po_id} not found")
    for field, value in (
        ("received_value", received_value), ("invoiced_value", invoiced_value),
        ("paid_value", paid_value),
    ):
        if value is not None:
            setattr(row, field, value)
    if delivery_date is not None:
        row.delivery_date = _date(delivery_date)
    if status is not None:
        row.status = status
    elif row.paid_value >= row.order_value > 0:
        row.status = "closed"
    elif row.received_value >= row.order_value > 0:
        row.status = "received"
    elif row.received_value > 0:
        row.status = "partial"
    db.commit()
    return {
        "id": row.id, "po_number": row.po_number, "status": row.status,
        "uninvoiced": round(row.uninvoiced, 2), "unpaid": round(row.unpaid, 2),
    }


# ======================================================= contribution margin ==

mg = APIRouter(prefix="/margin", tags=["contribution margin"])


@mg.get("/company")
def mg_company(period: str | None = None, db: Session = Depends(get_db)):
    """Contribution margin from the P&L, with the variable/fixed split stated."""
    return margin.company_level(db, period)


@mg.get("/projects")
def mg_projects(period: str | None = None, scenario: str = Query("actual"),
                db: Session = Depends(get_db)):
    """Contribution margin per project, from categorised cost lines."""
    return margin.by_project(db, period, scenario)


@mg.get("/categories")
def mg_categories(db: Session = Depends(get_db)):
    """The variable / fixed classification — a management judgement, so editable."""
    return margin.categories(db)


@mg.put("/categories")
def mg_set_category(
    name: str = Body(...),
    behaviour: str = Body(..., description="variable | fixed | semi"),
    variable_portion: float | None = Body(None, ge=0, le=1),
    note: str = Body(""),
    db: Session = Depends(get_db),
):
    if behaviour not in {"variable", "fixed", "semi"}:
        raise HTTPException(400, "behaviour must be variable, fixed or semi")
    return margin.set_category(db, name, behaviour, variable_portion, note)


@mg.get("/committed")
def mg_committed(db: Session = Depends(get_db)):
    """Committed direct spend per project, from open purchase orders."""
    return margin.committed_costs_from_pos(db)


@mg.post("/costs")
def mg_add_cost(
    project_id: int = Body(...),
    period: str = Body(...),
    category: str = Body(...),
    amount: float = Body(...),
    currency: str = Body("EGP"),
    scenario: str = Body("actual"),
    source: str = Body("manual"),
    source_ref: str = Body(""),
    db: Session = Depends(get_db),
):
    """Post a categorised project cost — this is what makes margin computable."""
    fx = FXConverter(db, date.today())
    row = ProjectCost(
        project_id=project_id, period=period, category=category, amount=amount,
        amount_egp=fx.try_to_egp(amount, currency) or 0.0, currency=currency,
        scenario=scenario, source=source, source_ref=source_ref,
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "project_id": project_id, "category": category}


# =================================================================== funding ==

fd = APIRouter(prefix="/funding", tags=["funding"])


@fd.get("/position")
def fd_position(horizon_days: int = Query(90, ge=1, le=730),
                as_of: date | None = None, db: Session = Depends(get_db)):
    """Everything that is or becomes money, as a ladder from cash outward."""
    return funding.position(db, horizon_days, as_of)


# ==================================================================== import ==

im = APIRouter(prefix="/commitments", tags=["commitments"])

CSV_TEMPLATES = {
    "trade-finance": [
        "reference", "instrument", "direction", "counterparty_type", "counterparty",
        "project_name", "purpose", "bank_name", "currency", "face_value",
        "margin_pct", "margin_amount", "margin_in_bank_balance", "commission_rate_pa",
        "issue_date", "expiry_date", "lc_type", "usance_days", "status",
    ],
    "down-payments": [
        "direction", "counterparty", "counterparty_type", "project_name", "reference",
        "currency", "amount", "pct_of_contract", "expected_date", "received_date",
        "status", "recovery_pct", "recovered_amount",
    ],
    "tax": [
        "tax_type", "description", "period", "authority", "currency",
        "base_amount", "rate", "amount_due", "due_date", "status", "reference",
    ],
    "requisitions": [
        "pr_number", "project_name", "requested_by", "department", "category",
        "description", "currency", "estimated_value", "required_by", "raised_date", "status",
    ],
    "orders": [
        "po_number", "supplier", "project_name", "category", "description",
        "currency", "order_value", "received_value", "invoiced_value", "paid_value",
        "order_date", "delivery_date", "payment_terms_days", "status",
    ],
}

_MODELS = {
    "trade-finance": TradeFinanceFacility,
    "down-payments": DownPayment,
    "tax": TaxObligation,
    "requisitions": PurchaseRequisition,
    "orders": PurchaseOrder,
}
_DATE_FIELDS = {
    "issue_date", "expiry_date", "latest_shipment_date", "released_date",
    "expected_date", "received_date", "fully_recovered_date",
    "due_date", "filed_date", "paid_date",
    "required_by", "raised_date", "approved_date", "order_date", "delivery_date",
}
_BOOL_FIELDS = {"margin_in_bank_balance", "auto_extend"}


@im.get("/template/{register}")
def csv_template(register: str):
    """The column headers for a register's CSV import."""
    if register not in CSV_TEMPLATES:
        raise HTTPException(404, f"Unknown register. Use one of: {', '.join(CSV_TEMPLATES)}")
    return {"register": register, "columns": CSV_TEMPLATES[register],
            "csv_header": ",".join(CSV_TEMPLATES[register])}


@im.post("/import/{register}")
async def csv_import(
    register: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Bulk-load a register from CSV.

    Rows that fail are reported with their line number and reason rather than
    skipped silently — a half-loaded register that looks complete is worse than
    one that refuses.
    """
    if register not in _MODELS:
        raise HTTPException(404, f"Unknown register. Use one of: {', '.join(_MODELS)}")
    model = _MODELS[register]
    columns = {c.name for c in model.__table__.columns}

    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    loaded, errors = 0, []

    fx = FXConverter(db, date.today())
    for i, line in enumerate(reader, start=2):
        payload = {}
        try:
            for k, v in line.items():
                key = (k or "").strip()
                if key not in columns or v is None or str(v).strip() == "":
                    continue
                v = str(v).strip()
                if key in _DATE_FIELDS:
                    payload[key] = date.fromisoformat(v)
                elif key in _BOOL_FIELDS:
                    payload[key] = v.lower() in ("1", "true", "yes", "y")
                else:
                    col = model.__table__.columns[key]
                    py = col.type.python_type
                    payload[key] = py(v) if py in (int, float) else v
            if not payload:
                continue
            row = model(**payload)
            if register == "down-payments" and hasattr(row, "amount"):
                row.amount_egp = fx.try_to_egp(row.amount, row.currency or "EGP") or 0.0
            if register == "trade-finance" and not row.margin_amount:
                row.margin_amount = (row.face_value or 0) * (row.margin_pct or 0)
            db.add(row)
            db.flush()
            loaded += 1
        except Exception as exc:
            db.rollback()
            errors.append({"line": i, "error": f"{type(exc).__name__}: {exc}"})

    db.commit()
    return {
        "register": register,
        "loaded": loaded,
        "failed": len(errors),
        "errors": errors[:25],
        "note": f"{len(errors)} row(s) rejected — fix and re-import those lines."
        if errors else None,
    }


for sub in (tf, dp, tx, pc, mg, fd, im):
    router.include_router(sub)
