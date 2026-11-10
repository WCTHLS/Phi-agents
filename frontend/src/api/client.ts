/**
 * Typed OpenAPI Client for Pharma AI Platform Backend
 */

export interface HealthResponse {
  status: string;
}

export interface Citation {
  source_id: string;
  document_title: string;
  section: string;
  /** The page the quote starts on. page_end is set when the quote runs onto another page. */
  page: number;
  quoted_evidence: string;
  chunk_id?: string | null;
  page_end?: number | null;
  version_id?: string | null;
  version_number?: number | null;
  version_label?: string | null;
  sha256?: string | null;
  attribution?: string | null;
}

export interface EvidenceChunk {
  chunk_id: string;
  source_id: string;
  document_title: string;
  section: string;
  /** The first page of the passage; it can run on to page_end. */
  page: number;
  page_end?: number | null;
  text: string;
  similarity_score: number;
  version_label?: string | null;
  version_number?: number | null;
  sha256?: string | null;
}

export interface ResearchQueryRequest {
  query: string;
  project_id?: string | null;
  publisher_filter?: string[] | null;
  top_k?: number;
}

export interface ResearchResponse {
  query: string;
  answer: string;
  citations: Citation[];
  evidence_gaps: string[];
  confidence_score: number;
  verified: boolean;
  retrieved_evidence: EvidenceChunk[];
  /** "project_documents": real passages. "built_in_sample": invented, never show as evidence. */
  data_source?: "project_documents" | "built_in_sample";
  workflow_run_id?: string | null;
  review_status?: ReviewGateStatus | null;
  /** "quotes-only" (word matching) or the name of the model that picked the quotes. */
  answer_mode?: string | null;
}

export type ReviewDecisionAction = "APPROVED" | "REJECTED" | "EVIDENCE_REQUESTED";
export type ReviewGateStatus = "PENDING_EXPERT_REVIEW" | ReviewDecisionAction;

/** The server looks up the question and cited sources from the stored run, so only these are sent. */
export interface ReviewDecisionRequest {
  workflow_run_id: string;
  project_id: string;
  decision: ReviewDecisionAction;
  rationale: string;
}

export interface ReviewHistoryEntry {
  review_id: string;
  workflow_run_id: string;
  project_id: string;
  decision: ReviewDecisionAction;
  rationale: string;
  reviewer_email: string;
  reviewer_role: string;
  cited_sources: string[];
  timestamp: string;
  audit_event_id: string;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

/**
 * An error message that says what the server said ("Not authorized for this project",
 * "Requires one of the roles: qa, reviewer", "Workflow run not found"), so a refused or failed
 * request is never shown as success or hidden behind sample data.
 */
async function failure(response: Response, what: string): Promise<Error> {
  let detail = "";
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else if (Array.isArray(body.detail)) detail = "the request was not valid";
  } catch {
    // The reply was not JSON; the status code alone will do.
  }
  return new Error(`${what} (${response.status}${detail ? `: ${detail}` : ""})`);
}

export async function checkHealth(accessToken?: string): Promise<HealthResponse> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(`${API_BASE}/health`, { headers });
  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }
  return (await response.json()) as HealthResponse;
}

export async function submitResearchQuery(
  payload: ResearchQueryRequest,
  accessToken?: string
): Promise<ResearchResponse> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(`${API_BASE}/api/v1/research/query`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await failure(response, "Question failed");
  }
  return (await response.json()) as ResearchResponse;
}

export async function submitReviewDecision(
  payload: ReviewDecisionRequest,
  accessToken?: string
): Promise<ReviewHistoryEntry> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(`${API_BASE}/api/v1/research/reviews`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await failure(response, "Review decision not recorded");
  }
  return (await response.json()) as ReviewHistoryEntry;
}

