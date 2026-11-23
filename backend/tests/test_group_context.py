"""What each group speaker knows about her current circumstances."""

import json
import sqlite3
from dataclasses import replace

import pytest

from eidolon.config import Settings
from eidolon.cycles import IN_GROUP, PERIOD_EARLY, PREMENSTRUAL_NEAR
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeLLM, context_of
from tests.test_cycles_switch import switch
from tests.test_group_chats import KOGANE, MANO, body, create, events
from tests.test_her_days import SHANGHAI, a_noon_when, in_period, on_a_day, premenstrual
from tests.test_her_now import scheduling
from tests.test_text_turn import send_turn, start_conversation


async def test_private_and_group_turns_keep_their_own_scene(make_client: ClientFactory):
    at_home = "<ja>家で休んでるよ。</ja><zh>我在家休息。</zh>"
    in_class = "<ja>まだ授業中だよ。</ja><zh>我还在上课。</zh>"
    llm = FakeLLM([at_home], [in_class], KOGANE, MANO)
    client = make_client(llm)
    private = await start_conversation(client)
    await send_turn(client, private, "现在在做什么？")

    sent = await events(client, await create(client), body("mano", "现在在做什么？"))
    assert sent[-1]["status"] == "done"
    assert "我还在上课。" in str(sent)
    assert all("在家休息" not in message.content for message in llm.prompts[1])

    await send_turn(client, private, "现在呢？")
    assert all("授業中" not in message.content for message in llm.prompts[3])


@pytest.mark.parametrize(("wanted", "feeling"), [
    (in_period(early=True), PERIOD_EARLY), (premenstrual(near=True), PREMENSTRUAL_NEAR),
    (lambda day: day is None, None),
])
async def test_group_cycle_tracks_private_cycle_and_user_switch(
    make_client: ClientFactory, settings: Settings, wanted, feeling,
):
    llm = FakeLLM(MANO, KOGANE, MANO, KOGANE, MANO)
    client = on_a_day(make_client, replace(settings, cycles=True), wanted, llm)
    group = await create(client)
    await events(client, group, body())
    with sqlite3.connect(settings.database_path) as db:
        assert not db.execute("SELECT 1 FROM conversation_events").fetchone()
    private = await start_conversation(client)
    await send_turn(client, private, "今天怎样？")
    if feeling:
        assert feeling in context_of(llm.prompts[0]) and feeling in context_of(llm.prompts[2])
    assert (IN_GROUP in context_of(llm.prompts[0])) == (feeling is not None)
    await switch(client, False)
    await events(client, group, body("kogane"))
    assert all(IN_GROUP not in context_of(prompt) for prompt in llm.prompts[3:])


async def test_group_cycle_is_absent_when_deployment_switch_is_off(
    make_client: ClientFactory, settings: Settings,
):
    llm = FakeLLM(MANO, KOGANE)
    client = on_a_day(make_client, replace(settings, cycles=True), in_period(early=True), llm,
                      deployment=replace(settings, cycles=False))
    await events(client, await create(client), body())
    assert IN_GROUP not in context_of(llm.prompts[0])
    assert PERIOD_EARLY not in context_of(llm.prompts[0])


async def test_group_reads_ongoing_job_without_settling_completed_job(
    make_client: ClientFactory, settings: Settings,
):
    settings = replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)
    _, clock = await scheduling(make_client, settings, ("排练", 1, 0, 0), ("访谈", 1, 0, 1))
    clock.advance(hours=12)
    llm = FakeLLM(MANO, KOGANE)
    client = make_client(llm, clock=clock, settings=settings)
    await events(client, await create(client), body())
    assert "你正在访谈" in context_of(llm.prompts[0])
    with sqlite3.connect(settings.database_path) as db:
        assert not db.execute("SELECT 1 FROM schedule_placements WHERE counted_at IS NOT NULL").fetchone()


