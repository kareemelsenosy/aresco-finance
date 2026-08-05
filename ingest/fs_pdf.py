"""Extract audited financial statements from a scanned PDF.

The signed 2024 financial statements are page images with no text layer, so
there is nothing to parse — the pages are rendered and read with vision.

This is the only ingest path that can misread a figure, so it is built to be
checkable rather than trusted: every extracted line carries the page it came
from, the statements are cross-footed against their own totals, and anything
that doesn't foot is reported instead of silently loaded. Load the result with
`confirm=True` only after someone has looked at the check report.
"""

import base64
import json
import re
from datetime import date

from sqlalchemy.orm import Session

from api.config import settings
from api.models import FinancialLine, IngestLog

RENDER_DPI = 200          # legible for scanned Arabic/English tables
MAX_PAGES_PER_CALL = 4    # keeps each request well inside the image size limits

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page_number": {"type": "integer"},
                    "statement": {
                        "type": "string",
                        "description": "IS, BS, CF, EQUITY, NOTE, or NONE if the page carries no financial statement table",
                    },
                    "currency_unit": {
                        "type": "string",
                        "description": "The unit stated on the page, e.g. EGP or thousands of EGP. Empty if not stated.",
                    },
                    "column_periods": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Period label of each numeric column, left to right, e.g. ['2024','2023']",
                    },
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "label_arabic": {"type": "string"},
                                "values": {
                                    "type": "array",
                                    "items": {"type": ["number", "null"]},
                                    "description": "One value per column in column_periods order. Negative for bracketed figures. null where the cell is blank.",
                                },
                                "is_total": {"type": "boolean"},
                            },
                            "required": ["label", "label_arabic", "values", "is_total"],
                            "additionalProperties": False,
                        },
                    },
                    "unreadable": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Any figure you could not read with confidence. Say which line and column.",
                    },
                },
                "required": [
                    "page_number", "statement", "currency_unit",
                    "column_periods", "lines", "unreadable",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["pages"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You transcribe scanned financial statements into structured \
data. You are reading the audited financial statements of ARESCO, an Egyptian \
company. Pages may be bilingual Arabic/English and may be rotated.

Transcribe only. Do not compute, correct, reconcile or infer any figure. If a \
statement's own total does not equal the sum of its components as printed, \
transcribe both as printed — an inconsistency in the source is information, and \
silently fixing it destroys it.

Read every digit carefully. Figures in brackets are negative. Where a cell is \
blank use null, not zero — a blank and a zero are different facts. If you cannot \
read a figure with confidence, put null in `values` and describe it in \
`unreadable`; never guess a digit.

For `statement`, use: IS for the income statement or statement of profit or \
loss, BS for the statement of financial position or balance sheet, CF for the \
cash flow statement, EQUITY for the statement of changes in equity, NOTE for a \
note containing a numeric table, and NONE for cover pages, the auditor's \
report, or narrative pages with no table."""


def render_pages(path: str, first: int = 1, last: int | None = None) -> list[tuple[int, bytes]]:
    """Render PDF pages to PNG bytes."""
    import fitz

    doc = fitz.open(path)
    last = min(last or doc.page_count, doc.page_count)
    zoom = RENDER_DPI / 72
    out = []
    for i in range(first - 1, last):
        page = doc[i]
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        out.append((i + 1, pix.tobytes("png")))
    doc.close()
    return out


def _extract_batch(client, pages: list[tuple[int, bytes]], model: str) -> dict:
    content = []
    for page_no, png in pages:
        content.append({"type": "text", "text": f"--- PDF page {page_no} ---"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(png).decode(),
                },
            }
        )
    content.append(
        {
            "type": "text",
            "text": (
                "Transcribe each page above. Use the PDF page number shown before "
                "each image as `page_number`."
            ),
        }
    )

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": EXTRACT_SCHEMA},
        },
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to transcribe these pages.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)


_YEAR = re.compile(r"(20\d{2})")


def _normalise_period(label: str) -> str | None:
    m = _YEAR.search(str(label))
    return m.group(1) if m else None


def _scale_for(unit: str) -> tuple[float, str]:
    """Return the multiplier that converts the page's unit to whole EGP."""
    u = (unit or "").lower()
    if any(k in u for k in ("thousand", "'000", "000s", "kegp", "الف", "آلاف")):
        return 1000.0, "thousands"
    if any(k in u for k in ("million", "megp", "مليون")):
        return 1_000_000.0, "millions"
    return 1.0, "units"


TOLERANCE = 0.005  # 0.5% — absorbs the source's own rounding, not a misread digit


