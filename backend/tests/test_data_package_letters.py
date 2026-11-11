"""Letters in data packages (GLOSSARY.md「信」「数据包」): the letters she wrote the producer go
into a package of their own category, `letters`, as she wrote them, delivery cues and all, but
without her voice; and come out of it into another deployment as they were, read and popped
up, to read without her voice. Driven through the API, letters written as `test_letters.py`
writes them."""

from dataclasses import replace
from pathlib import Path

import httpx

from eidolon import data_package
from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM
from tests.test_data_package import (
    MANIFEST,
    export,
    import_package,
    package_of,
    unzipped,
)
from tests.test_letters import (
    LETTER,
    close_with_mano,
    letters,
    read,
    settings,  # noqa: F401  letters on in `.env`
    written,
)
from tests.test_reach_outs import backend_of

PACKAGED_LETTER = {
    "character_id": "mano",
    "character_name": "櫻木真乃",
    "occasions": ["close"],
    "day": "2026-09-25",
    "salutation": LETTER["salutation"],
    "paragraphs": [
        {"ja": "[smiling] いつもありがとうございます。", "zh": "一直以来谢谢你。"},
        LETTER["paragraphs"][1],
    ],
}
"""The letter `close_with_mano` has her write, as a package keeps it, but for its time:
delivery cues in the Japanese, as she says it, none in the Chinese."""


async def a_letter_written(make_client: ClientFactory, settings: Settings) -> httpx.AsyncClient:
    """A client of a user Mano has written a letter, `PACKAGED_LETTER`."""
    client, _, _, _ = await close_with_mano(make_client, settings, written())
    await backend_of(client).letters.run_due()
    return client


def there_of(make_client: ClientFactory, settings: Settings, tmp_path: Path) -> httpx.AsyncClient:
    """A client of another deployment, with letters off: the same characters, a database of
    its own."""
    return make_client(
        FakeLLM(),
        settings=replace(settings, database_path=tmp_path / "there.db", letters=False),
    )


def letters_package(*letters: dict, format: int = 5) -> bytes:
    return package_of(
        MANIFEST | {
            "format": format, "categories": ["letters"], "counts": {"letters": len(letters)},
        },
        {"letters": list(letters)},
    )


def packaged(**changed: object) -> dict:
    return PACKAGED_LETTER | {"written_at": "2026-09-25T04:00:00.000000+00:00"} | changed


async def test_her_letters_move_to_another_deployment_as_they_were_read_and_without_voice(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    here = await a_letter_written(make_client, settings)
    there = there_of(make_client, settings, tmp_path)

    package = await export(here, "letters")
    _, manifest, data = unzipped(package)
    res = await import_package(there, package, "letters")
    again = await import_package(there, package, "letters")

    assert (manifest["format"], manifest["categories"], manifest["counts"]) == (
        data_package.FORMAT, ["letters"], {"letters": 1}
    )
    [kept] = data["letters"]
    assert kept == PACKAGED_LETTER | {"written_at": kept["written_at"]}
    assert res.status_code == 200, res.text
    assert (res.json()["letters"], again.json()["letters"]) == (1, 0)
    [letter] = await letters(there)
    assert (letter["read"], letter["popped"]) == (True, True)
    opened = (await read(there, letter["id"])).json()
    assert opened["voice"] is None
    assert opened["salutation"] == "プロデューサーさんへ"
    assert unzipped(await export(there, "letters"))[2]["letters"] == [kept]


async def test_letters_are_neither_exported_nor_imported_left_out(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    here = await a_letter_written(make_client, settings)
    there = there_of(make_client, settings, tmp_path)

    _, manifest, data = unzipped(await export(here, "conversations"))
    res = await import_package(there, letters_package(packaged()), "conversations")

    assert "letters" not in manifest["categories"] and "letters" not in data
    assert res.json()["letters"] == 0
    assert await letters(there) == []


async def test_one_already_here_is_not_added_again_nor_one_of_a_character_not_here(
    make_client: ClientFactory, settings: Settings
):
    """Another she wrote the same day, elsewhere, is no less hers."""
    client = await a_letter_written(make_client, settings)
    [here] = unzipped(await export(client, "letters"))[2]["letters"]

    res = await import_package(client, letters_package(
        here,
        packaged(character_id="nobody", character_name="谁"),
        packaged(salutation="もう一通", written_at="2026-09-25T15:00:00.000000+00:00"),
    ), "letters")

    assert res.status_code == 200, res.text
    assert (res.json()["letters"], res.json()["letters_skipped"]) == (1, 1)
    assert [each["day"] for each in await letters(client)] == ["2026-09-25"] * 2


async def test_a_letter_on_an_occasion_twice_is_refused(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM())

    res = await import_package(
        client, letters_package(packaged(occasions=["close", "close"])), "letters"
    )

    assert (res.status_code, res.json()["code"]) == (422, "broken")
    assert await letters(client) == []


async def test_a_package_of_a_format_before_them_with_letters_is_refused(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM())

    res = await import_package(client, letters_package(packaged(), format=4), "letters")

    assert (res.status_code, res.json()["code"]) == (422, "broken")
    assert await letters(client) == []
