"""Currency conversion.

Rates come from three places, in priority order:
  1. an explicit FXRate row on/before the requested date
  2. the rate header on the Cash-In workbook (ingested as source='cash-in')
  3. the Business Plan MacroAssumptions sheet (annual, source='business-plan')

Anything unresolvable falls back to 1.0 for EGP and raises for other currencies,
so a missing rate is a visible error rather than a silently wrong total.
"""

from datetime import date
from sqlalchemy import and_
from sqlalchemy.orm import Session

from api.models import FXRate

BASE = "EGP"
_ALIASES = {
    "EGP": "EGP", "جنية": "EGP", "جنيه": "EGP", "LE": "EGP", "L.E": "EGP",
    "USD": "USD", "$": "USD", "دولار": "USD", "US$": "USD",
    "EUR": "EUR", "€": "EUR", "EURO": "EUR", "يورو": "EUR",
    "AED": "AED", "درهم": "AED",
}


def normalize_currency(raw) -> str:
    if raw is None:
        return BASE
    key = str(raw).strip().upper()
    if key in _ALIASES:
        return _ALIASES[key]
    for alias, code in _ALIASES.items():
        if alias.upper() in key:
            return code
    return key or BASE


class FXConverter:
    """Caches the rate lookup so a 5,000-row ingest doesn't hit the DB per row.

    A missing rate is never guessed. `to_egp` raises so an ingest fails loudly;
    `try_to_egp` returns None and records the currency in `missing`, so a report
    can convert what it can and disclose what it couldn't. Both are needed:
    silently dropping an unconvertible balance would understate the position,
    and inventing a rate would misstate it.
    """

    def __init__(self, db: Session, as_of: date | None = None):
        self.db = db
        self.as_of = as_of or date.today()
        self._cache: dict[tuple[str, date], float | None] = {}
        self.missing: set[str] = set()

    def find_rate(self, currency: str, on: date | None = None) -> float | None:
        ccy = normalize_currency(currency)
        if ccy == BASE:
            return 1.0
        when = on or self.as_of
        key = (ccy, when)
        if key in self._cache:
            return self._cache[key]

        row = (
            self.db.query(FXRate)
            .filter(and_(FXRate.currency == ccy, FXRate.rate_date <= when))
            .order_by(FXRate.rate_date.desc())
            .first()
        )
        if row is None:
            # Nothing on or before `when` — accept the nearest later rate rather
            # than failing an entire report on a back-dated row.
            row = (
                self.db.query(FXRate)
                .filter(FXRate.currency == ccy)
                .order_by(FXRate.rate_date.asc())
                .first()
            )
        value = row.rate_to_egp if row else None
        self._cache[key] = value
        if value is None:
            self.missing.add(ccy)
        return value

    def rate(self, currency: str, on: date | None = None) -> float:
        value = self.find_rate(currency, on)
        if value is None:
            raise ValueError(
                f"No FX rate available for {normalize_currency(currency)}. "
                f"Add one via POST /fx/rates, or ingest a Cash-In workbook "
                f"(its header carries the day's rates)."
            )
        return value

    def to_egp(self, amount: float, currency: str, on: date | None = None) -> float:
        if amount is None:
            return 0.0
        return float(amount) * self.rate(currency, on)

    def try_to_egp(self, amount: float, currency: str, on: date | None = None) -> float | None:
        """Convert, or return None when no rate is held for the currency."""
        if amount is None:
            return 0.0
        value = self.find_rate(currency, on)
        return None if value is None else float(amount) * value


def upsert_rate(db: Session, rate_date: date, currency: str, rate: float, source: str):
    """Insert or update one rate. Flushes so repeat calls in a loop update
    rather than queueing a second insert that violates the unique constraint —
    the Business Plan's MacroAssumptions sheet repeats several forecast years
    across two scenario blocks, so the same (date, currency) does come round twice.
    """
    ccy = normalize_currency(currency)
    if ccy == BASE or not rate:
        return None
    existing = (
        db.query(FXRate)
        .filter(and_(FXRate.rate_date == rate_date, FXRate.currency == ccy))
        .first()
    )
    if existing:
        existing.rate_to_egp = rate
        existing.source = source
        db.flush()
        return existing
    row = FXRate(rate_date=rate_date, currency=ccy, rate_to_egp=rate, source=source)
    db.add(row)
    db.flush()
    return row
