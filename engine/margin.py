"""Contribution margin.

    contribution margin = revenue − variable cost
    CM %                = contribution margin / revenue
    break-even revenue  = fixed cost / CM %

Gross profit and contribution margin are not the same thing and the difference
matters for a fabricator: gross profit deducts everything in cost of sales
including the factory overhead absorbed into it, while contribution margin
deducts only what actually moves with the work. A job can show a thin gross
margin and still be worth taking if its contribution covers overhead that is
being paid anyway — which is the decision this screen exists to inform.

The split between variable and fixed is a management judgement, not a fact, so
it lives in an editable table (`cost_categories`) and every figure here reports
which classification produced it.
"""

from collections import defaultdict
from datetime import date
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.models import (
    CostCategory, DEFAULT_COST_CATEGORIES, FinancialLine, Project,
    ProjectCost, ProjectPeriod, PurchaseOrder,
)
from engine.reporting import canonical_line

KEGP_TO_EGP = 1000.0


def seed_categories(db: Session, force: bool = False) -> int:
    if db.query(CostCategory).count() and not force:
        return 0
    if force:
        db.query(CostCategory).delete(synchronize_session=False)
    for name, behaviour, portion, note in DEFAULT_COST_CATEGORIES:
        db.add(CostCategory(name=name, behaviour=behaviour,
                            variable_portion=portion, note=note))
    db.commit()
    return len(DEFAULT_COST_CATEGORIES)


def categories(db: Session) -> list[dict]:
    seed_categories(db)
    return [
        {
            "id": c.id, "name": c.name, "behaviour": c.behaviour,
            "variable_portion": c.variable_portion, "note": c.note,
        }
        for c in db.query(CostCategory).order_by(CostCategory.behaviour, CostCategory.name).all()
    ]


def set_category(db: Session, name: str, behaviour: str,
                 variable_portion: float | None = None, note: str = "") -> dict:
    seed_categories(db)
    row = db.query(CostCategory).filter(CostCategory.name == name).first()
    if row is None:
        row = CostCategory(name=name)
        db.add(row)
    row.behaviour = behaviour
    if variable_portion is None:
        variable_portion = 1.0 if behaviour == "variable" else 0.0 if behaviour == "fixed" else 0.5
    row.variable_portion = variable_portion
    row.note = note
    db.commit()
    return {"name": name, "behaviour": behaviour, "variable_portion": variable_portion}


def _variable_share(db: Session) -> dict[str, float]:
    """category name -> the fraction of it that is variable."""
    seed_categories(db)
    out = {}
    for c in db.query(CostCategory).all():
        if c.behaviour == "variable":
            out[c.name.lower()] = 1.0
        elif c.behaviour == "fixed":
            out[c.name.lower()] = 0.0
        else:
            out[c.name.lower()] = c.variable_portion if c.variable_portion is not None else 0.5
    return out


def _split(amount: float, category: str, shares: dict[str, float]) -> tuple[float, float, bool]:
    """-> (variable, fixed, classified). Unknown categories are reported, not guessed."""
    key = (category or "").strip().lower()
    if key in shares:
        v = shares[key]
        return amount * v, amount * (1 - v), True
    for name, v in shares.items():
        if name and (name in key or key in name):
            return amount * v, amount * (1 - v), True
    return 0.0, 0.0, False


def by_project(db: Session, period: str | None = None,
               scenario: str = "actual") -> dict:
    """Contribution margin per project.

    Revenue comes from ProjectPeriod; costs from ProjectCost where they exist,
    falling back to the ProjectPeriod COGS line. A project whose costs are not
    categorised cannot have a contribution margin computed — it is listed as
    unclassified rather than given a margin that assumes everything is variable.
    """
    shares = _variable_share(db)

    rev_q = db.query(ProjectPeriod).filter(ProjectPeriod.scenario == scenario)
    if period:
        rev_q = rev_q.filter(ProjectPeriod.period == period)
    revenue: dict[int, float] = defaultdict(float)
    cogs_fallback: dict[int, float] = defaultdict(float)
    for p in rev_q.all():
        revenue[p.project_id] += p.revenue or 0.0
        cogs_fallback[p.project_id] += p.cogs or 0.0

    cost_q = db.query(ProjectCost).filter(ProjectCost.scenario == scenario)
    if period:
        cost_q = cost_q.filter(ProjectCost.period == period)
    var: dict[int, float] = defaultdict(float)
    fix: dict[int, float] = defaultdict(float)
    unclassified: dict[int, float] = defaultdict(float)
    has_costs: set[int] = set()
    unknown_categories: set[str] = set()

    for c in cost_q.all():
        amount = c.amount_egp or c.amount or 0.0
        v, f, ok = _split(amount, c.category, shares)
        has_costs.add(c.project_id)
        if ok:
            var[c.project_id] += v
            fix[c.project_id] += f
        else:
            unclassified[c.project_id] += amount
            unknown_categories.add(c.category)

    names = {p.id: p for p in db.query(Project).all()}
    rows = []
    for pid in sorted(set(revenue) | set(has_costs)):
        proj = names.get(pid)
        rev = revenue.get(pid, 0.0)
        if pid in has_costs:
            v, f, u = var[pid], fix[pid], unclassified[pid]
            basis = "categorised project costs"
        else:
            # No categorised costs — COGS is not splittable, so no margin.
            v = f = 0.0
            u = abs(cogs_fallback.get(pid, 0.0))
            basis = "COGS only — not categorised"
        computable = u == 0 and (v or f)
        cm = rev - v if computable else None
        rows.append(
            {
                "project_id": pid,
                "project": proj.name if proj else f"#{pid}",
                "segment": proj.segment if proj else "",
                "currency": proj.currency if proj else "EGP",
                "revenue": round(rev, 2),
                "variable_cost": round(v, 2),
                "fixed_cost": round(f, 2),
                "unclassified_cost": round(u, 2),
                "contribution_margin": round(cm, 2) if cm is not None else None,
                "cm_pct": round(cm / rev, 4) if cm is not None and rev else None,
                "gross_profit": round(rev - v - f, 2) if computable else None,
                "computable": bool(computable),
                "basis": basis,
            }
        )

    good = [r for r in rows if r["computable"]]
    tot_rev = sum(r["revenue"] for r in good)
    tot_var = sum(r["variable_cost"] for r in good)
    tot_fix = sum(r["fixed_cost"] for r in good)
    tot_cm = tot_rev - tot_var

    return {
        "period": period or "all",
        "scenario": scenario,
        "unit": "EGP",
        "projects": sorted(rows, key=lambda r: -(r["revenue"] or 0)),
        "totals": {
            "revenue": round(tot_rev, 2),
            "variable_cost": round(tot_var, 2),
            "fixed_cost": round(tot_fix, 2),
            "contribution_margin": round(tot_cm, 2),
            "cm_pct": round(tot_cm / tot_rev, 4) if tot_rev else None,
            "operating_profit": round(tot_cm - tot_fix, 2),
            "break_even_revenue": round(tot_fix / (tot_cm / tot_rev), 2)
            if tot_rev and tot_cm > 0 else None,
            "margin_of_safety": round((tot_rev - tot_fix / (tot_cm / tot_rev)) / tot_rev, 4)
            if tot_rev and tot_cm > 0 else None,
        },
        "computable_projects": len(good),
        "unclassified_categories": sorted(c for c in unknown_categories if c),
        "note": (
            "No categorised project costs are loaded, so contribution margin "
            "cannot be computed. It needs cost lines tagged by category — post "
            "them to /margin/costs, or let approved POs supply the direct spend."
            if not good else None
        ),
        "empty": not rows,
    }


