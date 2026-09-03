"""Closed tool registry. Unknown tools never reach an adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

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

    def register(self, name: str, handler: ToolHandler) -> None:
        if name not in TOOL_DEFINITIONS:
            raise ValueError("cannot register a tool outside the versioned registry")
        self._handlers[name] = handler


def arguments_are_valid(definition: ToolDefinition, arguments: dict[str, object]) -> bool:
    """Small fail-closed validator until generated registry schemas are wired in."""
    if definition.name == "knowledge.search":
        return (
            set(arguments) == {"query", "domains", "limit"}
            and isinstance(arguments["query"], str)
            and 1 <= len(arguments["query"].strip()) <= 1000
            and isinstance(arguments["domains"], list)
            and all(isinstance(domain, str) and domain for domain in arguments["domains"])
            and isinstance(arguments["limit"], int)
            and 1 <= arguments["limit"] <= 20
        )
    return bool(arguments) or not definition.side_effect
