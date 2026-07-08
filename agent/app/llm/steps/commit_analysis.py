"""Commit analysis: which recent commit (if any) caused this alert?

Like the runbook matcher, the schema is constrained at runtime: suspect SHAs
are a Literal enum of the commits actually shown to the model, so it cannot
invent a commit. The load-bearing field is `mechanism` — a commit is only a
suspect if the model can articulate how its diff produces these errors.
"""

import json
from typing import Literal

from pydantic import BaseModel, create_model
from sqlalchemy.orm import Session

from app.config import get_settings
from app.context.git_history import Commit
from app.db.models import Incident
from app.llm.client import run_step

SYSTEM_PROMPT = """You are an incident-response engineer performing initial triage. \
Given a production alert and the recent commits to the affected service, identify \
which commit (if any) most likely caused the failure.

Rules:
- A commit is a suspect only if you can state a mechanism: how its specific \
changed lines produce the observed errors. Correlate the diff with the error \
signature.
- Commit recency and message wording are weak signals. A plausible failure \
mechanism in the diff is a strong signal.
- evidence: pair the relevant diff hunk excerpt with the error line it explains. \
Quote both verbatim from the provided data.
- List at most 3 suspects, ordered most to least likely.
- If no commit plausibly explains the errors, set no_culprit_found to true and \
return an empty suspects list — a deployment is not the only cause of incidents. \
Offer alternative_hypotheses (infrastructure, dependencies, traffic) worth checking."""


class Evidence(BaseModel):
    diff_hunk: str
    error_line: str


def build_output_model(shas: list[str]) -> type[BaseModel]:
    sha_literal = Literal[tuple(shas)]  # type: ignore[valid-type]
    suspect = create_model(
        "SuspectCommit",
        sha=(sha_literal, ...),
        confidence=(Literal["high", "medium", "low"], ...),
        mechanism=(str, ...),
        evidence=(list[Evidence], ...),
    )
    return create_model(
        "CommitAnalysis",
        suspect_commits=(list[suspect], ...),  # type: ignore[valid-type]
        no_culprit_found=(bool, ...),
        alternative_hypotheses=(list[str], ...),
    )


def _render_user_content(
    incident: Incident, commits: list[Commit], error_samples: list[str]
) -> str:
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
        "## Sample error log lines (incident window)",
    ]
    if error_samples:
        lines += [f"- {s}" for s in error_samples[:15]]
    else:
        lines.append("(no error lines captured — alert may be latency- or resource-based)")
    lines.append("")
    lines.append(f"## Recent commits to {incident.service} (newest first)")
    for c in commits:
        lines += [
            "",
            f"### commit {c.sha} — {c.author} — {c.date}",
            f"message: {c.message}",
            "```diff",
            c.diff,
            "```",
        ]
    return "\n".join(lines)


def run(
    session: Session,
    incident: Incident,
    commits: list[Commit],
    error_samples: list[str],
) -> BaseModel | None:
    return run_step(
        session=session,
        incident_id=incident.id,
        step="commit_analysis",
        model=get_settings().model_commit_analysis,
        system=SYSTEM_PROMPT,
        user_content=_render_user_content(incident, commits, error_samples),
        output_model=build_output_model([c.sha for c in commits]),
    )
