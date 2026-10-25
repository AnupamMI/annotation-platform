"""
Agents: plan -> a person approves -> execute -> everything logged in agent_runs.

They use a fixed set of server-side tools (below). None of the tools can create,
edit or delete annotations: the only writes are a dataset snapshot (which records
existing annotation versions) and the event log.
"""

from __future__ import annotations

import random
import re
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import llm
from app.models import (
    AgentRun,
    AgentRunStatus,
    Dataset,
    DatasetAnnotation,
    DatasetItem,
    Event,
    HierarchyLevel,
    Item,
    ItemStatus,
    Node,
    Project,
    Split,
    User,
)
from app.services.exporter import build_export, current_rows
from app.storage import get_storage

FORMAT_WORDS = {"coco": "coco", "cvat": "cvat_xml", "label studio": "label_studio", "labelstudio": "label_studio", "yolo": "yolo"}
FORMAT_NAMES = {"yolo": "YOLO", "coco": "COCO JSON", "cvat_xml": "CVAT XML", "label_studio": "Label Studio JSON"}
SPLITS = ("train", "val", "test")


# ---------------- dataset builder ----------------
@dataclass
class DatasetSpec:
    name: str = ""
    statuses: list[str] = field(default_factory=lambda: ["done"])
    ratios: dict[str, float] = field(default_factory=lambda: {"train": 0.7, "val": 0.15, "test": 0.15})
    format: str = "yolo"
    include_images: bool = True
    seed: int = 42


def spec_from_rules(request: str) -> DatasetSpec:
    q = request.lower()
    spec = DatasetSpec()
    for word, fmt in FORMAT_WORDS.items():
        if word in q:
            spec.format = fmt
            break
    if m := re.search(r"(\d{1,2})\s*[/:-]\s*(\d{1,2})(?:\s*[/:-]\s*(\d{1,2}))?", q):
        nums = [int(x) for x in m.groups() if x]
        if 90 <= sum(nums) <= 110 or sum(nums) == 10:
            total = sum(nums)
            spec.ratios = {s: n / total for s, n in zip(SPLITS, nums)} | {s: 0.0 for s in SPLITS[len(nums):]}
    if re.search(r"in[ -]?review|not (yet )?approved|all annotated", q):
        spec.statuses = ["done", "in_review"]
    if re.search(r"without images|no images|labels only|annotations only", q):
        spec.include_images = False
    if m := re.search(r"(?:named|called)\s+[\"']?([\w .-]{2,60})", request, re.I):
        spec.name = m.group(1).strip(" .'\"")
    return spec


async def spec_from_llm(request: str) -> DatasetSpec | None:
    if not llm.configured():
        return None
    system = (
        "Turn a request for a training dataset into JSON with keys: name (string or empty), statuses (list from: done, in_review, annotated), "
        "ratios (object with train, val, test summing to 1), format (yolo | coco | cvat_xml | label_studio), include_images (bool). "
        "Use defaults when the request doesn't say: statuses [\"done\"], ratios 0.7/0.15/0.15, format yolo, include_images true. Reply with JSON only."
    )
    try:
        d = llm.extract_json(await llm.complete(system, request, max_tokens=300))
        spec = DatasetSpec()
        if isinstance(d.get("name"), str):
            spec.name = d["name"][:80]
        if isinstance(d.get("statuses"), list) and d["statuses"]:
            spec.statuses = [s for s in d["statuses"] if s in ("done", "in_review", "annotated")] or spec.statuses
        r = d.get("ratios") or {}
        if all(isinstance(r.get(k, 0), (int, float)) for k in SPLITS) and 0.99 <= sum(float(r.get(k, 0)) for k in SPLITS) <= 1.01:
            spec.ratios = {k: float(r.get(k, 0)) for k in SPLITS}
        if d.get("format") in FORMAT_NAMES:
            spec.format = d["format"]
        if isinstance(d.get("include_images"), bool):
            spec.include_images = d["include_images"]
        return spec
    except (llm.LLMUnavailable, ValueError, TypeError):
        return None


