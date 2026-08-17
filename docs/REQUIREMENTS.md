# Product Requirements

## Problem
Build a realtime voice AI sales agent that can conduct complete customer qualification and sales conversations without depending on a fixed script.

The customer may interrupt, ask follow-ups, change requirements, return to earlier topics, raise objections, request information, ask for a demo, or request a human.

## Functional requirements

### FR-01 Realtime voice
Customer can join via Agora, speak naturally, hear the AI, and continue multi-turn conversation.

### FR-02 Interruption / barge-in
When the customer speaks while AI audio is playing:
- stop/truncate AI playback;
- prioritize the customer turn;
- record the interrupted response;
- resume old topic only if still relevant.

### FR-03 Dynamic conversation
Support nonlinear movement between discovery, pricing, product questions, competitor comparison, objections, requirement changes, demo booking, follow-up, and escalation.

### FR-04 Structured memory
Track customer, company, role, users, use cases, integrations, budget, timeline, competitors, objections, qualification, current topic, buying stage, next action, and outcome.

### FR-05 Requirement revision
Example:
```text
users = 50
Customer: “Actually support is joining too. Make that 250.”
users = 250
```
Emit a `REQUIREMENT_UPDATED` event containing old/new values.

### FR-06 Intent support
Minimum intents:
```text
DISCOVERY
PRICING
PRODUCT_QUESTION
COMPETITOR_COMPARISON
OBJECTION
CHANGE_REQUIREMENT
DEMO_REQUEST
BOOKING
FOLLOWUP
HUMAN_HANDOFF
GENERAL_QUESTION
CLOSING
```

### FR-07 Objections
Minimum categories:
```text
PRICE
COMPETITOR
SECURITY
TRUST
FEATURE_GAP
IMPLEMENTATION
TIMELINE
BUDGET
AUTHORITY
```

### FR-08 Tool-grounded business facts
Use trusted tools for product information, features, integrations, security, competitors, pricing, availability, CRM state, and calendar state.

### FR-09 Continuous qualification
Baseline score:
```text
Business Need          25
Company/Product Fit    20
User/Deployment Fit    15
Timeline               15
Authority              10
Budget                   5
Purchase Intent         10
--------------------------
Total                  100
```
Stages:
```text
0–39 NURTURE
40–59 FOLLOWUP
60–74 SALES_QUALIFIED
75–100 HIGH_INTENT
```
Explicit customer requests can override score thresholds.

### FR-10 Next best action
Possible actions:
```text
ASK_DISCOVERY
ANSWER_QUESTION
RETRIEVE_PRODUCT_INFO
GET_PRICING
HANDLE_OBJECTION
COMPARE_COMPETITOR
UPDATE_REQUIREMENT
OFFER_DEMO
BOOK_DEMO
CREATE_FOLLOWUP
ESCALATE_HUMAN
END_CALL
```

### FR-11 CRM
Find/create/update lead, persist requirements/objections/qualification/outcome, and store call summary/activity.

### FR-12 Calendar
Get available slots and book only after explicit customer selection and provider confirmation.

### FR-13 Human escalation
Triggers include explicit request, enterprise opportunity, complex negotiation, security/legal concern, low confidence, customer frustration, unsupported question, or unauthorized discount request.

Handoff context includes company, users, use cases, integrations, competitors, objections, qualification score, latest request, and concise summary.

### FR-14 Outcomes
```text
ENTERPRISE_DEMO_BOOKED
LEAD_QUALIFIED
FOLLOWUP_CREATED
HUMAN_ESCALATED
NURTURE
CLOSED_NO_ACTION
```

## Non-functional requirements
- low conversational latency;
- safe failure;
- auditability;
- modular tool integrations;
- server-side secrets;
- no fabricated transactional results.
