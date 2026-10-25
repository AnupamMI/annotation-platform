"""
Append-only annotations. There is exactly one write endpoint: add a version.
Create = version 1, edit = latest + 1, delete = latest + 1 with isDeleted.
Nothing is ever updated in place, so every past state stays restorable.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.convert import version_out
from app.api.deps import require
from app.db import get_session
from app.geo import GeometryError, to_db
from app.models import Annotation, AnnotationSource, Item, LabelClass, ShapeType, User

router = APIRouter(tags=["annotations"])


@router.get("/items/{item_id}/annotations", response_model=list[schemas.VersionOut])
async def item_annotations(item_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """All versions for one image, oldest first."""
    rows = (await session.execute(select(Annotation).where(Annotation.item_id == item_id).order_by(Annotation.annotation_id, Annotation.version))).scalars()
    return [version_out(a) for a in rows]


@router.post(
    "/annotations/versions",
    response_model=schemas.VersionOut,
    status_code=201,
    responses={409: {"description": "Someone else changed this annotation first; the latest version is returned."}},
)
async def append_version(
    body: schemas.VersionIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require("annotator", "reviewer")),
):
    item = await session.get(Item, body.item_id)
    if not item:
        raise HTTPException(404, "Image not found.")
    label = await session.get(LabelClass, body.label_id)
    if not label or label.project_id != item.project_id:
        raise HTTPException(422, "That label does not belong to this project.")

    latest = (
        await session.execute(
            select(Annotation).where(Annotation.annotation_id == body.annotation_id).order_by(Annotation.version.desc()).limit(1)
        )
    ).scalar_one_or_none()
    expected = (latest.version if latest else 0) + 1
    if body.version != expected:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "This annotation was changed by someone else. Reload to see the latest version.",
                "latest": version_out(latest).model_dump(mode="json", by_alias=True) if latest else None,
            },
        )
    if latest and latest.item_id != body.item_id:
        raise HTTPException(422, "An annotation cannot move to a different image.")

    if not body.is_deleted and body.shape not in (label.allowed_shapes or []):
        raise HTTPException(422, f"{label.name} cannot be drawn as {body.shape}.")

    if body.is_deleted:
        if not latest:
            raise HTTPException(422, "Cannot delete an annotation that does not exist.")
        geometry = latest.geometry  # tombstone keeps the last shape for history
    else:
        if body.geometry is None:
            raise HTTPException(422, "Geometry is required.")
        g = body.geometry.model_dump(mode="json")
        if g["kind"] == "point" and body.shape != "point" or g["kind"] == "bbox" and body.shape != "bbox" or g["kind"] == "region" and body.shape != "polygon":
            raise HTTPException(422, f"A {g['kind']} geometry does not match shape {body.shape}.")
        try:
            geometry = to_db(g)
        except GeometryError as exc:
            raise HTTPException(422, str(exc))
        _check_inside_image(g, item)

    row = Annotation(
        annotation_id=body.annotation_id,
        version=body.version,
        item_id=body.item_id,
        label_class_id=body.label_id,
        shape_type=ShapeType(body.shape),
        geometry=geometry,
        attributes={},
        is_deleted=body.is_deleted,
        source=AnnotationSource(body.source),
        created_by=user.id,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # Two writes raced for the same version number: the unique constraint picked one.
        await session.rollback()
        raise HTTPException(409, "This annotation was changed by someone else at the same moment. Reload and try again.")
    await session.refresh(row)
    return version_out(row)


def _check_inside_image(g: dict, item: Item) -> None:
    tol = 1.0
    if g["kind"] == "point":
        pts = [(g["x"], g["y"])]
    elif g["kind"] == "bbox":
        pts = [(g["x"], g["y"]), (g["x"] + g["w"], g["y"] + g["h"])]
    else:
        pts = [p for poly in g["polygons"] for ring in poly for p in ring]
    for x, y in pts:
        if x < -tol or y < -tol or x > item.width + tol or y > item.height + tol:
            raise HTTPException(422, "The shape goes outside the image.")


@router.get("/projects/{project_id}/annotation-counts")
async def annotation_counts(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Current (latest, not deleted) annotations per label. Used by the overview."""
    latest = (
        select(Annotation.annotation_id, func.max(Annotation.version).label("v"))
        .join(Item, Item.id == Annotation.item_id)
        .where(Item.project_id == project_id)
        .group_by(Annotation.annotation_id)
        .subquery()
    )
    rows = await session.execute(
        select(Annotation.label_class_id, func.count())
        .join(latest, (Annotation.annotation_id == latest.c.annotation_id) & (Annotation.version == latest.c.v))
        .where(Annotation.is_deleted.is_(False))
        .group_by(Annotation.label_class_id)
    )
    return {str(k): v for k, v in rows}
