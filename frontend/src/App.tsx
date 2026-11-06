import React, { useEffect, useState } from "react";
import {
  checkHealth,
  Citation,
  fetchAuditEvents,
  ResearchResponse,
  ReviewDecisionAction,
  ReviewGateStatus,
  ReviewHistoryEntry,
  submitResearchQuery,
  submitReviewDecision,
  fetchProjectDocuments,
  ProjectDocument,
  fetchMyProjects,
  fetchReviewQueue,
  ProjectInfo,
  ReviewQueue,
} from "./api/client";
import {
  createDevSession,
  DEV_PROJECT_IDS,
  DEV_USERS,
  OidcSession,
  requestDevToken,
} from "./auth/oidc";

export type WorkspaceTab = "research" | "trials" | "reports" | "queue" | "audit";

interface AuditRecord {
  id: string;
  timestamp: string;
  actor: string;
  role: string;
  project: string;
  workflowRunId: string;
  action: string;
  resource: string;
  details: string;
}

const GOLDEN_SAMPLE_QUESTIONS: Record<string, { label: string; query: string }[]> = {
  "PRJ-001": [
    {
      label: "NDMA & NDEA Intake Limits (FDA)",
      query: "What are the acceptable intake limits for NDMA and NDEA according to FDA?",
    },
    {
      label: "Class 1 Elemental Impurities Oral PDEs (ICH Q3D)",
      query: "What are the Class 1 elemental impurities and oral PDE limits in ICH Q3D?",
    },
    {
      label: "Excipient Nitrite Risk & 3-Step Control (FDA)",
      query:
        "How do nitrite impurities in excipients cause nitrosamine formation and what is the three-step control strategy?",
    },
  ],
  "PRJ-002": [
    {
      label: "Highly Soluble Drug Substance (ICH M9)",
      query: "When is a drug substance considered highly soluble under ICH M9?",
    },
    {
      label: "Dissolution Medium pH (FDA)",
      query: "What pH should a dissolution medium have to simulate gastric fluid?",
    },
    {
      label: "Bioequivalence Study Size (ICH M13A)",
      query: "What should the number of subjects in a bioequivalence study be based on?",
    },
    {
      label: "Other Project's Document Test: expect no relevant evidence (nitrosamines belong to PRJ-001)",
      query: "What are the acceptable intake limits for NDMA and NDEA according to FDA?",
    },
    {
      label: "Ungrounded / Out-of-Scope Claim Test",
      query:
        "What is the clinical half-life of experimental compound XYZ-999 in phase 3 oncology trials?",
    },
  ],
};

function resolveCitationProvenance(
  cit: Citation,
  projectDocs: ProjectDocument[]
): {
  versionLabel: string;
  sha256: string;
  attribution: string;
  authority: string;
} {
  // A citation from the server names the exact version it quotes. That version, not the
  // document's latest one, is what the reader must see next to the quote. Anything the server did
  // not record is shown as not recorded, never filled in.
  const owner = projectDocs.find((d) => d.source_id.toLowerCase() === cit.source_id.toLowerCase());
  const version =
    cit.version_number != null
      ? `v${cit.version_number}${cit.version_label ? ` (${cit.version_label})` : ""}`
      : "not recorded";
  return {
    versionLabel: version,
    sha256: cit.sha256 || "not recorded",
    attribution: cit.attribution || "Attribution not recorded for this source.",
    authority: owner?.authority ?? "not recorded",
  };
}

/** "10" for a one-page quote or passage, "10–11" when it runs across a page break. */
function pageLabel(page: number, pageEnd?: number | null): string {
  return pageEnd != null && pageEnd > page ? `${page}–${pageEnd}` : String(page);
}

