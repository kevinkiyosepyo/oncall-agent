import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class IncidentStatus(str, enum.Enum):
    open = "open"
    analyzing = "analyzing"
    briefed = "briefed"
    resolved = "resolved"
    postmortem_complete = "postmortem_complete"


OPEN_STATUSES = (
    IncidentStatus.open,
    IncidentStatus.analyzing,
    IncidentStatus.briefed,
)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    alert_name: Mapped[str] = mapped_column(Text, nullable=False)
    service: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(
            IncidentStatus,
            name="incident_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=IncidentStatus.open,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Denormalized analysis results, filled in by the LLM pipeline (Phase 1+).
    suspect_commit_sha: Mapped[str | None] = mapped_column(Text)
    suspect_confidence: Mapped[str | None] = mapped_column(Text)
    matched_runbook: Mapped[str | None] = mapped_column(Text)
    impact: Mapped[dict | None] = mapped_column(JSONB)
    slack_message_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    postmortem_path: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    timeline: Mapped[list["TimelineEvent"]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="TimelineEvent.occurred_at",
    )

    __table_args__ = (
        # One open incident per alert fingerprint, enforced at the DB level.
        Index(
            "one_open_incident_per_fingerprint",
            "fingerprint",
            unique=True,
            postgresql_where=text(
                "status NOT IN ('resolved', 'postmortem_complete')"
            ),
        ),
    )


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    incident: Mapped[Incident] = relationship(back_populates="timeline")

    __table_args__ = (Index("ix_timeline_incident_time", "incident_id", "occurred_at"),)


class LlmAnalysis(Base):
    __tablename__ = "llm_analyses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    step: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    output: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
