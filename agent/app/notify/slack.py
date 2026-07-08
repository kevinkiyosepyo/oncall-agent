"""Incident brief delivery: Slack incoming webhook, with a console fallback.

When SLACK_WEBHOOK_URL is unset (or the webhook fails), the rendered message
is logged to stdout instead — the full demo loop works without a Slack
workspace, and a Slack outage can't lose a brief.
"""

import logging

import httpx
from pydantic import BaseModel

from app.config import get_settings
from app.db.models import Incident
from app.llm.steps.runbook_match import NO_MATCH

log = logging.getLogger("oncall-agent.notify")

_SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟠", "info": "🔵"}

_UNAVAILABLE = "⚠️ unavailable (analysis step failed)"


def _runbook_section(match: BaseModel | None) -> tuple[str, list[str]]:
    if match is None:
        return f"*Runbook:* {_UNAVAILABLE}", []
    if match.runbook_path == NO_MATCH:
        return (
            f"*Runbook:* no match ({match.confidence} confidence). {match.reasoning}",
            match.first_actions,
        )
    return (
        f"*Runbook:* 📖 `runbooks/{match.runbook_path}` "
        f"({match.confidence} confidence)\n{match.reasoning}",
        match.first_actions,
    )


def _commit_section(commit_analysis: BaseModel | None) -> str:
    if commit_analysis is None:
        return f"*Suspect commit:* {_UNAVAILABLE}"
    if commit_analysis.no_culprit_found or not commit_analysis.suspect_commits:
        alts = "; ".join(commit_analysis.alternative_hypotheses[:3])
        return f"*Suspect commit:* none identified. Alternatives to check: {alts}"
    top = commit_analysis.suspect_commits[0]
    lines = [
        f"*Suspect commit:* 🔎 `{top.sha}` ({top.confidence} confidence)",
        top.mechanism,
    ]
    if top.evidence:
        ev = top.evidence[0]
        lines.append(f"> diff: `{ev.diff_hunk.strip()[:160]}`")
        lines.append(f"> error: `{ev.error_line.strip()[:160]}`")
    others = commit_analysis.suspect_commits[1:]
    if others:
        lines.append(
            "also suspect: " + ", ".join(f"`{s.sha}` ({s.confidence})" for s in others)
        )
    return "\n".join(lines)


def _impact_section(impact: BaseModel | None) -> str:
    if impact is None:
        return f"*Impact:* {_UNAVAILABLE}"
    return "\n".join(
        [
            f"*Impact:* 💥 {impact.severity} · {impact.blast_radius} · "
            f"affected: {', '.join(impact.affected_endpoints) or 'n/a'}",
            impact.user_impact_summary,
            impact.estimated_error_rate,
        ]
    )


def _post(payload: dict, console_text: str) -> str:
    """Deliver to Slack if configured, else console; returns the channel used."""
    url = get_settings().slack_webhook_url
    if url:
        try:
            resp = httpx.post(url, json=payload, timeout=10.0)
            if resp.status_code == 200:
                return "slack"
            log.error("slack webhook returned %s: %s", resp.status_code, resp.text)
        except httpx.HTTPError as e:
            log.error("slack webhook failed: %s", e)
        # fall through — the message must never be lost
    log.info("%s", console_text)
    return "console"


def _mrkdwn_block(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def post_brief(
    incident: Incident,
    match: BaseModel | None,
    commit_analysis: BaseModel | None = None,
    impact: BaseModel | None = None,
) -> str:
    annotations = incident.labels.get("annotations", {})
    labels = incident.labels.get("labels", {})
    emoji = _SEVERITY_EMOJI.get(incident.severity, "⚪")
    title = f"{incident.alert_name} — {incident.service}"
    endpoint = labels.get("endpoint", "")
    footer = f"incident {incident.id} · fired {incident.started_at:%Y-%m-%d %H:%M:%S %Z}"

    runbook_text, actions = _runbook_section(match)
    commit_text = _commit_section(commit_analysis)
    impact_text = _impact_section(impact)

    fields = [
        {"type": "mrkdwn", "text": f"*Severity:*\n{incident.severity}"},
        {"type": "mrkdwn", "text": f"*Service:*\n{incident.service}"},
    ]
    if endpoint:
        fields.append({"type": "mrkdwn", "text": f"*Endpoint:*\n`{endpoint}`"})

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} {title}"},
        },
        {"type": "section", "fields": fields},
    ]
    if annotations.get("summary"):
        blocks.append(_mrkdwn_block(annotations["summary"]))
    blocks.append(_mrkdwn_block(impact_text))
    blocks.append(_mrkdwn_block(commit_text))
    blocks.append(_mrkdwn_block(runbook_text))
    if actions:
        numbered = "\n".join(f"{i}. {a}" for i, a in enumerate(actions, 1))
        blocks.append(_mrkdwn_block(f"*First actions:*\n{numbered}"))
    blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]}
    )
    payload = {"text": f"[{incident.severity}] {title}", "blocks": blocks}

    sep = "-" * 62
    console_lines = [
        "",
        "=" * 62,
        f"{emoji} INCIDENT BRIEF: {title}",
        "=" * 62,
        f"severity: {incident.severity}"
        + (f"   endpoint: {endpoint}" if endpoint else ""),
    ]
    if annotations.get("summary"):
        console_lines.append(annotations["summary"])
    console_lines += [sep, impact_text, sep, commit_text, sep, runbook_text]
    if actions:
        console_lines.append("First actions:")
        console_lines += [f"  {i}. {a}" for i, a in enumerate(actions, 1)]
    console_lines += [sep, footer, "=" * 62]

    return _post(payload, "\n".join(console_lines))


def post_resolution_notice(
    incident: Incident, postmortem_path: str | None, pm: BaseModel | None
) -> str:
    duration = ""
    if incident.resolved_at and incident.started_at:
        minutes = int((incident.resolved_at - incident.started_at).total_seconds() // 60)
        duration = f" after {minutes}m"

    title = f"✅ Resolved{duration}: {incident.alert_name} — {incident.service}"
    lines = [title]
    if pm is not None:
        lines.append(
            f"Root cause ({pm.root_cause_confidence} confidence): "
            f"{pm.root_cause_hypothesis}"
        )
    if postmortem_path:
        lines.append(f"📄 Postmortem: {postmortem_path}")
    else:
        lines.append("⚠️ Postmortem generation failed — see agent logs")

    payload = {
        "text": title,
        "blocks": [_mrkdwn_block("\n".join(lines))],
    }
    console_text = "\n".join(["", "=" * 62, *lines, "=" * 62])
    return _post(payload, console_text)
