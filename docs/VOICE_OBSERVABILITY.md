# Realtime Voice Observability

## Signal contract

Every semantic turn propagates one UUIDv7 `correlation_id` through the media worker, Flask,
LangGraph, governed MCP calls, speech synthesis, and playback. Traces also carry the authorized
`session_id`, `turn_id`, and `response_id` when those identifiers exist. Operational telemetry
must never contain raw audio, transcript/prompt text, customer fields, authorization data,
cookies, tokens, or unrestricted provider payloads.

The required stage spans are `voice.capture`, `voice.transcription`, `voice.workflow`,
`voice.tool`, `voice.synthesis`, `voice.first_audio`, and `voice.interruption`. Each records a
bounded `success`, `failure`, or `cancelled` outcome. First-audio measures accepted customer-turn
end through confirmed first AI audio publication. Interruption measures qualifying customer
voice activity through confirmed AI publication stop.

Metrics deliberately exclude identifiers. Permitted labels are stage, outcome, bounded component,
bounded safe error code, and bounded quality signal. Audit events remain durable PostgreSQL records
and are not sampled with traces.

## Operator workflow

1. Start with the Realtime Voice dashboard and identify the affected stage, failure component, and
   time window. Never add a session ID to a metric query.
2. Use the customer-visible safe error ID or UUIDv7 correlation ID to locate the distributed trace.
3. Confirm the ordered control-event watermark, recovery checkpoint, and durable semantic-turn
   checkpoint. Treat missing durable state as a failure; do not infer provider success.
4. If first-audio or interruption latency alerts fire, compare capture, transcription, workflow,
   tool, synthesis, and playback spans to isolate the contributor.
5. Follow the dependency recovery policy. Escalate sustained critical alerts and preserve redacted
   trace/audit references in the incident record, never copied transcript or audio.

## Alerts

- `VoiceFirstAudioLatencyHigh`: first-audio p95 exceeds 1.5 seconds for 10 minutes.
- `VoiceInterruptionLatencyHigh`: detection-to-stop p95 exceeds 300 ms for 5 minutes.
- `VoiceFailureRateHigh`: failed turn ratio exceeds 2% for 5 minutes.

Release thresholds and percentile certification are defined by `P4-T010`; these alert thresholds
are operational tripwires and do not by themselves certify a deployment.
