"""
Import and export tests.

- Round trip per format, in memory: write -> read gives back the same shapes.
- Round trip through the API: export a project, import the zip into a second project
  with the same images, and compare every annotation.
- Import behaviour: matching, label suggestions, fixes, conflicts, undo.
"""

import io
import json
import uuid
import zipfile

import pytest

from app.formats import ExportImage, Shape, read_upload, write_export
from tests.conftest import png_bytes

pytestmark = pytest.mark.asyncio

W, H = 640, 480
SHAPES = [
    Shape("Cell", "bbox", {"kind": "bbox", "x": 10.5, "y": 20.0, "w": 100.0, "h": 50.2}),
    Shape("Cell", "polygon", {"kind": "region", "polygons": [[[[200, 200], [300, 210], [320, 300], [210, 310], [200, 200]]]]}),
    Shape("Nucleus", "polygon", {"kind": "region", "polygons": [[[[240, 240], [260, 240], [260, 260], [240, 260], [240, 240]]]]}),
    Shape("Inflammatory cell", "point", {"kind": "point", "x": 500.3, "y": 400.7}),
]
POINTS_SUPPORTED = {"cvat_xml", "label_studio"}


def key(s: Shape, ndigits=1):
    g = s.geometry
    if g["kind"] == "bbox":
        return (s.label, "bbox", round(g["x"], ndigits), round(g["y"], ndigits), round(g["w"], ndigits), round(g["h"], ndigits))
    if g["kind"] == "point":
        return (s.label, "point", round(g["x"], ndigits), round(g["y"], ndigits))
    ring = g["polygons"][0][0]
    ring = ring[:-1] if ring[0] == ring[-1] else ring
    return (s.label, "polygon", tuple((round(x, ndigits), round(y, ndigits)) for x, y in ring))


def resolve(s: Shape):
    """Undo normalization for formats that store relative coordinates."""
    if not s.normalized:
        return s
    g = dict(s.geometry)
    if g["kind"] == "bbox":
        g = {**g, "x": g["x"] * W, "y": g["y"] * H, "w": g["w"] * W, "h": g["h"] * H}
    elif g["kind"] == "point":
        g = {**g, "x": g["x"] * W, "y": g["y"] * H}
    else:
        g = {"kind": "region", "polygons": [[[[x * W, y * H] for x, y in r] for r in p] for p in g["polygons"]]}
    return Shape(s.label, s.shape, g)


@pytest.mark.parametrize("fmt", ["yolo", "coco", "cvat_xml", "label_studio"])
async def test_format_round_trip_in_memory(fmt):
    labels = ["Cell", "Nucleus", "Inflammatory cell"]
    img = ExportImage("fov_01.png", W, H, SHAPES, png_bytes(W, H))
    data, notes = write_export(fmt, [img], labels)
    parsed = read_upload("export.zip", data)
    assert parsed.format == fmt
    entry = next(e for e in parsed.images if e.file_name.lower().startswith("fov_01"))
    assert entry.data is not None, "image travels inside the archive"
    expected = {key(s) for s in SHAPES if s.shape != "point" or fmt in POINTS_SUPPORTED}
    got = {key(resolve(s)) for s in entry.shapes}
    assert got == expected, (fmt, got ^ expected)
    if fmt not in POINTS_SUPPORTED:
        assert any("point" in n for n in notes), "dropped points are reported, not silent"


# ---------------- through the API ----------------
PROJECT = {
    "domainPack": "cytology",
    "levels": [{"name": "Patient", "isSplitLevel": True}, {"name": "Slide"}, {"name": "Field of view"}],
    "labels": [
        {"name": "Cell", "color": "#3fb59a", "allowedShapes": ["polygon", "bbox"]},
        {"name": "Nucleus", "color": "#7b80e0", "allowedShapes": ["polygon"], "mustBeInsideName": "Cell"},
        {"name": "Inflammatory cell", "color": "#e9a13b", "allowedShapes": ["point", "bbox"]},
    ],
}
IMAGES = [("fov_a.png", (90, 40, 40)), ("fov_b.png", (40, 90, 40))]


async def make_project(client, name):
    r = await client.post("/projects", json={**PROJECT, "name": name})
    assert r.status_code == 201, r.text
    pid = r.json()["project"]["id"]
    labels = {l["name"]: l["id"] for l in r.json()["labels"]}
    files = [("files", (n, png_bytes(W, H, c), "image/png")) for n, c in IMAGES]
    r = await client.post(f"/projects/{pid}/items", files=files, data={"path": json.dumps(["P-9", "S-9"])})
    assert r.status_code == 201, r.text
    items = {i["name"]: i["id"] for i in r.json()["created"]}
    return pid, labels, items


