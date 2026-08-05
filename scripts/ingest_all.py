"""Load every workbook in Finance/ into the database.

Usage:  .venv/bin/python scripts/ingest_all.py [source_dir]

Order matters: Cash-In first, because its header carries the FX rates that the
cheque and receivable conversions need.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.database import SessionLocal, init_db  # noqa: E402
from ingest.bank_cash import ingest_bank_cash  # noqa: E402
from ingest.business_plan import ingest_business_plan  # noqa: E402
from ingest.cash_in import ingest_cash_in  # noqa: E402
from ingest.checks import ingest_checks  # noqa: E402

# (filename fragment, loader) — Cash-In first for the FX rates
PIPELINE = [
    ("cash-in", ingest_cash_in),
    ("bank cash", ingest_bank_cash),
    ("bank balance with checks", ingest_checks),
    ("bp -", ingest_business_plan),
]


def main():
    source = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent.parent.parent)
    init_db()
    db = SessionLocal()
    try:
        files = sorted(p for p in source.glob("*.xlsx") if not p.name.startswith("~"))
        if not files:
            print(f"No .xlsx files found in {source}")
            return
        for fragment, loader in PIPELINE:
            for path in files:
                if fragment in path.name.lower():
                    print(f"\n=== {path.name} -> {loader.__name__} ===")
                    try:
                        result = loader(db, str(path))
                    except Exception as exc:  # keep going; report at the end
                        print(f"  FAILED: {type(exc).__name__}: {exc}")
                        continue
                    warnings = result.pop("warnings", [])
                    for k, v in result.items():
                        print(f"  {k}: {v}")
                    if warnings:
                        print(f"  warnings ({len(warnings)}):")
                        for w in warnings[:15]:
                            print(f"    - {w}")
                        if len(warnings) > 15:
                            print(f"    ... and {len(warnings) - 15} more")
    finally:
        db.close()


if __name__ == "__main__":
    main()
