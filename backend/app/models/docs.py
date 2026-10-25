"""Guideline documents (SOPs, annotation protocols) split into searchable sections."""

import uuid

from sqlalchemy import Computed, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk


class Document(UUIDPk, CreatedAt, Base):
    __tablename__ = "documents"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    file_name: Mapped[str] = mapped_column(String(300))
    storage_key: Mapped[str] = mapped_column(String(1024))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class DocumentChunk(UUIDPk, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (Index("ix_document_chunks_tsv", "tsv", postgresql_using="gin"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(500))  # heading path, e.g. "3 Nuclei > 3.2 Atypical nuclei"
    page: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', coalesce(section, '') || ' ' || text)", persisted=True)
    )
