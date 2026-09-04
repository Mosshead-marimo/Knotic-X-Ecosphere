"use client";

import { useState } from "react";
import { useAgoraCall, type CallStatus } from "./useAgoraCall";

export interface VoiceCallPanelProps {
  sessionId: string;
  apiBaseUrl: string;
  csrfToken: string;
}

const STATUS_LABEL: Record<CallStatus, string> = {
  idle: "Ready to call.",
  "requesting-permission": "Requesting microphone access…",
  connecting: "Connecting…",
  connected: "Connected.",
  reconnecting: "Connection lost. Reconnecting…",
  ending: "Ending call…",
  ended: "Call ended.",
  error: "The call could not continue.",
};

/**
 * Production Agora voice call UI (P4-T002).
 *
 * Every state -- requesting a device permission, connecting, reconnecting, and every error --
 * renders a visible status and, on failure, a concrete next step (retry or dismiss). Nothing here
 * ever claims a call, payment, booking, or CRM action succeeded; that authority stays with the
 * backend and its governed tools.
 */
export function VoiceCallPanel({ sessionId, apiBaseUrl, csrfToken }: VoiceCallPanelProps) {
  const { status, errorMessage, muted, join, leave, toggleMute } = useAgoraCall(sessionId, apiBaseUrl, csrfToken);
  const [consentGiven, setConsentGiven] = useState(false);

  const isConnecting = status === "connecting" || status === "requesting-permission";
  const isActive = status === "connected" || status === "reconnecting";
  const canJoin = consentGiven && (status === "idle" || status === "ended" || status === "error");

  return (
    <section aria-label="Voice call">
      <p>
        Starting a call records and processes your audio to provide this service.{" "}
        <label>
          <input
            type="checkbox"
            checked={consentGiven}
            onChange={(event) => setConsentGiven(event.target.checked)}
            disabled={isActive || isConnecting}
          />{" "}
          I consent to this call being processed.
        </label>
      </p>

      <p role="status" aria-live="polite">
        {STATUS_LABEL[status]}
      </p>

      {status === "error" && errorMessage ? (
        <p role="alert">{errorMessage}</p>
      ) : null}

      <div>
        {isActive || isConnecting ? (
          <button type="button" onClick={() => void leave()} disabled={status === "ending"}>
            End call
          </button>
        ) : (
          <button type="button" onClick={() => void join()} disabled={!canJoin}>
            Start call
          </button>
        )}

        <button
          type="button"
          onClick={() => void toggleMute()}
          disabled={!isActive}
          aria-pressed={muted}
        >
          {muted ? "Unmute" : "Mute"}
        </button>
      </div>
    </section>
  );
}