async def _split_groups(session: AsyncSession, project: Project, items: list[Item]) -> tuple[dict[str, list[Item]], str]:
    """Images grouped by their ancestor at the project's split level (e.g. patient)."""
    levels = (await session.execute(select(HierarchyLevel).where(HierarchyLevel.project_id == project.id))).scalars().all()
    split_level = next((l for l in levels if l.is_split_level), None)
    nodes = {n.id: n for n in (await session.execute(select(Node).where(Node.project_id == project.id))).scalars()}
    groups: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        node = nodes.get(it.node_id) if it.node_id else None
        key = None
        while node is not None:
            if split_level and node.level_id == split_level.id:
                key = node.external_id
                break
            node = nodes.get(node.parent_id) if node.parent_id else None
        groups[key or f"image:{it.id}"].append(it)
    return groups, (split_level.name if split_level else "image")


def assign_splits(groups: dict[str, list[Item]], ratios: dict[str, float], seed: int) -> dict[str, list[str]]:
    """Whole groups go to one split each, filling whichever split is furthest below its target."""
    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    keys.sort(key=lambda k: -len(groups[k]))  # big groups first; ties keep the seeded order
    total = sum(len(v) for v in groups.values())
    active = [s for s in SPLITS if ratios.get(s, 0) > 0]
    out: dict[str, list[str]] = {s: [] for s in SPLITS}
    count = {s: 0 for s in SPLITS}
    for k in keys:
        empty = [s for s in active if not out[s]]
        target = empty[0] if empty and len(keys) >= len(active) else max(active, key=lambda s: ratios[s] * total - count[s])
        out[target].append(k)
        count[target] += len(groups[k])
    return out


async def plan_dataset(session: AsyncSession, project: Project, user: User, request: str) -> AgentRun:
    t0 = time.monotonic()
    calls = []
    spec = await spec_from_llm(request)
    calls.append({"tool": "read_request", "args": {"via": "language model" if spec else "rules"}, "ms": _ms(t0)})
    spec = spec or spec_from_rules(request)
    if not spec.name:
        spec.name = f"{re.sub(r'[^a-z0-9]+', '-', project.name.lower()).strip('-')[:40]}-{datetime.now(timezone.utc):%Y%m%d-%H%M}"

    t1 = time.monotonic()
    items = (
        await session.execute(select(Item).where(Item.project_id == project.id, Item.status.in_([ItemStatus(s) for s in spec.statuses])))
    ).scalars().all()
    pending = (
        await session.execute(select(Item.id).where(Item.project_id == project.id, Item.status == ItemStatus.in_review))
    ).scalars().all()
    calls.append({"tool": "find_images", "args": {"statuses": spec.statuses}, "ms": _ms(t1), "summary": f"{len(items)} images"})

    t2 = time.monotonic()
    groups, level_name = await _split_groups(session, project, list(items))
    assignment = assign_splits(groups, spec.ratios, spec.seed)
    calls.append({"tool": "split_by_level", "args": {"level": level_name, "seed": spec.seed}, "ms": _ms(t2)})

    rows = await current_rows(session, [i.id for i in items])
    n_ann = sum(len(v) for v in rows.values())
    per_split = {s: sum(len(groups[k]) for k in assignment[s]) for s in SPLITS}
    split_text = ", ".join(
        f"{s} {per_split[s]} image{'s' if per_split[s] != 1 else ''} ({', '.join(sorted(assignment[s])[:6])}{'…' if len(assignment[s]) > 6 else ''})"
        for s in SPLITS if assignment[s]
    )
    warning = None
    empty = [s for s in SPLITS if spec.ratios.get(s, 0) > 0 and not assignment[s]]
    if not items:
        warning = "No images match. Approve some images first, or ask to include images in review."
    elif empty:
        warning = (f"Only {len(groups)} {level_name.lower()}{'s have' if len(groups) != 1 else ' has'} matching images, so the "
                   f"{' and '.join(empty)} split{'s' if len(empty) > 1 else ''} will be empty. Splits never share a {level_name.lower()}.")
    status_words = {"done": "approved", "in_review": "in review", "annotated": "annotated"}
    run = AgentRun(
        project_id=project.id, user_id=user.id, kind="dataset_builder", request=request, status=AgentRunStatus.awaiting_approval,
        plan={
            "summary": f"I'll build \"{spec.name}\" from {len(items)} {' and '.join(status_words[s] for s in spec.statuses)} images, "
                       f"keeping each {level_name.lower()} in one split so nothing leaks between train and test.",
            "steps": [
                {"text": "Select images", "detail": f"{len(items)} {' and '.join(status_words[s] for s in spec.statuses)}"
                 + (f"; {len(pending)} still in review are left out" if "in_review" not in spec.statuses and pending else "")},
                {"text": "Freeze the exact annotation versions", "detail": f"{n_ann} annotations, so the dataset can be rebuilt identically"},
                {"text": f"Split by {level_name.lower()}", "detail": split_text or "nothing to split"},
                {"text": f"Export as {FORMAT_NAMES[spec.format]}", "detail": "with images" if spec.include_images else "labels only"},
                {"text": "Write the manifest and lineage record"},
            ],
            "warning": warning,
            "spec": asdict(spec),
            "assignment": {s: [str(i.id) for k in assignment[s] for i in groups[k]] for s in SPLITS},
            "levelName": level_name,
        },
        tool_calls=calls, result={}, **_llm_fields(),
    )
    session.add(run)
    await session.commit()
    return run


