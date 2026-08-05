"""IFRS 9 Expected Credit Loss on trade receivables.

Uses the **simplified approach** (IFRS 9.5.5.15), which is the approach
permitted — and for trade receivables without a significant financing
component, required — for this asset class: lifetime ECL is recognised from
initial recognition, with no 12-month/lifetime stage assessment.

Measurement is a provision matrix (IFRS 9.B5.5.35):

    ECL = gross carrying amount  x  bucket loss rate  x  forward-looking overlay

The overlay is the forward-looking adjustment IFRS 9.5.5.17(c) requires: rates
derived from historical loss experience must be adjusted for current conditions
and reasonable, supportable forecasts. Here it is driven off the Business Plan's
own macro assumptions, so the audit trail points at a board-approved input
rather than an unexplained management percentage.
"""

from collections import defaultdict
from datetime import date
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.models import (
    ECLPolicy, ECLResult, ECLRun, MacroAssumption, Receivable,
)
from engine.receivables import bucket_for, latest_ar_snapshot

# Starting provision matrix. These are defaults to be calibrated against
# ARESCO's own write-off history — the policy table is editable for that reason.
DEFAULT_MATRIX = [
    ("default", "current", 0.005),
    ("default", "1m", 0.02),
    ("default", "2m", 0.05),
    ("default", "3m+", 0.20),
    ("default", "dormant", 0.75),
    ("default", "unaged", 0.05),
    # Government and related-party counterparties pay late but rarely default,
    # so the aging curve overstates their credit risk.
    ("Projects", "current", 0.003),
    ("Projects", "1m", 0.01),
    ("Projects", "2m", 0.03),
    ("Projects", "3m+", 0.12),
    ("Projects", "dormant", 0.75),
    ("Projects", "unaged", 0.03),
]


def seed_policy(db: Session, force: bool = False) -> int:
    if db.query(ECLPolicy).count() and not force:
        return 0
    if force:
        db.query(ECLPolicy).delete(synchronize_session=False)
    for segment, bucket, rate in DEFAULT_MATRIX:
        db.add(
            ECLPolicy(
                segment=segment, bucket=bucket, loss_rate=rate,
                effective_from=date.today(),
                note="Seeded default — calibrate against actual write-off history.",
            )
        )
    db.commit()
    return len(DEFAULT_MATRIX)


def get_policy(db: Session) -> dict[tuple[str, str], float]:
    seed_policy(db)
    return {(p.segment, p.bucket): p.loss_rate for p in db.query(ECLPolicy).all()}


def set_policy(db: Session, segment: str, bucket: str, loss_rate: float,
               note: str = "") -> dict:
    row = (
        db.query(ECLPolicy)
        .filter(ECLPolicy.segment == segment, ECLPolicy.bucket == bucket)
        .first()
    )
    if row is None:
        row = ECLPolicy(segment=segment, bucket=bucket)
        db.add(row)
    row.loss_rate = loss_rate
    row.note = note
    row.effective_from = date.today()
    db.commit()
    return {"segment": segment, "bucket": bucket, "loss_rate": loss_rate}


def _loss_rate(policy: dict, segment: str, bucket: str) -> float:
    bucket = bucket or "unaged"
    if (segment, bucket) in policy:
        return policy[(segment, bucket)]
    return policy.get(("default", bucket), policy.get(("default", "unaged"), 0.05))


def macro_overlay(db: Session, as_of: date) -> tuple[float, str]:
    """Forward-looking adjustment factor from the Business Plan macro set.

    A depreciating EGP raises the local-currency cost of imported inputs for
    ARESCO's customers and historically tracks higher default rates, so the
    projected devaluation over the coming year scales the base loss rates. The
    factor is deliberately bounded so a single bad assumption cannot swing the
    provision without someone noticing.
    """
    rows = {
        m.period: m.value
        for m in db.query(MacroAssumption)
        .filter(MacroAssumption.name.ilike("USD%EGP%"))
        .all()
        if len(m.period) == 4
    }
    this_year, next_year = str(as_of.year), str(as_of.year + 1)
    if this_year not in rows or next_year not in rows or not rows[this_year]:
        return 1.0, "No USD/EGP forecast available — overlay defaults to 1.00."

    devaluation = (rows[next_year] - rows[this_year]) / rows[this_year]
    factor = round(min(1.50, max(0.80, 1.0 + devaluation)), 4)
    return factor, (
        f"USD/EGP forecast {rows[this_year]:.2f} ({this_year}) -> "
        f"{rows[next_year]:.2f} ({next_year}) = {devaluation:.1%} devaluation; "
        f"overlay factor {factor} (bounded 0.80-1.50)."
    )


