"""
Make an upload readable before anything tries to parse it.

A file's extension is a claim, not a fact. Treasury's cheque register arrives as
`cheque 23-8.XLS` and is actually a UTF-16 tab-separated ERP dump; other exports
arrive as `.xlsx` and are CSV. openpyxl refuses all of them, so the upload dies
at the door with a message about the extension rather than the content.

This reads the magic bytes, works out what the file really is, and — where it
can — rewrites it as a real .xlsx that the existing loaders can open. Nothing
about the *data* is interpreted here; that is the repair layer's job. This only
makes the bytes openable.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

ZIP_MAGIC = b"PK\x03\x04"          # xlsx/xlsm are zip archives
OLE2_MAGIC = b"\xd0\xcf\x11\xe0"   # genuine legacy .xls (Excel 97-2003)

# Enough of the file to sniff a delimiter without reading a 30 MB export.
_SNIFF_BYTES = 16000


class UnreadableUpload(ValueError):
    """The file is not something we can turn into a workbook."""


def detect(raw: bytes) -> str:
    """What this file actually is: xlsx | xls_legacy | text | unknown."""
    if raw.startswith(ZIP_MAGIC):
        return "xlsx"
    if raw.startswith(OLE2_MAGIC):
        return "xls_legacy"
    for encoding in ("utf-16", "utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            sample = raw[:_SNIFF_BYTES].decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # A delimited export has a separator on most of its lines; prose doesn't.
        lines = [l for l in sample.splitlines() if l.strip()][:40]
        if len(lines) >= 2 and any(
            sum(1 for l in lines if d in l) >= max(2, len(lines) // 2)
            for d in ("\t", ",", ";", "|")
        ):
            return "text"
    return "unknown"


def _decode(raw: bytes) -> str:
    for encoding in ("utf-16", "utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # UTF-16 misread as a single-byte encoding leaves NULs everywhere.
        if text.count("\x00") > len(text) // 10:
            continue
        return text
    return raw.decode("utf-8", errors="replace")


def _delimiter(text: str) -> str:
    head = "\n".join([l for l in text.splitlines() if l.strip()][:40])
    try:
        return csv.Sniffer().sniff(head, delimiters="\t,;|").delimiter
    except csv.Error:
        # Sniffer gives up on ragged ERP dumps; the most frequent candidate wins.
        return max("\t,;|", key=head.count)


def _text_to_xlsx(raw: bytes, dest: Path) -> list[str]:
    import openpyxl

    text = _decode(raw)
    delim = _delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    written = 0
    for row in rows:
        if not any((c or "").strip() for c in row):
            continue
        # Numbers arrive as text in an ERP dump; leave them as text and let the
        # loaders' own to_float handle it — guessing types here would silently
        # reinterpret an account number as a float.
        ws.append([(c or "").strip() for c in row])
        written += 1
    if not written:
        raise UnreadableUpload("The file decoded to no usable rows.")
    wb.save(dest)

    name = {"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}.get(delim, delim)
    return [f"Read as {name}-separated text, not a workbook, "
            f"and converted to .xlsx ({written} rows)."]


def _legacy_xls_to_xlsx(path: Path, dest: Path) -> list[str]:
    """Genuine Excel 97-2003. Needs xlrd<2, which is not a hard dependency."""
    try:
        import xlrd  # noqa: F401
    except ImportError as exc:
        raise UnreadableUpload(
            "This is a genuine Excel 97-2003 (.xls) file. Open it in Excel and "
            "'Save As' .xlsx, or add `xlrd<2` to requirements.txt to read it here."
        ) from exc

    import openpyxl
    import xlrd

    book = xlrd.open_workbook(str(path))
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet in book.sheets():
        ws = wb.create_sheet(sheet.name[:31])
        for r in range(sheet.nrows):
            ws.append(list(sheet.row_values(r)))
    wb.save(dest)
    return [f"Converted Excel 97-2003 .xls to .xlsx ({book.nsheets} sheet(s))."]


def normalize(path: str | Path, raw: bytes | None = None) -> tuple[Path, list[str]]:
    """Return (path_to_a_real_workbook, notes).

    The returned path is the original when it was already a workbook, and a
    converted sibling otherwise. `notes` is empty when nothing had to be done.
    """
    path = Path(path)
    raw = raw if raw is not None else path.read_bytes()
    if not raw:
        raise UnreadableUpload("The file is empty.")

    kind = detect(raw)
    if kind == "xlsx":
        return path, []

    dest = path.with_name(path.stem + "__converted.xlsx")
    if kind == "text":
        return dest, _text_to_xlsx(raw, dest)
    if kind == "xls_legacy":
        return dest, _legacy_xls_to_xlsx(path, dest)

    raise UnreadableUpload(
        f"'{path.name}' is neither a workbook nor a delimited text export — "
        f"the first bytes are {raw[:8]!r}."
    )
