"""Closed tool registry. Unknown tools never reach an adapter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from .contracts import ToolEnvelope, ToolInvocation, TrustedContext

ToolHandler = Callable[[TrustedContext, ToolInvocation], ToolEnvelope]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    version: int
    scope: str
    approval: str
    side_effect: bool
    timeout_ms: int
    max_request_bytes: int = 32_768


TOOL_DEFINITIONS = {
    "knowledge.search": ToolDefinition("knowledge.search", 1, "knowledge:read", "NONE", False, 2500),
    "product.search": ToolDefinition("product.search", 1, "knowledge:read", "NONE", False, 2500),
    "product.get_feature": ToolDefinition("product.get_feature", 1, "knowledge:read", "NONE", False, 2000),
    "product.get_integration": ToolDefinition("product.get_integration", 1, "knowledge:read", "NONE", False, 2000),
    "competitor.compare": ToolDefinition("competitor.compare", 1, "knowledge:read", "NONE", False, 3000),
    "security.get_information": ToolDefinition("security.get_information", 1, "knowledge:read", "NONE", False, 2500),
    "pricing.get_quote": ToolDefinition("pricing.get_quote", 1, "sales:read", "NONE", False, 3000),
    "pricing.compare_plans": ToolDefinition("pricing.compare_plans", 1, "sales:read", "NONE", False, 3000),
    "lead.qualify": ToolDefinition("lead.qualify", 1, "sales:read", "NONE", False, 1000),
    "lead.next_action": ToolDefinition("lead.next_action", 1, "sales:read", "NONE", False, 1000),
    "followup.create": ToolDefinition("followup.create", 1, "followup:write", "POLICY", True, 5000),
    "crm.get_lead": ToolDefinition("crm.get_lead", 1, "crm:read", "NONE", False, 3000),
    "crm.create_lead": ToolDefinition("crm.create_lead", 1, "crm:write", "POLICY", True, 5000),
    "crm.update_lead": ToolDefinition("crm.update_lead", 1, "crm:write", "POLICY", True, 5000),
    "crm.add_note": ToolDefinition("crm.add_note", 1, "crm:write", "POLICY", True, 5000),
    "crm.add_call_summary": ToolDefinition("crm.add_call_summary", 1, "crm:write", "POLICY", True, 5000),
    "calendar.get_slots": ToolDefinition("calendar.get_slots", 1, "calendar:read", "NONE", False, 4000),
    "calendar.book_meeting": ToolDefinition(
        "calendar.book_meeting", 1, "calendar:write", "CUSTOMER_CONFIRMATION", True, 6000
    ),
}


class ToolRegistry:
    def __init__(self, handlers: dict[str, ToolHandler] | None = None) -> None:
        self._handlers = handlers or {}

    def definition(self, name: str, version: int) -> ToolDefinition | None:
        item = TOOL_DEFINITIONS.get(name)
        return item if item and item.version == version else None

    def invoke(self, context: TrustedContext, invocation: ToolInvocation) -> ToolEnvelope:
        return self._handlers[invocation.tool](context, invocation)

    def has_handler(self, name: str) -> bool:
        return name in self._handlers

    def register(self, name: str, handler: ToolHandler) -> None:
        if name not in TOOL_DEFINITIONS:
            raise ValueError("cannot register a tool outside the versioned registry")
        self._handlers[name] = handler


def _is_limit(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 20


def _is_nonempty_str(value: object) -> bool:
    return isinstance(value, str) and len(value.strip()) >= 1


def _in_bounds(value: object, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def _is_uuid_str(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _is_object(value: object, *, min_properties: int = 0) -> bool:
    return isinstance(value, dict) and len(value) >= min_properties


def _is_nonempty_str_bounded(value: object, *, max_length: int) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= max_length


def arguments_are_valid(definition: ToolDefinition, arguments: dict[str, object]) -> bool:
    """Small fail-closed validator until generated registry schemas are wired in."""
    if definition.name == "knowledge.search":
        return (
            set(arguments) == {"query", "domains", "limit"}
            and isinstance(arguments["query"], str)
            and 1 <= len(arguments["query"].strip()) <= 1000
            and isinstance(arguments["domains"], list)
            and all(isinstance(domain, str) and domain for domain in arguments["domains"])
            and _is_limit(arguments["limit"])
        )
    if definition.name == "product.search":
        return (
            set(arguments) == {"query", "limit"}
            and _is_nonempty_str(arguments["query"])
            and _is_limit(arguments["limit"])
        )
    if definition.name == "product.get_feature":
        return set(arguments) == {"feature"} and _is_nonempty_str(arguments["feature"])
    if definition.name == "product.get_integration":
        return set(arguments) == {"integration"} and _is_nonempty_str(arguments["integration"])
    if definition.name == "competitor.compare":
        return (
            set(arguments) == {"competitor", "dimensions"}
            and _is_nonempty_str(arguments["competitor"])
            and isinstance(arguments["dimensions"], list)
            and len(arguments["dimensions"]) >= 1
            and all(_is_nonempty_str(dimension) for dimension in arguments["dimensions"])
        )
    if definition.name == "security.get_information":
        return (
            set(arguments) <= {"topic", "customer_clearance"}
            and "topic" in arguments
            and _is_nonempty_str(arguments["topic"])
            and ("customer_clearance" not in arguments or isinstance(arguments["customer_clearance"], str))
        )
    if definition.name == "pricing.get_quote":
        return (
            set(arguments) <= {"users", "billing_period", "plan"}
            and {"users", "billing_period"} <= set(arguments)
            and isinstance(arguments["users"], int)
            and not isinstance(arguments["users"], bool)
            and arguments["users"] >= 1
            and arguments["billing_period"] in {"MONTHLY", "ANNUAL"}
            and ("plan" not in arguments or arguments["plan"] is None or isinstance(arguments["plan"], str))
        )
    if definition.name == "pricing.compare_plans":
        return (
            set(arguments) == {"plan_ids", "users"}
            and isinstance(arguments["plan_ids"], list)
            and len(arguments["plan_ids"]) >= 2
            and all(isinstance(item, str) for item in arguments["plan_ids"])
            and isinstance(arguments["users"], int)
            and not isinstance(arguments["users"], bool)
            and arguments["users"] >= 1
        )
    if definition.name == "lead.qualify":
        bounds = {
            "need": 25,
            "product_fit": 20,
            "deployment_fit": 15,
            "timeline": 15,
            "authority": 10,
            "budget": 5,
            "purchase_intent": 10,
        }
        return set(arguments) == set(bounds) and all(
            _in_bounds(arguments[name], maximum) for name, maximum in bounds.items()
        )
    if definition.name == "lead.next_action":
        return (
            set(arguments) == {"intent", "stage", "explicit_request"}
            and isinstance(arguments["intent"], str)
            and arguments["intent"] != ""
            and isinstance(arguments["stage"], str)
            and arguments["stage"] != ""
            and (arguments["explicit_request"] is None or isinstance(arguments["explicit_request"], str))
        )
    if definition.name == "crm.get_lead":
        return set(arguments) == {"lookup"} and _is_object(arguments["lookup"], min_properties=1)
    if definition.name == "crm.create_lead":
        lead = arguments.get("lead")
        return (
            set(arguments) == {"lead"}
            and isinstance(lead, dict)
            and isinstance(lead.get("company"), str)
            and lead["company"] != ""
        )
    if definition.name == "crm.update_lead":
        changes = arguments.get("changes")
        return (
            set(arguments) == {"lead_id", "expected_version", "changes"}
            and _is_uuid_str(arguments["lead_id"])
            and isinstance(arguments["expected_version"], int)
            and not isinstance(arguments["expected_version"], bool)
            and arguments["expected_version"] >= 1
            and _is_object(changes, min_properties=1)
        )
    if definition.name == "crm.add_note":
        return (
            set(arguments) == {"lead_id", "note"}
            and _is_uuid_str(arguments["lead_id"])
            and _is_nonempty_str_bounded(arguments["note"], max_length=8000)
        )
    if definition.name == "crm.add_call_summary":
        return (
            set(arguments) == {"lead_id", "session_id", "summary", "outcome"}
            and _is_uuid_str(arguments["lead_id"])
            and _is_uuid_str(arguments["session_id"])
            and _is_nonempty_str_bounded(arguments["summary"], max_length=8000)
            and isinstance(arguments["outcome"], str)
            and arguments["outcome"] != ""
        )
    if definition.name == "calendar.get_slots":
        return (
            set(arguments) == {"from", "to", "timezone", "duration_minutes"}
            and isinstance(arguments["from"], str)
            and isinstance(arguments["to"], str)
            and isinstance(arguments["timezone"], str)
            and arguments["timezone"] != ""
            and isinstance(arguments["duration_minutes"], int)
            and not isinstance(arguments["duration_minutes"], bool)
            and 15 <= arguments["duration_minutes"] <= 240
        )
    if definition.name == "calendar.book_meeting":
        attendees = arguments.get("attendees")
        return (
            set(arguments) == {"slot_id", "snapshot_reference", "attendees", "title"}
            and _is_nonempty_str_bounded(arguments["slot_id"], max_length=256)
            and _is_nonempty_str_bounded(arguments["snapshot_reference"], max_length=256)
            and isinstance(attendees, list)
            and len(attendees) >= 1
            and all(isinstance(item, str) and item for item in attendees)
            and isinstance(arguments["title"], str)
            and arguments["title"] != ""
        )
    return bool(arguments) or not definition.side_effect
