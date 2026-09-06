"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { ApiError, consoleApi } from "./api";
import type { BrowserSession, SessionSummary } from "./types";

interface ConsoleContextValue {
  session: BrowserSession | null;
  sessions: SessionSummary[];
  loading: boolean;
  liveStatus: "connecting" | "connected" | "degraded";
  error: string | null;
  refreshSessions: () => Promise<void>;
  signOut: () => Promise<void>;
}

const ConsoleContext = createContext<ConsoleContextValue | null>(null);

export function ConsoleProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<BrowserSession | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [liveStatus, setLiveStatus] = useState<ConsoleContextValue["liveStatus"]>("connecting");
  const [error, setError] = useState<string | null>(null);
  const refreshInFlight = useRef<Promise<void> | null>(null);

  const refreshSessions = useCallback(async () => {
    if (refreshInFlight.current) return refreshInFlight.current;
    const operation = consoleApi.sessions("limit=100").then((page) => {
      setSessions(page.items);
      setError(null);
    }).catch((cause: unknown) => {
      setError(cause instanceof Error ? cause.message : "Sessions could not be loaded.");
    }).finally(() => {
      refreshInFlight.current = null;
    });
    refreshInFlight.current = operation;
    return operation;
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      try {
        let activeSession: BrowserSession;
        try {
          activeSession = await consoleApi.session();
        } catch (cause) {
          if (!(cause instanceof ApiError) || cause.status !== 401) throw cause;
          try {
            activeSession = await consoleApi.devSession();
          } catch {
            window.location.replace(`/api/v1/auth/login?return_to=${encodeURIComponent(window.location.pathname)}`);
            return;
          }
        }
        if (!cancelled) setSession(activeSession);
        await refreshSessions();
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "The console could not be initialized.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void bootstrap();
    return () => { cancelled = true; };
  }, [refreshSessions]);

  useEffect(() => {
    if (!session) return;
    const events = new EventSource("/api/v1/console/stream", { withCredentials: true });
    events.addEventListener("ready", () => setLiveStatus("connected"));
    events.addEventListener("session.changed", () => void refreshSessions());
    events.addEventListener("degraded", () => setLiveStatus("degraded"));
    events.onerror = () => setLiveStatus("degraded");
    return () => events.close();
  }, [refreshSessions, session]);

  useEffect(() => {
    if (liveStatus !== "degraded" || !session) return;
    const timer = window.setInterval(() => void refreshSessions(), 15_000);
    return () => window.clearInterval(timer);
  }, [liveStatus, refreshSessions, session]);

  const signOut = useCallback(async () => {
    if (!session) return;
    await consoleApi.logout(session);
    window.location.replace("/");
  }, [session]);

  const value = useMemo(
    () => ({ session, sessions, loading, liveStatus, error, refreshSessions, signOut }),
    [session, sessions, loading, liveStatus, error, refreshSessions, signOut],
  );
  return <ConsoleContext.Provider value={value}>{children}</ConsoleContext.Provider>;
}

export function useConsole() {
  const value = useContext(ConsoleContext);
  if (!value) throw new Error("useConsole must be used inside ConsoleProvider");
  return value;
}
