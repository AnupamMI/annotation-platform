"""
Load the synthetic sample images into a real demo project.

    cd backend
    python -m app.seed                      # demo users get a random password, printed once
    python -m app.seed --reset-passwords    # new password for the demo users (e.g. if you lost it)
    DEMO_PASSWORD=... python -m app.seed    # choose the password yourself

Creates (once) a small team, the "Cervical cytology, LBC set A (demo)" project with
patient / slide / field-of-view levels and labels, uploads the 12 sample images from
frontend/public/samples, and adds the generator's outlines to some of them as a
committed import, so review, history and undo-import all have something to show,
and a placeholder guideline that Ask can search.
Running it again does nothing if the demo project already exists.
"""

import asyncio
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from app.ai import guidelines
from app.auth import hash_password
from app.services import images
from app.db import SessionLocal
from app.geo import to_db
from app.migrate import upgrade_to_head
from app.models import (
    Annotation,
    AnnotationSource,
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
from app.models.docs import Document
from app.storage import get_storage

SAMPLES = Path(__file__).resolve().parents[2] / "frontend" / "public" / "samples"
PROJECT_NAME = "Cervical cytology, LBC set A (demo)"

TEAM = [
    ("anupam@example.com", "Anupam Mishra", Role.admin),
    ("riya@example.com", "Riya Sharma", Role.annotator),
    ("aman@example.com", "Aman Verma", Role.annotator),
    ("mehta@example.com", "Dr. K. Mehta", Role.reviewer),
]
LAYOUT = [("P-0101", [("S-0101-A", [1, 2, 3, 4])]), ("P-0102", [("S-0102-A", [5, 6, 7]), ("S-0102-B", [8, 9])]), ("P-0103", [("S-0103-A", [10, 11, 12])])]
IMPORTED = {1, 2, 3, 4, 5, 6}  # images that receive the generator outlines as an import
STATUS = {1: "done", 2: "done", 3: "done", 4: "in_review", 5: "in_review", 6: "in_review"}


async def main() -> None:
    if not (SAMPLES / "samples.json").exists():
        raise SystemExit(f"Sample images not found in {SAMPLES}. Run: cd frontend && python scripts/make_samples.py")
    await asyncio.to_thread(upgrade_to_head)

    password = os.environ.get("DEMO_PASSWORD") or secrets.token_urlsafe(9)
    async with SessionLocal() as s:
        if (await s.execute(select(Project).where(Project.name == PROJECT_NAME))).scalar_one_or_none():
            if "--reset-passwords" in sys.argv:
                for email, _, _ in TEAM:
                    u = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
                    if u:
                        u.password_hash = hash_password(password)
                await s.commit()
                print_logins(password)
            else:
                print("Demo project already exists. Nothing to do. (Use --reset-passwords for new demo passwords.)")
            return

        users = {}
        for email, name, role in TEAM:
            u = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if not u:
                u = User(email=email, display_name=name, role=role, password_hash=hash_password(password))
                s.add(u)
            users[name] = u
        await s.flush()
        admin = users["Anupam Mishra"]

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

        batch = ImportBatch(project_id=p.id, uploaded_by=admin.id, detected_format="cvat_xml", original_key="seed/cvat_export_set_A.zip",
                            status=ImportStatus.committed, report={"file_name": "cvat_export_set_A.zip"}, label_mapping={}, approved_by=admin.id)
        s.add(batch)
        await s.flush()

        meta = json.loads((SAMPLES / "samples.json").read_text())
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
                    info = images.analyze((SAMPLES / sample["file"]).read_bytes())
                    item_id = uuid.uuid4()
                    fields = images.store(storage, p.id, item_id, info)
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
                                                 import_batch_id=batch.id, created_by=admin.id))
        s.add(Event(project_id=p.id, user_id=admin.id, action="import.committed", payload={"detail": "cvat_export_set_A.zip"}))
        guide = (Path(__file__).parent / "sample_guideline.md").read_bytes()
        doc = Document(project_id=p.id, title="Sample guideline v0.1 (placeholder)", file_name="sample_guideline.md",
                       storage_key=f"projects/{p.id}/documents/sample_guideline.md", uploaded_by=admin.id)
        s.add(doc)
        await s.flush()
        await guidelines.add(s, doc, guide)
        storage.put(doc.storage_key, guide, "text/markdown")
        await s.commit()
        print(f"Created demo project with {sum(len(f) for _, sl in LAYOUT for _, f in sl)} images.")
        print_logins(password)


def print_logins(password: str) -> None:
    print("\nDemo sign-ins (synthetic data only; change these before real use):")
    for email, name, role in TEAM:
        print(f"  {email:<22} {role.value:<10} {name}")
    print(f"  password for all four: {password}\n")


if __name__ == "__main__":
    asyncio.run(main())
