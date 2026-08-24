"""Clear every number that came from an upload, keep the setup.

Reference data a loader needs in order to work — the bank alias table, the
cheque status map, cost categories, the ECL policy, the audit knowledge pack —
survives, and so do user logins. Everything a team uploaded, and everything
derived from it, goes.

    python -m scripts.reset_data           # show what would be cleared
    python -m scripts.reset_data --yes     # actually clear it
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.database import SessionLocal  # noqa: E402

# Uploaded, or derived from an upload. All of this goes.
CLEARED = [
    "bank_balances", "checks", "receivables", "cash_in_forecast",
    "collection_forecasts", "expense_forecast", "manual_cash_flows",
    "financial_lines", "macro_assumptions", "project_periods", "projects",
    "project_costs", "customers", "fx_rates", "trade_finance", "down_payments",
    "tax_obligations", "purchase_requisitions", "purchase_orders",
    "ecl_results", "ecl_runs", "audit_queries", "audit_engagements",
    "ingest_log", "email_verifications",
]

# Setup and reference. Kept — the loaders read these to do their job.
KEPT = ["users", "banks", "check_status_map", "cost_categories",
        "ecl_policy", "knowledge_docs", "tax_evaders"]


def main(commit: bool) -> None:
    db = SessionLocal()
    try:
        counts = {}
        for table in CLEARED:
            counts[table] = db.execute(
                __import__("sqlalchemy").text(f'SELECT COUNT(*) FROM "{table}"')
            ).scalar()

        total = sum(counts.values())
        print(f"{'CLEARING' if commit else 'WOULD CLEAR'} — {total} rows\n")
        for table, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            if n:
                print(f"  {n:>8}  {table}")

        print("\nKEEPING")
        for table in KEPT:
            n = db.execute(
                __import__("sqlalchemy").text(f'SELECT COUNT(*) FROM "{table}"')
            ).scalar()
            print(f"  {n:>8}  {table}")

        if not commit:
            print("\nDry run. Re-run with --yes to apply.")
            return

        from sqlalchemy import text

        db.execute(text("PRAGMA foreign_keys = OFF"))
        for table in CLEARED:
            db.execute(text(f'DELETE FROM "{table}"'))
        # sqlite_sequence exists only when a table uses AUTOINCREMENT; these
        # models use plain INTEGER PRIMARY KEY, so it is absent. Reset it only
        # if it is there, otherwise the whole clear rolls back on a missing table.
        exists = db.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        )).scalar()
        if exists:
            db.execute(text("DELETE FROM sqlite_sequence"))
        db.execute(text("PRAGMA foreign_keys = ON"))
        db.commit()
        db.close()
        # VACUUM cannot run inside a transaction, so it needs its own connection.
        from api.database import engine
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("VACUUM"))
        print(f"\nCleared {total} rows. Setup and logins untouched.")
    finally:
        db.close()


if __name__ == "__main__":
    main("--yes" in sys.argv)
