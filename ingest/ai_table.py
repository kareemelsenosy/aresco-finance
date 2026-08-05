"""
Read a team's own export by mapping its columns onto ours.

Every team keeps its register in its own shape — the AR sub-ledger comes out of
the accounting system with its own headers, procurement exports theirs, costing
theirs. Rather than ask each team to re-key into a template, this reads the
header row and asks the model which of our fields each column corresponds to.

Only the *mapping* is done by the model. Every value is then parsed and typed by
the code below, so a hallucinated number cannot reach the database — the model
never sees its output written anywhere.

Nothing is committed on upload: `map_table` returns the mapping and a typed
preview, and `commit_rows` writes only what came back from a confirmed preview.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime

from sqlalchemy.orm import Session

from api.config import settings
from api.models import (
    Customer, DownPayment, FinancialLine, IngestLog, Project, ProjectCost,
    PurchaseOrder, PurchaseRequisition, Receivable, TaxObligation,
    TradeFinanceFacility,
)

MAX_PREVIEW_ROWS = 15
MAX_SAMPLE_ROWS = 8       # rows shown to the model; the header does most of the work


# --- what each target accepts ---------------------------------------------

TARGETS: dict[str, dict] = {
    "receivables": {
        "model": Receivable,
        "label": "AR sub-ledger",
        "fields": {
            "customer_name": "Customer / debtor name",
            "ar_class": "Accounting class: trade, contract_asset or retention",
            "category": "Business unit the debt sits in, e.g. Factory or Projects",
            "currency": "Currency code, e.g. EGP or USD",
            "amount": "Outstanding amount in that currency",
            "invoice_date": "Invoice date",
            "due_date": "Date the amount falls due",
            "doc_type": "Invoice, IPC, certificate, retention",
            "aging_bucket": "Ageing band if the export carries one",
            "notes": "Anything else worth keeping",
        },
        "required": ["customer_name", "amount"],
    },
    "customers": {
        "model": Customer,
        "label": "Customer payment terms",
        "fields": {
            "name": "Customer name",
            "category": "Segment or customer type",
            "payment_terms_days": "Contractual payment terms in days",
            "avg_days_late": "Typical days late beyond terms",
            "collection_rate": "Share of billing historically collected, 0-1",
            "notes": "Anything else worth keeping",
        },
        "required": ["name"],
        "upsert_on": "name",
    },
    "project_costs": {
        "model": ProjectCost,
        "label": "Per-project costs",
        "fields": {
            "project_name": "Project name or code (matched to the project register)",
            "period": "Period label, e.g. 2026-07 or Q3-2026",
            "category": "Cost category — materials, labour, subcontract, overhead",
            "scenario": "actual or budget",
            "currency": "Currency code",
            "amount": "Cost amount in that currency",
            "notes": "Anything else worth keeping",
        },
        "required": ["amount"],
    },
    "financial_lines": {
        "model": FinancialLine,
        "label": "Management / consolidated accounts",
        "fields": {
            "statement": "IS, BS or CF",
            "line_item": "Caption as printed",
            "period": "Period label, e.g. 2026-Q2 or 2026",
            "scenario": "actual, budget or forecast",
            "scope": "standalone or consolidated",
            "currency": "Currency code",
            "amount": "Amount in that currency",
        },
        "required": ["line_item", "amount"],
    },
    # The five registers that already have templates. Mapping is only used when
    # the uploaded headers do not match the template outright.
    "trade-finance": {"model": TradeFinanceFacility, "label": "L/G and L/C register",
                      "required": ["counterparty", "face_value"]},
    "down-payments": {"model": DownPayment, "label": "Contracts & advances",
                      "required": ["counterparty", "amount"]},
    "tax": {"model": TaxObligation, "label": "Tax & governmental dues",
            "required": ["tax_type", "amount_due"]},
    "requisitions": {"model": PurchaseRequisition, "label": "Purchase requisitions",
                     "required": ["pr_number"]},
    "orders": {"model": PurchaseOrder, "label": "Purchase orders",
               "required": ["po_number"]},
}


def _fields_for(target: str) -> dict[str, str]:
    """Field -> description. Registers fall back to their column names."""
    spec = TARGETS[target]
    if "fields" in spec:
        return spec["fields"]
    model = spec["model"]
    skip = {"id", "created_at", "updated_at", "ingested_at", "source_file", "amount_egp"}
    return {c.name: f"{c.name.replace('_', ' ')}" for c in model.__table__.columns
            if c.name not in skip}


# --- reading the uploaded file --------------------------------------------


def read_table(filename: str, raw: bytes) -> tuple[list[str], list[list]]:
    """Return (headers, rows) from a CSV or Excel upload."""
    low = filename.lower()
    if low.endswith((".xlsx", ".xlsm")):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        ws = wb[wb.sheetnames[0]]
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
    else:
        text = raw.decode("utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|") \
            if text.strip() else csv.excel
        grid = [r for r in csv.reader(io.StringIO(text), dialect)]

    # The header is the first row with at least two non-empty cells — exports
    # routinely carry a title row above it.
    head_at = 0
    for i, row in enumerate(grid[:12]):
        if sum(1 for c in row if str(c or "").strip()) >= 2:
            head_at = i
            break
    headers = [str(c or "").strip() for c in grid[head_at]]
    rows = [r for r in grid[head_at + 1:] if any(str(c or "").strip() for c in r)]
    return headers, rows


# --- value parsing (never delegated to the model) --------------------------

_NUM = re.compile(r"-?[\d,]*\.?\d+")


def to_float(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("٬", ",")
    neg = s.startswith("(") and s.endswith(")")
    m = _NUM.search(s.replace(",", ""))
    if not m:
        return None
    out = float(m.group())
    return -out if neg else out


def to_int(v):
    f = to_float(v)
    return int(round(f)) if f is not None else None


def to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
                "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _coerce(model, field: str, value):
    col = model.__table__.columns.get(field)
    if col is None:
        return None if value in ("", None) else str(value)
    py = col.type.python_type
    if py is date:
        return to_date(value)
    if py is datetime:
        d = to_date(value)
        return datetime.combine(d, datetime.min.time()) if d else None
    if py is float:
        return to_float(value)
    if py is int:
        return to_int(value)
    if py is bool:
        return str(value).strip().lower() in {"1", "true", "yes", "y"}
    s = "" if value is None else str(value).strip()
    limit = getattr(col.type, "length", None)
    return s[:limit] if limit else s


# --- the mapping call ------------------------------------------------------

MAPPING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source_column": {"type": "string"},
                    "target_field": {
                        "type": "string",
                        "description": "One of the target fields, or empty to ignore this column",
                    },
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                },
                "required": ["source_column", "target_field", "confidence", "reason"],
            },
        },
        "constants": {
            "type": "array",
            "description": "Fields not present as a column but implied by the file, e.g. scope=consolidated from the title",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target_field": {"type": "string"},
                    "value": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["target_field", "value", "reason"],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["mappings", "constants", "warnings"],
}

SYSTEM = """You map the columns of a finance export onto a fixed set of target fields.

