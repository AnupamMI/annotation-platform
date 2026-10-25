import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.api.convert import event_out, item_out, label_out, node_out, project_out, review_out, user_out, version_out
from app.api.deps import get_project, require
from app.db import get_session
from app.models import (
    Annotation,
    Event,
    HierarchyLevel,
    ImportBatch,
    ImportStatus,
    Item,
    LabelClass,
    Node,
    Project,
    Review,
    User,
)

router = APIRouter(tags=["projects"])


@router.get("/users", response_model=list[schemas.UserOut])
async def list_users(session: AsyncSession = Depends(get_session)):
    users = (await session.execute(select(User).where(User.is_active).order_by(User.created_at))).scalars()
    return [user_out(u) for u in users]


@router.get("/projects", response_model=list[schemas.ProjectOut])
async def list_projects(session: AsyncSession = Depends(get_session)):
    projects = (await session.execute(select(Project).order_by(Project.created_at))).scalars().all()
    levels = (await session.execute(select(HierarchyLevel))).scalars().all()
    by_project: dict[uuid.UUID, list[HierarchyLevel]] = {}
    for l in levels:
        by_project.setdefault(l.project_id, []).append(l)
    return [project_out(p, by_project.get(p.id, [])) for p in projects]


@router.post("/projects", response_model=schemas.SetupOut, status_code=201)
async def create_project(
    body: schemas.ProjectCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require()),
):
    if await session.scalar(select(func.count()).select_from(Project).where(Project.name == body.name)):
        raise HTTPException(409, f'A project called "{body.name}" already exists.')
    if sum(l.is_split_level for l in body.levels) > 1:
        raise HTTPException(422, "Only one level can be the split level.")
    project = Project(name=body.name, description=body.description, domain_pack=body.domain_pack, settings={})
    session.add(project)
    await session.flush()
    levels = [
        HierarchyLevel(project_id=project.id, depth=i, name=l.name, is_split_level=l.is_split_level, metadata_schema={})
        for i, l in enumerate(body.levels)
    ]
    session.add_all(levels)
    labels = [
        LabelClass(
            project_id=project.id, name=l.name, color=l.color, allowed_shapes=list(l.allowed_shapes),
            attributes_schema={}, hotkey=l.hotkey or (str(i + 1) if i < 9 else None), sort_order=i,
        )
        for i, l in enumerate(body.labels)
    ]
    session.add_all(labels)
    await session.flush()
    by_name = {l.name: l for l in labels}
    for spec, label in zip(body.labels, labels):
        if spec.must_be_inside_name:
            parent = by_name.get(spec.must_be_inside_name)
            if not parent:
                raise HTTPException(422, f'"{spec.name}" must be inside an unknown label "{spec.must_be_inside_name}".')
            label.must_be_inside_id = parent.id
    session.add(Event(project_id=project.id, user_id=user.id, action="project.created", payload={}))
    await session.commit()
    await session.refresh(project)
    return schemas.SetupOut(project=project_out(project, levels), labels=[label_out(l) for l in labels])


@router.put("/projects/{project_id}/setup", response_model=schemas.SetupOut)
async def save_setup(
    project_id: uuid.UUID,
    body: schemas.SetupIn,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require()),
):
    project = await get_project(project_id, session)
    levels = {l.id: l for l in (await session.execute(select(HierarchyLevel).where(HierarchyLevel.project_id == project_id))).scalars()}
    if sum(l.is_split_level for l in body.levels) > 1:
        raise HTTPException(422, "Only one level can be the split level.")
    for l in body.levels:
        if l.id not in levels:
            raise HTTPException(422, "Levels can be renamed here, not added or removed.")
        levels[l.id].name = l.name
        levels[l.id].is_split_level = l.is_split_level

    existing = {str(l.id): l for l in (await session.execute(select(LabelClass).where(LabelClass.project_id == project_id))).scalars()}
    id_map: dict[str, LabelClass] = {}
    for order, spec in enumerate(body.labels):
        label = existing.get(spec.id)
        if label is None:  # new label (temporary id from the client)
            label = LabelClass(project_id=project_id, attributes_schema={})
            session.add(label)
        label.name = spec.name
        label.color = spec.color
        label.allowed_shapes = list(spec.allowed_shapes)
        label.hotkey = spec.hotkey or None
        label.sort_order = order
        label.is_archived = False
        id_map[spec.id] = label
    await session.flush()
    for spec in body.labels:
        target = id_map.get(spec.must_be_inside_id) if spec.must_be_inside_id else None
        if spec.must_be_inside_id and not target:
            raise HTTPException(422, f'"{spec.name}" points to a label that does not exist.')
        if target is id_map[spec.id]:
            raise HTTPException(422, f'"{spec.name}" cannot be inside itself.')
        id_map[spec.id].must_be_inside_id = target.id if target else None
    # Labels removed in the UI are archived, never deleted: existing annotations keep their meaning.
    for key, label in existing.items():
        if key not in id_map:
            label.is_archived = True
    await session.commit()
    labels = [l for l in id_map.values()]
    return schemas.SetupOut(project=project_out(project, list(levels.values())), labels=[label_out(l) for l in labels])


