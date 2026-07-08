"""Parsing and ingest of Prometheus Alertmanager webhook payloads.

The chaos injector sends this exact format, so a real Alertmanager is a
drop-in replacement later.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Incident, IncidentStatus, OPEN_STATUSES, TimelineEvent


class AlertmanagerAlert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime
    endsAt: datetime | None = None
    fingerprint: str


class AlertmanagerPayload(BaseModel):
    version: str = "4"
    groupKey: str = ""
    status: str = "firing"
    receiver: str = ""
    alerts: list[AlertmanagerAlert]


def _find_open_incident(session: Session, fingerprint: str) -> Incident | None:
    return session.execute(
        select(Incident).where(
            Incident.fingerprint == fingerprint,
            Incident.status.in_(OPEN_STATUSES),
        )
    ).scalar_one_or_none()


def _handle_firing(session: Session, alert: AlertmanagerAlert) -> tuple[Incident, str]:
    existing = _find_open_incident(session, alert.fingerprint)
    if existing is not None:
        existing.timeline.append(
            TimelineEvent(
                event_type="alert_refired",
                payload={"labels": alert.labels, "annotations": alert.annotations},
            )
        )
        session.commit()
        return existing, "refired"

    incident = Incident(
        fingerprint=alert.fingerprint,
        alert_name=alert.labels.get("alertname", "unknown"),
        service=alert.labels.get("service", alert.labels.get("job", "unknown")),
        severity=alert.labels.get("severity", "unknown"),
        started_at=alert.startsAt,
        labels={"labels": alert.labels, "annotations": alert.annotations},
    )
    incident.timeline.append(
        TimelineEvent(
            event_type="alert_fired",
            payload={"labels": alert.labels, "annotations": alert.annotations},
        )
    )
    session.add(incident)
    try:
        session.commit()
    except IntegrityError:
        # Lost a race against a concurrent firing of the same alert — the
        # partial unique index caught it; record a refire on the winner.
        session.rollback()
        winner = _find_open_incident(session, alert.fingerprint)
        if winner is None:
            raise
        winner.timeline.append(
            TimelineEvent(
                event_type="alert_refired",
                payload={"labels": alert.labels, "annotations": alert.annotations},
            )
        )
        session.commit()
        return winner, "refired"
    return incident, "created"


def _handle_resolved(
    session: Session, alert: AlertmanagerAlert
) -> tuple[Incident | None, str]:
    incident = _find_open_incident(session, alert.fingerprint)
    if incident is None:
        return None, "unmatched_resolve"

    incident.status = IncidentStatus.resolved
    incident.resolved_at = alert.endsAt or datetime.now(timezone.utc)
    incident.timeline.append(
        TimelineEvent(event_type="alert_resolved", payload={"labels": alert.labels})
    )
    session.commit()
    return incident, "resolved"


def process_payload(
    session: Session, payload: AlertmanagerPayload
) -> list[dict[str, str | None]]:
    """Route each alert in the payload; returns one action record per alert."""
    results: list[dict[str, str | None]] = []
    for alert in payload.alerts:
        if alert.status == "firing":
            incident, action = _handle_firing(session, alert)
        else:
            incident, action = _handle_resolved(session, alert)
        results.append(
            {
                "fingerprint": alert.fingerprint,
                "action": action,
                "incident_id": str(incident.id) if incident else None,
            }
        )
    return results