def company_level(db: Session, period: str | None = None) -> dict:
    """Contribution margin from the P&L, when project costs aren't loaded.

    A coarse split: cost of operations is treated as variable and G&A, S&M and
    depreciation as fixed. That is the standard first approximation for a
    fabricator, and it is stated on the output rather than presented as if the
    costs had actually been analysed.
    """
    q = (
        db.query(FinancialLine)
        .filter(
            FinancialLine.statement == "IS",
            FinancialLine.period_type == "annual",
            FinancialLine.scope == "standalone",
        )
    )
    if period:
        q = q.filter(FinancialLine.period == period)
    # The signed statements and the plan name the same lines differently, so a
    # period with reported actuals would otherwise be read as still on plan.
    data: dict[tuple[str, str], dict] = defaultdict(dict)
    for r in q.all():
        data[(canonical_line(r.line_item), r.period)][r.scenario] = r.value

    periods = sorted({p for _, p in data}, key=lambda x: int(x) if x.isdigit() else 0)

    def val(item, p):
        cell = data.get((item, p), {})
        return cell.get("actual", cell.get("budget"))

    rows = []
    for p in periods:
        rev = val("Total Revenues", p)
        if not rev:
            continue
        variable = abs(val("Cost Of Operations", p) or 0.0)
        fixed = sum(
            abs(val(k, p) or 0.0)
            for k in ("G&A", "S&M", "Depreciation & Amortization")
        )
        cm = rev - variable
        scen = "actual" if "actual" in data.get(("Total Revenues", p), {}) else "budget"
        rows.append(
            {
                "period": p,
                "scenario": scen,
                "revenue_kegp": round(rev, 1),
                "variable_cost_kegp": round(variable, 1),
                "fixed_cost_kegp": round(fixed, 1),
                "contribution_margin_kegp": round(cm, 1),
                "cm_pct": round(cm / rev, 4) if rev else None,
                "operating_profit_kegp": round(cm - fixed, 1),
                "break_even_revenue_kegp": round(fixed / (cm / rev), 1)
                if rev and cm > 0 else None,
                "margin_of_safety": round((rev - fixed / (cm / rev)) / rev, 4)
                if rev and cm > 0 else None,
            }
        )

    return {
        "unit": "KEGP",
        "periods": rows,
        "basis": (
            "Cost of operations treated as variable; G&A, S&M and depreciation as "
            "fixed. A first approximation from the P&L — not a cost analysis. Load "
            "categorised project costs for a margin you can defend to a board."
        ),
        "empty": not rows,
    }


def committed_costs_from_pos(db: Session, period: str | None = None) -> dict:
    """Turn open POs into committed project costs so a live job shows a margin
    before its supplier invoices arrive."""
    shares = _variable_share(db)
    rows = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.status.in_(("open", "partial", "received")))
        .all()
    )
    by_project = defaultdict(lambda: {"variable": 0.0, "fixed": 0.0, "unclassified": 0.0})
    for o in rows:
        if not o.project_id:
            continue
        v, f, ok = _split(o.order_value or 0.0, o.category, shares)
        if ok:
            by_project[o.project_id]["variable"] += v
            by_project[o.project_id]["fixed"] += f
        else:
            by_project[o.project_id]["unclassified"] += o.order_value or 0.0

    names = {p.id: p.name for p in db.query(Project).all()}
    return {
        "projects": [
            {"project_id": k, "project": names.get(k, f"#{k}"),
             **{kk: round(vv, 2) for kk, vv in v.items()}}
            for k, v in sorted(by_project.items(), key=lambda kv: -kv[1]["variable"])
        ],
        "note": "Committed direct spend from open purchase orders, split by the "
                "cost-category classification.",
        "empty": not by_project,
    }
