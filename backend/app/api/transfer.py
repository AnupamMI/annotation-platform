import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_project, require
from app.api.projects import import_list
from app.config import get_settings
from app.db import get_session
from app.formats import FormatError, read_upload
from app.models import Event, ImportBatch, ImportStatus, Item, ItemStatus, User
from app.schemas import ApiModel, ImportOut
from app.services import importer
from app.services.exporter import build_export
from app.storage import get_storage

router = APIRouter(tags=["import and export"])


class ImportPreview(ApiModel):
    id: uuid.UUID
    status: str
    report: dict


class CommitIn(ApiModel):
    label_mapping: dict[str, str]  # source label -> project label id, or "skip"
    conflict_policy: Literal["keep_both", "replace", "skip"] = "keep_both"
    unlinked: Literal["skip", "hold"] = "skip"


class CommitOut(ApiModel):
    result: dict
    imports: list[ImportOut]


class ExportIn(ApiModel):
    format: Literal["yolo", "coco", "cvat_xml", "label_studio"]
    scope: Literal["approved", "annotated", "all"] = "approved"
    include_images: bool = True
    item_ids: list[uuid.UUID] | None = Field(default=None, description="Export exactly these images instead of a scope.")


def _preview(b: ImportBatch) -> ImportPreview:
    return ImportPreview(id=b.id, status=b.status.value, report=b.report or {})


@router.post("/projects/{project_id}/imports", response_model=ImportPreview, status_code=201)
async def start_import(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require()),
):
    """Read the upload and report what an import would do. Nothing is written to annotations yet."""
    await get_project(project_id, session)
    data = await file.read()
    if len(data) > get_settings().max_import_mb * 1024 * 1024:
        raise HTTPException(413, f"The file is larger than {get_settings().max_import_mb} MB.")
    name = PurePosixPath(file.filename or "upload").name
    try:
        parsed = await run_in_threadpool(read_upload, name, data)
    except FormatError as exc:
        raise HTTPException(422, str(exc))
    batch = ImportBatch(project_id=project_id, uploaded_by=user.id, detected_format=parsed.format, original_key="", status=ImportStatus.inspecting, report={}, label_mapping={})
    session.add(batch)
    await session.flush()
    batch.original_key = f"projects/{project_id}/imports/{batch.id}/{name}"
    await run_in_threadpool(get_storage().put, batch.original_key, data, "application/octet-stream")
    analysis = await importer.analyze(session, project_id, parsed, hold_unlinked=False)
    batch.report = await importer.build_report(session, batch, analysis, name)
    batch.status = ImportStatus.awaiting_approval
    await session.commit()
    return _preview(batch)


@router.get("/imports/{batch_id}", response_model=ImportPreview)
async def get_import(batch_id: uuid.UUID, session: AsyncSession = Depends(get_session), _u: User = Depends(require())):
    b = await session.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "Import not found.")
    return _preview(b)


@router.post("/imports/{batch_id}/commit", response_model=CommitOut)
async def commit_import(batch_id: uuid.UUID, body: CommitIn, session: AsyncSession = Depends(get_session), user: User = Depends(require())):
    b = await session.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "Import not found.")
    if b.status != ImportStatus.awaiting_approval:
        raise HTTPException(409, f"This import is {b.status.value.replace('_', ' ')}, not waiting for approval.")
    try:
        data = await run_in_threadpool(get_storage().get, b.original_key)
    except FileNotFoundError:
        raise HTTPException(410, "The uploaded file is no longer in storage. Upload it again.")
    try:
        result = await importer.commit(session, b, data, body.label_mapping, body.conflict_policy, body.unlinked, user)
    except importer.ImportProblem as exc:
        await session.rollback()
        raise HTTPException(422, str(exc))
    return CommitOut(result=result, imports=await import_list(b.project_id, session))


@router.post("/imports/{batch_id}/cancel", response_model=list[ImportOut])
async def cancel_import(batch_id: uuid.UUID, session: AsyncSession = Depends(get_session), _u: User = Depends(require())):
    b = await session.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "Import not found.")
    if b.status == ImportStatus.awaiting_approval:
        b.status = ImportStatus.cancelled
        await session.commit()
    return await import_list(b.project_id, session)


@router.post("/projects/{project_id}/export")
async def export_project(project_id: uuid.UUID, body: ExportIn, session: AsyncSession = Depends(get_session), user: User = Depends(require("reviewer"))):
    """A zip in the chosen format, with images (optional) and a manifest of the exact annotation versions."""
    await get_project(project_id, session)
    q = select(Item).where(Item.project_id == project_id).order_by(Item.original_filename)
    if body.item_ids is not None:
        q = q.where(Item.id.in_(body.item_ids))
    elif body.scope == "approved":
        q = q.where(Item.status == ItemStatus.done)
    elif body.scope == "annotated":
        q = q.where(Item.status.in_([ItemStatus.annotated, ItemStatus.in_review, ItemStatus.done]))
    items = (await session.execute(q)).scalars().all()
    if not items:
        raise HTTPException(422, "No images match. Approve some images first, or export all images.")
    try:
        data, manifest = await build_export(session, project_id, list(items), body.format, body.include_images)
    except FormatError as exc:
        raise HTTPException(422, str(exc))
    session.add(Event(project_id=project_id, user_id=user.id, action="export.created", payload={"detail": f"{body.format}, {len(items)} images"}))
    await session.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    filename = f"export-{body.format}-{stamp}.zip"
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Notes": str(len(manifest["notes"])),
        },
    )

