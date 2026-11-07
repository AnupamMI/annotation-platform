"""
Whole-slide images. Uses OpenSlide's public test slide when it is available locally
(set TEST_SLIDE=/path/to/CMU-1-JP2K-33005.svs or any .svs); skipped otherwise, since
slide files are too big to keep in the repo.
"""

import json
import os
import time

import pytest

from app.config import get_settings
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
    # deepest level, a tile over tissue (found from the thumbnail): made from the slide on first request
    import io
    import math

    from PIL import Image, ImageStat

    top = math.ceil(math.log2(max(item["width"], item["height"])))
    col, row = tissue_tile(SLIDE, get_settings().tile_size)
    t0 = time.monotonic()
    first = await client.get(f"/items/{item['id']}/image_files/{top}/{col}_{row}.jpeg")
    assert first.status_code == 200 and first.headers["content-type"] == "image/jpeg"
    stats = ImageStat.Stat(Image.open(io.BytesIO(first.content)).convert("L"))
    assert stats.stddev[0] > 8 and stats.mean[0] < 235, "tile over tissue came back blank"
    again = await client.get(f"/items/{item['id']}/image_files/{top}/{col}_{row}.jpeg")
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


def tissue_tile(path: str, tile_size: int) -> tuple[int, int]:
    """Full-resolution tile column and row at the darkest (most tissue) part of the slide's thumbnail."""
    import openslide
    from PIL import ImageFilter

    with openslide.OpenSlide(path) as s:
        w, h = s.dimensions
        thumb = s.get_thumbnail((256, 256)).convert("L").filter(ImageFilter.BoxBlur(3))  # blur: dense tissue, not a speck
    px = thumb.load()
    x, y = min(((x, y) for y in range(thumb.height) for x in range(thumb.width)), key=lambda p: px[p])
    return int(x * w / thumb.width) // tile_size, int(y * h / thumb.height) // tile_size


async def test_ordinary_png_still_works(client):
    r = await client.post("/projects", json={
        "name": "Plain", "domainPack": "generic", "levels": [{"name": "Batch"}, {"name": "Image"}],
        "labels": [{"name": "Thing", "color": "#3fb59a", "allowedShapes": ["bbox"]}]})
    pid = r.json()["project"]["id"]
    r = await client.post(f"/projects/{pid}/items", files=[("files", ("a.png", png_bytes(), "image/png"))], data={"path": '["B-1"]'})
    item = r.json()["created"][0]
    assert item["dziUrl"] is None and item["mpp"] is None
