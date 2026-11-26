"""Retrieval for matching, duplicates and Ask Daelys (design rule 8).

Every query runs inside a tenant transaction (RLS) and filters documents by the caller's role in
SQL, before any ranking, so a restricted document can never be a candidate. Ranking uses pgvector
when an embedding is available, and otherwise Postgres full-text / trigram similarity. The mode
used is returned with every result: it is never silently swapped.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.briefs import embed
from core.security import Role

ROLE_RANK_SQL = (
    "CASE {col} WHEN 'viewer' THEN 0 WHEN 'member' THEN 1 WHEN 'lead' THEN 2 WHEN 'owner' THEN 3 END"
)
VIEW_RANK = {Role.viewer: 0, Role.member: 1, Role.lead: 2, Role.owner: 3, Role.agent: 1}
EVENT_TEXT = (
    "coalesce(e.payload->>'body', '') || ' ' || coalesce(e.payload->>'text', '') || ' ' "
    "|| coalesce(e.payload->'fields'->>'title', '')"
)

_ITEMS_VECTOR = text("""
    SELECT id, key, title, status, lane, 1 - (title_embedding <=> CAST(:q AS vector)) AS score FROM work_items
    WHERE title_embedding IS NOT NULL AND status NOT IN ('done', 'cancelled')
      AND (CAST(:exclude AS uuid) IS NULL OR id <> :exclude)
    ORDER BY title_embedding <=> CAST(:q AS vector) LIMIT :k
""")
_ITEMS_TRGM = text("""
    SELECT id, key, title, status, lane, similarity(title, :q) AS score FROM work_items
    WHERE title IS NOT NULL AND status NOT IN ('done', 'cancelled')
      AND (CAST(:exclude AS uuid) IS NULL OR id <> :exclude)
    ORDER BY similarity(title, :q) DESC, key LIMIT :k
""")
# Constant SQL fragments only (role-rank CASE, event text expression); every value is a bind parameter.
_CHUNKS_VECTOR = text(
    """
    SELECT c.id, c.content, c.char_start, c.char_end, a.id AS artifact_id, a.title AS artifact_title,
           1 - (c.embedding <=> CAST(:q AS vector)) AS score
    FROM chunks c JOIN artifacts a ON a.id = c.artifact_id
    WHERE c.embedding IS NOT NULL AND {rank} <= :viewer_rank
    ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :k
    """.format(rank=ROLE_RANK_SQL.format(col="a.min_role"))  # noqa: S608
)
_CHUNKS_FTS = text(
    """
    SELECT c.id, c.content, c.char_start, c.char_end, a.id AS artifact_id, a.title AS artifact_title,
           ts_rank(to_tsvector('simple', c.content), q) AS score
    FROM chunks c JOIN artifacts a ON a.id = c.artifact_id, websearch_to_tsquery('simple', :q) q
    WHERE {rank} <= :viewer_rank AND to_tsvector('simple', c.content) @@ q
    ORDER BY score DESC LIMIT :k
    """.format(rank=ROLE_RANK_SQL.format(col="a.min_role"))  # noqa: S608
)
_EVENTS_FTS = text(
    f"""
    SELECT e.id, e.type, e.occurred_at, e.subject_key, e.actor, e.payload,
           ts_rank(to_tsvector('simple', {EVENT_TEXT}), q) AS score
    FROM events e, websearch_to_tsquery('simple', :q) q
    WHERE to_tsvector('simple', {EVENT_TEXT}) @@ q
    ORDER BY score DESC, e.occurred_at DESC LIMIT :k
    """  # noqa: S608
)


@dataclass(frozen=True)
class Hits:
    mode: str  # "vector" | "lexical"
    rows: list[dict[str, Any]]


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(map(str, vector)) + "]"


async def similar_items(session: AsyncSession, query: str, *, k: int = 5, exclude: Any = None) -> Hits:
    vectors = await embed([query])
    if vectors is not None:
        rows = await session.execute(
            _ITEMS_VECTOR, {"q": _vector_literal(vectors[0]), "k": k, "exclude": exclude}
        )
        found = [dict(r) for r in rows.mappings()]
        if found:
            return Hits("vector", found)
    rows = await session.execute(_ITEMS_TRGM, {"q": query, "k": k, "exclude": exclude})
    return Hits("lexical", [dict(r) for r in rows.mappings()])


def _or_query(question: str) -> str:
    """Full-text query matching any meaningful word, so long questions still find evidence."""
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in question.lower()).split() if len(w) > 2]
    return " OR ".join(dict.fromkeys(words))


async def search_documents(session: AsyncSession, question: str, role: Role, *, k: int = 6) -> Hits:
    params = {"viewer_rank": VIEW_RANK[role], "k": k}
    vectors = await embed([question])
    if vectors is not None:
        rows = await session.execute(_CHUNKS_VECTOR, {**params, "q": _vector_literal(vectors[0])})
        found = [dict(r) for r in rows.mappings()]
        if found:
            return Hits("vector", found)
    query = _or_query(question)
    if not query:
        return Hits("lexical", [])
    rows = await session.execute(_CHUNKS_FTS, {**params, "q": query})
    return Hits("lexical", [dict(r) for r in rows.mappings()])


async def search_events(session: AsyncSession, question: str, *, k: int = 10) -> Hits:
    query = _or_query(question)
    if not query:
        return Hits("lexical", [])
    rows = await session.execute(_EVENTS_FTS, {"q": query, "k": k})
    return Hits("lexical", [dict(r) for r in rows.mappings()])
