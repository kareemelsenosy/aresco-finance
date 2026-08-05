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

## Sign-in

Access is limited to **@aresco.com.eg** addresses. Anything else is refused at
sign-up and at login.

First time for an address:

1. Enter the ARESCO email on the login page.
2. A six-digit code is emailed; it expires in 15 minutes.
3. Enter the code, then choose a password (10+ characters, letters mixed with
   numbers or symbols).

After that it is email + password. Sessions last 12 hours and are held in an
HttpOnly cookie. Eight wrong passwords locks the account for 15 minutes.

### Mail

Verification codes need SMTP. Set these in `.env`:

```
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USER=no-reply@aresco.com.eg
SMTP_PASSWORD=...
SMTP_FROM=no-reply@aresco.com.eg
SMTP_STARTTLS=true
```

**With `SMTP_HOST` empty the code is not emailed — it is written to the server
log instead.** That keeps the flow testable on a machine with no mail relay,
but it means anyone who can read the log can complete someone else's sign-up.
Configure SMTP before the tool is used by more than yourself.

`SECRET_KEY` signs the session cookies. It must be a long random string and
must not change, or everyone is signed out:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Access is gated in one place

`api/gate.py` is middleware in front of every route, default-deny with a small
allow-list (the login page, its assets, `/health`, the `/auth/*` endpoints).
A new router is therefore protected the moment it is added — there is no
per-route decorator to forget. `/docs` is behind it too.

### The figures are shared

Every signed-in user sees the same cash position, receivables and reporting —
an account is an access grant, not a private slice of the data. Only sign-in
state is per-user.

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