async def run_dataset(session: AsyncSession, run: AgentRun, user: User) -> dict:
    spec = DatasetSpec(**run.plan["spec"])
    assignment: dict[str, list[str]] = run.plan["assignment"]
    split_of = {uuid.UUID(i): s for s, ids in assignment.items() for i in ids}
    if not split_of:
        raise AgentProblem("There are no images to put in the dataset.")
    items = (await session.execute(select(Item).where(Item.id.in_(split_of.keys())).order_by(Item.original_filename))).scalars().all()
    t0 = time.monotonic()
    rows = await current_rows(session, [i.id for i in items])
    level = await session.scalar(select(HierarchyLevel).where(HierarchyLevel.project_id == run.project_id, HierarchyLevel.is_split_level.is_(True)))
    ds = Dataset(project_id=run.project_id, name=spec.name, spec=run.plan["spec"], export_format=spec.format, created_by=user.id,
                 agent_run_id=run.id, split_level_id=level.id if level else None)
    session.add(ds)
    await session.flush()
    for it in items:
        session.add(DatasetItem(dataset_id=ds.id, item_id=it.id, split=Split(split_of[it.id])))
        for r in rows.get(it.id, []):
            session.add(DatasetAnnotation(dataset_id=ds.id, annotation_row_id=r.id))
    run.tool_calls = run.tool_calls + [{"tool": "freeze_versions", "args": {}, "ms": _ms(t0), "summary": f"{sum(len(v) for v in rows.values())} annotation rows"}]

    t1 = time.monotonic()
    data, manifest = await build_export(
        session, run.project_id, list(items), spec.format, spec.include_images, splits=split_of,
        extra_manifest={"dataset": {"id": str(ds.id), "name": spec.name, "agentRunId": str(run.id), "seed": spec.seed,
                                    "splitLevel": run.plan.get("levelName"), "approvedBy": str(user.id)}},
    )
    key = f"projects/{run.project_id}/datasets/{ds.id}/{_safe(spec.name)}-{spec.format}.zip"
    get_storage().put(key, data, "application/zip")
    ds.export_key = key
    run.tool_calls = run.tool_calls + [{"tool": "export_dataset", "args": {"format": spec.format}, "ms": _ms(t1), "summary": f"{len(data) // 1024} KB"}]
    counts = {s: sum(1 for v in split_of.values() if v == s) for s in SPLITS}
    session.add(Event(project_id=run.project_id, user_id=user.id, action="dataset.created", payload={"detail": spec.name}))
    return {
        "text": f"Dataset \"{spec.name}\" created: {len(items)} images ({', '.join(f'{s} {n}' for s, n in counts.items() if n)}), "
                f"{manifest['annotations']} annotations frozen at their current versions.",
        "datasetId": str(ds.id),
        "download": f"/api/datasets/{ds.id}/download",
        "notes": manifest["notes"],
    }


