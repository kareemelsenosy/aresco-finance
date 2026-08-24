"""Bring an existing database up to the current model definitions.

SQLAlchemy's create_all() makes missing tables but never alters an existing one,
so a column added to a model after the database was created is simply absent
until something asks for it and the query fails. This adds what is missing.

    python -m scripts.migrate            # report
    python -m scripts.migrate --yes      # apply
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from api.database import SessionLocal, engine  # noqa: E402
from api.models import Base  # noqa: E402

# SQLite can only add a column with a constant default, which is all we need.
_SQL_TYPE = {"VARCHAR": "TEXT", "INTEGER": "INTEGER", "FLOAT": "REAL",
             "BOOLEAN": "INTEGER", "DATE": "DATE", "DATETIME": "DATETIME"}


def _missing(db):
    out = []
    for table in Base.metadata.sorted_tables:
        rows = db.execute(text(f'PRAGMA table_info("{table.name}")')).fetchall()
        if not rows:
            continue  # table doesn't exist yet; create_all handles that
        have = {r[1] for r in rows}
        for col in table.columns:
            if col.name in have:
                continue
            kind = _SQL_TYPE.get(str(col.type).split("(")[0].upper(), "TEXT")
            out.append((table.name, col.name, kind))
    return out


def main(commit: bool) -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        missing = _missing(db)
        if not missing:
            print("Schema is current — nothing to add.")
            return
        print(f"{'ADDING' if commit else 'WOULD ADD'} {len(missing)} column(s):")
        for table, col, kind in missing:
            print(f"  {table}.{col}  {kind}")
        if not commit:
            print("\nDry run. Re-run with --yes to apply.")
            return
        for table, col, kind in missing:
            db.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {kind}'))
        db.commit()
        print(f"\nAdded {len(missing)} column(s).")
    finally:
        db.close()


if __name__ == "__main__":
    main("--yes" in sys.argv)
