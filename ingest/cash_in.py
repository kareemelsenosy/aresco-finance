"""Ingest the Cash-In workbook (`Cash-In <date>.xlsx`).

Four things live in this file:

  'Cash-In'                     open AR + expected inflows by category, with the
                                FX rates for the day in the header row
  'Expenses Forecast'           planned outflows (payroll, taxes, rents)
  'مديونيات العملاء المصنع'      factory AR aging (1m / 2m / 3m+ buckets)
  'مديونيات العملاء المشروعات'   projects AR aging
  'ارصدة عملاء مصنع متوقفة'      dormant/stopped customer balances

Categories in the 'Cash-In' sheet are Factory / Projects / Dormant / Forecast.
'Forecast' rows are expected but not yet invoiced, so they go to CashInForecast
rather than Receivable — mixing them would overstate AR and distort RDOH.
"""

from datetime import date
import openpyxl
from sqlalchemy.orm import Session

from api.models import (
    CashInForecast, Customer, ExpenseForecast, IngestLog, Receivable,
)
from engine.fx import FXConverter, normalize_currency, upsert_rate
from ingest.common import (
    clean_str, to_float, to_date, sheet_rows, find_header_row, is_total_row,
)

# Aging buckets, in the order the Arabic sheets lay them out
BUCKETS = ["current", "1m", "2m", "3m+"]
_BUCKET_HEADERS = {
    "حديث الاصدار": "current",
    "شهر": "1m",
    "شهرين": "2m",
    "اكثر من ثلاث شهور": "3m+",
}


def _sheet_by_keyword(wb, *keywords):
    for ws in wb.worksheets:
        title = clean_str(ws.title).lower()
        if all(k.lower() in title for k in keywords):
            return ws
    return None


def _read_rates(rows: list[list]) -> dict[str, float]:
    """The Cash-In header row carries the day's USD and EUR rates.

    Shape: `... | Rates | 51.03 | 58.22 | | <date>`. The two numbers are USD
    then EUR, matching the column order of the table below.
    """
    for row in rows[:4]:
        labels = [clean_str(c).lower() for c in row]
        if any("rate" in l for l in labels):
            nums = [to_float(c) for c in row if to_float(c) > 1.0]
            # Guard against the date serial sneaking in as a "rate"
            nums = [n for n in nums if n < 1000]
            if len(nums) >= 2:
                return {"USD": nums[0], "EUR": nums[1]}
            if len(nums) == 1:
                return {"USD": nums[0]}
    return {}


