export type OperatorRole = "ADMIN" | "SUPERVISOR" | "SALES_REP" | "CUSTOMER";

export interface BrowserSession {
  authenticated: true;
  actor: { actor_id: string; display_name: string; roles: OperatorRole[] };
  tenant: { tenant_id: string };
  csrf_token: string;
  expires_at: string;
}

export interface SessionSummary {
  session_id: string;
  version: number;
  status: "CREATED" | "ACTIVE" | "ENDING" | "ENDED" | "FAILED";
  customer: { name: string; company: string | null };
  current_intent: string | null;
  buying_stage: string;
  qualification_score: number;
  next_best_action: string | null;
  outcome: string | null;
  started_at: string;
  updated_at: string;
  ended_at: string | null;
}

export interface SessionPage {
  items: SessionSummary[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface TranscriptMessage {
  id: string;
  speaker: "CUSTOMER" | "ASSISTANT" | "HUMAN_AGENT" | "SYSTEM";
  source: string;
  content: string;
  interrupted: boolean;
  created_at: string;
}

export interface SessionDetail extends SessionSummary {
  transcript: TranscriptMessage[];
  requirements: Array<{ field: string; value: unknown; currency: string | null; confidence: number; version: number }>;
  objections: Array<{ id: string; category: string; status: string; detail: string; version: number }>;
  operations: {
    meetings: Array<Record<string, unknown>>;
    followups: Array<Record<string, unknown>>;
    handoffs: Array<Record<string, unknown>>;
  };
}

export interface Analytics {
  range: { days: number; from: string; to: string };
  totals: {
    sessions: number;
    active: number;
    completed: number;
    average_qualification: number;
    average_duration_seconds: number;
  };
  conversion_rate: number;
  outcomes: Array<{ outcome: string; count: number }>;
  series: Array<{ at: string; sessions: number; converted: number }>;
}

export interface IntegrationStatus {
  mcp: { status: "available" | "degraded" | "unavailable" };
  providers: Array<{
    provider: string;
    counts: Record<string, number>;
    last_activity_at: string | null;
  }>;
  registrations: McpServerRegistration[];
}

export type McpCapability = "KNOWLEDGE" | "CRM" | "CALENDAR" | "MESSAGING" | "HANDOFF";

export interface McpServerRegistration {
  id: string;
  display_name: string;
  server_url: string;
  transport: "STREAMABLE_HTTP" | "SSE";
  auth_scheme: "NONE" | "BEARER" | "OAUTH2";
  capabilities: McpCapability[];
  status: "requested" | "validating" | "active" | "rejected" | "deactivated" | "failed";
  safe_status_detail: string;
  created_at: string;
  updated_at: string;
}

export interface SystemStatus {
  status: "ok" | "degraded";
  checks: Record<string, string>;
  service: string;
  schema_revision: string;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  domain: string;
  classification: string;
  document_version: number;
  status: "PENDING_INDEX" | "ACTIVE" | "INACTIVE" | "INDEX_FAILED";
  source_uri: string;
  created_at: string;
  updated_at: string;
}

export interface ApiProblemBody {
  error?: { code?: string; message?: string };
}
