"""
Read a workbook the dedicated loader could not.

The four workbook loaders each know one file's shape. That is the right default
— they are exact, they are free, and they catch things a general reader would
miss. But they break the moment the shape moves: a sheet renamed `Cash-In (2)`,
a column relabelled `Customer Name` instead of `Project Name`, the register put
on the fourteenth tab instead of the first.

When that happens this takes over. It surveys every sheet, shows the model what
it found, and asks *where the data is* — which sheet, which row is the header,
which column feeds which field. The model never sees the database and never
produces a value: it returns coordinates, and the code reads the cells at those
coordinates through the same typed parsers the loaders use. A hallucinated
number cannot reach the ledger, because no number ever comes back from the model.

Nothing is written. `repair()` returns a plan and a typed preview for review.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

from ingest import ai_table, llm
from ingest.common import clean_str

MAX_SHEETS = 40         # a survey past this is noise, and the file is a workbook dump
MAX_SAMPLE_ROWS = 12    # enough to see a header block and the first real rows
MAX_COLS = 30
CELL_CHARS = 40


# --- what the model is allowed to answer -----------------------------------
# Every property is required and additionalProperties is false throughout:
# OpenAI's strict json_schema mode rejects a schema that leaves either open, and
# Anthropic accepts the same shape, so one schema serves both providers.

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "diagnosis": {
            "type": "string",
            "description": "Why the standard loader failed, in one sentence a "
                           "finance person would understand.",
        },
        "sheet": {"type": "string", "description": "Exact name of the sheet holding the register"},
        "header_row": {
            "type": "integer",
            "description": "0-based index of the header row WITHIN THE SAMPLE shown for that sheet",
        },
        "first_data_row": {
            "type": "integer",
            "description": "0-based index of the first row of real data, within the same sample",
        },
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "column_index": {"type": "integer", "description": "0-based column index"},
                    "source_header": {"type": "string"},
                    "target_field": {"type": "string", "description": "One of the target fields"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                },
                "required": ["column_index", "source_header", "target_field",
                             "confidence", "reason"],
            },
        },
        "constants": {
            "type": "array",
            "description": "A field every row shares, stated somewhere in the file "
                           "rather than in a column (a date in the title, a currency block)",
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
        "missing_required": {
            "type": "array",
            "description": "Required fields with no column and no constant in this file",
            "items": {"type": "string"},
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["diagnosis", "sheet", "header_row", "first_data_row", "columns",
                 "constants", "missing_required", "confidence", "warnings"],
}

SYSTEM = """You locate finance data inside a spreadsheet whose layout is unknown.

You are given every sheet in a workbook, with the first rows of each, and a list
of target fields. Return WHERE the data is — the sheet, the header row, and which
column feeds which field. You never return values; code reads the cells you point at.

Rules:
- Pick the sheet holding the actual line-item register. Prefer detail over a
  summary, pivot, or cover sheet. A sheet of totals is the wrong answer.
- header_row and first_data_row are indexes INTO THE SAMPLE ROWS shown for that
  sheet, counting the first sample row as 0.
- Where a header is stacked over two or three rows, give the row carrying the
  field names, and set first_data_row past the whole stack.
- Map a column only when the sample values confirm what it holds. Headers get
  reused and go stale; the values are the evidence. Leave target_field empty
  rather than guess — an unmapped column can be fixed, a wrongly mapped one
  silently corrupts the register.
- Two columns must never map to the same field. Choose one, warn about the other.
- List every required field you cannot find in missing_required. Do not invent a
  column to satisfy one, and do not map a loosely-related column to fill the gap.
- Say so in warnings when a mapping carries a real consequence — a missing status
  column meaning nothing can be marked delivered, amounts that may be a different
  currency than labelled, a date format that could be read two ways."""


# --- surveying the workbook ------------------------------------------------

def survey(path: str | Path) -> list[dict]:
    """Every sheet, with the top rows of each, trimmed for a prompt."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    try:
        for ws in wb.worksheets[:MAX_SHEETS]:
            rows = []
            for row in ws.iter_rows(max_row=MAX_SAMPLE_ROWS, max_col=MAX_COLS,
                                    values_only=True):
                rows.append([clean_str(c)[:CELL_CHARS] for c in row])
            while rows and not any(c for c in rows[-1]):
                rows.pop()
            out.append({
                "sheet": ws.title,
                "dimensions": ws.calculate_dimension(),
                "sample_rows": rows,
            })
    finally:
        wb.close()
    return out