def run_ecl(db: Session, as_of: date | None = None, overlay: float | None = None,
            user: str = "", persist: bool = True) -> dict:
    """Compute lifetime ECL over the open receivables at `as_of`."""
    as_of = as_of or latest_ar_snapshot(db)
    if as_of is None:
        raise ValueError("No receivables ingested — nothing to measure.")

    policy = get_policy(db)
    if overlay is None:
        overlay, overlay_basis = macro_overlay(db, as_of)
    else:
        overlay_basis = f"Management overlay of {overlay} applied manually."

    rows = db.query(Receivable).filter(Receivable.snapshot_date == as_of).all()

    results, by_bucket, by_currency = [], defaultdict(lambda: {
        "gross_egp": 0.0, "ecl_egp": 0.0, "count": 0}), defaultdict(lambda: {
        "gross": 0.0, "ecl_egp": 0.0})
    total_gross = total_ecl = 0.0

    for r in rows:
        bucket = r.aging_bucket or bucket_for(r, as_of) or "unaged"
        segment = r.category or "default"
        base_rate = _loss_rate(policy, segment, bucket)
        rate = min(1.0, base_rate * overlay)
        ecl = r.amount_egp * rate

        results.append(
            ECLResult(
                customer_name=r.customer_name, segment=segment, bucket=bucket,
                currency=r.currency, gross_amount=r.amount,
                gross_amount_egp=r.amount_egp, loss_rate=rate, ecl_egp=ecl,
            )
        )
        b = by_bucket[bucket]
        b["gross_egp"] += r.amount_egp
        b["ecl_egp"] += ecl
        b["count"] += 1
        c = by_currency[r.currency]
        c["gross"] += r.amount
        c["ecl_egp"] += ecl
        total_gross += r.amount_egp
        total_ecl += ecl

    run = ECLRun(
        run_date=date.today(), as_of=as_of, macro_overlay=overlay,
        overlay_basis=overlay_basis, total_gross_egp=total_gross,
        total_ecl_egp=total_ecl, created_by=user,
    )
    if persist:
        db.add(run)
        db.flush()
        for res in results:
            res.run_id = run.id
            db.add(res)
        db.commit()

    return {
        "run_id": run.id if persist else None,
        "as_of": as_of.isoformat(),
        "basis": "IFRS 9 simplified approach — lifetime ECL, provision matrix",
        "macro_overlay": overlay,
        "overlay_basis": overlay_basis,
        "total_gross_egp": round(total_gross, 2),
        "total_ecl_egp": round(total_ecl, 2),
        "coverage_ratio": round(total_ecl / total_gross, 4) if total_gross else 0.0,
        "by_bucket": [
            {
                "bucket": k,
                "count": v["count"],
                "gross_egp": round(v["gross_egp"], 2),
                "ecl_egp": round(v["ecl_egp"], 2),
                "effective_rate": round(v["ecl_egp"] / v["gross_egp"], 4)
                if v["gross_egp"] else 0.0,
            }
            for k, v in sorted(by_bucket.items())
        ],
        "by_currency": [
            {"currency": k, "gross": round(v["gross"], 2),
             "ecl_egp": round(v["ecl_egp"], 2)}
            for k, v in sorted(by_currency.items())
        ],
        "by_customer": sorted(
            (
                {
                    "customer": res.customer_name,
                    "segment": res.segment,
                    "bucket": res.bucket,
                    "currency": res.currency,
                    "gross": round(res.gross_amount, 2),
                    "gross_egp": round(res.gross_amount_egp, 2),
                    "loss_rate": round(res.loss_rate, 4),
                    "ecl_egp": round(res.ecl_egp, 2),
                }
                for res in results
            ),
            key=lambda x: -x["ecl_egp"],
        ),
    }


def run_history(db: Session, limit: int = 24) -> list[dict]:
    runs = db.query(ECLRun).order_by(ECLRun.as_of.desc()).limit(limit).all()
    return [
        {
            "run_id": r.id,
            "as_of": r.as_of.isoformat(),
            "run_date": r.run_date.isoformat(),
            "macro_overlay": r.macro_overlay,
            "total_gross_egp": round(r.total_gross_egp, 2),
            "total_ecl_egp": round(r.total_ecl_egp, 2),
            "coverage_ratio": round(r.total_ecl_egp / r.total_gross_egp, 4)
            if r.total_gross_egp else 0.0,
            "created_by": r.created_by,
        }
        for r in runs
    ]


def movement(db: Session) -> dict:
    """Period-on-period ECL movement — the disclosure note auditors ask for."""
    runs = db.query(ECLRun).order_by(ECLRun.as_of.asc()).all()
    if len(runs) < 2:
        return {
            "periods": [r.as_of.isoformat() for r in runs],
            "note": "At least two ECL runs are needed to show a movement.",
        }
    moves = []
    for prev, curr in zip(runs, runs[1:]):
        moves.append(
            {
                "from": prev.as_of.isoformat(),
                "to": curr.as_of.isoformat(),
                "opening_ecl_egp": round(prev.total_ecl_egp, 2),
                "closing_ecl_egp": round(curr.total_ecl_egp, 2),
                "charge_egp": round(curr.total_ecl_egp - prev.total_ecl_egp, 2),
                "gross_change_egp": round(curr.total_gross_egp - prev.total_gross_egp, 2),
            }
        )
    return {"movements": moves}
