"""
The demo project: synthetic sample images in a small, realistic project.

    cd backend
    python -m app.seed                      # create it once; demo users get a random password, printed once
    python -m app.seed --reset              # delete the demo project and build it fresh (new demo password)
    python -m app.seed --reset-passwords    # new password for the demo users (e.g. if you lost it)
    DEMO_PASSWORD=... python -m app.seed    # choose the password yourself

On a deployed site an admin does the same from the app (Projects → Demo project), which
calls reset_demo() below. There the demo team is only annotators and a reviewer, so a
demo login can be shared without giving anyone admin rights.

The project ("Cervical cytology, LBC set A (demo)") has patient / slide / field-of-view
levels and labels, the 12 sample images from frontend/public/samples, the generator's
outlines on some of them as a committed import (so review, history and undo-import all
have something to show), and a placeholder guideline that Ask can search.
"""

import asyncio
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import guidelines
from app.auth import hash_password
from app.config import get_settings
from app.db import SessionLocal
from app.geo import to_db
from app.migrate import upgrade_to_head
from app.models import (
    Annotation,
    AnnotationSource,
    AuthSession,
    Event,
    HierarchyLevel,
    ImportBatch,
    ImportStatus,
    Item,
    ItemStatus,
    LabelClass,
    Node,
    Project,
    Role,
    ShapeType,
    User,
)
from app.models.dataset import Dataset
from app.models.docs import Document
from app.services import images
from app.storage import get_storage

PROJECT_NAME = "Cervical cytology, LBC set A (demo)"

# Shareable demo accounts: never admins.
DEMO_TEAM = [
    ("riya@example.com", "Riya Sharma", Role.annotator),
    ("aman@example.com", "Aman Verma", Role.annotator),
    ("mehta@example.com", "Dr. K. Mehta", Role.reviewer),
]
# Local development only (python -m app.seed): a demo admin as well.
LOCAL_ADMIN = ("anupam@example.com", "Anupam Mishra", Role.admin)

LAYOUT = [("P-0101", [("S-0101-A", [1, 2, 3, 4])]), ("P-0102", [("S-0102-A", [5, 6, 7]), ("S-0102-B", [8, 9])]), ("P-0103", [("S-0103-A", [10, 11, 12])])]
IMPORTED = {1, 2, 3, 4, 5, 6}  # images that receive the generator outlines as an import
STATUS = {1: "done", 2: "done", 3: "done", 4: "in_review", 5: "in_review", 6: "in_review"}


def samples_dir() -> Path:
    """frontend/public/samples in the repository; the built frontend's copy in the Docker image."""
    here = Path(__file__).resolve().parents[2] / "frontend" / "public" / "samples"
    built = Path(get_settings().static_dir or "/nonexistent") / "samples"
    for d in (here, built):
        if (d / "samples.json").exists():
            return d
    raise RuntimeError(f"Sample images not found in {here} or {built}. Run: cd frontend && python scripts/make_samples.py")


async def find_demo(s: AsyncSession) -> Project | None:
    return (await s.execute(select(Project).where(Project.name == PROJECT_NAME))).scalar_one_or_none()


async def set_demo_users(s: AsyncSession, password: str, team: list[tuple[str, str, Role]]) -> dict[str, User]:
    """Creates the demo accounts or resets them (role, password, active), and signs them out everywhere."""
    users = {}
    for email, name, role in team:
        u = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if not u:
            u = User(email=email, display_name=name)
            s.add(u)
        u.role, u.is_active, u.password_hash = role, True, hash_password(password)
        users[name] = u
    await s.flush()
    await s.execute(delete(AuthSession).where(AuthSession.user_id.in_([u.id for u in users.values()])))
    return users


async def delete_demo(s: AsyncSession, project: Project) -> None:
    """Removes the demo project with everything in it, files included."""
    pid = project.id
    # rows that refuse to be deleted by cascade (datasets pin items and annotation versions) go first
    await s.execute(delete(Dataset).where(Dataset.project_id == pid))
    await s.execute(delete(Annotation).where(Annotation.item_id.in_(select(Item.id).where(Item.project_id == pid))))
    await s.execute(delete(Project).where(Project.id == pid))
    await s.flush()
    await asyncio.to_thread(get_storage().delete_prefix, f"projects/{pid}/")


