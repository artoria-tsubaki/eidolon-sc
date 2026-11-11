"""User image decoding and multimodal wire formats."""
import base64
import io

import pytest
from PIL import Image

from eidolon.llm import ChatMessage, ImageContent
from eidolon.llm.anthropic import _messages as anthropic_messages
from eidolon.llm.openai_compat import _messages as openai_messages
from eidolon.llm.responses import _items
from eidolon.user_images import InvalidImage, normalize


def picture(fmt="PNG", size=(20, 10)):
    stream = io.BytesIO()
    image = Image.new("RGB", size, "red")
    image.save(stream, fmt)
    return stream.getvalue()


def test_normalized_pixels_have_bounded_size_and_no_metadata():
    data, width, height = normalize(picture(size=(3000, 1500)))
    assert (width, height) == (2560, 1280)
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.format == "WEBP"
        assert not decoded.getexif()


@pytest.mark.parametrize("data", [b"", b"not an image", picture("GIF"), b"x" * (10 * 1024 * 1024 + 1)], ids=["empty", "corrupt", "gif", "oversized"])
def test_invalid_images_are_rejected(data):
    with pytest.raises(InvalidImage):
        normalize(data)


def test_animated_png_is_rejected():
    stream = io.BytesIO()
    image = Image.new("RGB", (10, 10), "red")
    image.save(stream, "PNG", save_all=True, append_images=[Image.new("RGB", (10, 10), "blue")])
    with pytest.raises(InvalidImage):
        normalize(stream.getvalue())


def test_all_protocols_keep_caption_and_image_together():
    image = ImageContent("image/png", base64.b64encode(picture()).decode())
    message = ChatMessage("user", "今天自己做的", images=(image,))
    content = openai_messages([message])[0]["content"]
    assert content[0]["text"] == message.content
    assert content[1]["image_url"]["url"] == image.data_url
    content = _items(message)[0]["content"]
    assert content[0]["text"] == message.content
    assert content[1]["image_url"] == image.data_url
    content = anthropic_messages([message])[0]["content"]
    assert content[0]["text"] == message.content
    assert content[1]["source"]["data"] == image.data

