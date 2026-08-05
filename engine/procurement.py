"""Purchase requisitions and purchase orders.

The reason procurement sits in a treasury tool: an approved PO is money already
spent as far as the business is concerned, but it reaches the payables ledger
only when the supplier invoices. Between those two moments the commitment is
invisible to a cash forecast built from payables — and for a fabricator buying
steel months ahead, that gap is most of the working capital.

Two numbers matter here:
  committed        ordered but not yet invoiced — the forecast's blind spot
  payable pipeline ordered and invoiced but not yet paid — due on PO terms
"""

from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.models import PurchaseOrder, PurchaseRequisition
from engine.fx import FXConverter

OPEN_PO_STATUSES = ("open", "partial", "received")


def requisitions(db: Session, status: str | None = None,
                 project_id: int | None = None) -> dict:
    q = db.query(PurchaseRequisition)
    if status:
        q = q.filter(PurchaseRequisition.status == status)
    if project_id:
        q = q.filter(PurchaseRequisition.project_id == project_id)

    fx = FXConverter(db, date.today())
    rows, by_status = [], defaultdict(lambda: {"count": 0, "value_egp": 0.0})
    for r in q.order_by(PurchaseRequisition.raised_date.desc()).all():
        egp = fx.try_to_egp(r.estimated_value, r.currency) or 0.0
        converted = sum(1 for o in r.orders)
        rows.append(
            {
                "id": r.id,
                "pr_number": r.pr_number,
                "project": r.project_name,
                "requested_by": r.requested_by,
                "department": r.department,
                "category": r.category,
                "description": r.description,
                "currency": r.currency,
                "estimated_value": r.estimated_value,
                "estimated_value_egp": round(egp, 2),
                "required_by": r.required_by.isoformat() if r.required_by else None,
                "raised_date": r.raised_date.isoformat() if r.raised_date else None,
                "approved_by": r.approved_by,
                "approved_date": r.approved_date.isoformat() if r.approved_date else None,
                "status": r.status,
                "orders": converted,
                "age_days": (date.today() - r.raised_date).days if r.raised_date else None,
            }
        )
        by_status[r.status]["count"] += 1
        by_status[r.status]["value_egp"] += egp

    pending = [r for r in rows if r["status"] in ("draft", "pending")]
    return {
        "requisitions": rows,
        "count": len(rows),
        "by_status": [
            {"status": k, "count": v["count"], "value_egp": round(v["value_egp"], 2)}
            for k, v in sorted(by_status.items())
        ],
        "pending_approval": {
            "count": len(pending),
            "value_egp": round(sum(r["estimated_value_egp"] for r in pending), 2),
            "oldest_days": max((r["age_days"] or 0 for r in pending), default=0),
        },
        "empty": not rows,
    }


