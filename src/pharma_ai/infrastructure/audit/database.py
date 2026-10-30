"""The real audit backend: rows in the append-only ``audit_events`` table (sprint task S1-11).

Every call opens its own short transaction and commits before returning. If the database cannot
be reached or refuses the row, ``AuditUnavailableError`` is raised and the caller must not carry
on with the action (fail closed): an action with no audit record is worse than a refused action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from pharma_ai.core.config import Settings
from pharma_ai.infrastructure.audit.service import (
    AuditActor,
    AuditEvent,
    AuditLogService,
    AuditUnavailableError,
    clean_filter,
    format_timestamp,
    validate_event_fields,
)
from pharma_ai.infrastructure.db.models import AuditEvent as AuditEventRow
from pharma_ai.infrastructure.db.session import make_engine, make_session_factory


def _escape_like(text: str) -> str:
    """Make %, _ and \\ in user input match literally in a LIKE pattern."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_api(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        event_id=str(row.event_id),
        timestamp=format_timestamp(row.created_at),
        actor_id=row.actor_email or row.actor_id,
        actor_subject=row.actor_id,
        actor_email=row.actor_email,
        actor_role=row.actor_role,
        actor_roles=list(row.actor_roles or []),
        project_id=row.project_id,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        resource_version=row.resource_version,
        outcome=row.outcome,
        details=dict(row.details or {}),
    )


class DatabaseAuditLogService(AuditLogService):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @classmethod
    def from_settings(cls, cfg: Settings) -> DatabaseAuditLogService:
        """Build the service without connecting: the connection is made on first use."""
        return cls(make_session_factory(make_engine(cfg.database_url)))

    def ping(self) -> None:
        """Raise AuditUnavailableError if the database cannot be reached (used by /ready)."""
        try:
            with self._session_factory() as session:
                session.execute(select(1))
        except SQLAlchemyError as exc:
            raise AuditUnavailableError("database unavailable") from exc

    def record_event(
        self,
        *,
        actor: AuditActor,
        action: str,
        resource_type: str,
        project_id: str | None = None,
        resource_id: str | None = None,
        resource_version: str | None = None,
        outcome: str = "SUCCESS",
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        validate_event_fields(action, resource_type, outcome)
        row = AuditEventRow(
            actor_id=actor.subject,
            actor_email=actor.email,
            actor_role=actor.role_label,
            actor_roles=list(actor.roles),
            project_id=project_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_version=resource_version,
            outcome=outcome,
            details=dict(details or {}),
        )
        try:
            with self._session_factory() as session:
                session.add(row)
                session.commit()
                session.refresh(row)  # picks up the event ID and the database's own timestamp
                return _to_api(row)
        except SQLAlchemyError as exc:
            raise AuditUnavailableError("audit event could not be stored") from exc

    def list_events(
        self,
        *,
        project_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        outcome: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AuditEvent], int]:
        project_id, action, outcome = (clean_filter(v) for v in (project_id, action, outcome))
        actor = clean_filter(actor)
        resource_type, resource_id = clean_filter(resource_type), clean_filter(resource_id)

        conditions = []
        if project_id:
            conditions.append(AuditEventRow.project_id == project_id)
        if actor:
            pattern = f"%{_escape_like(actor)}%"
            conditions.append(
                or_(
                    AuditEventRow.actor_id.ilike(pattern, escape="\\"),
                    AuditEventRow.actor_email.ilike(pattern, escape="\\"),
                )
            )
        if action:
            conditions.append(AuditEventRow.action == action)
        if outcome:
            conditions.append(AuditEventRow.outcome == outcome)
        if resource_type:
            conditions.append(AuditEventRow.resource_type == resource_type)
        if resource_id:
            conditions.append(AuditEventRow.resource_id == resource_id)
        if since:
            conditions.append(AuditEventRow.created_at >= since)
        if until:
            conditions.append(AuditEventRow.created_at <= until)

        try:
            with self._session_factory() as session:
                total = session.scalar(
                    select(func.count()).select_from(AuditEventRow).where(*conditions)
                )
                rows = session.scalars(
                    select(AuditEventRow)
                    .where(*conditions)
                    .order_by(AuditEventRow.created_at.desc(), AuditEventRow.event_id.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
                return [_to_api(r) for r in rows], int(total or 0)
        except SQLAlchemyError as exc:
            raise AuditUnavailableError("audit events could not be read") from exc
