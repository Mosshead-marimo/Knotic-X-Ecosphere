import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "..");
const specificationPath = resolve(repositoryRoot, "docs", "contracts", "openapi.v1.json");
const documentationPath = resolve(repositoryRoot, "docs", "API_CONTRACTS.md");
const specification = JSON.parse(readFileSync(specificationPath, "utf8"));
const documentation = readFileSync(documentationPath, "utf8");
const failures = [];

function record(condition, message) {
  if (!condition) failures.push(message);
}

function decodePointerPart(value) {
  return value.replaceAll("~1", "/").replaceAll("~0", "~");
}

function resolveReference(reference) {
  record(typeof reference === "string" && reference.startsWith("#/"), `Only local references are permitted: ${reference}`);
  if (typeof reference !== "string" || !reference.startsWith("#/")) return undefined;

  let current = specification;
  for (const part of reference.slice(2).split("/").map(decodePointerPart)) {
    if (current === null || typeof current !== "object" || !(part in current)) {
      failures.push(`Unresolved reference: ${reference}`);
      return undefined;
    }
    current = current[part];
  }
  return current;
}

function resolved(value) {
  let current = value;
  const visited = new Set();
  while (current && typeof current === "object" && typeof current.$ref === "string") {
    if (visited.has(current.$ref)) {
      failures.push(`Reference cycle while resolving ${current.$ref}`);
      return undefined;
    }
    visited.add(current.$ref);
    current = resolveReference(current.$ref);
  }
  return current;
}

function valueType(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (Number.isInteger(value)) return "integer";
  if (typeof value === "number") return "number";
  return typeof value;
}

function typeMatches(value, expected) {
  if (expected === "number") return typeof value === "number" && Number.isFinite(value);
  if (expected === "integer") return Number.isInteger(value);
  if (expected === "object") return value !== null && typeof value === "object" && !Array.isArray(value);
  if (expected === "array") return Array.isArray(value);
  if (expected === "null") return value === null;
  return typeof value === expected;
}

function validateSchema(value, rawSchema, location, errors = []) {
  if (rawSchema === true || rawSchema === undefined) return errors;
  if (rawSchema === false) {
    errors.push(`${location}: value is forbidden by schema`);
    return errors;
  }

  const schema = rawSchema?.$ref ? resolveReference(rawSchema.$ref) : rawSchema;
  if (!schema || typeof schema !== "object") {
    errors.push(`${location}: invalid schema`);
    return errors;
  }

  if (Array.isArray(schema.allOf)) {
    for (const member of schema.allOf) validateSchema(value, member, location, errors);
  }

  if (Array.isArray(schema.oneOf)) {
    const attempts = schema.oneOf.map((member) => validateSchema(value, member, location, []));
    const matches = attempts.filter((attempt) => attempt.length === 0).length;
    if (matches !== 1) errors.push(`${location}: expected exactly one oneOf match, received ${matches}`);
    if (matches !== 1 && attempts.length > 0) errors.push(...attempts[0].slice(0, 2));
  }

  if (Array.isArray(schema.anyOf)) {
    const attempts = schema.anyOf.map((member) => validateSchema(value, member, location, []));
    if (!attempts.some((attempt) => attempt.length === 0)) {
      errors.push(`${location}: did not match any anyOf member`);
      errors.push(...attempts[0].slice(0, 2));
    }
  }

  if (schema.const !== undefined && JSON.stringify(value) !== JSON.stringify(schema.const)) {
    errors.push(`${location}: expected constant ${JSON.stringify(schema.const)}`);
  }
  if (Array.isArray(schema.enum) && !schema.enum.some((item) => JSON.stringify(item) === JSON.stringify(value))) {
    errors.push(`${location}: ${JSON.stringify(value)} is not an allowed enum value`);
  }

  const expectedTypes = Array.isArray(schema.type) ? schema.type : schema.type ? [schema.type] : [];
  if (expectedTypes.length > 0 && !expectedTypes.some((expected) => typeMatches(value, expected))) {
    errors.push(`${location}: expected ${expectedTypes.join("|")}, received ${valueType(value)}`);
    return errors;
  }

  if (typeof value === "string") {
    if (schema.minLength !== undefined && value.length < schema.minLength) errors.push(`${location}: shorter than minLength ${schema.minLength}`);
    if (schema.maxLength !== undefined && value.length > schema.maxLength) errors.push(`${location}: longer than maxLength ${schema.maxLength}`);
    if (schema.pattern !== undefined && !new RegExp(schema.pattern, "u").test(value)) errors.push(`${location}: does not match pattern ${schema.pattern}`);
    if (schema.format === "uuid" && !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu.test(value)) errors.push(`${location}: invalid UUID`);
    if (schema.format === "date-time" && (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/u.test(value) || Number.isNaN(Date.parse(value)))) errors.push(`${location}: invalid UTC date-time`);
    if (schema.format === "uri") {
      try { new URL(value); } catch { errors.push(`${location}: invalid URI`); }
    }
  }

  if (typeof value === "number" && Number.isFinite(value)) {
    if (schema.minimum !== undefined && value < schema.minimum) errors.push(`${location}: below minimum ${schema.minimum}`);
    if (schema.maximum !== undefined && value > schema.maximum) errors.push(`${location}: above maximum ${schema.maximum}`);
  }

  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) errors.push(`${location}: fewer than minItems ${schema.minItems}`);
    if (schema.maxItems !== undefined && value.length > schema.maxItems) errors.push(`${location}: more than maxItems ${schema.maxItems}`);
    if (schema.uniqueItems && new Set(value.map((item) => JSON.stringify(item))).size !== value.length) errors.push(`${location}: items are not unique`);
    if (schema.items) value.forEach((item, index) => validateSchema(item, schema.items, `${location}[${index}]`, errors));
  }

  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const properties = schema.properties ?? {};
    for (const name of schema.required ?? []) {
      if (!Object.hasOwn(value, name)) errors.push(`${location}: missing required property ${name}`);
    }
    for (const [name, item] of Object.entries(value)) {
      if (Object.hasOwn(properties, name)) validateSchema(item, properties[name], `${location}.${name}`, errors);
      else if (schema.additionalProperties === false) errors.push(`${location}: unknown property ${name}`);
      else if (schema.additionalProperties && typeof schema.additionalProperties === "object") validateSchema(item, schema.additionalProperties, `${location}.${name}`, errors);
    }
    if (schema.maxProperties !== undefined && Object.keys(value).length > schema.maxProperties) errors.push(`${location}: more than maxProperties ${schema.maxProperties}`);
  }

  return errors;
}

