"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { IAgoraRTCClient, IMicrophoneAudioTrack, UID } from "agora-rtc-sdk-ng";
import { VoiceEventSync, type VoiceControlEventType } from "./voiceEventSync";

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
  join: () => Promise<void>;
  leave: () => Promise<void>;
  toggleMute: () => Promise<void>;
}

async function requestVoiceToken(
  apiBaseUrl: string,
  sessionId: string,
  csrfToken: string,
  path: "/voice/token" | "/voice/token/renew",
): Promise<VoiceTokenResponse> {
  const response = await fetch(`${apiBaseUrl}/api/v1/sessions/${sessionId}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrfToken },
  });
  if (!response.ok) {
    throw new Error(`Voice token request failed (status ${response.status}).`);
  }
  return (await response.json()) as VoiceTokenResponse;
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

export function useAgoraCall(sessionId: string, apiBaseUrl: string, csrfToken: string): UseAgoraCallResult {
  const [status, setStatus] = useState<CallStatus>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const clientRef = useRef<IAgoraRTCClient | null>(null);
  const trackRef = useRef<IMicrophoneAudioTrack | null>(null);
  const eventSyncRef = useRef<VoiceEventSync | null>(null);

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
    const track = trackRef.current;
    trackRef.current = null;
    if (track) {
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
  }, []);

  const leave = useCallback(async () => {
    setStatus("ending");
    await synchronize("CALL_ENDED").catch(() => undefined);
    await teardown();
    setStatus("ended");
  }, [synchronize, teardown]);

  const join = useCallback(async () => {
    setErrorMessage(null);
    setStatus("connecting");
    try {
      const issued = await requestVoiceToken(apiBaseUrl, sessionId, csrfToken, "/voice/token");
      const { default: AgoraRTC } = await import("agora-rtc-sdk-ng");
      const client = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });
      clientRef.current = client;
      await synchronize("CLIENT_READY");

      client.on("connection-state-change", (nextState) => {
        if (nextState === "RECONNECTING") {
          setStatus("reconnecting");
          synchronizeConnectionState("RTC_RECONNECTING");
        } else if (nextState === "CONNECTED") {
          setStatus("connected");
          synchronizeConnectionState("RTC_CONNECTED");
        } else if (nextState === "DISCONNECTED") {
          setStatus((previous) => (previous === "error" ? previous : "ended"));
          synchronizeConnectionState("RTC_DISCONNECTED");
        }
      });
      client.on("token-privilege-will-expire", () => {
        void requestVoiceToken(apiBaseUrl, sessionId, csrfToken, "/voice/token/renew")
          .then((renewed) => client.renewToken(renewed.token))
          .catch(() => {
            setErrorMessage("The call's access token could not be renewed.");
            setStatus("error");
          });
      });

      setStatus("requesting-permission");
      const track = await AgoraRTC.createMicrophoneAudioTrack();
      trackRef.current = track;

      const uid: UID = issued.uid;
      await client.join(issued.app_id, issued.channel_name, issued.token, uid);
      await client.publish([track]);
      setMuted(false);
      setStatus("connected");
    } catch (error) {
      setErrorMessage(describeJoinFailure(error));
      setStatus("error");
      await teardown();
    }
  }, [apiBaseUrl, sessionId, csrfToken, synchronize, synchronizeConnectionState, teardown]);

  const toggleMute = useCallback(async () => {
    const track = trackRef.current;
    if (!track) {
      return;
    }
    const nextMuted = !muted;
    await track.setEnabled(!nextMuted);
    setMuted(nextMuted);
    await synchronize(nextMuted ? "MICROPHONE_MUTED" : "MICROPHONE_UNMUTED");
  }, [muted, synchronize]);

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

  return { status, errorMessage, muted, join, leave, toggleMute };
}
