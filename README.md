# ARESCO Finance

Cash management, receivables, reporting, IFRS 9 ECL and external-audit support,
built on the workbooks the finance team already produces.

FastAPI + SQLAlchemy + SQLite, vanilla-JS front end — the same stack as the BOQ
engine in `Estimation/boq-engine`, so there is one thing to run and one thing to
deploy.

---

## Run it

```bash
cd Finance/finance-app
python3.13 -m venv .venv                 # 3.14 has no wheel for pydantic-core yet
./.venv/bin/pip install -r requirements.txt
cp .env.example .env                     # add OPENAI_API_KEY / ANTHROPIC_API_KEY
./.venv/bin/python scripts/ingest_all.py ..   # load the workbooks in Finance/
./.venv/bin/uvicorn api.main:app --port 8100
```

Then open **http://127.0.0.1:8100/app/** — API docs are at `/docs`.

An API key is needed by the audit assistant, the scanned-PDF reader, and the
upload repair path. Every other module works without one.

`LLM_ORDER` (default `openai,anthropic`) sets which provider is asked first. A
provider with no key is skipped, and if the first one errors the second takes
the call — so a rate limit or an outage on one side doesn't stop a team
uploading their file. The review screen names whichever model actually read it.

---

## What it does

### Cash management
- **Daily bank balances** across 9 accounts and 4 currencies, per-currency and
  consolidated to EGP.
- **Net available cash** — the headline figure, presented as the subtraction it
  is: bank balance, less cheques delivered to vendors and uncleared, less
  cheques issued but still on hand.
- **Outstanding cheque register**, split delivered / on hand, with an aging
  analysis of the on-hand cheques.
- **Post-dated cheque calendar** with the projected balance after each payment
  day, and the first date the balance goes negative.
- **Rolling 30/60/90-day daily cash flow** combining opening balance, expected
  collections, forecast inflows, scheduled cheques and planned costs — with the
  line items driving each day, so a shortage traces to its cause.

### Receivables
- Aging by bucket, customer and currency.
- **Collection forecast** from payment terms, due dates, the customer's average
  days late and historical collection rate — every date **editable by the
  receivables team**, with the model's own value kept alongside so a recompute
  never destroys a human judgement.
- **RDOH**, reported with its caveats rather than as a bare number (see
  *Known data gaps*).

### Business plan — actual vs plan
- Headline KPIs for the plan year: revenue, EBITDA and margin, EAT, and the
  step-up **against what the prior year actually delivered**, not against its plan.
- Revenue by year, actual bars solid and plan bars hatched.
- Quarterly phasing of the plan year, with the H2 concentration called out.
- The full P&L 2022–2033, with the variance printed under any figure where the
  year has both a reported actual and a plan.

### Reporting
- Summarized BS/IS matrix — Total Assets, Total Liabilities, Working Capital,
  Retained Earnings, Revenues, EBITDA, COGS, Net Profit, No. of Shares —
  monthly / quarterly / annual, standalone and consolidated.
- Full IS, BS and CF; the ratio set from the business plan.
- Actual vs budget variance.
- Per-project P&L and cash flow.

### Trade finance — L/G and L/C, both sides
- Letters of guarantee and credit in one register, `issued` (our exposure) and
  `received` (protection we hold), against clients and suppliers.
- **A cash margin on an issued facility is deducted from net available cash** —
  it sits in the bank and the bank will not release it. Only margins flagged as
  living inside an account the daily workbook reports are deducted; the rest are
  disclosed, because double-deducting is as wrong as not deducting.
- Commission schedules feed the cash forecast; expiries are tracked so a margin
  is released or renewed deliberately.

### Down payments
- Advances received from clients and paid to suppliers, each linkable to its
  advance-payment guarantee — an advance received with no guarantee is flagged.
- Recovery is tracked, because an advance is cash today that unwinds against
  later progress invoices. Without it the forecast counts the money twice.

### Tax and governmental dues
- Obligation register with due dates, ageing and the Egyptian filing cadence.
- **An obligation with no due date cannot enter a dated forecast**, so it is
  reported as a gap rather than silently omitted.

### Procurement — PR → PO
- Requisitions through to orders, with receipt, invoicing and payment.
- **Committed-uninvoiced** is the number that matters: spend the payables ledger
  cannot see yet, which for a fabricator buying steel months ahead is most of
  the working capital. It enters the cash forecast on PO terms.

### Contribution margin
- Revenue less the costs that actually move with the work — not gross profit.
  CM %, break-even revenue and margin of safety, company-wide and per project.
- The variable/fixed split is a management judgement, so it lives in an editable
  table and every figure states which classification produced it.

### Funding position
- The consolidated answer to "what money do we have": a ladder from cash at bank
  out through receivables, advances and commitments. The rungs are deliberately
  not summed into one number — cash and a government receivable are both money,
  but only one pays a supplier this week.
- Everything deliberately excluded is listed with its effect on the position.

### IFRS 9 ECL
- Lifetime ECL under the **simplified approach** (IFRS 9.5.5.15), measured with
  a provision matrix (IFRS 9.B5.5.35), multi-currency, run monthly.
