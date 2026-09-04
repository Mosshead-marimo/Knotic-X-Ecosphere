"""The only LangGraph-facing conversion from MCP results to grounded facts.

Every string here originates from a retrieved document or another tool's output and is
therefore untrusted data (see ``untrusted_content.py``): it is never treated as an instruction,
never trusted to name a tool to call, and never allowed into graph state unless it passes the
fail-closed safety check. A malformed, oversized, or adversarial result yields no facts rather
than a best-effort partial parse — an explicit miss is always safer than smuggled content.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError

from .contracts import GroundedFact, GroundingDomain
from .mcp_client import McpToolResult
from .untrusted_content import is_safe_untrusted_text

_MAX_FACTS = 4
_CITATION_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

# A GroundedFact statement/title is bounded before ``is_safe_untrusted_text`` inspects it so an
# adversarial document cannot smuggle an injection payload past the length check by padding.
_STATEMENT_MAX = 500
_TITLE_MAX = 160
# Retrieved chunk text is ~900 characters at ingestion time (see mcp/knowledge.py chunking), so
# anything far beyond that from a tool result is itself suspicious oversized/DoS input and is
# rejected outright rather than silently truncated down to a plausible-looking length.
_RAW_TEXT_MAX = 2000


def _valid_citation(citation: object) -> dict[str, str] | None:
    if not isinstance(citation, dict):
        return None
    if not all(isinstance(citation.get(key), str) and citation.get(key) for key in ("source_uri", "title", "chunk_id")):
        return None
    chunk_id = citation["chunk_id"][:48]
    if not 8 <= len(chunk_id) <= 48 or any(character not in _CITATION_ID_CHARS for character in chunk_id):
        return None
    return {"source_uri": citation["source_uri"], "title": citation["title"], "chunk_id": chunk_id}


def _knowledge_search_items(data: object) -> list[tuple[str, dict[str, str]]] | None:
    if not isinstance(data, dict):
        return None
    matches = data.get("matches")
    if not isinstance(matches, list):
        return None
    items: list[tuple[str, dict[str, str]]] = []
    for match in matches:
        if not isinstance(match, dict) or not isinstance(match.get("text"), str):
            return None
        citation = _valid_citation(match.get("citation"))
        if citation is None:
            return None
        items.append((match["text"], citation))
    return items


def _product_search_items(data: object) -> list[tuple[str, dict[str, str]]] | None:
    if not isinstance(data, dict):
        return None
    products, citations = data.get("products"), data.get("citations")
    if not isinstance(products, list) or not isinstance(citations, list) or len(products) != len(citations):
        return None
    items: list[tuple[str, dict[str, str]]] = []
    for product, raw_citation in zip(products, citations, strict=True):
        if not isinstance(product, dict) or not isinstance(product.get("summary"), str):
            return None
        citation = _valid_citation(raw_citation)
        if citation is None:
            return None
        items.append((product["summary"], citation))
    return items


def _single_field_items(text_key: str) -> Callable[[object], list[tuple[str, dict[str, str]]] | None]:
    def extract(data: object) -> list[tuple[str, dict[str, str]]] | None:
        if not isinstance(data, dict):
            return None
        text, citations = data.get(text_key), data.get("citations")
        if not isinstance(text, str) or not isinstance(citations, list) or not citations:
            return None
        citation = _valid_citation(citations[0])
        return None if citation is None else [(text, citation)]

    return extract


def _integration_items(data: object) -> list[tuple[str, dict[str, str]]] | None:
    if not isinstance(data, dict):
        return None
    integration, support_status, citations = data.get("integration"), data.get("support_status"), data.get("citations")
    if (
        not isinstance(integration, str)
        or not isinstance(support_status, str)
        or not isinstance(citations, list)
        or not citations
    ):
        return None
    citation = _valid_citation(citations[0])
    if citation is None:
        return None
    return [(f"{integration} integration support status: {support_status}.", citation)]


def _competitor_compare_items(data: object) -> list[tuple[str, dict[str, str]]] | None:
    if not isinstance(data, dict):
        return None
    comparisons = data.get("comparisons")
    if not isinstance(comparisons, list):
        return None
    items: list[tuple[str, dict[str, str]]] = []
    for item in comparisons:
        if not isinstance(item, dict) or not isinstance(item.get("statement"), str):
            return None
        citation = _valid_citation(item.get("citation"))
        if citation is None:
            return None
        items.append((item["statement"], citation))
    return items


_EXTRACTORS: dict[str, Callable[[object], list[tuple[str, dict[str, str]]] | None]] = {
    "knowledge.search": _knowledge_search_items,
    "product.search": _product_search_items,
    "product.get_feature": _single_field_items("description"),
    "product.get_integration": _integration_items,
    "competitor.compare": _competitor_compare_items,
    "security.get_information": _single_field_items("answer"),
}


def grounded_facts_from_result(result: McpToolResult, *, domain: GroundingDomain) -> tuple[GroundedFact, ...]:
    """Reject malformed, unverified, or adversarial tool output before it enters graph state.

    Returns an empty tuple — the same shape as a genuine no-evidence miss — for any result that
    is not a validated success, whose shape does not match a known tool contract, or whose text
    fails the untrusted-content safety check. Callers must not distinguish "no evidence" from
    "evidence was rejected as unsafe": treating them identically removes any signal an attacker
    could use to probe the defense.
    """
    if result.status != "SUCCEEDED" or result.data is None:
        return ()
    extractor = _EXTRACTORS.get(result.tool)
    if extractor is None:
        return ()
    items = extractor(result.data)
    if items is None:
        return ()
    facts: list[GroundedFact] = []
    for text, citation in items:
        if len(facts) >= _MAX_FACTS:
            break
        # Safety is checked on the untruncated text first: truncating before checking would let
        # an oversized payload slip through simply by having its dangerous prefix fit within the
        # bound, and would defeat the oversized-payload check entirely (truncated length always
        # satisfies its own bound).
        if not is_safe_untrusted_text(text, max_length=_RAW_TEXT_MAX) or not is_safe_untrusted_text(
            citation["title"], max_length=_TITLE_MAX
        ):
            continue
        statement, title = text[:_STATEMENT_MAX], citation["title"][:_TITLE_MAX]
        try:
            facts.append(
                GroundedFact(
                    citation_id=f"cite_{citation['chunk_id']}",
                    domain=domain,
                    statement=statement,
                    source_title=title,
                    source_reference=citation["source_uri"],
                    retrieved_at=datetime.now(UTC),
                )
            )
        except ValidationError:
            continue
    return tuple(facts)
