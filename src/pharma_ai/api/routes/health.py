"""Health-check endpoint for local development and deployment checks."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from pharma_ai.infrastructure.audit.database import DatabaseAuditLogService
from pharma_ai.infrastructure.audit.service import (
    AuditLogService,
    AuditUnavailableError,
    get_audit_service,
)

router = APIRouter()


@router.get("/health", summary="Health check")
def health_check() -> dict[str, str]:
    """Return a lightweight status response; does not imply dependencies are connected."""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness check")
def readiness_check(
    service: Annotated[AuditLogService, Depends(get_audit_service)],
) -> dict[str, str]:
    """Return readiness status for container orchestration probes.

    When the audit trail is stored in the database, the API is only ready if the database answers.
    """
    if isinstance(service, DatabaseAuditLogService):
        try:
            service.ping()
        except AuditUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
            ) from exc
    return {"status": "ready"}
