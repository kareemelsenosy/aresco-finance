"""External audit assistant: knowledge store, query drafting, tax evader screening."""

import json
from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import AuditEngagement, AuditQuery, KnowledgeDoc, TaxEvader
from engine import audit_agent as agent

router = APIRouter(prefix="/audit", tags=["audit"])


def _json_list(raw: str):
    try:
        return json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return [raw]


# --- Engagements ---------------------------------------------------------

@router.post("/engagements")
def create_engagement(
    name: str = Body(...),
    fiscal_year: str = Body(...),
    auditor: str = Body(""),
    framework: str = Body("EAS"),
    db: Session = Depends(get_db),
):
    row = AuditEngagement(
        name=name, fiscal_year=fiscal_year, auditor=auditor, framework=framework
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "name": row.name, "fiscal_year": row.fiscal_year}


@router.get("/engagements")
def engagements(db: Session = Depends(get_db)):
    return [
        {
            "id": e.id, "name": e.name, "fiscal_year": e.fiscal_year,
            "auditor": e.auditor, "framework": e.framework, "status": e.status,
            "queries": db.query(AuditQuery).filter(
                AuditQuery.engagement_id == e.id
            ).count(),
        }
        for e in db.query(AuditEngagement).all()
    ]


# --- Query drafting ------------------------------------------------------

@router.post("/queries/draft")
def draft(
    query_text: str = Body(..., description="The auditor's comment or inquiry, verbatim"),
    context: str = Body("", description="Anything the controller wants the agent to know"),
    area_hint: str = Body("", description="e.g. Revenue, Receivables, Provisions, Tax"),
    engagement_id: int | None = Body(None),
    reference: str = Body("", description="The auditor's own reference for this point"),
    save: bool = Body(True),
    db: Session = Depends(get_db),
):
    """Draft a response to an auditor query.

    The agent cites only references held in the knowledge store and quotes only
    figures read from the database. Anything it needed but did not have comes
    back in `missing_references` and `data_required`.
    """
    try:
        result = agent.draft_response(db, query_text, context, area_hint)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")

    if save:
        row = agent.save_query(db, engagement_id, query_text, result, reference)
        result["query_id"] = row.id
    return result


