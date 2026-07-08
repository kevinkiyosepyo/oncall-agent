"""Analysis pipelines, run as FastAPI BackgroundTasks.

- run_analysis_pipeline: on a new incident — collect context, run the three
  triage steps (runbook match, commit analysis, impact), post the brief.
- run_postmortem_pipeline: on resolution — generate and write the postmortem,
  post the resolution notice.

Design rule: an LLM-step failure degrades the output; it never crashes the
incident. Errors land in llm_analyses and the timeline.
"""

import logging
import uuid

from app.config import get_settings
from app.context.git_history import collect_commits
from app.context.logs import aggregate_logs
from app.context.runbooks import load_runbook_body, load_runbook_index
from app.db.models import Incident, IncidentStatus, LlmAnalysis, TimelineEvent
from app.db.session import SessionLocal
from app.llm.steps import commit_analysis, impact, postmortem, runbook_match
from app.notify.slack import post_brief, post_resolution_notice
from app.report import write_postmortem

log = logging.getLogger("oncall-agent.pipeline")


def _add_event(session, incident_id: uuid.UUID, event_type: str, payload: dict | None = None):
    session.add(
        TimelineEvent(incident_id=incident_id, event_type=event_type, payload=payload or {})
    )


def run_analysis_pipeline(incident_id: uuid.UUID) -> None:
    settings = get_settings()
    session = SessionLocal()
    try:
        incident = session.get(Incident, incident_id)
        if incident is None:
            log.error("pipeline: incident %s not found", incident_id)
            return

        incident.status = IncidentStatus.analyzing
        _add_event(session, incident.id, "analysis_started")
        session.commit()

        # --- context collection (all optional; steps degrade when absent) ---
        commits = collect_commits(settings.demo_repo_dir)
        aggregates = aggregate_logs(settings.demo_log_file, incident.started_at)
        index = load_runbook_index(settings.runbooks_dir)
        error_samples = aggregates["error_samples"] if aggregates else []

        # --- step 1: runbook match ---
        match = None
        if index:
            match = runbook_match.run(session, incident, index)
        if match is not None:
            if match.runbook_path != runbook_match.NO_MATCH:
                incident.matched_runbook = match.runbook_path
            _add_event(
                session,
                incident.id,
                "runbook_matched",
                {
                    "runbook_path": match.runbook_path,
                    "confidence": match.confidence,
                    "reasoning": match.reasoning,
                },
            )
        else:
            _add_event(
                session, incident.id, "error",
                {"step": "runbook_match", "detail": "no result — see llm_analyses"},
            )
        session.commit()

        # --- step 2: commit analysis ---
        commit_result = None
        if commits:
            commit_result = commit_analysis.run(session, incident, commits, error_samples)
        else:
            _add_event(
                session, incident.id, "error",
                {"step": "commit_analysis", "detail": "no git repo available — skipped"},
            )
        if commit_result is not None:
            if commit_result.suspect_commits and not commit_result.no_culprit_found:
                top = commit_result.suspect_commits[0]
                incident.suspect_commit_sha = top.sha
                incident.suspect_confidence = top.confidence
            _add_event(
                session,
                incident.id,
                "commit_identified",
                {
                    "no_culprit_found": commit_result.no_culprit_found,
                    "suspects": [
                        {"sha": s.sha, "confidence": s.confidence, "mechanism": s.mechanism}
                        for s in commit_result.suspect_commits
                    ],
                },
            )
        elif commits:
            _add_event(
                session, incident.id, "error",
                {"step": "commit_analysis", "detail": "no result — see llm_analyses"},
            )
        session.commit()

        # --- step 3: impact estimate ---
        impact_result = None
        if aggregates:
            impact_result = impact.run(session, incident, aggregates)
        else:
            _add_event(
                session, incident.id, "error",
                {"step": "impact", "detail": "no log data available — skipped"},
            )
        if impact_result is not None:
            incident.impact = impact_result.model_dump()
            _add_event(
                session,
                incident.id,
                "impact_estimated",
                {
                    "severity": impact_result.severity,
                    "blast_radius": impact_result.blast_radius,
                    "summary": impact_result.user_impact_summary,
                },
            )
        elif aggregates:
            _add_event(
                session, incident.id, "error",
                {"step": "impact", "detail": "no result — see llm_analyses"},
            )
        session.commit()

        # --- brief ---
        channel = post_brief(incident, match, commit_result, impact_result)
        incident.slack_message_sent = channel == "slack"
        _add_event(session, incident.id, "brief_posted", {"channel": channel})
        incident.status = IncidentStatus.briefed
        session.commit()
        log.info("incident %s briefed via %s", incident.id, channel)
    except Exception:
        log.exception("pipeline failed for incident %s", incident_id)
        session.rollback()
        try:
            _add_event(
                session, incident_id, "error",
                {"step": "pipeline", "detail": "unhandled exception — see agent logs"},
            )
            session.commit()
        except Exception:
            log.exception("could not record pipeline error event for %s", incident_id)
    finally:
        session.close()


def run_postmortem_pipeline(incident_id: uuid.UUID) -> None:
    settings = get_settings()
    session = SessionLocal()
    try:
        incident = session.get(Incident, incident_id)
        if incident is None:
            log.error("postmortem: incident %s not found", incident_id)
            return

        timeline = list(incident.timeline)
        analyses = postmortem.latest_analyses(session, incident)
        runbook_body = (
            load_runbook_body(settings.runbooks_dir, incident.matched_runbook)
            if incident.matched_runbook
            else None
        )

        pm = postmortem.run(session, incident, timeline, analyses, runbook_body)

        path = None
        if pm is not None:
            llm_rows = (
                session.query(LlmAnalysis)
                .filter(LlmAnalysis.incident_id == incident.id)
                .order_by(LlmAnalysis.id)
                .all()
            )
            path = write_postmortem(
                settings.postmortems_dir, incident, pm, timeline, llm_rows
            )
            incident.postmortem_path = path
            incident.status = IncidentStatus.postmortem_complete
            _add_event(session, incident.id, "postmortem_generated", {"path": path})
        else:
            _add_event(
                session, incident.id, "error",
                {"step": "postmortem", "detail": "no result — see llm_analyses"},
            )
        session.commit()

        channel = post_resolution_notice(incident, path, pm)
        session.commit()
        log.info(
            "incident %s resolution notice via %s (postmortem: %s)",
            incident.id, channel, path or "failed",
        )
    except Exception:
        log.exception("postmortem pipeline failed for incident %s", incident_id)
        session.rollback()
    finally:
        session.close()
