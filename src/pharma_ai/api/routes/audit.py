"""Audit log API endpoints (S1-12).

Provides read access to the append-only audit trail for administrative compliance review,
protected strictly by role-based authorization (admin role required).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from pharma_ai.infrastructure.audit.service import (
    AuditEvent,
    AuditLogService,
    get_audit_service,
)
from pharma_ai.infrastructure.security.auth import (
    CurrentPrincipal,
    require_role,
)

audit_router = APIRouter()


class AuditEventsPage(BaseModel):
    items: list[AuditEvent]
    total: int
    page: int
    page_size: int


class RecordAuditEventRequest(BaseModel):
    action: str
    resource_type: str
    resource_id: str | None = None
    project_id: str | None = None
    outcome: str = "SUCCESS"
    details: dict[str, Any] = Field(default_factory=dict)


@audit_router.get(
    "/events",
    response_model=AuditEventsPage,
    summary="List audit events (Admin only)",
    description="Returns paginated immutable audit records. Requires caller to have 'admin' role.",
    dependencies=[Depends(require_role("admin"))],
)
def list_audit_events(
    project_id: str | None = Query(None, description="Filter by project ID"),
    actor: str | None = Query(None, description="Search by actor identifier"),
    action: str | None = Query(None, description="Filter by exact action"),
    outcome: str | None = Query(None, description="Filter by outcome"),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    service: Annotated[AuditLogService, Depends(get_audit_service)] = ...,
) -> AuditEventsPage:
    """Retrieve filtered, paginated audit records for compliance inspection."""
    items, total = service.list_events(
        project_id=project_id,
        actor=actor,
        action=action,
        outcome=outcome,
        page=page,
        page_size=page_size,
    )
    return AuditEventsPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@audit_router.post(
    "/events",
    response_model=AuditEvent,
    status_code=201,
    summary="Record an audit event",
    description="Record an action in the immutable audit trail (e.g. human sign-off gate).",
)
def create_audit_event(
    payload: RecordAuditEventRequest,
    principal: CurrentPrincipal,
    service: Annotated[AuditLogService, Depends(get_audit_service)],
) -> AuditEvent:
    """Record an action into the audit trail using caller's verified identity."""
    role = next(iter(principal.roles), "user")
    return service.record_event(
        actor_id=principal.email or principal.subject,
        actor_role=role,
        action=payload.action,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        project_id=payload.project_id,
        outcome=payload.outcome,
        details=payload.details,
    )
