"""
The data-intake register.

One entry per thing a team owes the tool, taken from the agreed data-request
table. Each entry says who owns it, how often it is due, and — the part that
matters here — how an uploaded file is actually turned into data:

  workbook:<kind>   one of the four existing workbook loaders
  register:<name>   one of the five existing CSV registers
  fs_pdf            the scanned financial-statements vision path
  ai:<target>       no dedicated loader — the AI column-mapper handles it
  in_tool           not an upload; done on a screen in the tool
  document          filed as-is for the record, nothing parsed

Nothing here re-implements an existing loader. Where one exists it is used
directly, and the AI mapper is only the fallback for when a team's own export
does not carry the column names the loader expects.
"""

from __future__ import annotations

CORE, HIGHEST, HIGH, MEDIUM, SETUP = "core", "highest", "high", "medium", "setup"

REQUIREMENTS = [
    {
        "id": "bank_balances", "no": 1, "team": "Treasury",
        "need": "Daily bank balances, all accounts & currencies",
        "format": "Bank Cash workbook (no format change)",
        "frequency": "Daily, by 10:00", "due_days": 1, "priority": CORE,
        "handler": "workbook:bank_cash", "accepts": ".xlsx,.xlsm",
    },
    {
        "id": "cheques", "no": 2, "team": "Treasury",
        "need": "Issued cheque list",
        "format": "Bank Balance With Checks List workbook",
        "frequency": "Daily / on change", "due_days": 1, "priority": CORE,
        "handler": "workbook:checks", "accepts": ".xlsx,.xlsm",
    },
    {
        "id": "ar_position", "no": 3, "team": "Receivables",
        "need": "Open AR position",
        "format": "Cash-In workbook",
        "frequency": "Weekly (daily in a tight month)", "due_days": 7, "priority": CORE,
        "handler": "workbook:cash_in", "accepts": ".xlsx,.xlsm",
    },
    {
        "id": "collection_review", "no": 4, "team": "Receivables",
        "need": "Review of expected collection dates",
        "format": "Directly in the tool",
        "frequency": "Weekly", "due_days": 7, "priority": HIGH,
        "handler": "in_tool", "goto": "receivables",
    },
    {
        "id": "ar_subledger", "no": 5, "team": "Receivables",
        "need": ("Full AR sub-ledger — aged by due date, reconciled to the control "
                 "account, split into trade / contract assets / retentions"),
        "format": "Sub-ledger export (CSV or Excel)",
        "frequency": "One-off, then on update", "due_days": None, "priority": HIGHEST,
        "handler": "ai:receivables", "accepts": ".csv,.xlsx,.xlsm",
    },
    {
        "id": "customer_terms", "no": 6, "team": "Receivables",
        "need": "Customer payment terms & typical days late",
        "format": "One-off list (CSV or Excel)",
        "frequency": "Once, then on change", "due_days": None, "priority": SETUP,
        "handler": "ai:customers", "accepts": ".csv,.xlsx,.xlsm",
    },
    {
        "id": "trade_finance", "no": 7, "team": "Trade Finance",
        "need": "Every L/G & L/C (both directions)",
        "format": "CSV register (19 columns)",
        "frequency": "On every new / renewed facility", "due_days": 30, "priority": HIGH,
        "handler": "register:trade-finance", "accepts": ".csv,.xlsx,.xlsm",
    },
    {
        "id": "procurement", "no": 8, "team": "Procurement",
        "need": "Every PR and PO, plus status updates",
        "format": "CSV (PR + PO templates)",
        "frequency": "On every PR/PO and each status change", "due_days": 30, "priority": HIGH,
        "handler": "register:requisitions", "alt_handler": "register:orders",
        "accepts": ".csv,.xlsx,.xlsm",
    },
    {
        "id": "tax", "no": 9, "team": "Tax & Compliance",
        "need": "Tax & governmental obligations + settlement dates",
        "format": "CSV (11 columns)",
        "frequency": "Monthly + on every assessment", "due_days": 31, "priority": HIGHEST,
        "handler": "register:tax", "accepts": ".csv,.xlsx,.xlsm",
    },
    {
        "id": "contracts_advances", "no": 10, "team": "Commercial",
        "need": "Contracts & advances, each linked to its APG",
        "format": "CSV (13 columns)",
        "frequency": "On every contract / advance", "due_days": 30, "priority": HIGH,
        "handler": "register:down-payments", "accepts": ".csv,.xlsx,.xlsm",
    },
    {
        "id": "project_costs", "no": 11, "team": "Costing",
        "need": "Per-project costs tagged by category",
        "format": "Monthly export (CSV or Excel)",
        "frequency": "Monthly", "due_days": 31, "priority": HIGH,
        "handler": "ai:project_costs", "accepts": ".csv,.xlsx,.xlsm",
    },
    {
        "id": "cost_split", "no": 12, "team": "Costing",
        "need": "Confirm variable / fixed split (incl. direct-labour %)",
        "format": "Confirmation from the Financial Controller",
        "frequency": "One-off", "due_days": None, "priority": SETUP,
        "handler": "document", "accepts": ".pdf,.docx,.xlsx,.csv,.md,.txt",
    },
    {
        "id": "business_plan", "no": 13, "team": "FP&A",
        "need": "Updated Business Plan workbook",
        "format": "Workbook",
        "frequency": "On each revision", "due_days": 90, "priority": CORE,
        "handler": "workbook:business_plan", "accepts": ".xlsx,.xlsm",
    },
    {
        "id": "consolidated", "no": 14, "team": "FP&A",
        "need": "Consolidated figures (the plan is standalone only)",
        "format": "Workbook / export",
        "frequency": "Quarterly", "due_days": 92, "priority": MEDIUM,
        "handler": "ai:financial_lines", "accepts": ".csv,.xlsx,.xlsm",
    },
    {
        "id": "management_accounts", "no": 15, "team": "FP&A",
        "need": "Management accounts",
        "format": "Workbook",
        "frequency": "Monthly if available", "due_days": 31, "priority": MEDIUM,
        "handler": "ai:financial_lines", "accepts": ".csv,.xlsx,.xlsm",
    },
    {
        "id": "signed_fs", "no": 16, "team": "Reporting",
        "need": "Signed financial statements",
        "format": "PDF / workbook",
        "frequency": "On issue (annually)", "due_days": 365, "priority": CORE,
        "handler": "fs_pdf", "accepts": ".pdf",
    },
]

BY_ID = {r["id"]: r for r in REQUIREMENTS}

TEAM_ORDER = ["Treasury", "Receivables", "Trade Finance", "Procurement",
              "Tax & Compliance", "Commercial", "Costing", "FP&A", "Reporting"]

# The `kind` written to IngestLog, so "when did this last arrive?" can be
# answered from the existing log rather than a second table.
def log_kind(req_id: str) -> str:
    return f"intake:{req_id}"
