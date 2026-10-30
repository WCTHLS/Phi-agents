"""Audit trail service (sprint task S1-11).

Plain language: an audit trail is the permanent record that lets an auditor reconstruct who did
what, to which object, when, and with what result. It is kept apart from operational logging
(which developers read to fix bugs and which may be deleted).

Two implementations sit behind one interface:

- ``DatabaseAuditLogService`` (``database.py``): the real one. Rows go into the append-only
  ``audit_events`` table, whose triggers refuse any change or delete.
- ``InMemoryAuditLogService`` (this file): a list in memory, for automated tests and for running
  the API without a database on a developer machine. It is refused when ``ENVIRONMENT`` is not
  ``local`` or ``test``, so it can never silently replace the real trail in production.

Events are written by the server itself when real actions happen (see ``recorder.py``). The
identity always comes from the verified login token, never from the request body.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from pharma_ai.core.config import Settings, settings

if TYPE_CHECKING:
    from pharma_ai.infrastructure.security.auth import Principal

OUTCOMES = ("SUCCESS", "DENIED", "FAILURE")
MEMORY_ALLOWED_ENVIRONMENTS = frozenset({"local", "test"})
ROLE_COLUMN_LENGTH = 64  # width of the actor_role column


class AuditConfigurationError(RuntimeError):
    """Raised when the audit settings are unsafe or incomplete."""


class AuditUnavailableError(RuntimeError):
    """The audit trail could not be written. The action must not go ahead (fail closed)."""


@dataclass(frozen=True)
class AuditActor:
    """The person (or system) performing an action, taken from the verified login token."""

    subject: str  # stable ID from the identity provider
    email: str | None
    roles: tuple[str, ...]  # sorted, so the same person is always recorded the same way

    @classmethod
    def from_principal(cls, principal: Principal) -> AuditActor:
        return cls(
            subject=principal.subject,
            email=principal.email,
            roles=tuple(sorted(principal.roles)),
        )

    @property
    def role_label(self) -> str:
        return ",".join(self.roles)[:ROLE_COLUMN_LENGTH] or "user"


class AuditEvent(BaseModel):
    """One audit record as returned by the API.

    ``actor_id`` is what a person reading the log should see (the email when there is one).
    The stable identity is ``actor_subject``.
    """

    event_id: str
    timestamp: str  # server clock, "YYYY-MM-DD HH:MM:SS UTC"
    actor_id: str
    actor_subject: str
    actor_email: str | None = None
    actor_role: str
    actor_roles: list[str] = Field(default_factory=list)
    project_id: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    resource_version: str | None = None
    outcome: str = "SUCCESS"
    details: dict[str, Any] = Field(default_factory=dict)


def format_timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def validate_event_fields(action: str, resource_type: str, outcome: str) -> None:
    if not action.strip() or len(action) > 128:
        raise ValueError("action must be 1-128 characters")
    if not resource_type.strip() or len(resource_type) > 64:
        raise ValueError("resource_type must be 1-64 characters")
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}")


def clean_filter(value: str | None) -> str | None:
    """The viewer sends "ALL" for "no filter"."""
    if value is None:
        return None
    value = value.strip()
    return None if not value or value == "ALL" else value


class AuditLogService(ABC):
    """What every audit backend can do."""

    @abstractmethod
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
        """Append one event and return it. Raises AuditUnavailableError if it cannot be stored."""

    @abstractmethod
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
        """Newest first. Returns one page and the total number of matching events."""


class InMemoryAuditLogService(AuditLogService):
    """Keeps events in a list. For tests and database-less local runs only."""

    def __init__(self) -> None:
        self._events: list[tuple[datetime, AuditEvent]] = []

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
        moment = datetime.now(UTC)
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            timestamp=format_timestamp(moment),
            actor_id=actor.email or actor.subject,
            actor_subject=actor.subject,
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
        self._events.insert(0, (moment, event))  # newest first
        return event

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

        def matches(moment: datetime, e: AuditEvent) -> bool:
            if project_id and e.project_id != project_id:
                return False
            if actor and actor.lower() not in f"{e.actor_subject} {e.actor_email or ''}".lower():
                return False
            if action and e.action != action:
                return False
            if outcome and e.outcome != outcome:
                return False
            if resource_type and e.resource_type != resource_type:
                return False
            if resource_id and e.resource_id != resource_id:
                return False
            if since and moment < since:
                return False
            return not (until and moment > until)

        filtered = [e for moment, e in self._events if matches(moment, e)]
        start = (page - 1) * page_size
        return filtered[start : start + page_size], len(filtered)


def build_audit_service(cfg: Settings) -> AuditLogService:
    """Choose the backend from settings, refusing unsafe combinations."""
    if cfg.audit_backend == "memory":
        if cfg.environment not in MEMORY_ALLOWED_ENVIRONMENTS:
            raise AuditConfigurationError(
                f"AUDIT_BACKEND=memory is not allowed when ENVIRONMENT={cfg.environment!r}; "
                "use AUDIT_BACKEND=database"
            )
        return InMemoryAuditLogService()

    from pharma_ai.infrastructure.audit.database import DatabaseAuditLogService

    return DatabaseAuditLogService.from_settings(cfg)


_audit_service_instance: AuditLogService | None = None


def get_audit_service() -> AuditLogService:
    """Return the process-wide audit service (created on first use)."""
    global _audit_service_instance
    if _audit_service_instance is None:
        _audit_service_instance = build_audit_service(settings)
    return _audit_service_instance


def reset_audit_service() -> None:
    """Forget the cached service. Used by tests so each test starts with an empty trail."""
    global _audit_service_instance
    _audit_service_instance = None
