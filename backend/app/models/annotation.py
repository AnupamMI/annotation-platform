"""Append-only annotations and import batches."""

import uuid
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, StrEnum, UUIDPk


class ShapeType(StrEnum):
    bbox = "bbox"
    polygon = "polygon"
    mask = "mask"  # stored as (multi)polygon geometry
    point = "point"
    polyline = "polyline"
    classification = "classification"  # image-level label, no geometry


class AnnotationSource(StrEnum):
    human = "human"
    tool_assisted = "tool_assisted"  # annotator-triggered helper, e.g. click-to-segment
    import_ = "import"


class Annotation(UUIDPk, CreatedAt, Base):
    """
    One VERSION of an annotation. Rows are never updated or deleted.

    - Editing inserts a new row: same annotation_id, version + 1.
    - Deleting inserts a tombstone row: is_deleted = true.
    - Current state = latest version per annotation_id where is_deleted is false.

    Geometry is in full-image pixel coordinates (level 0), SRID 0.
    """

    __tablename__ = "annotations"
    __table_args__ = (
        UniqueConstraint("annotation_id", "version"),
        Index("ix_annotations_geometry", "geometry", postgresql_using="gist"),
        Index("ix_annotations_item_current", "item_id", "annotation_id", "version"),
    )

    annotation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, server_default=text("gen_random_uuid()")
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    label_class_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("label_classes.id", ondelete="RESTRICT"), index=True
    )
    shape_type: Mapped[ShapeType] = mapped_column(Enum(ShapeType, name="shape_type"))
    geometry: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="GEOMETRY", srid=0, spatial_index=False)
    )
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    source: Mapped[AnnotationSource] = mapped_column(
        # store the values ("import"), not the Python names ("import_")
        Enum(AnnotationSource, name="annotation_source", values_callable=lambda e: [m.value for m in e]),
        default=AnnotationSource.human,
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL"), index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # Imported annotations start unreviewed; human work in the app starts reviewed=false too
    # until it passes review. Kept per version so a review applies to exactly what was seen.
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)


class ImportStatus(StrEnum):
    uploaded = "uploaded"
    inspecting = "inspecting"
    awaiting_approval = "awaiting_approval"
    committing = "committing"
    committed = "committed"
    rolled_back = "rolled_back"
    failed = "failed"
    cancelled = "cancelled"


class ImportBatch(UUIDPk, CreatedAt, Base):
    """One upload of existing annotations, processed and committed as a single undoable unit."""

    __tablename__ = "import_batches"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    detected_format: Mapped[str | None] = mapped_column(String(50))  # yolo, coco, cvat_xml...
    original_key: Mapped[str] = mapped_column(String(1024))  # uploaded archive in storage
    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus, name="import_status"), default=ImportStatus.uploaded
    )
    # source label -> project label_class_id, as approved by the user
    label_mapping: Mapped[dict] = mapped_column(JSONB, default=dict)
    # how conflicts with existing annotations were resolved: keep_both / replace / skip
    conflict_policy: Mapped[str | None] = mapped_column(String(20))
    # inspection report: counts, unmatched images, invalid shapes, conflicts
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
