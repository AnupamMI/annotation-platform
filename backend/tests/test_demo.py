"""The demo project: admins build and reset it; demo logins are never admins and old passwords stop working."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

pytestmark = pytest.mark.asyncio


async def demo_login(email: str, password: str) -> tuple[int, AsyncClient]:
    c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    r = await c.post("/auth/login", json={"email": email, "password": password})
    return r.status_code, c


async def test_only_admins_manage_the_demo(reviewer, annotator):
    for c in (reviewer, annotator):
        assert (await c.get("/admin/demo")).status_code == 403
        assert (await c.post("/admin/demo/reset")).status_code == 403


async def test_reset_builds_a_fresh_demo(client):
    assert (await client.get("/admin/demo")).json()["projectId"] is None

    first = (await client.post("/admin/demo/reset")).json()
    assert first["projectId"] and len(first["password"]) >= 10
    assert {x["role"] for x in first["logins"]} == {"annotator", "reviewer"}, "demo logins are never admins"
    bundle = (await client.get(f"/projects/{first['projectId']}/bundle")).json()
    assert len(bundle["items"]) == 12 and bundle["annotations"]

    # a demo reviewer signs in and changes something
    status, guest = await demo_login("mehta@example.com", first["password"])
    assert status == 200
    item = bundle["items"][0]["id"]
    assert (await guest.patch(f"/items/{item}", json={"status": "rejected"})).status_code == 200

    second = (await client.post("/admin/demo/reset")).json()
    assert second["projectId"] != first["projectId"] and second["password"] != first["password"]
    assert (await guest.get("/auth/me")).status_code == 401, "demo users are signed out by a reset"
    assert (await demo_login("mehta@example.com", first["password"]))[0] == 401
    assert (await demo_login("mehta@example.com", second["password"]))[0] == 200
    assert (await client.get(f"/projects/{first['projectId']}/bundle")).status_code == 404
    fresh = (await client.get(f"/projects/{second['projectId']}/bundle")).json()
    assert len(fresh["items"]) == 12 and "rejected" not in {i["status"] for i in fresh["items"]}
    assert (await client.get("/admin/demo")).json()["projectId"] == second["projectId"]
    await guest.aclose()
