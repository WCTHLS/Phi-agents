"""FastAPI application entry point."""

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from pharma_ai.api.routes.audit import audit_router
from pharma_ai.api.routes.health import router as health_router
from pharma_ai.api.routes.projects import router as projects_router
from pharma_ai.api.routes.research import router as research_router
from pharma_ai.core.config import settings
from pharma_ai.infrastructure.audit.service import (
    AuditActor,
    AuditLogService,
    AuditUnavailableError,
    get_audit_service,
)
from pharma_ai.infrastructure.security.auth import get_token_verifier

logger = logging.getLogger(__name__)

# Fail fast on unsafe or incomplete auth settings (e.g. AUTH_MODE=dev in production).
get_token_verifier()
# Same for the audit trail (e.g. AUDIT_BACKEND=memory in production). Does not connect to the DB.
get_audit_service()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Starter API for a scientist-assistance platform. Generated outputs will require "
        "evidence traceability and qualified human review."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8088"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _audit_service_for(request: Request) -> AuditLogService:
    """The audit service for this request (honours test overrides)."""
    return request.app.dependency_overrides.get(get_audit_service, get_audit_service)()


@app.exception_handler(StarletteHTTPException)
async def record_refused_access(request: Request, exc: StarletteHTTPException):
    """Write every 403 for a logged-in caller to the audit trail, then answer as usual.

    A refused request has no side effects, so if the audit write itself fails the caller is
    still refused (and the failure is logged). 401s are not recorded: nobody was identified.
    """
    principal = getattr(request.state, "principal", None)
    if exc.status_code == 403 and principal is not None:
        try:
            await run_in_threadpool(
                _audit_service_for(request).record_event,
                actor=AuditActor.from_principal(principal),
                action="ACCESS_DENIED",
                resource_type="endpoint",
                resource_id=f"{request.method} {request.url.path}",
                project_id=request.path_params.get("project_id"),
                outcome="DENIED",
                details={"status": 403, "reason": str(exc.detail)[:200]},
            )
        except Exception:
            logger.exception("Could not record a refused request in the audit trail")
    return await http_exception_handler(request, exc)


@app.exception_handler(AuditUnavailableError)
async def audit_trail_unavailable(request: Request, exc: AuditUnavailableError) -> JSONResponse:
    """If the trail cannot be written, the action is not completed (fail closed)."""
    logger.error("Audit trail unavailable: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "Audit trail unavailable; the action was not completed"},
    )


@app.get("/", summary="Root status", tags=["health"])
def root_status() -> dict[str, str]:
    """Provide quick status and links to interactive documentation and frontend."""
    return {
        "service": "Pharma AI Platform Backend",
        "status": "running",
        "health_check": "/health",
        "readiness_check": "/ready",
        "interactive_docs": "/docs",
        "frontend_dev_url": "http://localhost:5173",
    }


app.include_router(health_router, tags=["health"])
app.include_router(projects_router)
app.include_router(research_router)
app.include_router(audit_router, prefix="/api/v1/audit", tags=["audit"])
