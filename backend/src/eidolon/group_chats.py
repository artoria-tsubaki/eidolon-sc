"""A producer's text group chats, stored apart from one-character conversations."""

import asyncio
import json
import logging
import re
import sqlite3
import unicodedata
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal
from uuid import uuid4

from anyio import CancelScope
from pydantic import BaseModel, ConfigDict, Field

from eidolon.activity import Result
from eidolon.admission import Admission, Admissions, CoolingDown
from eidolon.affinity import Affinities, tier_prompt
from eidolon.affinity_judge import AffinityJudge
from eidolon.config import ImageSettings
from eidolon.judge import JudgeError
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.delivery_cues import without_cues
from eidolon.llm import ChatLLM, ChatMessage
from eidolon.memory import Memories
from eidolon.own_keys import BroughtKeys, OwnKeyFailed, TurnKeys
from eidolon.photo_prompt import UNPEOPLED, Unpeopled
from eidolon.photos import CANNOT_SEND, Photos, PhotoTurn, group_photo_turn, photo_turn
from eidolon.producer import effective_timezone, local_date, producer_prompt
from eidolon.reply_format import PhotoTagged, ReplyParser, SegmentDelta, format_instructions
from eidolon.retrieval import Retrieval
from eidolon.store import KeptPhoto, Message, PhotoFailure, PhotoStatus, read_profile
from eidolon.today import always_known
from eidolon.world import World

log = logging.getLogger(__name__)