def _ingest_cash_in_sheet(db: Session, ws, snapshot: date, fname: str, warnings: list):
    rows = sheet_rows(ws)
    rates = _read_rates(rows)
    for ccy, rate in rates.items():
        upsert_rate(db, snapshot, ccy, rate, source="cash-in")
    db.flush()
    fx = FXConverter(db, snapshot)

    hdr_idx = find_header_row(rows, ["category", "project name"], limit=6)
    if hdr_idx is None:
        warnings.append("Cash-In sheet: no 'Category / Project Name' header, skipped")
        return 0, 0

    header = rows[hdr_idx]

    def col(*names):
        for ci, cell in enumerate(header):
            label = clean_str(cell).lower()
            for n in names:
                if n.lower() == label:
                    return ci
        for ci, cell in enumerate(header):
            label = clean_str(cell).lower()
            for n in names:
                if label and n.lower() in label:
                    return ci
        return None

    c_cat = col("category")
    c_name = col("project name", "customer")
    c_egp = col("egp")
    c_usd = col("usd")
    c_eur = col("euro", "eur")
    c_total = col("total egp")
    c_inv = col("invoice date")

    ar_rows = fc_rows = 0
    for row in rows[hdr_idx + 1:]:
        if not row or all(c is None for c in row):
            continue
        name = clean_str(row[c_name]) if c_name is not None and c_name < len(row) else ""
        category = clean_str(row[c_cat]) if c_cat is not None and c_cat < len(row) else ""
        if not name or is_total_row(name):
            continue

        inv_date = to_date(row[c_inv]) if c_inv is not None and c_inv < len(row) else None
        total_egp_stated = to_float(row[c_total]) if c_total is not None and c_total < len(row) else 0.0

        # One source row can carry amounts in several currencies; each becomes
        # its own record so the multi-currency views stay exact.
        parts = []
        for ci, ccy in ((c_egp, "EGP"), (c_usd, "USD"), (c_eur, "EUR")):
            if ci is None or ci >= len(row):
                continue
            amt = to_float(row[ci])
            if amt:
                parts.append((ccy, amt))
        if not parts:
            if total_egp_stated:
                parts = [("EGP", total_egp_stated)]
            else:
                continue

        is_forecast = "forecast" in category.lower()
        for ccy, amt in parts:
            amt_egp = fx.to_egp(amt, ccy, snapshot)
            # Trust the workbook's own EGP total when it's the only line — the
            # finance team sometimes uses a contract rate, not the header rate.
            if len(parts) == 1 and total_egp_stated:
                amt_egp = total_egp_stated

            if is_forecast:
                db.add(
                    CashInForecast(
                        snapshot_date=snapshot,
                        project_name=name,
                        category=category or "Forecast",
                        currency=ccy,
                        amount=amt,
                        amount_egp=amt_egp,
                        expected_date=inv_date,
                        source_file=fname,
                    )
                )
                fc_rows += 1
            else:
                db.add(
                    Receivable(
                        snapshot_date=snapshot,
                        customer_name=name,
                        category=category or "Factory",
                        currency=ccy,
                        amount=amt,
                        amount_egp=amt_egp,
                        invoice_date=inv_date,
                        aging_bucket="dormant" if "dormant" in category.lower() else "",
                        source_file=fname,
                    )
                )
                ar_rows += 1
    return ar_rows, fc_rows


def _ingest_aging_sheet(db: Session, ws, snapshot: date, category: str, warnings: list):
    """Read an Arabic aging sheet to attach bucket labels to the AR rows."""
    rows = sheet_rows(ws)
    hdr_idx = None
    for i, row in enumerate(rows[:6]):
        cells = [clean_str(c) for c in row]
        if any(c in _BUCKET_HEADERS for c in cells):
            hdr_idx = i
            break
    if hdr_idx is None:
        return {}

    header = [clean_str(c) for c in rows[hdr_idx]]
    bucket_cols = {ci: _BUCKET_HEADERS[h] for ci, h in enumerate(header) if h in _BUCKET_HEADERS}
    name_col = next(
        (ci for ci, h in enumerate(header) if h in {"العميل", "customer"}), 1
    )

    buckets: dict[str, str] = {}
    for row in rows[hdr_idx + 1:]:
        if not row or name_col >= len(row):
            continue
        name = clean_str(row[name_col])
        if not name or is_total_row(name):
            continue
        # The bucket a customer's balance sits in is the column carrying a value.
        best, best_amt = "", 0.0
        for ci, bucket in bucket_cols.items():
            if ci >= len(row):
                continue
            amt = abs(to_float(row[ci]))
            if amt > best_amt:
                best, best_amt = bucket, amt
        if best:
            buckets[name] = best
    return buckets


def _ingest_expenses(db: Session, ws, snapshot: date, fname: str, replace: bool):
    rows = sheet_rows(ws)
    if replace:
        db.query(ExpenseForecast).filter(
            ExpenseForecast.snapshot_date == snapshot
        ).delete(synchronize_session=False)

    hdr_idx = find_header_row(rows, ["monthly"], limit=6)
    start = (hdr_idx + 1) if hdr_idx is not None else 0
    header = rows[hdr_idx] if hdr_idx is not None else []

    def col(name, default):
        for ci, cell in enumerate(header):
            if name.lower() in clean_str(cell).lower():
                return ci
        return default

    c_m, c_q, c_a = col("monthly", 2), col("qtr", 3), col("anu", 4)

    count = 0
    for row in rows[start:]:
        if not row:
            continue
        label = ""
        for cell in row[:3]:
            s = clean_str(cell)
            if s and to_float(cell, default=None) is None:
                label = s
                break
        if not label or is_total_row(label):
            continue
        monthly = to_float(row[c_m]) if c_m < len(row) else 0.0
        quarterly = to_float(row[c_q]) if c_q < len(row) else 0.0
        annual = to_float(row[c_a]) if c_a < len(row) else 0.0
        if not (monthly or quarterly or annual):
            # Keep zero-value lines: they are the team's placeholder for costs
            # they know exist but haven't quantified, and hiding them hides a gap.
            pass
        db.add(
            ExpenseForecast(
                snapshot_date=snapshot,
                category=label,
                monthly=monthly,
                quarterly=quarterly,
                annual=annual,
                source_file=fname,
            )
        )
        count += 1
    return count


