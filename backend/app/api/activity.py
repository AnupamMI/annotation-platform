import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.convert import event_out, review_out
from app.api.deps import get_project, require
from app.api.projects import import_list
from app.db import get_session
from app.models import (
    Annotation,
    AnnotationSource,
    Event,
    ImportBatch,
    ImportStatus,
    Item,
    ItemStatus,
    Review,
    ReviewVerdict,
    User,
)

router = APIRouter(tags=["activity"])


@router.post("/events", response_model=schemas.EventOut, status_code=201)
async def add_event(body: schemas.EventIn, session: AsyncSession = Depends(get_session), user: User = Depends(require("annotator", "reviewer", "operator"))):
    await get_project(body.project_id, session)
    e = Event(
        project_id=body.project_id, user_id=user.id, item_id=body.item_id, action=body.action,
        active_ms=body.active_ms, payload={"detail": body.detail} if body.detail else {},
    )
    session.add(e)
    await session.commit()
    await session.refresh(e)
    return event_out(e)


@router.post("/reviews", response_model=schemas.ReviewOut, status_code=201)
async def add_review(body: schemas.ReviewIn, session: AsyncSession = Depends(get_session), user: User = Depends(require("reviewer"))):
    item = await session.get(Item, body.item_id)
    if not item:
        raise HTTPException(404, "Image not found.")
    if body.verdict == "rejected" and not body.comment.strip():
        raise HTTPException(422, "Add a note so the annotator knows what to change.")
    r = Review(item_id=item.id, reviewer_id=user.id, verdict=ReviewVerdict(body.verdict), comment=body.comment.strip())
    item.status = ItemStatus.done if body.verdict == "approved" else ItemStatus.rejected
    session.add(r)
    await session.commit()
    await session.refresh(r)
    return review_out(r)


@router.post("/imports/{batch_id}/rollback", response_model=list[schemas.ImportOut])
async def rollback_import(batch_id: uuid.UUID, session: AsyncSession = Depends(get_session), user: User = Depends(require())):
    """
    Undo an import: every annotation from the batch gets a deleted version on top.
    History is kept, so the import itself stays visible in each annotation's history.
    """
    batch = await session.get(ImportBatch, batch_id)
    if not batch:
        raise HTTPException(404, "Import not found.")
    if batch.status != ImportStatus.committed:
        raise HTTPException(409, "Only a committed import can be undone.")
    ids = (await session.execute(select(func.distinct(Annotation.annotation_id)).where(Annotation.import_batch_id == batch_id))).scalars().all()
    touched: set[uuid.UUID] = set()
    for aid in ids:
        latest = (
            await session.execute(select(Annotation).where(Annotation.annotation_id == aid).order_by(Annotation.version.desc()).limit(1))
        ).scalar_one()
        touched.add(latest.item_id)
        if latest.is_deleted:
            continue
        session.add(
            Annotation(
                annotation_id=aid, version=latest.version + 1, item_id=latest.item_id, label_class_id=latest.label_class_id,
                shape_type=latest.shape_type, geometry=latest.geometry, attributes=latest.attributes, is_deleted=True,
                source=AnnotationSource.human, created_by=user.id,
            )
        )
    batch.status = ImportStatus.rolled_back
    batch.rolled_back_at = datetime.now(timezone.utc)
    await session.flush()
    for item_id in touched:
        live = await session.scalar(_live_count(item_id))
        item = await session.get(Item, item_id)
        item.status = ItemStatus.in_progress if live else ItemStatus.new
    session.add(Event(project_id=batch.project_id, user_id=user.id, action="import.rolled_back", payload={"detail": (batch.report or {}).get("fileName") or (batch.report or {}).get("file_name")}))
    await session.commit()
    return await import_list(batch.project_id, session)


def _live_count(item_id: uuid.UUID):
    latest = (
        select(Annotation.annotation_id, func.max(Annotation.version).label("v"))
        .where(Annotation.item_id == item_id)
        .group_by(Annotation.annotation_id)
        .subquery()
    )
    return (
        select(func.count())
        .select_from(Annotation)
        .join(latest, (Annotation.annotation_id == latest.c.annotation_id) & (Annotation.version == latest.c.v))
        .where(Annotation.is_deleted.is_(False))
    )
