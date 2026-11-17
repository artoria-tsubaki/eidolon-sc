"""Text conversation turns, driven through the backend's external API."""

import asyncio
import json

import httpx

from eidolon.llm import ChatMessage
from eidolon.reply_format import FORMAT_INSTRUCTIONS
from tests.conftest import MANO_SHEET, ClientFactory
from tests.fakes import FakeLLM, system_of


async def start_conversation(client: httpx.AsyncClient, character: str = "mano") -> str:
    res = await client.post("/api/conversations", json={"character_id": character})
    assert res.status_code == 201
    return res.json()["id"]


async def send_turn(
    client: httpx.AsyncClient,
    conversation_id: str,
    text: str,
    own_llm: dict | None = None,
    *,
    own_keys: dict | None = None,
    read_aloud_model: str | None = None,
) -> list[dict]:
    """Send a text turn, bringing the user's `own_keys` if given, or else their `own_llm`
    alone (as `own_keys`, which turns and calls bring alike), and the model of their own key
    for speech synthesis if given (`read_aloud_model`), and collect the server-sent events it
    streams back."""
    events: list[dict] = []
    own_keys = own_keys or ({"llm": own_llm} if own_llm else None)
    body = {"text": text} | ({"own_keys": own_keys} if own_keys else {}) | (
        {"read_aloud_model": read_aloud_model} if read_aloud_model else {}
    )
    async with client.stream(
        "POST", f"/api/conversations/{conversation_id}/turns", json=body
    ) as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        async for line in res.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
    return events


async def leave_turn(
    client: httpx.AsyncClient, conversation_id: str, text: str, *, after: str
) -> list[dict]:
    """Send a text turn and leave, closing the page, as soon as an event of type `after`
    arrives; the events that came by then.

    Closing `client.stream(...)` early never reaches the app: httpx's ASGITransport runs
    the app to the end first. So this drives the app as uvicorn does, on ASGI spec 2.3,
    where the streaming response listens for `http.disconnect` and cancels the stream."""
    return await disconnect_after(
        client, f"/api/conversations/{conversation_id}/turns", {"text": text}, after,
    )


async def disconnect_after(client: httpx.AsyncClient, path: str, body: dict, after: str) -> list[dict]:
    """Drive the stream at the ASGI boundary and disconnect after the designated event."""
    request = client.build_request("POST", path, json=body)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": request.method,
        "scheme": "http",
        "path": request.url.path,
        "raw_path": request.url.raw_path,
        "query_string": b"",
        "headers": [(name.lower(), value) for name, value in request.headers.raw],
        "server": ("test", 80),
        "client": ("127.0.0.1", 50000),
        "root_path": "",
    }
    events: list[dict] = []
    left = asyncio.Event()
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": request.content, "more_body": False}
        await left.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            assert message["status"] == 200
        for line in message.get("body", b"").decode().splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line.removeprefix("data: ")))
                if events[-1]["type"] == after:
                    left.set()

    app = client._transport.app  # pyright: ignore[reportAttributeAccessIssue]
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    return events


async def test_reply_streams_japanese_and_chinese_subtitle(make_client: ClientFactory):
    llm = FakeLLM(["<ja>こんにちは、プロデューサーさん！</ja><zh>你好，制作人！</zh>"])
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "你好呀")

    assert events[0] == {"type": "user", "text": "你好呀"}
    done = events[-1]
    assert done["type"] == "done"
    assert done["message"]["ja"] == "こんにちは、プロデューサーさん！"
    assert done["message"]["zh"] == "你好，制作人！"


def deltas(events: list[dict], lang: str) -> list[tuple[int, str]]:
    return [(e["segment"], e["text"]) for e in events if e["type"] == "delta" and e["lang"] == lang]


async def test_reply_is_streamed_segment_by_segment_even_when_tags_are_split(
    make_client: ClientFactory,
):
    llm = FakeLLM(
        ["<j", "a>ほわっ、", "プロデューサーさん", "！</", "ja><zh>哇，制作人！", "</zh>\n",
         "<ja>今日は</ja><", "zh>今天</zh>"]
    )
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "こんにちは")

    assert deltas(events, "ja") == [(0, "ほわっ、"), (0, "プロデューサーさん"), (0, "！"), (1, "今日は")]
    assert deltas(events, "zh") == [(0, "哇，制作人！"), (1, "今天")]
    assert events[-1]["message"]["ja"] == "ほわっ、プロデューサーさん！今日は"
    assert events[-1]["message"]["zh"] == "哇，制作人！今天"


