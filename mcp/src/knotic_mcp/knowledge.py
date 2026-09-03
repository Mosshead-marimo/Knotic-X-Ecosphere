"""Deterministic approved-source ingestion and isolated hybrid retrieval."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

_SPACE = re.compile(r"\s+")
_SCRIPT = re.compile(r"<script[^>]*>.*?</script>", re.I | re.S)


@dataclass(frozen=True, slots=True)
class ApprovedSource:
    source_uri: str
    tenant_id: UUID
    domains: frozenset[str]
    classification: str = "PUBLIC"
    allowed_content_types: frozenset[str] = frozenset({"text/plain", "text/markdown", "text/html"})
    active: bool = True


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    id: str
    tenant_id: UUID
    source_uri: str
    document_version: int
    domain: str
    title: str
    text: str
    content_hash: str
    embedding: tuple[float, ...]
    effective_at: datetime
    expires_at: datetime | None = None
    tombstoned_at: datetime | None = None


class EmbeddingPort(Protocol):
    def embed(self, text: str) -> tuple[float, ...]: ...


class HashEmbedding:
    """Stable local embedding for tests; production injects the approved embedding adapter."""

    dimensions = 64

    def embed(self, text: str) -> tuple[float, ...]:
        values = [0.0] * self.dimensions
        for word in re.findall(r"[a-z0-9]+", text.casefold()):
            values[int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dimensions] += 1.0
        norm = math.sqrt(sum(item * item for item in values)) or 1.0
        return tuple(item / norm for item in values)


@dataclass(slots=True)
class KnowledgeStore:
    sources: dict[tuple[UUID, str], ApprovedSource] = field(default_factory=dict)
    chunks: list[KnowledgeChunk] = field(default_factory=list)
    reindex_jobs: list[dict[str, str]] = field(default_factory=list)


class KnowledgeIngestionService:
    def __init__(self, store: KnowledgeStore, embeddings: EmbeddingPort | None = None, *, chunk_size: int = 900) -> None:
        self._store = store
        self._embeddings = embeddings or HashEmbedding()
        self._chunk_size = chunk_size

    def register_source(self, source: ApprovedSource) -> None:
        self._store.sources[(source.tenant_id, source.source_uri)] = source

    def ingest(self, *, tenant_id: UUID, source_uri: str, title: str, domain: str, content_type: str, body: bytes) -> int:
        source = self._store.sources.get((tenant_id, source_uri))
        if source is None or not source.active or domain not in source.domains:
            raise ValueError("source is not approved for this tenant and domain")
        if content_type not in source.allowed_content_types or len(body) > 5_000_000 or body.startswith(b"MZ"):
            raise ValueError("document failed type or malware safety checks")
        try:
            cleaned = _SPACE.sub(" ", _SCRIPT.sub("", body.decode("utf-8", errors="strict"))).strip()
        except UnicodeDecodeError as error:
            raise ValueError("document must be valid UTF-8 text") from error
        if not cleaned:
            raise ValueError("document has no indexable content")
        previous = [item for item in self._store.chunks if item.tenant_id == tenant_id and item.source_uri == source_uri]
        content_hash = hashlib.sha256(cleaned.encode()).hexdigest()
        if any(item.content_hash == content_hash and item.tombstoned_at is None for item in previous):
            return max(item.document_version for item in previous)
        document_version = max((item.document_version for item in previous), default=0) + 1
        self.tombstone(tenant_id=tenant_id, source_uri=source_uri)
        now = datetime.now(UTC)
        self._store.chunks.extend(
            KnowledgeChunk(
                id=f"{content_hash[:16]}-{ordinal}", tenant_id=tenant_id, source_uri=source_uri,
                document_version=document_version, domain=domain, title=title, text=fragment,
                content_hash=content_hash, embedding=self._embeddings.embed(fragment), effective_at=now,
            )
            for ordinal, fragment in enumerate(_chunks(cleaned, self._chunk_size))
        )
        self._store.reindex_jobs.append({"tenant_id": str(tenant_id), "source_uri": source_uri, "version": str(document_version)})
        return document_version

    def tombstone(self, *, tenant_id: UUID, source_uri: str) -> None:
        now = datetime.now(UTC)
        self._store.chunks[:] = [
            item if item.tenant_id != tenant_id or item.source_uri != source_uri else KnowledgeChunk(**{**asdict(item), "tombstoned_at": now})
            for item in self._store.chunks
        ]


def _chunks(text: str, size: int) -> list[str]:
    words, output, current = text.split(), [], []
    for word in words:
        if len(" ".join(current + [word])) > size and current:
            output.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        output.append(" ".join(current))
    return output


@dataclass(frozen=True, slots=True)
class RetrievalMatch:
    chunk_id: str
    text: str
    score: float
    citation: dict[str, str]


class PgvectorRetrievalService:
    """Hybrid lexical/vector retrieval with mandatory tenant and freshness filtering."""

    def __init__(self, store: KnowledgeStore, embeddings: EmbeddingPort | None = None) -> None:
        self._store = store
        self._embeddings = embeddings or HashEmbedding()

    def search(self, *, tenant_id: UUID, query: str, domains: set[str], limit: int, threshold: float = 0.15) -> list[RetrievalMatch]:
        if not query.strip() or not 1 <= limit <= 20:
            raise ValueError("query and result limit are invalid")
        now, query_embedding = datetime.now(UTC), self._embeddings.embed(query)
        query_words = set(re.findall(r"[a-z0-9]+", query.casefold()))
        ranked: list[RetrievalMatch] = []
        for chunk in self._store.chunks:
            source = self._store.sources.get((tenant_id, chunk.source_uri))
            if (chunk.tenant_id != tenant_id or source is None or not source.active or chunk.domain not in domains
                    or chunk.tombstoned_at is not None or chunk.effective_at > now or (chunk.expires_at and chunk.expires_at <= now)):
                continue
            lexical = len(query_words & set(re.findall(r"[a-z0-9]+", chunk.text.casefold()))) / max(len(query_words), 1)
            vector = sum(left * right for left, right in zip(query_embedding, chunk.embedding, strict=True))
            score = 0.35 * lexical + 0.65 * vector
            if score >= threshold:
                ranked.append(RetrievalMatch(chunk.id, chunk.text, score, {"source_uri": chunk.source_uri, "title": chunk.title, "chunk_id": chunk.id, "document_version": str(chunk.document_version)}))
        return sorted(ranked, key=lambda item: (-item.score, item.chunk_id))[:limit]


class PostgresPgvectorRetrievalService:
    """Production pgvector query path; the in-memory service remains a deterministic test double."""

    def __init__(self, database_url: str, embeddings: EmbeddingPort) -> None:
        self._database_url = database_url
        self._embeddings = embeddings

    def search(
        self,
        *,
        tenant_id: UUID,
        query: str,
        domains: set[str],
        limit: int,
        threshold: float = 0.15,
    ) -> list[RetrievalMatch]:
        if not query.strip() or not domains or not 1 <= limit <= 20:
            raise ValueError("query, domains, and result limit are required")
        vector = "[" + ",".join(str(value) for value in self._embeddings.embed(query)) + "]"
        statement = """
            select c.id, c.approved_text, d.source_uri, d.title, d.document_version,
                   1 - (e.embedding <=> %(embedding)s::vector) as score
              from knowledge_embeddings e
              join knowledge_chunks c on c.tenant_id = e.tenant_id and c.id = e.chunk_id
              join knowledge_documents d on d.tenant_id = c.tenant_id and d.id = c.document_id
             where d.tenant_id = %(tenant_id)s
               and d.status = 'ACTIVE'
               and d.domain = any(%(domains)s)
               and (d.effective_at is null or d.effective_at <= timezone('utc', now()))
               and (d.expires_at is null or d.expires_at > timezone('utc', now()))
               and 1 - (e.embedding <=> %(embedding)s::vector) >= %(threshold)s
             order by e.embedding <=> %(embedding)s::vector, c.id
             limit %(limit)s
        """
        import psycopg

        with psycopg.connect(self._database_url, connect_timeout=2) as connection:
            connection.execute("select set_config('app.tenant_id', %s, true)", (str(tenant_id),))
            rows = connection.execute(
                statement,
                {
                    "tenant_id": tenant_id,
                    "domains": list(domains),
                    "embedding": vector,
                    "threshold": threshold,
                    "limit": limit,
                },
            ).fetchall()
        return [
            RetrievalMatch(
                chunk_id=str(chunk_id),
                text=text,
                score=float(score),
                citation={
                    "source_uri": source_uri,
                    "title": title,
                    "chunk_id": str(chunk_id),
                    "document_version": str(document_version),
                },
            )
            for chunk_id, text, source_uri, title, document_version, score in rows
        ]
