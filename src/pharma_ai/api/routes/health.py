"""Health-check endpoint for local development and deployment checks."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", summary="Health check")
def health_check() -> dict[str, str]:
    """Return a lightweight status response; does not imply dependencies are connected."""
    return {"status": "ok"}


@router.get("/ready", summary="Readiness check")
def readiness_check() -> dict[str, str]:
    """Return readiness status for container orchestration probes."""
    return {"status": "ready"}