def check_extraction(pages: list[dict]) -> dict:
    """Cross-foot each statement against its own printed totals.

    A financial statement is a sequence of nested sections, not one flat list.
    Each total is checked against the lines since the previous total, in three
    shapes — real statements use all three:

      section total   components since the last subtotal   (Total current assets)
      running total   previous subtotal + components since (Operating profit)
      grand total     the sum of the open subtotals        (Total assets)

    The pool of open subtotals is maintained as the walk proceeds: a running
    total *consumes* the subtotal it rolled up, and a grand total closes its
    section. Without that, a balance sheet's second half is measured against
    the first half's subtotals and every line fails.

    Statements of changes in equity are skipped: they foot across columns, not
    down rows, so this rule does not apply to them.
    """
    issues, checked = [], 0

    for page in pages:
        if page.get("statement") in (None, "", "NONE", "EQUITY"):
            continue
        lines = page.get("lines", [])
        if not any(l.get("is_total") for l in lines):
            continue

        for ci, period in enumerate(page.get("column_periods", [])):
            running = 0.0            # components since the last total
            open_subtotals: list[float] = []
            last_subtotal = None

            for line in lines:
                vals = line.get("values", [])
                value = vals[ci] if ci < len(vals) else None

                if not line.get("is_total"):
                    running += value or 0.0
                    continue
                if value is None:
                    running = 0.0
                    continue

                checked += 1
                near = lambda c: abs(c - value) <= max(abs(value) * TOLERANCE, 1.0)
                section = running
                rollup = (last_subtotal or 0.0) + running
                grand = sum(open_subtotals)

                if near(section):
                    open_subtotals.append(value)
                elif near(rollup):
                    # This total absorbed the previous subtotal — replace it in
                    # the pool rather than counting both.
                    if open_subtotals:
                        open_subtotals.pop()
                    open_subtotals.append(value)
                elif near(grand):
                    # A section closed. Its parts are spent; the next section
                    # starts from an empty pool.
                    open_subtotals = []
                else:
                    best = min(
                        {
                            "components since last subtotal": section,
                            "previous subtotal + components": rollup,
                            "sum of open subtotals": grand,
                        }.items(),
                        key=lambda kv: abs(kv[1] - value),
                    )
                    issues.append(
                        {
                            "page": page.get("page_number"),
                            "statement": page.get("statement"),
                            "total_line": line.get("label"),
                            "column": period,
                            "stated_total": value,
                            "closest_check": best[0],
                            "computed": round(best[1], 2),
                            "difference": round(best[1] - value, 2),
                        }
                    )
                    open_subtotals.append(value)

                last_subtotal = value
                running = 0.0

    unreadable = [
        {"page": p.get("page_number"), "items": p["unreadable"]}
        for p in pages
        if p.get("unreadable")
    ]
    return {
        "totals_checked": checked,
        "foot_failures": issues,
        "unreadable": unreadable,
        "clean": not issues and not unreadable,
    }