def _prompt(target: str, filename: str, sheets: list[dict], error: str) -> str:
    fields = ai_table._fields_for(target)
    spec = ai_table.TARGETS[target]

    parts = [
        f"Target register: {spec['label']}",
        "",
        "Target fields:",
        *(f"  {k} — {v}" for k, v in fields.items()),
        "",
        f"Required: {', '.join(spec['required'])}",
        "",
        f"Uploaded file: {filename}",
        f"What the standard loader said: {error}",
        "",
        f"The workbook has {len(sheets)} sheet(s):",
    ]
    for s in sheets:
        parts.append("")
        parts.append(f"--- sheet {s['sheet']!r}  (range {s['dimensions']})")
        for i, row in enumerate(s["sample_rows"]):
            trimmed = list(row)
            while trimmed and not trimmed[-1]:
                trimmed.pop()
            parts.append(f"  [{i}] {trimmed}")
    return "\n".join(parts)


# --- applying a plan -------------------------------------------------------

def _read_rows(path: str | Path, sheet: str, header_row: int, first_data_row: int):
    """Every row of the chosen sheet from first_data_row on, plus its header."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in wb.sheetnames:
            raise ValueError(f"The plan named sheet {sheet!r}, which is not in the file.")
        ws = wb[sheet]
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()

    header = [clean_str(c) for c in grid[header_row]] if header_row < len(grid) else []
    body = [r for r in grid[first_data_row:] if any(clean_str(c) for c in r)]
    return header, body


def apply_plan(target: str, path: str | Path, plan: dict) -> dict:
    """Turn a plan into typed rows. No database, no writes — a preview only."""
    spec = ai_table.TARGETS[target]
    model = spec["model"]
    fields = ai_table._fields_for(target)

    header_row = max(0, int(plan.get("header_row", 0)))
    first_data_row = max(header_row + 1, int(plan.get("first_data_row", header_row + 1)))
    header, body = _read_rows(path, plan.get("sheet", ""), header_row, first_data_row)

    col_of: dict[str, int] = {}
    mapping_out = []
    for c in plan.get("columns", []):
        field = (c.get("target_field") or "").strip()
        idx = c.get("column_index")
        entry = {"source_column": c.get("source_header", ""), "column_index": idx,
                 "target_field": field, "confidence": c.get("confidence", "low"),
                 "reason": c.get("reason", "")}
        if not field or field not in fields or not isinstance(idx, int) or idx < 0:
            entry["target_field"] = ""
        elif field in col_of:
            entry["target_field"] = ""
            entry["reason"] = "another column already maps here"
        else:
            col_of[field] = idx
        mapping_out.append(entry)

    constants = {}
    for c in plan.get("constants", []):
        f = (c.get("target_field") or "").strip()
        if f in fields and f not in col_of:
            constants[f] = c.get("value", "")

    parsed, skipped = [], 0
    for r in body:
        row = {f: ai_table._coerce(model, f, r[i] if i < len(r) else None)
               for f, i in col_of.items()}
        for f, v in constants.items():
            row[f] = ai_table._coerce(model, f, v)
        if not any(v not in (None, "", 0) for v in row.values()):
            skipped += 1
            continue
        if any(row.get(f) in (None, "") for f in spec["required"]):
            skipped += 1
            continue
        parsed.append(row)

    warnings = list(plan.get("warnings", []))
    missing = [f for f in spec["required"] if f not in col_of and f not in constants]
    if missing:
        warnings.append("No column found for required field(s): " + ", ".join(missing))
    if body and not parsed:
        warnings.append(
            f"All {len(body)} rows were skipped — every one was missing a required "
            f"field ({', '.join(spec['required'])}). The mapping is probably wrong."
        )

    return {
        "target": target,
        "label": spec["label"],
        "sheet": plan.get("sheet", ""),
        "header_row": header_row,
        "first_data_row": first_data_row,
        "headers": header,
        "mapping": mapping_out,
        "constants": constants,
        "rows_in": len(body),
        "rows_ready": len(parsed),
        "rows_skipped": skipped,
        "warnings": warnings,
        "preview": parsed[:ai_table.MAX_PREVIEW_ROWS],
        "rows": parsed,
    }


# --- the whole job ---------------------------------------------------------

def repair(target: str, path: str | Path, filename: str, error: str) -> dict:
    """Survey, ask, and return a reviewable plan. Raises if no provider answers."""
    if target not in ai_table.TARGETS:
        raise ValueError(f"Nothing to repair into: unknown target {target!r}.")

    sheets = survey(path)
    if not sheets:
        raise ValueError(f"{filename} has no readable worksheets.")

    plan, meta = llm.complete_json(
        _prompt(target, filename, sheets, error), SYSTEM, PLAN_SCHEMA, max_tokens=8000)

    result = apply_plan(target, path, plan)
    result["diagnosis"] = plan.get("diagnosis", "")
    result["confidence"] = plan.get("confidence", "low")
    result["missing_required"] = plan.get("missing_required", [])
    result["read_by"] = meta
    result["sheets_considered"] = [s["sheet"] for s in sheets]
    return result
