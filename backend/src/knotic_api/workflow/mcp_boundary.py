"""The only LangGraph-facing conversion from MCP results to grounded facts."""

from __future__ import annotations

from datetime import UTC, datetime
from .contracts import GroundedFact, GroundingDomain
from .mcp_client import McpToolResult


def grounded_facts_from_result(result: McpToolResult, *, domain: GroundingDomain) -> tuple[GroundedFact, ...]:
    """Reject malformed/unverified output before it can enter graph state."""
    if result.status != "SUCCEEDED" or result.data is None:
        return ()
    matches = result.data.get("matches")
    if not isinstance(matches, list):
        return ()
    facts: list[GroundedFact] = []
    for match in matches[:4]:
        if not isinstance(match, dict) or not isinstance(match.get("text"), str) or not isinstance(match.get("citation"), dict):
            return ()
        citation = match["citation"]
        if not all(isinstance(citation.get(key), str) for key in ("source_uri", "title", "chunk_id")):
            return ()
        facts.append(
            GroundedFact(
                citation_id=f"cite_{citation['chunk_id'][:48]}",
                domain=domain,
                statement=match["text"][:500],
                source_title=citation["title"][:160],
                source_reference=citation["source_uri"],
                retrieved_at=datetime.now(UTC),
            )
        )
    return tuple(facts)
