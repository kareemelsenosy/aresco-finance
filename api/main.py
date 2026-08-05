"""
ARESCO Finance — cash management, receivables, reporting, ECL and audit support.

  /cash         daily balances, outstanding cheques, available cash, PDC schedule
  /receivables  aging, collection forecast with team overrides, RDOH
  /forecast     rolling 30/60/90-day daily cash flow with shortage warnings
  /reporting    P&L, balance sheet, project results, actual vs budget
  /ecl          IFRS 9 expected credit loss
  /audit        external audit assistant and reference store
  /trade-finance L/Gs and L/Cs, both directions, restricted cash and commission
  /down-payments client advances and supplier advances
  /tax          tax and governmental dues, with the filing calendar
  /procurement  purchase requisitions and orders — committed spend
  /margin       contribution margin, company and per project
  /funding      the consolidated funding position
  /ingest       load the finance team's workbooks
  /dashboard    everything the landing page needs, in one call
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.database import init_db
from api.gate import AuthGate
from api.routers import (
    admin, audit, auth, cash, commitments, ecl, forecast, ingest, receivables,
    reporting,
)

app = FastAPI(
    title="ARESCO Finance",
    description=__doc__,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# The gate runs in front of every route except the login flow itself.
app.add_middleware(AuthGate)

# Same-origin front end, so no cross-origin allowance is needed. A wildcard
# origin is also invalid alongside credentialed requests, which the session
# cookie now makes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(ingest.router)
app.include_router(cash.router)
app.include_router(receivables.router)
app.include_router(forecast.router)
app.include_router(reporting.router)
app.include_router(ecl.router)
app.include_router(audit.router)
app.include_router(commitments.router)

frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/app", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")


@app.get("/health")
def health():
    from api.config import settings

    return {
        "status": "ok",
        "base_currency": settings.base_currency,
        "audit_assistant": "configured" if settings.anthropic_api_key else "no API key",
    }
