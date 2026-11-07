"""
API request/response shapes. Field names are camelCase on the wire to match the
frontend types (frontend/src/types.ts); Python code uses snake_case.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ---------- geometry ----------
class BBoxGeometry(ApiModel):
    kind: Literal["bbox"]
    x: float
    y: float
    w: float
    h: float


class RegionGeometry(ApiModel):
    kind: Literal["region"]
    polygons: list[list[list[tuple[float, float]]]]


class PointGeometry(ApiModel):
    kind: Literal["point"]
    x: float
    y: float


Geometry = Annotated[BBoxGeometry | RegionGeometry | PointGeometry, Field(discriminator="kind")]

ShapeName = Literal["bbox", "polygon", "point"]
StatusName = Literal["new", "in_progress", "annotated", "in_review", "done", "rejected"]


# ---------- people ----------
class UserOut(ApiModel):
    id: uuid.UUID
    display_name: str
    initials: str
    role: str


# ---------- projects ----------
class LevelOut(ApiModel):
    id: uuid.UUID
    depth: int
    name: str
    is_split_level: bool


class ProjectOut(ApiModel):
    id: uuid.UUID
    name: str
    description: str
    domain_pack: str
    levels: list[LevelOut]
    created_at: datetime


class LevelIn(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    is_split_level: bool = False


class LabelIn(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = "#3fb59a"
    allowed_shapes: list[ShapeName] = Field(min_length=1)
    must_be_inside_name: str | None = None
    hotkey: str | None = None


class ProjectCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    domain_pack: Literal["generic", "cytology"] = "generic"
    levels: list[LevelIn] = Field(min_length=2)
    labels: list[LabelIn] = Field(default_factory=list)


class SetupLevel(ApiModel):
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)
    is_split_level: bool


class SetupLabel(ApiModel):
    id: str  # existing UUID, or a temporary id for a new label
    name: str = Field(min_length=1, max_length=100)
    color: str
    allowed_shapes: list[ShapeName] = Field(min_length=1)
    must_be_inside_id: str | None = None
    hotkey: str | None = None


class SetupIn(ApiModel):
    levels: list[SetupLevel]
    labels: list[SetupLabel]


class LabelOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    color: str
    allowed_shapes: list[str]
    must_be_inside_id: uuid.UUID | None
    hotkey: str


class SetupOut(ApiModel):
    project: ProjectOut
    labels: list[LabelOut]


# ---------- hierarchy and images ----------
class NodeOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    level_id: uuid.UUID
    parent_id: uuid.UUID | None
    external_id: str
    meta: dict


class ItemOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    node_id: uuid.UUID | None
    name: str
    url: str
    thumb_url: str
    dzi_url: str | None = None  # set when the image is tiled (large images)
    mpp: float | None = None  # microns per pixel, when the scan says (whole-slide images)
    width: int
    height: int
    status: StatusName
    assignee_id: uuid.UUID | None
    sha256: str


class UploadResult(ApiModel):
    created: list[ItemOut]
    nodes: list[NodeOut]
    skipped: list[dict]  # {"file": ..., "reason": ...}


class ItemPatch(ApiModel):
    status: StatusName | None = None
    assignee_id: uuid.UUID | None = None


class BulkAssign(ApiModel):
    item_ids: list[uuid.UUID] = Field(min_length=1)
    assignee_id: uuid.UUID | None


# ---------- annotations (append-only) ----------
class VersionIn(ApiModel):
    """One new version. version must be latest + 1 (or 1 for a new annotation)."""

    annotation_id: uuid.UUID
    version: int = Field(ge=1)
    item_id: uuid.UUID
    label_id: uuid.UUID
    shape: ShapeName
    geometry: Geometry | None = None
    is_deleted: bool = False
    source: Literal["human", "tool_assisted"] = "human"


class VersionOut(ApiModel):
    row_id: uuid.UUID
    annotation_id: uuid.UUID
    version: int
    item_id: uuid.UUID
    label_id: uuid.UUID
    shape: ShapeName
    geometry: dict | None
    is_deleted: bool
    source: str
    created_by: uuid.UUID | None
    created_at: datetime
    import_batch_id: uuid.UUID | None


# ---------- activity ----------
class EventIn(ApiModel):
    project_id: uuid.UUID
    item_id: uuid.UUID | None = None
    action: str = Field(min_length=1, max_length=100)
    active_ms: int = Field(default=0, ge=0, le=12 * 3600 * 1000)
    detail: str | None = None


class EventOut(ApiModel):
    id: int
    project_id: uuid.UUID | None
    user_id: uuid.UUID | None
    item_id: uuid.UUID | None
    action: str
    active_ms: int
    at: datetime
    detail: str | None


class ReviewIn(ApiModel):
    item_id: uuid.UUID
    verdict: Literal["approved", "rejected"]
    comment: str = ""


class ReviewOut(ApiModel):
    id: uuid.UUID
    item_id: uuid.UUID
    reviewer_id: uuid.UUID | None
    verdict: str
    comment: str
    at: datetime


class ImportOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    file_name: str
    format: str
    status: str
    uploaded_by: uuid.UUID | None
    created_at: datetime
    annotations: int
    images: int


class ProjectBundle(ApiModel):
    """Everything the UI needs for one project, in one request."""

    nodes: list[NodeOut]
    items: list[ItemOut]
    labels: list[LabelOut]
    annotations: list[VersionOut]
    events: list[EventOut]
    reviews: list[ReviewOut]
    imports: list[ImportOut]
