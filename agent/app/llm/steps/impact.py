"""Impact / blast-radius estimation from precomputed traffic aggregates.

The model interprets numbers computed in Python (context/logs.py); it never
counts raw log lines itself.
"""

import json
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Incident
from app.llm.client import run_step

SYSTEM_PROMPT = """You are an incident-response engineer estimating the user impact of \
a production incident from traffic and error aggregates.

Rules:
- Ground every judgment in the numbers provided; quote them. Do not invent \
metrics that are not present.
- The `incident` window numbers ARE the incident; when citing an error rate, \
use the relevant `error_rate_pct` value verbatim. The `baseline` window is \
only the before-picture for comparison — never merge baseline and incident \
counts into a combined rate.
- Attribute rates to the level they were measured at: a per-endpoint rate \
comes from that endpoint's own numbers, never from the service-wide totals. \
Say "X% of <endpoint> requests" only when using that endpoint's numbers.
- Compare the incident window against the baseline window when both exist.
- Weigh user-facing endpoints (checkout, purchase flows) far more heavily \
than browse or internal endpoints.
- Distinguish degraded (elevated errors or latency, most requests succeed) \
from hard-down (most requests fail).
- severity: sev1 = user-facing hard-down or revenue path broken; sev2 = \
significant degradation or partial user-facing failure; sev3 = minor or \
non-user-facing degradation.
- user_impact_summary: one plain-language sentence a non-engineer can read.
- estimated_error_rate: cite the actual numbers, e.g. "~70% of checkout \
requests failing (baseline 0%)". For latency incidents, cite latency instead."""


class ImpactEstimate(BaseModel):
    severity: Literal["sev1", "sev2", "sev3"]
    blast_radius: Literal["single_endpoint", "service_wide", "cross_service_risk"]
    affected_endpoints: list[str]
    user_impact_summary: str
    estimated_error_rate: str
    reasoning: str


def _render_user_content(incident: Incident, aggregates: dict) -> str:
    annotations = incident.labels.get("annotations", {})
    return "\n".join(
        [
            "## Alert",
            f"name: {incident.alert_name}",
            f"service: {incident.service}",
            f"summary: {annotations.get('summary', '')}",
            "",
            "## Traffic aggregates (computed from access logs)",
            "Baseline = before the alert condition began; incident = after.",
            "```json",
            json.dumps(aggregates, indent=1, sort_keys=True),
            "```",
        ]
    )


def run(session: Session, incident: Incident, aggregates: dict) -> BaseModel | None:
    return run_step(
        session=session,
        incident_id=incident.id,
        step="impact",
        model=get_settings().model_impact,
        system=SYSTEM_PROMPT,
        user_content=_render_user_content(incident, aggregates),
        output_model=ImpactEstimate,
        max_tokens=2048,
    )
