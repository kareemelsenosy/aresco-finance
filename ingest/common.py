"""Shared helpers for reading the finance team's workbooks.

The source files are hand-maintained, so every parser here is written to be
tolerant of the things that actually go wrong in them: typo'd sheet names,
shifted header rows, merged cells, Arabic and English labels in the same column,
and numbers stored as text.
"""

import re
from datetime import date, datetime

_NUM_CLEAN = re.compile(r"[^\d.\-]")


def clean_str(v) -> str:
    if v is None:
        return ""
    return str(v).replace("‏", "").replace("‎", "").strip()


def to_float(v, default: float = 0.0) -> float:
    """Parse a cell that should be a number. Handles '1,234.5', '(500)', ' - '."""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = clean_str(v)
    if not s or s in {"-", "—", "N/A", "na"}:
        return default
    negative = s.startswith("(") and s.endswith(")")
    s = _NUM_CLEAN.sub("", s)
    if not s or s in {"-", ".", "-."}:
        return default
    try:
        val = float(s)
    except ValueError:
        return default
    return -val if negative else val


def to_date(v):
    """Parse a cell that should be a date. Excel gives datetimes; humans give text."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = clean_str(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


_SHEET_DATE = re.compile(r"(\d{1,2})\D{0,2}(\d{1,2})\D{0,2}(\d{2,4})")


def parse_sheet_date(name: str):
    """Parse a 'DD.MM.YYYY' worksheet name, tolerating the separator typos.

    Returns (date | None, warning | None). Year repair against neighbouring
    sheets is done by `repair_sheet_dates` — this only handles the local parse.
    """
    s = clean_str(name)
    m = _SHEET_DATE.search(s)
    if not m:
        return None, f"sheet '{name}': no date found in name"
    day, month, year = (int(g) for g in m.groups())
    warn = None
    if len(m.group(3)) == 2:
        year += 2000
    elif len(m.group(3)) == 3:
        # '206' for '2026' — a dropped character. Flag; repaired against siblings.
        warn = f"sheet '{name}': malformed year '{m.group(3)}'"
        year = None
    if day > 31 or month > 12:
        return None, f"sheet '{name}': impossible day/month {day}/{month}"
    if year is None:
        return (day, month, None), warn
    try:
        return date(year, month, day), warn
    except ValueError:
        return None, f"sheet '{name}': invalid date {day}/{month}/{year}"


_WINDOW = 5  # neighbouring sheets consulted when judging a suspect year


def _median(values: list[float]):
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def repair_sheet_dates(parsed: list):
    """Fix out-of-sequence sheet dates using workbook order.

    Daily-balance sheets are stored chronologically, so a sheet whose year
    disagrees with the sheets around it is a typo — the workbook has
    '28.08.2028' sitting between two August-2025 sheets and '11.05.2027'
    between two May-2026 sheets.

    The reference year is the *median* year of the surrounding window, not the
    single nearest neighbour: a median is unmoved by one bad value, so a typo
    can't drag its correct neighbours along with it. A real year boundary
    (31.12.2025 -> 01.01.2026) lands mid-window, where the median sits between
    the two years and no repair fires.

    `parsed` is a list of (sheet_name, value, warning) where value is either a
    date or a (day, month, None) tuple from an unrecoverable year.
    Returns (list of (sheet_name, date | None), warnings).
    """
    out, warnings = [], []
    years = [v.year if isinstance(v, date) else None for _, v, _ in parsed]

    def reference_year(idx: int):
        lo, hi = max(0, idx - _WINDOW), min(len(years), idx + _WINDOW + 1)
        window = [y for j, y in enumerate(years[lo:hi], start=lo) if y and j != idx]
        return _median(window) if len(window) >= 3 else None

    for i, (name, value, warn) in enumerate(parsed):
        if warn:
            warnings.append(warn)

        if isinstance(value, tuple):  # year unrecoverable from the name
            day, month, _ = value
            ref = reference_year(i)
            if ref is None:
                out.append((name, None))
                warnings.append(f"sheet '{name}': dropped, no reference year")
                continue
            yr = int(round(ref))
            try:
                fixed = date(yr, month, day)
            except ValueError:
                out.append((name, None))
                warnings.append(f"sheet '{name}': dropped, invalid after repair")
                continue
            warnings.append(f"sheet '{name}': year repaired to {yr} from sheet order")
            out.append((name, fixed))
            continue

        if not isinstance(value, date):
            out.append((name, None))
            continue

        ref = reference_year(i)
        if ref is not None and abs(value.year - ref) >= 1:
            yr = int(round(ref))
            try:
                fixed = date(yr, value.month, value.day)
            except ValueError:
                out.append((name, value))
                continue
            warnings.append(
                f"sheet '{name}': year {value.year} out of sequence with "
                f"surrounding sheets, read as {fixed.isoformat()}"
            )
            out.append((name, fixed))
            continue

        out.append((name, value))
    return out, warnings


def find_header_row(rows: list[list], must_contain: list[str], limit: int = 12):
    """Return the index of the first row containing all of `must_contain`.

    Case-insensitive substring match — the workbooks are inconsistent about
    capitalisation ('Usd' vs 'USD', 'Bank Name' vs 'bank name').
    """
    needles = [n.lower() for n in must_contain]
    for i, row in enumerate(rows[:limit]):
        cells = [clean_str(c).lower() for c in row]
        joined = " | ".join(cells)
        if all(n in joined for n in needles):
            return i
    return None


def sheet_rows(ws, max_col: int | None = None) -> list[list]:
    return [
        list(r)
        for r in ws.iter_rows(max_col=max_col or ws.max_column, values_only=True)
    ]


def is_total_row(label: str) -> bool:
    """Subtotal rows re-state amounts already captured, so ingest must skip them."""
    s = clean_str(label).lower()
    return (
        s.startswith("total")
        or s.endswith("total")
        or s in {"الاجمالي", "الإجمالي", "اجمالي", "المجموع"}
    )
