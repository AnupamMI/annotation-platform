"""
Ask, guidelines and agents.

The language model is replaced by a fake that returns scripted replies, including
hostile ones, so the safety layers are tested against what a model might write.
"""

import io
import json
import uuid
import zipfile

import pytest

from app.ai import llm, sql
from app.config import get_settings
from tests.conftest import png_bytes

pytestmark = pytest.mark.asyncio

PROJECT = {
    "name": "Ask test",
    "domainPack": "cytology",
    "levels": [{"name": "Patient", "isSplitLevel": True}, {"name": "Slide"}, {"name": "Field of view"}],
    "labels": [
        {"name": "Cell", "color": "#3fb59a", "allowedShapes": ["polygon", "bbox"]},
        {"name": "Nucleus", "color": "#7b80e0", "allowedShapes": ["polygon"], "mustBeInsideName": "Cell"},
    ],
}
GUIDE = b"""# Cytology annotation guideline

## 1 Cells
Outline every complete cell. Skip cells cut by the image edge.

## 3 Nuclei
### 3.2 Atypical nuclei
Use Atypical nucleus only when the nucleus is clearly enlarged or darker than the normal nuclei around it.
If you are unsure, label it Nucleus and leave a note for the reviewer.
"""
st: dict = {}


def sq(x, y, s):
    return {"kind": "region", "polygons": [[[[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]]]}


async def _shape(c, item, label, geom, shape="polygon"):
    r = await c.post("/annotations/versions", json={"annotationId": str(uuid.uuid4()), "version": 1, "itemId": item,
                                                     "labelId": label, "shape": shape, "geometry": geom})
    assert r.status_code == 201, r.text


@pytest.fixture
def fake_llm(monkeypatch):
    """Scripted model replies, consumed in order."""
    replies: list[str] = []
    seen: list[str] = []

    async def complete(system, user, max_tokens=1024):
        seen.append(user)
        if not replies:
            raise llm.LLMUnavailable("no scripted reply")
        return replies.pop(0)

    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "describe", lambda: {"provider": "fake", "model": "fake-1"})
    monkeypatch.setattr(llm, "complete", complete)
    return replies


async def test_setup_project_with_patients(client):
    r = await client.post("/projects", json=PROJECT)
    assert r.status_code == 201, r.text
    st["pid"] = r.json()["project"]["id"]
    st["labels"] = {l["name"]: l["id"] for l in r.json()["labels"]}
    st["items"] = {}
    n = 0
    for patient in ("P-01", "P-02", "P-03", "P-04"):
        files = [("files", (f"{patient}_{k}.png", png_bytes(color=(n * 9 % 255, k * 40, 90 + n)), "image/png")) for k in range(2)]
        n += 1
        r = await client.post(f"/projects/{st['pid']}/items", files=files, data={"path": json.dumps([patient, f"S-{patient}"])})
        assert r.status_code == 201, r.text
        for it in r.json()["created"]:
            st["items"][it["name"]] = it["id"]
    cell, nuc = st["labels"]["Cell"], st["labels"]["Nucleus"]
    for name, item in st["items"].items():
        if name == "P-04_1":
            continue  # left unannotated
        await _shape(client, item, cell, sq(10, 10, 100))
        await _shape(client, item, nuc, sq(40, 40, 20))
    bad = st["items"]["P-01_0"]
    await _shape(client, bad, nuc, sq(250, 200, 20))   # nucleus outside any cell
    await _shape(client, bad, cell, sq(11, 11, 99))     # near-duplicate of the first cell
    await _shape(client, bad, cell, sq(300, 20, 1.5))   # stray click
    for name, item in st["items"].items():
        if name != "P-04_0":
            r = await client.post("/reviews", json={"itemId": item, "verdict": "approved"})
            assert r.status_code == 201, r.text
    r = await client.patch(f"/items/{st['items']['P-04_0']}", json={"status": "in_review"})
    assert r.status_code == 200, r.text
    # a second project the questions must never see
    other = {**PROJECT, "name": "Other project"}
    r = await client.post("/projects", json=other)
    st["other"] = r.json()["project"]["id"]
    await client.post(f"/projects/{st['other']}/items", files=[("files", ("secret.png", png_bytes(color=(1, 2, 3)), "image/png"))],
                      data={"path": json.dumps(["P-99", "S-99"])})


