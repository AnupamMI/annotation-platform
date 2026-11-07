"""Database rows -> API responses."""

from pathlib import PurePath

from app import schemas
from app.geo import from_db
from app.models import Annotation, Event, HierarchyLevel, Item, LabelClass, Node, Project, Review, User


def user_out(u: User) -> schemas.UserOut:
    parts = [p for p in u.display_name.replace(".", " ").split() if p]
    initials = "".join(p[0] for p in parts[-2:]).upper() if parts else "?"
    return schemas.UserOut(id=u.id, display_name=u.display_name, initials=initials, role=u.role.value)


def project_out(p: Project, levels: list[HierarchyLevel]) -> schemas.ProjectOut:
    return schemas.ProjectOut(
        id=p.id,
        name=p.name,
        description=p.description or "",
        domain_pack=p.domain_pack,
        created_at=p.created_at,
        levels=[
            schemas.LevelOut(id=l.id, depth=l.depth, name=l.name, is_split_level=l.is_split_level)
            for l in sorted(levels, key=lambda x: x.depth)
        ],
    )


def label_out(l: LabelClass) -> schemas.LabelOut:
    return schemas.LabelOut(
        id=l.id,
        project_id=l.project_id,
        name=l.name,
        color=l.color,
        allowed_shapes=list(l.allowed_shapes or []),
        must_be_inside_id=l.must_be_inside_id,
        hotkey=l.hotkey or "",
    )


def node_out(n: Node) -> schemas.NodeOut:
    return schemas.NodeOut(
        id=n.id, project_id=n.project_id, level_id=n.level_id, parent_id=n.parent_id,
        external_id=n.external_id, meta=n.meta or {},
    )


def item_out(i: Item) -> schemas.ItemOut:
    name = (i.meta or {}).get("display_name") or PurePath(i.original_filename).stem
    return schemas.ItemOut(
        id=i.id,
        project_id=i.project_id,
        node_id=i.node_id,
        name=name,
        url=f"/api/items/{i.id}/image",
        thumb_url=f"/api/items/{i.id}/thumb",
        dzi_url=f"/api/items/{i.id}/image.dzi" if i.is_tiled else None,
        mpp=((i.meta or {}).get("slide") or {}).get("mpp_x"),
        width=i.width,
        height=i.height,
        status=i.status.value,
        assignee_id=i.assignee_id,
        sha256=i.sha256,
    )


def version_out(a: Annotation) -> schemas.VersionOut:
    return schemas.VersionOut(
        row_id=a.id,
        annotation_id=a.annotation_id,
        version=a.version,
        item_id=a.item_id,
        label_id=a.label_class_id,
        shape=a.shape_type.value if a.shape_type.value != "mask" else "polygon",
        geometry=from_db(a.geometry, a.shape_type),
        is_deleted=a.is_deleted,
        source=a.source.value,
        created_by=a.created_by,
        created_at=a.created_at,
        import_batch_id=a.import_batch_id,
    )


def event_out(e: Event) -> schemas.EventOut:
    return schemas.EventOut(
        id=e.id, project_id=e.project_id, user_id=e.user_id, item_id=e.item_id, action=e.action,
        active_ms=e.active_ms, at=e.created_at, detail=(e.payload or {}).get("detail"),
    )


def review_out(r: Review) -> schemas.ReviewOut:
    return schemas.ReviewOut(
        id=r.id, item_id=r.item_id, reviewer_id=r.reviewer_id, verdict=r.verdict.value,
        comment=r.comment or "", at=r.created_at,
    )