# ---------------- consistency checker ----------------
async def plan_consistency(session: AsyncSession, project: Project, user: User, request: str) -> AgentRun:
    n = await session.scalar(select(text("count(*)")).select_from(Item).where(Item.project_id == project.id))
    run = AgentRun(
        project_id=project.id, user_id=user.id, kind="consistency_checker", request=request, status=AgentRunStatus.awaiting_approval,
        plan={
            "summary": f"I'll check all {n} images against the project rules and list anything a person should look at. I won't change any annotation.",
            "steps": [
                {"text": "Check containment rules", "detail": "e.g. a nucleus must lie inside a cell"},
                {"text": "Look for near-duplicate shapes", "detail": "same label, overlapping by 90% or more"},
                {"text": "Look for tiny shapes", "detail": "under 4 px², usually stray clicks"},
                {"text": "Find approved or submitted images with no annotations"},
                {"text": "List what needs attention, with links"},
            ],
        },
        tool_calls=[], result={}, **_llm_fields(),
    )
    session.add(run)
    await session.commit()
    return run


CHECKS = {
    "containment": """
        WITH live AS (
          SELECT DISTINCT ON (a.annotation_id) a.* FROM annotations a JOIN items i ON i.id = a.item_id
          WHERE i.project_id = :p ORDER BY a.annotation_id, a.version DESC
        )
        SELECT a.item_id, count(*) AS n, min(l.name) AS label, min(pl.name) AS parent
        FROM live a JOIN label_classes l ON l.id = a.label_class_id JOIN label_classes pl ON pl.id = l.must_be_inside_id
        WHERE NOT a.is_deleted AND NOT EXISTS (
          SELECT 1 FROM live c WHERE NOT c.is_deleted AND c.item_id = a.item_id AND c.label_class_id = l.must_be_inside_id
            AND ST_Within(a.geometry, c.geometry))
        GROUP BY a.item_id""",
    "duplicates": """
        WITH live AS (
          SELECT DISTINCT ON (a.annotation_id) a.* FROM annotations a JOIN items i ON i.id = a.item_id
          WHERE i.project_id = :p ORDER BY a.annotation_id, a.version DESC
        )
        SELECT x.item_id, count(*) AS n
        FROM live x JOIN live y ON y.item_id = x.item_id AND y.label_class_id = x.label_class_id AND x.annotation_id < y.annotation_id
        WHERE NOT x.is_deleted AND NOT y.is_deleted AND x.shape_type::text <> 'point' AND y.shape_type::text <> 'point'
          AND ST_Intersects(x.geometry, y.geometry)
          AND ST_Area(ST_Intersection(x.geometry, y.geometry)) / NULLIF(ST_Area(ST_Union(x.geometry, y.geometry)), 0) >= 0.9
        GROUP BY x.item_id""",
    "tiny": """
        WITH live AS (
          SELECT DISTINCT ON (a.annotation_id) a.* FROM annotations a JOIN items i ON i.id = a.item_id
          WHERE i.project_id = :p ORDER BY a.annotation_id, a.version DESC
        )
        SELECT item_id, count(*) AS n FROM live
        WHERE NOT is_deleted AND shape_type::text <> 'point' AND ST_Area(geometry) < 4 GROUP BY item_id""",
    "empty": """
        SELECT i.id AS item_id, 1 AS n, i.status::text AS status FROM items i
        WHERE i.project_id = :p AND i.status::text IN ('done', 'in_review') AND NOT EXISTS (
          SELECT 1 FROM (SELECT DISTINCT ON (a.annotation_id) a.is_deleted FROM annotations a WHERE a.item_id = i.id
                         ORDER BY a.annotation_id, a.version DESC) c WHERE NOT c.is_deleted)""",
}


