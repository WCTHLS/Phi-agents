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
    assert "Run Grounded Query" in app_tsx


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


def test_the_ui_has_no_canned_answers_or_hidden_fallbacks() -> None:
    """The screens show what the API returns, or an error. Nothing invented stands in for either.

    These are the pieces that once made a failed call look like a working answer or a saved review.
    """
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")
    client_ts = (FRONTEND_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")

    for removed in (
        "DEFAULT_PREVIEW_RESULT",
        "FALLBACK_SOURCES",
        "FALLBACK_PROJECT_DOCS",
        "INITIAL_REVIEW_HISTORY",
        "INITIAL_AUDIT_LOGS",
        "buildSamplePdfBytes",
        "Upload Sample Revision",
        "fda-nitrosamines-rev1",  # the canned NDMA/NDEA answer
        "ich-q3d-elemental-impurities",  # the canned cadmium answer
    ):
        assert removed not in app_tsx, f"{removed} is a canned stand-in and must not come back"

    # A question goes to the real project, and the run id comes from the server.
    assert "project_id: selectedProject" in app_tsx
    assert "response.workflow_run_id" in app_tsx
    assert "generatedRunId" not in app_tsx
    # A failed question or review shows its error instead of an answer or a saved decision.
    assert "setQueryError(" in app_tsx and "setReviewFeedback(" in app_tsx
    # The review request carries no question or sources: the server uses the stored run.
    assert "cited_sources" not in client_ts.split("ReviewDecisionRequest")[1].split("}")[0]
    # Invented sample sections say so.
    assert app_tsx.count("SAMPLE DATA") >= 2


def test_the_upload_form_lists_the_real_register_not_a_typed_in_list() -> None:
    """The source choices come from the API; a typed-in list once omitted six approved documents."""
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")
    client_ts = (FRONTEND_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")

    assert "fetchSourceRegister" in app_tsx and "/api/v1/sources" in client_ts
    assert 'useState<string>("ICH-Q3DR2")' not in app_tsx
    for typed_in in ('<option value="ICH-Q3DR2">', '<option value="ICH-Q8R2">', "Tests 409 Rule"):
        assert typed_in not in app_tsx, typed_in


def test_the_ui_does_not_show_the_built_in_sample_sources() -> None:
    """The old "Approved Regulatory Sources" panel listed three made-up documents with a hash of
    their title text as a "SHA-256". Documents and fingerprints come from the project's own list."""
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")
    client_ts = (FRONTEND_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")

    for removed in ("Approved Regulatory Sources", "fetchApprovedSources", "content_hash"):
        assert removed not in app_tsx and removed not in client_ts, removed
    assert "/api/v1/research/sources" not in client_ts


def test_the_ui_does_not_fill_in_values_the_database_holds() -> None:
    """Where the database has a value the screen shows it, or says it is not recorded.

    Each of these once stood in for a real value: an extraction status of "ok" or a text layer of
    "no" for a version that had none recorded, a guessed extraction engine, a made-up run id taken
    from an event id, a role of "admin" for an event without one, a "GLOBAL" project, a guessed
    citation version and authority, and the project names typed into the page.
    """
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")

    for invented in (
        '?? "pypdf"',
        'extraction_status || "ok"',
        'text_layer || "no"',
        "page_count ?? 0",
        'it.actor_role as UserRole) || "admin"',
        '"GLOBAL"',
        "event_id.slice(0, 12)",
        "Approved Baseline",
        '?? "Approved source"',
        'includes("q3d")',
        "Metformin ER Nitrosamine Mitigation",
        "BCS II Oral Bioavailability",
    ):
        assert invented not in app_tsx, invented
    assert "fetchMyProjects" in app_tsx


def test_there_is_no_zero_hallucination_switch_in_the_ui() -> None:
    """A "Strict Zero-Hallucination Mode" checkbox once did nothing: the setting was sent and
    recorded but never read. Answers are always built from verified quotes, so the screen says so
    in a fixed note and offers no switch."""
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")
    client_ts = (FRONTEND_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")

    for removed in ("Zero-Hallucination", "strictCitationMode", "strict_citation_mode"):
        assert removed not in app_tsx and removed not in client_ts, removed
    assert "built only from quotes" in app_tsx and "word for word in the retrieved" in app_tsx
    assert "does not" in app_tsx and "scientifically correct" in app_tsx


def test_the_answer_panel_does_not_overstate_what_it_shows() -> None:
    """The answer is a quote, not a synthesis, and a verified quote with flagged gaps is only partly
    supported: it was once headed "Synthesized Grounded Answer" under a green "grounded" badge
    beside three red gaps."""
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")

    assert "Synthesized Grounded Answer" not in app_tsx and "Lexical Overlap Score" not in app_tsx
    assert "Quoted Evidence:" in app_tsx and "Question Words Found in Quotes" in app_tsx
    # A verified answer is green only when nothing is flagged; otherwise it is the amber badge.
    assert "PARTLY SUPPORTED" in app_tsx
    assert app_tsx.index("evidence_gaps.length > 0") < app_tsx.index("GROUNDED IN")
    assert app_tsx.count("expect no relevant evidence") == 1  # the PRJ-002 chip that remains
    # The two PRJ-001 test chips (a held source, and another project's document) were removed.
    assert "Held / Unapproved Source Test" not in app_tsx
    assert "dissolution belongs to PRJ-002" not in app_tsx


def test_a_long_dropdown_label_cannot_push_a_column_off_the_screen() -> None:
    """A plain 1fr grid column cannot shrink below its content, and a dropdown is as wide as its
    longest option, so a long source title once pushed the Q&A column off the right edge."""
    css = (FRONTEND_DIR / "src" / "styles.css").read_text(encoding="utf-8")

    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1.38fr)" in css
    assert "grid-template-columns: minmax(0, 1.2fr) minmax(0, 0.8fr)" in css
    assert ".upload-grid select" in css and "text-overflow: ellipsis" in css


def test_the_review_queue_screen_reads_the_servers_list_and_is_for_reviewers() -> None:
    """Reviewers see the project's stored questions from the API (decision log D25). Nothing about
    the list is kept in the page, and the screen follows the same rule as the server."""
    app_tsx = (FRONTEND_DIR / "src" / "App.tsx").read_text(encoding="utf-8")
    client_ts = (FRONTEND_DIR / "src" / "api" / "client.ts").read_text(encoding="utf-8")

    assert "/api/v1/research/reviews?" in client_ts and "fetchReviewQueue" in app_tsx
    assert "4. Review Queue (Reviewer)" in app_tsx and "5. Audit Log Viewer (Admin)" in app_tsx
    # The list is only requested for a reviewer; anyone else gets a note, as the server refuses them.
    assert 'activeTab !== "queue" || !canReview' in app_tsx
    assert "Only a reviewer can see the questions waiting for review" in app_tsx
    # The asker's e-mail is not part of the listing or the screen.
    assert "asked_by_email" not in app_tsx and "asked_by_email" not in client_ts
    # A question that got no answer cannot be approved.
    assert "disabled={openRun.withheld}" in app_tsx