def orders(db: Session, status: str | None = None, supplier: str | None = None,
           project_id: int | None = None, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    q = db.query(PurchaseOrder)
    if status:
        q = q.filter(PurchaseOrder.status == status)
    if supplier:
        q = q.filter(PurchaseOrder.supplier.ilike(f"%{supplier}%"))
    if project_id:
        q = q.filter(PurchaseOrder.project_id == project_id)

    fx = FXConverter(db, as_of)
    rows = []
    committed = payable = ordered = 0.0
    by_category = defaultdict(lambda: {"count": 0, "ordered_egp": 0.0, "committed_egp": 0.0})
    by_supplier = defaultdict(lambda: {"count": 0, "ordered_egp": 0.0, "committed_egp": 0.0})

    for o in q.order_by(PurchaseOrder.delivery_date).all():
        rate_ok = fx.find_rate(o.currency, as_of) is not None
        to_egp = (lambda v: fx.try_to_egp(v, o.currency, as_of) or 0.0) if rate_ok else (lambda v: 0.0)
        o_egp, u_egp, p_egp = to_egp(o.order_value), to_egp(o.uninvoiced), to_egp(o.unpaid)
        overdue_delivery = (
            o.delivery_date is not None
            and o.delivery_date < as_of
            and o.status in OPEN_PO_STATUSES
        )
        rows.append(
            {
                "id": o.id,
                "po_number": o.po_number,
                "supplier": o.supplier,
                "project": o.project_name,
                "category": o.category,
                "description": o.description,
                "currency": o.currency,
                "order_value": o.order_value,
                "order_value_egp": round(o_egp, 2),
                "received_value": o.received_value,
                "invoiced_value": o.invoiced_value,
                "paid_value": o.paid_value,
                "uninvoiced": round(o.uninvoiced, 2),
                "uninvoiced_egp": round(u_egp, 2),
                "unpaid_egp": round(p_egp, 2),
                "order_date": o.order_date.isoformat() if o.order_date else None,
                "delivery_date": o.delivery_date.isoformat() if o.delivery_date else None,
                "payment_terms_days": o.payment_terms_days,
                "status": o.status,
                "overdue_delivery": overdue_delivery,
                "rate_missing": not rate_ok,
            }
        )
        if o.status in OPEN_PO_STATUSES:
            ordered += o_egp
            committed += u_egp
            payable += p_egp - u_egp  # invoiced but unpaid
            by_category[o.category or "uncategorised"]["count"] += 1
            by_category[o.category or "uncategorised"]["ordered_egp"] += o_egp
            by_category[o.category or "uncategorised"]["committed_egp"] += u_egp
            by_supplier[o.supplier]["count"] += 1
            by_supplier[o.supplier]["ordered_egp"] += o_egp
            by_supplier[o.supplier]["committed_egp"] += u_egp

    return {
        "as_of": as_of.isoformat(),
        "orders": rows,
        "count": len(rows),
        "open_order_value_egp": round(ordered, 2),
        "committed_uninvoiced_egp": round(committed, 2),
        "invoiced_unpaid_egp": round(max(payable, 0.0), 2),
        "by_category": [
            {"category": k, **{kk: round(vv, 2) if isinstance(vv, float) else vv
                               for kk, vv in v.items()}}
            for k, v in sorted(by_category.items(), key=lambda kv: -kv[1]["ordered_egp"])
        ],
        "by_supplier": [
            {"supplier": k, **{kk: round(vv, 2) if isinstance(vv, float) else vv
                               for kk, vv in v.items()}}
            for k, v in sorted(by_supplier.items(), key=lambda kv: -kv[1]["ordered_egp"])[:20]
        ],
        "overdue_delivery": [r for r in rows if r["overdue_delivery"]],
        "missing_rates": sorted(fx.missing),
        "note": (
            "Committed-uninvoiced is spend the payables ledger cannot see yet. "
            "It is the figure the cash forecast needs and the one a payables-based "
            "forecast misses."
        ),
        "empty": not rows,
    }


def payment_schedule(db: Session, horizon_days: int = 90,
                     as_of: date | None = None) -> list[dict]:
    """When open POs turn into cash out.

    An invoiced line is due on the PO's payment terms from the delivery date; an
    uninvoiced line is assumed to invoice on delivery and pay on terms after
    that. Both are estimates and are labelled as such — a PO is a commitment,
    not an agreed payment date.
    """
    as_of = as_of or date.today()
    end = as_of + timedelta(days=horizon_days)
    fx = FXConverter(db, as_of)

    out = []
    rows = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.status.in_(OPEN_PO_STATUSES))
        .all()
    )
    for o in rows:
        outstanding = o.unpaid
        if outstanding <= 0:
            continue
        egp = fx.try_to_egp(outstanding, o.currency, as_of)
        if not egp:
            continue
        base = o.delivery_date or o.order_date or as_of
        when = base + timedelta(days=o.payment_terms_days or 30)
        if when < as_of:
            when = as_of
        if when > end:
            continue
        out.append(
            {
                "date": when.isoformat(),
                "po_number": o.po_number,
                "supplier": o.supplier,
                "project": o.project_name,
                "category": o.category,
                "amount_egp": round(egp, 2),
                "basis": (
                    f"delivery {base.isoformat()} + {o.payment_terms_days or 30}d terms"
                ),
                "estimated": o.invoiced_value < o.order_value,
            }
        )
    return sorted(out, key=lambda x: x["date"])


def summary(db: Session, as_of: date | None = None) -> dict:
    pr = requisitions(db)
    po = orders(db, as_of=as_of)
    sched = payment_schedule(db, 90, as_of)
    return {
        "as_of": (as_of or date.today()).isoformat(),
        "requisitions": {
            "count": pr["count"],
            "pending_approval": pr["pending_approval"],
        },
        "orders": {
            "count": po["count"],
            "open_value_egp": po["open_order_value_egp"],
            "committed_uninvoiced_egp": po["committed_uninvoiced_egp"],
            "invoiced_unpaid_egp": po["invoiced_unpaid_egp"],
            "overdue_delivery": len(po["overdue_delivery"]),
        },
        "cash_out_90d_egp": round(sum(s["amount_egp"] for s in sched), 2),
        "empty": pr["empty"] and po["empty"],
    }