async def test_reply_that_ignores_the_format_is_kept_as_japanese(make_client: ClientFactory):
    client = make_client(FakeLLM(["えっと、", "ありがとうございますっ"]))
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "谢谢")

    assert events[-1]["message"]["ja"] == "えっと、ありがとうございますっ"
    assert events[-1]["message"]["zh"] == ""


async def test_both_sides_of_a_turn_are_kept_in_the_conversation_record(
    make_client: ClientFactory,
):
    client = make_client(
        FakeLLM(["<ja>おはようございます！</ja><zh>早上好！</zh><ja>ほわっ</ja><zh>哇</zh>"])
    )
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "早上好")

    reopened = make_client(FakeLLM())
    listed = (await reopened.get("/api/conversations", params={"character_id": "mano"})).json()
    messages = (await reopened.get(f"/api/conversations/{conversation_id}/messages")).json()

    assert [c["id"] for c in listed] == [conversation_id]
    assert [(m["role"], m["text"], m["ja"], m["zh"]) for m in messages] == [
        ("user", "早上好", None, None),
        ("character", None, "おはようございます！ほわっ", "早上好！哇"),
    ]
    assert messages[1]["segments"] == [
        {"ja": "おはようございます！", "zh": "早上好！"},
        {"ja": "ほわっ", "zh": "哇"},
    ]


async def test_prompt_carries_the_character_sheet_and_the_conversation_so_far(
    make_client: ClientFactory,
):
    llm = FakeLLM(
        ["<ja>ピーちゃんです！</ja><zh>是小皮！</zh>"],
        ["<ja>はいっ</ja><zh>嗯！</zh>"],
    )
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    await send_turn(client, conversation_id, "这只鸽子叫什么？")
    await send_turn(client, conversation_id, "好可爱")

    # Without a world setting, the part every user's turns with her share is her sheet, her
    # birthday and how she is to format her reply.
    assert llm.prompts[1][0] == ChatMessage(
        "system", f"{MANO_SHEET}\n\n你的生日是4月25日。\n\n{FORMAT_INSTRUCTIONS}",
        cache_breakpoint=True,
    )
    system = system_of(llm.prompts[1])
    assert "櫻木真乃" in system
    assert "<ja>" in system and "<zh>" in system
    assert [(m.role, m.content) for m in llm.prompts[1] if m.role != "system"] == [
        ("user", "这只鸽子叫什么？"),
        ("assistant", "<ja>ピーちゃんです！</ja><zh>是小皮！</zh>"),
        ("user", "好可爱"),
    ]


async def test_conversations_are_kept_apart(make_client: ClientFactory):
    llm = FakeLLM(["<ja>一</ja><zh>一</zh>"], ["<ja>二</ja><zh>二</zh>"])
    client = make_client(llm)
    first = await start_conversation(client)
    second = await start_conversation(client)

    await send_turn(client, first, "第一次")
    await send_turn(client, second, "第二次")

    assert [(m.role, m.content) for m in llm.prompts[1] if m.role != "system"] == [
        ("user", "第二次")
    ]
    listed = (await client.get("/api/conversations", params={"character_id": "mano"})).json()
    assert [c["id"] for c in listed] == [second, first]


async def test_unknown_character_or_conversation_is_rejected(make_client: ClientFactory):
    client = make_client(FakeLLM())

    res = await client.post("/api/conversations", json={"character_id": "nobody"})
    assert res.status_code == 404
    res = await client.post("/api/conversations/missing/turns", json={"text": "hi"})
    assert res.status_code == 404


async def test_reply_cut_off_inside_a_closing_tag_keeps_only_the_text(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM(["<ja>はいっ</ja><zh>好的</z"]))
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "好吗")

    assert events[-1]["message"]["segments"] == [{"ja": "はいっ", "zh": "好的"}]
