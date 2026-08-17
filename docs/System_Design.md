# System Design — Adaptive Real-Time Voice AI Sales Agent

## 1. Overview
The system separates realtime communication, application APIs, adaptive sales reasoning, business tools, live state, durable data, and semantic product knowledge.

## 2. Canonical architecture
```text
                         CUSTOMER
                            │
                            ▼
                  ┌────────────────────┐
                  │ React / Next.js UI │
                  │ Agora Web SDK      │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │   Agora RTC Layer  │
                  │ realtime audio     │
                  │ turn-taking        │
                  │ interruption       │
                  │ voice playback     │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │ Realtime Voice AI  │
                  │ speech in / out    │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │   Flask Backend    │
                  │ API Gateway        │
                  │ Session Manager    │
                  │ Agora service      │
                  │ LangGraph entry    │
                  └─────────┬──────────┘
                            │
                            ▼
        ╔══════════════════════════════════════╗
        ║               LangGraph              ║
        ║          ADAPTIVE SALES BRAIN        ║
        ║ conversation state                   ║
        ║ memory                               ║
        ║ intent/entity understanding          ║
        ║ objection handling                   ║
        ║ qualification                        ║
        ║ next-best-action                     ║
        ║ human escalation                     ║
        ╚══════════════════╤═══════════════════╝
                           │
                           ▼
                  ┌────────────────────┐
                  │    MCP Client      │
                  └─────────┬──────────┘
                            │
                            ▼
                  ┌────────────────────┐
                  │    MCP Gateway     │
                  │ auth / policy      │
                  │ validation / audit │
                  └─────────┬──────────┘
                            │
           ┌────────────────┼────────────────┐
           │                │                │
           ▼                ▼                ▼
   ┌──────────────┐  ┌──────────────┐  ┌────────────────┐
   │  Sales MCP   │  │ Knowledge MCP│  │ Integration MCP│
   └──────┬───────┘  └──────┬───────┘  └────────┬───────┘
          │                 │                   │
          ▼                 ▼                   ▼
   Pricing / Lead      pgvector / KB       CRM / Calendar /
   Qualification       Product/Security    Messaging/Handoff
```

## 3. Data plane
- Redis: active `SalesState`, current topic/intent, recent tool data.
- PostgreSQL: sessions, leads, messages, requirements, objections, meetings, followups, tool calls, outcomes, events.
- pgvector: product docs, FAQs, security, competitors, implementation, case studies.

## 4. Runtime turn loop
```text
Customer speaks
  ↓
Agora receives audio
  ↓
Realtime voice layer produces semantic turn
  ↓
Flask receives turn
  ↓
LangGraph loads SalesState
  ↓
understand_turn
  ↓
update_memory
  ↓
detect_intent
  ↓
detect_objection
  ↓
update_qualification
  ↓
Need external data/action?
  ├─ No ──────────────────────────────┐
  └─ Yes                              │
       ↓                              │
     MCP client → gateway → tool      │
       ↓                              │
     validated result ────────────────┘
  ↓
next_best_action
  ↓
generate_response
  ↓
voice generation
  ↓
Agora playback
  ↓
Customer
```

## 5. LangGraph
```text
START
  ↓
receive_turn
  ↓
understand_turn
  ↓
update_memory
  ↓
detect_intent
  ↓
detect_objection
  ↓
route_turn
  ├────────────┬────────────┬────────────┐
  ▼            ▼            ▼            ▼
Product      Pricing     Competitor    General
  │            │            │
  ▼            ▼            ▼
Knowledge     Sales       Knowledge
MCP           MCP         MCP
  └────────────┴────────────┘
               ↓
      update_qualification
               ↓
       next_best_action
        ┌──────┼─────────┐
        ▼      ▼         ▼
     Continue Booking Escalation
        │      │         │
        ▼      ▼         ▼
      Reply   MCP     Handoff MCP
        └──────┴─────────┘
               ↓
       generate_response
               ↓
              END
```
The graph is reinvoked for every customer turn with the same session/thread identity.

## 6. SalesState
```python
class SalesState(TypedDict):
    session_id: str
    customer: dict
    requirements: dict
    objections: list
    competitors: list
    messages: list
    current_topic: str
    current_intent: str
    buying_stage: str
    qualification_score: int
    tool_calls: list
    tool_results: list
    next_best_action: str
    conversation_summary: str
    outcome: str | None
```

## 7. Memory
Short-term memory: recent turns, current question, unfinished response, current topic, recent tool results.

Structured sales memory: customer/company/role/users/use cases/integrations/budget/timeline/competitors/objections/qualification/stage/outcome.

Latest confirmed values are authoritative.

## 8. MCP logical domains
### Sales MCP
`pricing.get_quote`, `pricing.compare_plans`, `lead.qualify`, `lead.next_action`, `followup.create`.

### Knowledge MCP
`knowledge.search`, `product.search`, `product.get_feature`, `product.get_integration`, `competitor.compare`, `security.get_information`.

### Integration MCP
`crm.get_lead`, `crm.create_lead`, `crm.update_lead`, `crm.add_note`, `crm.add_call_summary`, `calendar.get_slots`, `calendar.book_meeting`, `handoff.request_agent`, `handoff.transfer_context`.

For MVP, logical namespaces may live in one physical MCP server.

## 9. RAG
```text
Documents → clean → chunk → embed → pgvector → Knowledge MCP → LangGraph
```
RAG is for explanatory knowledge. Mutable transactional facts come from authoritative APIs/databases.

## 10. Human handoff
```text
Trigger → LangGraph → structured summary → Handoff MCP → CRM/dashboard → human agent → Agora
```

## 11. Failure principles
- pricing failure: never invent price;
- calendar failure: never claim booking;
- RAG miss: clarify/escalate;
- CRM failure: persist pending update and retry;
- voice disconnect: attempt recovery, then terminate gracefully.

## 12. Core principle
**Agora handles the conversation. Flask runs the application. LangGraph runs the sales brain. MCP connects the brain to the business. Redis/PostgreSQL/pgvector keep the system stateful and grounded.**
