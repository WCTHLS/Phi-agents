"""Human-review gate: record a reviewer's decision on an answer (S2-10, NFR-12 / FR-27).

- ``POST /api/v1/research/reviews``: a reviewer approves, rejects or asks for more evidence for
  one stored answer, with a written scientific rationale. Reviewers (``reviewer``, ``qa``) who are
  members of the project only.

The decision is checked against the server's own record of the answer (S2-09):
- the run must exist and belong to the project in the request (otherwise 404, the same whether the
  run is missing or belongs to another project);
- the cited sources come from the stored answer, never from the request;
- the reviewer is who the login token says.

The run's status change and the audit event are saved in one transaction (ADR-005): both or
neither. The audit event can never be edited or deleted; the run's status shows the latest
decision, and the events are the full history. A run may be decided more than once (for example
"more evidence requested", then "approved"); the latest decision wins.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from pharma_ai.api.routes.search import SessionDep
from pharma_ai.infrastructure.audit.recorder import AuditRecorderDep
from pharma_ai.infrastructure.db.repositories import WorkflowRunRepository
from pharma_ai.infrastructure.security.auth import (
    CurrentPrincipal,
    Principal,
    ensure_project_access,
)

router = APIRouter(prefix="/api/v1/research/reviews", tags=["research"])

# Roles that may record a decision. A scientist who asks a question cannot approve the answer.
REVIEWER_ROLES = frozenset({"reviewer", "qa"})

ACTIONS = {
    "APPROVED": "ANSWER_REVIEW_APPROVED",
    "REJECTED": "ANSWER_REVIEW_REJECTED",
    "EVIDENCE_REQUESTED": "ANSWER_EVIDENCE_REQUESTED",
}

MAX_RATIONALE_CHARS = 4000


def require_reviewer(principal: CurrentPrincipal) -> Principal:
    if not principal.roles & REVIEWER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires one of the roles: {', '.join(sorted(REVIEWER_ROLES))}",
        )
    return principal


ReviewerDep = Annotated[Principal, Depends(require_reviewer)]


class ReviewDecisionRequest(BaseModel):
    """What the reviewer sends. Fields the browser used to send about the question and the sources
    (``query``, ``cited_sources``) are ignored: the server uses what it stored with the answer."""

    workflow_run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    project_id: str = Field(min_length=1, max_length=64)
    decision: Literal["APPROVED", "REJECTED", "EVIDENCE_REQUESTED"]
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_CHARS)

    @field_validator("rationale")
    @classmethod
    def _rationale_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("a scientific rationale is required")
        return value


class ReviewDecisionOut(BaseModel):
    """The shape the review screen's history list shows."""

    review_id: str  # the audit event's id: the event is the record
    workflow_run_id: str
    project_id: str
    decision: Literal["APPROVED", "REJECTED", "EVIDENCE_REQUESTED"]
    rationale: str
    reviewer_email: str
    reviewer_role: str
    cited_sources: list[str]
    timestamp: str
    audit_event_id: str


def _source_labels(citations: list[Any]) -> list[str]:
    """ "FDA-NITRO-2024 (v1, p. 43)" for each distinct source and page the answer quoted."""
    labels: dict[str, None] = {}
    for c in citations:
        labels.setdefault(f"{c['source_id']} (v{c['version_number']}, p. {c['page_start']})", None)
    return list(labels)


@router.post(
    "",
    response_model=ReviewDecisionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a reviewer's decision on a stored answer (reviewer or qa)",
    description=(
        "Records Approve, Reject or Request-More-Evidence with a required rationale, and moves the "
        "stored answer to that status. The reviewer is taken from the login token and the cited "
        "sources from the stored answer. The rationale is kept in full and, like every audit "
        "record, can never be edited or deleted."
    ),
)
def record_review_decision(
    body: ReviewDecisionRequest,
    principal: ReviewerDep,
    audit: AuditRecorderDep,
    session: SessionDep,
) -> ReviewDecisionOut:
    ensure_project_access(principal, body.project_id)
    runs = WorkflowRunRepository(session)
    run = runs.get_in_project(body.workflow_run_id, body.project_id)
    action = ACTIONS[body.decision]
    if run is None:
        audit.failure(
            action,
            "workflow_run",
            resource_id=body.workflow_run_id,
            project_id=body.project_id,
            details={"decision": body.decision, "reason": "run not found"},
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow run not found")

    previous_status = run.status
    runs.record_decision(run, status=body.decision, decided_by=principal.subject)
    event = audit.success(
        action,
        "workflow_run",
        resource_id=run.run_id,
        project_id=run.project_id,
        session=session,  # saved together with the status change, or not at all
        details={
            "decision": body.decision,
            "rationale": body.rationale,
            "previous_status": previous_status,
            "cited": [
                {
                    "chunk_id": c["chunk_id"],
                    "source_id": c["source_id"],
                    "version_id": c["version_id"],
                }
                for c in run.citations
            ],
        },
    )
    session.commit()
    return ReviewDecisionOut(
        review_id=event.event_id,
        workflow_run_id=run.run_id,
        project_id=run.project_id,
        decision=body.decision,
        rationale=body.rationale,
        reviewer_email=event.actor_email or event.actor_subject,
        reviewer_role=event.actor_role,
        cited_sources=_source_labels(run.citations),
        timestamp=event.timestamp,
        audit_event_id=event.event_id,
    )
