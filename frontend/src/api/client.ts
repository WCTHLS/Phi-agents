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
}

export interface EvidenceChunk {
  chunk_id: string;
  source_id: string;
  document_title: string;
  section: string;
  page: number;
  text: string;
  similarity_score: number;
}

export interface ResearchQueryRequest {
  query: string;
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
  if (filters?.page) params.append("page", String(filters.page));
  if (filters?.pageSize) params.append("page_size", String(filters.pageSize));

  const query = params.toString() ? `?${params.toString()}` : "";
  const response = await fetch(`${API_BASE}/api/v1/audit/events${query}`, { headers });
  if (!response.ok) {
    throw new Error(`Failed to load audit events (${response.status})`);
  }
  return (await response.json()) as AuditEventsResponse;
}

export async function recordAuditEvent(
  payload: {
    action: string;
    resource_type: string;
    resource_id?: string | null;
    project_id?: string | null;
    outcome?: string;
    details?: Record<string, unknown>;
  },
  accessToken?: string
): Promise<AuditEventItem> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(`${API_BASE}/api/v1/audit/events`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Failed to record audit event (${response.status})`);
  }
  return (await response.json()) as AuditEventItem;
}