/** A stored question and answer in the reviewer's queue. The asker's e-mail is not included. */
export interface QueueCitation {
  source_id: string;
  title: string;
  version_number: number;
  version_label?: string | null;
  page: number;
  page_end?: number | null;
  section?: string | null;
  quoted_evidence: string;
  sha256: string;
  attribution?: string | null;
}

export interface QueuedRun {
  run_id: string;
  project_id: string;
  status: ReviewGateStatus;
  asked_by: string;
  created_at: string;
  question: string;
  answer: string;
  withheld: boolean;
  verified: boolean;
  answer_mode: string;
  coverage: number;
  evidence_gaps: string[];
  citations: QueueCitation[];
  decided_by?: string | null;
  decided_at?: string | null;
}

export interface ReviewQueue {
  project_id: string;
  status: ReviewGateStatus;
  total: number;
  items: QueuedRun[];
}

/** The project's stored questions with this review status (reviewers who belong to the project). */
export async function fetchReviewQueue(
  projectId: string,
  status: ReviewGateStatus,
  accessToken?: string
): Promise<ReviewQueue> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const query = new URLSearchParams({ project_id: projectId, status, limit: "50" });
  const response = await fetch(`${API_BASE}/api/v1/research/reviews?${query.toString()}`, {
    headers,
  });
  if (!response.ok) {
    throw await failure(response, "The review queue could not be loaded");
  }
  return (await response.json()) as ReviewQueue;
}

export interface AuditEventItem {
  event_id: string;
  timestamp: string;
  actor_id: string;
  actor_role: string;
  project_id?: string | null;
  action: string;
  resource_type: string;
  resource_id?: string | null;
  outcome: string;
  details: Record<string, unknown> | string;
}

export interface AuditEventsResponse {
  items: AuditEventItem[];
  total: number;
  page: number;
  page_size: number;
}

export async function fetchAuditEvents(
  accessToken?: string,
  filters?: {
    projectId?: string;
    actor?: string;
    action?: string;
    resourceId?: string;
    page?: number;
    pageSize?: number;
  }
): Promise<AuditEventsResponse> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const params = new URLSearchParams();
  if (filters?.projectId && filters.projectId !== "ALL") params.append("project_id", filters.projectId);
  if (filters?.actor && filters.actor.trim()) params.append("actor", filters.actor.trim());
  if (filters?.action && filters.action !== "ALL") params.append("action", filters.action);
  if (filters?.resourceId && filters.resourceId.trim()) {
    params.append("resource_id", filters.resourceId.trim());
  }
  if (filters?.page) params.append("page", String(filters.page));
  if (filters?.pageSize) params.append("page_size", String(filters.pageSize));

  const query = params.toString() ? `?${params.toString()}` : "";
  const response = await fetch(`${API_BASE}/api/v1/audit/events${query}`, { headers });
  if (!response.ok) {
    throw new Error(`Failed to load audit events (${response.status})`);
  }
  const result = (await response.json()) as AuditEventsResponse;
  return result;
}

export interface DocumentVersionInfo {
  version_id: string;
  version_number: number;
  version_label?: string | null;
  sha256: string;
  byte_size: number;
  page_count?: number | null;
  text_layer?: string | null;
  extraction_method?: string | null;
  extraction_status?: string | null;
  extraction_notes: string[];
  retrieved_at?: string | null;
}

export interface IngestedDocumentResponse extends DocumentVersionInfo {
  document_id: string;
  source_id: string;
  already_ingested: boolean;
  project_ids: string[];
  /** Passages stored for this version. 0 means it is not indexed, so questions cannot use it yet. */
  passages: number;
  /** Things to check before indexing, for example a PDF that does not look like its register entry. */
  warnings: string[];
}

export interface IndexedVersionResponse {
  source_id: string;
  version_number: number;
  /** indexed, reindexed, already_indexed or skipped. */
  status: string;
  passages: number;
  embedding_model: string;
  reason?: string | null;
}

export interface ProjectDocument {
  document_id: string;
  source_id: string;
  title: string;
  authority: string;
  attribution?: string | null;
  latest_version: DocumentVersionInfo;
  versions?: DocumentVersionInfo[];
  preview_text?: string | null;
}