- Editable loss-rate matrix by segment and bucket.
- **Forward-looking overlay** derived from the business plan's own USD/EGP
  forecast, satisfying IFRS 9.5.5.17(c) with a board-approved input rather than
  an unexplained management percentage.
- Run history and period-on-period movement (the IFRS 7.35 disclosure note).

### External audit assistant
- Drafts responses to auditor comments, returning: an assessment for the
  controller, the draft response, standards cited, documents to attach, and
  risk flags.
- **Cites only references held in the knowledge store.** If it needs a reference
  it does not have, it says so in `missing_references` rather than inventing an
  article number. Extend the store via `POST /audit/knowledge`.
- **Quotes only figures read from the database.** It cannot invent a balance.
- Tax-evader register with counterparty screening (ships empty on purpose — load
  the current published list).

---

## Ingest

| Workbook | Loader | What it yields |
|---|---|---|
| `Bank Cash<year>.xlsx` | `ingest/bank_cash.py` | daily balances, one sheet per day |
| `Bank Balance With Checks List-*.xlsx` | `ingest/checks.py` | the issued-cheque register |
| `Cash-In *.xlsx` | `ingest/cash_in.py` | receivables, forecast inflows, expenses, **the day's FX rates** |
| `Aresco BP - *.xlsx` | `ingest/business_plan.py` | IS/BS/CF, ratios, projects, macro assumptions |
| `* FS *.pdf` (scanned) | `ingest/fs_pdf.py` | audited statements, read with vision |

### When a workbook no longer matches its loader

Each loader above knows one file's shape, which makes it exact and free — and
brittle the moment that shape moves. A sheet renamed `Cash-In (2)`, a column
relabelled `Customer Name` instead of `Project Name`, the register moved to the
fourteenth tab: any of these used to mean a hard failure or, worse, a cheerful
`200 loaded` with none of the rows the item exists for.

`ingest/repair.py` catches both cases. It surveys every sheet, sends the layout
(not the data) to the model, and asks *where* the register is — which sheet,
which header row, which column feeds which field. The answer is coordinates, so
every value is still read from the cell and typed by the same parsers the
loaders use; a hallucinated number has no route into the ledger. The result is a
plan and a preview on screen, and nothing is written until it is confirmed.

### Upload replaces, it does not accumulate

A team re-sends the same register with corrections far more often than they send
a genuinely new one, so an upload supersedes the last one for that item rather
than stacking on top of it. Every row carries the `source_file` it arrived in;
loading a new file for an item deletes the rows earlier uploads of *that* item
wrote and leaves everything else — other items, and anything entered by hand —
alone.

The loader runs with the transaction held open. It clears the rows it is about
to rewrite, so a file that turns out to be unreadable would otherwise destroy
what it was meant to replace; holding the transaction makes the rollback real,
and a failed upload leaves the previous version exactly where it was.

### Drop anything, anywhere

`POST /intake/auto/upload` takes a file without being told what it is. The
contents decide which of the sixteen requests it answers — the file name is
evidence of nothing, being stale, misspelt and reused — and it then follows the
identical path as if it had been filed by hand. What it decided, how sure it
was, and the runner-up are shown on the result, so a wrong call is visible
rather than quietly acted on. A file that matches nothing is refused: one that
never arrived gets chased, one filed in the wrong register is believed.

`ingest/normalize.py` runs before any of this and settles what the file actually
is. An extension is a claim: Treasury's cheque register arrives as `.XLS` and is
really a UTF-16 tab-separated ERP dump. It is converted to a real workbook rather
than rejected at the door.

The signed statements and the plan name the same lines differently
(`Revenues from contracts with customers` vs `Total Revenues`), so
`engine/reporting.py` carries an alias map. Only unambiguous equivalences are
mapped — the plan's single `Provisions & Impairments` against the statements'
separate provisions formed / ECL charge / provisions released is left unmapped
and reported, because a wrong pairing is worse than a missing one. Lines the
two sources book with opposite signs are compared on magnitude.

Load Cash-In **first** — its header carries the FX rates the other loaders need.
`scripts/ingest_all.py` and `POST /ingest/load-directory` both handle the order.

The loaders are written for workbooks maintained by hand, and they say what they
had to work around. On the current files they report:

- `28.08.2028` sits between two August-2025 sheets, and `11.05.2027` between two
  May-2026 sheets. Both are typo'd years; the year is taken from the median of
  the surrounding sheets, since these workbooks are chronological.
- `25.02.206` and `17.03.206` are missing a digit; repaired the same way.
- The cheque workbook's EGY and USD tabs carry **different header dates**
  (27 and 26 July). They are one register refreshed tab by tab, so every cheque
  is stamped with the file's latest date. Without this, the two USD cheques
  (USD 17,000 ≈ EGP 867,510) fell outside the current snapshot and dropped
  silently out of the cash position.
- `MacroAssumptions` repeats 2031–2033 in a second scenario block; the rightmost
  column wins.

Every warning is kept in the ingest log and shown on the **Data & sources**
page. They are how a bad source row gets caught.

