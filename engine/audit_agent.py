"""External audit assistant.

Takes an auditor comment or inquiry and produces a draft response, the
supporting documents to attach, and the standards the position rests on.

Two design rules make the output safe to put in front of an auditor:

  1. The agent may only cite references that exist in the knowledge store. If
     the right reference is not held, it says so rather than inventing an
     article number. A fabricated citation to an auditor is worse than no
     citation.
  2. Company figures are pulled from the database and passed in as facts, so a
     response quoting a balance quotes the actual ingested balance rather than
     a number the model reasoned its way to.
"""

import json
import re
from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import Session

from api.config import settings
from api.models import AuditQuery, KnowledgeDoc, TaxEvader
from engine.cash import available_cash
from engine.receivables import aging_report, rdoh

SYSTEM_PROMPT = """You are the technical accounting assistant to the finance \
team of ARESCO, an Egyptian steel fabrication and EPC contractor within Qalaa \
Holdings. You support the annual external audit.

You draft responses to auditor comments and inquiries. Your responses are read \
by a qualified controller before they are sent, but they must be correct enough \
to send with minimal editing.

## Grounding rules — these are absolute

1. Cite ONLY references supplied to you in the REFERENCE MATERIAL section. Never \
   cite a standard, article, decree or rate that is not in that section, even if \
   you are confident it exists. If the position needs a reference you were not \
   given, put it in `missing_references` and write the response without the \
   citation.
2. Use ONLY the figures supplied in the COMPANY DATA section. Never estimate, \
   derive or recall a company figure. If the response needs a number you were not \
   given, name it in `data_required`.
3. Where a reference is marked VERIFY or PLACEHOLDER, treat it as unconfirmed: \
   you may rely on the principle but must add the point to `risk_flags` so the \
   controller checks it before sending.

## How to respond

Write as the company responding to its auditor: direct, specific, and framed \
around what the accounting position is and why it complies. State the treatment \
applied, the basis for it, and the evidence available. Do not be defensive and \
do not over-explain.

Where the auditor's point appears well-founded — the treatment is wrong, a \
disclosure is missing, a provision is understated — say so plainly in \
`assessment` and draft a response that concedes and sets out the correction. \
Drafting a defence of an indefensible position is the one thing that will lose \
the controller's trust in you."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "area": {
            "type": "string",
            "description": "Financial statement area, e.g. Revenue, Receivables, Provisions, Tax, Related Parties, Going Concern",
        },
        "assessment": {
            "type": "string",
            "description": "Your own read on the auditor's point: is it well-founded, partly founded, or based on a misunderstanding? One short paragraph, written for the controller, not the auditor.",
        },
        "draft_response": {
            "type": "string",
            "description": "The response to send to the auditor. Plain prose or short numbered points. No preamble.",
        },
        "standards_cited": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "relevance": {"type": "string"},
                },
                "required": ["ref", "relevance"],
                "additionalProperties": False,
            },
            "description": "References used, taken verbatim from REFERENCE MATERIAL.",
        },
        "documents_required": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Specific supporting documents to attach, named precisely enough that someone can go and find them.",
        },
        "missing_references": {
            "type": "array",
            "items": {"type": "string"},
            "description": "References the response would have cited but which were not supplied.",
        },
        "data_required": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Company figures or documents needed to complete the response.",
        },
        "risk_flags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Points the controller must check before sending, including anything resting on a VERIFY or PLACEHOLDER reference.",
        },
    },
    "required": [
        "area", "assessment", "draft_response", "standards_cited",
        "documents_required", "missing_references", "data_required", "risk_flags",
    ],
    "additionalProperties": False,
}


# --- Knowledge retrieval ------------------------------------------------

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "was",
    "are", "were", "you", "your", "our", "please", "provide", "explain", "why",
    "how", "what", "which", "would", "could", "should", "kindly", "regarding",
}


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", text.lower())
    return [w for w in words if w not in _STOPWORDS]


def retrieve(db: Session, query: str, limit: int = 8) -> list[KnowledgeDoc]:
    """Score knowledge docs against the query and return the best matches.

    Deliberately a simple keyword scorer rather than embeddings: the store is a
    few dozen documents, the vocabulary is standardised accounting language,
    and a scorer the finance team can reason about beats one they cannot.
    """
    docs = db.query(KnowledgeDoc).all()
    if not docs:
        return []

    terms = _keywords(query)
    if not terms:
        return docs[:limit]

    scored = []
    for d in docs:
        haystack = f"{d.ref} {d.title} {d.tags} {d.body}".lower()
        tag_set = {t.strip().lower() for t in (d.tags or "").split(",") if t.strip()}
        score = 0
        for t in set(terms):
            if t in tag_set:
                score += 5          # an explicit tag hit is a strong signal
            elif t in (d.title or "").lower():
                score += 3
            elif t in haystack:
                score += 1
        # An explicit standard reference in the query pins that document
        if d.ref.lower() in query.lower():
            score += 20
        if score:
            scored.append((score, d))

    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored[:limit]]


def company_facts(db: Session, query: str) -> dict:
    """Pull the figures an auditor query is likely to need.

    Scoped by the query's subject so the model isn't handed the whole ledger
    for a question about one balance.
    """
    q = query.lower()
    facts: dict = {"as_of_note": "Figures are as at the latest ingested snapshot."}

    wants_cash = any(k in q for k in ("cash", "bank", "liquidity", "going concern", "cheque", "check"))
    wants_ar = any(k in q for k in ("receivable", "debtor", "ecl", "impairment", "credit loss", "aging", "ageing", "provision", "collection"))

    if wants_cash:
        try:
            cash = available_cash(db)
            facts["cash_position"] = {
                "as_of": cash["as_of"],
                "bank_balance_egp": cash["bank_balance_egp"],
                "outstanding_cheques_delivered_egp": cash["less_delivered_checks_egp"],
                "cheques_on_hand_egp": cash["less_on_hand_checks_egp"],
                "net_available_egp": cash["net_available_egp"],
                "by_currency": cash["by_currency"],
            }
        except Exception as exc:
            facts["cash_position_error"] = str(exc)

    if wants_ar:
        try:
            aging = aging_report(db)
            facts["receivables"] = {
                "as_of": aging["snapshot"],
                "total_egp": aging["total_egp"],
                "by_bucket": [
                    {"bucket": b["bucket"], "total_egp": b["total_egp"], "count": b["count"]}
                    for b in aging["buckets"]
                ],
                "by_currency": aging["by_currency"],
                "largest_exposures": aging["customers"][:10],
            }
            facts["rdoh"] = rdoh(db)
        except Exception as exc:
            facts["receivables_error"] = str(exc)

    return facts


def screen_counterparties(db: Session, names: list[str]) -> list[dict]:
    """Check names against the tax evader register."""
    if not names:
        return []
    hits = []
    for name in names:
        cleaned = name.strip()
        if not cleaned:
            continue
        matches = (
            db.query(TaxEvader)
            .filter(or_(TaxEvader.name.ilike(f"%{cleaned}%"),
                        TaxEvader.tax_id == cleaned))
            .all()
        )
        for m in matches:
            hits.append(
                {
                    "queried": cleaned,
                    "matched_name": m.name,
                    "tax_id": m.tax_id,
                    "announced_on": m.announced_on.isoformat() if m.announced_on else None,
                    "source": m.source,
                }
            )
    return hits


# --- The agent -----------------------------------------------------------

def _build_prompt(query_text: str, docs: list[KnowledgeDoc], facts: dict,
                  context: str) -> str:
    refs = "\n\n".join(
        f"[{d.ref}] {d.title}\nframework: {d.framework}\n{d.body}"
        for d in docs
    ) or "(none held)"

    parts = [
        "## REFERENCE MATERIAL",
        "These are the only references you may cite.",
        "",
        refs,
        "",
        "## COMPANY DATA",
        "These are the only company figures you may quote.",
        "",
        json.dumps(facts, indent=2, default=str) if facts else "(none supplied)",
    ]
    if context:
        parts += ["", "## ADDITIONAL CONTEXT FROM THE CONTROLLER", context]
    parts += ["", "## AUDITOR COMMENT / INQUIRY", query_text]
    return "\n".join(parts)


def draft_response(db: Session, query_text: str, context: str = "",
                   area_hint: str = "") -> dict:
    """Draft a response to one auditor query."""
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. The audit assistant needs it; every "
            "other module in this application works without it."
        )

    import anthropic

    search_text = f"{area_hint} {query_text}".strip()
    docs = retrieve(db, search_text)
    facts = company_facts(db, search_text)
    prompt = _build_prompt(query_text, docs, facts, context)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=16000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                # The system prompt and schema are identical on every query, so
                # cache them and pay full price only on the auditor's text.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    )

    if response.stop_reason == "refusal":
        raise RuntimeError(
            "The model declined this request. Rephrase the auditor query, or "
            "escalate to the controller."
        )

    text = next((b.text for b in response.content if b.type == "text"), "")
    result = json.loads(text)

    result["model_used"] = response.model
    result["references_supplied"] = [d.ref for d in docs]
    result["company_data_supplied"] = sorted(facts.keys())
    result["usage"] = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
    }
    return result


def save_query(db: Session, engagement_id: int | None, query_text: str,
               result: dict, reference: str = "", due: date | None = None) -> AuditQuery:
    row = AuditQuery(
        engagement_id=engagement_id,
        reference=reference,
        area=result.get("area", ""),
        query_text=query_text,
        due_date=due,
        draft_response=result.get("draft_response", ""),
        standards_cited=json.dumps(result.get("standards_cited", []), ensure_ascii=False),
        documents_required=json.dumps(result.get("documents_required", []), ensure_ascii=False),
        risk_flags=json.dumps(
            result.get("risk_flags", [])
            + [f"Missing reference: {m}" for m in result.get("missing_references", [])]
            + [f"Data required: {d}" for d in result.get("data_required", [])],
            ensure_ascii=False,
        ),
        model_used=result.get("model_used", ""),
        status="drafted",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def seed_knowledge(db: Session, force: bool = False) -> int:
    from knowledge.seed import SEED_DOCS

    existing = {d.ref for d in db.query(KnowledgeDoc).all()}
    added = 0
    for doc in SEED_DOCS:
        if doc["ref"] in existing and not force:
            continue
        if doc["ref"] in existing:
            row = db.query(KnowledgeDoc).filter(KnowledgeDoc.ref == doc["ref"]).first()
            row.title, row.body, row.tags = doc["title"], doc["body"], doc["tags"]
            row.framework = doc["framework"]
        else:
            db.add(KnowledgeDoc(**doc))
            added += 1
    db.commit()
    return added
