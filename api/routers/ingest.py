"""Upload and load the finance team's workbooks."""

import shutil
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from api.config import settings
from api.database import get_db
from api.models import IngestLog
from ingest.bank_cash import ingest_bank_cash
from ingest.business_plan import ingest_business_plan
from ingest.cash_in import ingest_cash_in
from ingest.checks import ingest_checks

router = APIRouter(prefix="/ingest", tags=["ingest"])

LOADERS = {
    "bank_cash": ingest_bank_cash,
    "checks": ingest_checks,
    "cash_in": ingest_cash_in,
    "business_plan": ingest_business_plan,
}

# Matched against the filename when `kind` isn't given explicitly
AUTODETECT = [
    ("bank balance with checks", "checks"),
    ("checks list", "checks"),
    ("bank cash", "bank_cash"),
    ("cash-in", "cash_in"),
    ("cash in", "cash_in"),
    ("bp -", "business_plan"),
    ("business plan", "business_plan"),
]


def detect_kind(filename: str) -> str | None:
    low = filename.lower()
    for fragment, kind in AUTODETECT:
        if fragment in low:
            return kind
    return None


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    kind: str | None = Query(None, description="bank_cash | checks | cash_in | business_plan"),
    replace: bool = Query(True, description="Replace existing data for the same date"),
    db: Session = Depends(get_db),
):
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Only .xlsx / .xlsm workbooks are accepted here")

    kind = kind or detect_kind(file.filename)
    if kind is None:
        raise HTTPException(
            400,
            f"Could not tell what '{file.filename}' is. Pass ?kind= one of: "
            + ", ".join(LOADERS),
        )
    if kind not in LOADERS:
        raise HTTPException(400, f"Unknown kind '{kind}'. Use one of: " + ", ".join(LOADERS))

    dest = settings.upload_path / file.filename
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)

    try:
        result = LOADERS[kind](db, str(dest), replace=replace)
    except Exception as exc:
        db.rollback()
        raise HTTPException(422, f"{type(exc).__name__} while reading {file.filename}: {exc}")

    return {"file": file.filename, "detected_kind": kind, **result}


@router.post("/load-directory")
def load_directory(
    path: str = Query(..., description="Absolute path to a folder of workbooks"),
    db: Session = Depends(get_db),
):
    """Load every recognised workbook in a folder.

    Cash-In goes first: its header carries the FX rates the other loaders need.
    """
    folder = Path(path)
    if not folder.is_dir():
        raise HTTPException(400, f"Not a directory: {path}")

    order = ["cash_in", "bank_cash", "checks", "business_plan"]
    files = [p for p in sorted(folder.glob("*.xlsx")) if not p.name.startswith("~")]
    queued = [(p, detect_kind(p.name)) for p in files]
    queued = [(p, k) for p, k in queued if k]

    results, skipped = [], [p.name for p in files if detect_kind(p.name) is None]
    for kind in order:
        for p, k in queued:
            if k != kind:
                continue
            try:
                results.append({"file": p.name, **LOADERS[kind](db, str(p))})
            except Exception as exc:
                db.rollback()
                results.append(
                    {"file": p.name, "kind": kind, "error": f"{type(exc).__name__}: {exc}"}
                )
    return {"loaded": results, "unrecognised": skipped}


@router.get("/log")
def ingest_log(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.query(IngestLog).order_by(IngestLog.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "file": r.file_name,
            "kind": r.kind,
            "as_of": r.as_of.isoformat() if r.as_of else None,
            "rows_in": r.rows_in,
            "rows_out": r.rows_out,
            "ok": r.ok,
            "warnings": [w for w in (r.warnings or "").split("\n") if w],
            "at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# --- Scanned financial statements (vision) --------------------------------

@router.post("/fs-pdf/extract")
def extract_fs(
    path: str = Query(..., description="Absolute path to the scanned FS PDF"),
    first_page: int = Query(1, ge=1),
    last_page: int | None = Query(None, ge=1),
):
    """Transcribe a scanned financial-statements PDF using vision.

    Returns the transcription plus a check report that cross-foots each
    statement against its own printed totals. Nothing is written to the
    database — review the report, then POST /ingest/fs-pdf/load.
    """
    from ingest.fs_pdf import extract_fs_pdf

    if not Path(path).is_file():
        raise HTTPException(400, f"Not a file: {path}")
    try:
        return extract_fs_pdf(path, first_page, last_page)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")


@router.post("/fs-pdf/load")
def load_fs(
    extraction: dict = Body(..., description="The payload returned by /ingest/fs-pdf/extract"),
    scope: str = Query("standalone", description="standalone | consolidated"),
    confirm: bool = Query(False, description="Required — vision output must be reviewed first"),
    db: Session = Depends(get_db),
):
    """Write a reviewed transcription into the reporting data as actuals."""
    from ingest.fs_pdf import load_extraction

    return load_extraction(db, extraction, scope, confirm)
