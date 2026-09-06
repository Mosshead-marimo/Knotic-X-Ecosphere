import { createUuid7 } from "./voiceEventSync";

/**
 * Bootstraps a real backend session for the "Start a demo call" CTA on the home page.
 *
 * The backend has no production identity provider wired in yet (see the comment in
 * `frontend/src/app/call/[sessionId]/page.tsx` and `backend/src/knotic_api/dev_auth_api.py`),
 * so this calls the development-only `/api/v1/auth/dev-session` bootstrap endpoint first to
 * obtain a browser-session cookie and CSRF token, then creates a real sales session the same
 * way any authenticated client would. That endpoint only exists when the backend is running
 * with `KNOTIC_ENV=development` or `test` -- never in staging or production.
 */
export class DemoCallBootstrapError extends Error {}

interface DevSessionResponse {
  csrf_token: string;
}

interface SessionResource {
  session_id: string;
}

function detectLocale(): string {
  if (typeof navigator !== "undefined" && navigator.language) {
    return navigator.language;
  }
  return "en-US";
}

function detectTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export async function startDemoCall(apiBaseUrl: string): Promise<{ sessionId: string; csrfToken: string }> {
  const bootstrap = await fetch(`${apiBaseUrl}/api/v1/auth/dev-session`, {
    method: "POST",
    credentials: "include",
    signal: AbortSignal.timeout(8_000),
  });
  if (!bootstrap.ok) {
    throw new DemoCallBootstrapError(`Could not start a demo session (status ${bootstrap.status}).`);
  }
  const { csrf_token: csrfToken } = (await bootstrap.json()) as DevSessionResponse;

  const created = await fetch(`${apiBaseUrl}/api/v1/sessions`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": createUuid7(),
    },
    body: JSON.stringify({ locale: detectLocale(), timezone: detectTimezone() }),
    signal: AbortSignal.timeout(8_000),
  });
  if (!created.ok) {
    throw new DemoCallBootstrapError(`Could not create a call session (status ${created.status}).`);
  }
  const { session_id: sessionId } = (await created.json()) as SessionResource;
  return { sessionId, csrfToken };
}
