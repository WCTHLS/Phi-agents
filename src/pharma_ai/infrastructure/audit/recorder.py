"""How API routes write to the audit trail (sprint task S1-11).

A route asks for an ``AuditRecorder`` the same way it asks for the logged-in user. The recorder
already knows who the caller is (taken from the verified login token), so a route can only say
*what happened*, never *who did it*:

    def get_project(project_id: str, audit: AuditRecorderDep):
        ...
        audit.success("PROJECT_VIEWED", "project", resource_id=project_id, project_id=project_id)

If the audit write fails, ``AuditUnavailableError`` propagates and the API answers 503: the action
does not complete without its record.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

from fastapi import Depends

from pharma_ai.infrastructure.audit.service import (
    AuditActor,
    AuditEvent,
    AuditLogService,
    get_audit_service,
)
from pharma_ai.infrastructure.security.auth import CurrentPrincipal


def fingerprint_text(text: str) -> dict[str, Any]:
    """A fingerprint of free text, so the log can show *that* a question was asked without
    keeping the question itself (it may contain confidential product details).

    Limit: the same text always gives the same fingerprint, so someone who can guess the exact
    question could confirm it. It does not reveal text they cannot already guess.
    """
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "length": len(text)}


class AuditRecorder:
    def __init__(self, service: AuditLogService, actor: AuditActor) -> None:
        self._service = service
        self._actor = actor

    def _record(self, outcome: str, action: str, resource_type: str, **fields: Any) -> AuditEvent:
        return self._service.record_event(
            actor=self._actor, action=action, resource_type=resource_type, outcome=outcome, **fields
        )

    def success(self, action: str, resource_type: str, **fields: Any) -> AuditEvent:
        return self._record("SUCCESS", action, resource_type, **fields)

    def denied(self, action: str, resource_type: str, **fields: Any) -> AuditEvent:
        return self._record("DENIED", action, resource_type, **fields)

    def failure(self, action: str, resource_type: str, **fields: Any) -> AuditEvent:
        return self._record("FAILURE", action, resource_type, **fields)


def get_audit_recorder(
    principal: CurrentPrincipal,
    service: Annotated[AuditLogService, Depends(get_audit_service)],
) -> AuditRecorder:
    return AuditRecorder(service, AuditActor.from_principal(principal))


AuditRecorderDep = Annotated[AuditRecorder, Depends(get_audit_recorder)]
