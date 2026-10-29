"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8088"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(research_router)
