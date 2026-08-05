"""P&L, balance sheet, project reporting and actual-vs-budget."""

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import Project, ProjectPeriod
from engine import reporting as rep

router = APIRouter(prefix="/reporting", tags=["reporting"])


@router.get("/summary")
def summary(
    period_type: str = Query("annual", description="monthly | quarterly | annual"),
    scope: str = Query("standalone", description="standalone | consolidated"),
    db: Session = Depends(get_db),
):
    """The summarized BS / IS matrix the CFO reports on."""
    return rep.summary_matrix(db, period_type, scope)


@router.get("/statement")
def statement(
    which: str = Query("IS", description="IS | BS | CF"),
    period_type: str = Query("annual"),
    scope: str = Query("standalone"),
    db: Session = Depends(get_db),
):
    """A full statement, every line item."""
    if which.upper() not in {"IS", "BS", "CF"}:
        raise HTTPException(400, "which must be IS, BS or CF")
    return rep.statement(db, which, period_type, scope)


@router.get("/variance")
def variance(
    period: str = Query(..., description="e.g. 2024 or Q1-2026"),
    statement: str = Query("IS", description="IS | BS | CF"),
    scope: str = Query("standalone"),
    db: Session = Depends(get_db),
):
    """Actual against budget for one period."""
    return rep.actual_vs_budget(db, period, statement, scope)


@router.get("/periods")
def periods(
    period_type: str = Query("annual"),
    scope: str = Query("standalone"),
    db: Session = Depends(get_db),
):
    return rep.available_periods(db, period_type, scope)


@router.get("/business-plan")
def business_plan(
    plan_year: str | None = Query(None, description="Defaults to the first projected year"),
    scope: str = Query("standalone"),
    db: Session = Depends(get_db),
):
    """Business plan actual vs plan: headline KPIs, revenue by year, quarterly
    phasing of the plan year, and the full P&L in KEGP."""
    try:
        result = rep.business_plan(db, plan_year, scope)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@router.get("/ratios")
def ratios(period_type: str = Query("annual"), db: Session = Depends(get_db)):
    return rep.ratios(db, period_type)


@router.get("/projects")
def projects(db: Session = Depends(get_db)):
    return rep.project_list(db)


@router.get("/projects/{project_id}")
def project(project_id: int, db: Session = Depends(get_db)):
    """Per-project P&L and cash flow, budget against actual."""
    try:
        return rep.project_pnl(db, project_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/projects/{project_id}/actuals")
def post_actuals(
    project_id: int,
    period: str = Body(..., description="2026-03 | Q1-2026 | 2026"),
    revenue: float = Body(0.0),
    cogs: float = Body(0.0),
    overheads: float = Body(0.0),
    depreciation: float = Body(0.0),
    cash_in: float = Body(0.0),
    cash_out: float = Body(0.0),
    db: Session = Depends(get_db),
):
    """Record actual results for a project period.

    Per-project actual costs are not in any of the source workbooks, so this is
    the entry point that makes project-level variance reporting possible.
    """
    if db.query(Project).filter(Project.id == project_id).first() is None:
        raise HTTPException(404, f"Project {project_id} not found")

    row = (
        db.query(ProjectPeriod)
        .filter(
            ProjectPeriod.project_id == project_id,
            ProjectPeriod.period == period,
            ProjectPeriod.scenario == "actual",
        )
        .first()
    )
    if row is None:
        row = ProjectPeriod(project_id=project_id, period=period, scenario="actual")
        db.add(row)

    row.revenue = revenue
    row.cogs = cogs
    row.gross_profit = revenue - cogs
    row.overheads = overheads
    row.ebitda = row.gross_profit - overheads
    row.depreciation = depreciation
    row.ebit = row.ebitda - depreciation
    row.net_profit = row.ebit
    row.cash_in = cash_in
    row.cash_out = cash_out
    row.net_cash_flow = cash_in - cash_out
    db.commit()

    return {
        "project_id": project_id,
        "period": period,
        "revenue": row.revenue,
        "gross_profit": row.gross_profit,
        "ebitda": row.ebitda,
        "net_cash_flow": row.net_cash_flow,
    }
