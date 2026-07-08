"""Render the postmortem markdown document.

The document skeleton, ordering, and every timestamp come from the database;
the LLM contributes prose sections and per-event annotations only.
"""

import re
from pathlib import Path

from app.db.models import Incident, LlmAnalysis, TimelineEvent
from app.llm.steps.postmortem import Postmortem

_PRIORITY_ORDER = {"p0": 0, "p1": 1, "p2": 2}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render_markdown(
    incident: Incident,
    pm: Postmortem,
    timeline: list[TimelineEvent],
    llm_rows: list[LlmAnalysis],
) -> str:
    notes = {a.event_id: a.note for a in pm.timeline_annotations}
    duration_min = (
        int((incident.resolved_at - incident.started_at).total_seconds() // 60)
        if incident.resolved_at
        else None
    )

    lines = [
        f"# Postmortem: {pm.title}",
        "",
        f"- **Alert:** {incident.alert_name} ({incident.severity})",
        f"- **Service:** {incident.service}",
        f"- **Fired:** {incident.started_at:%Y-%m-%d %H:%M:%S %Z}",
        f"- **Resolved:** "
        + (f"{incident.resolved_at:%Y-%m-%d %H:%M:%S %Z}" if incident.resolved_at else "n/a"),
        f"- **Duration:** {duration_min} minutes" if duration_min is not None else "",
        f"- **Incident ID:** `{incident.id}`",
        "",
        "## Summary",
        "",
        pm.summary,
        "",
        "## Root cause",
        "",
        f"**Hypothesis ({pm.root_cause_confidence} confidence):** {pm.root_cause_hypothesis}",
    ]
    if incident.suspect_commit_sha:
        lines.append(f"\nSuspect commit: `{incident.suspect_commit_sha}`")
    if pm.contributing_factors:
        lines += ["", "### Contributing factors", ""]
        lines += [f"- {f}" for f in pm.contributing_factors]

    if incident.impact:
        imp = incident.impact
        lines += [
            "",
            "## Impact",
            "",
            f"- **Severity:** {imp.get('severity', 'n/a')} · "
            f"**Blast radius:** {imp.get('blast_radius', 'n/a')}",
            f"- **Affected endpoints:** {', '.join(imp.get('affected_endpoints', [])) or 'n/a'}",
            f"- {imp.get('user_impact_summary', '')}",
            f"- {imp.get('estimated_error_rate', '')}",
        ]

    lines += ["", "## Timeline", ""]
    for e in timeline:
        note = notes.get(str(e.id))
        entry = f"- **{e.occurred_at:%H:%M:%S}** — `{e.event_type}`"
        if note:
            entry += f" — {note}"
        lines.append(entry)

    lines += ["", "## Action items", ""]
    for item in sorted(pm.action_items, key=lambda a: _PRIORITY_ORDER[a.priority]):
        lines.append(f"- [ ] **{item.priority}** ({item.category}) {item.description}")

    if pm.lessons_learned:
        lines += ["", "## Lessons learned", ""]
        lines += [f"- {l}" for l in pm.lessons_learned]

    if incident.matched_runbook:
        lines += ["", f"_Runbook used: `runbooks/{incident.matched_runbook}`_"]

    lines += [
        "",
        "---",
        "",
        "<details><summary>LLM analysis runs (this incident)</summary>",
        "",
        "| step | model | in | out | latency | ok |",
        "|---|---|---|---|---|---|",
    ]
    for r in llm_rows:
        lines.append(
            f"| {r.step} | {r.model} | {r.input_tokens or '-'} | "
            f"{r.output_tokens or '-'} | {r.latency_ms}ms | "
            f"{'✅' if r.error is None else '❌'} |"
        )
    lines += ["", "</details>", ""]

    return "\n".join(line for line in lines if line is not None)


def write_postmortem(
    postmortems_dir: str,
    incident: Incident,
    pm: Postmortem,
    timeline: list[TimelineEvent],
    llm_rows: list[LlmAnalysis],
) -> str:
    """Write the rendered document; returns its path (as visible in the repo)."""
    filename = (
        f"{incident.started_at:%Y-%m-%d}-{_slug(incident.alert_name)}"
        f"-{str(incident.id)[:8]}.md"
    )
    out_dir = Path(postmortems_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / filename).write_text(
        render_markdown(incident, pm, timeline, llm_rows), encoding="utf-8"
    )
    return f"postmortems/{filename}"