# The parent user deletion and group deletion both cascade, through every turn and message.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS group_chats (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    member_key TEXT NOT NULL,
    UNIQUE (user_id, member_key, created_at)
);
CREATE TABLE IF NOT EXISTS group_chat_members (
    group_id TEXT NOT NULL REFERENCES group_chats(id) ON DELETE CASCADE,
    character_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (group_id, position),
    UNIQUE (group_id, character_id)
);
CREATE TABLE IF NOT EXISTS group_chat_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL REFERENCES group_chats(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    mentioned_id TEXT,
    mentioned_ids TEXT,
    status TEXT NOT NULL CHECK (status IN ('running','done','failed','interrupted')),
    UNIQUE (group_id, request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS group_chat_running ON group_chat_turns(group_id)
    WHERE status = 'running';
CREATE INDEX IF NOT EXISTS group_chat_request ON group_chat_turns(request_id);
CREATE TABLE IF NOT EXISTS group_chat_messages (
    id TEXT PRIMARY KEY,
    turn_id INTEGER NOT NULL REFERENCES group_chat_turns(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    character_id TEXT,
    text TEXT,
    segments TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (turn_id, position)
);
CREATE TABLE IF NOT EXISTS group_chat_photos (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE REFERENCES group_chat_messages(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','ready','failed')),
    reason TEXT,
    image BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class GroupMissing(Exception):
    pass


class GroupBusy(Exception):
    pass


class BadGroup(ValueError):
    pass


class RequestConflict(Exception):
    pass


Status = Literal["running", "done", "failed", "interrupted"]


class GroupSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ja: str
    zh: str


class GroupPhoto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: PhotoStatus
    reason: PhotoFailure | None


class GroupMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    character_id: str | None
    text: str | None = None
    segments: list[GroupSegment] = Field(default_factory=list)
    created_at: str
    photo: GroupPhoto | None = None
    reply_to: str | None = None


class GroupTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    status: Status
    mentioned_id: str | None = None
    mentioned_ids: list[str] | None = None
    messages: list[GroupMessage]

    @property
    def targets(self) -> list[str]:
        if self.mentioned_ids is not None:
            return self.mentioned_ids
        return [self.mentioned_id] if self.mentioned_id else []


class GroupSummary(BaseModel):
    id: str
    member_ids: list[str]
    name: str
    created_at: str
    last_message: GroupMessage | None


class GroupDetail(GroupSummary):
    turns: list[GroupTurn]


@dataclass(frozen=True)
class _GroupPhoto:
    sender: str
    turn: PhotoTurn
    pictured: tuple[Character, ...] | Unpeopled | None = None
    problem: str | None = None


def _key(members: Sequence[str]) -> str:
    return json.dumps(sorted(members), ensure_ascii=False)


def _create_tables(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)
    columns = {row[1] for row in db.execute("PRAGMA table_info(group_chat_turns)")}
    if "mentioned_ids" not in columns:
        db.execute("ALTER TABLE group_chat_turns ADD COLUMN mentioned_ids TEXT")
    message_columns = {row[1] for row in db.execute("PRAGMA table_info(group_chat_messages)")}
    if "reply_to" not in message_columns:
        db.execute("ALTER TABLE group_chat_messages ADD COLUMN reply_to TEXT")


def _prepare(db: sqlite3.Connection) -> None:
    _create_tables(db)
    # The backend runs in one process. Nothing that was generating before startup can resume.
    db.execute("UPDATE group_chat_turns SET status='interrupted' WHERE status='running'")
    db.execute("UPDATE group_chat_photos SET status='failed',reason='timeout' WHERE status='pending'")


def _insert_group(
    db: sqlite3.Connection, user: str, members: Sequence[str], created_at: str
) -> str | None:
    group = uuid4().hex
    created = db.execute(
        "INSERT INTO group_chats VALUES (?,?,?,?)"
        " ON CONFLICT(user_id,member_key,created_at) DO NOTHING",
        (group, user, created_at, _key(members)),
    )
    if not created.rowcount:
        return None
    db.executemany(
        "INSERT INTO group_chat_members VALUES (?,?,?)",
        [(group, member, position) for position, member in enumerate(members)],
    )
    return group


def _message(row: tuple) -> GroupMessage:
    id, character, text, segments, at, photo, status, reason, reply_to = row
    return GroupMessage(id=id, character_id=character, text=text, segments=json.loads(segments),
                        created_at=at, photo=GroupPhoto(id=photo, status=status, reason=reason) if photo else None,
                        reply_to=reply_to)


_MESSAGE_COLUMNS = "m.id,m.character_id,m.text,m.segments,m.created_at,p.id,p.status,p.reason,m.reply_to"
_PHOTO_JOIN = " LEFT JOIN group_chat_photos p ON p.message_id=m.id"
_PHOTO_OWNER = (
    " FROM group_chat_photos p JOIN group_chat_messages m ON p.message_id=m.id"
    " JOIN group_chat_turns t ON m.turn_id=t.id JOIN group_chats g ON t.group_id=g.id"
    " WHERE p.id=? AND g.user_id=?"
)


def group_photo_image(db: sqlite3.Connection, photo_id: str, user: str) -> bytes | None:
    row = db.execute("SELECT p.image" + _PHOTO_OWNER + " AND p.status='ready'", (photo_id, user)).fetchone()
    return row[0] if row else None


def _photo(db: sqlite3.Connection, photo_id: str, user: str) -> GroupPhoto | None:
    row = db.execute("SELECT p.id,p.status,p.reason" + _PHOTO_OWNER, (photo_id, user)).fetchone()
    return GroupPhoto(id=row[0], status=row[1], reason=row[2]) if row else None


def _insert_photo(db: sqlite3.Connection, message: str, photo: KeptPhoto, image: bytes | None = None) -> None:
    db.execute(
        "INSERT INTO group_chat_photos VALUES (?,?,?,?,?,?,?,?)",
        (photo.id, message, photo.tag, photo.status, photo.reason, image, photo.created_at, photo.updated_at),
    )


def _finish_photo(
    db: sqlite3.Connection, photo: str, at: str, image: bytes | None, reason: PhotoFailure | None,
) -> bool:
    cursor = db.execute(
        "UPDATE group_chat_photos SET status=?,reason=?,image=?,updated_at=? WHERE id=? AND status='pending'",
        ("ready" if image is not None else "failed", reason, image, at, photo),
    )
    return cursor.rowcount > 0


def _turns(db: sqlite3.Connection, group: str, limit: int | None = None) -> list[GroupTurn]:
    rows = db.execute(
        "SELECT id,status,mentioned_id,mentioned_ids FROM group_chat_turns"
        " WHERE group_id=? ORDER BY id DESC LIMIT ?", (group, limit if limit is not None else -1),
    )
    return [_read_turn(db, *row) for row in reversed(rows.fetchall())]


def _read_turn(
    db: sqlite3.Connection, id: int, status: Status, mentioned: str | None, mentions: str | None,
) -> GroupTurn:
    rows = db.execute(
        f"SELECT {_MESSAGE_COLUMNS} FROM group_chat_messages m{_PHOTO_JOIN} WHERE m.turn_id=? ORDER BY m.position",
        (id,),
    )
    targets = json.loads(mentions) if mentions is not None else ([mentioned] if mentioned else [])
    return GroupTurn(id=id, status=status, mentioned_ids=targets,
                     mentioned_id=targets[0] if len(targets) == 1 else None,
                     messages=[_message(r) for r in rows])


def _members(db: sqlite3.Connection, group: str) -> list[str]:
    return [m for (m,) in db.execute(
        "SELECT character_id FROM group_chat_members WHERE group_id=? ORDER BY position", (group,),
    )]


def _owned(db: sqlite3.Connection, user: str, group: str) -> str:
    row = db.execute("SELECT created_at FROM group_chats WHERE id=? AND user_id=?", (group, user)).fetchone()
    if row is None:
        raise GroupMissing()
    return row[0]


def _last(db: sqlite3.Connection, group: str) -> GroupMessage | None:
    row = db.execute(
        f"SELECT {_MESSAGE_COLUMNS} FROM group_chat_messages m JOIN group_chat_turns t ON m.turn_id=t.id"
        f"{_PHOTO_JOIN} WHERE t.group_id=? ORDER BY t.id DESC,m.position DESC LIMIT 1", (group,),
    ).fetchone()
    return _message(row) if row else None


def packaged_groups(
    db: sqlite3.Connection, user: str, with_photos: bool,
) -> list[tuple[list[str], str, list[GroupTurn], dict[str, KeptPhoto]]]:
    return [
        (_members(db, group), at, _turns(db, group), _packaged_photos(db, group) if with_photos else {})
        for group, at in db.execute(
            "SELECT id,created_at FROM group_chats WHERE user_id=? ORDER BY created_at,id", (user,)
        )
    ]


def _packaged_photos(db: sqlite3.Connection, group: str) -> dict[str, KeptPhoto]:
    return {
        message: KeptPhoto(*photo)
        for message, *photo in db.execute(
            "SELECT p.message_id,p.id,p.tag,p.status,p.reason,p.created_at,p.updated_at"
            " FROM group_chat_photos p JOIN group_chat_messages m ON p.message_id=m.id"
            " JOIN group_chat_turns t ON m.turn_id=t.id WHERE t.group_id=?", (group,),
        )
    }


def add_packaged_group(
    db: sqlite3.Connection, user: str, members: list[str], at: str, turns: Sequence[GroupTurn],
    photos: Mapping[str, KeptPhoto], picture_of: Callable[[str], bytes],
) -> bool:
    if (group := _insert_group(db, user, members, at)) is None:
        return False
    ids = {message.id: uuid4().hex for turn in turns for message in turn.messages}
    for turn in turns:
        id = _insert_turn(db, group, uuid4().hex, turn.targets,
                          "interrupted" if turn.status == "running" else turn.status)
        for position, message in enumerate(turn.messages):
            new_id = ids[message.id]
            _insert_message(db, id, position, message.model_copy(update={"id": new_id,
                            "reply_to": ids.get(message.reply_to) if message.reply_to else None}))
            if photo := photos.get(message.id):
                _insert_photo(db, new_id, KeptPhoto(uuid4().hex, photo.tag, photo.status,
                                                     photo.reason, photo.created_at, photo.updated_at),
                              picture_of(photo.id) if photo.status == "ready" else None)
    return True


def _delete(db: sqlite3.Connection, user: str, group: str) -> None:
    _owned(db, user, group)
    if db.execute(
        "SELECT 1 FROM group_chat_turns WHERE group_id=? AND status='running'", (group,)
    ).fetchone():
        raise GroupBusy()
    db.execute("DELETE FROM group_chats WHERE id=?", (group,))


def _request(
    db: sqlite3.Connection, user: str, request: str, group: str, mentions: list[str], text: str, reply_to: str | None
) -> GroupTurn | None:
    row = db.execute(
        "SELECT t.id,t.status,t.mentioned_id,t.mentioned_ids,t.group_id FROM group_chat_turns t"
        " JOIN group_chats g ON t.group_id=g.id WHERE g.user_id=? AND t.request_id=?", (user, request),
    ).fetchone()
    if row is None:
        return None
    id, status, mentioned, stored_mentions, in_group = row
    turn = _read_turn(db, id, status, mentioned, stored_mentions)
    if in_group != group or turn.targets != mentions or turn.messages[0].text != text or turn.messages[0].reply_to != reply_to:
        raise RequestConflict()
    if status == "running":
        raise GroupBusy()
    return turn


def _insert_turn(
    db: sqlite3.Connection, group: str, request: str, mentions: list[str], status: Status
) -> int:
    row = db.execute(
        "INSERT INTO group_chat_turns(group_id,request_id,mentioned_ids,status) VALUES (?,?,?,?) RETURNING id",
        (group, request, json.dumps(mentions), status),
    ).fetchone()
    return row[0]


def _insert_message(db: sqlite3.Connection, turn: int, position: int, message: GroupMessage) -> None:
    db.execute(
        "INSERT INTO group_chat_messages (id,turn_id,position,character_id,text,segments,created_at,reply_to) VALUES (?,?,?,?,?,?,?,?)",
        (message.id, turn, position, message.character_id, message.text,
         json.dumps([s.model_dump() for s in message.segments], ensure_ascii=False), message.created_at, message.reply_to),
    )


def _quoted(db: sqlite3.Connection, group: str, id: str) -> GroupMessage:
    row = db.execute(
        f"SELECT {_MESSAGE_COLUMNS} FROM group_chat_messages m JOIN group_chat_turns t ON m.turn_id=t.id"
        f"{_PHOTO_JOIN} WHERE t.group_id=? AND m.id=? AND m.character_id IS NOT NULL", (group, id),
    ).fetchone()
    if row is None:
        raise BadGroup("引用的消息不存在或不属于本群。")
    return _message(row)


def _start(
    db: sqlite3.Connection, user: str, group: str, request: str, mentions: list[str], message: GroupMessage
) -> int:
    _owned(db, user, group)
    turn = _insert_turn(db, group, request, mentions, "running")
    _insert_message(db, turn, 0, message)
    return turn


def _finish(db: sqlite3.Connection, turn: int, status: Status) -> None:
    db.execute("UPDATE group_chat_turns SET status=? WHERE id=? AND status='running'", (status, turn))


def _save_reply(
    db: sqlite3.Connection, turn: int, position: int, message: GroupMessage, tag: str | None,
) -> None:
    _insert_message(db, turn, position, message)
    if message.photo is not None and tag is not None:
        _insert_photo(db, message.id, KeptPhoto(
            message.photo.id, tag, "pending", None, message.created_at, message.created_at,
        ))



# Recognize vocatives at clause boundaries, and leave incidental references in the text.
_ADDRESS_REQUEST = re.compile(r"^(?:请|能|可以|麻烦|说说|讲讲|发|拍|传|你(?:们)?|どう|お願い|教えて|送って|撮って)")
_CLAUSE = re.compile(r"([。！？!?；;\n，,])")
_CONNECTOR = re.compile(r"^(?:和|与|及|还有|、|と|&)\s*")


def _addressed(text: str, members: Sequence[Character]) -> list[tuple[list[str], str]]:
    aliases: dict[str, set[str]] = {}
    for member in members:
        for name in (member.name, member.short_name, member.name_zh, member.name_kana, member.name_romaji, member.id):
            if name:
                aliases.setdefault(unicodedata.normalize("NFKC", name).casefold(), set()).add(member.id)
    distinct = {name: next(iter(ids)) for name, ids in aliases.items() if len(ids) == 1}
    if not distinct:
        return [([], text)]
    names = "|".join(map(re.escape, sorted(distinct, key=len, reverse=True)))
    name_at = re.compile(rf"^(@)?({names})(?:さん|ちゃん)?", re.IGNORECASE)
    parts = _CLAUSE.split(unicodedata.normalize("NFKC", text))
    addressed = []
    carried: list[str] = []
    for index in range(0, len(parts), 2):
        clause = parts[index].strip()
        if not clause:
            continue
        end = parts[index + 1] if index + 1 < len(parts) else ""
        rest = clause
        found: list[str] = []
        explicit = False
        while match := name_at.match(rest):
            found.append(distinct[match[2].casefold()])
            explicit |= bool(match[1])
            rest = rest[match.end():].lstrip()
            if rest.startswith("@"):
                continue
            if connector := _CONNECTOR.match(rest):
                if name_at.match(rest[connector.end():]):
                    rest = rest[connector.end():]
                    continue
            break
        if found and (explicit or _ADDRESS_REQUEST.match(rest) or (not rest and end in ("，", ","))):
            rest = rest.lstrip(":： ")
            ids = list(dict.fromkeys([*carried, *found]))
            # Preserve the named roles even when a clause consists only of a vocative.
            addressed.append((ids, rest))
            carried = ids if not rest and end in ("，", ",") else []
        else:
            addressed.append((carried, clause))
            carried = []
    return addressed or [([], text)]


async def _reply_text(model: ChatLLM, prompt: list[ChatMessage]) -> AsyncGenerator[str, None]:
    # Some adapters return a plain AsyncIterator, others an async generator owning a connection.
    stream = model.stream(prompt)
    try:
        async for part in stream:
            if isinstance(part, str):
                yield part
    finally:
        if (close := getattr(stream, "aclose", None)) is not None:
            await close()


class GroupRun:
    def __init__(
        self, events: AsyncIterator[dict], end: Callable[[], Awaitable[None]],
    ) -> None:
        self.events = events
        self.end = end


class GroupChats:
    def __init__(
        self, db: Database, characters: dict[str, Character], world: World | None,
        admissions: Admissions, memories: Memories, retrieval: Retrieval | None, clock: Clock,
        history_turns: Callable[[], int | None], photos: Photos,
        photo_judge: AffinityJudge | None, affinities: Affinities,
    ) -> None:
        self._db = db
        self._characters = characters
        self._world = world
        self._admissions = admissions
        self._memories = memories
        self._retrieval = retrieval
        self._clock = clock
        self._history_turns = history_turns
        self._photos = photos
        self._photo_judge = photo_judge
        self._affinities = affinities
        # Claim before awaits, including while deciding admission. Running status is also persisted.
        self._busy: set[str] = set()
        self._requests: set[tuple[str, str]] = set()
        db.prepare(_prepare)

    def can_join(self, member: str) -> bool:
        character = self._characters.get(member)
        return bool(character and member != "hazuki" and (
            self._world is None or not self._world.is_staff(character.name)
        ))

    def _name(self, id: str) -> str:
        character = self._characters.get(id)
        return (character.short_name or character.name) if character else id

    def _summary(self, db: sqlite3.Connection, group: str, at: str) -> GroupSummary:
        members = _members(db, group)
        return GroupSummary(id=group, member_ids=members, created_at=at,
                            name="、".join(map(self._name, members)), last_message=_last(db, group))

    async def summaries(self, user: str) -> list[GroupSummary]:
        def read(db: sqlite3.Connection) -> list[GroupSummary]:
            groups = [self._summary(db, group, at) for group, at in db.execute(
                "SELECT id,created_at FROM group_chats WHERE user_id=? ORDER BY id", (user,),
            )]
            return sorted(groups, key=lambda g: (
                g.last_message.created_at if g.last_message else g.created_at
            ), reverse=True)
        return await self._db.read(read)

    async def create(self, user: str, members: list[str]) -> GroupSummary:
        if not 2 <= len(members) <= 3 or len(set(members)) != len(members):
            raise BadGroup("请选择 2 至 3 位不同的偶像。")
        if not all(map(self.can_join, members)):
            raise BadGroup("群聊成员必须是可选的偶像。")
        def write(db: sqlite3.Connection) -> GroupSummary:
            at = self._clock()
            # Frozen test clocks and simultaneous creations can share a timestamp.
            while True:
                stamped = at.astimezone(UTC).isoformat(timespec="microseconds")
                if group := _insert_group(db, user, members, stamped):
                    return self._summary(db, group, stamped)
                at += datetime.resolution
        return await self._db.write(write)

    async def read(self, user: str, group: str) -> GroupDetail:
        def read(db: sqlite3.Connection) -> GroupDetail:
            at = _owned(db, user, group)
            return GroupDetail(**self._summary(db, group, at).model_dump(), turns=_turns(db, group))
        return await self._db.read(read)

    async def delete(self, user: str, group: str) -> None:
        await self._db.read(_owned, user, group)
        if group in self._busy:
            raise GroupBusy()
        await self._db.write(_delete, user, group)

    async def photo(self, photo: str, user: str) -> GroupPhoto | None:
        return await self._db.read(_photo, photo, user)

    async def photo_image(self, photo: str, user: str) -> bytes | None:
        return await self._db.read(group_photo_image, photo, user)

    async def _finish_photo(self, photo: str, image: bytes | None, reason: PhotoFailure | None) -> bool:
        return await self._db.write(_finish_photo, photo, self._clock().isoformat(), image, reason)

    async def begin(
        self, user: str, group: str, request: str, mentions: list[str], text: str,
        brought: BroughtKeys, device_timezone: str | None,
        reply_to: str | None = None,
    ) -> GroupTurn | GroupRun:
        await self._db.read(_owned, user, group)
        members = await self._db.read(_members, group)
        if len(set(mentions)) != len(mentions) or any(m not in members for m in mentions):
            raise BadGroup("点名名单只能包含不同的本群成员。")
        addressed = _addressed(text, [self._characters[m] for m in members if m in self._characters])
        quoted = await self._db.read(_quoted, group, reply_to) if reply_to else None
        quote_target: list[str] = []
        if quoted is not None and quoted.character_id is not None:
            quote_target.append(quoted.character_id)
        targets: list[str] = list(dict.fromkeys([*quote_target, *mentions,
                                                *(m for ids, _ in addressed for m in ids)]))
        if existing := await self._db.read(_request, user, request, group, targets, text, reply_to):
            return existing
        key = user, request
        if group in self._busy or key in self._requests:
            raise GroupBusy()
        self._busy.add(group)
        self._requests.add(key)
        admission: Admission[TurnKeys] | None = None
        try:
            available = []
            cooling = False
            for member in members:
                if not self.can_join(member):
                    continue
                try:
                    await self._admissions.refuse_while_cooling(user, member)
                except CoolingDown:
                    cooling = True
                else:
                    available.append(member)
            if not available:
                raise BadGroup("本群可用的成员都在冷却中，请晚些再来。" if cooling else
                               "本群成员在当前部署不可用。")
            if quoted and any(target not in available for target in targets):
                raise BadGroup("被引用或点名的成员暂时无法回复，请稍后重试。")
            admission = await self._admissions.admitted(
                self._admissions.group_turn(user, brought), lambda entry: entry.settle("failed")
            )
            message = GroupMessage(id=uuid4().hex, character_id=None, text=text,
                                   created_at=self._clock().astimezone(UTC).isoformat(timespec="microseconds"),
                                   reply_to=reply_to)
            starting = asyncio.create_task(self._db.write(_start, user, group, request, targets, message))
            try:
                turn = await asyncio.shield(starting)
            except asyncio.CancelledError:
                await self._db.write(_finish, await starting, "interrupted")
                await admission.settle("left")
                raise
            return self._run(user, group, turn, request, members, available, message, targets, admission,
                             device_timezone, addressed, brought.image, quoted)
        except BaseException:
            self._busy.discard(group)
            self._requests.discard(key)
            if admission is not None:
                await admission.settle("failed")
            raise

    def _run(
        self, user: str, group: str, turn: int, request: str, members: list[str], available: list[str],
        message: GroupMessage, targets: list[str], admission: Admission[TurnKeys],
        device_timezone: str | None, addressed: list[tuple[list[str], str]], own_image: ImageSettings | None,
        quoted: GroupMessage | None,
    ) -> GroupRun:
        status: Status = "interrupted"
        result: Result = "left"
        ended = False
        started_at = perf_counter()

        async def events() -> AsyncGenerator[dict, None]:
            nonlocal status, result
            yield {"type": "message", "turn_id": turn, "message": message.model_dump(), "mentioned_ids": targets}
            detail = None
            photo_problem = None
            saved = 0
            with admission.recording.current():
                try:
                    turns = await self._db.read(_turns, group, min(self._history_turns() or 12, 12) + 1)
                    history = [m for t in turns if t.id != turn for m in t.messages]
                    snapshot = [*history, message]
                    direct = [m for m in targets if m in available]
                    others = [m for m in available if m not in targets]
                    if quoted and others:
                        try:
                            select_prompt = [ChatMessage("system", "判断哪些群成员有必要补充回答。只返回一个成员ID，或 none；最多选一人。"),
                                             ChatMessage("user", json.dumps({"candidates": others,
                                                 "quote": self._transcript([quoted]), "question": message.text}, ensure_ascii=False))]
                            async def select() -> str:
                                return "".join([chunk async for chunk in _reply_text(admission.llms.chat, select_prompt)]).strip()
                            chosen = await asyncio.wait_for(select(), timeout=5)
                            others = [chosen] if chosen in others else []
                        except Exception:
                            others = []
                    speakers = [*direct, *others]
                    direct_replies: list[GroupMessage] = []
                    replies: list[GroupMessage] = []
                    sending_photo = await self._photo_for(
                        user, speakers, targets, addressed, history, own_image, members, admission.llms.chat,
                        message.text or "",
                    )
                    photo_problem = sending_photo.problem if sending_photo else None
                    for position, speaker in enumerate(speakers, 1):
                        try:
                            await self._admissions.refuse_while_cooling(user, speaker)
                        except CoolingDown:
                            if quoted and speaker in targets:
                                detail = f"{self._speaker_name(speaker)}暂时无法回复，请重试。"
                            continue
                        context = snapshot if speaker in direct else [
                            *snapshot, *(direct_replies if targets else replies),
                        ]
                        if quoted:
                            context = [quoted, *[m for m in context if m.id != quoted.id]]
                        photo = sending_photo if sending_photo and speaker == sending_photo.sender else None
                        id = uuid4().hex
                        parser = ReplyParser()
                        tag = None
                        try:
                            prompt = await self._prompt(user, speaker, members, context, device_timezone, targets,
                                                        photo.turn if photo else None)
                            async with aclosing(_reply_text(admission.llms.chat, prompt)) as chunks:
                                async for part in chunks:
                                    for event in parser.feed(part):
                                        if isinstance(event, PhotoTagged) and photo and photo.turn.keeps_tag:
                                            tag = event.tag
                                        elif isinstance(event, SegmentDelta):
                                            admission.recording.spoke()
                                            yield _delta(turn, id, speaker, event)
                            for event in parser.finish():
                                if isinstance(event, SegmentDelta):
                                    admission.recording.spoke()
                                    yield _delta(turn, id, speaker, event)
                            segments = [GroupSegment(ja=without_cues(s.ja), zh=without_cues(s.zh))
                                        for s in parser.segments if s.ja.strip()]
                            if not segments:
                                raise ValueError("empty group reply")
                            reply = GroupMessage(id=id, character_id=speaker, segments=segments,
                                                 created_at=self._clock().astimezone(UTC).isoformat(timespec="microseconds"))
                            await self._commit_reply(user, turn, position, reply, tag, own_image,
                                                     photo.pictured if photo else None)
                        except OwnKeyFailed as e:
                            detail = (f"{self._speaker_name(speaker)}的回复未完成，请重试。" if quoted and speaker in targets else e.message)
                            yield {"type": "discard", "message_id": id}
                            continue
                        except Exception:
                            log.exception("group reply by %s failed in %s, turn %s", speaker, group, turn)
                            detail = detail or (f"{self._speaker_name(speaker)}的回复未完成，请重试。"
                                                if quoted and speaker in targets else "部分回复未完成，可以继续发送消息。")
                            yield {"type": "discard", "message_id": id}
                            continue
                        saved += 1
                        replies.append(reply)
                        if speaker in direct:
                            direct_replies.append(reply)
                        yield {"type": "message", "turn_id": turn, "message": reply.model_dump()}
                except Exception:
                    log.exception("group chat failed in %s, turn %s", group, turn)
                    detail = detail or "这轮回复未完成，可以继续发送消息。"
                if saved:
                    status, result = ("interrupted" if detail else "done"), "success"
                else:
                    status, result = "failed", "failed"
                    detail = detail or "这轮没有可发言的成员。"
                await self._db.write(_finish, turn, status)
            yield {"type": "done", "turn_id": turn, "status": status, "detail": detail or photo_problem}

        stream = events()

        async def end() -> None:
            nonlocal ended
            if ended:
                return
            ended = True
            try:
                try:
                    await stream.aclose()
                finally:
                    await self._db.write(_finish, turn, status)
            finally:
                try:
                    await admission.settle(result, duration=perf_counter() - started_at)
                finally:
                    self._busy.discard(group)
                    self._requests.discard((user, request))

        return GroupRun(stream, end)

    async def _commit_reply(
        self, user: str, turn: int, position: int, message: GroupMessage, tag: str | None,
        own: ImageSettings | None, pictured: tuple[Character, ...] | Unpeopled | None = None,
    ) -> None:
        if tag is None or message.character_id is None:
            await self._db.write(_save_reply, turn, position, message, None)
            return
        # Keep the message and its pending photo together even if the stream closes meanwhile.
        with CancelScope(shield=True):
            id = self._photos.begin(self._characters[message.character_id], tag, user, own,
                                    finish=self._finish_photo, pictured=pictured)
            message.photo = GroupPhoto(id=id, status="pending", reason=None)
            try:
                await self._db.write(_save_reply, turn, position, message, tag)
            except BaseException:
                self._photos.dropped(id)
                raise
            await self._photos.recorded(id)

    async def _photo_for(
        self, user: str, speakers: list[str], targets: list[str], addressed: list[tuple[list[str], str]],
        history: list[GroupMessage], own: ImageSettings | None, members: list[str], model: ChatLLM,
        text: str,
    ) -> _GroupPhoto | None:
        judge = self._photo_judge
        if judge is None:
            return None
        # Classify addressed clauses separately: asking one idol for a photo does not
        # give the other idols permission to send one.
        context = self._retrieval_context(history)
        directed: dict[str, list[str]] = {}
        general = []
        for ids, clause in addressed:
            if not clause:
                continue
            if ids:
                for id in ids:
                    if id in speakers:
                        directed.setdefault(id, []).append(clause)
            else:
                general.append(clause)
        requests = [(id, "。".join(parts)) for id, parts in directed.items()]
        general_text = "。".join(general)
        general_sender = next((id for id in speakers if not targets or id in targets), None)
        if general_text and general_sender is not None:
            requests.append((general_sender, general_text))
        for speaker, question in requests:
            character = self._characters[speaker]
            try:
                _, asked = await judge.judge(character, context, question, False, True)
            except JudgeError:
                return None
            if not asked:
                continue
            try:
                pictured = await self._photo_cast(model, question, text, members, speaker)
            except Exception:
                return self._photo_refused(speaker, "无法确定照片里的成员，请明确指定后重试。")
            if problem := self._photos.group_unavailable(pictured, own):
                return self._photo_refused(speaker, problem)
            eligible = await self._db.read(self._photos.user_eligibility, user, own)
            if eligible is None:
                return self._photo_refused(speaker, "当前没有可用的照片额度。")
            affinity = (
                await self._db.read(self._affinities.read_status, user, speaker)
                if self._affinities.on else None
            )
            photo = photo_turn(asked, affinity.tier if affinity else None, eligible)
            if photo:
                if photo.keeps_tag:
                    return _GroupPhoto(speaker, group_photo_turn(pictured), pictured)
                problem = "照片额度暂时用完了。" if photo.used_up_until else f"{character.name}现在不想发照片。"
                return _GroupPhoto(speaker, photo, problem=problem)
        return None

    @staticmethod
    def _photo_refused(speaker: str, problem: str) -> _GroupPhoto:
        return _GroupPhoto(speaker, PhotoTurn(f"# 照片\n{problem}请向制作人说明，不要写照片标签。", False),
                           problem=problem)

    async def _photo_cast(
        self, model: ChatLLM, request: str, text: str, members: list[str], sender: str,
    ) -> tuple[Character, ...] | Unpeopled:
        """Resolve the image subjects, independently of who was asked to send it."""
        prompt = [ChatMessage("system", "识别制作人要拍的照片内容，只输出一个 JSON 对象。"
                              "根据已选中的请求判断，原消息可以补全名字和指代。"
                              '未指定入镜角色时输出 {"members":null}，表示全体群成员入镜。'
                              '明确指定角色时输出 {"members":["角色ID"]}，按要求列出一至多位。'
                              "指定谁发照片不代表谁入镜；要求某位成员自己的自拍时，她单独入镜。"
                              '明确只拍风景、食物或物品时输出 {"subject":"scenery"}、'
                              '{"subject":"food"} 或 {"subject":"object"}。'
                              '指定了不在名单中的人或无法确定指定对象时输出 {"unsupported":true}。'),
                  ChatMessage("user", json.dumps({"request": request, "message": text, "sender": sender,
                    "members": [{"id": m, "name": self._speaker_name(m),
                                 "aliases": [self._characters[m].short_name, self._characters[m].name_zh,
                                             self._characters[m].name_kana, self._characters[m].name_romaji]}
                                for m in members]}, ensure_ascii=False))]
        async def classify() -> str:
            return "".join([chunk async for chunk in _reply_text(model, prompt)])
        response = await asyncio.wait_for(classify(), timeout=5)
        start, end = response.find("{"), response.rfind("}")
        parsed = json.loads(response[start:end + 1])
        for subject in UNPEOPLED:
            if parsed == {"subject": subject}:
                return subject
        if not isinstance(parsed, dict) or parsed.keys() != {"members"}:
            raise ValueError("invalid photo subjects")
        ids = parsed["members"]
        if ids is None:
            ids = members
        if (not isinstance(ids, list) or not ids
            or any(not isinstance(id, str) or id not in members or id not in self._characters for id in ids)
            or len(ids) != len(set(ids))):
            raise ValueError("invalid photo participants")
        return tuple(self._characters[id] for id in ids)

    def _speaker_name(self, id: str) -> str:
        character = self._characters.get(id)
        return character.name if character else id

    def _transcript(self, messages: Sequence[GroupMessage]) -> list[dict]:
        return [{
            "speaker": self._speaker_name(m.character_id) if m.character_id else "制作人",
            "role": "character" if m.character_id else "user",
            "text": m.text if m.text is not None else "\n".join(s.ja for s in m.segments),
            **({"photo_status": m.photo.status} if m.photo is not None else {}),
        } for m in messages]

    def _retrieval_context(self, history: Sequence[GroupMessage]) -> list[Message]:
        return [
            Message(i, "user" if m.character_id is None else "character",
                    m.text, f"{self._speaker_name(m.character_id)}："
                    + "\n".join(s.ja for s in m.segments) if m.character_id else None,
                    None, m.created_at)
            for i, m in enumerate(history)
        ]

    async def _prompt(
        self, user: str, speaker: str, members: list[str], history: list[GroupMessage],
        device_timezone: str | None, targets: list[str], photo: PhotoTurn | None,
    ) -> list[ChatMessage]:
        character = self._characters[speaker]
        profile = await self._db.read(read_profile, user)
        affinity = (await self._db.read(self._affinities.read_status, user, speaker)
                    if self._affinities.on else None)
        timezone = effective_timezone(profile, device_timezone)
        world = (
            self._world.prompt(character.name, local_date(self._clock(), timezone)) if self._world else None
        )
        retrieved = []
        if self._retrieval is not None:
            # The shared memory lookup labels the source from the speaker's perspective.
            memory = await self._memories.for_group(user, speaker, timezone)
            newest = next(m for m in reversed(history) if m.character_id is None)
            context = self._retrieval_context([m for m in history if m.id != newest.id])
            found = await self._retrieval.retrieve(character, context, newest.text or "", memory)
            retrieved = [p for p in (found.memories_prompt, found.prompt) if p]
        if targets and speaker in targets:
            guidance = "你被制作人点名了。独立回答明确交给你的问题；不要猜其他成员这轮会怎么答。"
        elif targets:
            guidance = ("你没有被点名。用一句有自己口吻的补充、反应或善意吐槽，"
                        "可以接制作人的话，也可以接刚才被点名成员的回复。避免重复他们的回答。")
        else:
            guidance = "用一两句回应制作人或接前面成员的话，给群聊带来一点不同内容。"
        return [
            ChatMessage("system", "\n\n".join([
                *always_known(character, world), format_instructions(None),
                "这是制作人和几位偶像的文字群聊。只写你自己的一条消息，不代别人发言。"
                "可以回应制作人，也可以接已经发言的成员的话。"
                "别人讲过的经历是你听来的，只有自己出场过的剧情才是你的亲历。"
                "对话记录中的名字用于识别说话人，不要在输出里加姓名前缀。",
                "群聊成员：制作人、" + "、".join(map(self._speaker_name, members)) + "。",
                *tier_prompt(affinity),
                "这是大家都能听到的群聊，表达与制作人的亲近时注意公开场合。",
                guidance,
                "对话中只有 photo_status 为 ready 的照片已经发出。不要猜照片里有什么。",
            ]), cache_breakpoint=True),
            ChatMessage("system", producer_prompt(profile)),
            ChatMessage("system", "\n\n".join([photo.prompt if photo else CANNOT_SEND, *retrieved])),
            ChatMessage("user", json.dumps({
                "mentioned": [self._speaker_name(id) for id in targets],
                "messages": self._transcript(history),
            }, ensure_ascii=False)),
        ]


def _delta(turn: int, id: str, speaker: str, event: SegmentDelta) -> dict:
    return {"type": "delta", "turn_id": turn, "message_id": id, "character_id": speaker,
            "segment": event.segment, "lang": event.lang, "text": event.text}
