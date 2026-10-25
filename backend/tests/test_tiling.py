"""Large images are cut into a Deep Zoom pyramid; small ones are served whole."""

import io
import json
import math

import pytest
from PIL import Image

from tests.conftest import png_bytes

pytestmark = pytest.mark.asyncio


def big_jpeg(w=6000, h=4500) -> bytes:
    img = Image.new("RGB", (w, h), (230, 220, 235))
    for x in range(0, w, 500):  # some structure so tiles differ
        img.paste((120, 90, 160), (x, 0, x + 40, h))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


async def test_large_upload_is_tiled(client):
    r = await client.post("/projects", json={
        "name": "Tiling", "levels": [{"name": "Slide", "isSplitLevel": True}, {"name": "Scan"}],
        "labels": [{"name": "Region", "allowedShapes": ["polygon"]}],
    })
    pid = r.json()["project"]["id"]
    files = [("files", ("scan.jpg", big_jpeg(), "image/jpeg")), ("files", ("small.png", png_bytes(), "image/png"))]
    r = await client.post(f"/projects/{pid}/items", files=files, data={"path": json.dumps(["SL-1"])})
    assert r.status_code == 201, r.text
    big, small = r.json()["created"]
    assert big["dziUrl"] == f"/api/items/{big['id']}/image.dzi"
    assert small["dziUrl"] is None, "small images are served whole"

    dzi = (await client.get(f"/items/{big['id']}/image.dzi")).text
    assert 'TileSize="254"' in dzi and 'Overlap="1"' in dzi and 'Width="6000"' in dzi and 'Height="4500"' in dzi

    top = math.ceil(math.log2(6000))  # 13: the full-resolution level
    async def tile(level, col, row):
        r = await client.get(f"/items/{big['id']}/image_files/{level}/{col}_{row}.jpeg")
        assert r.status_code == 200, (level, col, row, r.status_code)
        return Image.open(io.BytesIO(r.content)).size

    assert await tile(top, 0, 0) == (255, 255), "first tile: overlap on one side"
    assert await tile(top, 1, 1) == (256, 256), "inner tile: overlap on both sides"
    last_col, last_row = math.ceil(6000 / 254) - 1, math.ceil(4500 / 254) - 1
    assert await tile(top, last_col, last_row) == (6000 - last_col * 254 + 1, 4500 - last_row * 254 + 1)
    assert await tile(0, 0, 0) == (1, 1), "lowest level is a single pixel"
    assert (await client.get(f"/items/{big['id']}/image_files/{top}/999_0.jpeg")).status_code == 404
    assert (await client.get(f"/items/{big['id']}/image_files/{top}/../../etc.jpeg")).status_code == 404
    assert (await client.get(f"/items/{small['id']}/image.dzi")).status_code == 404

    thumb = Image.open(io.BytesIO((await client.get(f"/items/{big['id']}/thumb")).content))
    assert max(thumb.size) <= 320