def extract_fs_pdf(path: str, first: int = 1, last: int | None = None,
                   model: str | None = None) -> dict:
    """Read the PDF and return the transcription plus a check report.

    Does not write to the database — call `load_extraction` once the check
    report has been reviewed.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. The scanned FS PDF has no text layer, "
            "so it can only be read with vision."
        )
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    model = model or settings.anthropic_model

    rendered = render_pages(path, first, last)
    all_pages: list[dict] = []
    errors: list[str] = []

    for i in range(0, len(rendered), MAX_PAGES_PER_CALL):
        batch = rendered[i:i + MAX_PAGES_PER_CALL]
        try:
            result = _extract_batch(client, batch, model)
            all_pages.extend(result.get("pages", []))
        except Exception as exc:
            errors.append(
                f"pages {batch[0][0]}-{batch[-1][0]}: {type(exc).__name__}: {exc}"
            )

    with_tables = [p for p in all_pages if p.get("statement") not in (None, "", "NONE")]
    return {
        "file": path.rsplit("/", 1)[-1],
        "pages_rendered": len(rendered),
        "pages_read": len(all_pages),
        "pages_with_statements": len(with_tables),
        "statements_found": sorted({p["statement"] for p in with_tables}),
        "pages": all_pages,
        "checks": check_extraction(all_pages),
        "errors": errors,
    }


def _qualified_labels(lines: list[dict]) -> list[str]:
    """Disambiguate captions that repeat within one statement.

    A balance sheet legitimately carries 'Due from related parties' twice — once
    in non-current assets, once in current. They are different balances, so they
    cannot share a key. Where a caption repeats, it is qualified by the section
    it sits in, taken from the subtotal that closes that section.
    """
    raw = [(l.get("label") or l.get("label_arabic") or "").strip() for l in lines]

    # Section of each line = the label of the next total row beneath it.
    sections: list[str] = []
    pending: list[int] = []
    for i, line in enumerate(lines):
        pending.append(i)
        if line.get("is_total"):
            section = raw[i]
            while pending:
                sections.append(section) if len(sections) == pending.pop(0) else None
    while len(sections) < len(raw):
        sections.append("")

    counts: dict[str, int] = {}
    for label in raw:
        counts[label] = counts.get(label, 0) + 1

    out, used = [], set()
    for i, label in enumerate(raw):
        if not label or counts[label] == 1:
            out.append(label)
            continue
        section = sections[i] if i < len(sections) else ""
        qualified = f"{label} ({section})" if section and section != label else label
        # If qualifying still collides, fall back to an occurrence index rather
        # than dropping a real balance.
        n = 2
        base = qualified
        while qualified in used:
            qualified = f"{base} [{n}]"
            n += 1
        used.add(qualified)
        out.append(qualified)
    return out


def load_extraction(db: Session, extraction: dict, scope: str = "standalone",
                    confirm: bool = False) -> dict:
    """Write a reviewed extraction into FinancialLine as actuals.

    Refuses to load unless `confirm=True`, and refuses outright if the check
    report found a statement that does not foot — a balance sheet that does not
    balance is a transcription error until proven otherwise.
    """
    checks = extraction.get("checks", {})
    if checks.get("foot_failures") and not confirm:
        return {
            "loaded": False,
            "reason": "Statements did not cross-foot. Review the failures, then "
                      "re-call with confirm=true if the source itself is "
                      "inconsistent.",
            "foot_failures": checks["foot_failures"],
        }
    if not confirm:
        return {
            "loaded": False,
            "reason": "Set confirm=true to write a vision extraction into the "
                      "reporting data. Review the check report first.",
            "checks": checks,
        }

    fname = extraction.get("file", "fs-pdf")
    count, skipped = 0, []
    seen: set[tuple] = set()

    for page in extraction.get("pages", []):
        statement = page.get("statement")
        if statement not in {"IS", "BS", "CF"}:
            continue
        scale, unit_label = _scale_for(page.get("currency_unit", ""))
        periods = [_normalise_period(p) for p in page.get("column_periods", [])]

        labels = _qualified_labels(page.get("lines", []))

        for line, label in zip(page.get("lines", []), labels):
            if not label:
                continue
            for ci, period in enumerate(periods):
                if period is None or ci >= len(line.get("values", [])):
                    continue
                value = line["values"][ci]
                if value is None:
                    continue
                key = (statement, label, period, "actual", scope)
                if key in seen:
                    continue
                seen.add(key)
                existing = (
                    db.query(FinancialLine)
                    .filter(
                        FinancialLine.statement == statement,
                        FinancialLine.line_item == label,
                        FinancialLine.period == period,
                        FinancialLine.scenario == "actual",
                        FinancialLine.scope == scope,
                    )
                    .first()
                )
                if existing:
                    # The Business Plan's own actual column already covers this
                    # period; keep it and record the disagreement rather than
                    # overwriting one source with another.
                    stated = value * scale
                    if abs(existing.value * 1000 - stated) / max(abs(stated), 1) > 0.01:
                        skipped.append(
                            {
                                "line_item": label,
                                "period": period,
                                "business_plan_egp": round(existing.value * 1000, 2),
                                "audited_pdf_egp": round(stated, 2),
                                "page": page.get("page_number"),
                            }
                        )
                    continue
                db.add(
                    FinancialLine(
                        statement=statement,
                        line_item=label,
                        period=period,
                        period_type="annual",
                        scenario="actual",
                        scope=scope,
                        value=value * scale / 1000,   # stored in KEGP, as the BP is
                        currency="KEGP",
                        source_file=fname,
                    )
                )
                count += 1

    db.add(
        IngestLog(
            file_name=fname, kind="fs_pdf", as_of=date.today(),
            rows_in=extraction.get("pages_with_statements", 0), rows_out=count,
            warnings=json.dumps(checks, ensure_ascii=False)[:4000], ok=True,
        )
    )
    db.commit()
    return {
        "loaded": True,
        "lines_written": count,
        "conflicts_with_business_plan": skipped,
        "note": (
            f"{len(skipped)} figures differ from the Business Plan's actual "
            f"column. The Business Plan value was kept; review the conflicts."
            if skipped else None
        ),
    }
