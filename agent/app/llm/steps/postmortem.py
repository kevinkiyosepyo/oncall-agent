"""Postmortem generation on incident resolution.

The model produces structured sections; the markdown document is rendered by
a Python template (app/report.py) with the timeline and timestamps sourced
from the database — every postmortem has identical structure, and the model
cannot mis-transcribe a timestamp it never renders.
"""

import json
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Incident, LlmAnalysis, TimelineEvent
from app.llm.client import run_step

SYSTEM_PROMPT = """You are an incident scribe writing the post-incident review for a \
resolved production incident. You are given the full incident record: the alert, \
the timeline of events, the triage analyses produced during the incident, and the \
matched runbook.

Rules:
- Use only facts present in the provided record. Do not invent metrics, people, \
systems, or events.
- root_cause_hypothesis: state it as a hypothesis unless the evidence is \
conclusive; root_cause_confidence reflects how strong the evidence chain is.
- summary: 3-5 sentences an engineer who missed the incident can read cold: what \
broke, who/what was affected, why (hypothesis), how it was resolved.
- timeline_annotations: a one-line plain-language note for timeline events worth \
explaining. Reference events by their [id] exactly as shown. Annotate only \
noteworthy events, not every row.
- action_items: concrete and checkable, not vague ("add an integration test that \
checkouts without a discount code succeed", not "improve testing"). category: \
prevent = stops recurrence; detect = catches it faster; mitigate = reduces blast \
when it recurs.
- lessons_learned: 2-4 process-level observations, not restatements of the fix."""


class ActionItem(BaseModel):
    description: str
    category: Literal["prevent", "detect", "mitigate"]
    priority: Literal["p0", "p1", "p2"]


class TimelineAnnotation(BaseModel):
    event_id: str
    note: str


class Postmortem(BaseModel):
    title: str
    summary: str
    root_cause_hypothesis: str
    root_cause_confidence: Literal["high", "medium", "low"]
    contributing_factors: list[str]
    timeline_annotations: list[TimelineAnnotation]
    action_items: list[ActionItem]
    lessons_learned: list[str]


def _compact(payload: dict, limit: int = 300) -> str:
    text = json.dumps(payload, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "...}"


def _render_user_content(
    incident: Incident,
    timeline: list[TimelineEvent],
    analyses: dict[str, dict],
    runbook_body: str | None,
) -> str:
    duration_min = (
        int((incident.resolved_at - incident.started_at).total_seconds() // 60)
        if incident.resolved_at
        else None
    )
    lines = [
        "## Incident",
        f"alert: {incident.alert_name}",
        f"service: {incident.service}",
        f"severity (alert label): {incident.severity}",
        f"fired: {incident.started_at.isoformat()}",
        f"resolved: {incident.resolved_at.isoformat() if incident.resolved_at else 'n/a'}",
        f"duration_minutes: {duration_min}",
        f"labels: {json.dumps(incident.labels.get('labels', {}), sort_keys=True)}",
        f"annotations: {json.dumps(incident.labels.get('annotations', {}), sort_keys=True)}",
        "",
        "## Timeline (authoritative, from the incident database)",
    ]
    for e in timeline:
        lines.append(
            f"[{e.id}] {e.occurred_at.isoformat()} {e.event_type} {_compact(e.payload)}"
        )
    lines.append("")
    lines.append("## Triage analyses produced during the incident")
    for step, output in analyses.items():
        lines += [f"### {step}", "```json", json.dumps(output, sort_keys=True), "```"]
    if runbook_body:
        lines += ["", "## Matched runbook (full text)", runbook_body]
    return "\n".join(lines)


def run(
    session: Session,
    incident: Incident,
    timeline: list[TimelineEvent],
    analyses: dict[str, dict],
    runbook_body: str | None,
) -> Postmortem | None:
    return run_step(
        session=session,
        incident_id=incident.id,
        step="postmortem",
        model=get_settings().model_postmortem,
        system=SYSTEM_PROMPT,
        user_content=_render_user_content(incident, timeline, analyses, runbook_body),
        output_model=Postmortem,
        max_tokens=8192,
    )


def latest_analyses(session: Session, incident: Incident) -> dict[str, dict]:
    """Most recent successful output per step, for the postmortem context."""
    analyses: dict[str, dict] = {}
    rows = (
        session.query(LlmAnalysis)
        .filter(
            LlmAnalysis.incident_id == incident.id,
            LlmAnalysis.output.isnot(None),
            LlmAnalysis.step != "postmortem",
        )
        .order_by(LlmAnalysis.id)
        .all()
    )
    for row in rows:
        analyses[row.step] = row.output  # later rows overwrite earlier ones
    return analyses
