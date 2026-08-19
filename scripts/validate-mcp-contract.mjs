import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const registry = JSON.parse(readFileSync(resolve(root, "docs", "contracts", "mcp-tools.v1.json"), "utf8"));
const docs = readFileSync(resolve(root, "docs", "MCP_TOOLS.md"), "utf8");
const failures = [];
const required = ["pricing.get_quote","pricing.compare_plans","lead.qualify","lead.next_action","followup.create","knowledge.search","product.search","product.get_feature","product.get_integration","competitor.compare","security.get_information","crm.get_lead","crm.create_lead","crm.update_lead","crm.add_note","crm.add_call_summary","calendar.get_slots","calendar.book_meeting","handoff.request_agent","handoff.transfer_context"];

if (registry.contract_version !== 1) failures.push("contract_version must be 1");
if (registry.schema_dialect !== "https://json-schema.org/draft/2020-12/schema") failures.push("schema dialect mismatch");
if (!Array.isArray(registry.tools) || registry.tools.length !== 20) failures.push(`expected 20 tools, found ${registry.tools?.length}`);
const names = registry.tools.map((tool) => tool.name);
if (new Set(names).size !== names.length) failures.push("tool names must be unique");
for (const name of required) {
  if (!names.includes(name)) failures.push(`missing System Design tool: ${name}`);
  if (!docs.includes(`\`${name}\``)) failures.push(`missing narrative tool: ${name}`);
}

for (const tool of registry.tools) {
  const at = tool.name;
  if (tool.version !== 1) failures.push(`${at}: version must be 1`);
  if (!/^[a-z]+\.[a-z_]+$/u.test(tool.name)) failures.push(`${at}: invalid name`);
  if (!new Set(["SALES","KNOWLEDGE","INTEGRATION"]).has(tool.domain)) failures.push(`${at}: invalid domain`);
  if (typeof tool.scope !== "string" || !tool.scope.includes(":")) failures.push(`${at}: missing scope`);
  if (!new Set(["NONE","POLICY","CUSTOMER_CONFIRMATION","HUMAN_APPROVAL"]).has(tool.approval)) failures.push(`${at}: invalid approval`);
  if (!Number.isInteger(tool.timeout_ms) || tool.timeout_ms < 100 || tool.timeout_ms > 10000) failures.push(`${at}: unsafe timeout`);
  if (!Number.isInteger(tool.max_attempts) || tool.max_attempts < 1 || tool.max_attempts > 2) failures.push(`${at}: unsafe attempts`);
  for (const schemaName of ["input_schema","output_schema"]) {
    const schema = tool[schemaName];
    if (schema?.type !== "object" || schema.additionalProperties !== false || !Array.isArray(schema.required) || typeof schema.properties !== "object") failures.push(`${at}: invalid ${schemaName}`);
    for (const field of schema?.required ?? []) if (!Object.hasOwn(schema.properties ?? {}, field)) failures.push(`${at}: required ${field} absent from ${schemaName}`);
  }
  if (!Array.isArray(tool.failures) || tool.failures.length < 1) failures.push(`${at}: failure semantics missing`);
  if (tool.side_effect) {
    if (tool.idempotency !== "REQUIRED") failures.push(`${at}: side effect must require idempotency`);
    if (tool.approval === "NONE") failures.push(`${at}: side effect must require deterministic approval`);
    if (tool.max_attempts !== 1) failures.push(`${at}: side effect cannot blindly retry`);
  } else if (tool.approval !== "NONE") failures.push(`${at}: read/pure tool unexpectedly requires approval`);
}

for (const field of ["tool_call_id","tenant_id","actor_id","session_id","correlation_id","policy_version","approval_decision","request_hash","idempotency_key_hmac","provider_reference","status","error_code"]) if (!registry.required_audit_fields.includes(field)) failures.push(`missing audit field: ${field}`);
for (const code of ["INVALID_ARGUMENT","UNAUTHENTICATED","PERMISSION_DENIED","POLICY_DENIED","APPROVAL_REQUIRED","IDEMPOTENCY_CONFLICT","RATE_LIMITED","TIMEOUT","DEPENDENCY_UNAVAILABLE","INVALID_RESULT","NO_GROUNDED_RESULT","PENDING_CONFIRMATION","INTERNAL_ERROR"]) if (!registry.common_failures.includes(code)) failures.push(`missing common failure: ${code}`);
for (const requirement of ["FR-08","FR-09","FR-10","FR-11","FR-12","FR-13","FR-14"]) if (!docs.includes(`| ${requirement} `)) failures.push(`missing traceability: ${requirement}`);

if (failures.length) {
  console.error(`MCP contract validation failed with ${failures.length} issue(s):`);
  failures.forEach((failure) => console.error(`- ${failure}`));
  process.exitCode = 1;
} else console.log("MCP contract validation passed: 20 tools, schemas, authorization, approval, idempotency, retry, audit, failure, and FR-08-14 traceability.");