async def test_rules_answer_progress_and_warnings_without_a_model(client):
    r = await client.post(f"/projects/{st['pid']}/ask", json={"question": "How many images are left?"})
    body = r.json()
    assert r.status_code == 200 and body["kind"] == "answer" and body["via"] == "rules"
    assert "7 are approved" in body["text"] and "ask.images" in body["sql"]
    assert sum(row[1] for row in body["table"]["rows"]) == 8
    r = await client.post(f"/projects/{st['pid']}/ask", json={"question": "Which images have rule warnings?"})
    body = r.json()
    assert body["text"].startswith("1 image has")
    assert body["links"] == [{"label": "Open P-01_0", "itemId": st["items"]["P-01_0"]}]
    assert body["table"]["cols"] == ["Image name", "Shapes to check"]  # ids become links, not columns


async def test_guard_blocks_writes_and_other_tables(client):
    pid = uuid.UUID(st["pid"])
    for q, why in [
        ("DELETE FROM annotations", "Only SELECT"),
        ("SELECT 1; DROP TABLE items", "one statement"),
        ("WITH x AS (DELETE FROM items RETURNING *) SELECT * FROM x", "DELETE"),
        ("SELECT set_config('app.project_id', 'x', true)", "SET_CONFIG"),
        ("SELECT 1 AS \"'\", set_config('a','b',true) AS \"'\"", "SET_CONFIG"),
        ("SELECT $$'$$, set_config('a','b',true)", "Dollar"),
        ("SELECT pg_sleep(10)", "PG_SLEEP"),
    ]:
        with pytest.raises(sql.UnsafeQuery, match=why):
            await sql.run(pid, q)
    with pytest.raises(sql.UnsafeQuery, match="only read the ask"):
        await sql.run(pid, "SELECT email, password_hash FROM users")
    with pytest.raises(sql.UnsafeQuery, match="only read the ask"):
        await sql.run(pid, "SELECT * FROM restricted.identity_mappings")
    # keywords inside a string are just text
    ok = await sql.run(pid, "SELECT count(*) AS n FROM ask.reviews WHERE comment <> 'please delete this'")
    assert ok.columns == ["n"]


async def test_queries_only_see_their_project():
    a = await sql.run(uuid.UUID(st["pid"]), "SELECT count(*) FROM ask.images")
    b = await sql.run(uuid.UUID(st["other"]), "SELECT image_name FROM ask.images")
    assert a.rows == [[8]] and b.rows == [["secret"]]


async def test_slow_queries_are_stopped(monkeypatch):
    monkeypatch.setattr(get_settings(), "ask_timeout_ms", 300)
    with pytest.raises(sql.UnsafeQuery, match="too long"):
        await sql.run(uuid.UUID(st["pid"]), "SELECT count(*) FROM generate_series(1, 300000000)")


async def test_model_sql_runs_through_the_guard(client, fake_llm):
    fake_llm += [
        json.dumps({"type": "sql", "sql": "SELECT label, count(*) AS n FROM ask.annotations GROUP BY label ORDER BY label"}),
        "There are 9 cells and 8 nuclei.",
    ]
    r = await client.post(f"/projects/{st['pid']}/ask", json={"question": "count shapes per label"})
    body = r.json()
    assert body["via"] == "model" and body["text"] == "There are 9 cells and 8 nuclei."
    assert body["table"]["rows"] == [["Cell", 9], ["Nucleus", 8]]


async def test_hostile_model_sql_is_blocked_then_retried(client, fake_llm):
    fake_llm += [
        "```json\n" + json.dumps({"type": "sql", "sql": "UPDATE items SET status = 'done'"}) + "\n```",
        json.dumps({"type": "sql", "sql": "SELECT * FROM users"}),
    ]
    r = await client.post(f"/projects/{st['pid']}/ask", json={"question": "approve everything"})
    body = r.json()
    assert "couldn't answer that safely" in body["text"] and body["table"] is None
    bundle = (await client.get(f"/projects/{st['pid']}/bundle")).json()
    assert sum(1 for i in bundle["items"] if i["status"] == "done") == 7, "nothing was changed"


