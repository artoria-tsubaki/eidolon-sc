"""The periods the user wrote down in data packages (GLOSSARY.md「周期」「数据包」): a category
of its own, `cycles`, each record its character, first day and days, nothing else; imported,
they join those there, each checked as it is: one of a character not there, out of 1 to 10
days, after today where the producer is, or overlapping another of hers, those there first
or imported before it, is skipped, and said so. Exported and imported with cycles off too.
Driven through the API, records written down as `test_cycle_records.py` writes them."""

import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from eidolon import data_package
from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.test_cycle_records import added, client_of, firsts
from tests.test_cycles_switch import switch
from tests.test_data_package import (
    MANIFEST,
    PACKAGED_PROFILE,
    export,
    import_package,
    package_of,
    unzipped,
)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return replace(settings, cycles=True)


def there_of(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
) -> httpx.AsyncClient:
    return client_of(make_client, replace(settings, database_path=tmp_path / "there.db"))


def records_in(database: Path) -> list[tuple[str, str, int]]:
    """Every record kept, of anyone: whose, from when and how many days, by first day."""
    with sqlite3.connect(database) as db:
        return db.execute(
            "SELECT character_id, first_day, days FROM cycle_records ORDER BY first_day"
        ).fetchall()


def package_with(*cycles: dict, format: int = 3) -> bytes:
    return package_of(
        MANIFEST | {"format": format, "categories": ["cycles"], "counts": {"cycles": len(cycles)}},
        {"cycles": list(cycles)},
    )


def record(first_day: str, days: int = 5, character: str = "mano") -> dict:
    return {"character_id": character, "first_day": first_day, "days": days}


async def test_records_go_into_a_package_with_their_category_alone(
    make_client: ClientFactory, settings: Settings
):
    client = client_of(make_client, settings)
    await added(client, "2026-09-25", 6)
    await added(client, "2026-08-27", 4)
    await client.post("/api/cycles/kogane/records", json={"first_day": "2026-10-03", "days": 5})

    _, manifest, data = unzipped(await export(client, "conversations,cycles"))
    _, without_manifest, without = unzipped(await export(client, "conversations"))

    assert manifest["format"] == data_package.FORMAT
    assert manifest["categories"] == ["conversations", "cycles"]
    assert manifest["counts"] == {"conversations": 0, "cycles": 3}
    assert data["cycles"] == [
        record("2026-08-27", 4), record("2026-09-25", 6), record("2026-10-03", 5, "kogane"),
    ]
    assert "cycles" not in without_manifest["categories"] and "cycles" not in without


async def test_imported_they_join_those_there_overlapping_ones_skipped_those_there_kept(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    here = client_of(make_client, settings)
    for first_day in ("2026-08-01", "2026-08-29", "2026-09-26"):
        await added(here, first_day)
    there = there_of(make_client, settings, tmp_path)
    await added(there, "2026-09-28", 3)

    res = await import_package(there, await export(here, "cycles"), "cycles")

    assert res.status_code == 200, res.text
    assert (res.json()["cycles"], res.json()["cycles_skipped"]) == (2, 1)
    assert firsts((await there.get("/api/cycles")).json()["characters"][0]) == [
        ("2026-08-01", 5), ("2026-08-29", 5), ("2026-09-28", 3),
    ]
    again = await import_package(there, await export(here, "cycles"), "cycles")
    assert (again.json()["cycles"], again.json()["cycles_skipped"]) == (0, 3)


async def test_ones_overlapping_another_imported_before_them_are_skipped(
    make_client: ClientFactory, settings: Settings
):
    client = client_of(make_client, settings)

    res = await import_package(client, package_with(
        record("2026-10-01"), record("2026-10-03"), record("2026-10-06"),
        record("2026-10-03", character="kogane"),
    ), "cycles")

    assert (res.json()["cycles"], res.json()["cycles_skipped"]) == (3, 1)
    assert records_in(settings.database_path) == [
        ("mano", "2026-10-01", 5), ("kogane", "2026-10-03", 5), ("mano", "2026-10-06", 5),
    ]


async def test_ones_of_a_character_not_there_after_today_or_out_of_bounds_are_skipped(
    make_client: ClientFactory, settings: Settings
):
    client = client_of(make_client, settings)  # the 25th in Shanghai

    res = await import_package(client, package_with(
        record("2026-10-01", character="nobody"), record("2026-10-26"), record("2026-08-01", 11),
        record("2026-10-25", 10),
    ), "cycles")

    assert res.status_code == 200, res.text
    assert (res.json()["cycles"], res.json()["cycles_skipped"]) == (1, 3)
    assert records_in(settings.database_path) == [("mano", "2026-10-25", 10)]


async def test_today_is_where_the_profile_imported_with_them_has_the_producer(
    make_client: ClientFactory, settings: Settings
):
    client = client_of(make_client, settings)  # the 26th in Auckland, the 25th in Shanghai
    package = package_of(
        MANIFEST | {
            "format": 3, "categories": ["profile", "cycles"],
            "counts": {"profile": 1, "cycles": 1},
        },
        {"profile": PACKAGED_PROFILE | {"timezone": "Pacific/Auckland"}, "cycles": [record("2026-10-26")]},
    )

    res = await import_package(client, package, "profile,cycles")

    assert (res.json()["cycles"], res.json()["cycles_skipped"]) == (1, 0)


async def test_they_are_exported_and_imported_with_cycles_off(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    here = client_of(make_client, settings)
    await added(here, "2026-10-01")
    await switch(here, False)
    there = there_of(make_client, replace(settings, cycles=False), tmp_path)

    package = await export(here, "cycles")
    res = await import_package(there, package, "cycles")

    assert unzipped(package)[2]["cycles"] == [record("2026-10-01")]
    assert res.json()["cycles"] == 1
    assert records_in(tmp_path / "there.db") == [("mano", "2026-10-01", 5)]


async def test_left_out_of_the_import_none_are_imported(
    make_client: ClientFactory, settings: Settings
):
    client = client_of(make_client, settings)

    res = await import_package(client, package_with(record("2026-10-01")), "profile")

    assert res.status_code == 200, res.text
    assert (res.json()["cycles"], res.json()["cycles_skipped"]) == (0, 0)
    assert records_in(settings.database_path) == []


async def test_a_record_with_more_than_its_character_first_day_and_days_is_refused(
    make_client: ClientFactory, settings: Settings
):
    client = client_of(make_client, settings)

    res = await import_package(
        client, package_with(record("2026-10-01") | {"id": "a"}), "cycles"
    )

    assert (res.status_code, res.json()["code"]) == (422, "broken")


async def test_a_package_of_format_2_imports_as_before(
    make_client: ClientFactory, settings: Settings
):
    client = client_of(make_client, settings)

    res = await import_package(
        client, package_of(MANIFEST | {"format": 2}, {"profile": PACKAGED_PROFILE}), "profile,cycles"
    )

    assert res.status_code == 200, res.text
    assert (res.json()["profile"], res.json()["cycles"], res.json()["cycles_skipped"]) == (
        True, 0, 0
    )


async def test_records_in_a_format_before_them_are_refused(
    make_client: ClientFactory, settings: Settings
):
    client = client_of(make_client, settings)

    res = await import_package(client, package_with(record("2026-10-01"), format=2), "cycles")

    assert (res.status_code, res.json()["code"]) == (422, "broken")