async def test_image_caption_is_understood_and_recorded_once(make_client):
    import json
    from tests.fakes import FakeLLM, FakeLLMService
    from tests.test_text_turn import start_conversation
    vision = FakeLLM([json.dumps({"visible": "一盘料理", "text": "", "relevant": "正在讨论晚饭", "uncertain": "无法确认食材"})])
    chat = FakeLLM(["<ja>おいしそう！</ja><zh>看起来很好吃！</zh>"])
    client = make_client(chat, own_llm_service=FakeLLMService(vision))
    conversation = await start_conversation(client)
    upload = await client.post(f"/api/conversations/{conversation}/images", content=picture(), headers={"content-type": "image/png"})
    assert upload.status_code == 201
    image_id = upload.json()["id"]
    body = {"text": "今天自己做的", "image_id": image_id, "request_id": "test-request-00000001", "own_keys": {"vision_llm": {
        "base_url": "https://vision.example/v1", "api_key": "test-vision-key", "model": "vision", "protocol": "chat-completions"}}}
    reply = await client.post(f"/api/conversations/{conversation}/turns", json=body)
    events = [json.loads(line[6:]) for line in reply.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["type"] == "done", events
    assert vision.prompts[0][-1].images
    assert "一盘料理" in "\n".join(m.content for m in chat.prompts[0])
    again = await client.post(f"/api/conversations/{conversation}/turns", json=body)
    assert '"done"' in again.text
    assert len(vision.prompts) == 1
    history = (await client.get(f"/api/conversations/{conversation}/messages")).json()
    assert len(history) == 2
    assert history[0]["text"] == "今天自己做的"
    assert history[0]["user_image"]["id"] == image_id
    assert "image_observation" not in history[0]
    changed = await client.post(f"/api/conversations/{conversation}/turns", json=body | {"text": "另一句话"})
    assert changed.status_code == 409


async def test_upload_cannot_be_read_by_another_user(make_client):
    from tests.fakes import FakeLLM
    from tests.test_text_turn import start_conversation
    first = make_client(FakeLLM())
    second = make_client(FakeLLM(), user="someone-else")
    conversation = await start_conversation(first)
    upload = await first.post(f"/api/conversations/{conversation}/images", content=picture())
    url = f"/api/conversations/{conversation}/images/{upload.json()['id']}"
    assert (await second.get(url)).status_code == 404
    await first.delete(url)
    assert (await first.get(url)).status_code == 404


def test_image_memory_requires_real_user_evidence():
    from eidolon.memory import parse_memories
    assert parse_memories('{"memories":[{"text":"制作人养了猫","supersedes":[]}]}', evidence_in="好看吗") == []
    assert parse_memories('{"memories":[{"text":"制作人养了猫","evidence":"我养了猫","supersedes":[]}]}', evidence_in="好看吗") == []
    written = parse_memories('{"memories":[{"text":"制作人养了猫","evidence":"我养了猫","supersedes":[]}]}', evidence_in="我养了猫")
    assert written and written[0].text == "制作人养了猫"


def _seen(visible="一盘料理"):
    import json
    return json.dumps({"visible": visible, "text": "", "relevant": "晚饭", "uncertain": ""})


def _vision_keys():
    return {"vision_llm": {
        "base_url": "https://vision.example/v1", "api_key": "test-vision-key",
        "model": "vision", "protocol": "chat-completions",
    }}


def _events(reply):
    import json
    return [json.loads(line[6:]) for line in reply.text.splitlines() if line.startswith("data: ")]


async def _image_turn(client, conversation, image_id, text, request_id, *, referenced=False):
    body = {"text": text, "request_id": request_id, "own_keys": _vision_keys()}
    body["referenced_image_id" if referenced else "image_id"] = image_id
    reply = await client.post(f"/api/conversations/{conversation}/turns", json=body)
    events = _events(reply)
    assert events and events[-1]["type"] == "done", events
    return events


async def test_own_vision_failure_does_not_fall_back_or_record_the_turn(make_client):
    from eidolon.llm import LLMError
    from tests.fakes import FakeLLM, FakeLLMService
    from tests.test_text_turn import start_conversation
    vision = FakeLLM(LLMError("vision key rejected"))
    chat = FakeLLM(["<ja>見えない</ja><zh>没看见</zh>"])
    client = make_client(chat, own_llm_service=FakeLLMService(vision))
    conversation = await start_conversation(client)
    upload = await client.post(f"/api/conversations/{conversation}/images", content=picture())
    image_id = upload.json()["id"]
    reply = await client.post(f"/api/conversations/{conversation}/turns", json={
        "text": "今天自己做的", "image_id": image_id, "request_id": "test-request-00000001",
        "own_keys": _vision_keys(),
    })
    events = _events(reply)
    assert events[-1]["type"] == "error"
    assert "没看清" not in events[-1]["message"]
    assert chat.prompts == []
    assert len(vision.prompts) == 1
    assert (await client.get(f"/api/conversations/{conversation}/messages")).json() == []


async def test_an_older_picture_is_looked_at_again_for_a_new_question(make_client):
    from tests.fakes import FakeLLM, FakeLLMService
    from tests.test_text_turn import start_conversation
    vision = FakeLLM([_seen()], [_seen("深色酱汁")])
    chat = FakeLLM(
        ["<ja>おいしそう</ja><zh>看起来很好吃</zh>"],
        ["<ja>濃いね</ja><zh>颜色挺深的</zh>"],
    )
    client = make_client(chat, own_llm_service=FakeLLMService(vision))
    conversation = await start_conversation(client)
    upload = await client.post(f"/api/conversations/{conversation}/images", content=picture())
    image_id = upload.json()["id"]
    await _image_turn(client, conversation, image_id, "今天自己做的", "test-request-00000001")
    await _image_turn(client, conversation, image_id, "酱汁是什么颜色", "test-request-00000002", referenced=True)
    assert len(vision.prompts) == 2
    assert vision.prompts[1][-1].images
    assert "酱汁是什么颜色" in vision.prompts[1][-1].content
    assert "一盘料理" in "\n".join(message.content for message in chat.prompts[1])


async def test_deleting_the_conversation_deletes_its_picture(make_client, settings):
    import sqlite3
    from tests.fakes import FakeLLM, FakeLLMService
    from tests.test_delete_account import rows_mentioning
    from tests.test_text_turn import start_conversation
    vision = FakeLLM([_seen()])
    client = make_client(FakeLLM(["<ja>うん</ja><zh>嗯</zh>"]), own_llm_service=FakeLLMService(vision))
    conversation = await start_conversation(client)
    upload = await client.post(f"/api/conversations/{conversation}/images", content=picture())
    image_id = upload.json()["id"]
    await _image_turn(client, conversation, image_id, "今天自己做的", "test-request-00000001")
    await start_conversation(client)
    assert (await client.delete(f"/api/conversations/{conversation}")).status_code == 204
    url = f"/api/conversations/{conversation}/images/{image_id}"
    assert (await client.get(url)).status_code == 404
    assert "user_images" not in rows_mentioning(settings.database_path, conversation)
    with sqlite3.connect(settings.database_path) as db:
        left = db.execute("SELECT COUNT(*) FROM user_images WHERE id = ?", (image_id,)).fetchone()[0]
    assert left == 0


async def test_an_unsent_picture_expires(make_client, settings):
    import sqlite3
    import time
    from eidolon.user_images import TEMP_SECONDS
    from tests.fakes import FakeLLM
    from tests.test_text_turn import start_conversation
    client = make_client(FakeLLM())
    conversation = await start_conversation(client)
    upload = await client.post(f"/api/conversations/{conversation}/images", content=picture())
    image_id = upload.json()["id"]
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE user_images SET created_at = ? WHERE id = ?", (time.time() - TEMP_SECONDS - 5, image_id))
    assert (await client.get(f"/api/conversations/{conversation}/images/{image_id}")).status_code == 404
    again = await client.post(f"/api/conversations/{conversation}/images", content=picture("JPEG"))
    assert again.status_code == 201
    with sqlite3.connect(settings.database_path) as db:
        left = db.execute("SELECT COUNT(*) FROM user_images WHERE id = ?", (image_id,)).fetchone()[0]
    assert left == 0


def test_a_finished_look_uses_the_hourly_allowance():
    import sqlite3
    from eidolon.user_images import SCHEMA, VisionLimited, LimitedVision
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA)
    LimitedVision._reserve(connection, "one", "user", 1)
    with pytest.raises(VisionLimited):
        LimitedVision._reserve(connection, "two", "user", 1)
    LimitedVision._refund(connection, "one")
    LimitedVision._reserve(connection, "three", "user", 1)