def ingest_cash_in(db: Session, path: str, replace: bool = True,
                   commit: bool = True) -> dict:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    fname = path.rsplit("/", 1)[-1]
    warnings: list[str] = []

    cash_ws = _sheet_by_keyword(wb, "cash-in") or _sheet_by_keyword(wb, "cash in")
    if cash_ws is None:
        wb.close()
        raise ValueError("No 'Cash-In' worksheet found in this workbook")

    # Snapshot date: the header row of the Cash-In sheet carries it.
    rows = sheet_rows(cash_ws)
    snapshot = None
    for r in rows[:4]:
        for cell in r:
            d = to_date(cell)
            if d and d.year > 2000:
                snapshot = d
                break
        if snapshot:
            break
    if snapshot is None:
        snapshot = date.today()
        warnings.append("No snapshot date in Cash-In header; used today's date")

    if replace:
        db.query(Receivable).filter(Receivable.snapshot_date == snapshot).delete(
            synchronize_session=False
        )
        db.query(CashInForecast).filter(
            CashInForecast.snapshot_date == snapshot
        ).delete(synchronize_session=False)

    ar_rows, fc_rows = _ingest_cash_in_sheet(db, cash_ws, snapshot, fname, warnings)

    # Aging buckets from the Arabic detail sheets
    buckets: dict[str, str] = {}
    for keywords, cat in (
        (("مديونيات", "المصنع"), "Factory"),
        (("مديونيات", "المشروعات"), "Projects"),
    ):
        ws = _sheet_by_keyword(wb, *keywords)
        if ws is not None:
            buckets.update(_ingest_aging_sheet(db, ws, snapshot, cat, warnings))

    db.flush()
    tagged = 0
    if buckets:
        for r in db.query(Receivable).filter(Receivable.snapshot_date == snapshot).all():
            if r.aging_bucket:
                continue
            b = buckets.get(r.customer_name)
            if b:
                r.aging_bucket = b
                tagged += 1

    exp_ws = _sheet_by_keyword(wb, "expenses forecast") or _sheet_by_keyword(wb, "expense")
    exp_rows = _ingest_expenses(db, exp_ws, snapshot, fname, replace) if exp_ws else 0

    # Register any customer we haven't seen before, so payment terms can be set.
    known = {c.name for c in db.query(Customer).all()}
    new_customers = 0
    for r in db.query(Receivable).filter(Receivable.snapshot_date == snapshot).all():
        if r.customer_name not in known:
            db.add(Customer(name=r.customer_name, category=r.category))
            known.add(r.customer_name)
            new_customers += 1

    wb.close()
    db.add(
        IngestLog(
            file_name=fname,
            kind="cash_in",
            as_of=snapshot,
            rows_in=ar_rows + fc_rows,
            rows_out=ar_rows + fc_rows + exp_rows,
            warnings="\n".join(warnings),
            ok=True,
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()

    return {
        "kind": "cash_in",
        "snapshot_date": snapshot.isoformat(),
        "receivables": ar_rows,
        "forecast_inflows": fc_rows,
        "expense_lines": exp_rows,
        "aging_buckets_tagged": tagged,
        "new_customers": new_customers,
        "warnings": warnings,
    }
