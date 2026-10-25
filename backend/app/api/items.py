import json
import re
import uuid
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app import schemas
from app.api.convert import item_out, node_out
from app.api.deps import get_project, require
from app.config import get_settings
from app.db import get_session
from app.models import Event, HierarchyLevel, Item, ItemStatus, Node, User
from app.services import images, tiling
from app.storage import get_storage

router = APIRouter(tags=["images"])

# Kept for older imports of these names; the logic lives in app.services.images.
dhash = images.dhash


def process_image(data: bytes) -> dict:
    return images.analyze(data)


async def ensure_path(project_id: uuid.UUID, path: list[str], session: AsyncSession) -> tuple[Node | None, list[Node]]:
    """Find or create the nodes for e.g. ["P-0104", "S-0104-A"]. Returns (leaf, created)."""
    levels = (
        await session.execute(select(HierarchyLevel).where(HierarchyLevel.project_id == project_id).order_by(HierarchyLevel.depth))
    ).scalars().all()
    container_levels = levels[:-1]  # the last level is the image itself
    if len(path) != len(container_levels):
        names = ", ".join(l.name for l in container_levels)
        raise HTTPException(422, f"Give one ID for each of: {names}.")
    parent: Node | None = None
    created: list[Node] = []
    for level, ext in zip(container_levels, path):
        ext = ext.strip()
        if not ext:
            raise HTTPException(422, f"{level.name} ID is empty.")
        node = (
            await session.execute(
                select(Node).where(Node.project_id == project_id, Node.level_id == level.id, Node.external_id == ext)
            )
        ).scalar_one_or_none()
        if node is None:
            node = Node(project_id=project_id, level_id=level.id, parent_id=parent.id if parent else None, external_id=ext, meta={})
            session.add(node)
            await session.flush()
            created.append(node)
        elif parent and node.parent_id != parent.id:
            raise HTTPException(422, f"{level.name} {ext} already belongs to a different {container_levels[level.depth - 1].name.lower()}.")
        parent = node
    return parent, created


@router.post("/projects/{project_id}/items", response_model=schemas.UploadResult, status_code=201)
async def upload_images(
    project_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    path: str = Form("[]", description='JSON list of IDs for the levels above the image, e.g. ["P-0104","S-0104-A"]'),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require("operator")),
):
    await get_project(project_id, session)
    try:
        path_ids = json.loads(path)
        assert isinstance(path_ids, list) and all(isinstance(x, str) for x in path_ids)
    except Exception:
        raise HTTPException(422, "path must be a JSON list of strings.")
    leaf, new_nodes = await ensure_path(project_id, path_ids, session)
    storage = get_storage()
    limit = get_settings().max_upload_mb * 1024 * 1024
    created: list[Item] = []
    skipped: list[dict] = []
    for f in files:
        name = PurePath(f.filename or "image").name
        data = await f.read()
        if len(data) > limit:
            skipped.append({"file": name, "reason": f"larger than {get_settings().max_upload_mb} MB"})
            continue
        try:
            info = await run_in_threadpool(images.analyze, data)
        except ValueError:
            skipped.append({"file": name, "reason": "not a readable image"})
            continue
        dup = (await session.execute(select(Item.original_filename).where(Item.project_id == project_id, Item.sha256 == info["sha256"]))).scalar_one_or_none()
        if dup:
            skipped.append({"file": name, "reason": f"same image already uploaded as {dup}"})
            continue
        item_id = uuid.uuid4()
        fields = await run_in_threadpool(images.store, storage, project_id, item_id, info)
        fields.pop("tile_count", None)
        item = Item(id=item_id, project_id=project_id, node_id=leaf.id if leaf else None, original_filename=name,
                    meta={"source_format": info["format"]}, **fields)
        session.add(item)
        created.append(item)
    if created:
        session.add(Event(project_id=project_id, user_id=user.id, action="items.uploaded", payload={"detail": f"{len(created)} images"}))
    await session.commit()
    return schemas.UploadResult(created=[item_out(i) for i in created], nodes=[node_out(n) for n in new_nodes], skipped=skipped)


async def _blob(item_id: uuid.UUID, which: str, session: AsyncSession) -> Response:
    item = await session.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Image not found.")
    key = item.storage_key if which == "image" else (item.thumb_key or item.storage_key)
    try:
        data = await run_in_threadpool(get_storage().get, key)
    except FileNotFoundError:
        raise HTTPException(404, "Image file is missing from storage.")
    media = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "tif": "image/tiff"}.get(key.rsplit(".", 1)[-1], "application/octet-stream")
    return Response(data, media_type=media, headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/items/{item_id}/image")
async def item_image(item_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await _blob(item_id, "image", session)


@router.get("/items/{item_id}/thumb")
async def item_thumb(item_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await _blob(item_id, "thumb", session)


@router.get("/items/{item_id}/image.dzi")
async def item_dzi(item_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    item = await session.get(Item, item_id)
    if not item or not item.is_tiled:
        raise HTTPException(404, "This image has no tiles.")
    return Response(tiling.dzi_xml(item.width, item.height, item.tile_size, item.tile_format), media_type="application/xml")


TILE_NAME = re.compile(r"^(\d+)_(\d+)\.(jpeg|png)$")


@router.get("/items/{item_id}/image_files/{level}/{name}")
async def item_tile(item_id: uuid.UUID, level: int, name: str, session: AsyncSession = Depends(get_session)):
    m = TILE_NAME.match(name)
    item = await session.get(Item, item_id)
    if not m or not item or not item.is_tiled or not 0 <= level <= 40:
        raise HTTPException(404, "Tile not found.")
    try:
        data = await run_in_threadpool(get_storage().get, f"{item.tiles_prefix}/{level}/{m[1]}_{m[2]}.{m[3]}")
    except FileNotFoundError:
        raise HTTPException(404, "Tile not found.")
    return Response(data, media_type=f"image/{m[3]}", headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.patch("/items/{item_id}", response_model=schemas.ItemOut)
async def update_item(
    item_id: uuid.UUID,
    body: schemas.ItemPatch,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require("annotator", "reviewer")),
):
    item = await session.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Image not found.")
    if "assignee_id" in body.model_fields_set and user.role.value not in ("admin", "reviewer"):
        raise HTTPException(403, "Only admins and reviewers can assign work.")
    if body.status in ("done", "rejected") and user.role.value not in ("admin", "reviewer"):
        raise HTTPException(403, "Only admins and reviewers can approve or reject.")
    if "status" in body.model_fields_set and body.status:
        item.status = ItemStatus(body.status)
    if "assignee_id" in body.model_fields_set:
        if body.assignee_id and not await session.get(User, body.assignee_id):
            raise HTTPException(422, "Unknown user.")
        item.assignee_id = body.assignee_id
    await session.commit()
    return item_out(item)


@router.post("/items/assign", status_code=204)
async def assign_items(body: schemas.BulkAssign, session: AsyncSession = Depends(get_session), _user: User = Depends(require("reviewer"))):
    if body.assignee_id and not await session.get(User, body.assignee_id):
        raise HTTPException(422, "Unknown user.")
    await session.execute(update(Item).where(Item.id.in_(body.item_ids)).values(assignee_id=body.assignee_id))
    await session.commit()
