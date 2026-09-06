"use client";

import { useEffect, useState } from "react";

import { consoleApi } from "../control-center/api";
import type { BrowserSession } from "../control-center/types";
import { VoiceCallPanel } from "./VoiceCallPanel";

export function AuthenticatedCall({ sessionId, mediaRegion }: { sessionId: string; mediaRegion: string }) {
  const [session, setSession] = useState<BrowserSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    void consoleApi.session().then((value) => { if (current) setSession(value); }).catch((cause: unknown) => {
      if (current) setError(cause instanceof Error ? cause.message : "Authentication is required.");
    });
    return () => { current = false; };
  }, []);
  if (error) return <p role="alert" className="rounded-xl border border-ops-red/30 bg-ops-red/10 p-4 text-ops-red">{error}</p>;
  if (!session) return <p role="status" className="text-sm text-ops-muted">Loading secure call session…</p>;
  return <VoiceCallPanel sessionId={sessionId} apiBaseUrl="" csrfToken={session.csrf_token} mediaRegion={mediaRegion} />;
}