---

## Known data gaps

These are limits of the source data, not bugs. Each one is surfaced in the UI
rather than hidden behind a plausible-looking number.

**No AED exchange rate.** `AAIB Dubai` holds AED 13,319 and no source file
carries an AED rate. It is excluded from the consolidated EGP total and
disclosed rather than guessed. Fix: `POST /fx/rates`.

**The Cash-In list is not the AR ledger.** It totals EGP 77.9m; the balance
sheet reports EGP 701m of receivables for 2026. RDOH on the working list is
23.9 days against 215.3 days on balance-sheet AR — a ~9× understatement. RDOH is
returned with `reportable: false` and the caveat attached until the full ledger
is loaded. **This is the single most valuable thing to fix.**

**Forecast inflows are unphased.** All 15 forecast rows (EGP 217.9m) carry the
same placeholder date of 2026-08-01. The projection flags this; it needs real
expected dates before anything past that day means much.

**Eight expense lines are unquantified.** Salaries Tax, WH Tax, Social
Insurance, Electricity, Cars Rent, Employees Transportation, Customs and
Buildings Rent are named in the Expenses Forecast tab with no monthly amount, so
they are excluded from the projection — which therefore **understates outflows**.
Fix: `PUT /forecast/expenses/{id}`.

**Aging tabs are dated 22 July; the Cash-In tab is 26 July.** Bucket labels are
therefore four days older than the balances they describe.

**No per-project actual costs** exist in any workbook, so project variance is
unavailable until actuals are posted via
`POST /reporting/projects/{id}/actuals`.

**No consolidated figures.** The business plan is standalone only; the
consolidated view will be empty until a group pack is loaded.

**2025 landed 37% below plan.** The signed December 2025 statements report
revenue of EGP 751.6m against a plan of EGP 1,202.2m, and EAT of EGP 96.3m
against EGP 154.3m. The 2026 plan of EGP 4,206m is therefore a **5.6× step-up on
what 2025 actually delivered**, not the 3.5× a plan-on-plan comparison shows.
The Business Plan screen leads with this.

**EGP 358.3m of governmental dues have no settlement date.** The Business Plan's
Governmental Dues sheet gives balances but leaves every settlement year at zero,
so they load as *unscheduled* and are excluded from the dated cash forecast — the
projection warns that it is optimistic by at least that amount. Setting a due
date on the Tax screen brings each one in.

**The L/G, L/C, down payment and PR/PO registers ship empty.** None of that is in
the source workbooks. Each has a CSV template at
`GET /commitments/template/{register}` and a bulk import at
`POST /commitments/import/{register}`.

**Contribution margin per project needs categorised costs.** The company-level
figure works from the P&L today; project-level margin needs cost lines tagged by
category, posted to `/margin/costs` or supplied by approved POs.

**2034 and 2035 do not balance.** The business plan's own BS and CF checker rows
read `#REF!` for those columns. Those periods are marked *unbalanced* in the UI.

**The audit knowledge pack is unverified.** 26 seeded references, several marked
`VERIFY` or `PLACEHOLDER` — article numbers, tax rates and ARESCO's own
accounting policy. They must be checked against the official text before any
response relying on them is sent to an auditor. The UI shows the count in red
until they are.

---

## Layout

```
api/            FastAPI app, models, routers
engine/         cash, receivables, forecast, reporting, ecl, audit_agent, fx
ingest/         one adapter per source workbook + the scanned-PDF reader
knowledge/      the seeded accounting/tax reference pack
frontend/       index.html, app.js, style.css
scripts/        ingest_all.py
```

## Design

The front end implements the **ARESCO Treasury UI** handoff from
claude.ai/design. Tokens, type scale and component shapes come from the design
source; its inline styles are lifted into classes in `frontend/style.css`. The
four rules the design states are enforced there:

1. **Numbers are the hero** — IBM Plex Mono, tabular, right-aligned, no decimals
   above a thousand.
2. **Colour is semantic only** — positive, shortage, ageing, delivered,
   on-hand/override. Nothing is coloured for decoration.
3. **Forecast is never solid** — projections are dashed, hatched or lighter, and
   a dashed marker separates actual from projected on every chart. In the
   business plan, a line the audited statements do not report falls back to the
   plan and is hatched with a `P` marker even inside an "actual" column.
4. **Provenance is built in** — every tile and table carries its as-of date and
   source workbook.

Arabic counterparty names sit in left-aligned LTR cells wrapped in `dir="rtl"` +
`unicode-bidi: isolate`, so the name renders correctly without dragging
punctuation, digits or the column alignment with it.

## Endpoints

`/dashboard` · `/data-status` · `/cash/*` · `/receivables/*` · `/forecast/*` ·
`/reporting/*` (including `/reporting/business-plan`) · `/ecl/*` · `/audit/*` ·
`/ingest/*` · `/fx/rates` · `/fx/current` — full schema at `/docs`.

`/fx/rates` lists every rate on file including the plan's long-dated
projections; `/fx/current` returns the rates actually in force on a date. Read
the newest row out of the former and you get the 2035 planning rate.