@router.get("/projects/{project_id}/bundle", response_model=schemas.ProjectBundle)
async def project_bundle(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await get_project(project_id, session)
    nodes = (await session.execute(select(Node).where(Node.project_id == project_id).order_by(Node.external_id))).scalars().all()
    items = (await session.execute(select(Item).where(Item.project_id == project_id).order_by(Item.original_filename))).scalars().all()
    labels = (
        await session.execute(
            select(LabelClass).where(LabelClass.project_id == project_id, LabelClass.is_archived.is_(False)).order_by(LabelClass.sort_order)
        )
    ).scalars().all()
    item_ids = [i.id for i in items]
    annotations = (
        (await session.execute(select(Annotation).where(Annotation.item_id.in_(item_ids)).order_by(Annotation.created_at))).scalars().all()
        if item_ids else []
    )
    events = (
        await session.execute(select(Event).where(Event.project_id == project_id).order_by(Event.id.desc()).limit(2000))
    ).scalars().all()
    reviews = (
        (await session.execute(select(Review).where(Review.item_id.in_(item_ids)).order_by(Review.created_at))).scalars().all()
        if item_ids else []
    )
    return schemas.ProjectBundle(
        nodes=[node_out(n) for n in nodes],
        items=[item_out(i) for i in items],
        labels=[label_out(l) for l in labels],
        annotations=[version_out(a) for a in annotations],
        events=[event_out(e) for e in reversed(events)],
        reviews=[review_out(r) for r in reviews],
        imports=await import_list(project_id, session),
    )


async def import_list(project_id: uuid.UUID, session: AsyncSession) -> list[schemas.ImportOut]:
    batches = (
        await session.execute(
            select(ImportBatch)
            .where(ImportBatch.project_id == project_id, ImportBatch.status.notin_([ImportStatus.cancelled, ImportStatus.inspecting, ImportStatus.uploaded]))
            .order_by(ImportBatch.created_at.desc())
        )
    ).scalars().all()
    out = []
    for b in batches:
        if b.status == ImportStatus.awaiting_approval:
            t = (b.report or {}).get("totals", {})
            out.append(schemas.ImportOut(
                id=b.id, project_id=b.project_id, file_name=(b.report or {}).get("fileName", "upload"), format=b.detected_format or "unknown",
                status="awaiting_approval", uploaded_by=b.uploaded_by, created_at=b.created_at, annotations=t.get("annotations", 0), images=t.get("matched", 0),
            ))
            continue
        counts = (
            await session.execute(
                select(func.count(func.distinct(Annotation.annotation_id)), func.count(func.distinct(Annotation.item_id))).where(
                    Annotation.import_batch_id == b.id
                )
            )
        ).one()
        out.append(
            schemas.ImportOut(
                id=b.id, project_id=b.project_id, file_name=(b.report or {}).get("fileName") or (b.report or {}).get("file_name") or b.original_key.split("/")[-1],
                format=b.detected_format or "unknown",
                status={"committed": "committed", "rolled_back": "rolled_back", "failed": "failed"}.get(b.status.value, "awaiting_approval"),
                uploaded_by=b.uploaded_by, created_at=b.created_at, annotations=counts[0], images=counts[1],
            )
        )
    return out
