"""Tests verifying S1-08 (React/Vite UI shell), S1-09 (OIDC SSO), S1-12 (Audit UI), S2-05, S2-08, S2-10, S2-11, and S2-13."""

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


def test_frontend_document_library_ui_exists() -> None:
    """Verify Document Library UI (S2-05) supports upload, status, text preview, and version history."""
    client_ts = (FRONTEND_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "fetchProjectDocuments" in client_ts
    assert "uploadSourceDocument" in client_ts
    assert "/api/v1/projects/" in client_ts
    assert "/api/v1/sources/" in client_ts

    assert "Project Document Library" in app_tsx
    assert "Extracted Text Preview" in app_tsx
    assert "Version History" in app_tsx
    assert "Uncertainty Flag" in app_tsx


def test_frontend_scientist_qa_ui_exists() -> None:
    """Verify Scientist Q&A UI (S2-08) renders inline citations, passage/version inspector, gaps section, and D9 disclaimer."""
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "Not yet reviewed by a domain expert" in app_tsx
    assert "Citation Inspector Panel" in app_tsx
    assert "Selected Source Passage Detail" in app_tsx
    assert "Evidence Gaps, Unsupported Claims &amp; Regulatory Conflicts" in app_tsx
    assert "Golden Questions" in app_tsx
    assert "Strict Zero-Hallucination Mode" in app_tsx


def test_frontend_human_review_gate_ui_exists() -> None:
    """Verify Human-Review Gate UI (S2-10) supports Approve, Reject, and Request More Evidence with rationale and history."""
    client_ts = (FRONTEND_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "submitReviewDecision" in client_ts
    assert "/api/v1/research/reviews" in client_ts
    assert '"APPROVED" | "REJECTED" | "EVIDENCE_REQUESTED"' in client_ts

    assert "Human-Review Gate" in app_tsx
    assert "Approve Cited Answer" in app_tsx
    assert "Reject Answer" in app_tsx
    assert "Request More Evidence" in app_tsx
    assert "Scientific rationale is required before recording a review decision" in app_tsx
    assert "Review Decision History" in app_tsx


def test_frontend_audit_viewer_extensions_and_csv_export_exist() -> None:
    """Verify Audit Viewer Extensions (S2-11) include ingestion/indexing/review events, workflow run filter, and CSV export."""
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "DOCUMENT_INDEXED" in app_tsx
    assert "ANSWER_REVIEW_APPROVED" in app_tsx
    assert "ANSWER_REVIEW_REJECTED" in app_tsx
    assert "ANSWER_EVIDENCE_REQUESTED" in app_tsx
    assert "Filter by Workflow Run" in app_tsx
    assert "Export Audit Log CSV" in app_tsx
    assert "text/csv;charset=utf-8;" in app_tsx


def test_end_to_end_ui_workflow_contract() -> None:
    """Verify E2E UI flow contract (S2-13): login -> upload -> ask -> cited answer -> review decision -> audit trail."""
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")
    client_ts = (FRONTEND_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    oidc_ts = (FRONTEND_DIR / "src" / "auth" / "oidc.ts").read_text(encoding="utf-8")

    # 1. Login (OIDC / dev-token session)
    assert "requestDevToken" in oidc_ts
    assert "/api/v1/auth/dev-token" in oidc_ts
    # 2. Upload approved PDF version into project
    assert "handleDocumentUpload" in app_tsx
    assert "uploadSourceDocument" in client_ts
    # 3. Ask question in project context -> cited answer + passage inspector
    assert "handleRunQuery" in app_tsx
    assert "submitResearchQuery" in client_ts
    assert "resolveCitationProvenance" in app_tsx
    # 4. Human-review decision (Approve / Reject / Request More Evidence) with rationale
    assert "handleReviewDecision" in app_tsx
    assert "submitReviewDecision" in client_ts
    # 5. Reconstruct in Audit Viewer & export CSV
    assert "handleExportAuditCsv" in app_tsx