Rules:
- Map a source column only when you are confident what it holds. Leave
  target_field empty rather than guessing; an unmapped column is recoverable,
  a wrongly mapped one silently corrupts the register.
- Never invent data. You are choosing column correspondences, not values.
- Use the sample rows to confirm a column really holds what its header claims —
  headers are often stale or reused.
- If a required field has no matching column, say so in warnings.
- Two source columns must not map to the same target field. Pick the better one
  and warn about the other.
- Use `constants` only where the file itself implies a value for every row (a
  title saying "Consolidated", a single currency stated in a header)."""


def map_table(target: str, filename: str, raw: bytes, model_name: str | None = None) -> dict:
    """Read the upload, ask the model for a column mapping, return a typed preview."""
    if target not in TARGETS:
        raise ValueError(f"Unknown target '{target}'. One of: {', '.join(TARGETS)}")
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set, so a file whose columns do not already "
            "match the template cannot be mapped."
        )

    headers, rows = read_table(filename, raw)
    if not headers:
        raise ValueError(f"No header row found in {filename}.")
    if not rows:
        raise ValueError(f"{filename} has a header but no data rows.")

    fields = _fields_for(target)
    spec = TARGETS[target]
    sample = [[("" if c is None else str(c))[:60] for c in r[:len(headers)]]
              for r in rows[:MAX_SAMPLE_ROWS]]

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = (
        f"Target register: {spec['label']}\n\n"
        f"Target fields:\n"
        + "\n".join(f"  {k} — {v}" for k, v in fields.items())
        + f"\n\nRequired: {', '.join(spec['required'])}\n\n"
        f"Uploaded file: {filename}\n"
        f"Header row: {json.dumps(headers, ensure_ascii=False)}\n\n"
        f"First {len(sample)} data rows:\n"
        + "\n".join(json.dumps(r, ensure_ascii=False) for r in sample)
    )

    response = client.messages.create(
        model=model_name or settings.anthropic_model,
        max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        output_config={"format": {"type": "json_schema", "schema": MAPPING_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to map this file.")
    plan = json.loads(next((b.text for b in response.content if b.type == "text"), "{}"))

    return _apply_mapping(target, filename, headers, rows, plan)


def _apply_mapping(target: str, filename: str, headers, rows, plan: dict) -> dict:
    spec = TARGETS[target]
    model = spec["model"]
    fields = _fields_for(target)

    col_of, used, mapping_out = {}, set(), []
    for m in plan.get("mappings", []):
        field = (m.get("target_field") or "").strip()
        src = m.get("source_column", "")
        entry = {"source_column": src, "target_field": field,
                 "confidence": m.get("confidence", "low"), "reason": m.get("reason", "")}
        if not field or field not in fields:
            entry["target_field"] = ""
            mapping_out.append(entry)
            continue
        if src not in headers:
            entry["target_field"] = ""
            entry["reason"] = "column not found in the file"
            mapping_out.append(entry)
            continue
        if field in used:
            entry["target_field"] = ""
            entry["reason"] = "another column already maps here"
            mapping_out.append(entry)
            continue
        used.add(field)
        col_of[field] = headers.index(src)
        mapping_out.append(entry)

    constants = {}
    for c in plan.get("constants", []):
        f = (c.get("target_field") or "").strip()
        if f in fields and f not in used:
            constants[f] = c.get("value", "")

    warnings = list(plan.get("warnings", []))
    missing = [f for f in spec["required"] if f not in col_of and f not in constants]
    if missing:
        warnings.append("No column found for required field(s): " + ", ".join(missing))

    parsed, skipped = [], 0
    for r in rows:
        row = {}
        for field, idx in col_of.items():
            row[field] = _coerce(model, field, r[idx] if idx < len(r) else None)
        for field, value in constants.items():
            row[field] = _coerce(model, field, value)
        if not any(v not in (None, "", 0) for v in row.values()):
            skipped += 1
            continue
        if any(row.get(f) in (None, "") for f in spec["required"]):
            skipped += 1
            continue
        parsed.append(row)

    unmapped = [h for h in headers if h not in
                {m["source_column"] for m in mapping_out if m["target_field"]}]

    return {
        "target": target,
        "label": spec["label"],
        "file": filename,
        "headers": headers,
        "mapping": mapping_out,
        "constants": constants,
        "unmapped_columns": unmapped,
        "rows_in": len(rows),
        "rows_ready": len(parsed),
        "rows_skipped": skipped,
        "warnings": warnings,
        "preview": parsed[:MAX_PREVIEW_ROWS],
        "rows": parsed,
    }


# --- committing a reviewed preview ----------------------------------------


def commit_rows(db: Session, target: str, filename: str, rows: list[dict]) -> dict:
    """Write rows that came back from a reviewed preview."""
    if target not in TARGETS:
        raise ValueError(f"Unknown target '{target}'")
    spec = TARGETS[target]
    model = spec["model"]
    upsert_on = spec.get("upsert_on")

    written = updated = failed = 0
    errors: list[str] = []

    for i, raw_row in enumerate(rows, start=1):
        try:
            row = {k: _coerce(model, k, v) for k, v in raw_row.items()
                   if k in model.__table__.columns}
            if not row:
                continue

            if upsert_on and row.get(upsert_on):
                existing = (db.query(model)
                            .filter(getattr(model, upsert_on) == row[upsert_on]).first())
                if existing:
                    for k, v in row.items():
                        if v not in (None, ""):
                            setattr(existing, k, v)
                    updated += 1
                    continue

            obj = model(**row)
            _post_fill(db, target, obj, raw_row)
            db.add(obj)
            db.flush()
            written += 1
        except Exception as exc:
            db.rollback()
            failed += 1
            if len(errors) < 25:
                errors.append(f"row {i}: {type(exc).__name__}: {exc}")

    db.add(IngestLog(
        file_name=filename, kind=f"ai:{target}",
        rows_in=len(rows), rows_out=written + updated,
        ok=failed == 0, warnings="\n".join(errors),
    ))
    db.commit()

    return {"target": target, "file": filename, "created": written,
            "updated": updated, "failed": failed, "errors": errors}


def _post_fill(db: Session, target: str, obj, raw_row: dict):
    """Derived fields the source file never carries."""
    if target == "receivables":
        obj.snapshot_date = obj.snapshot_date or date.today()
        obj.source_file = obj.source_file or "intake"
        if obj.amount is not None and obj.amount_egp in (None, 0):
            obj.amount_egp = _to_egp(db, obj.amount, obj.currency)
    elif target == "project_costs":
        name = (raw_row.get("project_name") or "").strip()
        if name and not obj.project_id:
            proj = db.query(Project).filter(Project.name == name).first()
            if proj:
                obj.project_id = proj.id
        obj.scenario = obj.scenario or "actual"
        obj.source = obj.source or "intake"
        if obj.amount is not None and obj.amount_egp in (None, 0):
            obj.amount_egp = _to_egp(db, obj.amount, obj.currency)
    elif target == "financial_lines":
        obj.scenario = obj.scenario or "actual"
        obj.scope = obj.scope or "standalone"
    elif target == "down-payments":
        if obj.amount is not None and not obj.amount_egp:
            obj.amount_egp = _to_egp(db, obj.amount, obj.currency)
    elif target == "trade-finance":
        if not obj.margin_amount:
            obj.margin_amount = (obj.face_value or 0) * (obj.margin_pct or 0)


def _to_egp(db: Session, amount: float, currency: str | None) -> float:
    cur = (currency or "EGP").upper()
    if cur == "EGP":
        return amount
    try:
        from engine.fx import FXConverter

        return FXConverter(db).try_to_egp(amount, cur) or 0.0
    except Exception:
        return 0.0
