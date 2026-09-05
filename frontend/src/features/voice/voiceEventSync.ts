export type VoiceControlEventType =
  | "CLIENT_READY"
  | "RTC_CONNECTED"
  | "RTC_RECONNECTING"
  | "RTC_DISCONNECTED"
  | "MICROPHONE_MUTED"
  | "MICROPHONE_UNMUTED"
  | "CALL_ENDED";

interface PendingVoiceEvent {
  event_id: string;
  stream_id: string;
  sequence: number;
  event_type: VoiceControlEventType;
  occurred_at: string;
  payload: Record<string, string | number | boolean | null>;
  schema_version: 1;
}

interface VoiceEventAck {
  acknowledged_sequence: number;
  server_sequence: number;
}

interface SequenceConflict {
  error?: { details?: { expected_sequence?: number } };
}

export function createUuid7(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  let timestamp = Date.now();
  for (let index = 5; index >= 0; index -= 1) {
    bytes[index] = timestamp % 256;
    timestamp = Math.floor(timestamp / 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x70;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * Per-tab ordered outbox for non-sensitive voice control events (P4-T006).
 *
 * sessionStorage is intentionally used instead of localStorage: tabs receive independent
 * streams, stale queues disappear with their tab, and neither credentials nor conversation
 * content are persisted. The backend remains authoritative for the session-wide order.
 */
export class VoiceEventSync {
  private readonly storageKey: string;
  private readonly streamId: string;
  private pending: PendingVoiceEvent[];
  private flushing: Promise<void> | null = null;

  constructor(
    private readonly apiBaseUrl: string,
    private readonly sessionId: string,
    private readonly csrfToken: string,
  ) {
    this.storageKey = `knotic:voice-events:${sessionId}`;
    const saved = this.readState();
    this.streamId = saved?.streamId ?? createUuid7();
    this.pending = saved?.pending ?? [];
    this.persist();
  }

  enqueue(eventType: VoiceControlEventType): Promise<void> {
    const sequence = (this.pending.at(-1)?.sequence ?? this.lastAcknowledged()) + 1;
    this.pending.push({
      event_id: createUuid7(),
      stream_id: this.streamId,
      sequence,
      event_type: eventType,
      occurred_at: new Date().toISOString(),
      payload: {},
      schema_version: 1,
    });
    this.persist();
    return this.flush();
  }

  flush(): Promise<void> {
    if (!this.flushing) {
      this.flushing = this.drain().finally(() => {
        this.flushing = null;
      });
    }
    return this.flushing;
  }

  private async drain(): Promise<void> {
    while (this.pending.length > 0 && navigator.onLine) {
      const event = this.pending[0];
      const response = await fetch(`${this.apiBaseUrl}/api/v1/sessions/${this.sessionId}/voice/events`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": this.csrfToken,
          "Idempotency-Key": event.event_id,
        },
        body: JSON.stringify(event),
      });
      if (response.status === 409) {
        const conflict = (await response.json()) as SequenceConflict;
        const expected = conflict.error?.details?.expected_sequence;
        if (expected === event.sequence + 1) {
          this.acknowledge(event.sequence);
          continue;
        }
        throw new Error(`Voice event stream requires sequence ${expected ?? "unknown"}.`);
      }
      if (!response.ok) {
        throw new Error(`Voice event synchronization failed (status ${response.status}).`);
      }
      const acknowledgement = (await response.json()) as VoiceEventAck;
      if (acknowledgement.acknowledged_sequence !== event.sequence) {
        throw new Error("Voice event acknowledgement did not match the queued event.");
      }
      this.acknowledge(event.sequence);
    }
  }

  private acknowledge(sequence: number): void {
    sessionStorage.setItem(`${this.storageKey}:ack`, String(sequence));
    this.pending = this.pending.filter((event) => event.sequence > sequence);
    this.persist();
  }

  private lastAcknowledged(): number {
    return Number(sessionStorage.getItem(`${this.storageKey}:ack`) ?? "0");
  }

  private persist(): void {
    sessionStorage.setItem(this.storageKey, JSON.stringify({ streamId: this.streamId, pending: this.pending }));
  }

  private readState(): { streamId: string; pending: PendingVoiceEvent[] } | null {
    try {
      const raw = sessionStorage.getItem(this.storageKey);
      return raw ? (JSON.parse(raw) as { streamId: string; pending: PendingVoiceEvent[] }) : null;
    } catch {
      sessionStorage.removeItem(this.storageKey);
      return null;
    }
  }
}