function escapeCsvCell(value: string): string {
  const safe = String(value ?? "").replace(/"/g, '""');
  return `"${safe}"`;
}

export function App(): React.ReactElement {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("research");
  const [selectedProject, setSelectedProject] = useState<string>("PRJ-001");
  const [session, setSession] = useState<OidcSession | null>(() => createDevSession("thorne"));
  const [loginUser, setLoginUser] = useState<string>("thorne");
  const [apiHealth, setApiHealth] = useState<"checking" | "ok" | "offline">("checking");

  // Document Library UI state (S2-05). Only what the API returns is shown; a failure is an error.
  const [projectDocs, setProjectDocs] = useState<ProjectDocument[]>([]);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  // Review queue (reviewers): the project's stored questions with one review status.
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [queueLoading, setQueueLoading] = useState<boolean>(false);
  const [queueStatus, setQueueStatus] = useState<ReviewGateStatus>("PENDING_EXPERT_REVIEW");
  const [queueRefresh, setQueueRefresh] = useState<number>(0);
  const [openRunId, setOpenRunId] = useState<string>("");
  const [queueRationale, setQueueRationale] = useState<string>("");
  const [queueFeedback, setQueueFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);
  // The signed-in person's own projects, as the API describes them (names included).
  const [myProjects, setMyProjects] = useState<ProjectInfo[]>([]);

  // Research & Strategy state (S2-08)
  const [queryText, setQueryText] = useState<string>(
    "What is the acceptable intake limit for NDMA?"
  );
  const [publisherFilter, setPublisherFilter] = useState<string>("ALL");
  const [topK, setTopK] = useState<number>(3);
  // Nothing is shown until the API has answered a question the user asked.
  const [researchResult, setResearchResult] = useState<ResearchResponse | null>(null);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [selectedCitationIndex, setSelectedCitationIndex] = useState<number>(0);
  const [queryLoading, setQueryLoading] = useState<boolean>(false);

  // Human-Review Gate state (S2-10). The run id is issued by the server with the answer.
  const [activeWorkflowRunId, setActiveWorkflowRunId] = useState<string>("");
  const [answerReviewStatus, setAnswerReviewStatus] =
    useState<ReviewGateStatus>("PENDING_EXPERT_REVIEW");
  // Empty on purpose: a reviewer has to write their own reason.
  const [reviewRationale, setReviewRationale] = useState<string>("");
  const [reviewHistory, setReviewHistory] = useState<ReviewHistoryEntry[]>([]);
  const [reviewFeedback, setReviewFeedback] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  // Audit Viewer Filter state (S1-12 & S2-11)
  const [auditLogs, setAuditLogs] = useState<AuditRecord[]>([]);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [filterProject, setFilterProject] = useState<string>("ALL");
  const [filterAction, setFilterAction] = useState<string>("ALL");
  const [filterActor, setFilterActor] = useState<string>("");
  const [filterWorkflowRun, setFilterWorkflowRun] = useState<string>("");

  // The page starts with a placeholder token ("dev-jwt-...") that the API rejects. It is swapped
  // for a signed one by the effect below; until then nothing should be asked of the API.
  const hasSignedToken = !!session?.accessToken && !session.accessToken.startsWith("dev-jwt-");
  useEffect(() => {
    if (!hasSignedToken) {
      setMyProjects([]);
      return;
    }
    let mounted = true;
    fetchMyProjects(session?.accessToken)
      .then((items) => {
        if (mounted) setMyProjects(items);
      })
      .catch(() => {
        if (mounted) setMyProjects([]); // the project list then shows ids only
      });
    return () => {
      mounted = false;
    };
  }, [session?.accessToken, hasSignedToken]);

  const projectLabel = (id: string): string => {
    const known = myProjects.find((p) => p.project_id === id);
    if (known) return `${id}: ${known.name}`;
    return session && !session.projectIds.includes(id) ? `${id} — no access` : id;
  };

  // Signing in as someone opens the first project they belong to. The project list still shows
  // the others (marked "no access"), so choosing one shows the API refusing it.
  const signInAs = (userKey: string) => {
    const next = createDevSession(userKey);
    setSession(next);
    if (next.projectIds.length > 0 && !next.projectIds.includes(selectedProject)) {
      setSelectedProject(next.projectIds[0]);
    }
  };
  // The server only accepts decisions from the reviewer role; the buttons follow the same rule.
  const canReview = session?.role === "reviewer";

  useEffect(() => {
    if (activeTab !== "queue" || !canReview || !hasSignedToken) {
      setQueue(null);
      setQueueError(null);
      return;
    }
    let mounted = true;
    setQueueLoading(true);
    fetchReviewQueue(selectedProject, queueStatus, session?.accessToken)
      .then((result) => {
        if (mounted) {
          setQueue(result);
          setQueueError(null);
        }
      })
      .catch((err: unknown) => {
        if (mounted) {
          setQueue(null);
          setQueueError(err instanceof Error ? err.message : "The review queue could not be loaded");
        }
      })
      .finally(() => {
        if (mounted) setQueueLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [
    activeTab,
    canReview,
    hasSignedToken,
    selectedProject,
    queueStatus,
    queueRefresh,
    session?.accessToken,
  ]);

  // A different project, filter or person starts with nothing open and no half-written rationale.
  useEffect(() => {
    setOpenRunId("");
    setQueueRationale("");
    setQueueFeedback(null);
  }, [selectedProject, queueStatus, session?.userKey]);

  const openRun = queue?.items.find((item) => item.run_id === openRunId);

  async function handleQueueDecision(decision: ReviewDecisionAction) {
    if (!session || !openRun) return;
    const rationale = queueRationale.trim();
    if (!rationale) {
      setQueueFeedback({
        type: "error",
        message: "A scientific rationale is required before a decision is recorded.",
      });
      return;
    }
    setQueueFeedback(null);
    try {
      const entry = await submitReviewDecision(
        {
          workflow_run_id: openRun.run_id,
          project_id: selectedProject,
          decision,
          rationale,
        },
        session.accessToken
      );
      setQueueFeedback({
        type: "success",
        message: `Recorded ${decision} for ${entry.workflow_run_id} (audit event ${entry.audit_event_id}).`,
      });
      setQueueRationale("");
      setOpenRunId("");
      setQueueRefresh((n) => n + 1);
    } catch (err) {
      setQueueFeedback({
        type: "error",
        message: err instanceof Error ? err.message : "The review decision was not recorded",
      });
    }
  }

  // Automatically acquire a genuine signed dev JWT from the API backend
  useEffect(() => {
    let mounted = true;
    if (session?.userKey) {
      requestDevToken(session.userKey).then((token) => {
        if (mounted && token && token !== session.accessToken) {
          setSession((prev) => (prev ? { ...prev, accessToken: token } : null));
        }
      });
    }
    return () => {
      mounted = false;
    };
  }, [session?.userKey, selectedProject]);

  useEffect(() => {
    let mounted = true;
    checkHealth(session?.accessToken)
      .then((res) => {
        if (mounted) setApiHealth(res.status === "ok" ? "ok" : "offline");
      })
      .catch(() => {
        if (mounted) setApiHealth("offline");
      });

    return () => {
      mounted = false;
    };
  }, [session?.accessToken]);

  useEffect(() => {
    if (activeTab !== "audit" || session?.role !== "admin" || !hasSignedToken) return;
    let mounted = true;
    fetchAuditEvents(session.accessToken, {
      projectId: filterProject,
      action: filterAction,
      actor: filterActor,
      resourceId: filterWorkflowRun,
      page: 1,
      pageSize: 50,
    })
      .then((res) => {
        if (!mounted) return;
        setAuditError(null);
        const mapped: AuditRecord[] = (res.items ?? []).map((it) => {
          const detailsObj =
            typeof it.details === "object" && it.details !== null
              ? (it.details as Record<string, unknown>)
              : {};
          // A run id is shown only when the event is about a run.
          const runId =
            (detailsObj.workflow_run_id as string | undefined) ||
            (it.resource_type === "workflow_run" ? it.resource_id : null);
          return {
            id: it.event_id,
            timestamp: it.timestamp,
            actor: it.actor_id,
            role: it.actor_role,
            project: it.project_id || "—",
            workflowRunId: runId ? String(runId) : "—",
            action: it.action,
            resource: it.resource_id || it.resource_type,
            details:
              typeof it.details === "string"
                ? it.details
                : JSON.stringify(it.details).replace(/[{}"]/g, " "),
          };
        });
        setAuditLogs(mapped);
      })
      .catch((err: unknown) => {
        if (!mounted) return;
        setAuditLogs([]);
        setAuditError(err instanceof Error ? err.message : "Could not load the audit log");
      });

    return () => {
      mounted = false;
    };
  }, [
    activeTab,
    session?.accessToken,
    session?.role,
    filterProject,
    filterAction,
    filterActor,
    filterWorkflowRun,
  ]);

  useEffect(() => {
    let mounted = true;
    setLibraryError(null);
    // Until the page has swapped its placeholder token for a signed one there is nothing to ask.
    if (!hasSignedToken) {
      setProjectDocs([]);
      return;
    }
    fetchProjectDocuments(selectedProject, session?.accessToken)
      .then((docs) => {
        if (mounted) setProjectDocs(docs);
      })
      .catch((err: unknown) => {
        if (!mounted) return;
        setProjectDocs([]);
        setLibraryError(err instanceof Error ? err.message : "Could not load the documents");
      });
    return () => {
      mounted = false;
    };
  }, [selectedProject, session?.accessToken]);

  // An answer belongs to the project it was asked in, so changing project clears it.
  useEffect(() => {
    setResearchResult(null);
    setQueryError(null);
    setActiveWorkflowRunId("");
    setAnswerReviewStatus("PENDING_EXPERT_REVIEW");
    setReviewFeedback(null);
  }, [selectedProject]);

  async function handleRunQuery(e: React.FormEvent) {
    e.preventDefault();
    const question = queryText.trim();
    if (!question) return;
    setQueryLoading(true);
    setQueryError(null);
    setReviewFeedback(null);
    try {
      const response = await submitResearchQuery(
        {
          query: question,
          project_id: selectedProject,
          top_k: topK,
        },
        session?.accessToken
      );
      setSelectedCitationIndex(0);
      setResearchResult(response);
      setActiveWorkflowRunId(response.workflow_run_id ?? "");
      setAnswerReviewStatus(response.review_status ?? "PENDING_EXPERT_REVIEW");
    } catch (err) {
      // No canned answer: a question that fails shows its error and nothing else.
      setResearchResult(null);
      setActiveWorkflowRunId("");
      setQueryError(err instanceof Error ? err.message : "The question could not be answered");
    } finally {
      setQueryLoading(false);
    }
  }

  // S2-10: Human-Review Gate. The server records the decision against the stored answer, takes the
  // reviewer from the sign-in token and the cited sources from the stored run. Nothing is shown as
  // recorded unless the server says it was.
  async function handleReviewDecision(decision: ReviewDecisionAction) {
    if (!session) return;
    if (!activeWorkflowRunId) {
      setReviewFeedback({
        type: "error",
        message: "Ask a question first: a decision is recorded against a stored answer.",
      });
      return;
    }
    const trimmedRationale = reviewRationale.trim();
    if (!trimmedRationale) {
      setReviewFeedback({
        type: "error",
        message:
          "Scientific rationale is required before recording a review decision (NFR-12 / FR-27).",
      });
      return;
    }
    setReviewFeedback(null);
    try {
      const entry = await submitReviewDecision(
        {
          workflow_run_id: activeWorkflowRunId,
          project_id: selectedProject,
          decision,
          rationale: trimmedRationale,
        },
        session.accessToken
      );
      setAnswerReviewStatus(decision);
      setReviewHistory((prev) => [entry, ...prev]);
      setReviewFeedback({
        type: "success",
        message: `Recorded ${decision} for ${entry.workflow_run_id} (audit event ${entry.audit_event_id}).`,
      });
    } catch (err) {
      setReviewFeedback({
        type: "error",
        message: err instanceof Error ? err.message : "The review decision was not recorded",
      });
    }
  }

  // S2-11: Export filtered audit records to RFC-4180 CSV
  function handleExportAuditCsv() {
    const headers = [
      "Event ID",
      "Timestamp (UTC)",
      "Actor",
      "Role",
      "Project",
      "Workflow Run ID",
      "Action",
      "Resource",
      "Audit Details",
    ];
    const rows = filteredAuditLogs.map((log) =>
      [
        escapeCsvCell(log.id),
        escapeCsvCell(log.timestamp),
        escapeCsvCell(log.actor),
        escapeCsvCell(log.role),
        escapeCsvCell(log.project),
        escapeCsvCell(log.workflowRunId),
        escapeCsvCell(log.action),
        escapeCsvCell(log.resource),
        escapeCsvCell(log.details),
      ].join(",")
    );
    const csvContent = [headers.map(escapeCsvCell).join(","), ...rows].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute(
      "download",
      `pharma-audit-log-${filterProject === "ALL" ? "all-projects" : filterProject.toLowerCase()}.csv`
    );
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  const filteredAuditLogs = auditLogs.filter((item) => {
    if (filterProject !== "ALL" && item.project !== filterProject) return false;
    if (filterAction !== "ALL" && item.action !== filterAction) return false;
    if (filterActor.trim() && !item.actor.toLowerCase().includes(filterActor.toLowerCase())) {
      return false;
    }
    if (
      filterWorkflowRun.trim() &&
      !item.workflowRunId.toLowerCase().includes(filterWorkflowRun.toLowerCase()) &&
      !item.resource.toLowerCase().includes(filterWorkflowRun.toLowerCase()) &&
      !item.details.toLowerCase().includes(filterWorkflowRun.toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  const activeCitation =
    researchResult && researchResult.citations.length > 0
      ? researchResult.citations[
          Math.min(selectedCitationIndex, researchResult.citations.length - 1)
        ]
      : null;
  const activeCitationProvenance = activeCitation
    ? resolveCitationProvenance(activeCitation, projectDocs)
    : null;

  function renderReviewStatusBadge(status: ReviewGateStatus) {
    if (status === "APPROVED") {
      return <span className="badge-verified">✓ Expert Reviewed &amp; Approved</span>;
    }
    if (status === "REJECTED") {
      return <span className="badge-unverified">✕ Rejected by Reviewer</span>;
    }
    if (status === "EVIDENCE_REQUESTED") {
      return <span className="badge-warning">⚑ More Evidence Requested</span>;
    }
    return (
      <span className="badge-warning">
        ⚠ Not yet reviewed by a domain expert (Decision D9 / NFR-12)
      </span>
    );
  }

  return (
    <div className="app-shell">
      {/* Top Header: Royal/Clinical Blue gradient (#1e3a8a to #1d4ed8) and white text */}
      <header className="top-header">
        <div className="brand-block">
          <h1>Pharma AI R&amp;D Platform</h1>
        </div>

        <div className="header-controls">
          <label className="header-control-group">
            <span className="header-label">Project:</span>
            <select
              aria-label="Select Active Project"
              value={selectedProject}
              onChange={(e) => setSelectedProject(e.target.value)}
              className="header-dropdown-select"
            >
              {[...new Set([selectedProject, ...DEV_PROJECT_IDS])].sort().map((id) => (
                <option key={id} value={id}>
                  {projectLabel(id)}
                </option>
              ))}
            </select>
          </label>

          <div
            className="health-pill header-health-pill"
            role="status"
            aria-live="polite"
            title="Backend Health Probe (GET /health)"
          >
            <span className={`dot ${apiHealth}`} />
            <span>
              {apiHealth === "ok"
                ? "Backend Connected"
                : apiHealth === "checking"
                  ? "Connecting..."
                  : "Backend Offline"}
            </span>
          </div>

          {session ? (
            <div className="sso-bar">
              <label className="header-control-group">
                <span className="header-label">Signed in as:</span>
                <select
                  aria-label="Switch signed-in user"
                  value={session.userKey}
                  onChange={(e) => signInAs(e.target.value)}
                  className="header-dropdown-select"
                >
                  {DEV_USERS.map((user) => (
                    <option key={user.key} value={user.key}>
                      {user.name} ({user.blurb})
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="btn-header-secondary"
                onClick={() => setSession(createDevSession(session.userKey))}
              >
                Refresh Token
              </button>
              <button
                type="button"
                className="btn-header-outline"
                onClick={() => setSession(null)}
              >
                Logout
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="btn-primary"
              onClick={() => signInAs(loginUser)}
            >
              OIDC Login (Keycloak PKCE)
            </button>
          )}
        </div>
      </header>

      {/* Primary Workspace Navigation Tabs (S1-08 & S1-12) */}
      <nav className="workspace-nav" aria-label="Primary Workspace Views">
        <button
          type="button"
          className={activeTab === "research" ? "nav-tab active" : "nav-tab"}
          onClick={() => setActiveTab("research")}
        >
          <svg
            className="tab-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25"
            />
          </svg>
          <span>1. Research &amp; Strategy</span>
        </button>
        <button
          type="button"
          className={activeTab === "trials" ? "nav-tab active" : "nav-tab"}
          onClick={() => setActiveTab("trials")}
        >
          <svg
            className="tab-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5m4.75-11.396C10.608 3.037 11.296 3 12 3c.704 0 1.392.037 2.25.104m-4.5 0v5.714m4.5-5.714v5.714c0 .597.237 1.17.659 1.591L19 14.5M5 14.5a3.75 3.75 0 003.75 3.75h6.5A3.75 3.75 0 0019 14.5m-14 0l2.25-2.25m11.75 2.25l-2.25-2.25"
            />
          </svg>
          <span>2. Trial Verification &amp; DOE</span>
        </button>
        <button
          type="button"
          className={activeTab === "reports" ? "nav-tab active" : "nav-tab"}
          onClick={() => setActiveTab("reports")}
        >
          <svg
            className="tab-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
            />
          </svg>
          <span>3. Report Review Studio</span>
        </button>
        <button
          type="button"
          className={activeTab === "queue" ? "nav-tab active" : "nav-tab"}
          onClick={() => setActiveTab("queue")}
        >
          <svg
            className="tab-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M8.25 6.75h12M8.25 12h12M8.25 17.25h12M3.75 6.75h.007v.008H3.75V6.75zm0 5.25h.007v.008H3.75V12zm0 5.25h.007v.008H3.75v-.008z"
            />
          </svg>
          <span>4. Review Queue (Reviewer)</span>
        </button>
        <button
          type="button"
          className={activeTab === "audit" ? "nav-tab active" : "nav-tab"}
          onClick={() => setActiveTab("audit")}
        >
          <svg
            className="tab-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9 12.75L11.25 15 15 9.75m-2.25-6.75A9.004 9.004 0 003 12c0 4.298 3.01 7.893 7.058 8.78.6.13 1.284.13 1.884 0A9.004 9.004 0 0021 12c0-3.95-2.545-7.309-6.09-8.487"
            />
          </svg>
          <span>5. Audit Log Viewer (Admin)</span>
        </button>
      </nav>

      {/* Main Content Area */}
      <main className="workspace-main">
        {!session ? (
          <section className="sso-login-gate" role="alert">
            <div className="sso-login-card">
              <div className="sso-gate-status-row">
                <span className="sso-status-pill">401 Authentication Required</span>
                <span className="spec-pill">OIDC Authorization Code + PKCE (S256)</span>
              </div>
              <h2>Pharma AI R&amp;D Platform — Enterprise OIDC Login</h2>
              <p className="muted">
                Unauthenticated access is blocked by the OIDC gateway. Select your enterprise
                persona and sign in via Keycloak / Microsoft Entra ID SSO to access project{" "}
                <strong>{selectedProject}</strong>.
              </p>

              <div className="sso-persona-grid" role="radiogroup" aria-label="Select SSO Persona">
                {DEV_USERS.map((user) => (
                  <button
                    key={user.key}
                    type="button"
                    className={
                      loginUser === user.key ? "sso-persona-card active" : "sso-persona-card"
                    }
                    onClick={() => setLoginUser(user.key)}
                  >
                    <div className="sso-persona-title">
                      {user.name} — {user.role}
                    </div>
                    <div className="sso-persona-sub">{user.email}</div>
                    <span className="spec-pill">
                      {user.projects.length > 0 ? user.projects.join(" + ") : "No project"}
                    </span>
                  </button>
                ))}
              </div>

              <div className="sso-login-actions">
                <button
                  type="button"
                  className="btn-primary btn-sso-large"
                  onClick={() => signInAs(loginUser)}
                >
                  Sign in with SSO (OIDC)
                </button>
              </div>
            </div>
          </section>
        ) : (
          <>
            {/* VIEW 1: RESEARCH & STRATEGY */}
            {activeTab === "research" && (
              <div className="two-column-grid">
                <section className="card" aria-labelledby="sources-heading">
                  <div className="section-title-row">
                    <h2 id="sources-heading">
                      Project Document Library — {selectedProject} ({projectDocs.length})
                    </h2>
                    <span className="spec-pill">S2-05 · ADR-007</span>
                  </div>
                  <p className="muted">
                    Extraction status, text-layer uncertainty flags, extracted text previews and
                    version history for <strong>{selectedProject}</strong>.
                  </p>

                  {libraryError && (
                    <div className="upload-status error" role="alert">
                      Could not load this project&apos;s documents: {libraryError}
                    </div>
                  )}
                  {!libraryError && hasSignedToken && projectDocs.length === 0 && (
                    <p className="muted">No documents are available to this project yet.</p>
                  )}

                  <ul className="source-list" aria-label="Project Ingested Documents">
                    {[...projectDocs]
                      .sort((a, b) => b.latest_version.version_number - a.latest_version.version_number)
                      .map((doc) => {
                        const ver = doc.latest_version;
                        const versions =
                          doc.versions && doc.versions.length > 0 ? doc.versions : [ver];
                        const isStatusOk = ver.extraction_status === "ok";
                        const isTextLayerFull = ver.text_layer === "yes";
                        return (
                          <li key={doc.document_id} className="source-item">
                            <div className="source-header">
                              <div className="doc-title-row">
                                <span className="doc-type-badge pdf-badge">PDF</span>
                                <strong>{doc.title}</strong>
                              </div>
                              <div className="doc-header-tags">
                                <span className="publisher-tag">{doc.authority}</span>
                                <code className="hash-code">{doc.source_id}</code>
                              </div>
                            </div>

                            <div className="doc-badges">
                              <span className={isStatusOk ? "badge-verified" : "badge-unverified"}>
                                {ver.extraction_status
                                  ? `Status: ${ver.extraction_status.toUpperCase()} (Extracted)`
                                  : "Status: NOT RECORDED"}
                              </span>
                              <span
                                className={isTextLayerFull ? "badge-verified" : "badge-warning"}
                              >
                                {isTextLayerFull
                                  ? "Text Layer: YES (Selectable)"
                                  : `⚠ Uncertainty Flag: Text Layer ${(ver.text_layer || "not recorded").toUpperCase()}`}
                              </span>
                              <span className="hash-code">
                                Latest: v{ver.version_number}
                                {ver.version_label ? ` (${ver.version_label})` : ""}
                              </span>
                            </div>

                            <div className="source-meta-grid">
                              <div className="meta-grid-item">
                                <span className="meta-item-label">Pages</span>
                                <span className="meta-item-value">{ver.page_count ?? "-"}</span>
                              </div>
                              <div className="meta-grid-item">
                                <span className="meta-item-label">Engine</span>
                                <span className="meta-item-value">{ver.extraction_method ?? "not recorded"}</span>
                              </div>
                              <div className="meta-grid-item">
                                <span className="meta-item-label">Size</span>
                                <span className="meta-item-value">{(ver.byte_size / 1024).toFixed(1)} KB</span>
                              </div>
                              <div className="meta-grid-item">
                                <span className="meta-item-label">Text Layer</span>
                                <span
                                  className={`meta-item-value ${isTextLayerFull ? "text-emerald" : "text-amber"}`}
                                >
                                  {isTextLayerFull ? "YES" : (ver.text_layer || "not recorded").toUpperCase()}
                                </span>
                              </div>
                            </div>

                            <code className="hash-code" title="SHA-256 Provenance Fingerprint">
                              SHA-256: {ver.sha256.slice(0, 24)}...
                            </code>

                            {ver.extraction_notes && ver.extraction_notes.length > 0 && (
                              <div className="attribution-note" role="note">
                                <strong>Extraction Uncertainty Notes:</strong>{" "}
                                {ver.extraction_notes.join("; ")}
                              </div>
                            )}

                            {doc.attribution && (
                              <div className="attribution-note">
                                <strong>Mandatory Attribution (ADR-002):</strong> {doc.attribution}
                              </div>
                            )}

                            {doc.preview_text && (
                              <div className="doc-preview-box" aria-label="Extracted Text Preview">
                                <strong>Extracted Text Preview:</strong>
                                {"\n"}
                                {doc.preview_text}
                              </div>
                            )}

                            <div className="version-history-box version-timeline">
                              <span className="version-history-title">
                                <strong>Version History ({versions.length}):</strong>
                              </span>
                              <div className="timeline-track">
                                {versions.map((v) => (
                                  <div key={v.version_id} className="timeline-item">
                                    <span className="timeline-dot" aria-hidden="true" />
                                    <div className="timeline-body">
                                      <span className="timeline-version-name">
                                        <strong>v{v.version_number}</strong>
                                        {v.version_label ? ` — ${v.version_label}` : ""}
                                      </span>
                                      <span className="timeline-meta">
                                        ({v.page_count ?? "?"}p, {v.extraction_status ?? "status not recorded"})
                                      </span>
                                      <code className="timeline-hash">
                                        SHA-256: {v.sha256.slice(0, 12)}...
                                      </code>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            </div>
                          </li>
                        );
                      })}
                  </ul>
                </section>

                {/* S2-08 & S2-10: Scientist Q&A UI + Inline Human-Review Gate */}
                <section className="card" aria-labelledby="qa-heading">
                  <div className="section-title-row">
                    <div className="section-title-with-icon">
                      <div className="header-icon-box header-icon-blue">
                        <svg
                          className="sparkle-book-icon"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25"
                          />
                        </svg>
                      </div>
                      <h2 id="qa-heading">
                        Evidence-Grounded Q&amp;A Assistant ({selectedProject})
                      </h2>
                    </div>
                    <div className="doc-badges">
                      <span className="hash-code">
                        Scope: {projectDocs.length} Project Docs + Approved Corpus
                      </span>
                      <span className="spec-pill">S2-08 · FR-11 · FR-12</span>
                    </div>
                  </div>
                  <p className="muted">
                    Ask a formulation or regulatory question within{" "}
                    <strong>{selectedProject}</strong>. Every claim links to a clickable source
                    passage and document version; unsupported claims are flagged as evidence gaps.
                  </p>

                  {/* Sample Golden Questions Quick-Select */}
                  <div className="sample-questions-bar" aria-label="Sample Golden Questions">
                    <span className="sample-label">Golden Questions ({selectedProject}):</span>
                    <div className="sample-chips">
                      {(
                        GOLDEN_SAMPLE_QUESTIONS[selectedProject] ||
                        GOLDEN_SAMPLE_QUESTIONS["PRJ-001"]
                      ).map((item, idx) => (
                        <button
                          key={idx}
                          type="button"
                          className="chip-btn"
                          onClick={() => setQueryText(item.query)}
                        >
                          <span className="chip-icon" aria-hidden="true">
                            ✦
                          </span>{" "}
                          {item.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  <form onSubmit={handleRunQuery} className="query-form">
                    <label htmlFor="research-query-input" className="field-label">
                      Formulation or Regulatory Question:
                    </label>
                    <textarea
                      id="research-query-input"
                      rows={3}
                      value={queryText}
                      onChange={(e) => setQueryText(e.target.value)}
                      placeholder="Enter a scientific or regulatory question..."
                    />
                    <div className="form-row">
                      <div className="query-controls-group">
                        <label>
                          Publisher Filter:{" "}
                          <select
                            aria-label="Publisher Filter"
                            value={publisherFilter}
                            onChange={(e) => setPublisherFilter(e.target.value)}
                            disabled
                            title="Not available yet: questions search all of the project's documents"
                          >
                            <option value="ALL">All Approved Authorities (FDA, ICH, EMA)</option>
                            <option value="FDA">FDA Only</option>
                            <option value="ICH">ICH Only</option>
                          </select>
                        </label>
                        <label>
                          Passages (Top-K):{" "}
                          <select
                            aria-label="Top-K Passages"
                            value={topK}
                            onChange={(e) => setTopK(Number(e.target.value))}
                          >
                            <option value={2}>Top 2</option>
                            <option value={3}>Top 3</option>
                            <option value={5}>Top 5</option>
                          </select>
                        </label>
                      </div>
                      <button
                        type="submit"
                        className="btn-primary"
                        disabled={queryLoading || !hasSignedToken}
                        title={hasSignedToken ? undefined : "Waiting for the API to sign you in"}
                      >
                        {queryLoading ? "Retrieving Evidence..." : "Run Grounded Query"}
                      </button>
                    </div>
                  </form>
                  <p className="muted">
                    Answers are built only from quotes that appear word for word in the retrieved
                    passages. “Verified” means the quotes are really in the documents. It does not
                    mean they answer the question or are scientifically correct.
                  </p>

                  {!hasSignedToken && (
                    <div className="upload-status error" role="alert">
                      Not signed in to the API. Questions need a signed token: start the API with
                      AUTH_MODE=dev (see the README), then reload this page.
                    </div>
                  )}
                  {queryError && (
                    <div className="upload-status error" role="alert">
                      {queryError}
                    </div>
                  )}

                  {researchResult && (
                    <div className="answer-box" aria-live="polite">
                      {/* Decision D9 & NFR-12 Review Status Banner */}
                      {researchResult.data_source === "built_in_sample" && (
                        <div className="upload-status error" role="alert">
                          SAMPLE DATA: this answer comes from invented sample passages, not from the
                          project&apos;s documents. Do not treat it as evidence.
                        </div>
                      )}
                      <div className="expert-review-banner" role="note">
                        {renderReviewStatusBadge(answerReviewStatus)}
                        <span className="expert-review-note">
                          Workflow Run: <code>{activeWorkflowRunId || "—"}</code> — Answers cannot
                          be marked reviewed without an audited human sign-off event.
                          {researchResult.answer_mode && (
                            <>
                              {" "}
                              Quotes chosen by: <code>{researchResult.answer_mode}</code>.
                            </>
                          )}
                        </span>
                      </div>

                      <div className="answer-status-bar">
                        <div className="doc-badges">
                          <span
                            className={
                              !researchResult.verified
                                ? "badge-unverified"
                                : researchResult.evidence_gaps.length > 0
                                  ? "badge-warning"
                                  : "badge-verified"
                            }
                          >
                            {researchResult.verified
                              ? researchResult.evidence_gaps.length > 0
                                ? `⚠ PARTLY SUPPORTED: ${researchResult.citations.length} CITATION(S), BUT GAPS ARE FLAGGED BELOW`
                                : `✓ GROUNDED IN ${researchResult.citations.length} CITATION(S)`
                              : researchResult.citations.length > 0
                                ? "⚠ PARTLY UNVERIFIED: some quotes were removed (see the gaps below)"
                                : "⚠ UNSUPPORTED / WITHHELD (FAIL-CLOSED)"}
                          </span>
                          <span className="hash-code">Project Context: {selectedProject}</span>
                        </div>
                        <span
                          className="lexical-score-pill"
                          title="Share of the question's distinctive words found in the quoted text. A coverage figure, not a probability that the answer is right."
                        >
                          Question Words Found in Quotes:{" "}
                          {(researchResult.confidence_score * 100).toFixed(0)}%
                        </span>
                      </div>

                      {/* Synthesized Answer with Inline Clickable Citation Badges */}
                      <div className="synthesized-answer-panel">
                        <div className="synthesized-answer-header">
                          <strong>Quoted Evidence:</strong>
                          {researchResult.citations.length > 0 && (
                            <div className="inline-citation-strip" aria-label="Inline Citations">
                              <span>Jump to Citation:</span>
                              {researchResult.citations.map((cit, idx) => (
                                <button
                                  key={idx}
                                  type="button"
                                  className={
                                    selectedCitationIndex === idx
                                      ? "citation-badge-btn active"
                                      : "citation-badge-btn"
                                  }
                                  onClick={() => setSelectedCitationIndex(idx)}
                                  title={`${cit.document_title} (${cit.section}, p. ${pageLabel(cit.page, cit.page_end)})`}
                                >
                                  [{idx + 1}] {cit.source_id} (p. {pageLabel(cit.page, cit.page_end)})
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                        <pre className="answer-text">{researchResult.answer}</pre>
                      </div>

                      {/* Interactive Citation & Source Passage Inspector Panel (S2-08) */}
                      {researchResult.citations.length > 0 && (
                        <div className="citations-block" aria-label="Citation Inspector Panel">
                          <h3>
                            Traceable Citations &amp; Source Passage Inspector (
                            {researchResult.citations.length})
                          </h3>
                          <div className="citation-inspector-grid">
                            <div className="citation-list-col">
                              {researchResult.citations.map((cit, idx) => {
                                const prov = resolveCitationProvenance(cit, projectDocs);
                                const isSelected = selectedCitationIndex === idx;
                                return (
                                  <div
                                    key={idx}
                                    role="button"
                                    tabIndex={0}
                                    aria-label={`Inspect citation ${idx + 1} from ${cit.document_title}`}
                                    className={
                                      isSelected ? "citation-card selected" : "citation-card"
                                    }
                                    onClick={() => setSelectedCitationIndex(idx)}
                                    onKeyDown={(e) => {
                                      if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        setSelectedCitationIndex(idx);
                                      }
                                    }}
                                  >
                                    <div className="citation-card-header">
                                      <strong>
                                        [{idx + 1}] {cit.document_title}
                                      </strong>
                                      <span className="publisher-tag">{prov.authority}</span>
                                    </div>
                                    <div className="citation-loc">
                                      {cit.section} — <strong>Page {pageLabel(cit.page, cit.page_end)}</strong> | Version:{" "}
                                      <code>{prov.versionLabel}</code>
                                    </div>
                                    <blockquote>
                                      &ldquo;{cit.quoted_evidence.slice(0, 140)}
                                      {cit.quoted_evidence.length > 140 ? "..." : ""}&rdquo;
                                    </blockquote>
                                  </div>
                                );
                              })}
                            </div>

                            {activeCitation && activeCitationProvenance && (
                              <aside
                                className="passage-inspector-card"
                                aria-label="Selected Source Passage Detail"
                              >
                                <div className="inspector-header">
                                  <span className="publisher-tag">
                                    {activeCitationProvenance.authority}
                                  </span>
                                  <strong>
                                    Citation [{selectedCitationIndex + 1}] Source Passage &amp;
                                    Version Provenance
                                  </strong>
                                </div>
                                <div className="inspector-meta-grid">
                                  <div>
                                    <span>Document:</span>{" "}
                                    <strong>{activeCitation.document_title}</strong>
                                  </div>
                                  <div>
                                    <span>Source ID:</span>{" "}
                                    <code className="hash-code">{activeCitation.source_id}</code>
                                  </div>
                                  <div>
                                    <span>Document Version:</span>{" "}
                                    <code className="hash-code">
                                      {activeCitationProvenance.versionLabel}
                                    </code>
                                  </div>
                                  <div>
                                    <span>Section &amp; Page:</span>{" "}
                                    <strong>
                                      {activeCitation.section} (Page {pageLabel(activeCitation.page, activeCitation.page_end)})
                                    </strong>
                                  </div>
                                  <div>
                                    <span>SHA-256 Fingerprint:</span>{" "}
                                    <code className="hash-code">
                                      {activeCitationProvenance.sha256.slice(0, 24)}...
                                    </code>
                                  </div>
                                </div>

                                <div className="inspector-passage-box">
                                  <strong>Exact Verbatim Passage:</strong>
                                  <p>&ldquo;{activeCitation.quoted_evidence}&rdquo;</p>
                                </div>

                                <div className="attribution-note">
                                  <strong>Mandatory Attribution (ADR-002):</strong>{" "}
                                  {activeCitationProvenance.attribution}
                                </div>
                              </aside>
                            )}
                          </div>
                        </div>
                      )}

                      {/* Evidence Gaps, Unsupported Claims & Conflicts Section (S2-08) */}
                      <div
                        className="gaps-section-container"
                        aria-label="Evidence Gaps and Conflicts"
                      >
                        <h3>Evidence Gaps, Unsupported Claims &amp; Regulatory Conflicts</h3>
                        {researchResult.evidence_gaps.length > 0 ? (
                          <div className="gaps-block" role="alert">
                            <strong>
                              ⚠ {researchResult.evidence_gaps.length} Unsupported Claim / Evidence
                              Gap(s) Flagged:
                            </strong>
                            <ul>
                              {researchResult.evidence_gaps.map((gap, i) => (
                                <li key={i}>{gap}</li>
                              ))}
                            </ul>
                          </div>
                        ) : (
                          <div className="no-gaps-banner">
                            ✓ All query terms are supported by the retrieved passages above. No
                            missing terms or conflicting regulatory limits detected across cited
                            sources.
                          </div>
                        )}
                      </div>

                      {/* Retrieved Evidence Chunks & Similarity Scores */}
                      {researchResult.retrieved_evidence &&
                        researchResult.retrieved_evidence.length > 0 && (
                          <div className="retrieved-chunks-block">
                            <h3>
                              Retrieved Candidate Passages (
                              {researchResult.retrieved_evidence.length})
                            </h3>
                            <ul className="retrieved-chunks-list">
                              {researchResult.retrieved_evidence.map((chunk) => (
                                <li key={chunk.chunk_id} className="retrieved-chunk-item">
                                  <div className="retrieved-chunk-header">
                                    <span>
                                      <strong>{chunk.document_title}</strong> — {chunk.section} (p. {pageLabel(chunk.page, chunk.page_end)})
                                    </span>
                                    <code className="hash-code">
                                      Chunk: {chunk.chunk_id} | Score:{" "}
                                      {(chunk.similarity_score * 100).toFixed(0)}%
                                    </code>
                                  </div>
                                  <p className="retrieved-chunk-text">{chunk.text}</p>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}

                      {/* S2-10: Inline Human-Review Gate on Q&A Answer */}
                      <div className="review-gate-box" aria-label="Human-Review Gate Panel">
                        <div className="section-title-row">
                          <h3>Human-Review Gate</h3>
                          <div className="doc-badges">
                            {renderReviewStatusBadge(answerReviewStatus)}
                            <span className="spec-pill">S2-10 · NFR-12 · FR-27</span>
                          </div>
                        </div>
                        <label htmlFor="qa-review-rationale" className="field-label">
                          Reviewer Scientific Rationale (Mandatory for Audit Trail):
                        </label>
                        <textarea
                          id="qa-review-rationale"
                          rows={2}
                          value={reviewRationale}
                          onChange={(e) => setReviewRationale(e.target.value)}
                          placeholder="State scientific/regulatory verification rationale..."
                        />
                        {!canReview && (
                          <p className="muted">
                            Only a reviewer can record a decision. Switch the role in the top bar to
                            Reviewer (the server checks this too).
                          </p>
                        )}
                        <div className="button-group">
                          <button
                            type="button"
                            className="btn-approve"
                            disabled={!canReview || !activeWorkflowRunId}
                            onClick={() => handleReviewDecision("APPROVED")}
                          >
                            Approve Cited Answer
                          </button>
                          <button
                            type="button"
                            className="btn-danger"
                            disabled={!canReview || !activeWorkflowRunId}
                            onClick={() => handleReviewDecision("REJECTED")}
                          >
                            Reject Answer
                          </button>
                          <button
                            type="button"
                            className="btn-warning-action"
                            disabled={!canReview || !activeWorkflowRunId}
                            onClick={() => handleReviewDecision("EVIDENCE_REQUESTED")}
                          >
                            Request More Evidence
                          </button>
                        </div>
                        {reviewFeedback && (
                          <div
                            className={`upload-status ${reviewFeedback.type}`}
                            role="status"
                            aria-live="polite"
                          >
                            {reviewFeedback.message}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </section>
              </div>
            )}

            {/* VIEW 2: TRIAL VERIFICATION & DOE */}
            {activeTab === "trials" && (
              <section className="card" aria-labelledby="trials-heading">
                <div className="section-title-row">
                  <h2 id="trials-heading">
                    Trial Verification &amp; Design of Experiments (DOE) — {selectedProject}
                  </h2>
                  <span className="spec-pill">Deterministic DOE · Scipy</span>
                </div>
                <div className="upload-status error" role="note">
                  SAMPLE DATA: the batches below are invented for the interface. Trial verification
                  and DOE are not part of Sprint 2 and are not connected to any data.
                </div>
                <p className="muted">
                  Deterministic statistical evaluation (`scipy` / `statsmodels`) of formulation
                  batches against FDA and ICH specification limits.
                </p>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Batch / Trial ID</th>
                      <th>Excipient Grade (MCC Nitrite)</th>
                      <th>Antioxidant (Ascorbic Acid)</th>
                      <th>Dissolution (45 min)</th>
                      <th>NDMA Assay (40°C/75% RH)</th>
                      <th>FDA Limit (96 ng/day)</th>
                      <th>Verification Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>
                        <strong>DOE-RUN-101</strong>
                      </td>
                      <td>Standard MCC (1.2 ppm nitrite)</td>
                      <td>0.0% w/w</td>
                      <td>91.4%</td>
                      <td className="text-danger">118.0 ng/day</td>
                      <td>96.0 ng/day</td>
                      <td>
                        <span className="badge-unverified">OUT OF SPEC (OOS)</span>
                      </td>
                    </tr>
                    <tr>
                      <td>
                        <strong>DOE-RUN-102</strong>
                      </td>
                      <td>Low-Nitrite MCC (0.15 ppm)</td>
                      <td>0.5% w/w</td>
                      <td>94.2%</td>
                      <td className="text-success">19.4 ng/day</td>
                      <td>96.0 ng/day</td>
                      <td>
                        <span className="badge-verified">✓ VERIFIED PASS</span>
                      </td>
                    </tr>
                    <tr>
                      <td>
                        <strong>DOE-RUN-103</strong>
                      </td>
                      <td>Low-Nitrite MCC (0.15 ppm)</td>
                      <td>1.0% w/w</td>
                      <td>93.8%</td>
                      <td className="text-success">11.2 ng/day</td>
                      <td>96.0 ng/day</td>
                      <td>
                        <span className="badge-verified">✓ VERIFIED PASS</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </section>
            )}

            {/* VIEW 3: REPORT REVIEW STUDIO & HUMAN-REVIEW GATE (S2-10) */}
            {activeTab === "reports" && (
              <section className="card" aria-labelledby="reports-heading">
                <div className="section-title-row">
                  <h2 id="reports-heading">
                    Product Development Report (PDR) &amp; Human-Review Gate
                  </h2>
                  <span className="spec-pill">S2-10 · NFR-12 · FR-27</span>
                </div>
                <div className="upload-status error" role="note">
                  SAMPLE DATA: this report draft and its trial figures are invented for the
                  interface. Report drafting is not part of Sprint 2. Review decisions on answers
                  are recorded on the Research tab.
                </div>
                <div className="report-meta-bar">
                  <span>
                    Document: <strong>CTD 3.2.P.2.2 Formulation Development Draft (v1.3)</strong>
                  </span>
                  <span>
                    Workflow Run: <code>{activeWorkflowRunId || "—"}</code>
                  </span>
                  <span>Gate Status: {renderReviewStatusBadge(answerReviewStatus)}</span>
                </div>

                <div className="report-draft-preview">
                  <h3>Section 3.2.P.2.2 — Nitrosamine &amp; Elemental Impurity Control Strategy</h3>
                  <p>
                    Per{" "}
                    <strong>
                      FDA Guidance for Industry: Control of Nitrosamine Impurities (Rev 1, Section
                      III, p. 10)
                    </strong>
                    , the acceptable intake limit for NDMA is established at{" "}
                    <strong>96 ng/day</strong>. Experimental DOE trials (
                    <strong>DOE-RUN-102</strong> and <strong>DOE-RUN-103</strong>) demonstrate that
                    substituting low-nitrite microcrystalline cellulose (0.15 ppm) combined with
                    0.5–1.0% w/w ascorbic acid maintains NDMA formation below 20 ng/day under
                    accelerated stability.
                  </p>
                  <p>
                    Per <strong>ICH Q3D(R2) (Section 5.1, p. 12)</strong>, Class 1 elemental
                    impurities are controlled below oral PDE thresholds: Cadmium (5.0 µg/day), Lead
                    (5.0 µg/day), Inorganic Arsenic (15.0 µg/day), and Inorganic Mercury (30.0
                    µg/day).
                  </p>
                </div>

                <div className="review-gate-box" aria-label="Report Review Gate">
                  <div className="section-title-row">
                    <h3>Human-in-the-Loop Sign-Off Gate</h3>
                    <span className="spec-pill">NFR-12 · FR-27 · FR-30</span>
                  </div>
                  <label htmlFor="review-rationale-input" className="field-label">
                    Reviewer Scientific Rationale (Required for Audit Trail):
                  </label>
                  <textarea
                    id="review-rationale-input"
                    rows={2}
                    value={reviewRationale}
                    onChange={(e) => setReviewRationale(e.target.value)}
                    disabled
                  />
                  <p className="muted">
                    Not available for report drafts yet. A decision here would be recorded against
                    the last answer asked on the Research tab, so use the review gate there.
                  </p>
                  <div className="button-group">
                    <button type="button" className="btn-approve" disabled>
                      Approve Controlled Draft
                    </button>
                    <button type="button" className="btn-danger" disabled>
                      Reject Draft
                    </button>
                    <button type="button" className="btn-warning-action" disabled>
                      Request Additional Evidence
                    </button>
                  </div>
                  {reviewFeedback && (
                    <div
                      className={`upload-status ${reviewFeedback.type}`}
                      role="status"
                      aria-live="polite"
                    >
                      {reviewFeedback.message}
                    </div>
                  )}
                </div>

                {/* S2-10: Review Decision History */}
                <div className="review-history-section" aria-label="Review Decision History">
                  <h3>Review Decision History ({reviewHistory.length})</h3>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Review ID</th>
                        <th>Timestamp (UTC)</th>
                        <th>Workflow Run</th>
                        <th>Project</th>
                        <th>Reviewer</th>
                        <th>Decision</th>
                        <th>Cited Sources</th>
                        <th>Scientific Rationale</th>
                        <th>Audit Event</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reviewHistory.map((rev) => (
                        <tr key={rev.review_id}>
                          <td>
                            <code>{rev.review_id}</code>
                          </td>
                          <td>{rev.timestamp}</td>
                          <td>
                            <code>{rev.workflow_run_id}</code>
                          </td>
                          <td>{rev.project_id}</td>
                          <td>
                            {rev.reviewer_email} ({rev.reviewer_role})
                          </td>
                          <td>
                            <span
                              className={
                                rev.decision === "APPROVED"
                                  ? "badge-verified"
                                  : rev.decision === "REJECTED"
                                    ? "badge-unverified"
                                    : "badge-warning"
                              }
                            >
                              {rev.decision}
                            </span>
                          </td>
                          <td>{rev.cited_sources.join(", ")}</td>
                          <td>{rev.rationale}</td>
                          <td>
                            <code>{rev.audit_event_id}</code>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {/* VIEW 4: ADMIN AUDIT LOG VIEWER (S1-12 & S2-11) */}
            {/* VIEW 4: REVIEW QUEUE (S2-10) */}
            {activeTab === "queue" && (
              <section className="card" aria-labelledby="queue-heading">
                <div className="section-title-row">
                  <h2 id="queue-heading">Review Queue — {selectedProject}</h2>
                  <span className="spec-pill">S2-10 · NFR-12 · FR-27</span>
                </div>
                {!canReview ? (
                  <p className="muted">
                    Only a reviewer can see the questions waiting for review. Sign in as a reviewer
                    (the server checks this too, and only for the reviewer's own projects).
                  </p>
                ) : (
                  <>
                    <div className="queue-controls">
                      <label className="field-label" htmlFor="queue-status">
                        Show:{" "}
                        <select
                          id="queue-status"
                          value={queueStatus}
                          onChange={(e) => setQueueStatus(e.target.value as ReviewGateStatus)}
                        >
                          <option value="PENDING_EXPERT_REVIEW">Waiting for review</option>
                          <option value="APPROVED">Approved</option>
                          <option value="REJECTED">Rejected</option>
                          <option value="EVIDENCE_REQUESTED">More evidence requested</option>
                        </select>
                      </label>
                      <button
                        type="button"
                        className="btn-header-outline"
                        onClick={() => setQueueRefresh((n) => n + 1)}
                        disabled={queueLoading}
                      >
                        {queueLoading ? "Loading..." : "Refresh"}
                      </button>
                      {queue && (
                        <span className="muted">
                          {queue.total} question(s)
                          {queue.total > queue.items.length
                            ? `, showing the first ${queue.items.length}`
                            : ""}
                          {queue.status === "PENDING_EXPERT_REVIEW" ? ", longest waiting first" : ""}
                        </span>
                      )}
                    </div>
                    {queueError && (
                      <div className="upload-status error" role="alert">
                        {queueError}
                      </div>
                    )}
                    {queueFeedback && (
                      <div
                        className={`upload-status ${queueFeedback.type}`}
                        role="status"
                        aria-live="polite"
                      >
                        {queueFeedback.message}
                      </div>
                    )}
                    {queue && queue.items.length === 0 && !queueError && (
                      <p className="muted">
                        {queue.status === "PENDING_EXPERT_REVIEW"
                          ? "Nothing is waiting for review in this project."
                          : "No questions with this status in this project."}
                      </p>
                    )}
                    <div className="two-column-grid">
                      <ul className="source-list queue-list" aria-label="Stored questions">
                        {(queue?.items ?? []).map((item) => (
                          <li key={item.run_id} className="source-item">
                            <button
                              type="button"
                              className={
                                item.run_id === openRunId ? "queue-item active" : "queue-item"
                              }
                              onClick={() => {
                                setOpenRunId(item.run_id);
                                setQueueFeedback(null);
                              }}
                            >
                              <strong>{item.question}</strong>
                              <span className="muted">
                                Asked by {item.asked_by} ·{" "}
                                {new Date(item.created_at).toLocaleString()}
                              </span>
                              <span className="doc-badges">
                                <span
                                  className={
                                    item.withheld
                                      ? "badge-unverified"
                                      : item.evidence_gaps.length > 0
                                        ? "badge-warning"
                                        : "badge-verified"
                                  }
                                >
                                  {item.withheld
                                    ? "No answer given"
                                    : item.evidence_gaps.length > 0
                                      ? "Partly supported"
                                      : "Quotes verified"}
                                </span>
                                <span className="hash-code">{item.run_id}</span>
                              </span>
                            </button>
                          </li>
                        ))}
                      </ul>
                      <div className="queue-detail" aria-live="polite">
                        {!openRun ? (
                          <p className="muted">
                            Choose a question to read its answer and evidence.
                          </p>
                        ) : (
                          <>
                            <h3>{openRun.question}</h3>
                            <div className="doc-badges">
                              <span className="hash-code">{openRun.run_id}</span>
                              <span className="hash-code">Mode: {openRun.answer_mode}</span>
                              <span className="hash-code">
                                Question words found in quotes: {(openRun.coverage * 100).toFixed(0)}
                                %
                              </span>
                              {openRun.decided_by && (
                                <span className="hash-code">
                                  {openRun.status} by {openRun.decided_by}
                                </span>
                              )}
                            </div>
                            <div className="synthesized-answer-panel">
                              <div className="synthesized-answer-header">
                                <strong>Quoted Evidence:</strong>
                              </div>
                              <p className="answer-text">{openRun.answer}</p>
                            </div>
                            {openRun.citations.map((cit, index) => (
                              <div key={`${cit.source_id}-${index}`} className="attribution-note">
                                <strong>
                                  [{index + 1}] {cit.title} — {cit.source_id}, v{cit.version_number}
                                  {cit.version_label ? ` (${cit.version_label})` : ""}, page{" "}
                                  {pageLabel(cit.page, cit.page_end)}
                                </strong>
                                {cit.section ? <div className="muted">{cit.section}</div> : null}
                                <em>“{cit.quoted_evidence}”</em>
                                <div className="hash-code">
                                  SHA-256: {cit.sha256.slice(0, 24)}...
                                </div>
                              </div>
                            ))}
                            {openRun.evidence_gaps.length > 0 && (
                              <div className="upload-status error">
                                <strong>Evidence gaps:</strong>
                                <ul>
                                  {openRun.evidence_gaps.map((gap) => (
                                    <li key={gap}>{gap}</li>
                                  ))}
                                </ul>
                              </div>
                            )}
                            <label htmlFor="queue-rationale" className="field-label">
                              Reviewer Scientific Rationale (Mandatory for Audit Trail):
                            </label>
                            <textarea
                              id="queue-rationale"
                              rows={3}
                              value={queueRationale}
                              onChange={(e) => setQueueRationale(e.target.value)}
                              placeholder="State scientific/regulatory verification rationale..."
                            />
                            <div className="button-group">
                              <button
                                type="button"
                                className="btn-approve"
                                disabled={openRun.withheld}
                                title={
                                  openRun.withheld
                                    ? "No answer was given, so there is nothing to approve"
                                    : undefined
                                }
                                onClick={() => handleQueueDecision("APPROVED")}
                              >
                                Approve Cited Answer
                              </button>
                              <button
                                type="button"
                                className="btn-danger"
                                onClick={() => handleQueueDecision("REJECTED")}
                              >
                                Reject Answer
                              </button>
                              <button
                                type="button"
                                className="btn-warning-action"
                                onClick={() => handleQueueDecision("EVIDENCE_REQUESTED")}
                              >
                                Request More Evidence
                              </button>
                            </div>
                          </>
                        )}
                      </div>
                    </div>
                  </>
                )}
              </section>
            )}

            {activeTab === "audit" && (
              <section className="card" aria-labelledby="audit-heading">
                <div className="section-title-row">
                  <div className="doc-badges">
                    <h2 id="audit-heading">Immutable Compliance Audit Log Viewer (Admin Only)</h2>
                    <span className="spec-pill">S2-11 · FR-30 · NFR-07</span>
                  </div>
                  {session.role === "admin" && (
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={handleExportAuditCsv}
                      aria-label="Export Audit Log CSV"
                    >
                      Export Audit Log CSV
                    </button>
                  )}
                </div>

                {session.role !== "admin" ? (
                  <div className="forbidden-banner" role="alert">
                    <h3>403 Forbidden — Insufficient Role Permissions</h3>
                    <p>
                      Current role (<code>{session.role}</code>) is not authorized to view
                      system-wide audit records. Switch OIDC role to <strong>Admin</strong> in the
                      top bar to inspect audit events.
                    </p>
                  </div>
                ) : (
                  <>
                    <p className="muted">
                      Reconstruct end-to-end provenance across document ingestion, chunk indexing,
                      evidence retrieval, and human-review sign-offs. Filter by project or workflow
                      run and export CSV for regulatory inspection.
                    </p>

                    <div className="filter-bar">
                      <label>
                        Project:{" "}
                        <select
                          aria-label="Filter Audit by Project"
                          value={filterProject}
                          onChange={(e) => setFilterProject(e.target.value)}
                        >
                          <option value="ALL">All Projects</option>
                          <option value="PRJ-001">PRJ-001</option>
                          <option value="PRJ-002">PRJ-002</option>
                        </select>
                      </label>
                      <label>
                        Action:{" "}
                        <select
                          aria-label="Filter Audit by Action"
                          value={filterAction}
                          onChange={(e) => setFilterAction(e.target.value)}
                        >
                          <option value="ALL">All Actions</option>
                          <option value="DOCUMENT_INGESTED">DOCUMENT_INGESTED</option>
                          <option value="DOCUMENT_ALREADY_INGESTED">
                            DOCUMENT_ALREADY_INGESTED
                          </option>
                          <option value="DOCUMENT_INGEST_REFUSED">DOCUMENT_INGEST_REFUSED</option>
                          <option value="DOCUMENT_INDEXED">DOCUMENT_INDEXED</option>
                          <option value="DOCUMENTS_LISTED">DOCUMENTS_LISTED</option>
                          <option value="RESEARCH_QUERY_EXECUTED">RESEARCH_QUERY_EXECUTED</option>
                          <option value="ANSWER_REVIEW_APPROVED">ANSWER_REVIEW_APPROVED</option>
                          <option value="ANSWER_REVIEW_REJECTED">ANSWER_REVIEW_REJECTED</option>
                          <option value="ANSWER_EVIDENCE_REQUESTED">
                            ANSWER_EVIDENCE_REQUESTED
                          </option>
                          <option value="TRIAL_VERIFICATION_APPROVED">
                            TRIAL_VERIFICATION_APPROVED
                          </option>
                          <option value="REPORT_SECTION_APPROVED">REPORT_SECTION_APPROVED</option>
                          <option value="REPORT_EVIDENCE_REQUESTED">
                            REPORT_EVIDENCE_REQUESTED
                          </option>
                          <option value="ACCESS_DENIED">ACCESS_DENIED</option>
                        </select>
                      </label>
                      <label>
                        Workflow Run / Resource:{" "}
                        <input
                          type="text"
                          aria-label="Filter by Workflow Run"
                          placeholder="e.g. RUN-2026-101 or ICH-Q3DR2"
                          value={filterWorkflowRun}
                          onChange={(e) => setFilterWorkflowRun(e.target.value)}
                        />
                      </label>
                      <label>
                        Actor Filter:{" "}
                        <input
                          type="text"
                          aria-label="Filter by Actor"
                          placeholder="Search user email..."
                          value={filterActor}
                          onChange={(e) => setFilterActor(e.target.value)}
                        />
                      </label>
                    </div>

                    {auditError && (
                      <div className="upload-status error" role="alert">
                        Could not load the audit log: {auditError}
                      </div>
                    )}
                    {!auditError && filteredAuditLogs.length === 0 && (
                      <p className="muted">No audit events match these filters.</p>
                    )}
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Event ID</th>
                          <th>Timestamp (UTC)</th>
                          <th>Actor</th>
                          <th>Role</th>
                          <th>Project</th>
                          <th>Workflow Run</th>
                          <th>Action</th>
                          <th>Resource</th>
                          <th>Audit Details</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredAuditLogs.map((log) => (
                          <tr key={log.id}>
                            <td>
                              <code>{log.id}</code>
                            </td>
                            <td>{log.timestamp}</td>
                            <td>{log.actor}</td>
                            <td>{log.role}</td>
                            <td>{log.project}</td>
                            <td>
                              <code>{log.workflowRunId}</code>
                            </td>
                            <td>
                              <code>{log.action}</code>
                            </td>
                            <td>{log.resource}</td>
                            <td>{log.details}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}

export default App;
