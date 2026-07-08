"""Ingest lifecycle against real Postgres: create, dedupe, resolve — and the
partial unique index that enforces one-open-incident-per-fingerprint."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import Incident, IncidentStatus
from app.ingest.alertmanager import AlertmanagerPayload, process_payload

FIXTURES = Path(__file__).parent / "fixtures"


def _payload(name: str) -> AlertmanagerPayload:
    return AlertmanagerPayload.model_validate(
        json.loads((FIXTURES / name).read_text())
    )


def test_firing_creates_incident(db_session):
    results = process_payload(db_session, _payload("alert-firing.json"))

    assert len(results) == 1
    assert results[0]["action"] == "created"

    incident = db_session.execute(select(Incident)).scalar_one()
    assert str(incident.id) == results[0]["incident_id"]
    assert incident.alert_name == "HighErrorRate"
    assert incident.service == "demo-shop"
    assert incident.severity == "critical"
    assert incident.status == IncidentStatus.open
    assert incident.started_at.tzinfo is not None
    assert incident.labels["labels"]["endpoint"] == "/checkout"
    assert [e.event_type for e in incident.timeline] == ["alert_fired"]


def test_refire_dedupes_onto_open_incident(db_session):
    first = process_payload(db_session, _payload("alert-firing.json"))
    second = process_payload(db_session, _payload("alert-firing.json"))

    assert second[0]["action"] == "refired"
    assert second[0]["incident_id"] == first[0]["incident_id"]

    incident = db_session.execute(select(Incident)).scalar_one()  # still one row
    assert [e.event_type for e in incident.timeline] == ["alert_fired", "alert_refired"]


def test_resolve_closes_incident(db_session):
    process_payload(db_session, _payload("alert-firing.json"))
    results = process_payload(db_session, _payload("alert-resolved.json"))

    assert results[0]["action"] == "resolved"
    incident = db_session.execute(select(Incident)).scalar_one()
    assert incident.status == IncidentStatus.resolved
    assert incident.resolved_at is not None
    assert incident.timeline[-1].event_type == "alert_resolved"


def test_unmatched_resolve_is_noop(db_session):
    results = process_payload(db_session, _payload("alert-resolved.json"))

    assert results[0]["action"] == "unmatched_resolve"
    assert results[0]["incident_id"] is None
    assert db_session.execute(select(Incident)).first() is None


def test_new_incident_allowed_after_resolve(db_session):
    process_payload(db_session, _payload("alert-firing.json"))
    process_payload(db_session, _payload("alert-resolved.json"))
    results = process_payload(db_session, _payload("alert-firing.json"))

    assert results[0]["action"] == "created"
    incidents = db_session.execute(select(Incident)).scalars().all()
    assert len(incidents) == 2
    assert sorted(i.status.value for i in incidents) == ["open", "resolved"]


def test_partial_index_blocks_second_open_incident(db_session):
    process_payload(db_session, _payload("alert-firing.json"))
    existing = db_session.execute(select(Incident)).scalar_one()

    db_session.add(
        Incident(
            fingerprint=existing.fingerprint,
            alert_name=existing.alert_name,
            service=existing.service,
            severity=existing.severity,
            started_at=existing.started_at,
            labels={},
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
