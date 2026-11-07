"""
Whole-slide images. Uses OpenSlide's public test slide when it is available locally
(set TEST_SLIDE=/path/to/CMU-1-JP2K-33005.svs or any .svs); skipped otherwise, since
slide files are too big to keep in the repo.
"""

import json
import os
import time

import pytest

from app.services import slides
from tests.conftest import png_bytes

pytestmark = pytest.mark.asyncio
SLIDE = os.environ.get("TEST_SLIDE", "")


async def test_plain_tiff_is_not_a_slide(tmp_path):
    from PIL import Image

    p = tmp_path / "flat.tif"
    Image.new("RGB", (64, 48), (120, 80, 160)).save(p)
    assert slides.is_slide("flat.tif", str(p)) is False
    assert slides.is_slide("photo.png", str(p)) is False


@pytest.mark.skipif(not os.path.exists(SLIDE), reason="set TEST_SLIDE to a whole-slide file")
async def test_slide_upload_tiles_and_scale(client):
    r = await client.post("/projects", json={
        "name": "Slides", "domainPack": "generic",
        "levels": [{"name": "Case", "isSplitLevel": True}, {"name": "Slide"}],
        "labels": [{"name": "Region", "color": "#3fb59a", "allowedShapes": ["polygon"]}]})
    pid = r.json()["project"]["id"]
    with open(SLIDE, "rb") as f:
        r = await client.post(f"/projects/{pid}/items", files=[("files", (os.path.basename(SLIDE), f, "application/octet-stream"))],
                              data={"path": json.dumps(["C-1"])})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["skipped"] == []
    item = body["created"][0]
    assert item["dziUrl"] and item["width"] > 10000 and item["mpp"] and 0.1 < item["mpp"] < 2
    dzi = (await client.get(f"/items/{item['id']}/image.dzi")).text
    assert f'Width="{item["width"]}"' in dzi
    # deepest level, a tile in the middle: made from the slide on first request
    import math
    top = math.ceil(math.log2(max(item["width"], item["height"])))
    t0 = time.monotonic()
    first = await client.get(f"/items/{item['id']}/image_files/{top}/40_30.jpeg")
    assert first.status_code == 200 and first.headers["content-type"] == "image/jpeg"
    again = await client.get(f"/items/{item['id']}/image_files/{top}/40_30.jpeg")
    assert again.content == first.content
    assert (await client.get(f"/items/{item['id']}/image_files/{top}/9999_0.jpeg")).status_code == 404
    assert (await client.get(f"/items/{item['id']}/thumb")).status_code == 200
    assert time.monotonic() - t0 < 30
    # an annotation far into the slide is accepted (coordinates are full-resolution pixels)
    labels = (await client.get(f"/projects/{pid}/bundle")).json()["labels"]
    x, y = item["width"] - 500, item["height"] - 500
    r = await client.post("/annotations/versions", json={
        "annotationId": "6a1f0c62-0000-4000-8000-000000000001", "version": 1, "itemId": item["id"], "labelId": labels[0]["id"],
        "shape": "polygon", "geometry": {"kind": "region", "polygons": [[[[x, y], [x + 200, y], [x + 200, y + 200], [x, y + 200], [x, y]]]]}})
    assert r.status_code == 201, r.text


async def test_ordinary_png_still_works(client):
    r = await client.post("/projects", json={
        "name": "Plain", "domainPack": "generic", "levels": [{"name": "Batch"}, {"name": "Image"}],
        "labels": [{"name": "Thing", "color": "#3fb59a", "allowedShapes": ["bbox"]}]})
    pid = r.json()["project"]["id"]
    r = await client.post(f"/projects/{pid}/items", files=[("files", ("a.png", png_bytes(), "image/png"))], data={"path": '["B-1"]'})
    item = r.json()["created"][0]
    assert item["dziUrl"] is None and item["mpp"] is None