async def test_an_imported_picture_can_be_asked_about_again(make_client, settings, tmp_path):
    from dataclasses import replace
    from tests.fakes import FakeLLM, FakeLLMService
    from tests.test_data_package import export, import_package, unzipped
    from tests.test_past_conversations import hers
    from tests.test_text_turn import start_conversation
    vision = FakeLLM([_seen()])
    again = FakeLLM([_seen("深色酱汁")])
    here = make_client(
        FakeLLM(["<ja>うん</ja><zh>嗯</zh>"]),
        settings=replace(settings, database_path=tmp_path / "here.db"),
        own_llm_service=FakeLLMService(vision),
    )
    conversation = await start_conversation(here)
    upload = await here.post(f"/api/conversations/{conversation}/images", content=picture())
    await _image_turn(here, conversation, upload.json()["id"], "今天自己做的", "test-request-00000001")
    package = await export(here, "conversations,photos")
    _, manifest, data = unzipped(package)
    from eidolon import data_package
    assert manifest["format"] == data_package.FORMAT
    assert data["conversations"][0]["messages"][0]["user_image"]
    there = make_client(
        FakeLLM(["<ja>濃いね</ja><zh>颜色挺深的</zh>"]),
        settings=replace(settings, database_path=tmp_path / "there.db"),
        own_llm_service=FakeLLMService(again),
    )
    imported = await import_package(there, package, "conversations,photos")
    assert imported.status_code == 200, imported.text
    brought = (await hers(there))[0]["id"]
    messages = (await there.get(f"/api/conversations/{brought}/messages")).json()
    image_id = next(message["user_image"]["id"] for message in messages if message.get("user_image"))
    await _image_turn(there, brought, image_id, "酱汁是什么颜色", "test-request-00000002", referenced=True)
    assert again.prompts[-1][-1].images
    assert "酱汁是什么颜色" in again.prompts[-1][-1].content
