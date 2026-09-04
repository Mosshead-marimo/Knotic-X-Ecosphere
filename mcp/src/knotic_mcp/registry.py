"""Closed tool registry. Unknown tools never reach an adapter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
            and (
                "customer_clearance" not in arguments
                or isinstance(arguments["customer_clearance"], str)
            )
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
            isinstance(arguments[name], int) and not isinstance(arguments[name], bool) and 0 <= arguments[name] <= maximum
            for name, maximum in bounds.items()
        )
    if definition.name == "lead.next_action":
        return (
            set(arguments) == {"intent", "stage", "explicit_request"}
            and isinstance(arguments["intent"], str)
            and arguments["intent"]
            and isinstance(arguments["stage"], str)
            and arguments["stage"]
            and (arguments["explicit_request"] is None or isinstance(arguments["explicit_request"], str))
        )
    return bool(arguments) or not definition.side_effect