async def run_consistency(session: AsyncSession, run: AgentRun, user: User) -> dict:
    del user
    names = {i.id: (i.meta or {}).get("display_name") or re.sub(r"\.[^.]+$", "", i.original_filename)
             for i in (await session.execute(select(Item).where(Item.project_id == run.project_id))).scalars()}
    findings = []
    calls = []
    for check, sql in CHECKS.items():
        t0 = time.monotonic()
        rows = (await session.execute(text(sql), {"p": run.project_id})).mappings().all()
        calls.append({"tool": f"check_{check}", "args": {}, "ms": _ms(t0), "summary": f"{len(rows)} images"})
        for r in rows:
            n = r["n"]
            note = {
                "containment": lambda: f"{n} {r['label'].lower()} shape{'s are' if n > 1 else ' is'} not inside a {r['parent'].lower()}",
                "duplicates": lambda: f"{n} pair{'s' if n > 1 else ''} of near-duplicate shapes",
                "tiny": lambda: f"{n} tiny shape{'s' if n > 1 else ''} (probably stray clicks)",
                "empty": lambda: f"{'approved' if r['status'] == 'done' else 'submitted'} but has no annotations",
            }[check]()
            findings.append({"itemId": str(r["item_id"]), "imageName": names.get(r["item_id"], "?"), "kind": check, "note": note})
    run.tool_calls = run.tool_calls + calls
    findings.sort(key=lambda f: f["imageName"])
    return {
        "text": (f"Checked {len(names)} images. {len(findings)} thing{'s need' if len(findings) != 1 else ' needs'} a person to look. No annotations were changed."
                 if findings else f"Checked {len(names)} images. Everything passes. No annotations were changed."),
        "findings": findings,
    }


# ---------------- shared ----------------
class AgentProblem(ValueError):
    pass


PLANNERS = {"dataset_builder": plan_dataset, "consistency_checker": plan_consistency}
RUNNERS = {"dataset_builder": run_dataset, "consistency_checker": run_consistency}
APPROVERS = {"dataset_builder": ("admin", "reviewer"), "consistency_checker": ("admin", "reviewer", "annotator", "operator", "viewer")}


async def approve(session: AsyncSession, run: AgentRun, user: User) -> AgentRun:
    if run.status != AgentRunStatus.awaiting_approval:
        raise AgentProblem(f"This plan is {run.status.value.replace('_', ' ')}, not waiting for approval.")
    if user.role.value not in APPROVERS[run.kind]:
        raise PermissionError("Only admins and reviewers can approve building a dataset.")
    if run.user_id != user.id and user.role.value != "admin":
        raise PermissionError("Only the person who asked, or an admin, can approve this plan.")
    run.status = AgentRunStatus.running
    run.approved_by, run.approved_at = user.id, datetime.now(timezone.utc)
    await session.commit()
    try:
        run.result = await RUNNERS[run.kind](session, run, user)
        run.status = AgentRunStatus.completed
    except AgentProblem as exc:
        run.status, run.error = AgentRunStatus.failed, str(exc)
    except Exception as exc:  # recorded, and the run is marked failed rather than left "running"
        await session.rollback()
        run = await session.get(AgentRun, run.id)
        run.status, run.error = AgentRunStatus.failed, f"Something went wrong: {exc.__class__.__name__}"
    await session.commit()
    return run


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _llm_fields() -> dict:
    d = llm.describe()
    return {"llm_provider": d["provider"], "llm_model": d["model"]}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")[:60] or "dataset"
