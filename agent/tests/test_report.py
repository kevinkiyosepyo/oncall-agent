"""Postmortem rendering: skeleton and timestamps come from the DB rows, model
prose slots into the sections, action items sort by priority."""

import uuid
from datetime import datetime, timezone

from app.db.models import Incident, IncidentStatus, LlmAnalysis, TimelineEvent
from app.llm.steps.postmortem import Postmortem
from app.report import render_markdown, write_postmortem

T0 = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 7, 2, 12, 14, 0, tzinfo=timezone.utc)


def _incident() -> Incident:
    return Incident(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        fingerprint="fp",
        alert_name="HighErrorRate",
        service="demo-shop",
        severity="critical",
        status=IncidentStatus.resolved,
        started_at=T0,
        resolved_at=T1,
        labels={},
        suspect_commit_sha="abc1234",
        matched_runbook="high-error-rate.md",
        impact={
            "severity": "sev1",
            "blast_radius": "single_endpoint",
            "affected_endpoints": ["POST /checkout"],
            "user_impact_summary": "Checkout failing for most users.",
            "estimated_error_rate": "~70% of checkout requests failing",
        },
    )


def _postmortem() -> Postmortem:
    return Postmortem(
        title="Checkout KeyError",
        summary="Checkout broke.",
        root_cause_hypothesis="Unsafe dict access in pricing.",
        root_cause_confidence="high",
        contributing_factors=["no tests for missing codes"],
        timeline_annotations=[{"event_id": "11", "note": "triage began here"}],
        action_items=[
            {"description": "later thing", "category": "detect", "priority": "p2"},
            {"description": "fix it now", "category": "prevent", "priority": "p0"},
        ],
        lessons_learned=["validate lookups on revenue paths"],
    )


def _timeline() -> list[TimelineEvent]:
    return [
        TimelineEvent(id=10, event_type="alert_fired", payload={}, occurred_at=T0),
        TimelineEvent(id=11, event_type="analysis_started", payload={}, occurred_at=T0),
        TimelineEvent(id=12, event_type="alert_resolved", payload={}, occurred_at=T1),
    ]


def test_render_pulls_structure_from_db_and_prose_from_model():
    md = render_markdown(_incident(), _postmortem(), _timeline(), [])

    assert "# Postmortem: Checkout KeyError" in md
    assert "**Duration:** 14 minutes" in md
    assert "**Hypothesis (high confidence):** Unsafe dict access in pricing." in md
    assert "Suspect commit: `abc1234`" in md
    assert "~70% of checkout requests failing" in md
    # timeline timestamps come from the rows; annotation matched by id
    assert "- **12:00:00** — `analysis_started` — triage began here" in md
    assert "- **12:14:00** — `alert_resolved`" in md
    assert "_Runbook used: `runbooks/high-error-rate.md`_" in md


def test_action_items_sorted_by_priority():
    md = render_markdown(_incident(), _postmortem(), _timeline(), [])

    assert md.index("**p0** (prevent) fix it now") < md.index("**p2** (detect) later thing")


def test_llm_appendix_rows():
    rows = [
        LlmAnalysis(
            step="runbook_match", model="claude-haiku-4-5",
            input_tokens=1000, output_tokens=200, latency_ms=3500, output={},
        ),
        LlmAnalysis(step="impact", model="claude-haiku-4-5", latency_ms=10, error="boom"),
    ]
    md = render_markdown(_incident(), _postmortem(), _timeline(), rows)

    assert "| runbook_match | claude-haiku-4-5 | 1000 | 200 | 3500ms | ✅ |" in md
    assert "| impact | claude-haiku-4-5 | - | - | 10ms | ❌ |" in md


def test_write_postmortem_names_file_from_incident(tmp_path):
    path = write_postmortem(str(tmp_path), _incident(), _postmortem(), _timeline(), [])

    assert path == "postmortems/2026-07-02-higherrorrate-00000000.md"
    assert (tmp_path / "2026-07-02-higherrorrate-00000000.md").exists()
