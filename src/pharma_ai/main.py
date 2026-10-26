"""FastAPI application entry point."""

from fastapi import FastAPI

from pharma_ai.api.routes.health import router as health_router
from pharma_ai.api.routes.research import router as research_router
from pharma_ai.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Starter API for a scientist-assistance platform. Generated outputs will require "
        "evidence traceability and qualified human review."
    ),
)

app.include_router(health_router, tags=["health"])
app.include_router(research_router)