async def build_demo(s: AsyncSession, owner: User, users: dict[str, User]) -> Project:
    """Creates the demo project. `owner` uploads and approves the seed import; `users` are the demo team."""
    samples = samples_dir()
    p = Project(name=PROJECT_NAME, description="Synthetic liquid-based cytology fields of view for trying the platform.", domain_pack="cytology", settings={})
    s.add(p)
    await s.flush()
    levels = [HierarchyLevel(project_id=p.id, depth=i, name=n, is_split_level=i == 0, metadata_schema={}) for i, n in enumerate(["Patient", "Slide", "Field of view"])]
    s.add_all(levels)
    specs = [("Squamous cell", "#3fb59a", ["polygon", "bbox"]), ("Nucleus", "#7b80e0", ["polygon"]), ("Atypical nucleus", "#e47fa2", ["polygon"]),
             ("Inflammatory cell", "#e9a13b", ["point", "bbox"]), ("Artifact", "#9aa5b3", ["bbox", "polygon"])]
    labels = [LabelClass(project_id=p.id, name=n, color=c, allowed_shapes=sh, attributes_schema={}, hotkey=str(i + 1), sort_order=i) for i, (n, c, sh) in enumerate(specs)]
    s.add_all(labels)
    await s.flush()
    labels[1].must_be_inside_id = labels[0].id
    labels[2].must_be_inside_id = labels[0].id

    batch = ImportBatch(project_id=p.id, uploaded_by=owner.id, detected_format="cvat_xml", original_key="seed/cvat_export_set_A.zip",
                        status=ImportStatus.committed, report={"file_name": "cvat_export_set_A.zip"}, label_mapping={}, approved_by=owner.id)
    s.add(batch)
    await s.flush()

    meta = json.loads((samples / "samples.json").read_text())
    storage = get_storage()
    annotators = [users["Riya Sharma"], users["Aman Verma"]]
    for patient, slides in LAYOUT:
        pn = Node(project_id=p.id, level_id=levels[0].id, external_id=patient, meta={})
        s.add(pn)
        await s.flush()
        for slide, fovs in slides:
            sn = Node(project_id=p.id, level_id=levels[1].id, parent_id=pn.id, external_id=slide, meta={"stain": "Pap", "scanner": "Motic EasyScan"})
            s.add(sn)
            await s.flush()
            for f in fovs:
                sample = meta[f - 1]
                info = await asyncio.to_thread(images.analyze, (samples / sample["file"]).read_bytes())
                item_id = uuid.uuid4()
                fields = await asyncio.to_thread(images.store, storage, p.id, item_id, info)
                fields.pop("tile_count", None)
                item = Item(id=item_id, project_id=p.id, node_id=sn.id, original_filename=f"FOV {f:02d}.jpg",
                            status=ItemStatus(STATUS.get(f, "new")), assignee_id=annotators[f % 2].id, meta={"display_name": f"FOV {f:02d}"}, **fields)
                s.add(item)
                await s.flush()
                if f in IMPORTED:
                    for c in sample["cells"]:
                        for ring, label in ((c["cell"], labels[0]), (c["nucleus"], labels[2] if c["abnormal"] else labels[1])):
                            closed = ring + [ring[0]] if ring[0] != ring[-1] else ring
                            s.add(Annotation(annotation_id=uuid.uuid4(), version=1, item_id=item.id, label_class_id=label.id, shape_type=ShapeType.polygon,
                                             geometry=to_db({"kind": "region", "polygons": [[closed]]}), attributes={}, source=AnnotationSource.import_,
                                             import_batch_id=batch.id, created_by=owner.id))
    s.add(Event(project_id=p.id, user_id=owner.id, action="import.committed", payload={"detail": "cvat_export_set_A.zip"}))
    guide = (Path(__file__).parent / "sample_guideline.md").read_bytes()
    doc = Document(project_id=p.id, title="Sample guideline v0.1 (placeholder)", file_name="sample_guideline.md",
                   storage_key=f"projects/{p.id}/documents/sample_guideline.md", uploaded_by=owner.id)
    s.add(doc)
    await s.flush()
    await guidelines.add(s, doc, guide)
    await asyncio.to_thread(storage.put, doc.storage_key, guide, "text/markdown")
    return p


async def reset_demo(s: AsyncSession, owner: User) -> tuple[Project, str]:
    """For the app: rebuild the demo project fresh, owned by this admin, with a new shared demo password.
    Commits. Old demo passwords stop working and demo users are signed out."""
    samples_dir()  # fail before deleting anything if the images aren't there
    if owner.email.lower() in {e for e, _, _ in DEMO_TEAM}:
        raise ValueError("Your own account uses a demo email, so a reset would demote it. Sign in with another admin account.")
    if existing := await find_demo(s):
        await delete_demo(s, existing)
    password = secrets.token_urlsafe(9)
    users = await set_demo_users(s, password, DEMO_TEAM)
    project = await build_demo(s, owner, users)
    await s.commit()
    return project, password


# ---------------- command line (local development) ----------------
async def main() -> None:
    try:
        samples_dir()
    except RuntimeError as exc:
        raise SystemExit(str(exc))
    await asyncio.to_thread(upgrade_to_head)
    team = [LOCAL_ADMIN, *DEMO_TEAM]
    password = os.environ.get("DEMO_PASSWORD") or secrets.token_urlsafe(9)
    async with SessionLocal() as s:
        existing = await find_demo(s)
        if existing and "--reset" not in sys.argv:
            if "--reset-passwords" in sys.argv:
                await set_demo_users(s, password, team)
                await s.commit()
                print_logins(team, password)
            else:
                print("Demo project already exists. Nothing to do. (--reset to rebuild it, --reset-passwords for new demo passwords.)")
            return
        if existing:
            await delete_demo(s, existing)
        users = await set_demo_users(s, password, team)
        project = await build_demo(s, users[LOCAL_ADMIN[1]], users)
        await s.commit()
        print(f"{'Rebuilt' if existing else 'Created'} the demo project with {sum(len(f) for _, sl in LAYOUT for _, f in sl)} images ({project.id}).")
        print_logins(team, password)


def print_logins(team: list[tuple[str, str, Role]], password: str) -> None:
    print("\nDemo sign-ins (synthetic data only; change these before real use):")
    for email, name, role in team:
        print(f"  {email:<22} {role.value:<10} {name}")
    print(f"  password for all of them: {password}\n")


if __name__ == "__main__":
    asyncio.run(main())
