"""
API tests, in the order a real session uses the app:
create project -> upload -> annotate (create/edit/delete/conflict) -> review -> assign -> events -> undo import.
"""

import json
import uuid

import pytest

from tests.conftest import png_bytes

pytestmark = pytest.mark.asyncio

CYTO = {
    "name": "Test cytology",
    "domainPack": "cytology",
    "levels": [{"name": "Patient", "isSplitLevel": True}, {"name": "Slide"}, {"name": "Field of view"}],
    "labels": [
        {"name": "Cell", "color": "#3fb59a", "allowedShapes": ["polygon", "bbox"]},
        {"name": "Nucleus", "color": "#7b80e0", "allowedShapes": ["polygon"], "mustBeInsideName": "Cell"},
    ],
}
state: dict = {}


def square(x, y, s):
    return {"kind": "region", "polygons": [[[[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]]]}


async def test_create_project(client):
    r = await client.post("/projects", json=CYTO)
    assert r.status_code == 201, r.text
    body = r.json()
    assert [l["name"] for l in body["project"]["levels"]] == ["Patient", "Slide", "Field of view"]
    labels = {l["name"]: l for l in body["labels"]}
    assert labels["Nucleus"]["mustBeInsideId"] == labels["Cell"]["id"]
    assert labels["Cell"]["hotkey"] == "1"
    state["project"] = body["project"]["id"]
    state["labels"] = labels
    dup = await client.post("/projects", json=CYTO)
    assert dup.status_code == 409


async def test_upload_creates_nodes_thumbs_and_skips_duplicates(client):
    files = [
        ("files", ("fov_a.png", png_bytes(color=(10, 20, 30)), "image/png")),
        ("files", ("fov_b.png", png_bytes(color=(40, 50, 60)), "image/png")),
        ("files", ("again.png", png_bytes(color=(10, 20, 30)), "image/png")),  # same bytes as fov_a
        ("files", ("notes.txt", b"not an image", "text/plain")),
    ]
    r = await client.post(f"/projects/{state['project']}/items", files=files, data={"path": json.dumps(["P-1", "S-1"])})
    assert r.status_code == 201, r.text
    body = r.json()
    assert [i["name"] for i in body["created"]] == ["fov_a", "fov_b"]
    assert {n["externalId"] for n in body["nodes"]} == {"P-1", "S-1"}
    reasons = {s["file"]: s["reason"] for s in body["skipped"]}
    assert "already uploaded" in reasons["again.png"] and "not a readable image" in reasons["notes.txt"]
    item = body["created"][0]
    assert (item["width"], item["height"]) == (400, 300)
    state["item"] = item["id"]
    img = await client.get(f"/items/{item['id']}/image")
    thumb = await client.get(f"/items/{item['id']}/thumb")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"


async def test_upload_needs_a_full_path(client):
    r = await client.post(f"/projects/{state['project']}/items", files=[("files", ("x.png", png_bytes(), "image/png"))], data={"path": '["P-1"]'})
    assert r.status_code == 422
    assert "Patient, Slide" in r.json()["detail"]


async def test_annotation_versions_are_append_only(client):
    aid = str(uuid.uuid4())
    base = {"annotationId": aid, "itemId": state["item"], "labelId": state["labels"]["Cell"]["id"], "shape": "polygon"}
    v1 = await client.post("/annotations/versions", json={**base, "version": 1, "geometry": square(10, 10, 100)})
    assert v1.status_code == 201, v1.text
    v2 = await client.post("/annotations/versions", json={**base, "version": 2, "geometry": square(10, 10, 120)})
    assert v2.status_code == 201
    # a stale client that still thinks the latest is v1 gets 409 plus the latest version
    stale = await client.post("/annotations/versions", json={**base, "version": 2, "geometry": square(0, 0, 50)})
    assert stale.status_code == 409
    assert stale.json()["latest"]["version"] == 2
    v3 = await client.post("/annotations/versions", json={**base, "version": 3, "isDeleted": True})
    assert v3.status_code == 201 and v3.json()["isDeleted"] is True
    assert v3.json()["geometry"] == v2.json()["geometry"]  # tombstone keeps the last shape
    rows = (await client.get(f"/items/{state['item']}/annotations")).json()
    assert [r["version"] for r in rows if r["annotationId"] == aid] == [1, 2, 3]
    assert rows[1]["geometry"]["polygons"][0][0][2] == [130.0, 130.0]


async def test_shape_and_geometry_rules(client):
    item, cell, nucleus = state["item"], state["labels"]["Cell"]["id"], state["labels"]["Nucleus"]["id"]
    box = {"annotationId": str(uuid.uuid4()), "version": 1, "itemId": item, "labelId": nucleus, "shape": "bbox",
           "geometry": {"kind": "bbox", "x": 1, "y": 1, "w": 10, "h": 10}}
    r = await client.post("/annotations/versions", json=box)
    assert r.status_code == 422 and "cannot be drawn as bbox" in r.json()["detail"]
    ok_box = {**box, "labelId": cell}
    r = await client.post("/annotations/versions", json=ok_box)
    assert r.status_code == 201 and r.json()["geometry"] == {"kind": "bbox", "x": 1.0, "y": 1.0, "w": 10.0, "h": 10.0}
    outside = {**ok_box, "annotationId": str(uuid.uuid4()), "geometry": {"kind": "bbox", "x": 390, "y": 10, "w": 50, "h": 10}}
    r = await client.post("/annotations/versions", json=outside)
    assert r.status_code == 422 and "outside the image" in r.json()["detail"]
    bowtie = {"annotationId": str(uuid.uuid4()), "version": 1, "itemId": item, "labelId": cell, "shape": "polygon",
              "geometry": {"kind": "region", "polygons": [[[[0, 0], [100, 100], [100, 0], [0, 100], [0, 0]]]]}}
    r = await client.post("/annotations/versions", json=bowtie)
    assert r.status_code == 201, r.text  # self-crossing outline is repaired, not rejected
    assert len(r.json()["geometry"]["polygons"]) == 2


async def test_review_sets_status_and_needs_a_note_to_reject(client):
    r = await client.post("/reviews", json={"itemId": state["item"], "verdict": "rejected", "comment": " "})
    assert r.status_code == 422
    r = await client.post("/reviews", json={"itemId": state["item"], "verdict": "rejected", "comment": "Split the two cells"})
    assert r.status_code == 201
    bundle = (await client.get(f"/projects/{state['project']}/bundle")).json()
    assert next(i for i in bundle["items"] if i["id"] == state["item"])["status"] == "rejected"
    assert bundle["reviews"][0]["comment"] == "Split the two cells"


async def test_assign_status_and_events(client, reviewer):
    users = (await client.get("/users")).json()
    rev = next(u for u in users if u["role"] == "reviewer")
    assert rev["initials"] == "TR"
    r = await client.post("/items/assign", json={"itemIds": [state["item"]], "assigneeId": rev["id"]})
    assert r.status_code == 204
    r = await client.patch(f"/items/{state['item']}", json={"status": "in_review"})
    assert r.json()["assigneeId"] == rev["id"] and r.json()["status"] == "in_review"
    r = await reviewer.post("/events", json={"projectId": state["project"], "itemId": state["item"], "action": "item.submitted", "activeMs": 61000})
    assert r.status_code == 201 and r.json()["userId"] == rev["id"], "events are recorded for the signed-in user"


async def test_setup_renames_adds_and_archives(client):
    pid = state["project"]
    bundle = (await client.get(f"/projects/{pid}/bundle")).json()
    project = next(p for p in (await client.get("/projects")).json() if p["id"] == pid)
    levels = [{"id": l["id"], "name": l["name"] + "s" if l["depth"] == 0 else l["name"], "isSplitLevel": l["isSplitLevel"]} for l in project["levels"]]
    cell = state["labels"]["Cell"]
    labels = [
        {"id": cell["id"], "name": "Squamous cell", "color": cell["color"], "allowedShapes": ["polygon"], "hotkey": "1"},
        {"id": "tmp-1", "name": "Debris", "color": "#9aa5b3", "allowedShapes": ["bbox"], "mustBeInsideId": None, "hotkey": "2"},
        {"id": "tmp-2", "name": "Inner", "color": "#e47fa2", "allowedShapes": ["polygon"], "mustBeInsideId": "tmp-1", "hotkey": "3"},
    ]  # Nucleus is left out -> archived, not deleted
    r = await client.put(f"/projects/{pid}/setup", json={"levels": levels, "labels": labels})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["project"]["levels"][0]["name"] == "Patients"
    names = {l["name"]: l for l in out["labels"]}
    assert names["Inner"]["mustBeInsideId"] == names["Debris"]["id"]
    after = (await client.get(f"/projects/{pid}/bundle")).json()
    assert {l["name"] for l in after["labels"]} == {"Squamous cell", "Debris", "Inner"}
    assert len(after["annotations"]) == len(bundle["annotations"])  # nothing lost


async def test_undo_import_marks_annotations_deleted(client):
    """Import rollback appends deleted versions; nothing is removed from history."""
    from app.db import SessionLocal
    from app.geo import to_db
    from app.models import Annotation, AnnotationSource, ImportBatch, ImportStatus, ShapeType

    async with SessionLocal() as s:
        batch = ImportBatch(project_id=uuid.UUID(state["project"]), original_key="t/x.zip", status=ImportStatus.committed,
                            detected_format="yolo", report={"file_name": "x.zip"}, label_mapping={})
        s.add(batch)
        await s.flush()
        for k in range(3):
            s.add(Annotation(annotation_id=uuid.uuid4(), version=1, item_id=uuid.UUID(state["item"]), label_class_id=uuid.UUID(state["labels"]["Cell"]["id"]),
                             shape_type=ShapeType.polygon, geometry=to_db(square(200 + k * 20, 200, 10)), attributes={},
                             source=AnnotationSource.import_, import_batch_id=batch.id))
        await s.commit()
        bid = str(batch.id)
    r = await client.post(f"/imports/{bid}/rollback")
    assert r.status_code == 200, r.text
    row = next(b for b in r.json() if b["id"] == bid)
    assert row["status"] == "rolled_back" and row["annotations"] == 3
    rows = [a for a in (await client.get(f"/items/{state['item']}/annotations")).json() if a["importBatchId"] == bid]
    assert len(rows) == 3 and all(a["source"] == "import" for a in rows)
    again = await client.post(f"/imports/{bid}/rollback")
    assert again.status_code == 409
