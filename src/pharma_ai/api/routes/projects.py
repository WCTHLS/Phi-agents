"""Project endpoints with object-level authorization (S1-09 / S1-10)."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from pharma_ai.infrastructure.audit.recorder import AuditRecorderDep
from pharma_ai.infrastructure.security.auth import CurrentPrincipal, ensure_project_access

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


class Project(BaseModel):
    project_id: str
    name: str
    therapeutic_area: str
    dosage_form: str


# Stand-in for the projects table until the PostgreSQL repository lands (S1-07).
# Mirrors the seed rows in scripts/init-db.sql.
_PROJECTS: dict[str, Project] = {
    "PRJ-001": Project(
        project_id="PRJ-001",
        name="Metformin ER Nitrosamine Risk Mitigation",
        therapeutic_area="Metabolic Disorders",
        dosage_form="Extended-Release Tablet",
    ),
    "PRJ-002": Project(
        project_id="PRJ-002",
        name="BCS Class II Oral Bioavailability Enhancement",
        therapeutic_area="Oncology",
        dosage_form="Immediate-Release Capsule",
    ),
}


@router.get("", response_model=list[Project], summary="List the caller's projects")
def list_my_projects(principal: CurrentPrincipal, audit: AuditRecorderDep) -> list[Project]:
    """Return only projects the caller is a member of."""
    projects = [p for pid, p in _PROJECTS.items() if principal.can_access_project(pid)]
    audit.success("PROJECT_LISTED", "project", details={"count": len(projects)})
    return projects


@router.get("/{project_id}", response_model=Project, summary="Get one project")
def get_project(project_id: str, principal: CurrentPrincipal, audit: AuditRecorderDep) -> Project:
    """Return a project. Membership is checked before existence so 403 does not leak IDs.

    A refused request (403) is written to the audit trail by the handler in main.py.
    """
    ensure_project_access(principal, project_id)
    project = _PROJECTS.get(project_id)
    if project is None:
        audit.failure(
            "PROJECT_VIEWED",
            "project",
            resource_id=project_id,
            project_id=project_id,
            details={"reason": "not found"},
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    audit.success("PROJECT_VIEWED", "project", resource_id=project_id, project_id=project_id)
    return project
