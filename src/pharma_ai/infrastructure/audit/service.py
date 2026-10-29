"""Immutable append-only audit trail service (21 CFR Part 11 / GxP).

Kept separate from operational telemetry. Records who, what, when, object, version,
and outcome for all regulatory-impacting actions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    """Immutable record of an action taken within the platform."""

    event_id: str = Field(default_factory=lambda: f"AUD-{uuid.uuid4().hex[:8].upper()}")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    )
    actor_id: str
    actor_role: str
    project_id: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    outcome: str = "SUCCESS"
    details: dict[str, Any] = Field(default_factory=dict)


class AuditLogService:
    """In-memory append-only audit service with seed history.

    In production this writes to an append-only PostgreSQL table where
    UPDATE and DELETE privileges have been revoked from the application user.
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._seed_baseline()

    def _seed_baseline(self) -> None:
        """Seed baseline events representing platform initialization and sample activity."""
        baseline = [
            AuditEvent(
                event_id="AUD-1001",
                timestamp="2026-10-29 09:15:02 UTC",
                actor_id="a.thorne@pharma.local",
                actor_role="scientist",
                project_id="PRJ-001",
                action="RESEARCH_QUERY_EXECUTED",
                resource_type="document",
                resource_id="fda-nitrosamines-rev1",
                outcome="SUCCESS",
                details={
                    "query": "What are the acceptable intake limits for NDMA?",
                    "citation": "FDA Rev 1 Section III p. 10",
                },
            ),
            AuditEvent(
                event_id="AUD-1002",
                timestamp="2026-10-29 10:04:19 UTC",
                actor_id="e.vance@pharma.local",
                actor_role="reviewer",
                project_id="PRJ-001",
                action="TRIAL_VERIFICATION_APPROVED",
                resource_type="trial_batch",
                resource_id="DOE-RUN-102",
                outcome="SUCCESS",
                details={
                    "justification": "Verified low-nitrite MCC excipient batch met < 24 ng/day NDMA threshold",
                    "status": "VERIFIED_PASS",
                },
            ),
            AuditEvent(
                event_id="AUD-1003",
                timestamp="2026-10-29 11:30:44 UTC",
                actor_id="e.vance@pharma.local",
                actor_role="reviewer",
                project_id="PRJ-001",
                action="REPORT_SECTION_APPROVED",
                resource_type="report_section",
                resource_id="PDR-3.2.P.2-v1.3",
                outcome="SUCCESS",
                details={
                    "section": "3.2.P.2.2 Nitrosamine & Elemental Impurity Control Strategy",
                    "electronic_signature": "21 CFR Part 11 compliant sign-off",
                },
            ),
            AuditEvent(
                event_id="AUD-1004",
                timestamp="2026-10-29 12:00:00 UTC",
                actor_id="system-bootstrap",
                actor_role="admin",
                project_id="PRJ-001",
                action="DATABASE_SEED_INITIALIZED",
                resource_type="database",
                resource_id="pharma_ai",
                outcome="SUCCESS",
                details={"note": "Local development seed data loaded"},
            ),
        ]
        self._events.extend(baseline)

    def record_event(
        self,
        *,
        actor_id: str,
        actor_role: str,
        action: str,
        resource_type: str,
        project_id: str | None = None,
        resource_id: str | None = None,
        outcome: str = "SUCCESS",
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Append an immutable audit entry."""
        event = AuditEvent(
            actor_id=actor_id,
            actor_role=actor_role,
            project_id=project_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details or {},
        )
        self._events.insert(0, event)  # newest first
        return event

    def list_events(
        self,
        *,
        project_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        outcome: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AuditEvent], int]:
        """Filter and paginate audit records."""
        filtered = self._events

        if project_id and project_id != "ALL":
            filtered = [e for e in filtered if e.project_id == project_id]

        if actor and actor.strip():
            term = actor.strip().lower()
            filtered = [e for e in filtered if term in e.actor_id.lower()]

        if action and action != "ALL":
            filtered = [e for e in filtered if e.action == action]

        if outcome and outcome != "ALL":
            filtered = [e for e in filtered if e.outcome == outcome]

        total = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size
        return filtered[start:end], total


_audit_service_instance: AuditLogService | None = None


def get_audit_service() -> AuditLogService:
    """Return process-wide singleton audit service."""
    global _audit_service_instance
    if _audit_service_instance is None:
        _audit_service_instance = AuditLogService()
    return _audit_service_instance
