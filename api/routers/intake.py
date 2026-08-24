"""
/intake — one upload point per thing a team owes the tool.

The register in `api/intake.py` says who owns each item and how a file for it
should be read. This router does the routing:

  * a workbook goes straight to the loader that already reads that workbook
  * a CSV whose headers match a register template goes through the existing
    register import
  * anything else is mapped by `ingest.ai_table` and comes back as a preview to
    confirm before a single row is written

The last case is the only one that needs review, and it says so in the response
rather than loading optimistically and asking for forgiveness.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from api.config import settings
from api.database import get_db
from api.intake import BY_ID, REQUIREMENTS, TEAM_ORDER, log_kind
from api.models import IngestLog
from ingest import ai_table, llm, repair
from ingest.normalize import UnreadableUpload, normalize

router = APIRouter(prefix="/intake", tags=["intake"])

WORKBOOK_LOADERS = {
    "bank_cash": "ingest.bank_cash:ingest_bank_cash",
    "checks": "ingest.checks:ingest_checks",
    "cash_in": "ingest.cash_in:ingest_cash_in",
    "business_plan": "ingest.business_plan:ingest_business_plan",
}


# When a workbook loader fails, the data still has to land somewhere. This is
# the register each requirement's rows belong in once a repair has read them.
REPAIR_TARGET = {
    "bank_balances": "bank_balances",
    "cheques": "checks",
    "ar_position": "receivables",
    "business_plan": "financial_lines",
    "consolidated": "financial_lines",
    "management_accounts": "financial_lines",
}

# How many rows of the thing the requirement actually exists for. A loader that
# reports success having written none of these has not really loaded the file —
# Cash-In writing expense lines but no receivables is the case in point.
PRIMARY_COUNT = {
    "bank_cash": lambda r: r.get("rows", 0),
    "checks": lambda r: r.get("checks_ingested", 0),
    "cash_in": lambda r: r.get("receivables", 0) + r.get("forecast_inflows", 0),
    "business_plan": lambda r: r.get("financial_lines", 0),
}


def _loader(kind: str):
    from ingest.bank_cash import ingest_bank_cash
    from ingest.business_plan import ingest_business_plan
    from ingest.cash_in import ingest_cash_in
    from ingest.checks import ingest_checks

    return {"bank_cash": ingest_bank_cash, "checks": ingest_checks,
            "cash_in": ingest_cash_in, "business_plan": ingest_business_plan}[kind]


# --- status ----------------------------------------------------------------


def _last_seen(db: Session) -> dict[str, IngestLog]:
    """Most recent successful load per requirement id."""
    out: dict[str, IngestLog] = {}
    rows = (db.query(IngestLog)
            .order_by(IngestLog.created_at.desc())
            .limit(600).all())
    for r in rows:
        kind = r.kind or ""
        req = None
        if kind.startswith("intake:"):
            req = kind.split(":", 1)[1]
        else:
            # loaders write their own kind; match it back to the requirement
            for candidate in REQUIREMENTS:
                h = candidate["handler"]
                if h.startswith(("workbook:", "register:", "ai:")) and \
                        h.split(":", 1)[1] == kind.replace("ai:", ""):
                    req = candidate["id"]
                    break
        if req and req not in out and r.ok:
            out[req] = r
    return out


def _state(req: dict, log: IngestLog | None) -> str:
    if req["handler"] == "in_tool":
        return "in_tool"
    if log is None:
        return "never"
    if not req.get("due_days"):
        return "received"
    age = (datetime.utcnow() - log.created_at).days
    return "current" if age <= req["due_days"] else "overdue"


@router.get("/requirements")
def requirements(db: Session = Depends(get_db)):
    """Everything each team owes, with when it last arrived."""
    seen = _last_seen(db)
    items = []
    for req in REQUIREMENTS:
        log = seen.get(req["id"])
        items.append({
            **{k: v for k, v in req.items() if k not in ("handler", "alt_handler")},
            "handler_kind": req["handler"].split(":", 1)[0],
            "target": req["handler"].split(":", 1)[1] if ":" in req["handler"] else None,
            "state": _state(req, log),
            "last_file": log.file_name if log else None,
            "last_at": log.created_at.isoformat() if log else None,
            "last_rows": log.rows_out if log else None,
        })

    teams = []
    for team in TEAM_ORDER:
        rows = [i for i in items if i["team"] == team]
        if rows:
            teams.append({"team": team, "items": rows})

    return {
        "teams": teams,
        "counts": {
            "total": len(items),
            "never": sum(1 for i in items if i["state"] == "never"),
            "overdue": sum(1 for i in items if i["state"] == "overdue"),
            "current": sum(1 for i in items if i["state"] in ("current", "received")),
        },
        "ai_available": bool(llm.available()),
        "ai_providers": llm.available(),
    }


# --- upload ----------------------------------------------------------------


async def _repair_response(req_id: str, req: dict, filename: str,
                           path, reason: str, notes: list[str]):
    """Hand the file to the AI reader and return a plan to review.

    The read is a blocking, multi-second call to a provider. On the event loop
    it would stall every other request for its whole duration, so it goes to a
    worker thread.
    """
    # Workbook requirements need the map — their handler names a loader, not a
    # register. A register:/ai: handler already names the target it writes into.
    target = REPAIR_TARGET.get(req_id)
    if not target:
        handler_kind, _, handler_target = req["handler"].partition(":")
        if handler_kind in ("register", "ai") and handler_target in ai_table.TARGETS:
            target = handler_target
    if not target:
        raise HTTPException(422, f"Could not read {filename}: {reason}")

    try:
        plan = await run_in_threadpool(repair.repair, target, path, filename, reason)
    except llm.NoProviderError as exc:
        raise HTTPException(503, str(exc))
    except llm.AllProvidersFailed as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:
        raise HTTPException(422, f"Could not read {filename}: {reason} "
                                 f"(and the AI reader also failed: "
                                 f"{type(exc).__name__}: {exc})")

    return {
        "status": "needs_review",
        "mode": "repair",
        "req_id": req_id,
        "file": filename,
        "why": reason,
        "conversion_notes": notes,
        **plan,
    }


@router.post("/{req_id}/upload")
async def upload(req_id: str, file: UploadFile = File(...),
                 db: Session = Depends(get_db)):
    """
    Take a file for one requirement.

    The dedicated loader gets it first — it is exact and costs nothing. If the
    file's shape has moved and the loader either fails or comes back with none
    of the rows the requirement exists for, the AI reader takes over, works out
    where the data actually is, and returns a plan to confirm. Nothing is
    written on that path until /confirm.
    """
    req = BY_ID.get(req_id)
    if not req:
        raise HTTPException(404, f"Unknown requirement '{req_id}'.")
    handler = req["handler"]
    if handler == "in_tool":
        raise HTTPException(400, f"{req['need']} is done in the tool, not uploaded.")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "The file is empty.")

    dest = settings.upload_path / f"{req_id}__{file.filename}"
    dest.write_bytes(raw)

    kind, _, target = handler.partition(":")

    if kind == "document":
        db.add(IngestLog(file_name=file.filename, kind=log_kind(req_id),
                         rows_in=0, rows_out=0, ok=True))
        db.commit()
        return {"status": "filed", "file": file.filename,
                "message": "Filed for the record. Nothing to parse in this one."}

    if kind == "fs_pdf":
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, f"{req['need']} expects a PDF.")
        from ingest.fs_pdf import extract_fs_pdf

        try:
            extraction = extract_fs_pdf(str(dest))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"{type(exc).__name__}: {exc}")
        return {"status": "needs_review", "mode": "fs_pdf", "file": file.filename,
                "extraction": extraction,
                "message": "Vision transcription — check the cross-footing report "
                           "before loading."}

    # Everything else has to be a table. The extension is only a claim, so the
    # bytes decide: a .XLS that is really a tab-separated ERP dump is converted
    # here rather than rejected at the door.
    try:
        readable, notes = normalize(dest, raw)
    except UnreadableUpload as exc:
        raise HTTPException(400, str(exc))

    if kind == "workbook":
        try:
            result = _loader(target)(db, str(readable), replace=True)
        except Exception as exc:
            db.rollback()
            return await _repair_response(req_id, req, file.filename, readable,
                                          f"{type(exc).__name__}: {exc}", notes)

        loaded = PRIMARY_COUNT.get(target, lambda r: 1)(result)
        if not loaded:
            why = "; ".join(result.get("warnings") or []) or \
                  "the loader read the file but found none of the rows this item is for"
            return await _repair_response(req_id, req, file.filename, readable, why, notes)

        db.add(IngestLog(file_name=file.filename, kind=log_kind(req_id),
                         rows_in=result.get("rows_in") or 0,
                         rows_out=result.get("rows_out") or loaded, ok=True))
        db.commit()
        out = {"status": "loaded", "file": file.filename, **result}
        if notes:
            out["conversion_notes"] = notes
        return out

    # register:* and ai:* both end up here. A register file whose headers already
    # match the template needs no model call, so try that first.
    if kind == "register":
        native = _try_native_register(db, target, file.filename, raw)
        if native is not None:
            db.add(IngestLog(file_name=file.filename, kind=log_kind(req_id),
                             rows_in=native.get("loaded", 0) + native.get("failed", 0),
                             rows_out=native.get("loaded", 0),
                             ok=native.get("failed", 0) == 0))
            db.commit()
            return {"status": "loaded", "file": file.filename, "via": "template",
                    **native}

    # A multi-sheet workbook defeats the flat column mapper, which only ever sees
    # the first sheet. Send those through the repair reader, which surveys them all.
    if str(readable).lower().endswith((".xlsx", ".xlsm")):
        import openpyxl

        wb = openpyxl.load_workbook(readable, read_only=True)
        multi = len(wb.sheetnames) > 1
        wb.close()
        if multi:
            return await _repair_response(req_id, req, file.filename, readable,
                                          "the workbook has several sheets, so which one "
                                          "holds the register had to be worked out", notes)

    try:
        plan = ai_table.map_table(target, file.filename, raw)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")

    return {"status": "needs_review", "mode": "mapping", "req_id": req_id, **plan}


def _try_native_register(db: Session, register: str, filename: str, raw: bytes):
    """Load through the existing template import when the headers already fit."""
    if not filename.lower().endswith(".csv"):
        return None
    from api.routers.commitments import CSV_TEMPLATES, _MODELS

    if register not in CSV_TEMPLATES:
        return None
    try:
        headers, _ = ai_table.read_table(filename, raw)
    except Exception:
        return None

    template = set(CSV_TEMPLATES[register])
    given = {h.strip().lower().replace(" ", "_") for h in headers if h}
    overlap = template & given
    # Enough of the template present that mapping would be busywork.
    if len(overlap) < max(3, len(template) // 2):
        return None

    rows = ai_table.read_table(filename, raw)[1]
    model = _MODELS[register]
    norm = [dict(zip([h.strip().lower().replace(" ", "_") for h in headers], r))
            for r in rows]
    keep = [{k: v for k, v in row.items() if k in model.__table__.columns}
            for row in norm]
    return ai_table.commit_rows(db, register, filename, keep) | {"matched_columns": sorted(overlap)}


@router.post("/{req_id}/confirm")
def confirm(req_id: str,
            target: str = Body(...),
            file: str = Body(...),
            rows: list[dict] = Body(...),
            db: Session = Depends(get_db)):
    """Write a mapping preview that has been reviewed on screen."""
    if req_id not in BY_ID:
        raise HTTPException(404, f"Unknown requirement '{req_id}'.")
    if not rows:
        raise HTTPException(400, "Nothing to write.")
    try:
        result = ai_table.commit_rows(db, target, file, rows)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    db.add(IngestLog(file_name=file, kind=log_kind(req_id),
                     rows_in=len(rows), rows_out=result["created"] + result["updated"],
                     ok=result["failed"] == 0))
    db.commit()
    return {"status": "loaded", **result}


@router.get("/{req_id}/template")
def template(req_id: str):
    """The column headers we expect, for requirements that have a template."""
    req = BY_ID.get(req_id)
    if not req:
        raise HTTPException(404, f"Unknown requirement '{req_id}'.")
    kind, _, target = req["handler"].partition(":")

    if kind == "register":
        from api.routers.commitments import CSV_TEMPLATES

        cols = CSV_TEMPLATES.get(target, [])
    elif kind == "ai":
        cols = list(ai_table._fields_for(target))
    else:
        raise HTTPException(400, f"{req['need']} has no CSV template — "
                                 f"send the workbook as it is.")
    return {"req_id": req_id, "columns": cols, "csv_header": ",".join(cols),
            "note": "Column names need not match — anything recognisable is mapped."}
