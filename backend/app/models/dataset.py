"""Reproducible dataset snapshots and exports."""

import uuid

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, StrEnum, UUIDPk


class Split(StrEnum):
    train = "train"
    val = "val"
    test = "test"


class Dataset(UUIDPk, CreatedAt, Base):
    __tablename__ = "datasets"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    # The selection rules used to build it (filters, ratios, seed), for reproducibility.
    spec: Mapped[dict] = mapped_column(JSONB, default=dict)
    split_level_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hierarchy_levels.id", ondelete="SET NULL")
    )
    export_format: Mapped[str | None] = mapped_column(String(50))
    manifest_key: Mapped[str | None] = mapped_column(String(1024))
    export_key: Mapped[str | None] = mapped_column(String(1024))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )


class DatasetItem(Base):
    __tablename__ = "dataset_items"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("items.id", ondelete="RESTRICT"), primary_key=True
    )
    split: Mapped[Split] = mapped_column(Enum(Split, name="dataset_split"))


class DatasetAnnotation(Base):
    """Exact annotation ROWS (versions) frozen into a dataset."""

    __tablename__ = "dataset_annotations"

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True
    )
    annotation_row_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("annotations.id", ondelete="RESTRICT"), primary_key=True
    )
