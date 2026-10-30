"""Append-only audit trail for who did what, when (21 CFR Part 11 / GxP support)."""

from pharma_ai.infrastructure.audit.recorder import (
    AuditRecorder,
    AuditRecorderDep,
    fingerprint_text,
)
from pharma_ai.infrastructure.audit.service import (
    AuditActor,
    AuditEvent,
    AuditLogService,
    AuditUnavailableError,
    get_audit_service,
)

__all__ = [
    "AuditActor",
    "AuditEvent",
    "AuditLogService",
    "AuditRecorder",
    "AuditRecorderDep",
    "AuditUnavailableError",
    "fingerprint_text",
    "get_audit_service",
]
