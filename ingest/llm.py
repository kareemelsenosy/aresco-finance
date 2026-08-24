"""
One LLM entry point for the ingest layer, with provider fallback.

Order is `LLM_ORDER` in .env (default `openai,anthropic`): the first provider
that has a key gets the call, and if it errors the next one takes it. A provider
with no key is skipped silently rather than failing — so the tool works with one
key configured and gets better when the second is added.

Both backends are asked for the same JSON Schema, so a caller gets the same
shape whichever provider answered. `complete_json` returns (object, meta) and
meta says who actually served it — that goes on screen, because "the AI read
this file" means something different depending on which model did the reading.

Mirrors ai/llm_client.py in the BOQ engine. The difference is fallback: there,
a provider is chosen; here, one is tried and the other catches it.
"""

from __future__ import annotations

import json
import re

from api.config import settings


class NoProviderError(RuntimeError):
    """No configured provider has an API key."""


class AllProvidersFailed(RuntimeError):
    """Every provider with a key raised. Carries what each one said."""

    def __init__(self, failures: list[tuple[str, Exception]]):
        self.failures = failures
        detail = "; ".join(f"{p}: {type(e).__name__}: {e}" for p, e in failures)
        super().__init__(f"Every LLM provider failed — {detail}")


def _order() -> list[str]:
    raw = (getattr(settings, "llm_order", "") or "openai,anthropic")
    out = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return [p for p in out if p in ("openai", "anthropic")] or ["openai", "anthropic"]


def _key_for(provider: str) -> str:
    return (settings.openai_api_key if provider == "openai"
            else settings.anthropic_api_key) or ""


def _model_for(provider: str) -> str:
    return settings.openai_model if provider == "openai" else settings.anthropic_model


def available() -> list[str]:
    """Providers that have a key, in the order they'd be tried."""
    return [p for p in _order() if _key_for(p)]


# Built once and reused. A client per call opens a fresh connection pool each
# time and leaves the old one to be closed by the garbage collector, which under
# an event loop produces spurious teardown errors.
_clients: dict[str, object] = {}


# A read that has not come back inside this is not coming back. Without a
# ceiling the upload request hangs on the provider indefinitely and the fallback
# never gets its turn — the timeout is what makes the fallback reachable.
TIMEOUT_SECONDS = 90
MAX_RETRIES = 1


def _client(provider: str):
    if provider not in _clients:
        if provider == "openai":
            from openai import OpenAI
            _clients[provider] = OpenAI(api_key=settings.openai_api_key,
                                        timeout=TIMEOUT_SECONDS,
                                        max_retries=MAX_RETRIES)
        else:
            import anthropic
            _clients[provider] = anthropic.Anthropic(api_key=settings.anthropic_api_key,
                                                     timeout=TIMEOUT_SECONDS,
                                                     max_retries=MAX_RETRIES)
    return _clients[provider]


def _strip_fences(raw: str) -> str:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    return re.sub(r"\s*```$", "", raw).strip()


# --- OpenAI ---------------------------------------------------------------

def _openai_json(prompt: str, system: str, schema: dict, max_tokens: int) -> str:
    client = _client("openai")
    model = _model_for("openai")
    kwargs = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "repair_plan", "schema": schema, "strict": True},
        },
    }
    # GPT-5 and the o-series renamed max_tokens and reject the old name outright.
    token_param = ("max_completion_tokens"
                   if re.match(r"^(gpt-[5-9]|gpt-\d{2}|o[1-9])", model, re.I)
                   else "max_tokens")
    try:
        resp = client.chat.completions.create(**kwargs, **{token_param: max_tokens})
    except Exception as exc:
        msg = str(exc)
        if "max_tokens" in msg or "max_completion_tokens" in msg:
            other = ("max_tokens" if token_param == "max_completion_tokens"
                     else "max_completion_tokens")
            resp = client.chat.completions.create(**kwargs, **{other: max_tokens})
        elif "response_format" in msg or "json_schema" in msg:
            # Older model without schema mode — ask for a bare JSON object.
            kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs, **{token_param: max_tokens})
        else:
            raise
    return resp.choices[0].message.content or ""


# --- Anthropic ------------------------------------------------------------

def _anthropic_json(prompt: str, system: str, schema: dict, max_tokens: int) -> str:
    resp = _client("anthropic").messages.create(
        model=_model_for("anthropic"),
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("The model declined to read this file.")
    # Never index content[0]: a non-text block can come first.
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


# --- public ---------------------------------------------------------------

def complete_json(prompt: str, system: str, schema: dict,
                  max_tokens: int = 8000) -> tuple[dict, dict]:
    """Ask for one JSON object matching `schema`. Returns (object, meta).

    Tries each provider that has a key, in LLM_ORDER. Raises NoProviderError if
    none is configured, AllProvidersFailed if every one of them errored.
    """
    providers = available()
    if not providers:
        raise NoProviderError(
            "No LLM API key is set. Add OPENAI_API_KEY or ANTHROPIC_API_KEY to .env "
            "for the tool to read a file whose layout it does not already know."
        )

    failures: list[tuple[str, Exception]] = []
    for provider in providers:
        try:
            raw = (_openai_json if provider == "openai" else _anthropic_json)(
                prompt, system, schema, max_tokens)
            obj = json.loads(_strip_fences(raw))
            return obj, {
                "provider": provider,
                "model": _model_for(provider),
                "fell_back": provider != providers[0],
                "failed_first": [
                    {"provider": p, "error": f"{type(e).__name__}: {e}"}
                    for p, e in failures
                ],
            }
        except Exception as exc:
            failures.append((provider, exc))
            continue
    raise AllProvidersFailed(failures)
