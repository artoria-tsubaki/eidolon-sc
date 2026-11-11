"""The producer's city (GLOSSARY.md「制作人档案」), found on the deployer's QWeather account
(和风天气): by a name, or nearest where the browser says the user is; kept in the profile and
in data packages; the account itself set in the admin console, tried before it is kept.

QWeather is stood in for over its transport (`FakeQWeather`); each request it is sent is
checked to be signed as QWeather has it, with the account's own key."""

import base64
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from eidolon import data_package
from eidolon.config import Settings
from eidolon.qweather import private_key_body, private_key_pem
from tests.conftest import (
    ClientFactory,
    FakeIdentityProvider,
    agree_to_terms,
    github_identity,
    sign_in_with,
)
from tests.fakes import DONGCHENG, HARBIN, FakeClock, FakeLLM, FakeQWeather
from tests.test_admin_users import ADMIN
from tests.test_data_package import (
    MANIFEST,
    PACKAGED_PROFILE,
    export,
    import_package,
    other_deployment,
    package_of,
    unzipped,
)
from tests.test_producer_weather import NOON, account, city_of


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return replace(settings, qweather=account(), admins=frozenset({ADMIN}))


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def checked_token(auth: str, settings: Settings) -> dict:
    """The token's payload, its header and signature checked against the account."""
    assert settings.qweather is not None
    assert auth.startswith("Bearer ")
    header, payload, signature = auth.removeprefix("Bearer ").split(".")
    assert json.loads(_b64(header)) == {"alg": "EdDSA", "kid": settings.qweather.key_id}
    key = load_pem_private_key(settings.qweather.private_key.encode(), None)
    key.public_key().verify(_b64(signature), f"{header}.{payload}".encode())  # pyright: ignore
    return json.loads(_b64(payload))


async def test_cities_are_found_by_name_on_the_deployers_account_signed_with_its_key(
    make_client: ClientFactory, settings: Settings
):
    qweather = FakeQWeather(cities=[HARBIN])
    client = make_client(FakeLLM(), clock=FakeClock(NOON), qweather_transport=qweather.transport())

    res = await client.get("/api/cities", params={"q": " 哈尔滨 "})

    assert res.status_code == 200
    assert res.json() == {"cities": [city_of(HARBIN)]}
    [(path, query, auth)] = qweather.requests
    assert path == "/geo/v2/city/lookup"
    assert query == {"location": "哈尔滨", "lang": "zh", "number": "10"}
    payload = checked_token(auth, settings)
    assert payload["sub"] == "PROJ0001" and payload["iss"] == "Q000000001"
    assert payload["iat"] == int(NOON.timestamp()) - 30
    assert payload["exp"] - payload["iat"] <= 86400


async def test_the_city_nearest_the_browsers_spot_is_found_its_spot_kept_nowhere(
    make_client: ClientFactory,
):
    qweather = FakeQWeather(cities=[DONGCHENG])
    client = make_client(FakeLLM(), qweather_transport=qweather.transport())

    res = await client.get(
        "/api/cities/nearest", params={"latitude": 39.92345, "longitude": 116.41234}
    )

    assert res.json() == {"city": city_of(DONGCHENG)}
    assert qweather.lookups() == [{"location": "116.41,39.92", "lang": "zh", "number": "10"}]


async def test_none_found_is_no_city(make_client: ClientFactory):
    client = make_client(FakeLLM(), qweather_transport=FakeQWeather(cities=[]).transport())

    assert (await client.get("/api/cities", params={"q": "不存在"})).json() == {"cities": []}
    nearest = await client.get("/api/cities/nearest", params={"latitude": 0, "longitude": 0})
    assert nearest.json() == {"city": None}


async def test_qweather_failing_is_told_as_such(make_client: ClientFactory):
    client = make_client(FakeLLM(), qweather_transport=FakeQWeather(failing=True).transport())

    res = await client.get("/api/cities", params={"q": "北京"})

    assert res.status_code == 502
    assert "401" in res.json()["detail"] and "Unauthorized" in res.json()["detail"]


async def test_without_the_deployers_account_there_are_no_cities_to_find(
    make_client: ClientFactory, settings: Settings
):
    qweather = FakeQWeather()
    client = make_client(
        FakeLLM(), settings=replace(settings, qweather=None),
        qweather_transport=qweather.transport(),
    )

    assert (await client.get("/api/cities", params={"q": "北京"})).status_code == 409
    nearest = await client.get("/api/cities/nearest", params={"latitude": 1, "longitude": 1})
    assert nearest.status_code == 409
    assert (await client.get("/api/profile")).json()["finds_cities"] is False
    assert qweather.requests == []


async def test_each_user_may_look_for_cities_sixty_times_an_hour(make_client: ClientFactory):
    clock = FakeClock(NOON)
    qweather = FakeQWeather()
    client = make_client(FakeLLM(), clock=clock, qweather_transport=qweather.transport())
    friend = make_client(FakeLLM(), "friend", clock=clock, qweather_transport=qweather.transport())

    for _ in range(60):
        assert (await client.get("/api/cities", params={"q": "哈"})).status_code == 200
    assert (await client.get("/api/cities", params={"q": "哈"})).status_code == 429
    assert (await friend.get("/api/cities", params={"q": "哈"})).status_code == 200

    clock.advance(hours=1)
    assert (await client.get("/api/cities", params={"q": "哈"})).status_code == 200