async def test_model_down_falls_back_to_rules(client, fake_llm):
    r = await client.post(f"/projects/{st['pid']}/ask", json={"question": "How many images are left?"})
    assert r.json()["via"] == "rules"


async def test_guidelines_upload_search_and_cite(client, annotator):
    r = await annotator.post(f"/projects/{st['pid']}/documents", files={"file": ("sop.md", GUIDE, "text/markdown")})
    assert r.status_code == 403
    r = await client.post(f"/projects/{st['pid']}/documents", files={"file": ("sop.md", GUIDE, "text/markdown")},
                          data={"title": "Cytology SOP v1"})
    assert r.status_code == 201, r.text
    assert r.json()["sections"] == 2
    r = await annotator.post(f"/projects/{st['pid']}/ask", json={"question": "What does our guideline say about atypical nuclei?"})
    body = r.json()
    assert "clearly enlarged or darker" in body["text"]
    assert body["sources"][0]["section"].endswith("3.2 Atypical nuclei") and body["sources"][0]["title"] == "Cytology SOP v1"
    docs = (await client.get(f"/projects/{st['pid']}/documents")).json()
    assert [d["title"] for d in docs] == ["Cytology SOP v1"]


async def test_consistency_checker_finds_problems_and_changes_nothing(client, annotator):
    before = (await client.get(f"/items/{st['items']['P-01_0']}/annotations")).json()
    r = await annotator.post(f"/projects/{st['pid']}/ask", json={"question": "Run a consistency check"})
    plan = r.json()
    assert plan["kind"] == "plan" and plan["run"]["status"] == "awaiting_approval"
    rid = plan["run"]["id"]
    other = await client.post(f"/agent-runs/{rid}/approve")
    assert other.status_code == 200, "an admin can approve someone else's plan"
    out = other.json()
    assert out["status"] == "completed"
    kinds = {(f["imageName"], f["kind"]) for f in out["result"]["findings"]}
    assert kinds == {("P-01_0", "containment"), ("P-01_0", "duplicates"), ("P-01_0", "tiny"), ("P-04_1", "empty")}
    assert [c["tool"] for c in out["toolCalls"]][:4] == ["check_containment", "check_duplicates", "check_tiny", "check_empty"]
    after = (await client.get(f"/items/{st['items']['P-01_0']}/annotations")).json()
    assert after == before
    again = await client.post(f"/agent-runs/{rid}/approve")
    assert again.status_code == 409


async def test_dataset_builder_plan_approve_and_no_patient_leak(client, annotator, reviewer):
    r = await reviewer.post(f"/projects/{st['pid']}/ask", json={"question": "Build a training dataset split by patient in COCO, 50/25/25"})
    run = r.json()["run"]
    assert run["agent"] == "Dataset builder" and "7 approved images" in run["summary"]
    assert "1 still in review are left out" in run["steps"][0]["detail"]
    # annotators can't approve building a dataset, and people can't approve someone else's plan
    assert (await annotator.post(f"/agent-runs/{run['id']}/approve")).status_code == 403
    r = await reviewer.post(f"/agent-runs/{run['id']}/approve")
    out = r.json()
    assert out["status"] == "completed", out
    zipped = await reviewer.get(out["result"]["download"].removeprefix("/api"))
    assert zipped.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(zipped.content))
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["dataset"]["splitLevel"] == "Patient"
    by_split: dict[str, set] = {}
    for img in manifest["items"]:
        by_split.setdefault(img["split"], set()).add(img["file"].split("_")[0].split("/")[-1])
    assert set(by_split) == {"train", "val", "test"}
    patients = [p for s in by_split.values() for p in s]
    assert len(patients) == len(set(patients)), f"a patient appears in two splits: {by_split}"
    datasets = (await client.get(f"/projects/{st['pid']}/datasets")).json()
    assert datasets[0]["images"] == 7


async def test_cancel_and_status(client, reviewer):
    r = await reviewer.post(f"/projects/{st['pid']}/ask", json={"question": "run a consistency check"})
    rid = r.json()["run"]["id"]
    r = await reviewer.post(f"/agent-runs/{rid}/cancel")
    assert r.json()["status"] == "cancelled"
    assert (await reviewer.post(f"/agent-runs/{rid}/approve")).status_code == 409
    s = (await client.get("/ai/status")).json()
    assert set(s) == {"provider", "model", "freeForm"}
