"""Brief ingestion: markdown, plain text or PDF -> `artifacts` row + offset-preserving chunks + embeddings."""

import asyncio
import hashlib
import io
import logging
from dataclasses import dataclass
from uuid import UUID

import httpx
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings

log = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
MAX_PDF_PAGES = 50
CHUNK_TARGET_CHARS = 800
EMBED_DIM = 1024
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
TEXT_TYPES = {"text/markdown", "text/plain", "text/x-markdown"}
PDF_TYPE = "application/pdf"


class UnsupportedBrief(ValueError):
    pass


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    start: int
    end: int
    content: str


@dataclass(frozen=True)
class StoredBrief:
    artifact_id: UUID
    chunks: int
    embedded: bool


def extract_text(content_type: str, body: bytes) -> str:
    media = content_type.split(";")[0].strip().lower()
    if len(body) > MAX_BYTES:
        raise UnsupportedBrief(f"brief larger than {MAX_BYTES} bytes")
    if media in TEXT_TYPES:
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedBrief("text brief must be UTF-8") from exc
    if media == PDF_TYPE:
        try:
            reader = PdfReader(io.BytesIO(body))
            if len(reader.pages) > MAX_PDF_PAGES:
                raise UnsupportedBrief(f"PDF has more than {MAX_PDF_PAGES} pages")
            return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
        except PdfReadError as exc:
            raise UnsupportedBrief(f"unreadable PDF: {exc}") from exc
    raise UnsupportedBrief(f"unsupported content type {media!r}; use markdown, plain text or PDF")


def chunk_text(text_: str, target: int = CHUNK_TARGET_CHARS) -> list[Chunk]:
    """Paragraph-packed chunks. Offsets index into the original text, so citations stay exact."""
    paragraphs: list[tuple[int, int]] = []
    start: int | None = None
    end = 0
    for index, line in _lines_with_offsets(text_):
        if line.strip():
            if start is None:
                start = index
            end = index + len(line)
        elif start is not None:
            paragraphs.append((start, end))
            start = None
    if start is not None:
        paragraphs.append((start, end))

    chunks: list[Chunk] = []
    cur_start: int | None = None
    cur_end = 0
    for p_start, p_end in paragraphs:
        if cur_start is not None and p_end - cur_start > target:
            chunks.append(Chunk(len(chunks), cur_start, cur_end, text_[cur_start:cur_end]))
            cur_start = None
        if cur_start is None:
            cur_start = p_start
        cur_end = p_end
    if cur_start is not None:
        chunks.append(Chunk(len(chunks), cur_start, cur_end, text_[cur_start:cur_end]))
    return chunks


def _lines_with_offsets(text_: str) -> list[tuple[int, str]]:
    out, offset = [], 0
    for line in text_.splitlines(keepends=True):
        out.append((offset, line.rstrip("\r\n")))
        offset += len(line)
    return out


async def embed(texts: list[str]) -> list[list[float]] | None:
    """Voyage embeddings, or None when no key is configured (caller reports `embedded: false`)."""
    settings = get_settings()
    if not texts or settings.voyage_api_key is None or not settings.voyage_api_key.get_secret_value():
        return None
    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(3):
            resp = await client.post(
                VOYAGE_URL,
                headers={"Authorization": f"Bearer {settings.voyage_api_key.get_secret_value()}"},
                json={
                    "input": texts,
                    "model": settings.embedding_model,
                    "input_type": "document",
                    "output_dimension": EMBED_DIM,
                },
            )
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
            await asyncio.sleep(2**attempt)
        resp.raise_for_status()
    vectors = [row["embedding"] for row in sorted(resp.json()["data"], key=lambda r: r["index"])]
    if any(len(v) != EMBED_DIM for v in vectors):
        raise ValueError(f"embedding model returned a dimension other than {EMBED_DIM}")
    return vectors


async def store_brief(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID | None,
    title: str,
    content: str,
    content_type: str,
    created_by: UUID | None,
    vectors: list[list[float]] | None,
    chunks: list[Chunk],
) -> StoredBrief:
    artifact_id = await session.scalar(
        text("""
            INSERT INTO artifacts
                (tenant_id, project_id, kind, title, content_type, content, sha256, created_by)
            VALUES (:t, :p, 'brief', :title, :ct, :content, :sha, :by) RETURNING id
        """),
        {
            "t": tenant_id,
            "p": project_id,
            "title": title,
            "ct": content_type,
            "content": content,
            "sha": hashlib.sha256(content.encode()).hexdigest(),
            "by": created_by,
        },
    )
    for chunk in chunks:
        vector = vectors[chunk.ordinal] if vectors else None
        await session.execute(
            text("""
                INSERT INTO chunks (tenant_id, artifact_id, ordinal, content, char_start, char_end, embedding)
                VALUES (:t, :a, :o, :c, :s, :e, CAST(:v AS vector))
            """),
            {
                "t": tenant_id,
                "a": artifact_id,
                "o": chunk.ordinal,
                "c": chunk.content,
                "s": chunk.start,
                "e": chunk.end,
                "v": "[" + ",".join(map(str, vector)) + "]" if vector else None,
            },
        )
    if vectors is None:
        log.warning("briefs.stored_without_embeddings", extra={"artifact": str(artifact_id)})
    return StoredBrief(UUID(str(artifact_id)), len(chunks), vectors is not None)
