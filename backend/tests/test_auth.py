"""Sign-in, sessions, roles and team management."""

import uuid

import pytest

from app.auth import hash_password, verify_password
from tests.conftest import PASSWORD, signed_in

pytestmark = pytest.mark.asyncio


async def test_password_hashing():
    h = hash_password("correct horse")
    assert h.startswith("scrypt$") and "correct horse" not in h
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)
    assert hash_password("same") != hash_password("same"), "salted"
    assert not verify_password("x", None) and not verify_password("x", "garbage")


async def test_everything_needs_a_session(anonymous):
    assert (await anonymous.get("/health")).status_code == 200
    assert (await anonymous.get("/auth/status")).json() == {"setupNeeded": False, "setupTokenRequired": False}
    for path in ("/projects", "/users", f"/items/{uuid.uuid4()}/image", "/auth/me"):
        assert (await anonymous.get(path)).status_code == 401, path


async def test_setup_code_guards_first_admin(anonymous, monkeypatch):
    """On a public install with SETUP_TOKEN set, nobody can claim the first admin account without the code."""
    from app.api import auth as auth_api
    from app.config import get_settings

    async def needed(_session):
        return True

    monkeypatch.setattr(auth_api, "_setup_needed", needed)
    monkeypatch.setattr(get_settings(), "setup_token", "s3cret-code")
    assert (await anonymous.get("/auth/status")).json() == {"setupNeeded": True, "setupTokenRequired": True}
    body = {"email": "intruder@test", "displayName": "Intruder", "password": "long-enough-1"}
    assert (await anonymous.post("/auth/setup", json=body)).status_code == 403
    assert (await anonymous.post("/auth/setup", json={**body, "setupToken": "wrong"})).status_code == 403


async def test_login_logout_and_cookie_flags(anonymous):
    r = await anonymous.post("/auth/login", json={"email": "REV@test ", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "reviewer", "email is case- and space-insensitive"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert (await anonymous.get("/auth/me")).status_code == 200
    assert (await anonymous.post("/auth/logout")).status_code == 204
    assert (await anonymous.get("/auth/me")).status_code == 401, "the session is gone on the server too"


async def test_wrong_password_is_rate_limited(anonymous):
    for _ in range(5):
        r = await anonymous.post("/auth/login", json={"email": "nobody@test", "password": "nope"})
        assert r.status_code == 401 and r.json()["detail"] == "That email and password don't match."
    r = await anonymous.post("/auth/login", json={"email": "nobody@test", "password": "nope"})
    assert r.status_code == 429


async def test_setup_only_while_no_admin_can_sign_in(anonymous):
    r = await anonymous.post("/auth/setup", json={"email": "x@test", "displayName": "X", "password": "long-enough"})
    assert r.status_code == 409


async def test_roles_are_enforced(client, reviewer, annotator):
    r = await client.post("/projects", json={"name": "Roles", "levels": [{"name": "A", "isSplitLevel": True}, {"name": "B"}],
                                            "labels": [{"name": "L", "allowedShapes": ["bbox"]}]})
    pid, label = r.json()["project"]["id"], r.json()["labels"][0]["id"]
    from tests.conftest import png_bytes
    item = (await client.post(f"/projects/{pid}/items", files=[("files", ("a.png", png_bytes(), "image/png"))], data={"path": '["A-1"]'})).json()["created"][0]["id"]

    assert (await annotator.post("/projects", json={"name": "No", "levels": [{"name": "A"}, {"name": "B"}]})).status_code == 403
    assert (await reviewer.put(f"/projects/{pid}/setup", json={"levels": [], "labels": []})).status_code == 403
    assert (await annotator.post(f"/projects/{pid}/items", files=[("files", ("b.png", png_bytes(color=(1, 1, 1)), "image/png"))], data={"path": '["A-1"]'})).status_code == 403
    box = {"annotationId": str(uuid.uuid4()), "version": 1, "itemId": item, "labelId": label, "shape": "bbox", "geometry": {"kind": "bbox", "x": 1, "y": 1, "w": 5, "h": 5}}
    assert (await annotator.post("/annotations/versions", json=box)).status_code == 201
    assert (await annotator.patch(f"/items/{item}", json={"status": "in_review"})).status_code == 200
    assert (await annotator.patch(f"/items/{item}", json={"status": "done"})).status_code == 403, "annotators can't approve"
    assert (await annotator.post("/reviews", json={"itemId": item, "verdict": "approved"})).status_code == 403
    r = await reviewer.post("/reviews", json={"itemId": item, "verdict": "approved"})
    assert r.status_code == 201
    assert (await annotator.post(f"/projects/{pid}/export", json={"format": "coco"})).status_code == 403
    assert (await reviewer.post(f"/projects/{pid}/export", json={"format": "coco"})).status_code == 200

    viewer = await signed_in("viewer")
    assert (await viewer.get(f"/projects/{pid}/bundle")).status_code == 200, "viewers can look"
    assert (await viewer.post("/annotations/versions", json={**box, "annotationId": str(uuid.uuid4())})).status_code == 403
    await viewer.aclose()


async def test_admin_manages_the_team(client, anonymous):
    r = await client.post("/admin/users", json={"email": "New@Test", "displayName": "New Person", "role": "annotator", "password": "short"})
    assert r.status_code == 422 and "8 characters" in r.json()["detail"]
    r = await client.post("/admin/users", json={"email": "New@Test", "displayName": "New Person", "role": "annotator", "password": "long-enough-1"})
    assert r.status_code == 201 and r.json()["email"] == "new@test" and r.json()["canSignIn"]
    uid = r.json()["id"]
    assert (await client.post("/admin/users", json={"email": "new@test", "displayName": "Again", "role": "viewer", "password": "long-enough-1"})).status_code == 409

    assert (await anonymous.post("/auth/login", json={"email": "new@test", "password": "long-enough-1"})).status_code == 200
    r = await client.patch(f"/admin/users/{uid}", json={"isActive": False})
    assert r.status_code == 200 and r.json()["isActive"] is False
    assert (await anonymous.get("/auth/me")).status_code == 401, "deactivating signs the person out"

    me = (await client.get("/auth/me")).json()
    r = await client.patch(f"/admin/users/{me['id']}", json={"role": "viewer"})
    assert r.status_code == 422, "an admin can't lock themselves out"


async def test_change_password(annotator):
    r = await annotator.post("/auth/password", json={"currentPassword": "wrong", "newPassword": "another-one-1"})
    assert r.status_code == 403
    r = await annotator.post("/auth/password", json={"currentPassword": PASSWORD, "newPassword": "another-one-1"})
    assert r.status_code == 204
    assert (await annotator.get("/auth/me")).status_code == 200, "this device stays signed in"
    r = await annotator.post("/auth/password", json={"currentPassword": "another-one-1", "newPassword": PASSWORD})
    assert r.status_code == 204
