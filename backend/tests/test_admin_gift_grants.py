"""Admin resource grants through HTTP, including retries and account deletion."""

import asyncio
import sqlite3
from dataclasses import replace
from uuid import uuid4

import pytest

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeIdentityProvider, FakeLLM, FakeRandom
from tests.test_admin_users import ADMIN, admin_beside, find
from tests.test_gift_backpack import catalog  # noqa: F401


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return replace(settings, admins=frozenset({ADMIN}), judge=FAKE_JUDGE, affinity_and_gifts=True)


def grant(**changes) -> dict:
    return {"request_id": str(uuid4()), "gifts": [{"gift_id": "cake", "count": 3}],
            "reset_cards": 2, "draws": 10, "note": " 补偿 ", **changes}


async def test_read_is_private_and_does_not_accrue(make_client: ClientFactory, github: FakeIdentityProvider):
    client = make_client(FakeLLM(), clock=FakeClock())
    async with admin_beside(client, github) as admin:
        uid = (await find(admin, "producer"))["id"]
        result = (await admin.get(f"/api/admin/users/{uid}/gifts")).json()
        assert set(result) == {"open", "catalog", "draws", "reset_cards", "backpack"}
        assert result["draws"] == 0
        assert result["backpack"] == []
        assert all("warm" not in gift for gift in result["catalog"])
        assert (await admin.get(f"/api/admin/users/{uid}/gift-grants")).json() == {"grants": [], "total": 0}


async def test_mixed_grant_retry_daily_accrual_and_no_achievements(
    make_client: ClientFactory, github: FakeIdentityProvider, settings: Settings,
):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    async with admin_beside(client, github) as admin:
        uid = (await find(admin, "producer"))["id"]
        url = f"/api/admin/users/{uid}/gift-grants"
        body = grant(gifts=[{"gift_id": "cake", "count": 3}, {"gift_id": "tea", "count": 2}])
        first = await admin.post(url, json=body)
        assert first.status_code == 200, first.text
        assert first.json()["resources"] == {
            "draws": 12, "reset_cards": 2,
            "backpack": [{"gift_id": "cake", "count": 3}, {"gift_id": "tea", "count": 2}],
        }
        assert first.json()["grant"]["note"] == "补偿"
        clock.advance(days=2)
        assert (await admin.post(url, json=body)).json() == first.json()
        assert (await client.get("/api/gifts")).json()["draws"] == 12
        assert (await admin.post(url, json=body | {"draws": 11})).status_code == 409
        second = await admin.post(url, json=grant())
        assert second.json()["resources"]["backpack"][0]["count"] == 6
        assert second.json()["resources"]["draws"] == 22
        history = (await admin.get(url)).json()
        assert history["total"] == 2
        assert history["grants"][0]["actor"]["name"] == "boss"
        with sqlite3.connect(settings.database_path) as db:
            assert db.execute("SELECT count(*) FROM gift_rewards WHERE user_id = ?", (uid,)).fetchone() == (0,)
            assert db.execute("SELECT count(*) FROM achievement_progress WHERE user_id = ?", (uid,)).fetchone() == (0,)


@pytest.mark.parametrize("changes", [
    {"gifts": [], "reset_cards": 0, "draws": 0},
    {"draws": -1}, {"draws": 1000}, {"draws": 1.5}, {"draws": True}, {"draws": "2"},
    {"reset_cards": 1000}, {"note": "x" * 501}, {"request_id": "invalid"},
    {"gifts": [{"gift_id": "cake", "count": 0}]},
    {"gifts": [{"gift_id": "cake", "count": 1000}]},
    {"gifts": [{"gift_id": "cake", "count": 1}, {"gift_id": "cake", "count": 2}]},
    {"gifts": [{"gift_id": "cake", "count": 1}, {"gift_id": "missing", "count": 1}]},
    {"gifts": [{"gift_id": "candy", "count": 1}]},
])
async def test_invalid_grants_change_nothing(make_client: ClientFactory, github: FakeIdentityProvider, changes: dict):
    client = make_client(FakeLLM(), clock=FakeClock())
    async with admin_beside(client, github) as admin:
        uid = (await find(admin, "producer"))["id"]
        url = f"/api/admin/users/{uid}"
        assert (await admin.post(url + "/gift-grants", json=grant(**changes))).status_code == 422
        assert (await admin.get(url + "/gifts")).json()["draws"] == 0
        assert (await admin.get(url + "/gift-grants")).json()["total"] == 0


async def test_auth_missing_users_and_closed(make_client: ClientFactory, github: FakeIdentityProvider):
    client = make_client(FakeLLM(), clock=FakeClock())
    async with admin_beside(client, github) as admin:
        uid = (await find(admin, "producer"))["id"]
        url = f"/api/admin/users/{uid}"
        for suffix in ("/gifts", "/gift-grants"):
            assert (await client.get(url + suffix)).status_code == 404
            assert (await admin.get("/api/admin/users/missing" + suffix)).status_code == 404
        assert (await client.post(url + "/gift-grants", json=grant())).status_code == 404
        assert (await admin.post("/api/admin/users/missing/gift-grants", json=grant())).status_code == 404
        res = await admin.put("/api/admin/config/affinity", json={"enabled": False})
        assert res.status_code == 200, res.text
        res = await admin.post(url + "/gift-grants", json=grant())
        assert res.status_code == 409 and res.json()["gifts_closed"]
        assert (await admin.get(url + "/gifts")).json()["open"] is False


