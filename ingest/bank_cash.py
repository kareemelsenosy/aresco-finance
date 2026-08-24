"""Ingest the daily bank cash workbook (`Bank Cash<year>.xlsx`).

Shape: one worksheet per business day, named `DD.MM.YYYY`. Each sheet is a
bank x currency grid:

        |  Currency
    Bank Name | EGP      | Usd  | EUR | AED
    Misr      | 79723.66 | 439  | 0   | 0
    ...
    Total     | ...

Currency columns changed over time (EUR added Jan-2026, AED later), so the
column set is read from each sheet's own header rather than assumed.
"""

from datetime import date
import openpyxl
from sqlalchemy.orm import Session

from api.models import Bank, BankBalance, IngestLog
from engine.fx import normalize_currency
from ingest.common import (
    clean_str, to_float, sheet_rows, find_header_row, is_total_row,
    parse_sheet_date, repair_sheet_dates,
)


def _bank_lookup(db: Session) -> dict[str, str]:
    """Map every known alias to its canonical bank name."""
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
    return clean_str(raw)  # unknown bank — keep the raw label, don't drop the money


def ingest_bank_cash(db: Session, path: str, replace: bool = True,
                     commit: bool = True) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    lookup = _bank_lookup(db)
    warnings: list[str] = []

    parsed = []
    for ws in wb.worksheets:
        value, warn = parse_sheet_date(ws.title)
        parsed.append((ws.title, value, warn))
    resolved, date_warnings = repair_sheet_dates(parsed)
    warnings.extend(date_warnings)

    rows_in = rows_out = 0
    dates_seen: list[date] = []

    for sheet_name, balance_date in resolved:
        if balance_date is None:
            continue
        ws = wb[sheet_name]
        rows = sheet_rows(ws)
        hdr_idx = find_header_row(rows, ["bank name"])
        if hdr_idx is None:
            warnings.append(f"sheet '{sheet_name}': no 'Bank Name' header row, skipped")
            continue

        header = rows[hdr_idx]
        # Column 0 is the bank label; every further non-empty header cell that
        # resolves to a currency becomes a value column.
        ccy_cols: list[tuple[int, str]] = []
        for ci, cell in enumerate(header[1:], start=1):
            label = clean_str(cell)
            if not label:
                continue
            ccy = normalize_currency(label)
            if ccy in {"EGP", "USD", "EUR", "AED"}:
                ccy_cols.append((ci, ccy))
        if not ccy_cols:
            warnings.append(f"sheet '{sheet_name}': no currency columns found, skipped")
            continue

        if replace:
            db.query(BankBalance).filter(
                BankBalance.balance_date == balance_date
            ).delete(synchronize_session=False)

        sheet_rows_out = 0
        for row in rows[hdr_idx + 1:]:
            label = clean_str(row[0] if row else "")
            if not label:
                continue
            if is_total_row(label):
                continue  # recomputed on read; storing it would double-count
            rows_in += 1
            bank = _canonical_bank(label, lookup)
            for ci, ccy in ccy_cols:
                if ci >= len(row):
                    continue
                amount = to_float(row[ci])
                db.add(
                    BankBalance(
                        balance_date=balance_date,
                        bank_name=bank,
                        currency=ccy,
                        amount=amount,
                        source_file=path.rsplit("/", 1)[-1],
                    )
                )
                sheet_rows_out += 1

        if sheet_rows_out:
            rows_out += sheet_rows_out
            dates_seen.append(balance_date)

    wb.close()

    log = IngestLog(
        file_name=path.rsplit("/", 1)[-1],
        kind="bank_cash",
        as_of=max(dates_seen) if dates_seen else None,
        rows_in=rows_in,
        rows_out=rows_out,
        warnings="\n".join(warnings),
        ok=rows_out > 0,
    )
    db.add(log)
    if commit:
        db.commit()
    else:
        db.flush()

    return {
        "kind": "bank_cash",
        "days_ingested": len(dates_seen),
        "date_from": min(dates_seen).isoformat() if dates_seen else None,
        "date_to": max(dates_seen).isoformat() if dates_seen else None,
        "rows": rows_out,
        "warnings": warnings,
    }