async def test_group_background_by_member_and_group(make_client: ClientFactory, settings: Settings):
    _, clock = await scheduling(make_client, settings, ("访谈", 1, 0, 1))
    clock.advance(hours=12)
    llm = FakeLLM(MANO, KOGANE, MANO, KOGANE, MANO)
    client = make_client(llm, clock=clock, settings=settings)
    roster = ["mano", "kogane"]
    for member in roster:
        response = await client.post("/api/group-chats", json={
            "member_ids": roster, "background": {"members": {member: {"place": "  车站  ", "activity": "等车"}}},
        })
        assert response.status_code == 201
        group = response.json()
        assert group["background"]["members"] == {member: {"place": "车站", "activity": "等车"}}
        sent = await events(client, group["id"], body())
        assert sent[-1]["status"] == "done"
        for prompt in llm.prompts[-2:]:
            setting, = json.loads(prompt[-1].content)["开场背景"]["成员设定"]
            assert setting["地点"] == "车站"
            assert setting["成员"] == {"mano": "櫻木真乃", "kogane": "月岡恋鐘"}[member]
        assert ("你正在访谈" in context_of(llm.prompts[-2])) == (member != "mano")
    await send_turn(client, await start_conversation(client), "在做什么？")
    assert "等车" not in str(llm.prompts[-1])


async def test_fixed_story_date_and_real_timestamps(make_client: ClientFactory, settings: Settings):
    settings = replace(settings, cycles=True)
    make_client(FakeLLM())
    clock = FakeClock(a_noon_when(settings, lambda day: day is None))
    story = a_noon_when(settings, in_period(early=True)).astimezone(SHANGHAI)
    local = story.strftime("%Y-%m-%dT23:55")
    llm = FakeLLM(MANO, KOGANE, MANO, KOGANE, MANO, KOGANE)
    client = make_client(llm, clock=clock, settings=settings)
    group = (await client.post("/api/group-chats", json={
        "member_ids": ["mano", "kogane"], "background": {"time": {"local": local, "timezone": "Asia/Shanghai"}},
    })).json()
    for turn in range(3):
        if turn == 1:
            clock.advance(hours=24)
        if turn == 2:
            await switch(client, False)
        sent = await events(client, group["id"], body())
        assert sent[-1]["status"] == "done"
        assert sent[0]["message"]["created_at"].startswith(clock().strftime("%Y-%m-%d"))
        current = context_of(llm.prompts[-2])
        assert f"{story.year}年{story.month}月{story.day}日" in current and "23:55" in current
        assert (PERIOD_EARLY in current) == (turn < 2)


async def test_empty_background_and_invalid_settings(make_client: ClientFactory):
    client = make_client(FakeLLM())
    for background in ({"place": "  "}, {"members": {"mano": {"place": " "}}}):
        result = await client.post("/api/group-chats", json={"member_ids": ["mano", "kogane"], "background": background})
        assert result.status_code == 201 and result.json()["background"] is None
    for background in ({"place": "家", "members": {"mano": {"activity": "散步"}}},
                       {"members": {"other": {"place": "车站"}}}, {"premise": "甲" * 301},
                       {"time": {"local": "2026-11-01T09:00", "timezone": "Missing/Zone"}},
                       {"time": {"local": "2026-03-08T02:30", "timezone": "America/New_York"}}):
        result = await client.post("/api/group-chats", json={"member_ids": ["mano", "kogane"], "background": background})
        assert result.status_code == 422


async def test_existing_group_survives_background_column_migration(make_client: ClientFactory, settings: Settings):
    first = make_client(FakeLLM(MANO, KOGANE))
    group = await create(first)
    await events(first, group, body())
    # Reopen a database with the schema used before group backgrounds.
    with sqlite3.connect(settings.database_path) as db:
        db.execute("ALTER TABLE group_chats DROP COLUMN background")
    reopened = make_client(FakeLLM())
    detail = (await reopened.get(f"/api/group-chats/{group}")).json()
    assert detail["background"] is None
    assert detail["turns"][0]["messages"][1]["segments"][0]["ja"] == "一緒に歌おう。"
