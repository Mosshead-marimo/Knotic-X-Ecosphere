"use client";

import { useState } from "react";
import { Mic, MicOff, PhoneCall, PhoneOff } from "lucide-react";
import { useAgoraCall, type CallStatus } from "./useAgoraCall";

export interface VoiceCallPanelProps {
  sessionId: string;
  apiBaseUrl: string;
  csrfToken: string;
  mediaRegion: string;
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

// Visual-only indicator color per status; does not affect the CallStatus union or STATUS_LABEL copy.
const STATUS_DOT_CLASS: Record<CallStatus, string> = {
  idle: "bg-ops-muted",
  "requesting-permission": "bg-ops-blue animate-pulse",
  connecting: "bg-ops-blue animate-pulse",
  connected: "bg-ops-green",
  reconnecting: "bg-ops-amber animate-pulse",
  ending: "bg-ops-muted animate-pulse",
  ended: "bg-ops-muted",
  error: "bg-ops-red",
};

/**
 * Production Agora voice call UI (P4-T002).
 *
 * Every state -- requesting a device permission, connecting, reconnecting, and every error --
 * renders a visible status and, on failure, a concrete next step (retry or dismiss). Nothing here
 * ever claims a call, payment, booking, or CRM action succeeded; that authority stays with the
 * backend and its governed tools.
 */
export function VoiceCallPanel({ sessionId, apiBaseUrl, csrfToken, mediaRegion }: VoiceCallPanelProps) {
  const { status, errorMessage, muted, join, leave, toggleMute } = useAgoraCall(
    sessionId,
    apiBaseUrl,
    csrfToken,
    mediaRegion,
  );
  const [consentGiven, setConsentGiven] = useState(false);

  const isConnecting = status === "connecting" || status === "requesting-permission";
  const isActive = status === "connected" || status === "reconnecting";
  const isEnding = status === "ending";
  const showEndCallButton = isActive || isConnecting || isEnding;
  const canJoin = consentGiven && (status === "idle" || status === "ended" || status === "error");

  return (
    <section
      aria-label="Voice call"
      className="mx-auto w-full max-w-lg rounded-3xl border border-ops-border bg-ops-panel p-8 shadow-2xl"
    >
      <p className="mb-6 text-sm leading-relaxed text-ops-muted">
        Starting a call processes your audio in real time. Raw audio is not recorded by default.{" "}
        <label className="mt-2 inline-flex items-center gap-2 font-medium text-ops-text">
          <input
            type="checkbox"
            checked={consentGiven}
            onChange={(event) => setConsentGiven(event.target.checked)}
            disabled={showEndCallButton}
            className="h-4 w-4 rounded border-ops-border text-ops-blue focus-visible:ring-2 focus-visible:ring-ops-cyan focus-visible:ring-offset-2 focus-visible:ring-offset-ops-panel"
          />{" "}
          I consent to this call being processed.
        </label>
      </p>

      <p
        role="status"
        aria-live="polite"
        className="mb-2 flex items-center gap-2 text-sm font-semibold text-ops-text"
      >
        <span className={`h-2 w-2 rounded-full ${STATUS_DOT_CLASS[status]}`} aria-hidden="true" />
        {STATUS_LABEL[status]}
      </p>

      {status === "error" && errorMessage ? (
        <p role="alert" className="mb-4 rounded-xl border border-ops-red/30 bg-ops-red/10 p-3 text-sm text-ops-red">
          {errorMessage}
        </p>
      ) : null}

      <div className="mt-6 flex items-center gap-4">
        {showEndCallButton ? (
          <button
            type="button"
            onClick={() => void leave()}
            disabled={isEnding}
            className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl bg-ops-red px-6 py-3 text-base font-semibold text-white transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <PhoneOff className="h-5 w-5" aria-hidden="true" />
            End call
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void join()}
            disabled={!canJoin}
            className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl bg-ops-blue px-6 py-3 text-base font-semibold text-white transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <PhoneCall className="h-5 w-5" aria-hidden="true" />
            Start call
          </button>
        )}

        <button
          type="button"
          onClick={() => void toggleMute()}
          disabled={!isActive}
          aria-pressed={muted}
          className="inline-flex items-center justify-center gap-2 rounded-xl border border-ops-border bg-ops-raised px-6 py-3 text-base font-semibold text-ops-text transition-colors hover:bg-ops-soft disabled:cursor-not-allowed disabled:opacity-50"
        >
          {muted ? <MicOff className="h-5 w-5" aria-hidden="true" /> : <Mic className="h-5 w-5" aria-hidden="true" />}
          {muted ? "Unmute" : "Mute"}
        </button>
      </div>
    </section>
  );
}
