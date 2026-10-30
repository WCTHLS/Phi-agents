"""Audit log API endpoints (S1-12, S1-11).

Read access to the append-only audit trail for administrative review (admin role required).

There is deliberately no endpoint for a client to create an audit event. Events are written by
the server when real actions happen (see ``infrastructure/audit/recorder.py``), with the actor
taken from the verified login token. A client-writable trail could be used to forge records such
as a sign-off.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from pharma_ai.infrastructure.audit.recorder import AuditRecorderDep
from pharma_ai.infrastructure.audit.service import (
    AuditEvent,
    AuditLogService,
    get_audit_service,
)
from pharma_ai.infrastructure.security.auth import require_role

audit_router = APIRouter()


class AuditEventsPage(BaseModel):
    items: list[AuditEvent]
    total: int
    page: int
    page_size: int


def _as_utc(moment: datetime | None) -> datetime | None:
    """A date-time sent without a time zone is taken to be UTC."""
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


@audit_router.get(
    "/events",
    response_model=AuditEventsPage,
    summary="List audit events (Admin only)",
    description=(
        "Returns paginated, newest-first audit records. Requires the 'admin' role. To reconstruct "
        "the history of one object, filter by resource_type and resource_id (and optionally by "
        "since/until). Reading the log is itself recorded."
    ),
    dependencies=[Depends(require_role("admin"))],
)
def list_audit_events(
    service: Annotated[AuditLogService, Depends(get_audit_service)],
    audit: AuditRecorderDep,
    project_id: Annotated[str | None, Query(description="Filter by project ID")] = None,
    actor: Annotated[str | None, Query(description="Search by actor email or subject ID")] = None,
    action: Annotated[str | None, Query(description="Filter by exact action")] = None,
    outcome: Annotated[str | None, Query(description="SUCCESS, DENIED or FAILURE")] = None,
    resource_type: Annotated[
        str | None, Query(description="Filter by type of object acted on")
    ] = None,
    resource_id: Annotated[
        str | None, Query(description="Filter by the object's identifier")
    ] = None,
    since: Annotated[
        datetime | None, Query(description="Only events at or after this time")
    ] = None,
    until: Annotated[
        datetime | None, Query(description="Only events at or before this time")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AuditEventsPage:
    """Retrieve filtered, paginated audit records for compliance inspection."""
    filters = {
        "project_id": project_id,
        "actor": actor,
        "action": action,
        "outcome": outcome,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
    }
    items, total = service.list_events(
        project_id=project_id,
        actor=actor,
        action=action,
        outcome=outcome,
        resource_type=resource_type,
        resource_id=resource_id,
        since=_as_utc(since),
        until=_as_utc(until),
        page=page,
        page_size=page_size,
    )
    # Recorded after reading, so a page never contains the event for its own request.
    audit.success(
        "AUDIT_LOG_VIEWED",
        "audit_log",
        details={
            "filters": {k: v for k, v in filters.items() if v},
            "page": page,
            "page_size": page_size,
            "returned": len(items),
        },
    )
    return AuditEventsPage(items=items, total=total, page=page, page_size=page_size)