async def annotate(client, item, labels, shapes):
    for s in shapes:
        r = await client.post("/annotations/versions", json={
            "annotationId": str(uuid.uuid4()), "version": 1, "itemId": item, "labelId": labels[s.label],
            "shape": s.shape, "geometry": s.geometry,
        })
        assert r.status_code == 201, r.text


async def current(client, pid, item):
    rows = (await client.get(f"/items/{item}/annotations")).json()
    latest = {}
    for r in rows:
        if r["annotationId"] not in latest or r["version"] > latest[r["annotationId"]]["version"]:
            latest[r["annotationId"]] = r
    names = {l["id"]: l["name"] for l in (await client.get(f"/projects/{pid}/bundle")).json()["labels"]}
    return {key(Shape(names[r["labelId"]], r["shape"], r["geometry"])) for r in latest.values() if not r["isDeleted"]}


@pytest.mark.parametrize("fmt", ["yolo", "coco", "cvat_xml", "label_studio"])
async def test_api_round_trip(client, fmt):
    src, src_labels, src_items = await make_project(client, f"RT source {fmt}")
    await annotate(client, src_items["fov_a"], src_labels, SHAPES)
    await annotate(client, src_items["fov_b"], src_labels, SHAPES[:2])
    for it in src_items.values():
        await client.patch(f"/items/{it}", json={"status": "done"})

    r = await client.post(f"/projects/{src}/export", json={"format": fmt, "scope": "approved", "includeImages": True})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("manifest.json"))
    assert manifest["images"] == 2 and len(manifest["items"][0]["annotationRows"]) > 0

    dst, dst_labels, dst_items = await make_project(client, f"RT target {fmt}")
    r = await client.post(f"/projects/{dst}/imports", files={"file": (f"export-{fmt}.zip", r.content, "application/zip")})
    assert r.status_code == 201, r.text
    preview = r.json()
    report = preview["report"]
    assert report["format"] == fmt
    assert report["totals"]["matched"] == 2 and report["totals"]["unmatched"] == 0
    assert all(row["how"] in ("file name", "checksum") for row in report["images"])
    mapping = {l["source"]: l["suggestedId"] for l in report["labels"]}
    assert all(mapping.values()), "same label names are matched automatically"

    r = await client.post(f"/imports/{preview['id']}/commit", json={"labelMapping": mapping})
    assert r.status_code == 200, r.text

    for name in ("fov_a", "fov_b"):
        want = await current(client, src, src_items[name])
        if fmt not in POINTS_SUPPORTED:
            want = {k for k in want if k[1] != "point"}
        got = await current(client, dst, dst_items[name])
        assert got == want, (fmt, name, got ^ want)
    bundle = (await client.get(f"/projects/{dst}/bundle")).json()
    assert {i["status"] for i in bundle["items"]} == {"in_review"}, "imported work goes to review"


def cvat_xml(images: list[tuple[str, int, int, str]]) -> bytes:
    body = "".join(f'<image id="{i}" name="{n}" width="{w}" height="{h}">{inner}</image>' for i, (n, w, h, inner) in enumerate(images))
    return f'<?xml version="1.0"?><annotations><version>1.1</version>{body}</annotations>'.encode()


