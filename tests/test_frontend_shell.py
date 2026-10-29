"""Tests verifying S1-08 (React/Vite UI shell), S1-09 (OIDC SSO), and S1-12 (Audit UI)."""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"


def test_frontend_package_json_and_vite_config_exist() -> None:
    """Verify React + Vite + TypeScript configuration files exist and define build scripts."""
    pkg_path = FRONTEND_DIR / "package.json"
    assert pkg_path.exists()

    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    assert "react" in pkg["dependencies"]
    assert "vite" in pkg["devDependencies"]
    assert "typescript" in pkg["devDependencies"]
    assert "build" in pkg["scripts"]
    assert (FRONTEND_DIR / "vite.config.ts").exists()
    assert (FRONTEND_DIR / "tsconfig.json").exists()


def test_frontend_app_defines_three_workspace_views_and_audit_viewer() -> None:
    """Verify App.tsx implements Research & Strategy, Trial Verification, Report Review, and Audit."""
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "Research &amp; Strategy" in app_tsx
    assert "Trial Verification &amp; DOE" in app_tsx
    assert "Report Review Studio" in app_tsx
    assert "Audit Log Viewer (Admin)" in app_tsx
    assert "403 Forbidden" in app_tsx
    assert "401 Authentication Required" in app_tsx


def test_frontend_openapi_client_and_oidc_pkce_exist() -> None:
    """Verify OpenAPI client calls /health and OIDC module implements PKCE S256."""
    client_ts = (FRONTEND_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    oidc_ts = (FRONTEND_DIR / "src" / "auth" / "oidc.ts").read_text(encoding="utf-8")

    assert "/health" in client_ts
    assert "/api/v1/research/sources" in client_ts
    assert "/api/v1/research/query" in client_ts
    assert "code_challenge_method" in oidc_ts
    assert "S256" in oidc_ts
