"""
ARESCO Finance — data model.

Mirrors the shape of the source workbooks the finance team already produces, so
ingestion is a straight mapping rather than a re-keying exercise:

  Bank Cash<year>.xlsx              -> BankBalance      (one sheet per date, bank x currency)
  Bank Balance With Checks List.xlsx-> Check            (issued cheques, EGY + USD tabs)
  Cash-In <date>.xlsx               -> Receivable / CashInForecast / ExpenseForecast
  Aresco BP - 2026-2030.xlsx        -> FinancialLine / ProjectRevenue / MacroAssumption
"""

from datetime import date, datetime
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship
from api.database import Base


class User(Base):
    """An ARESCO staff account. Registration is limited to the allowed domain.

    The finance figures are company-wide, so an account is an access grant —
    there is no per-user slice of the data.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(200), unique=True, nullable=False, index=True)
    full_name = Column(String(200), default="")
    password_hash = Column(String(255))  # null until the first password is set
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)

    email_verified_at = Column(DateTime)
    last_login_at = Column(DateTime)
    failed_logins = Column(Integer, default=0)
    locked_until = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def display_name(self) -> str:
        return self.full_name or self.email.split("@")[0].replace(".", " ").title()

    @property
    def initials(self) -> str:
        parts = [p for p in self.display_name.replace(".", " ").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "?"


class EmailVerification(Base):
    """A pending six-digit sign-up code. One live row per address."""

    __tablename__ = "email_verifications"

    id = Column(Integer, primary_key=True)
    email = Column(String(200), nullable=False, index=True)
    code_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, default=0)
    consumed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


# --- Reference data seeded on first run -------------------------------------

# (canonical name, comma-separated aliases as they appear across the workbooks)
DEFAULT_BANKS = [
    ("Banque Misr", "misr,bank misr,بنك مصر"),
    ("AIB", "aib,arab investment bank"),
    ("CIB", "cib,commercial international bank"),
    ("HSBC", "hsbc"),
    ("BDC", "bdc,banque du caire"),
    ("FABMISR", "fabmisr,fab misr,fab,first abu dhabi"),
    ("ADIB", "adib,abu dhabi islamic"),
    ("NBE", "nbe,national bank of egypt,الاهلي"),
    ("AAIB Dubai", "aaib dubai,aaib,arab african"),
]

# (raw status as typed by the treasury team, delivered?, english label)
# `delivered=True`  -> cheque is in the vendor's hands: a real outstanding claim
# `delivered=False` -> cheque printed/signed but still on hand in treasury
DEFAULT_CHECK_STATUSES = [
    ("جاهز للتسليم", False, "Ready for delivery (on hand)"),
    ("تم التسليم", True, "Delivered to vendor"),
    ("الكورنيش", True, "Sent to Corniche office"),
    ("تمويل خزنة", False, "Treasury funding"),
    ("تحت التحصيل", True, "Presented / under collection"),
]


class Bank(Base):
    __tablename__ = "banks"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    aliases = Column(String, default="")
    active = Column(Boolean, default=True)


class CheckStatusMap(Base):
    """Maps the free-text status the treasury team types onto delivered/not-delivered.

    Unmapped statuses surface in the UI so finance can classify them rather than
    being silently bucketed either way.
    """
    __tablename__ = "check_status_map"
    id = Column(Integer, primary_key=True)
    raw_status = Column(String, unique=True, nullable=False)
    delivered = Column(Boolean, nullable=False, default=False)
    english_label = Column(String, default="")


class FXRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("rate_date", "currency", name="uq_fx_date_ccy"),)
    id = Column(Integer, primary_key=True)
    rate_date = Column(Date, nullable=False, index=True)
    currency = Column(String, nullable=False)      # USD, EUR, AED
    rate_to_egp = Column(Float, nullable=False)
    source = Column(String, default="manual")      # manual | cash-in | business-plan


# --- Cash management ---------------------------------------------------------

class BankBalance(Base):
    """One row per (date, bank, currency) — from the daily Bank Cash workbook."""
    __tablename__ = "bank_balances"
    __table_args__ = (
        UniqueConstraint("balance_date", "bank_name", "currency", name="uq_bal"),
        Index("ix_bal_date", "balance_date"),
    )
    id = Column(Integer, primary_key=True)
    balance_date = Column(Date, nullable=False)
    bank_name = Column(String, nullable=False)
    currency = Column(String, nullable=False)
    amount = Column(Float, nullable=False, default=0.0)
    source_file = Column(String, default="")
    ingested_at = Column(DateTime, default=datetime.utcnow)


class Check(Base):
    """An issued cheque. `delivered` is resolved from CheckStatusMap at ingest."""
    __tablename__ = "checks"
    __table_args__ = (
        Index("ix_check_date", "check_date"),
        Index("ix_check_snapshot", "snapshot_date"),
    )
    id = Column(Integer, primary_key=True)
    snapshot_date = Column(Date, nullable=False)   # as-of date of the source list
    check_number = Column(String, index=True)
    supplier_name = Column(String, default="")
    bank_name = Column(String, default="")
    currency = Column(String, default="EGP")
    value = Column(Float, nullable=False, default=0.0)
    check_date = Column(Date)                      # the post-date written on the cheque
    raw_status = Column(String, default="")
    delivered = Column(Boolean, default=False)
    status_known = Column(Boolean, default=True)   # False => raw_status not in the map
    cleared = Column(Boolean, default=False)
    cleared_date = Column(Date)
    serial_no = Column(String, default="")
    source_file = Column(String, default="")
    ingested_at = Column(DateTime, default=datetime.utcnow)


# --- Receivables -------------------------------------------------------------

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    category = Column(String, default="")          # Factory | Projects | Dormant
    payment_terms_days = Column(Integer, default=60)
    # Rolling average of (actual collection date - due date) in days, from history
    avg_days_late = Column(Float, default=0.0)
    collection_rate = Column(Float, default=1.0)   # historical % eventually collected
    notes = Column(Text, default="")


class Receivable(Base):
    """An open AR item as of a snapshot date (Cash-In workbook + aging tabs)."""
    __tablename__ = "receivables"
    __table_args__ = (Index("ix_ar_snapshot", "snapshot_date"),)
    id = Column(Integer, primary_key=True)
    snapshot_date = Column(Date, nullable=False)
    customer_name = Column(String, nullable=False, index=True)
    category = Column(String, default="")          # Factory | Projects | Dormant | Forecast
    # Accounting classification from the AR sub-ledger. Deliberately separate
    # from `category`, which already carries the business unit.
    ar_class = Column(String, default="")          # trade | contract_asset | retention
    currency = Column(String, default="EGP")
    amount = Column(Float, nullable=False, default=0.0)
    amount_egp = Column(Float, nullable=False, default=0.0)
    invoice_date = Column(Date)
    due_date = Column(Date)
    doc_type = Column(String, default="")          # فاتورة / مطالبة / تامين ...
    aging_bucket = Column(String, default="")      # current | 1m | 2m | 3m+ | dormant
    notes = Column(Text, default="")
    source_file = Column(String, default="")
    ingested_at = Column(DateTime, default=datetime.utcnow)

    forecast = relationship(
        "CollectionForecast", back_populates="receivable",
        uselist=False, cascade="all, delete-orphan",
    )


class CollectionForecast(Base):
    """System-computed expected collection date, with a finance-team override.

    The requirement is explicit that the receivables team must be able to review
    and adjust forecast dates, so the override is stored alongside — never on top
    of — the computed value.
    """
    __tablename__ = "collection_forecasts"
    id = Column(Integer, primary_key=True)
    receivable_id = Column(Integer, ForeignKey("receivables.id"), unique=True)
    expected_date = Column(Date, nullable=False)
    probability = Column(Float, default=1.0)
    basis = Column(String, default="")             # how expected_date was derived
    override_date = Column(Date)
    override_probability = Column(Float)
    override_by = Column(String, default="")
    override_note = Column(Text, default="")
    override_at = Column(DateTime)

    receivable = relationship("Receivable", back_populates="forecast")

    @property
    def effective_date(self) -> date:
        return self.override_date or self.expected_date

    @property
    def effective_probability(self) -> float:
        return (
            self.override_probability
            if self.override_probability is not None
            else (self.probability or 1.0)
        )


class CashInForecast(Base):
    """Expected inflows that are not yet invoiced AR (the 'Forecast' rows)."""
    __tablename__ = "cash_in_forecast"
    id = Column(Integer, primary_key=True)
    snapshot_date = Column(Date, nullable=False)
    project_name = Column(String, nullable=False)
    category = Column(String, default="Forecast")
    currency = Column(String, default="EGP")
    amount = Column(Float, default=0.0)
    amount_egp = Column(Float, default=0.0)
    expected_date = Column(Date)
    probability = Column(Float, default=1.0)
    source_file = Column(String, default="")


class ExpenseForecast(Base):
    """Planned outflows (payroll, taxes, rents) from the Expenses Forecast tab."""
    __tablename__ = "expense_forecast"
    id = Column(Integer, primary_key=True)
    snapshot_date = Column(Date, nullable=False)
    category = Column(String, nullable=False)
    monthly = Column(Float, default=0.0)
    quarterly = Column(Float, default=0.0)
    annual = Column(Float, default=0.0)
    currency = Column(String, default="EGP")
    pay_day_of_month = Column(Integer, default=28)
    source_file = Column(String, default="")


class ManualCashFlow(Base):
    """Ad-hoc planned inflows/outflows the finance team enters directly."""
    __tablename__ = "manual_cash_flows"
    id = Column(Integer, primary_key=True)
    flow_date = Column(Date, nullable=False, index=True)
    description = Column(String, nullable=False)
    direction = Column(String, nullable=False)     # in | out
    currency = Column(String, default="EGP")
    amount = Column(Float, default=0.0)
    probability = Column(Float, default=1.0)
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


# --- Projects, P&L and budget ------------------------------------------------

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    client = Column(String, default="")
    segment = Column(String, default="")           # Factory | Projects | Related Party
    currency = Column(String, default="EGP")
    contract_value = Column(Float, default=0.0)
    contract_value_egp = Column(Float, default=0.0)
    start_date = Column(Date)
    end_date = Column(Date)
    status = Column(String, default="active")
    notes = Column(Text, default="")


class ProjectPeriod(Base):
    """Per-project P&L and cash flow for one period — budget and actual side by side."""
    __tablename__ = "project_periods"
    __table_args__ = (
        UniqueConstraint("project_id", "period", "scenario", name="uq_proj_period"),
    )
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    period = Column(String, nullable=False)        # 2026-01 | 2026-Q1 | 2026
    scenario = Column(String, nullable=False, default="budget")   # budget | actual

    revenue = Column(Float, default=0.0)
    cogs = Column(Float, default=0.0)
    gross_profit = Column(Float, default=0.0)
    overheads = Column(Float, default=0.0)
    ebitda = Column(Float, default=0.0)
    depreciation = Column(Float, default=0.0)
    ebit = Column(Float, default=0.0)
    net_profit = Column(Float, default=0.0)

    cash_in = Column(Float, default=0.0)
    cash_out = Column(Float, default=0.0)
    net_cash_flow = Column(Float, default=0.0)

    currency = Column(String, default="EGP")
    source_file = Column(String, default="")

    project = relationship("Project")


class FinancialLine(Base):
    """A single line of the IS / BS / CF for one period and one entity scope.

    Populated from the Business Plan workbook; 'A' columns land as actual and
    'P' columns as budget, which is what the Actual vs Budget report reads.
    """
    __tablename__ = "financial_lines"
    __table_args__ = (
        UniqueConstraint(
            "statement", "line_item", "period", "scenario", "scope",
            name="uq_finline",
        ),
        Index("ix_finline_period", "period"),
    )
    id = Column(Integer, primary_key=True)
    statement = Column(String, nullable=False)     # IS | BS | CF | RATIO
    line_item = Column(String, nullable=False)
    period = Column(String, nullable=False)        # 2024 | Q1-2026 | 2026-03
    period_type = Column(String, default="annual") # monthly | quarterly | annual
    scenario = Column(String, default="budget")    # actual | budget
    scope = Column(String, default="standalone")   # standalone | consolidated
    value = Column(Float, default=0.0)
    currency = Column(String, default="KEGP")
    source_file = Column(String, default="")


class MacroAssumption(Base):
    __tablename__ = "macro_assumptions"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)          # USD/EGP, Inflation - General, ...
    period = Column(String, nullable=False)
    value = Column(Float, default=0.0)
    unit = Column(String, default="")


# --- IFRS 9 ECL --------------------------------------------------------------

class ECLPolicy(Base):
    """Provision-matrix loss rates per aging bucket, per segment.

    IFRS 9 simplified approach: lifetime ECL for trade receivables, computed as
    gross carrying amount x bucket loss rate x forward-looking overlay.
    """
    __tablename__ = "ecl_policy"
    __table_args__ = (UniqueConstraint("segment", "bucket", name="uq_ecl_policy"),)
    id = Column(Integer, primary_key=True)
    segment = Column(String, nullable=False, default="default")
    bucket = Column(String, nullable=False)        # current | 1m | 2m | 3m+ | dormant
    loss_rate = Column(Float, nullable=False, default=0.0)
    effective_from = Column(Date)
    note = Column(Text, default="")


class ECLRun(Base):
    __tablename__ = "ecl_runs"
    id = Column(Integer, primary_key=True)
    run_date = Column(Date, nullable=False)
    as_of = Column(Date, nullable=False)
    macro_overlay = Column(Float, default=1.0)
    overlay_basis = Column(Text, default="")
    total_gross_egp = Column(Float, default=0.0)
    total_ecl_egp = Column(Float, default=0.0)
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    results = relationship("ECLResult", back_populates="run", cascade="all, delete-orphan")


class ECLResult(Base):
    __tablename__ = "ecl_results"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("ecl_runs.id"), nullable=False)
    customer_name = Column(String, nullable=False)
    segment = Column(String, default="")
    bucket = Column(String, default="")
    currency = Column(String, default="EGP")
    gross_amount = Column(Float, default=0.0)
    gross_amount_egp = Column(Float, default=0.0)
    loss_rate = Column(Float, default=0.0)
    ecl_egp = Column(Float, default=0.0)

    run = relationship("ECLRun", back_populates="results")


# --- External audit agent ----------------------------------------------------

class AuditEngagement(Base):
    __tablename__ = "audit_engagements"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    fiscal_year = Column(String, nullable=False)
    auditor = Column(String, default="")
    framework = Column(String, default="EAS")      # EAS | IFRS
    status = Column(String, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditQuery(Base):
    """An auditor comment/inquiry and the AI-drafted response."""
    __tablename__ = "audit_queries"
    id = Column(Integer, primary_key=True)
    engagement_id = Column(Integer, ForeignKey("audit_engagements.id"))
    reference = Column(String, default="")
    area = Column(String, default="")              # Revenue | AR | Provisions | Tax ...
    query_text = Column(Text, nullable=False)
    received_at = Column(Date, default=date.today)
    due_date = Column(Date)

    draft_response = Column(Text, default="")
    standards_cited = Column(Text, default="")     # JSON list
    documents_required = Column(Text, default="")  # JSON list
    risk_flags = Column(Text, default="")          # JSON list
    model_used = Column(String, default="")

    final_response = Column(Text, default="")
    reviewed_by = Column(String, default="")
    status = Column(String, default="drafted")     # drafted | reviewed | sent | closed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KnowledgeDoc(Base):
    """Reference material the audit agent grounds its responses in."""
    __tablename__ = "knowledge_docs"
    id = Column(Integer, primary_key=True)
    framework = Column(String, nullable=False)     # EAS | IFRS | LAW_159 | TAX | EVADERS
    ref = Column(String, nullable=False)           # "EAS 48", "IFRS 9.5.5.15", "Art. 40"
    title = Column(String, default="")
    body = Column(Text, nullable=False)
    tags = Column(String, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TaxEvader(Base):
    """Officially announced tax evaders — screened against customers/vendors."""
    __tablename__ = "tax_evaders"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, index=True)
    tax_id = Column(String, default="", index=True)
    announced_on = Column(Date)
    source = Column(String, default="")
    notes = Column(Text, default="")


class IngestLog(Base):
    __tablename__ = "ingest_log"
    id = Column(Integer, primary_key=True)
    file_name = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    as_of = Column(Date)
    rows_in = Column(Integer, default=0)
    rows_out = Column(Integer, default=0)
    warnings = Column(Text, default="")
    ok = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# =============================================================================
# Commitments, trade finance and funding
#
# Everything below is entered by the finance team or imported — none of it is in
# the five source workbooks. Each register ships empty with a CSV import path.
#
# The reason these live in the treasury app rather than a spreadsheet is that
# they all move cash, and the cash forecast was blind to them:
#   * an L/G or L/C cash margin is money in the bank that cannot be spent
#   * their commissions are a recurring outflow
#   * a tax obligation is a dated outflow
#   * an approved PO is committed spend that has not reached payables yet
#   * a down payment is cash in now that reduces later collections
# =============================================================================


class TradeFinanceFacility(Base):
    """A letter of guarantee or letter of credit, in either direction.

    `direction` is from ARESCO's side:
      issued   — our bank issued it on our behalf. Our cash margin is tied up.
      received — a counterparty's bank issued it in our favour. Protection we
                 hold; no cash impact for us.

    One table rather than two because the treasury rollup that matters —
    restricted cash and commission cost — is identical across both instruments.
    """
    __tablename__ = "trade_finance"
    __table_args__ = (
        Index("ix_tf_expiry", "expiry_date"),
        Index("ix_tf_kind", "instrument", "direction"),
    )
    id = Column(Integer, primary_key=True)
    reference = Column(String, index=True)            # bank's LG/LC number
    instrument = Column(String, nullable=False)       # LG | LC
    direction = Column(String, nullable=False)        # issued | received
    counterparty_type = Column(String, nullable=False)  # client | supplier
    counterparty = Column(String, nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"))
    project_name = Column(String, default="")

    # LG purpose: bid | advance_payment | performance | retention | maintenance | other
    # LC purpose: import | export
    purpose = Column(String, default="")

    bank_name = Column(String, default="")
    currency = Column(String, default="EGP")
    face_value = Column(Float, nullable=False, default=0.0)

    # Cash collateral the bank holds against the facility. Restricted: it is not
    # spendable even though it may sit inside a bank balance we already count.
    margin_pct = Column(Float, default=0.0)
    margin_amount = Column(Float, default=0.0)
    # True when the margin sits inside an account the daily bank workbook lists,
    # so the cash position must deduct it to avoid counting it as available.
    # Left False by default — double-deducting is as wrong as not deducting.
    margin_in_bank_balance = Column(Boolean, default=False)
    margin_account = Column(String, default="")

    commission_rate_pa = Column(Float, default=0.0)   # annual %, charged quarterly
    commission_period_months = Column(Integer, default=3)

    issue_date = Column(Date)
    expiry_date = Column(Date, index=True)
    auto_extend = Column(Boolean, default=False)
    # LC only
    lc_type = Column(String, default="")              # sight | usance
    usance_days = Column(Integer, default=0)
    latest_shipment_date = Column(Date)

    status = Column(String, default="active")         # active | expired | released | called | cancelled
    released_date = Column(Date)
    notes = Column(Text, default="")
    source_file = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DownPayment(Base):
    """An advance payment received from a client or paid to a supplier.

    A received advance is cash today, but it is not earned revenue — it unwinds
    as it is recovered from progress invoices. Recording the recovery matters:
    without it the forecast counts the advance once as cash in and again as a
    full-value collection later.
    """
    __tablename__ = "down_payments"
    id = Column(Integer, primary_key=True)
    direction = Column(String, nullable=False)        # received | paid
    counterparty = Column(String, nullable=False)
    counterparty_type = Column(String, default="")    # client | supplier
    project_id = Column(Integer, ForeignKey("projects.id"))
    project_name = Column(String, default="")
    reference = Column(String, default="")

    currency = Column(String, default="EGP")
    amount = Column(Float, nullable=False, default=0.0)
    amount_egp = Column(Float, default=0.0)
    pct_of_contract = Column(Float, default=0.0)

    expected_date = Column(Date)                      # when it is due
    received_date = Column(Date)                      # when it actually landed
    status = Column(String, default="expected")       # expected | received | recovered | cancelled

    # Recovery: an advance is normally deducted from each progress invoice at a
    # fixed percentage until fully recovered.
    recovery_pct = Column(Float, default=0.0)
    recovered_amount = Column(Float, default=0.0)
    fully_recovered_date = Column(Date)

    # An advance is almost always secured by an advance-payment guarantee.
    guarantee_id = Column(Integer, ForeignKey("trade_finance.id"))
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    guarantee = relationship("TradeFinanceFacility")

    @property
    def outstanding(self) -> float:
        return max((self.amount or 0.0) - (self.recovered_amount or 0.0), 0.0)


class TaxObligation(Base):
    """A tax or governmental due, with the date it must be settled.

    The Business Plan's Governmental Dues sheet carries the balances but leaves
    every settlement year at zero, so obligations ingested from it arrive with
    no due date and are excluded from the dated forecast until one is set.
    """
    __tablename__ = "tax_obligations"
    __table_args__ = (Index("ix_tax_due", "due_date"),)
    id = Column(Integer, primary_key=True)
    tax_type = Column(String, nullable=False)
    # corporate_income | vat | wht | payroll | social_insurance | stamp | property | other
    description = Column(String, default="")
    period = Column(String, default="")               # 2026-03 | Q1-2026 | 2025
    authority = Column(String, default="ETA")

    currency = Column(String, default="EGP")
    base_amount = Column(Float, default=0.0)
    rate = Column(Float, default=0.0)
    amount_due = Column(Float, nullable=False, default=0.0)
    penalty = Column(Float, default=0.0)
    paid_amount = Column(Float, default=0.0)

    due_date = Column(Date)
    filed_date = Column(Date)
    paid_date = Column(Date)
    status = Column(String, default="open")           # open | filed | paid | disputed | unscheduled
    reference = Column(String, default="")
    notes = Column(Text, default="")
    source_file = Column(String, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def outstanding(self) -> float:
        return max((self.amount_due or 0.0) + (self.penalty or 0.0) - (self.paid_amount or 0.0), 0.0)


class PurchaseRequisition(Base):
    """A request to buy, before it becomes a commitment."""
    __tablename__ = "purchase_requisitions"
    id = Column(Integer, primary_key=True)
    pr_number = Column(String, unique=True, nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    project_name = Column(String, default="")
    requested_by = Column(String, default="")
    department = Column(String, default="")
    description = Column(Text, default="")
    category = Column(String, default="")             # steel | consumables | subcontract | freight | services | other

    currency = Column(String, default="EGP")
    estimated_value = Column(Float, default=0.0)
    required_by = Column(Date)

    raised_date = Column(Date, default=date.today)
    approved_by = Column(String, default="")
    approved_date = Column(Date)
    status = Column(String, default="draft")          # draft | pending | approved | rejected | converted | cancelled
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    orders = relationship("PurchaseOrder", back_populates="requisition")


class PurchaseOrder(Base):
    """A placed order. Once approved this is committed spend, and the
    uninvoiced balance is a future cash outflow the payables ledger cannot see
    yet — which is precisely what the cash forecast was missing."""
    __tablename__ = "purchase_orders"
    __table_args__ = (Index("ix_po_delivery", "delivery_date"),)
    id = Column(Integer, primary_key=True)
    po_number = Column(String, unique=True, nullable=False, index=True)
    pr_id = Column(Integer, ForeignKey("purchase_requisitions.id"))
    supplier = Column(String, nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"))
    project_name = Column(String, default="")
    category = Column(String, default="")
    description = Column(Text, default="")

    currency = Column(String, default="EGP")
    order_value = Column(Float, nullable=False, default=0.0)
    received_value = Column(Float, default=0.0)
    invoiced_value = Column(Float, default=0.0)
    paid_value = Column(Float, default=0.0)

    order_date = Column(Date, default=date.today)
    delivery_date = Column(Date)
    payment_terms_days = Column(Integer, default=30)
    status = Column(String, default="open")           # open | partial | received | closed | cancelled
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    requisition = relationship("PurchaseRequisition", back_populates="orders")

    @property
    def uninvoiced(self) -> float:
        return max((self.order_value or 0.0) - (self.invoiced_value or 0.0), 0.0)

    @property
    def unpaid(self) -> float:
        return max((self.order_value or 0.0) - (self.paid_value or 0.0), 0.0)


class CostCategory(Base):
    """Classifies a cost as variable or fixed, which is what makes contribution
    margin computable. Seeded with the split a steel fabricator normally uses;
    editable, because the line between the two is a management judgement."""
    __tablename__ = "cost_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    behaviour = Column(String, nullable=False, default="variable")  # variable | fixed | semi
    variable_portion = Column(Float, default=1.0)     # for semi-variable
    note = Column(Text, default="")


class ProjectCost(Base):
    """An actual or committed cost against a project, classified for margin.

    Kept separate from ProjectPeriod so a cost can carry its category and its
    source (PO, invoice, allocation) rather than collapsing into one number.
    """
    __tablename__ = "project_costs"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    period = Column(String, nullable=False)           # 2026-03 | Q1-2026 | 2026
    category = Column(String, nullable=False)
    scenario = Column(String, default="actual")       # actual | budget | committed
    currency = Column(String, default="EGP")
    amount = Column(Float, default=0.0)
    amount_egp = Column(Float, default=0.0)
    source = Column(String, default="")               # po | invoice | allocation | manual
    source_ref = Column(String, default="")
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


# Default cost behaviour for a steel fabricator. Materials and subcontract move
# with output; factory overhead and G&A do not.
DEFAULT_COST_CATEGORIES = [
    ("steel", "variable", 1.0, "Plate, sections, bar — moves directly with tonnage"),
    ("consumables", "variable", 1.0, "Welding wire, gas, abrasives, paint"),
    ("subcontract", "variable", 1.0, "Erection, galvanising, machining bought out"),
    ("freight", "variable", 1.0, "Inbound and outbound transport"),
    ("direct labour", "semi", 0.6, "Core crew is fixed; overtime and hired hands vary"),
    ("site costs", "variable", 1.0, "Accommodation, site plant hire, temporary works"),
    ("customs & duties", "variable", 1.0, "On imported material"),
    ("factory overhead", "fixed", 0.0, "Rent, depreciation, utilities on the works"),
    ("G&A", "fixed", 0.0, "Head office"),
    ("selling & marketing", "fixed", 0.0, "Tendering and business development"),
    ("finance costs", "fixed", 0.0, "Interest, LG/LC commission"),
]
