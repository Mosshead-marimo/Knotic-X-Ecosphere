"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { IAgoraRTCClient, IMicrophoneAudioTrack, IRemoteAudioTrack, UID } from "agora-rtc-sdk-ng";
import { createUuid7, VoiceEventSync, type VoiceControlEventType } from "./voiceEventSync";

/**
 * Realtime voice call lifecycle (P4-T002).
 *
 * The Agora App ID, App Certificate, and provider keys never reach this file: every value used
 * here comes from the short-lived token minted by the backend's `/voice/token` endpoint
 * (see `knotic_api.voice_api`). The Agora SDK is loaded with a dynamic `import()` inside `join`,
 * never at module scope, so this file stays safe to reference during server-side rendering.
 */

export type CallStatus =
  | "idle"
  | "requesting-permission"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "ending"
  | "ended"
  | "error";

interface VoiceTokenResponse {
  app_id: string;
  channel_name: string;
  uid: number;
  role: "PUBLISHER" | "SUBSCRIBER";
  token: string;
  issued_at: string;
  expires_at: string;
}

export interface UseAgoraCallResult {
  status: CallStatus;
  errorMessage: string | null;
  muted: boolean;
  audioBlocked: boolean;
  join: () => Promise<void>;
  leave: () => Promise<void>;
  toggleMute: () => Promise<void>;
  resumeAudio: () => void;
}

