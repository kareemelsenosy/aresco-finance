"""Ingest the issued-cheque register (`Bank Balance With Checks List-<date>.xlsx`).

One worksheet per currency block (EGY, USD). Layout:

    row 1 |            | <snapshot date> |        |     | Misr | AIB | CIB | ...
    row 2 |            |                 |        | bank balance
    row 3 | Status | Currency | Value | Supplier Name | Check Date | Check Number |
          | Bank | s.n | <one column per bank>
    row 4+| data

The per-bank columns to the right restate `Value` under whichever bank the
cheque is drawn on, so they are read only as a cross-check on `Bank`.
"""

from datetime import date
import openpyxl
from sqlalchemy.orm import Session

from api.models import Bank, Check, CheckStatusMap, IngestLog
from engine.fx import normalize_currency
from ingest.common import (
    clean_str, to_float, to_date, sheet_rows, find_header_row, is_total_row,
)


def _status_map(db: Session) -> dict[str, bool]:
    return {
        clean_str(s.raw_status): s.delivered
        for s in db.query(CheckStatusMap).all()
    }


def _bank_names(db: Session) -> dict[str, str]:
    lookup = {}
    for b in db.query(Bank).all():
        lookup[b.name.lower()] = b.name
        for alias in (b.aliases or "").split(","):
            alias = alias.strip().lower()
            if alias:
                lookup[alias] = b.name
    return lookup


def _canonical_bank(raw: str, lookup: dict[str, str]) -> str:
    key = clean_str(raw).lower()
    if not key:
        return ""
    if key in lookup:
        return lookup[key]
    for alias, name in lookup.items():
        if alias and (alias in key or key in alias):
            return name
    return clean_str(raw)


def _column_index(header: list, *names: str):
    """Locate a column by any of its known header spellings."""
    for ci, cell in enumerate(header):
        label = clean_str(cell).lower()
        if not label:
            continue
        for n in names:
            if n.lower() == label or n.lower() in label:
                return ci
    return None


def ingest_checks(db: Session, path: str, replace: bool = True) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    statuses = _status_map(db)
    banks = _bank_names(db)
    warnings: list[str] = []
    unknown_statuses: set[str] = set()

    rows_in = rows_out = 0
    snapshots: list[date] = []
    staged: list[Check] = []

    for ws in wb.worksheets:
        rows = sheet_rows(ws)
        if not rows:
            continue
        hdr_idx = find_header_row(rows, ["status", "check number"], limit=15)
        if hdr_idx is None:
            warnings.append(f"sheet '{ws.title}': no cheque header row, skipped")
            continue
        header = rows[hdr_idx]

        col_status = _column_index(header, "status")
        col_ccy = _column_index(header, "currency")
        col_value = _column_index(header, "value")
        col_supplier = _column_index(header, "supplier name", "supplier")
        col_cdate = _column_index(header, "check date", "cheque date")
        col_cnum = _column_index(header, "check number", "cheque number")
        col_bank = _column_index(header, "bank")
        col_sn = _column_index(header, "s.n", "sn")

        if col_value is None or col_cnum is None:
            warnings.append(f"sheet '{ws.title}': missing Value/Check Number, skipped")
            continue

        # The snapshot date sits in the rows above the header — take the first
        # real date found there.
        snapshot = None
        for r in rows[:hdr_idx]:
            for cell in r:
                d = to_date(cell)
                if d:
                    snapshot = d
                    break
            if snapshot:
                break
        if snapshot is None:
            warnings.append(
                f"sheet '{ws.title}': no snapshot date in header block; "
                f"using latest cheque date instead"
            )

        sheet_dates: list[date] = []
        sheet_checks: list[Check] = []

        for row in rows[hdr_idx + 1:]:
            if not row or all(c is None for c in row):
                continue
            label = clean_str(row[col_supplier]) if col_supplier is not None and col_supplier < len(row) else ""
            status_raw = clean_str(row[col_status]) if col_status is not None and col_status < len(row) else ""
            if is_total_row(label) or is_total_row(status_raw):
                continue
            value = to_float(row[col_value]) if col_value < len(row) else 0.0
            cnum = clean_str(row[col_cnum]) if col_cnum < len(row) else ""
            if not value and not cnum:
                continue
            rows_in += 1

            known = status_raw in statuses
            if status_raw and not known:
                unknown_statuses.add(status_raw)

            cdate = to_date(row[col_cdate]) if col_cdate is not None and col_cdate < len(row) else None
            if cdate:
                sheet_dates.append(cdate)

            ccy_raw = row[col_ccy] if col_ccy is not None and col_ccy < len(row) else None
            currency = normalize_currency(ccy_raw)
            # The USD tab's Currency column is sometimes blank; fall back to the
            # sheet name, which is the currency block.
            if currency == "EGP" and "usd" in ws.title.lower():
                currency = "USD"

            bank_raw = clean_str(row[col_bank]) if col_bank is not None and col_bank < len(row) else ""

            sheet_checks.append(
                Check(
                    snapshot_date=snapshot,  # patched below if snapshot is None
                    check_number=cnum,
                    supplier_name=label,
                    bank_name=_canonical_bank(bank_raw, banks),
                    currency=currency,
                    value=value,
                    check_date=cdate,
                    raw_status=status_raw,
                    delivered=statuses.get(status_raw, False),
                    status_known=known or not status_raw,
                    serial_no=clean_str(row[col_sn]) if col_sn is not None and col_sn < len(row) else "",
                    source_file=path.rsplit("/", 1)[-1],
                )
            )

        if snapshot is None:
            snapshot = max(sheet_dates) if sheet_dates else date.today()

        if sheet_checks:
            snapshots.append(snapshot)
            staged.extend(sheet_checks)

    wb.close()

    # The currency tabs are one register split by currency, but their header
    # dates drift by a day or two because they are refreshed separately. Stamp
    # every cheque in the file with the file's latest date — otherwise the
    # trailing tab's cheques fall outside the current snapshot and silently
    # drop out of the cash position.
    if snapshots:
        file_snapshot = max(snapshots)
        for c in staged:
            c.snapshot_date = file_snapshot
        if len(set(snapshots)) > 1:
            warnings.append(
                "Worksheet header dates differ ("
                + ", ".join(sorted({s.isoformat() for s in snapshots}))
                + f") — all cheques recorded as at {file_snapshot.isoformat()}."
            )
        snapshots = [file_snapshot]

    if replace and snapshots:
        # Clear by source file as well as by date: a re-ingest after the
        # snapshot date was corrected would otherwise leave the old rows behind
        # under their previous date, double-counting the register.
        fname = path.rsplit("/", 1)[-1]
        db.query(Check).filter(
            (Check.snapshot_date == snapshots[0]) | (Check.source_file == fname)
        ).delete(synchronize_session=False)
    for c in staged:
        db.add(c)
        rows_out += 1

    if unknown_statuses:
        warnings.append(
            "Unmapped cheque statuses (treated as NOT delivered until classified "
            "via PUT /checks/status-map): " + ", ".join(sorted(unknown_statuses))
        )

    db.add(
        IngestLog(
            file_name=path.rsplit("/", 1)[-1],
            kind="checks",
            as_of=max(snapshots) if snapshots else None,
            rows_in=rows_in,
            rows_out=rows_out,
            warnings="\n".join(warnings),
            ok=rows_out > 0,
        )
    )
    db.commit()

    return {
        "kind": "checks",
        "snapshot_date": max(snapshots).isoformat() if snapshots else None,
        "checks_ingested": rows_out,
        "unknown_statuses": sorted(unknown_statuses),
        "warnings": warnings,
    }
