"""Shared Claude API access for all pipeline steps.

Every call goes through run_step(), which records one llm_analyses row per
call — model, tokens, latency, parsed output or error — so cost and behavior
are inspectable per incident, per step.
"""

import logging
import os
import time
import uuid

import anthropic
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import LlmAnalysis

log = logging.getLogger("oncall-agent.llm")

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # docker-compose's `${VAR:-}` pattern sets unset vars to empty strings,
        # and an empty ANTHROPIC_API_KEY still wins the SDK's credential
        # precedence (authenticating with an empty key -> 401). Drop empties.
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            if os.environ.get(var) == "":
                del os.environ[var]

        kwargs = {}
        # OAuth tokens (e.g. from `ant auth print-credentials`) need the oauth
        # beta header on /v1/messages; API keys must not send it.
        if not os.environ.get("ANTHROPIC_API_KEY") and os.environ.get(
            "ANTHROPIC_AUTH_TOKEN"
        ):
            kwargs["default_headers"] = {"anthropic-beta": "oauth-2025-04-20"}
        _client = anthropic.Anthropic(**kwargs)
    return _client


def run_step(
    session: Session,
    incident_id: uuid.UUID,
    step: str,
    model: str,
    system: str,
    user_content: str,
    output_model: type[BaseModel],
    max_tokens: int = 4096,
) -> BaseModel | None:
    """Run one structured-output LLM step; returns the parsed model or None.

    Failures never raise — they are recorded on the llm_analyses row so the
    pipeline can degrade instead of crashing the incident.
    """
    row = LlmAnalysis(incident_id=incident_id, step=step, model=model)
    started = time.monotonic()
    parsed: BaseModel | None = None
    try:
        response = get_client().messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
            output_format=output_model,
        )
        row.input_tokens = response.usage.input_tokens
        row.output_tokens = response.usage.output_tokens
        if response.stop_reason == "refusal":
            explanation = (
                response.stop_details.explanation if response.stop_details else ""
            )
            row.error = f"refusal: {explanation}"
        elif response.stop_reason == "max_tokens":
            row.error = f"truncated at max_tokens={max_tokens}"
        else:
            parsed = response.parsed_output
            row.output = parsed.model_dump()
    except anthropic.APIStatusError as e:
        row.error = f"api error {e.status_code}: {e}"
    except anthropic.APIConnectionError as e:
        row.error = f"connection error: {e}"
    except Exception as e:  # schema validation, unexpected response shape
        row.error = f"{type(e).__name__}: {e}"

    row.latency_ms = int((time.monotonic() - started) * 1000)
    session.add(row)
    session.commit()

    if row.error:
        log.error("llm step %s failed for incident %s: %s", step, incident_id, row.error)
    else:
        log.info(
            "llm step %s ok for incident %s (%s, in=%s out=%s tokens, %sms)",
            step, incident_id, model, row.input_tokens, row.output_tokens, row.latency_ms,
        )
    return parsed