async function manageAgent(
  apiBaseUrl: string, sessionId: string, csrfToken: string, method: "POST" | "DELETE",
): Promise<{ agent_uid?: number; status: string }> {
  const response = await fetch(`${apiBaseUrl}/api/v1/sessions/${sessionId}/voice/agent`, {
    method,
    credentials: "include",
    headers: {
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": createUuid7(),
    },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`The managed voice agent could not be ${method === "POST" ? "started" : "stopped"} (status ${response.status}).`);
  return (await response.json()) as { agent_uid?: number; status: string };
}

async function requestVoiceToken(
  apiBaseUrl: string,
  sessionId: string,
  csrfToken: string,
  path: "/voice/token" | "/voice/token/renew",
  signal?: AbortSignal,
): Promise<VoiceTokenResponse> {
  const response = await fetch(`${apiBaseUrl}/api/v1/sessions/${sessionId}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrfToken },
    signal,
  });
  if (!response.ok) {
    throw new Error(`Voice token request failed (status ${response.status}).`);
  }
  return (await response.json()) as VoiceTokenResponse;
}

async function grantVoiceConsent(
  apiBaseUrl: string,
  sessionId: string,
  csrfToken: string,
  mediaRegion: string,
): Promise<void> {
  const consentId = createUuid7();
  const response = await fetch(`${apiBaseUrl}/api/v1/sessions/${sessionId}/voice/consent`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
      "Idempotency-Key": consentId,
    },
    body: JSON.stringify({
      consent_id: consentId,
      processing_allowed: true,
      recording_allowed: false,
      policy_version: "voice-processing-v1",
      media_region: mediaRegion,
    }),
    signal: AbortSignal.timeout(5_000),
  });
  if (!response.ok) {
    throw new Error(`Voice consent could not be recorded (status ${response.status}).`);
  }
}

async function withBoundedRetries<T>(operation: () => Promise<T>, attempts = 3): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      lastError = error;
      if (attempt < attempts) {
        await new Promise((resolve) => window.setTimeout(resolve, 250 * 2 ** (attempt - 1)));
      }
    }
  }
  throw lastError;
}

function describeJoinFailure(error: unknown): string {
  if (error instanceof DOMException && (error.name === "NotAllowedError" || error.name === "SecurityError")) {
    return "Microphone access was denied. Allow microphone access in your browser settings and try again.";
  }
  if (error instanceof DOMException && error.name === "NotFoundError") {
    return "No microphone was found. Connect a microphone and try again.";
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "The call could not be started.";
}

export function useAgoraCall(
  sessionId: string,
  apiBaseUrl: string,
  csrfToken: string,
  mediaRegion: string,
): UseAgoraCallResult {
  const [status, setStatus] = useState<CallStatus>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [audioBlocked, setAudioBlocked] = useState(false);
  const clientRef = useRef<IAgoraRTCClient | null>(null);
  const trackRef = useRef<IMicrophoneAudioTrack | null>(null);
  const eventSyncRef = useRef<VoiceEventSync | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const endingRef = useRef(false);
  const agentUidRef = useRef<number | null>(null);
  const remoteAudioRef = useRef(new Set<IRemoteAudioTrack>());

  const synchronize = useCallback(
    async (eventType: VoiceControlEventType) => {
      if (!eventSyncRef.current) {
        eventSyncRef.current = new VoiceEventSync(apiBaseUrl, sessionId, csrfToken);
      }
      await eventSyncRef.current.enqueue(eventType);
    },
    [apiBaseUrl, csrfToken, sessionId],
  );

  const synchronizeConnectionState = useCallback(
    (eventType: VoiceControlEventType) => {
      void synchronize(eventType).catch(() => {
        setErrorMessage("Call state could not be synchronized. Reconnect to continue safely.");
        setStatus("error");
      });
    },
    [synchronize],
  );

  const teardown = useCallback(async () => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    const track = trackRef.current;
    trackRef.current = null;
    if (track) {
      await track.setEnabled(false).catch(() => undefined);
      track.close();
    }
    const client = clientRef.current;
    clientRef.current = null;
    if (client) {
      try {
        await client.leave();
      } catch {
        // The client may already be disconnected; there is nothing further to clean up.
      }
    }
    remoteAudioRef.current.forEach((track) => track.stop());
    remoteAudioRef.current.clear();
    agentUidRef.current = null;
    setAudioBlocked(false);
  }, []);

  const leave = useCallback(async () => {
    endingRef.current = true;
    setStatus("ending");
    try {
      await Promise.race([
        synchronize("CALL_ENDED"),
        new Promise<void>((resolve) => window.setTimeout(resolve, 2_000)),
      ]).catch(() => undefined);
      await manageAgent(apiBaseUrl, sessionId, csrfToken, "DELETE").catch(() => undefined);
      await fetch(`${apiBaseUrl}/api/v1/sessions/${sessionId}/voice/token`, {
        method: "DELETE",
        credentials: "include",
        headers: { "X-CSRF-Token": csrfToken },
        signal: AbortSignal.timeout(3_000),
      }).catch(() => undefined);
    } finally {
      await teardown();
      setStatus("ended");
    }
  }, [apiBaseUrl, csrfToken, sessionId, synchronize, teardown]);

  const join = useCallback(async () => {
    setErrorMessage(null);
    setStatus("connecting");
    endingRef.current = false;
    try {
      await grantVoiceConsent(apiBaseUrl, sessionId, csrfToken, mediaRegion);
      const issued = await requestVoiceToken(apiBaseUrl, sessionId, csrfToken, "/voice/token");
      const { default: AgoraRTC } = await import("agora-rtc-sdk-ng");
      const client = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });
      clientRef.current = client;
      await synchronize("CLIENT_READY");

      client.on("connection-state-change", (nextState) => {
        if (nextState === "RECONNECTING") {
          setStatus("reconnecting");
          synchronizeConnectionState("RTC_RECONNECTING");
          if (reconnectTimerRef.current === null) {
            reconnectTimerRef.current = window.setTimeout(() => {
              setErrorMessage("The call could not reconnect within the recovery window.");
              setStatus("error");
              void teardown();
            }, 20_000);
          }
        } else if (nextState === "CONNECTED") {
          if (reconnectTimerRef.current !== null) {
            window.clearTimeout(reconnectTimerRef.current);
            reconnectTimerRef.current = null;
          }
          setStatus("connected");
          synchronizeConnectionState("RTC_CONNECTED");
        } else if (nextState === "DISCONNECTED") {
          setStatus((previous) => (previous === "error" || endingRef.current ? previous : "reconnecting"));
          synchronizeConnectionState("RTC_DISCONNECTED");
        }
      });
      client.on("token-privilege-will-expire", () => {
        void withBoundedRetries(() =>
          requestVoiceToken(
            apiBaseUrl,
            sessionId,
            csrfToken,
            "/voice/token/renew",
            AbortSignal.timeout(3_000),
          ),
        )
          .then((renewed) => client.renewToken(renewed.token))
          .catch(() => {
            setErrorMessage("The call's access token could not be renewed.");
            setStatus("error");
            void teardown();
          });
      });
      client.on("user-published", async (user, mediaType) => {
        if (mediaType !== "audio") return;
        await client.subscribe(user, mediaType);
        if (agentUidRef.current !== null && Number(user.uid) !== agentUidRef.current) return;
        if (user.audioTrack) {
          remoteAudioRef.current.add(user.audioTrack);
          try {
            user.audioTrack.play();
            setAudioBlocked(false);
          } catch {
            setAudioBlocked(true);
          }
        }
      });
      client.on("user-unpublished", (user, mediaType) => {
        if (mediaType === "audio" && user.audioTrack) {
          user.audioTrack.stop();
          remoteAudioRef.current.delete(user.audioTrack);
        }
      });

      setStatus("requesting-permission");
      const track = await AgoraRTC.createMicrophoneAudioTrack();
      trackRef.current = track;

      const uid: UID = issued.uid;
      await client.join(issued.app_id, issued.channel_name, issued.token, uid);
      await client.publish([track]);
      const agent = await manageAgent(apiBaseUrl, sessionId, csrfToken, "POST");
      if (agent.status !== "confirmed" || typeof agent.agent_uid !== "number") {
        throw new Error("The managed voice agent did not confirm its connection.");
      }
      agentUidRef.current = agent.agent_uid;
      setMuted(false);
      setStatus("connected");
    } catch (error) {
      setErrorMessage(describeJoinFailure(error));
      setStatus("error");
      await teardown();
    }
  }, [apiBaseUrl, sessionId, csrfToken, mediaRegion, synchronize, synchronizeConnectionState, teardown]);

  const toggleMute = useCallback(async () => {
    const track = trackRef.current;
    if (!track) {
      return;
    }
    const nextMuted = !muted;
    try {
      if (nextMuted) {
        await track.setEnabled(false);
        setMuted(true);
        await synchronize("MICROPHONE_MUTED");
      } else {
        await synchronize("MICROPHONE_UNMUTED");
        await track.setEnabled(true);
        setMuted(false);
      }
    } catch {
      await track.setEnabled(false).catch(() => undefined);
      setMuted(true);
      setErrorMessage("The microphone remains muted because its privacy state could not be confirmed.");
      setStatus("error");
    }
  }, [muted, synchronize]);

  const resumeAudio = useCallback(() => {
    try {
      remoteAudioRef.current.forEach((track) => track.play());
      setAudioBlocked(false);
    } catch {
      setAudioBlocked(true);
    }
  }, []);

  useEffect(
    () => () => {
      void teardown();
    },
    [teardown],
  );

  useEffect(() => {
    const flush = () => {
      void eventSyncRef.current?.flush().catch(() => {
        setErrorMessage("Call state could not be synchronized. Reconnect to continue safely.");
        setStatus("error");
      });
    };
    window.addEventListener("online", flush);
    return () => window.removeEventListener("online", flush);
  }, []);

  return { status, errorMessage, muted, audioBlocked, join, leave, toggleMute, resumeAudio };
}
