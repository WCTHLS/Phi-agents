/**
 * Typed OpenAPI Client for Pharma AI Platform Backend
 */

export interface HealthResponse {
  status: string;
}

export interface DocumentMetadata {
  source_id: string;
  title: string;
  publisher: string;
  publication_date: string;
  version: string;
  url_or_identifier: string;
  content_hash: string;
  notes?: string | null;
}

export interface Citation {
  source_id: string;
  document_title: string;
  section: string;
  page: number;
  quoted_evidence: string;
  version_label?: string | null;
  sha256?: string | null;
  attribution?: string | null;
}

export interface EvidenceChunk {
  chunk_id: string;
  source_id: string;
  document_title: string;
  section: string;
  page: number;
  text: string;
  similarity_score: number;
  version_label?: string | null;
  sha256?: string | null;
}

export interface ResearchQueryRequest {
  query: string;
  project_id?: string | null;
  publisher_filter?: string[] | null;
  top_k?: number;
  strict_citation_mode?: boolean;
}

export interface ResearchResponse {
  query: string;
  answer: string;
  citations: Citation[];
  evidence_gaps: string[];
  confidence_score: number;
  verified: boolean;
  retrieved_evidence: EvidenceChunk[];
  workflow_run_id?: string | null;
  review_status?: ReviewGateStatus | null;
}

export type ReviewDecisionAction = "APPROVED" | "REJECTED" | "EVIDENCE_REQUESTED";
export type ReviewGateStatus = "PENDING_EXPERT_REVIEW" | ReviewDecisionAction;

export interface ReviewDecisionRequest {
  workflow_run_id: string;
  project_id: string;
  decision: ReviewDecisionAction;
  rationale: string;
  query: string;
  cited_sources: string[];
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

export async function fetchApprovedSources(accessToken?: string): Promise<DocumentMetadata[]> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(`${API_BASE}/api/v1/research/sources`, { headers });
  if (!response.ok) {
    throw new Error(`Failed to load approved sources (${response.status})`);
  }
  return (await response.json()) as DocumentMetadata[];
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
    throw new Error(`Research query failed (${response.status})`);
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
    throw new Error(`Review submission failed (${response.status})`);
  }
  return (await response.json()) as ReviewHistoryEntry;
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
