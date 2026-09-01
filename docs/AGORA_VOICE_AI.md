# Agora Voice AI Guide

This guide translates the shared Agora session resources into a safe implementation path for Knotic. It is supporting guidance; the binding requirements and architecture remain in [`REQUIREMENTS.md`](REQUIREMENTS.md), [`System_Design.md`](System_Design.md), and [`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md).

## Pipeline used by Knotic

A basic voice agent is commonly described as:

```text
Microphone -> STT -> LLM -> TTS -> voice response
```

Knotic expands that pipeline so business decisions, tools, and state remain governed:

```text
Microphone
  -> Agora RTC
  -> speech-to-text
  -> Flask semantic-turn API
  -> LangGraph sales workflow
  -> MCP knowledge or business action, when needed
  -> approved response text
  -> text-to-speech
  -> Agora RTC playback
  -> customer
```

Agora is the realtime media plane. LangGraph remains the sales-orchestration authority, and MCP remains the boundary for external knowledge and actions. A realtime provider must not independently quote prices, update CRM records, book meetings, or claim an action succeeded.

## Fastest prototype path

The shared session recommends starting with Agora-managed providers when separate provider keys are not yet available:

| Stage | Managed starting option |
|---|---|
| Speech-to-text | Deepgram |
| Language model | OpenAI |
| Text-to-speech | MiniMax |

Agora's official recipes demonstrate this managed pipeline and describe it as keyless for the model provider. Knotic still requires an Agora project, App ID, and App Certificate. The App Certificate and every provider key are server-only secrets; the browser receives only short-lived, session-bound credentials minted by Flask.

The session announcement states that a new Agora signup includes 300 free Conversational AI minutes. Treat that as a prototyping allowance rather than a capacity or cost assumption: confirm the current entitlement, region, expiration, and billing terms in the Agora Console before planning a demo or deployment.

## Bring your own provider

Agora can also connect to alternate model and speech providers through provider-specific or OpenAI-compatible configuration. The session highlighted Gemini, Claude, Sarvam, and ElevenLabs as examples. Availability, supported features, credentials, regions, and data handling vary by integration.

For Knotic, adding or replacing a provider requires:

1. A typed adapter behind the existing provider-neutral boundary.
2. Server-side secret storage; no provider key may enter frontend code or browser-visible configuration.
3. Evaluation of latency, interruption, correctness, safety, retention, regional support, quotas, and cost.
4. Environment-by-environment rollout with a tested fallback or rollback configuration.
5. No change to the authority of LangGraph, MCP, Redis, PostgreSQL, or Agora defined by the accepted architecture decisions.

The current accepted production baseline is the OpenAI realtime adapter in ADR-004. Using Agora-managed providers for a hackathon prototype does not silently replace that decision.

## Recommended setup workflow

Agora's current coding-assistant quick start is:

1. Install the Agora CLI and verify `agora --help`.
2. Run `agora login`, select or create the intended Agora project, and write credentials only to an ignored local environment file or approved secret store.
3. Optionally install Agora's coding-agent skill for starter and workflow guidance.
4. Start from an official recipe that matches the platform and capability instead of inventing the RTC lifecycle.
5. Adapt the example to Knotic's Next.js, Flask, LangGraph, and MCP boundaries before merging it.

On Windows, the official guide currently provides this PowerShell installer:

```powershell
irm https://dl.agora.io/cli/install.ps1 | iex
agora --help
```

Review remote scripts before executing them in a development environment. Do not commit the generated App Certificate or provider keys. Knotic's configuration names and handling rules are documented in [`CONFIGURATION.md`](CONFIGURATION.md); placeholders are available in [`.env.example`](../.env.example).

## Recipes relevant to Knotic

The [Agora Voice AI recipes catalog](https://recipes.agora.io/) contains official and community samples filterable by platform and capability. These are implementation references, not drop-in architecture replacements.

| Recipe | Knotic use | Important adaptation |
|---|---|---|
| [Next.js quickstart](https://recipes.agora.io/recipes/nextjs-quickstart) | RTC/RTM client lifecycle and browser call UI | Keep credential minting and authorization in Flask. |
| [Python quickstart](https://recipes.agora.io/recipes/python-quickstart) | Agent lifecycle and server interaction reference | Preserve the mandatory Flask backend rather than copying a different web framework. |
| [Interruption handling](https://recipes.agora.io/recipes/interruptions) | Barge-in modes, cancellation behavior, and test ideas | Knotic requires deterministic stop/truncation metadata per FR-02 and ADR-005. |
| [MCP tools](https://recipes.agora.io/recipes/mcp-tools) | Agora-to-MCP protocol and public callback requirements | Route actions through Knotic's authenticated MCP gateway and deterministic policies; never expose an unauthenticated MCP endpoint. |
| [Tool calling](https://recipes.agora.io/recipes/tool-calling) | Function-call event flow and tool-result rendering | Do not let the realtime model bypass LangGraph or execute business actions directly. |
| [Server-side webhooks](https://recipes.agora.io/recipes/webhooks) | Agent lifecycle events and reconciliation | Authenticate callbacks, make handling idempotent, and correlate events with the session timeline. |
| [Event observability](https://recipes.agora.io/recipes/event-observability) | State, transcript, error, and per-stage latency signals | Join provider events to Knotic traces using the session correlation ID and redact content by default. |

Useful follow-on recipes include retrieval-augmented generation, custom LLM, dynamic tool sets, cross-session memory, content filtering, and agent handoff. Apply the same ownership and security constraints before borrowing their patterns.

## Prototype completion checklist

- The browser joins an authorized Agora channel with a short-lived token.
- The App Certificate and provider keys remain server-side.
- A complete microphone-to-response turn works through the selected STT, workflow/model, and TTS path.
- Speaking over the agent stops playback and records the delivered/truncated boundary.
- At least one agentic capability calls a governed MCP tool, such as pricing lookup, CRM update, calendar availability, or workflow trigger.
- The UI never reports payment, booking, CRM, or workflow success until the authoritative tool confirms it.
- Reconnect, provider failure, timeout, and clean end-call paths preserve state and fail safely.
- Per-stage latency and errors are observable without storing unnecessary raw audio or sensitive transcript content.
- The demo documents which providers are Agora-managed and which use bring-your-own keys.

## Source links

- [Shared public resource](https://x.com/akshay81844/status/2081942770965754230) — the source post may require an X login.
- [Agora Voice AI recipes](https://recipes.agora.io/)
- [Agora: Start with AI](https://docs.agora.io/en/introduction/start-with-ai)

Provider availability, promotional minutes, commands, and recipe behavior can change. Recheck the official documentation and Agora Console when implementing Phase 4 or preparing a release.
