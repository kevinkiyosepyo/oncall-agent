import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.models import Base, Incident
from app.db.session import engine, get_session
from app.ingest.alertmanager import AlertmanagerPayload, process_payload
from app.pipeline import run_analysis_pipeline, run_postmortem_pipeline

log = logging.getLogger("oncall-agent")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    for attempt in range(10):
        try:
            Base.metadata.create_all(engine)
            break
        except OperationalError:
            log.warning("database not ready (attempt %d), retrying...", attempt + 1)
            time.sleep(2)
    else:
        raise RuntimeError("could not connect to database")
    yield


app = FastAPI(title="On-Call Agent", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/webhook/alert")
def receive_alert(
    payload: AlertmanagerPayload,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    results = process_payload(session, payload)
    for r in results:
        log.info(
            "alert %s -> %s (incident %s)",
            r["fingerprint"],
            r["action"],
            r["incident_id"],
        )
        if r["action"] == "created" and r["incident_id"]:
            background_tasks.add_task(
                run_analysis_pipeline, uuid.UUID(r["incident_id"])
            )
        elif r["action"] == "resolved" and r["incident_id"]:
            background_tasks.add_task(
                run_postmortem_pipeline, uuid.UUID(r["incident_id"])
            )
    return {"results": results}


def _incident_summary(inc: Incident) -> dict:
    return {
        "id": str(inc.id),
        "alert_name": inc.alert_name,
        "service": inc.service,
        "severity": inc.severity,
        "status": inc.status.value,
        "started_at": inc.started_at.isoformat(),
        "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
        "suspect_commit_sha": inc.suspect_commit_sha,
        "matched_runbook": inc.matched_runbook,
    }


@app.get("/incidents")
def list_incidents(session: Session = Depends(get_session)):
    incidents = (
        session.execute(select(Incident).order_by(Incident.created_at.desc()))
        .scalars()
        .all()
    )
    return [_incident_summary(i) for i in incidents]


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: uuid.UUID, session: Session = Depends(get_session)):
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    body = _incident_summary(incident)
    body["labels"] = incident.labels
    body["impact"] = incident.impact
    body["timeline"] = [
        {
            "event_type": e.event_type,
            "payload": e.payload,
            "occurred_at": e.occurred_at.isoformat(),
        }
        for e in incident.timeline
    ]
    return body