async def test_the_city_is_kept_in_the_profile_and_cleared_with_it(make_client: ClientFactory):
    client = make_client(FakeLLM())
    harbin = city_of(HARBIN)

    saved = await client.put("/api/profile", json={"timezone": "Asia/Shanghai", "city": harbin})

    assert saved.json()["city"] == harbin and saved.json()["finds_cities"] is True
    again = (await client.get("/api/profile")).json()
    assert again["city"] == harbin and again["timezone"] == "Asia/Shanghai"
    cleared = await client.put("/api/profile", json={"timezone": "Asia/Shanghai"})
    assert cleared.json()["city"] is None



async def test_the_city_moves_with_the_profile_in_a_data_package(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    here = make_client(FakeLLM())
    await here.put("/api/profile", json={"city": city_of(DONGCHENG)})

    package = await export(here, "profile")
    _, manifest, data = unzipped(package)
    assert manifest["format"] == data_package.FORMAT
    assert data["profile"]["city"] == city_of(DONGCHENG)
    there = other_deployment(make_client, settings, tmp_path)
    assert (await import_package(there, package, "profile")).status_code == 200

    assert (await there.get("/api/profile")).json()["city"] == city_of(DONGCHENG)


async def test_a_city_in_a_package_of_a_format_before_cities_is_refused(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM())
    profile = {**PACKAGED_PROFILE, "city": city_of(HARBIN)}

    old = await import_package(
        client, package_of({**MANIFEST, "format": 3}, {"profile": profile}), "profile"
    )
    new = await import_package(
        client, package_of({**MANIFEST, "format": 4}, {"profile": profile}), "profile"
    )

    assert old.status_code == 422
    assert new.status_code == 200
    assert (await client.get("/api/profile")).json()["city"] == city_of(HARBIN)


async def admin(make_client: ClientFactory, github: FakeIdentityProvider, **services) -> httpx.AsyncClient:
    client = make_client(FakeLLM(), user=None, **services)
    await sign_in_with(client, github, github_identity(ADMIN[1]))
    await agree_to_terms(client)
    return client


def changed_account(settings: Settings, pem: str) -> dict:
    assert settings.qweather is not None
    return {
        "host": "https://new123.re.qweatherapi.com/", "developer_id": "Q000000002",
        "project_id": "PROJ0002", "key_id": "KEY00002", "private_key": pem,
    }


async def test_the_account_is_set_in_the_admin_console_tried_on_beijing_first(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    qweather = FakeQWeather(cities=[DONGCHENG])
    client = await admin(
        make_client, github, settings=replace(settings, qweather=None),
        qweather_transport=qweather.transport(),
    )
    new = account()
    pem = new.private_key.replace("\n", "\\n")  # as one line of `.env` might have it

    saved = await client.put("/api/admin/config/qweather", json=changed_account(settings, pem))

    assert saved.status_code == 200, saved.text
    [(path, query, auth)] = qweather.requests
    assert path == "/geo/v2/city/lookup" and query["location"] == "116.41,39.92"
    shown = (await client.get("/api/admin/config")).json()["qweather"]
    assert shown["status"] == "overridden"
    assert shown["effective"] == {
        "host": "new123.re.qweatherapi.com", "developer_id": "Q000000002",
        "project_id": "PROJ0002", "key_id": "KEY00002",
        "private_key_last4": private_key_body(new.private_key)[-4:],
    }
    assert (await client.get("/api/profile")).json()["finds_cities"] is True
    assert (await client.get("/api/cities", params={"q": "东城"})).status_code == 200
    [*_, (_, _, used)] = qweather.requests
    assert checked_token(used, replace(settings, qweather=replace(new, key_id="KEY00002")))[
        "sub"
    ] == "PROJ0002"


async def test_an_account_qweather_refuses_is_not_kept(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    client = await admin(
        make_client, github, settings=replace(settings, qweather=None),
        qweather_transport=FakeQWeather(failing=True).transport(),
    )

    res = await client.put(
        "/api/admin/config/qweather", json=changed_account(settings, account().private_key)
    )

    assert res.status_code == 422
    assert "试调失败" in res.json()["detail"] and "401" in res.json()["detail"]
    assert (await client.get("/api/admin/config")).json()["qweather"]["status"] == "env"


async def test_a_private_key_that_is_none_is_refused_before_anything_is_asked(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    qweather = FakeQWeather()
    client = await admin(make_client, github, qweather_transport=qweather.transport())

    res = await client.put(
        "/api/admin/config/qweather", json=changed_account(settings, "not-a-key-at-all")
    )

    assert res.status_code == 422 and "私钥" in res.json()["detail"]
    assert qweather.requests == []


@pytest.mark.parametrize(
    "written",
    [
        lambda pem: pem,
        lambda pem: pem.replace("\n", ""),  # pasted into one line, its breaks gone
        lambda pem: pem.replace("\n", "\\n"),
        lambda pem: private_key_body(pem),
    ],
    ids=["pem", "one-line", "escaped-breaks", "base64-alone"],
)
def test_the_private_key_is_read_however_it_was_pasted(written):
    pem = account().private_key
    body = "".join(pem.splitlines()[1:-1])

    assert private_key_body(written(pem)) == body
    assert load_pem_private_key(private_key_pem(written(pem)).encode(), None)