function collectReferences(value, location = "#") {
  if (value === null || typeof value !== "object") return;
  if (typeof value.$ref === "string") resolveReference(value.$ref);
  for (const [name, child] of Object.entries(value)) collectReferences(child, `${location}/${name}`);
}

function parameterNames(pathItem, operation) {
  return [...(pathItem.parameters ?? []), ...(operation.parameters ?? [])]
    .map(resolved)
    .filter(Boolean)
    .map((parameter) => parameter.name);
}

record(specification.openapi === "3.1.0", "OpenAPI version must be 3.1.0");
record(specification.jsonSchemaDialect === "https://json-schema.org/draft/2020-12/schema", "JSON Schema dialect must be draft 2020-12");
record(specification.info?.version === "1.0.0", "Contract version must be 1.0.0");
record(specification.security?.[0]?.cookieAuth !== undefined, "Root browser cookie authentication is required");
collectReferences(specification);

const methods = new Set(["get", "post", "put", "patch", "delete", "options", "head"]);
const unauthenticatedPaths = new Set(["/api/v1/health/live", "/api/v1/auth/login", "/api/v1/auth/callback"]);
const operationIds = new Set();
let operationCount = 0;

for (const [path, pathItem] of Object.entries(specification.paths ?? {})) {
  record(/^\/(?:api|internal)\/v1\//u.test(path), `Unversioned path: ${path}`);
  record(documentation.includes(path), `Endpoint missing from narrative registry: ${path}`);

  for (const [method, operation] of Object.entries(pathItem)) {
    if (!methods.has(method)) continue;
    operationCount += 1;
    record(typeof operation.operationId === "string" && operation.operationId.length > 0, `${method.toUpperCase()} ${path} has no operationId`);
    record(!operationIds.has(operation.operationId), `Duplicate operationId: ${operation.operationId}`);
    operationIds.add(operation.operationId);

    const security = operation.security ?? specification.security;
    const isLiveness = path === "/api/v1/health/live";
    const isUnauthenticated = unauthenticatedPaths.has(path);
    const isInternal = path.startsWith("/internal/v1/");
    if (isUnauthenticated) record(Array.isArray(security) && security.length === 0, `${method.toUpperCase()} ${path} must be explicitly unauthenticated`);
    else if (isInternal) record(security?.some((requirement) => Object.hasOwn(requirement, "workloadAuth")), `${method.toUpperCase()} ${path} must require workloadAuth`);
    else record(security?.some((requirement) => Object.hasOwn(requirement, "cookieAuth")), `${method.toUpperCase()} ${path} must require cookieAuth`);

    const names = parameterNames(pathItem, operation);
    if (method === "post") {
      record(names.includes("Idempotency-Key"), `POST ${path} must require Idempotency-Key`);
      if (!isInternal) record(names.includes("X-CSRF-Token"), `POST ${path} must require X-CSRF-Token`);
      record(operation.requestBody?.required === true, `POST ${path} must require a request body`);
      record(operation.requestBody?.content?.["application/json"]?.schema !== undefined, `POST ${path} must define an application/json schema`);
    }

    if (operation.operationId === "listSessionEvents") {
      record(names.includes("cursor") && names.includes("limit"), "Event listing must define cursor and limit");
    }

    const responseCodes = Object.keys(operation.responses ?? {});
    record(responseCodes.some((code) => /^[23]\d\d$/u.test(code)), `${method.toUpperCase()} ${path} has no explicit success/redirect response`);
    record(responseCodes.includes("500"), `${method.toUpperCase()} ${path} has no safe 500 response`);
    if (!isUnauthenticated) {
      record(responseCodes.includes("401"), `${method.toUpperCase()} ${path} has no 401 response`);
      record(responseCodes.includes("403"), `${method.toUpperCase()} ${path} has no 403 response`);
      record(responseCodes.includes("429"), `${method.toUpperCase()} ${path} has no 429 response`);
    }
    if (Object.hasOwn(operation.responses ?? {}, "202")) {
      const accepted = resolved(operation.responses["202"]);
      record(accepted?.headers?.Location !== undefined || operation.operationId === "recordVoiceInterruption", `202 for ${operation.operationId} must identify the accepted resource`);
    }
    for (const [code, rawResponse] of Object.entries(operation.responses ?? {})) {
      const response = resolved(rawResponse);
      record(response?.headers?.["X-Request-ID"] !== undefined, `${method.toUpperCase()} ${path} response ${code} is missing X-Request-ID`);
      if (/^[45]\d\d$/u.test(code)) {
        const errorSchema = response?.content?.["application/json"]?.schema;
        record(errorSchema?.$ref === "#/components/schemas/ErrorEnvelope", `${method.toUpperCase()} ${path} response ${code} must use ErrorEnvelope`);
      }
    }
  }
}

record(operationCount === 14, `Expected 14 operations, found ${operationCount}`);

const expectedErrorCodes = ["400", "401", "403", "404", "409", "415", "422", "429", "500", "503", "504"];
for (const code of expectedErrorCodes) record(documentation.includes(`| \`${code}\``), `Narrative error mapping is missing HTTP ${code}`);

const examples = specification["x-contract-examples"];
record(Array.isArray(examples) && examples.length >= 10, "At least 10 executable contract examples are required");
for (const example of examples ?? []) {
  const schema = resolveReference(example.schema);
  const errors = validateSchema(example.value, schema, `example:${example.name}`);
  failures.push(...errors);
}

const negativeExamples = [
  ["#/components/schemas/SessionCreateRequest", {locale: "en-IN"}, "missing required request field"],
  ["#/components/schemas/TurnResult", {turn_id: "64a95db5-d568-4f2f-b624-76a3f675a6bc", status: "COMPLETED", response_disposition: "SPOKEN", response_id: null, response_text: null, session_version: 1, state: {current_intent: null, buying_stage: "NURTURE", qualification_score: 0, next_best_action: "ASK_DISCOVERY", conversation_summary: "", latest_request: null, outcome: null}, citations: []}, "spoken response with null content"],
  ["#/components/schemas/Operation", {operation_id: "2ebc3bc3-8b7b-4f77-8d23-856f548b2e5c", kind: "PROCESS_TURN", status: "SUCCEEDED", session_id: "f47ac10b-58cc-4372-a567-0e02b2c3d479", created_at: "2026-08-18T08:00:01Z", updated_at: "2026-08-18T08:00:01Z", result: null, error: null}, "successful operation without a result"],
  ["#/components/schemas/EventPage", {items: [], next_cursor: null, has_more: true}, "event page missing required continuation cursor"],
  ["#/components/schemas/RequirementUpdatedPayload", {field: "users", old_value: "fifty", new_value: 250, confirmed: true}, "requirement value inconsistent with field type"]
];
for (const [reference, value, name] of negativeExamples) {
  record(validateSchema(value, resolveReference(reference), `negative:${name}`, []).length > 0, `Validator self-check accepted invalid case: ${name}`);
}

const eventTypeSchema = specification.components?.schemas?.DomainEvent?.properties?.event_type;
const eventTypes = new Set(eventTypeSchema?.enum ?? []);
const payloadMap = specification["x-event-payload-map"] ?? {};
record(eventTypes.size === 9, `Expected 9 version 1 event types, found ${eventTypes.size}`);
record(Object.keys(payloadMap).length === eventTypes.size, "Every event type must have exactly one payload mapping");
for (const eventType of eventTypes) {
  record(typeof payloadMap[eventType] === "string", `Missing payload schema mapping for ${eventType}`);
  if (payloadMap[eventType]) resolveReference(payloadMap[eventType]);
}
for (const example of examples ?? []) {
  if (example.schema !== "#/components/schemas/DomainEvent") continue;
  const payloadSchema = resolveReference(payloadMap[example.value.event_type]);
  failures.push(...validateSchema(example.value.payload, payloadSchema, `event-payload:${example.name}`));
}

const secretPatterns = [/app[_-]?certificate/iu, /database_url/iu, /redis_url/iu, /openai_api_key/iu, /mcp_auth_token/iu];
const serializedExamples = JSON.stringify(examples ?? []);
for (const pattern of secretPatterns) record(!pattern.test(serializedExamples), `Contract examples contain forbidden server-only material: ${pattern}`);

if (failures.length > 0) {
  console.error(`API contract validation failed with ${failures.length} issue(s):`);
  for (const failure of failures) console.error(`- ${failure}`);
  process.exitCode = 1;
} else {
  console.log(`API contract validation passed: ${operationCount} operations, ${Object.keys(specification.components.schemas).length} schemas, ${eventTypes.size} event types, ${examples.length} examples.`);
}
