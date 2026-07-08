"""Runbook matching: pick the right runbook for an alert, or NO_MATCH.

The output schema's runbook_path field is an enum built at runtime from the
actual files in runbooks/ — an invented runbook path is structurally
impossible, not just discouraged.
"""

import json
from typing import Literal

from pydantic import BaseModel, create_model
from sqlalchemy.orm import Session

from app.config import get_settings
from app.context.runbooks import Runbook
from app.db.models import Incident
from app.llm.client import run_step

NO_MATCH = "NO_MATCH"

SYSTEM_PROMPT = """You are an incident-response engineer triaging a production alert. \
Match the alert to the most relevant runbook from the provided index.

Rules:
- Choose a runbook only if its stated failure mode clearly covers this alert. \
Match on the failure mechanism, not on surface keyword overlap.
- If no runbook applies, choose NO_MATCH. A wrong runbook actively misleads \
the on-call engineer and is worse than none.
- reasoning: one or two sentences citing the specific labels or annotations \
that drove your choice.
- first_actions: 2-4 immediate, concrete steps for the on-call engineer, \
grounded in the alert details and the chosen runbook's summary. Imperative \
voice, one line each. If you chose NO_MATCH, base them on the alert alone."""


def build_output_model(runbook_paths: list[str]) -> type[BaseModel]:
    choices = tuple(runbook_paths) + (NO_MATCH,)
    return create_model(
        "RunbookMatch",
        runbook_path=(Literal[choices], ...),  # type: ignore[valid-type]
        confidence=(Literal["high", "medium", "low"], ...),
        reasoning=(str, ...),
        first_actions=(list[str], ...),
    )


def _render_user_content(incident: Incident, index: list[Runbook]) -> str:
    labels = incident.labels.get("labels", {})
    annotations = incident.labels.get("annotations", {})
    lines = [
        "## Alert",
        f"name: {incident.alert_name}",
        f"service: {incident.service}",
        f"severity: {incident.severity}",
        f"labels: {json.dumps(labels, sort_keys=True)}",
        f"annotations: {json.dumps(annotations, sort_keys=True)}",
        "",
        "## Runbook index",
    ]
    for rb in index:
        lines.append(f"- {rb.path} — {rb.title}: {rb.summary}")
        if rb.applies_to:
            lines.append(f"  (intended for alerts like: {rb.applies_to})")
    return "\n".join(lines)


def run(session: Session, incident: Incident, index: list[Runbook]) -> BaseModel | None:
    return run_step(
        session=session,
        incident_id=incident.id,
        step="runbook_match",
        model=get_settings().model_runbook_match,
        system=SYSTEM_PROMPT,
        user_content=_render_user_content(incident, index),
        output_model=build_output_model([rb.path for rb in index]),
    )
