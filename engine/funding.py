"""The consolidated funding position — everything that is or becomes money.

The cash dashboard answers "what can I spend today". This answers the wider
question: across cash, receivables, advances and commitments, what is the
company's actual funding position over a horizon.

Structured as a ladder, because the distinction between the rungs is the whole
point — cash in the bank and an invoice a government client will pay in 200 days
are both "money", and treating them as the same number is how a company with a
healthy balance sheet misses a payroll.

    available now      bank balance, less issued cheques, less restricted margins
    contracted in      AR at confidence, advances due, export L/C proceeds
    committed out      cheques, POs, tax, payroll
    = funding position over the horizon

Every rung reports what it could not include, so a thin position is never the
result of a silently dropped input.
"""

from datetime import date, timedelta
from sqlalchemy.orm import Session

from engine import cash as cash_engine
from engine import procurement, tax as tax_engine, trade_finance
from engine import receivables as ar_engine


def position(db: Session, horizon_days: int = 90, as_of: date | None = None) -> dict:
    gaps: list[str] = []

    def attempt(fn, default, label):
        try:
            return fn()
        except Exception as exc:
            gaps.append(f"{label}: {type(exc).__name__}: {exc}")
            return default

    avail = attempt(lambda: cash_engine.available_cash(db, as_of), {}, "cash position")
    as_of_d = date.fromisoformat(avail["as_of"]) if avail.get("as_of") else (as_of or date.today())
    horizon_end = as_of_d + timedelta(days=horizon_days)

    restricted = attempt(lambda: trade_finance.restricted_cash(db, as_of_d), {}, "restricted cash")
    dp = attempt(lambda: trade_finance.down_payments(db, as_of=as_of_d), {}, "down payments")
    tf = attempt(lambda: trade_finance.summary(db, as_of_d), {}, "trade finance")
    po = attempt(lambda: procurement.orders(db, as_of=as_of_d), {}, "purchase orders")
    tx = attempt(lambda: tax_engine.obligations(db, as_of=as_of_d), {}, "tax obligations")
    coll = attempt(
        lambda: ar_engine.collection_forecast(db, horizon_days=horizon_days),
        {}, "collection forecast",
    )

    bank = avail.get("bank_balance_egp", 0.0)
    cheques = avail.get("less_delivered_checks_egp", 0.0) + avail.get("less_on_hand_checks_egp", 0.0)
    margin_deducted = restricted.get("deducted_from_cash_egp", 0.0)
    spendable = bank - cheques - margin_deducted

    # --- contracted inflows over the horizon -----------------------------
    collections = 0.0
    for d in coll.get("by_date", []):
        if d["date"] <= horizon_end.isoformat():
            collections += d["weighted_egp"]

    advances_due = 0.0
    for d in dp.get("down_payments", []):
        if (d["direction"] == "received" and d["status"] == "expected"
                and d["expected_date"] and d["expected_date"] <= horizon_end.isoformat()):
            advances_due += d["amount_egp"]

    export_lc = tf.get("lc", {}).get("export_face_egp", 0.0)

    # --- committed outflows over the horizon -----------------------------
    po_out = sum(
        s["amount_egp"] for s in attempt(
            lambda: procurement.payment_schedule(db, horizon_days, as_of_d), [], "PO schedule")
    )
    tax_out = sum(
        s["amount_egp"] for s in attempt(
            lambda: tax_engine.due_schedule(db, horizon_days, as_of_d), [], "tax schedule")
    )
    commission_out = sum(
        c["amount_egp"] for c in attempt(
            lambda: trade_finance.commission_schedule(db, horizon_days, as_of_d), [], "commissions")
    )

    inflow = collections + advances_due
    outflow = po_out + tax_out + commission_out
    projected = spendable + inflow - outflow

    # --- what is deliberately not counted --------------------------------
    excluded = []
    unscheduled_tax = tx.get("unscheduled_egp", 0.0)
    if unscheduled_tax:
        excluded.append({
            "item": "Tax and governmental dues with no settlement date",
            "amount_egp": round(unscheduled_tax, 2),
            "effect": "outflow not counted — the position is optimistic by this much",
        })
    committed_po = po.get("committed_uninvoiced_egp", 0.0)
    beyond = committed_po - po_out
    if beyond > 0:
        excluded.append({
            "item": "PO commitments falling due beyond the horizon",
            "amount_egp": round(beyond, 2),
            "effect": "outflow outside this window",
        })
    if export_lc:
        excluded.append({
            "item": "Export L/C face value",
            "amount_egp": round(export_lc, 2),
            "effect": "shown separately — proceeds depend on shipment and document "
                      "presentation, so it is not counted as a dated inflow",
        })
    off_bs_margin = restricted.get("held_outside_reported_balances_egp", 0.0)
    if off_bs_margin:
        excluded.append({
            "item": "Margins not flagged as inside a reported bank balance",
            "amount_egp": round(off_bs_margin, 2),
            "effect": "not deducted — flag them if those accounts are in the daily workbook",
        })
    ar_unforecast = 0.0
    if coll.get("items"):
        total_ar = sum(i["amount_egp"] for i in coll["items"])
        weighted = coll.get("total_expected_egp", 0.0)
        ar_unforecast = total_ar - weighted
    if ar_unforecast > 0:
        excluded.append({
            "item": "Receivables discounted for collection risk / beyond horizon",
            "amount_egp": round(ar_unforecast, 2),
            "effect": "gross AR not expected as cash in this window",
        })

    return {
        "as_of": as_of_d.isoformat(),
        "horizon_days": horizon_days,
        "horizon_end": horizon_end.isoformat(),
        "ladder": [
            {"rung": "Bank balance", "kind": "in", "amount_egp": round(bank, 2),
             "note": f"{avail.get('counts', {}).get('delivered', 0) + avail.get('counts', {}).get('on_hand', 0)} cheques already issued against it"},
            {"rung": "Less issued cheques", "kind": "out", "amount_egp": round(cheques, 2),
             "note": "delivered and on-hand, uncleared"},
            {"rung": "Less restricted margins", "kind": "out", "amount_egp": round(margin_deducted, 2),
             "note": "cash collateral against issued L/Gs and L/Cs"},
            {"rung": "Spendable today", "kind": "subtotal", "amount_egp": round(spendable, 2),
             "note": "net available cash"},
            {"rung": "Expected collections", "kind": "in", "amount_egp": round(collections, 2),
             "note": f"probability-weighted, to {horizon_end.isoformat()}"},
            {"rung": "Advances due from clients", "kind": "in", "amount_egp": round(advances_due, 2),
             "note": "down payments expected but not yet received"},
            {"rung": "Purchase order payments", "kind": "out", "amount_egp": round(po_out, 2),
             "note": "open POs falling due on their terms"},
            {"rung": "Tax and governmental dues", "kind": "out", "amount_egp": round(tax_out, 2),
             "note": "dated obligations only"},
            {"rung": "L/G and L/C commission", "kind": "out", "amount_egp": round(commission_out, 2),
             "note": "periodic charges on issued facilities"},
            {"rung": f"Funding position at {horizon_days}d", "kind": "total",
             "amount_egp": round(projected, 2), "note": "spendable plus contracted in, less committed out"},
        ],
        "spendable_today_egp": round(spendable, 2),
        "contracted_in_egp": round(inflow, 2),
        "committed_out_egp": round(outflow, 2),
        "projected_egp": round(projected, 2),
        "tight": projected < 0,
        "excluded": excluded,
        "gaps": gaps,
        "note": (
            "Rungs are not interchangeable. Cash at bank and a government "
            "receivable are both money, but only one of them pays a supplier "
            "this week."
        ),
    }