export async function fetchProjectDocuments(
  projectId: string,
  accessToken?: string
): Promise<ProjectDocument[]> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(
    `${API_BASE}/api/v1/projects/${encodeURIComponent(projectId)}/documents`,
    { headers }
  );
  if (!response.ok) {
    throw new Error(`Failed to load project documents (${response.status})`);
  }
  return (await response.json()) as ProjectDocument[];
}

/** A project as the API describes it. The API only lists the projects the caller belongs to. */
export interface ProjectInfo {
  project_id: string;
  name: string;
  therapeutic_area?: string | null;
  dosage_form?: string | null;
}

export async function fetchMyProjects(accessToken?: string): Promise<ProjectInfo[]> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(`${API_BASE}/api/v1/projects`, { headers });
  if (!response.ok) {
    throw new Error(`Failed to load your projects (${response.status})`);
  }
  return (await response.json()) as ProjectInfo[];
}

/** One entry of the approved-source register, as the upload form needs it (administrators only). */
export interface RegisterEntry {
  source_id: string;
  title: string;
  authority: string;
  version_label?: string | null;
  eligible: boolean;
  hold_reason?: string | null;
  tier: string;
  stored: boolean;
  latest_version?: number | null;
  project_ids: string[];
  /** Passages of the newest version; 0 means uploaded but not indexed yet. */
  latest_passages: number;
  /** The version questions are answered from (null: none can be searched yet). */
  searchable_version?: number | null;
}

export async function fetchSourceRegister(accessToken?: string): Promise<RegisterEntry[]> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(`${API_BASE}/api/v1/sources`, { headers });
  if (!response.ok) {
    throw new Error(`Failed to load the approved-source register (${response.status})`);
  }
  return (await response.json()) as RegisterEntry[];
}

export async function uploadSourceDocument(params: {
  sourceId: string;
  projectId: string;
  fileBytes: ArrayBuffer | Uint8Array;
  versionLabel?: string;
  accessToken?: string;
}): Promise<IngestedDocumentResponse> {
  const headers: Record<string, string> = {
    "Content-Type": "application/pdf",
    Accept: "application/json",
  };
  if (params.accessToken) {
    headers.Authorization = `Bearer ${params.accessToken}`;
  }
  const query = new URLSearchParams();
  if (params.projectId) {
    query.append("project_id", params.projectId);
  }
  if (params.versionLabel && params.versionLabel.trim()) {
    query.append("version_label", params.versionLabel.trim());
  }
  const qs = query.toString() ? `?${query.toString()}` : "";
  const response = await fetch(
    `${API_BASE}/api/v1/sources/${encodeURIComponent(params.sourceId)}/versions${qs}`,
    {
      method: "PUT",
      headers,
      body: params.fileBytes as BodyInit,
    }
  );
  if (!response.ok) {
    let detail = `Upload failed (${response.status})`;
    try {
      const errJson = (await response.json()) as { detail?: string };
      if (errJson?.detail) detail = `${response.status}: ${errJson.detail}`;
    } catch {
      // keep default status text
    }
    throw new Error(detail);
  }
  return (await response.json()) as IngestedDocumentResponse;
}

/** Cut a stored version into passages so questions can use it (administrators only). */
export async function indexSourceVersion(params: {
  sourceId: string;
  versionNumber: number;
  accessToken?: string;
}): Promise<IndexedVersionResponse> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (params.accessToken) {
    headers.Authorization = `Bearer ${params.accessToken}`;
  }
  const response = await fetch(
    `${API_BASE}/api/v1/sources/${encodeURIComponent(params.sourceId)}/versions/${params.versionNumber}/index`,
    { method: "POST", headers }
  );
  if (!response.ok) {
    throw await failure(response, "Indexing failed");
  }
  return (await response.json()) as IndexedVersionResponse;
}
