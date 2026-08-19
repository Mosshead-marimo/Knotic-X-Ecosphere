import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const model = readFileSync(resolve(root, "docs", "DATA_MODEL.md"), "utf8");
const failures = [];
const requireText = (value, group) => { if (!model.includes(value)) failures.push(`${group}: ${value}`); };

for (const section of ["PostgreSQL entity catalog", "Redis active-state model", "Knowledge and pgvector", "Invariants and transaction boundaries", "Encryption and data minimization", "Retention, deletion, and recovery", "Migration strategy", "Requirement traceability"]) requireText(`## ${section}`, "missing section");

for (const entity of ["tenants", "actors", "leads", "sales_sessions", "calls", "messages", "requirements_current", "requirement_changes", "objections", "session_competitors", "qualification_snapshots", "tool_calls", "tool_results", "operations", "idempotency_records", "meetings", "followups", "handoffs", "session_outcomes", "pending_provider_updates", "domain_events", "outbox_messages", "inbox_receipts", "audit_events", "knowledge_documents", "knowledge_chunks", "knowledge_embeddings", "knowledge_index_versions"]) requireText(`\`${entity}\``, "missing entity");

for (const state of ["session_id", "customer", "company", "role", "users", "use cases", "integrations", "budget", "timeline", "objections", "competitors", "current topic/intent", "buying stage", "qualification", "next action", "recent messages", "tool calls/results", "conversation summary", "outcome"]) requireText(state, "missing SalesState authority");

for (const event of ["session.created", "turn.accepted", "turn.completed", "response.interrupted", "requirement.updated", "qualification.updated", "operation.updated", "session.ended"]) requireText(event, "missing event lifecycle");

for (const requirement of ["FR-04", "FR-05", "FR-11", "FR-12", "FR-13", "FR-14"]) requireText(`| ${requirement} `, "missing requirement trace");

for (const control of ["UUIDv7", "composite foreign keys", "row-level security", "optimistic version", "for update skip locked", "application envelope encryption", "keyed HMAC", "expand/migrate/contract", "Raw audio is not persisted by default"]) requireText(control, "missing production control");

const redisRows = [...model.matchAll(/^\| `knotic:.*\|.*\|.*\|$/gmu)];
if (redisRows.length < 7) failures.push(`expected at least 7 Redis key contracts, found ${redisRows.length}`);

if (failures.length) {
  console.error(`Data model validation failed with ${failures.length} issue(s):`);
  failures.forEach((failure) => console.error(`- ${failure}`));
  process.exitCode = 1;
} else {
  console.log("Data model validation passed: 28 entities, 7 Redis key contracts, complete state/event ownership, and FR-04/05/11-14 traceability.");
}
