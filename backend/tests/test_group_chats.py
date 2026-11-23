"""Group chats through HTTP, with scripted models and temporary databases."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from eidolon.config import Settings
from eidolon.affinity import affinity_prompt
from eidolon.judge import SAID
from eidolon.llm import LLMError
from tests.conftest import CharacterConfig, ClientFactory
from tests.fakes import (
    FAKE_JUDGE, START, FakeBackgroundLLM, FakeClock, FakeEmbedder, FakeJudge, FakeLLM,
    FakeImageService, FakeLLMService, FakeReranker, context_of,
)
from tests.test_affinity import keep
from tests.test_own_llm import LLM as OWN_LLM
from tests.test_data_package import export, import_package, other_deployment, package_of, unzipped
from tests.test_photos import IMAGE, REPLY as PHOTO_REPLY, asking, settled
from tests.test_rate_limit import limited, post_turn
from tests.test_retrieval import PARK_LINE, embedder  # noqa: F401 - fixture
from tests.test_shared_memory import CAT, memorized, turn
from tests.test_text_turn import disconnect_after, start_conversation

MEMBERS = ["mano", "kogane"]
MANO = ["<ja>一緒に歌おう。</ja>", "<zh>一起唱吧。</zh>"]
KOGANE = ["<ja>よかよ！</ja><zh>好呀！</zh>"]
HIORI = ["<ja>しっかり準備したよ。</ja><zh>好好准备过了。</zh>"]


async def create(client: httpx.AsyncClient, members: list[str] | None = None) -> str:
    response = await client.post("/api/group-chats", json={"member_ids": members or MEMBERS})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def body(mention: str | None = None, text: str = "一起聊聊？") -> dict:
    return {"text": text, "mention_id": mention, "request_id": uuid4().hex}


async def events(client: httpx.AsyncClient, group: str, payload: dict) -> list[dict]:
    res = await client.post(f"/api/group-chats/{group}/turns", json=payload)
    assert res.status_code == 200, res.text
    return [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]


async def detail(client: httpx.AsyncClient, group: str) -> dict:
    res = await client.get(f"/api/group-chats/{group}")
    assert res.status_code == 200
    return res.json()


async def test_membership_validation_and_owner_scope(make_client: ClientFactory):
    client = make_client(FakeLLM())
    for members in [["mano"], ["mano", "mano"], ["mano", "missing"], ["mano", "hazuki"]]:
        res = await client.post("/api/group-chats", json={"member_ids": members})
        assert res.status_code == 422
    group = await create(client, MEMBERS[::-1])
    assert (await detail(client, group))["member_ids"] == MEMBERS[::-1]
    other = make_client(FakeLLM(), "other")
    assert (await other.get("/api/group-chats")).json() == []
    for method, path in [("GET", ""), ("DELETE", ""), ("POST", "/turns")]:
        res = await other.request(method, f"/api/group-chats/{group}{path}", json=body())
        assert res.status_code == 404


async def test_automatic_and_mentioned_replies_are_attributed_and_replayed(
    make_client: ClientFactory, settings: Settings,
):
    llm = FakeLLM(MANO, KOGANE, KOGANE, MANO, tokens=(30, 10))
    client = make_client(llm)
    group = await create(client)
    first = body()
    sent = await events(client, group, first)
    assert [e["message"]["character_id"] for e in sent if e["type"] == "message"] == [None, *MEMBERS]
    assert [e["character_id"] for e in sent if e["type"] == "delta"][0] == "mano"
    assert "一緒に歌おう。" in llm.prompts[1][-1].content
    assert "制作人" in llm.prompts[1][-1].content
    assert "月岡恋鐘" in llm.prompts[1][0].content

    before = await detail(client, group)
    replay = await events(client, group, first)
    assert [e["message"] for e in replay if e["type"] == "message"] == before["turns"][0]["messages"]
    assert len(llm.prompts) == 2
    conflicting = await client.post(f"/api/group-chats/{group}/turns", json=first | {"text": "别的消息"})
    assert conflicting.status_code == 409

    explicit = await events(client, group, body("kogane"))
    assert [e["message"]["character_id"] for e in explicit if e["type"] == "message"] == [None, "kogane", "mano"]
    assert explicit[-1]["status"] == "done"
    assert len(llm.prompts) == 4
    assert (await detail(client, group))["turns"][-1]["mentioned_ids"] == ["kogane"]
    assert len((await client.get("/api/conversations/latest")).json()) == 0
    with sqlite3.connect(settings.database_path) as db:
        records = db.execute(
            "SELECT id,time_to_speak_ms,duration_s FROM activity_records WHERE kind='group_turn' ORDER BY id"
        ).fetchall()
        assert len(records) == 2 and records[0][1] >= 0 and records[0][2] >= 0
        assert db.execute("SELECT count(*) FROM service_calls WHERE record_id=?", (records[0][0],)).fetchone() == (2,)


async def test_group_replies_use_each_members_affinity_without_changing_it(
    make_client: ClientFactory, settings: Settings,
):
    llm = FakeLLM(MANO, KOGANE)
    settings = replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)
    client = make_client(llm, settings=settings, clock=FakeClock())
    keep(settings.database_path, "producer", 85, START)
    keep(settings.database_path, "producer", 65, START, character="kogane")
    group = await create(client)
    await events(client, group, body())
    assert affinity_prompt("intimate", None)[0] in llm.prompts[0][0].content
    assert affinity_prompt("close", None)[0] in llm.prompts[1][0].content
    with sqlite3.connect(settings.database_path) as db:
        values = dict(db.execute("SELECT character_id, value FROM affinities"))
    assert values == {"mano": 85, "kogane": 65}


async def test_quote_targets_author_checks_scope_and_survives_replay(make_client: ClientFactory):
    llm = FakeLLM(MANO, KOGANE, ["none"], MANO)
    client = make_client(llm)
    group = await create(client)
    first = await events(client, group, body())
    original = next(e["message"] for e in first if e["type"] == "message" and e["message"]["character_id"] == "mano")
    payload = body(text="你刚才是什么意思？") | {"reply_to": original["id"]}
    result = await events(client, group, payload)
    assert [e["message"]["character_id"] for e in result if e["type"] == "message"] == [None, "mano"]
    assert "一緒に歌おう。" in llm.prompts[-1][-1].content
    assert (await detail(client, group))["turns"][-1]["messages"][0]["reply_to"] == original["id"]
    assert (await events(client, group, payload))[0]["message"]["reply_to"] == original["id"]
    other_group = await create(client, ["kogane", "mano"])
    response = await client.post(f"/api/group-chats/{other_group}/turns", json=body() | {"reply_to": original["id"]})
    assert response.status_code == 422


async def test_quote_survives_package_with_remapped_ids(make_client: ClientFactory, settings: Settings, tmp_path: Path):
    client = make_client(FakeLLM(MANO, KOGANE, ["none"], MANO))
    group = await create(client)
    first = await events(client, group, body())
    quoted = next(e["message"] for e in first if e["type"] == "message" and e["message"]["character_id"] == "mano")
    await events(client, group, body(text="什么意思？") | {"reply_to": quoted["id"]})
    package = await export(client, "conversations")
    other = other_deployment(make_client, settings, tmp_path)
    imported = await import_package(other, package, "conversations")
    assert imported.status_code == 200, imported.text
    group_id = (await other.get("/api/group-chats")).json()[0]["id"]
    turns = (await detail(other, group_id))["turns"]
    assert turns[1]["messages"][0]["reply_to"] == turns[0]["messages"][1]["id"]
    assert turns[1]["messages"][0]["reply_to"] != quoted["id"]


async def test_multiple_mentions_answer_independently_before_the_others(
    make_client: ClientFactory, characters: CharacterConfig,
):
    characters.add("hiori", "風野灯織", "灯織は真面目なアイドル。", short_name="灯織")
    llm = FakeLLM(HIORI, MANO, KOGANE)
    client = make_client(llm)
    group = await create(client, [*MEMBERS, "hiori"])
    payload = {"text": "你们分别说说排练怎么样？", "mention_ids": ["hiori", "mano"], "request_id": uuid4().hex}
    sent = await events(client, group, payload)
    assert [e["message"]["character_id"] for e in sent if e["type"] == "message"] == [
        None, "hiori", "mano", "kogane",
    ]
    first, second, reaction = [json.loads(prompt[-1].content) for prompt in llm.prompts]
    assert first == second
    assert [line["speaker"] for line in reaction["messages"]] == ["制作人", "風野灯織", "櫻木真乃"]
    assert (await detail(client, group))["turns"][0]["mentioned_ids"] == ["hiori", "mano"]


async def test_clear_address_is_a_mention_and_a_reference_stays_group_wide(make_client: ClientFactory):
    client = make_client(FakeLLM(KOGANE, MANO, MANO, KOGANE))
    group = await create(client)
    named = await events(client, group, body(text="月岡恋鐘，你觉得呢？"))
    assert named[0]["mentioned_ids"] == ["kogane"]
    assert [e["message"]["character_id"] for e in named if e["type"] == "message"] == [None, "kogane", "mano"]
    casual = await events(client, group, body(text="我觉得月岡恋鐘今天表现很好。"))
    assert casual[0]["mentioned_ids"] == []
    assert [e["message"]["character_id"] for e in casual if e["type"] == "message"] == [None, *MEMBERS]


async def test_photo_belongs_to_the_addressed_speaker_and_moves_with_the_group(
    make_client: ClientFactory, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    image = FakeImageService()
    image.gate = asyncio.Event()
    llm = FakeLLM(['{"members":["mano"]}'], PHOTO_REPLY, PHOTO_REPLY)
    configured = replace(settings, image=IMAGE, judge=FAKE_JUDGE)
    clients = asking()
    answer = clients.judge.answer

    async def judge_photo_clause(key, state, questions):
        clients.judge.photo = float("自拍" in state[SAID])
        return await answer(key, state, questions)

    monkeypatch.setattr(clients.judge, "answer", judge_photo_clause)
    client = make_client(llm, settings=configured, clients=clients, image_transport=image.transport())
    group = await create(client)
    sent = await events(client, group, body(text="@月岡恋鐘 说说排练，@櫻木真乃 发张自拍"))
    replies = [e["message"] for e in sent if e["type"] == "message"][1:]
    assert [m["character_id"] for m in replies] == ["kogane", "mano"]
    assert replies[0]["photo"] is None
    photo = replies[1]["photo"]
    assert photo["status"] == "pending"
    assert (await detail(client, group))["turns"][0]["messages"][2]["photo"] == photo
    photo_id = photo["id"]
    image.gate.set()
    assert (await settled(client, photo_id))["status"] == "ready"
    assert len(image.requests) == 1
    picture = await client.get(f"/api/photos/{photo_id}/image")
    assert picture.status_code == 200 and picture.headers["content-type"] == "image/webp"
    other_user = make_client(FakeLLM(), user="another")
    for suffix in ("", "/image"):
        assert (await other_user.get(f"/api/photos/{photo_id}{suffix}")).status_code == 404

    package = await export(client, "conversations,photos")
    imported = other_deployment(make_client, settings, tmp_path)
    response = await import_package(imported, package, "conversations,photos")
    assert response.status_code == 200, response.text
    assert response.json()["photos"] == 1
    restored_id = (await imported.get("/api/group-chats")).json()[0]["id"]
    restored_photo = (await detail(imported, restored_id))["turns"][0]["messages"][2]["photo"]
    assert restored_photo["id"] != photo_id and restored_photo["status"] == "ready"
    assert (await imported.get(f"/api/photos/{restored_photo['id']}/image")).content == picture.content

    assert (await client.delete(f"/api/group-chats/{group}")).status_code == 204
    assert (await client.get(f"/api/photos/{photo_id}/image")).status_code == 404


async def test_group_photo_uses_distinct_character_prompts(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig,
):
    characters.add("hiori", "風野灯織", "灯織。", photo=("kazano hiori", "black hair, blue eyes"))
    image = FakeImageService()
    llm = FakeLLM(['{"members":null}'], PHOTO_REPLY, HIORI)
    client = make_client(llm, settings=replace(settings, image=IMAGE, judge=FAKE_JUDGE),
                         clients=asking(), image_transport=image.transport())
    group = await create(client, ["mano", "hiori"])
    sent = await events(client, group, body(text="给我拍一张大家的合照"))
    photo = next(e["message"]["photo"] for e in sent if e["type"] == "message" and e["message"]["photo"])
    assert (await settled(client, photo["id"]))["status"] == "ready"
    body_sent = image.requests[0]["body"]
    captions = body_sent["parameters"]["v4_prompt"]["caption"]["char_captions"]
    assert len(captions) == 2
    assert "sakuragi mano" in captions[0]["char_caption"]
    assert "kazano hiori" in captions[1]["char_caption"]
    assert "2girls" in body_sent["input"] and "solo" not in body_sent["input"]


async def test_group_photo_can_be_sent_by_someone_outside_the_requested_cast(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig,
):
    characters.add("hiori", "風野灯織", "灯織。", photo=("kazano hiori", "black hair, blue eyes"))
    image = FakeImageService()
    llm = FakeLLM(['{"members":["mano","hiori"]}'], PHOTO_REPLY, MANO, HIORI)
    client = make_client(llm, settings=replace(settings, image=IMAGE, judge=FAKE_JUDGE),
                         clients=asking(), image_transport=image.transport())
    group = await create(client, ["mano", "kogane", "hiori"])
    sent = await events(client, group, body(text="@月岡恋鐘 发一张真乃和灯織的合照"))
    [sender] = [e["message"] for e in sent if e["type"] == "message" and e["message"]["photo"]]
    assert sender["character_id"] == "kogane"
    assert (await settled(client, sender["photo"]["id"]))["status"] == "ready"
    captions = image.requests[0]["body"]["parameters"]["v4_prompt"]["caption"]["char_captions"]
    assert len(captions) == 2
    assert "sakuragi mano" in captions[0]["char_caption"]
    assert "kazano hiori" in captions[1]["char_caption"]


async def test_group_photo_can_show_an_object_without_people(make_client: ClientFactory, settings: Settings):
    image = FakeImageService()
    tagged = [PHOTO_REPLY[0].replace("peace sign, smile", "bouquet"), PHOTO_REPLY[1]]
    client = make_client(FakeLLM(['{"subject":"object"}'], tagged, MANO),
                         settings=replace(settings, image=IMAGE, judge=FAKE_JUDGE),
                         clients=asking(), image_transport=image.transport())
    group = await create(client)
    sent = await events(client, group, body("kogane", "只拍桌上的花束"))
    [photo] = [e["message"]["photo"] for e in sent if e["type"] == "message" and e["message"]["photo"]]
    assert (await settled(client, photo["id"]))["status"] == "ready"
    request = image.requests[0]["body"]
    assert "bouquet" in request["input"] and "no humans" in request["input"]
    assert request["parameters"]["v4_prompt"]["caption"]["char_captions"] == []


@pytest.mark.parametrize("image_settings", [
    replace(IMAGE, api_format="openai"),
    replace(IMAGE, model="nai-diffusion-3"),
])
async def test_group_photo_requires_a_compatible_service(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig, image_settings,
):
    characters.add("hiori", "風野灯織", "灯織。", photo=("kazano hiori", "black hair, blue eyes"))
    image = FakeImageService()
    llm = FakeLLM(['{"members":null}'], PHOTO_REPLY, HIORI)
    client = make_client(llm, settings=replace(settings, image=image_settings, judge=FAKE_JUDGE),
                         clients=asking(), image_transport=image.transport())
    group = await create(client, ["mano", "hiori"])
    sent = await events(client, group, body(text="拍张大家的合照"))
    assert sent[-1]["status"] == "done" and "不支持多人合照" in sent[-1]["detail"]
    assert not image.requests


async def test_group_photo_missing_appearance_is_explained_without_generating(
    make_client: ClientFactory, settings: Settings,
):
    image = FakeImageService()
    llm = FakeLLM(['{"members":null}'], MANO, KOGANE)
    client = make_client(llm, settings=replace(settings, image=IMAGE, judge=FAKE_JUDGE),
                         clients=asking(), image_transport=image.transport())
    group = await create(client)
    sent = await events(client, group, body(text="拍张大家的合照"))
    assert "外貌资料" in sent[-1]["detail"]
    assert not image.requests


async def test_failure_preserves_committed_messages_and_refunds_only_when_none_was_saved(
    make_client: ClientFactory, settings: Settings,
):
    broken = LLMError("provider unavailable")
    llm = FakeLLM(broken, broken, ["<ja>途中", broken], KOGANE)
    client = make_client(llm, settings=limited(settings, turns=1))
    group = await create(client)
    failed = await events(client, group, body("mano"))
    assert failed[-1]["status"] == "failed"
    partial = await events(client, group, body())
    assert partial[-1]["status"] == "interrupted"
    turns = (await detail(client, group))["turns"]
    assert [[m["character_id"] for m in t["messages"]] for t in turns] == [[None], [None, "kogane"]]
    assert (await post_turn(client, await start_conversation(client))).status_code == 429


async def test_only_one_send_and_no_deletion_during_generation(make_client: ClientFactory):
    gate = asyncio.Event()
    llm = FakeLLM([gate, *MANO], KOGANE)
    client = make_client(llm)
    group = await create(client)
    payload = body("mano")
    sending = asyncio.create_task(events(client, group, payload))
    try:
        async with asyncio.timeout(5):
            while not llm.prompts:
                await asyncio.sleep(0.01)
        assert (await client.delete(f"/api/group-chats/{group}")).status_code == 409
        for attempt in [payload, body("kogane")]:
            assert (await client.post(f"/api/group-chats/{group}/turns", json=attempt)).status_code == 409
    finally:
        gate.set()
        await sending
    assert (await client.delete(f"/api/group-chats/{group}")).status_code == 204
    assert (await client.get(f"/api/group-chats/{group}")).status_code == 404


async def test_disconnect_drops_only_the_uncommitted_reply(make_client: ClientFactory):
    never = asyncio.Event()
    client = make_client(FakeLLM(["<ja>途中", never], KOGANE, MANO))
    group = await create(client)
    path = f"/api/group-chats/{group}/turns"
    sent = await disconnect_after(client, path, body("mano"), "delta")
    assert sent[-1]["type"] == "delta"
    old = (await detail(client, group))["turns"][0]
    assert old["status"] == "interrupted" and len(old["messages"]) == 1
    assert (await events(client, group, body("kogane")))[-1]["status"] == "done"


async def test_shared_memory_is_read_as_hearsay_and_group_turns_do_not_write_it(make_client: ClientFactory):
    llm = FakeLLM(MANO, KOGANE, MANO)
    background = FakeBackgroundLLM(memorized(CAT))
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="memories"), embedder=FakeEmbedder(),
        reranker=FakeReranker("猫"), background_llm=background,
    )
    await turn(client, await start_conversation(client), "我家的猫叫小白")
    group = await create(client)
    assert (await events(client, group, body("kogane", "你知道我家的猫吗？")))[-1]["status"] == "done"
    model_context = context_of(llm.prompts[1])
    line = next(line for line in model_context.splitlines() if CAT in line)
    assert "櫻木真乃" in line and "转述" in line
    await client.get("/api/memories")
    assert len(background.prompts) == 1


async def test_story_retrieval_uses_the_speaker_cast(make_client: ClientFactory, embedder: FakeEmbedder):
    llm = FakeLLM(MANO, KOGANE)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker(PARK_LINE))
    group = await create(client)
    await events(client, group, body(text="公園の鳩のこと覚えてる？"))
    assert PARK_LINE in context_of(llm.prompts[0])
    assert PARK_LINE not in context_of(llm.prompts[1])


async def test_cooling_members_are_excluded_before_admission(
    make_client: ClientFactory, settings: Settings,
):
    llm = FakeLLM(KOGANE)
    settings = replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)
    client = make_client(llm, settings=settings, clock=FakeClock())
    group = await create(client)
    keep(settings.database_path, "producer", 0, START, cooling=True)
    automatic = await events(client, group, body("mano"))
    assert [e["message"]["character_id"] for e in automatic if e["type"] == "message"] == [None, "kogane"]
    keep(settings.database_path, "producer", 0, START, character="kogane", cooling=True)
    refused = await client.post(f"/api/group-chats/{group}/turns", json=body())
    assert refused.status_code == 422
    assert len(llm.prompts) == 1


async def test_own_llm_covers_both_replies_without_using_the_limit(
    make_client: ClientFactory, settings: Settings, own_llm: FakeLLMService,
):
    deployer = FakeLLM()
    own_llm.llm = FakeLLM(MANO, KOGANE)
    client = make_client(deployer, settings=limited(settings, turns=0))
    group = await create(client)
    sent = await events(client, group, body() | {"own_keys": {"llm": OWN_LLM}})
    assert sent[-1]["status"] == "done"
    assert len(own_llm.llm.prompts) == 2 and not deployer.prompts


async def test_reopening_the_database_marks_a_running_round_interrupted(
    make_client: ClientFactory, settings: Settings,
):
    first = make_client(FakeLLM(MANO, KOGANE))
    group = await create(first)
    payload = body("mano")
    await events(first, group, payload)
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE group_chat_turns SET status='running' WHERE group_id=?", (group,))
    restarted = make_client(FakeLLM(KOGANE, MANO))
    assert (await detail(restarted, group))["turns"][0]["status"] == "interrupted"
    assert (await events(restarted, group, body("kogane")))[-1]["status"] == "done"


async def test_group_data_package_round_trip_and_account_deletion(
    make_client: ClientFactory, settings: Settings, tmp_path: Path,
):
    client = make_client(FakeLLM(KOGANE, MANO))
    background = {"place": "后台休息室", "premise": "刚散场"}
    created = await client.post("/api/group-chats", json={"member_ids": MEMBERS, "name": "  后台组  ", "background": background})
    group = created.json()["id"]
    await events(client, group, body("kogane"))
    package = await export(client, "conversations")
    _, manifest, data = unzipped(package)
    assert manifest["format"] == 11 and manifest["counts"]["conversations"] == 1
    assert data["group_chats"][0]["members"] == MEMBERS

    other = other_deployment(make_client, settings, tmp_path)
    imported = await import_package(other, package, "conversations")
    assert imported.status_code == 200, imported.text
    assert imported.json()["group_chats"] == 1
    imported_group = (await other.get("/api/group-chats")).json()[0]["id"]
    assert imported_group != group
    restored_group = await detail(other, imported_group)
    assert restored_group["name"] == created.json()["name"] == "后台组"
    assert restored_group["background"] == created.json()["background"]
    restored = restored_group["turns"][0]
    assert [m["character_id"] for m in restored["messages"]] == [None, "kogane", "mano"]
    assert restored["messages"][1]["segments"] == [{"ja": "よかよ！", "zh": "好呀！"}]
    again = await import_package(other, package, "conversations")
    assert again.status_code == 200 and not again.json().get("group_chats")
    # Format 9 has the same group records without the opening background.
    del data["group_chats"][0]["name"]
    del data["group_chats"][0]["background"]
    legacy = other_deployment(make_client, settings, tmp_path, user="legacy")
    old = await import_package(legacy, package_of({**manifest, "format": 9}, data), "conversations")
    assert old.status_code == 200 and old.json()["group_chats"] == 1
    assert (await legacy.get("/api/group-chats")).json()[0]["background"] is None

    missing_path = tmp_path / "missing-characters"
    missing_path.mkdir()
    config = missing_path / "characters.toml"
    original = settings.characters_path.read_text(encoding="utf-8")
    config.write_text(original.split("[[characters]]")[0] + "[[characters]]" +
                      original.split("[[characters]]")[1], encoding="utf-8")
    # Keep the source directory for its assets; only the TOML roster is reduced.
    for path in settings.characters_path.parent.rglob("*"):
        if path.is_file() and path != settings.characters_path:
            target = missing_path / path.relative_to(settings.characters_path.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    missing = make_client(FakeLLM(), settings=replace(settings, characters_path=config,
                                                     database_path=tmp_path / "missing.db"))
    skipped = await import_package(missing, package, "conversations")
    assert skipped.status_code == 200 and skipped.json()["group_chats_skipped"] == 1

    assert (await client.post("/api/account/delete", json={"confirm": True})).status_code == 204
    with sqlite3.connect(settings.database_path) as db:
        for table in ("group_chats", "group_chat_members", "group_chat_turns", "group_chat_messages"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


async def test_new_group_name_defaults_to_ordered_names_and_rejects_blanks(make_client: ClientFactory):
    client = make_client(FakeLLM())
    group = await create(client, ["kogane", "mano"])
    assert (await detail(client, group))["name"] == "月岡恋鐘、櫻木真乃"
    invalid = await client.post("/api/group-chats", json={"member_ids": MEMBERS, "name": "  "})
    assert invalid.status_code == 422
    groups = (await client.get("/api/group-chats")).json()
    assert [(item["id"], item["name"]) for item in groups] == [(group, "月岡恋鐘、櫻木真乃")]