async def test_import_checks_mapping_fixes_conflicts_and_undo(client):
    pid, labels, items = await make_project(client, "Import behaviour")
    await annotate(client, items["fov_a"], labels, SHAPES[:1])  # existing work -> conflict
    xml = cvat_xml([
        ("fov_a.png", W, H,
         '<box label="cells" xtl="600" ytl="10" xbr="700" ybr="60"/>'  # past the edge -> trimmed
         '<box label="cells" xtl="600" ytl="10" xbr="700" ybr="60"/>'  # duplicate
         '<polygon label="Nuclei" points="10,10;60,60;60,10;10,60"/>'  # bow tie -> repaired
         '<box label="rbc" xtl="5" ytl="5" xbr="5" ybr="40"/>'  # zero width -> dropped
         '<box label="rbc" xtl="300" ytl="300" xbr="320" ybr="320"/>'),
        ("fov_b.png", W // 2, H // 2, '<box label="cells" xtl="10" ytl="10" xbr="20" ybr="20"/>'),  # annotated at half size -> scaled
        ("not_here.png", W, H, '<box label="cells" xtl="1" ytl="1" xbr="9" ybr="9"/>'),
    ])
    r = await client.post(f"/projects/{pid}/imports", files={"file": ("annotations.xml", xml, "text/xml")})
    assert r.status_code == 201, r.text
    p = r.json()
    rep = p["report"]
    kinds = {i["kind"]: i["count"] for i in rep["issues"]}
    assert kinds["trimmed"] == 1 and kinds["duplicate"] == 1 and kinds["repaired"] == 1 and kinds["zero_area"] == 1 and kinds["scaled"] == 1
    assert rep["totals"]["unmatched"] == 1 and rep["unmatched"][0]["file"] == "not_here.png"
    sug = {l["source"]: (l["suggestedId"], l["why"]) for l in rep["labels"]}
    assert sug["cells"][0] == labels["Cell"] and sug["Nuclei"][0] == labels["Nucleus"]
    assert sug["rbc"][0] is None
    assert [c["itemId"] for c in rep["conflicts"]] == [items["fov_a"]]

    # every label needs a decision
    r = await client.post(f"/imports/{p['id']}/commit", json={"labelMapping": {"cells": labels["Cell"], "Nuclei": labels["Nucleus"]}})
    assert r.status_code == 422 and "rbc" in r.json()["detail"]
    # a point-only label can't take polygons
    bad = {"cells": labels["Cell"], "Nuclei": labels["Inflammatory cell"], "rbc": "skip"}
    r = await client.post(f"/imports/{p['id']}/commit", json={"labelMapping": bad})
    assert r.status_code == 422 and "only allows" in r.json()["detail"]

    mapping = {"cells": labels["Cell"], "Nuclei": labels["Nucleus"], "rbc": "skip"}
    r = await client.post(f"/imports/{p['id']}/commit", json={"labelMapping": mapping, "conflictPolicy": "replace"})
    assert r.status_code == 200, r.text
    assert r.json()["result"] == {"annotations": 3, "images": 2, "skippedImages": 0, "boxesAsPolygons": 0}

    a = await current(client, pid, items["fov_a"])
    assert ("Cell", "bbox", 600.0, 10.0, 40.0, 50.0) in a, "trimmed to the image edge"
    assert not any(k[1] == "bbox" and k[2] == 10.5 for k in a), "replace removed the earlier box"
    b = await current(client, pid, items["fov_b"])
    assert b == {("Cell", "bbox", 20.0, 20.0, 20.0, 20.0)}, "scaled from half size"

    r = await client.post(f"/imports/{p['id']}/commit", json={"labelMapping": mapping})
    assert r.status_code == 409, "an import can only be committed once"

    rb = await client.post(f"/imports/{p['id']}/rollback")
    assert rb.status_code == 200
    assert await current(client, pid, items["fov_b"]) == set()


async def test_unlinked_images_can_be_kept(client):
    pid, labels, _ = await make_project(client, "Unlinked")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("annotations.xml", cvat_xml([("brand_new.png", 200, 100, '<box label="Cell" xtl="1" ytl="1" xbr="50" ybr="50"/>')]))
        z.writestr("images/brand_new.png", png_bytes(200, 100, (1, 2, 3)))
    r = await client.post(f"/projects/{pid}/imports", files={"file": ("with_images.zip", buf.getvalue(), "application/zip")})
    p = r.json()
    assert p["report"]["unmatched"][0]["hasImage"] is True
    r = await client.post(f"/imports/{p['id']}/commit", json={"labelMapping": {"Cell": labels["Cell"]}, "unlinked": "hold"})
    assert r.status_code == 200, r.text
    bundle = (await client.get(f"/projects/{pid}/bundle")).json()
    new = next(i for i in bundle["items"] if i["name"] == "brand_new")
    assert new["nodeId"] is None and new["width"] == 200


async def test_yolo_needs_size_and_reports_ambiguity(client):
    pid, labels, items = await make_project(client, "YOLO edge cases")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("labels/fov_a.txt", "0 0.5 0.5 0.25 0.25\n")  # matches project image -> size known
        z.writestr("labels/elsewhere.txt", "0 0.5 0.5 0.1 0.1\n")  # no image, no match -> size unknown
        z.writestr("images/fov_b.png", png_bytes(W, H, (40, 90, 40)))  # image without a label file -> ambiguous
    r = await client.post(f"/projects/{pid}/imports", files={"file": ("yolo.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 201, r.text
    rep = r.json()["report"]
    texts = " ".join(i["text"] for i in rep["issues"])
    assert "No class names found" in texts and "may be unannotated" in texts
    assert rep["labels"][0]["source"] == "0" and rep["totals"]["annotations"] == 1


async def test_unknown_format_is_explained(client):
    pid, _, _ = await make_project(client, "Bad upload")
    r = await client.post(f"/projects/{pid}/imports", files={"file": ("notes.json", b'{"hello": 1}', "application/json")})
    assert r.status_code == 422 and "Supported" in r.json()["detail"]


async def test_names_match_despite_spacing_and_case(client):
    pid, labels, items = await make_project(client, "Loose names")
    xml = cvat_xml([("FOV A.PNG", W, H, '<box label="Cell" xtl="1" ytl="1" xbr="20" ybr="20"/>')])
    r = await client.post(f"/projects/{pid}/imports", files={"file": ("a.xml", xml, "text/xml")})
    rep = r.json()["report"]
    assert rep["images"][0]["itemId"] == items["fov_a"] and rep["images"][0]["how"] == "file name"
