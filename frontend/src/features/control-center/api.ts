import type {
  Analytics,
  ApiProblemBody,
  BrowserSession,
  IntegrationStatus,
  KnowledgeDocument,
  McpCapability,
  McpServerRegistration,
  SessionDetail,
  SessionPage,
  SystemStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: "include",
    cache: "no-store",
    headers: { Accept: "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiProblemBody;
    throw new ApiError(body.error?.message ?? "The request could not be completed.", response.status, body.error?.code ?? "REQUEST_FAILED");
  }
  return (await response.json()) as T;
}

export const consoleApi = {
  session: () => request<BrowserSession>("/auth/session"),
  devSession: () => request<BrowserSession>("/auth/dev-session", { method: "POST" }),
  sessions: (query = "") => request<SessionPage>(`/console/sessions${query ? `?${query}` : ""}`),
  sessionDetail: (id: string) => request<SessionDetail>(`/console/sessions/${encodeURIComponent(id)}`),
  analytics: (days: number) => request<Analytics>(`/console/analytics?days=${days}`),
  integrations: () => request<IntegrationStatus>("/console/integrations"),
  requestMcpRegistration: (
    session: BrowserSession,
    input: {
      display_name: string;
      server_url: string;
      transport: "STREAMABLE_HTTP" | "SSE";
      auth_scheme: "NONE" | "BEARER" | "OAUTH2";
      capabilities: McpCapability[];
    },
  ) =>
    request<McpServerRegistration>("/console/integrations", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": session.csrf_token,
        "Idempotency-Key": crypto.randomUUID().replaceAll("-", "") + Date.now(),
      },
      body: JSON.stringify(input),
    }),
  system: () => request<SystemStatus>("/console/system"),
  knowledge: () => request<{ items: KnowledgeDocument[] }>("/console/knowledge/documents"),
  uploadKnowledge: (session: BrowserSession, file: File, domain: string) => {
    const body = new FormData();
    body.set("file", file);
    body.set("domain", domain);
    return request<{ id: string; status: string; indexing_status: string }>("/console/knowledge/documents", {
      method: "POST",
      headers: {
        "X-CSRF-Token": session.csrf_token,
        "Idempotency-Key": crypto.randomUUID().replaceAll("-", "") + Date.now(),
      },
      body,
    });
  },
  knowledgeAction: (session: BrowserSession, id: string, action: "deactivate" | "reindex") =>
    request<{ id: string; status: string }>(
      `/console/knowledge/documents/${encodeURIComponent(id)}/${action}`,
      {
        method: "POST",
        headers: {
          "X-CSRF-Token": session.csrf_token,
          "Idempotency-Key": crypto.randomUUID().replaceAll("-", "") + Date.now(),
        },
      },
    ),
  handoff: (session: BrowserSession, id: string, version: number, reason: string, priority: string) =>
    request<{ id: string; status: string }>(`/console/sessions/${encodeURIComponent(id)}/handoff`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": session.csrf_token,
        "Idempotency-Key": crypto.randomUUID().replaceAll("-", "") + Date.now(),
      },
      body: JSON.stringify({ reason, priority, expected_session_version: version }),
    }),
  logout: (session: BrowserSession) =>
    request<{ status: string }>("/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": session.csrf_token },
    }),
};
