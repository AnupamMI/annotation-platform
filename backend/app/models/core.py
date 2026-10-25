"""Users, projects, the configurable hierarchy, images and label classes."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAt, StrEnum, UUIDPk


class Role(StrEnum):
    admin = "admin"
    annotator = "annotator"
    reviewer = "reviewer"
    operator = "operator"  # e.g. scanner operator: uploads and QC, no labeling
    viewer = "viewer"


class User(UUIDPk, CreatedAt, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role"), default=Role.annotator)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    password_hash: Mapped[str | None] = mapped_column(String(256))  # None = can't log in yet
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(UUIDPk, CreatedAt, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    # Which domain pack configures this project, e.g. "generic", "cytology".
    domain_pack: Mapped[str] = mapped_column(String(50), default="generic")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)

    levels: Mapped[list["HierarchyLevel"]] = relationship(
        back_populates="project", order_by="HierarchyLevel.depth"
    )
    label_classes: Mapped[list["LabelClass"]] = relationship(back_populates="project")


class HierarchyLevel(UUIDPk, Base):
    """One level of a project's hierarchy, e.g. patient (0) > slide (1) > scan (2) > tile (3)."""

    __tablename__ = "hierarchy_levels"
    __table_args__ = (
        UniqueConstraint("project_id", "depth"),
        UniqueConstraint("project_id", "name"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    depth: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    # JSON Schema for node metadata at this level. Fields can carry "x-sensitive": true.
    metadata_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Dataset splits never place one node of this level in two splits (prevents leakage).
    is_split_level: Mapped[bool] = mapped_column(Boolean, default=False)

    project: Mapped[Project] = relationship(back_populates="levels")


class Node(UUIDPk, CreatedAt, Base):
    """A node in the project tree: one patient, one slide, one farm, one store..."""

    __tablename__ = "nodes"
    __table_args__ = (UniqueConstraint("project_id", "level_id", "external_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    level_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hierarchy_levels.id", ondelete="RESTRICT"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), index=True
    )
    # Pseudonymous, human-readable ID such as a slide ID. Never a real patient name.
    external_id: Mapped[str] = mapped_column(String(200))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    parent: Mapped["Node | None"] = relationship(remote_side="Node.id")


class ItemStatus(StrEnum):
    new = "new"
    in_progress = "in_progress"
    annotated = "annotated"
    in_review = "in_review"
    done = "done"
    rejected = "rejected"


class Item(UUIDPk, CreatedAt, Base):
    """One image. Bytes live in object storage; this row stores where and what."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("project_id", "sha256"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL"), index=True
    )
    storage_key: Mapped[str] = mapped_column(String(1024))
    original_filename: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64))
    # Perceptual hash, used by import to match renamed or re-encoded images.
    phash: Mapped[str | None] = mapped_column(String(32), index=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # True once a tile pyramid exists (large images); small images are served directly.
    is_tiled: Mapped[bool] = mapped_column(Boolean, default=False)
    tiles_prefix: Mapped[str | None] = mapped_column(String(1024))
    tile_size: Mapped[int | None] = mapped_column(Integer)
    tile_format: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, name="item_status"), default=ItemStatus.new, index=True
    )
    # Who is working on it now. Tasks hold the full assignment history later.
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    thumb_key: Mapped[str | None] = mapped_column(String(1024))
    meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class LabelClass(UUIDPk, CreatedAt, Base):
    __tablename__ = "label_classes"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(9), default="#4F8EF7")
    # Which shape types are allowed for this label, e.g. ["bbox", "polygon"].
    allowed_shapes: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    # JSON Schema for per-annotation attributes (e.g. {"occluded": bool}).
    attributes_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Optional containment rule used by the consistency checker,
    # e.g. a "nucleus" must lie inside a "cell".
    must_be_inside_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("label_classes.id", ondelete="SET NULL")
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    hotkey: Mapped[str | None] = mapped_column(String(4))  # "1".."9"
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="label_classes")