async def test_concurrent_retries_and_consumption(make_client: ClientFactory, github: FakeIdentityProvider):
    client = make_client(FakeLLM(), clock=FakeClock(), random=FakeRandom(1, 0))
    await client.get("/api/gifts")
    async with admin_beside(client, github) as admin:
        uid = (await find(admin, "producer"))["id"]
        url = f"/api/admin/users/{uid}"
        body = grant()
        results = await asyncio.gather(
            admin.post(url + "/gift-grants", json=body),
            admin.post(url + "/gift-grants", json=body),
            client.post("/api/gifts/draws", json={}),
        )
        assert all(r.status_code == 200 for r in results)
        resources = (await admin.get(url + "/gifts")).json()
        assert resources["draws"] == 11
        assert resources["backpack"] == [{"gift_id": "cake", "count": 4}]
        assert (await admin.get(url + "/gift-grants")).json()["total"] == 1


async def test_history_pagination_and_deleting_actor_then_recipient(
    make_client: ClientFactory, github: FakeIdentityProvider, settings: Settings,
):
    client = make_client(FakeLLM(), clock=FakeClock())
    async with admin_beside(client, github) as admin:
        uid = (await find(admin, "producer"))["id"]
        url = f"/api/admin/users/{uid}/gift-grants"
        ids = []
        for _ in range(21):
            body = grant()
            ids.append(body["request_id"])
            assert (await admin.post(url, json=body)).status_code == 200
        page = (await admin.get(url)).json()
        assert page["total"] == 21
        assert [g["request_id"] for g in page["grants"]] == list(reversed(ids[1:]))
        assert (await admin.get(url, params={"offset": 20})).json()["grants"][0]["request_id"] == ids[0]
        assert (await admin.post("/api/account/delete", json={"confirm": True})).status_code == 204
        with sqlite3.connect(settings.database_path) as db:
            assert db.execute("SELECT DISTINCT actor_id FROM user_gift_grants").fetchall() == [(None,)]
        # A newly signed-in admin can still read the old records, with no actor identity.
        async with admin_beside(client, github) as next_admin:
            assert (await next_admin.get(url)).json()["grants"][0]["actor"] is None
    assert (await client.post("/api/account/delete", json={"confirm": True})).status_code == 204
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT count(*) FROM user_gift_grants").fetchone() == (0,)


async def test_failure_writing_history_rolls_back_all_resources(
    make_client: ClientFactory, github: FakeIdentityProvider, settings: Settings,
):
    client = make_client(FakeLLM(), clock=FakeClock())
    async with admin_beside(client, github) as admin:
        uid = (await find(admin, "producer"))["id"]
        with sqlite3.connect(settings.database_path) as db:
            db.execute("CREATE TRIGGER fail_grant BEFORE INSERT ON user_gift_grants"
                       " BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            await admin.post(f"/api/admin/users/{uid}/gift-grants", json=grant())
        result = (await admin.get(f"/api/admin/users/{uid}/gifts")).json()
        assert (result["draws"], result["reset_cards"], result["backpack"]) == (0, 0, [])


async def test_grants_to_admin_and_banned_user_and_daily_settlement(
    make_client: ClientFactory, github: FakeIdentityProvider,
):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    await client.get("/api/gifts")
    clock.advance(days=1)
    async with admin_beside(client, github) as admin:
        uid = (await find(admin, "producer"))["id"]
        boss = (await find(admin, "boss"))["id"]
        assert (await admin.post(f"/api/admin/users/{uid}/ban", json={})).status_code == 200
        result = await admin.post(f"/api/admin/users/{uid}/gift-grants", json=grant())
        assert result.status_code == 200
        assert result.json()["resources"]["draws"] == 14  # 2 yesterday + 2 due + 10 granted
        assert (await admin.get(f"/api/admin/users/{uid}")).json()["banned"] is True
        result = await admin.post(f"/api/admin/users/{boss}/gift-grants", json=grant(
            gifts=[], draws=999, reset_cards=999, note="",
        ))
        assert result.status_code == 200
        assert result.json()["resources"] == {"draws": 1001, "reset_cards": 999, "backpack": []}


async def test_retired_inventory_visible_and_completed_retry_after_closing(
    make_client: ClientFactory, github: FakeIdentityProvider, settings: Settings,
):
    client = make_client(FakeLLM(), clock=FakeClock())
    async with admin_beside(client, github) as admin:
        uid = (await find(admin, "producer"))["id"]
        url = f"/api/admin/users/{uid}"
        with sqlite3.connect(settings.database_path) as db:
            db.execute("INSERT INTO gift_backpacks VALUES (?, 'candy', 2)", (uid,))
        assert (await admin.get(url + "/gifts")).json()["backpack"] == [{"gift_id": "candy", "count": 2}]
        body = grant()
        first = await admin.post(url + "/gift-grants", json=body)
        await admin.put("/api/admin/config/affinity", json={"enabled": False})
        assert (await admin.post(url + "/gift-grants", json=body)).json() == first.json()
        assert (await admin.post(url + "/gift-grants", json=grant())).status_code == 409
