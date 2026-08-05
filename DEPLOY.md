# Deploying on the ARESCO local server

This repository holds the **application code only**. The finance workbooks it
reads, the database built from them, and the API key live in the private
companion repository:

    github.com/kareemelsenosy/aresco-finance-data   (private)

You need both for the app to show real numbers.

---

## First-time setup

```bash
# 1. Application code
git clone https://github.com/kareemelsenosy/aresco-finance.git
cd aresco-finance

# 2. Workbooks, database and secrets (private — asks for credentials)
git clone https://github.com/kareemelsenosy/aresco-finance-data.git /tmp/fin-data
/tmp/fin-data/install.sh .

# 3. Environment and start
python3.13 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8100
```

Then open **http://<server>:8100/app/** — API docs at `/docs`.

> Python **3.13** specifically. 3.14 has no `pydantic-core` wheel yet.

`install.sh` places `.env`, the prebuilt `aresco_finance.db` and the source
workbooks under `data/workbooks/`.

---

## Rebuilding the database from the workbooks

`install.sh` ships a database that is already loaded, so this is only needed
when the finance team issues newer workbooks:

```bash
./.venv/bin/python scripts/ingest_all.py data/workbooks
```

Order matters and the script handles it — Cash-In runs first because its
header carries the FX rates the cheque and receivable conversions need.

Drop the updated `.xlsx` files into `data/workbooks/` in the private repo,
commit them there, and re-run the command above.

---

## Updating

```bash
git -C /path/to/aresco-finance pull            # code
git -C /tmp/fin-data pull && /tmp/fin-data/install.sh /path/to/aresco-finance
```

---

## Running without the private repo

Everything starts and the schema builds itself, but every figure is empty
until workbooks are ingested. Copy `.env.example` to `.env`; only the audit
assistant and the scanned-PDF reader need `ANTHROPIC_API_KEY`, the rest of
the app works without one.

---

## What must never come back into this repo

| Category | Examples |
|---|---|
| Secrets | `.env` |
| Database | `aresco_finance.db` |
| Company financials | bank balance and cash workbooks, the business plan, signed FS PDFs |
| Internal material | board presentations, rollout emails |

`.gitignore` covers `.env`, `*.db`, `uploads/` and `data/`. Check
`git status` before committing anything new at the repo root.
