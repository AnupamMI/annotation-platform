"""
Guideline search.

Documents are split into sections (by Markdown headings, numbered headings, or
paragraph groups) and searched with PostgreSQL full-text search. Answers cite the
section and page. Semantic (embedding) search with pgvector can be added later
behind the same search() function.
"""

from __future__ import annotations

import io
import re
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.docs import Document, DocumentChunk

HEADING = re.compile(r"^(#{1,6})\s+(.+)$|^((?:\d+\.)*\d+)\.?\s+([A-Z].{2,80})$")
TARGET_CHARS = 900


def extract(file_name: str, data: bytes) -> list[tuple[int | None, str]]:
    """(page, text) pages. PDFs page by page; text and Markdown as one page."""
    if file_name.lower().endswith(".pdf"):
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            return [(i + 1, p.extract_text() or "") for i, p in enumerate(reader.pages)]
        except Exception as exc:
            raise ValueError("The PDF could not be read.") from exc
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return [(None, data.decode(enc))]
        except UnicodeDecodeError:
            continue
    raise ValueError("The text file could not be read.")


def chunk(pages: list[tuple[int | None, str]]) -> list[dict]:
    chunks: list[dict] = []
    trail: list[tuple[int, str]] = []  # (level, heading)
    buf: list[str] = []
    buf_page: int | None = None
    first = True

    def flush():
        body = "\n".join(buf).strip()
        if body:
            chunks.append({"section": " > ".join(h for _, h in trail) or "Introduction", "page": buf_page, "text": body})
        buf.clear()

    for page, content in pages:
        for line in content.splitlines():
            m = HEADING.match(line.strip())
            if m:
                flush()
                if first and m.group(1) == "#":  # the document's own title, not a section
                    first = False
                    continue
                first = False
                level = len(m.group(1)) if m.group(1) else m.group(3).count(".") + 1
                title = (m.group(2) or f"{m.group(3)} {m.group(4)}").strip()
                trail[:] = [t for t in trail if t[0] < level] + [(level, title)]
                buf_page = page
                continue
            if not buf:
                buf_page = page
            buf.append(line)
            if sum(len(b) for b in buf) > TARGET_CHARS and line.strip() == "":
                flush()
    flush()
    return [c for c in chunks if len(c["text"]) > 20]


async def add(session: AsyncSession, document: Document, data: bytes) -> int:
    parts = chunk(extract(document.file_name, data))
    if not parts:
        raise ValueError("No text was found in the document.")
    for i, c in enumerate(parts):
        session.add(DocumentChunk(document_id=document.id, position=i, section=c["section"][:500], page=c["page"], text=c["text"]))
    return len(parts)


async def search(session: AsyncSession, project_id: uuid.UUID, query: str, limit: int = 4) -> list[dict]:
    q = text(
        """
        SELECT c.id, d.title, c.section, c.page, c.text,
               ts_rank_cd(c.tsv, websearch_to_tsquery('english', :q)) AS rank
        FROM document_chunks c JOIN documents d ON d.id = c.document_id
        WHERE d.project_id = :p AND c.tsv @@ websearch_to_tsquery('english', :q)
        ORDER BY rank DESC, c.position LIMIT :n
        """
    )
    rows = (await session.execute(q, {"q": _or_query(query), "p": project_id, "n": limit})).mappings().all()
    return [dict(r) for r in rows]


def _or_query(question: str) -> str:
    """Match any meaningful word, ranked by how many match (questions are wordy)."""
    words = [w for w in re.findall(r"[A-Za-z0-9-]{3,}", question) if w.lower() not in STOP]
    return " or ".join(words) or question


STOP = {
    "what", "does", "our", "the", "say", "about", "how", "should", "guideline", "guidelines", "protocol", "sop", "with",
    "when", "which", "where", "who", "why", "for", "and", "are", "this", "that", "there", "rule", "rules", "tell", "please",
}


async def documents(session: AsyncSession, project_id: uuid.UUID) -> list[Document]:
    return (await session.execute(select(Document).where(Document.project_id == project_id).order_by(Document.created_at))).scalars().all()