@router.get("/queries")
def queries(
    engagement_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(AuditQuery)
    if engagement_id:
        q = q.filter(AuditQuery.engagement_id == engagement_id)
    if status:
        q = q.filter(AuditQuery.status == status)
    return [
        {
            "id": r.id,
            "reference": r.reference,
            "area": r.area,
            "query_text": r.query_text,
            "draft_response": r.draft_response,
            "final_response": r.final_response,
            "standards_cited": _json_list(r.standards_cited),
            "documents_required": _json_list(r.documents_required),
            "risk_flags": _json_list(r.risk_flags),
            "status": r.status,
            "reviewed_by": r.reviewed_by,
            "model_used": r.model_used,
            "created_at": r.created_at.isoformat(),
        }
        for r in q.order_by(AuditQuery.created_at.desc()).all()
    ]


@router.put("/queries/{query_id}")
def update_query(
    query_id: int,
    final_response: str | None = Body(None),
    status: str | None = Body(None, description="drafted | reviewed | sent | closed"),
    reviewed_by: str | None = Body(None),
    db: Session = Depends(get_db),
):
    """Record the controller's edited response and sign-off."""
    row = db.query(AuditQuery).filter(AuditQuery.id == query_id).first()
    if row is None:
        raise HTTPException(404, f"Query {query_id} not found")
    if final_response is not None:
        row.final_response = final_response
    if status is not None:
        row.status = status
    if reviewed_by is not None:
        row.reviewed_by = reviewed_by
    db.commit()
    return {"id": row.id, "status": row.status, "reviewed_by": row.reviewed_by}


# --- Knowledge store -----------------------------------------------------

@router.get("/knowledge")
def knowledge(framework: str | None = None, db: Session = Depends(get_db)):
    q = db.query(KnowledgeDoc)
    if framework:
        q = q.filter(KnowledgeDoc.framework == framework)
    return [
        {
            "id": d.id, "framework": d.framework, "ref": d.ref, "title": d.title,
            "tags": d.tags, "body": d.body,
            "needs_verification": "VERIFY" in d.body or "PLACEHOLDER" in d.body,
        }
        for d in q.order_by(KnowledgeDoc.framework, KnowledgeDoc.ref).all()
    ]


@router.post("/knowledge/seed")
def seed(force: bool = Query(False), db: Session = Depends(get_db)):
    """Load the starter reference pack.

    Entries marked VERIFY or PLACEHOLDER must be checked against the official
    text before any response relying on them is sent to an auditor.
    """
    added = agent.seed_knowledge(db, force)
    return {
        "added": added,
        "total": db.query(KnowledgeDoc).count(),
        "warning": "Verify every entry against the official text. The seeded pack "
                   "is a starting point, not a certified copy of the standards.",
    }


@router.post("/knowledge")
def add_knowledge(
    framework: str = Body(..., description="EAS | IFRS | LAW_159 | TAX | POLICY"),
    ref: str = Body(...),
    title: str = Body(""),
    body: str = Body(...),
    tags: str = Body(""),
    db: Session = Depends(get_db),
):
    """Add or replace a reference. This is how you extend what the agent can cite."""
    row = db.query(KnowledgeDoc).filter(KnowledgeDoc.ref == ref).first()
    if row is None:
        row = KnowledgeDoc(ref=ref)
        db.add(row)
    row.framework, row.title, row.body, row.tags = framework, title, body, tags
    db.commit()
    return {"ref": ref, "framework": framework}


@router.delete("/knowledge/{doc_id}")
def delete_knowledge(doc_id: int, db: Session = Depends(get_db)):
    row = db.query(KnowledgeDoc).filter(KnowledgeDoc.id == doc_id).first()
    if row is None:
        raise HTTPException(404, f"Reference {doc_id} not found")
    db.delete(row)
    db.commit()
    return {"deleted": doc_id}


@router.get("/knowledge/search")
def search(q: str = Query(...), limit: int = 8, db: Session = Depends(get_db)):
    """See what the agent would retrieve for a given query."""
    docs = agent.retrieve(db, q, limit)
    return [{"ref": d.ref, "title": d.title, "framework": d.framework} for d in docs]


# --- Tax evader register -------------------------------------------------

@router.get("/tax-evaders")
def tax_evaders(db: Session = Depends(get_db)):
    return [
        {
            "id": t.id, "name": t.name, "tax_id": t.tax_id,
            "announced_on": t.announced_on.isoformat() if t.announced_on else None,
            "source": t.source,
        }
        for t in db.query(TaxEvader).order_by(TaxEvader.name).all()
    ]


@router.post("/tax-evaders")
def add_evader(
    name: str = Body(...),
    tax_id: str = Body(""),
    announced_on: date | None = Body(None),
    source: str = Body("", description="Where this entry came from"),
    notes: str = Body(""),
    db: Session = Depends(get_db),
):
    """Add an entry to the tax evader register.

    The register ships empty on purpose: the list changes and must be taken from
    the Egyptian Tax Authority's own publication, not from this application.
    """
    row = TaxEvader(
        name=name, tax_id=tax_id, announced_on=announced_on,
        source=source, notes=notes,
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.post("/tax-evaders/screen")
def screen(
    names: list[str] = Body(..., embed=True),
    db: Session = Depends(get_db),
):
    """Screen counterparty names against the register."""
    hits = agent.screen_counterparties(db, names)
    return {
        "screened": len(names),
        "register_size": db.query(TaxEvader).count(),
        "matches": hits,
        "note": "An empty register returns no matches. Load the current published "
                "list before relying on a clean result.",
    }


@router.post("/tax-evaders/screen-customers")
def screen_customers(db: Session = Depends(get_db)):
    """Screen every ingested customer against the register."""
    from api.models import Customer

    names = [c.name for c in db.query(Customer).all()]
    hits = agent.screen_counterparties(db, names)
    return {
        "customers_screened": len(names),
        "register_size": db.query(TaxEvader).count(),
        "matches": hits,
    }
