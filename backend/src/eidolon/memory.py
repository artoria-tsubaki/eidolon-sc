"""Memory: what users tell the characters, kept across conversations.

After each turn the background model reads the exchange and writes down, in Chinese,
whatever the user revealed about themselves that is worth keeping. Each memory belongs
to the user and names the character it was told to, its source character. All of a
user's characters share their memories, and a character finding one told to someone
else is asked to pass it on as hearsay (ADR-0002); users never share theirs.

A turn goes `begin` → `candidates` → `find` → `write_down`. The turn's query (see
`memory_query`) is embedded once, by whoever takes the turn (`retrieval`), and the vector
serves all of them: the candidates are the user's holding memories nearest it, found
before anyone knows whether the turn needs memories at all, which only `find` reranks;
and writing down shows the background model the memories nearest it. Writing runs in
the background so it never holds up the reply; the user's next turn begins by waiting briefly for it, so what they
just said is remembered by the time they go on. A new conversation picks up where
earlier ones left off only through the memories it finds.

What a user says may update what they said before: writing it down, the background model
is shown the memories nearest the turn and says which of them the new memory supersedes
(ADR-0014). A superseded memory is neither found nor shown to the background model again,
but stays listed until the user deletes it; deleting what superseded it brings it back,
unless that was superseded in turn, by what superseded that.

Users can list their memories and delete them one by one or all at once, never add or
edit them. Listing and deleting wait briefly for what is being written down too;
whatever turns begun before a clearing write down after it is dropped, so a cleared
memory never turns up again.

Each memory is embedded as it is written down, and the vector kept until the memory is
gone; a memory whose embedding failed is embedded the next time its user's memories are
searched. After the embedding model changes, `eidolon reembed` embeds them all again.

Each turn writes down with the model it began with (see `OwnKeys.for_turn`). Without an
embedder and reranker, memory is off: turns find and write down nothing, but users can
still list and delete the memories already kept.
"""

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from eidolon.activity import detached
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.corpus import REEMBED_BATCH
from eidolon.data_access import Database
from eidolon.embedding import Document, Embedder, EmbeddingError, nearest, to_blob
from eidolon.llm import ChatLLM, ChatMessage
from eidolon.rerank import DEFAULT_THRESHOLDS, Reranker, RerankError
from eidolon.store import Conversation, Message

log = logging.getLogger(__name__)

SETTLE_TIMEOUT = 5.0
"""How long, in seconds, a turn waits for the memories of the user's previous turn to be
written before going on without them."""
SHOWN_MEMORIES = 20
"""How many of the user's holding memories nearest the turn the background model is shown,
numbered, so it neither writes them down again nor misses what the turn supersedes."""
EXCHANGE_HISTORY = 4
"""How many of the messages before the user's latest the background model reads with it."""
MAX_NEW_MEMORIES = 5
"""The most memories one turn writes down."""
MAX_MEMORY_LENGTH = 200
"""How much of a memory, in characters, is kept; longer ones are cut short."""
CANDIDATES = 12
"""How many of the user's memories nearest the turn the reranker is offered."""
MAX_FOUND = 3
"""The most memories one turn's prompt carries, after reranking."""
LATELY = timedelta(days=14)
"""How long ago what she reaches out with as his news may have been written down."""
MAX_LATEST = 5
"""The most of his news she reaches out with, besides what is found."""
ANOTHER_IDOL = "事务所的另一位偶像"
"""Who a memory was told to, when that character has been removed from the configuration."""

# `memories` predates this module and keeps the columns and constraints it had in the
# store; `memory_vectors` holds each memory's embedding, per embedding model.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    source_character_id TEXT NOT NULL,
    conversation_id TEXT REFERENCES conversations(id),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    superseded_by INTEGER,
    UNIQUE (user_id, text)
);
-- Deleting a conversation clears this from the memories written down in it.
CREATE INDEX IF NOT EXISTS memories_by_conversation ON memories(conversation_id);
CREATE TABLE IF NOT EXISTS memory_vectors (
    memory_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    model TEXT NOT NULL,
    embedding BLOB NOT NULL,
    PRIMARY KEY (memory_id, model)
);
CREATE INDEX IF NOT EXISTS memory_vectors_by_user ON memory_vectors(user_id, model);
"""


@dataclass(frozen=True)
class Memory:
    """Something about a user worth keeping across conversations, written in Chinese,
    with the character it was told to (its source character)."""

    id: int
    user_id: str
    source_character_id: str
    source_character_name: str | None
    """None if that character has been removed from the configuration."""
    text: str
    created_at: str
    """When it was told, as an ISO timestamp in UTC."""
    superseded_by: int | None = None
    """The memory that superseded it, None while it holds (ADR-0014)."""
    conversation_id: str | None = None
    """The conversation it was told in; None if that is not known or no longer there."""


PackageKey = int | str
"""What a data package calls one of its memories: a key within the package only."""


@dataclass(frozen=True)
class CarriedMemory:
    """A memory carried in by a data package (GLOSSARY.md「数据包」): `key` and
    `superseded_by` are keys in the package, not ids here."""

    key: PackageKey
    source_character_id: str
    text: str
    created_at: str
    superseded_by: PackageKey | None
    conversation_id: str | None
    """The conversation here it was told in, None if it is not here."""


def add_carried(db: sqlite3.Connection, user_id: str, carried: Sequence[CarriedMemory]) -> int:
    """Add the memories `carried` to the user's through `db`, in the caller's transaction;
    returns how many were new. One whose text the user already has is not added again; what
    supersedes it, or what it says supersedes it, is the one already here. The new ones are
    superseded as the package says, by new ones or ones already here; those already here are
    left as they are, their superseding too. Their source characters are kept as they are,
    here or not. They are embedded afterwards, outside the transaction (`embed_carried`)."""
    ids: dict[PackageKey, int] = {}
    new: list[CarriedMemory] = []
    for memory in carried:
        kept = db.execute(
            "SELECT id FROM memories WHERE user_id = ? AND text = ?", (user_id, memory.text)
        ).fetchone()
        if kept is not None:
            ids[memory.key] = kept[0]
            continue
        cursor = db.execute(
            "INSERT INTO memories"
            " (user_id, source_character_id, conversation_id, text, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, memory.source_character_id, memory.conversation_id, memory.text,
             memory.created_at),
        )
        assert cursor.lastrowid is not None
        ids[memory.key] = cursor.lastrowid
        new.append(memory)
    db.executemany(
        "UPDATE memories SET superseded_by = ? WHERE id = ?",
        [
            (ids[m.superseded_by], ids[m.key])
            for m in new
            if m.superseded_by is not None and ids[m.superseded_by] != ids[m.key]
        ],
    )
    return len(new)


@dataclass(frozen=True)
class _Services:
    """What memory needs to be on."""

    embedder: Embedder
    reranker: Reranker
    min_relevance: Callable[[], float]


class Memories:
    """Every user's memories."""

    label = "记忆"
    unit = "条"

    def __init__(
        self,
        db: Database,
        characters: Mapping[str, Character],
        clock: Clock,
        *,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
        min_relevance: Callable[[], float] = lambda: DEFAULT_THRESHOLDS.memories,
    ) -> None:
        """Memories kept in `db`, next to the users and conversations they belong to; off
        unless `embedder` and `reranker` are both given, though `embedder` alone is enough to
        embed them again. A memory the reranker scores below what `min_relevance` says when
        it is found is not (see `rerank.KNOWN_THRESHOLDS`)."""
        self._db = db
        db.prepare(_open)
        self._characters = characters
        self._clock = clock
        self._embedder = embedder
        self._services = (
            _Services(embedder, reranker, min_relevance)
            if embedder is not None and reranker is not None
            else None
        )
        self._pending: dict[str, set[asyncio.Task[None]]] = {}  # user id → writing
        self._clearings: dict[str, int] = {}  # user id → how many times cleared

    async def begin(
        self,
        conversation: Conversation,
        timezone: str,
        llm: ChatLLM,
        hold: Callable[[], Callable[[], None]] | None = None,
    ) -> "MemoryTurn":
        """Begin a turn of `conversation`, whose user is in `timezone`, once what their
        earlier turns are writing down is written (a while at most). The turn writes down
        with `llm`, held on to with `hold` (see `TurnLLMs.hold_background`) until written."""
        clearings = self._clearings.get(conversation.user_id, 0)
        await self._settle(conversation.user_id)
        return MemoryTurn(self, conversation, timezone, clearings, llm, hold)

    async def entries(self, user_id: str) -> list[Memory]:
        """All of the user's memories, newest first, including what their turns so far
        are writing down (unless that takes too long). Not named `list`, which would
        shadow the builtin in this class's annotations."""
        await self._settle(user_id)
        return list(reversed(await self.kept(user_id)))

    async def delete(self, user_id: str, memory_id: int) -> bool:
        """Forget one of the user's memories; False if they have no such memory."""
        await self._settle(user_id)
        return await self._db.write(_delete, user_id, memory_id)

    async def clear(self, user_id: str) -> None:
        """Forget all of the user's memories, including those their turns so far are still
        writing down."""
        self._clearings[user_id] = self._clearings.get(user_id, 0) + 1
        await self._settle(user_id)
        await self._db.write(_clear, user_id)

    async def forget(self, user_id: str) -> None:
        """Drop whatever the user's turns so far are still writing down, before the user
        is deleted (see `database.delete_user_data`, which deletes their memories)."""
        self._clearings[user_id] = self._clearings.get(user_id, 0) + 1
        await self._settle(user_id)

    async def embed_missing(
        self,
        batch: int = REEMBED_BATCH,
        progress: Callable[[int], None] | None = None,
        *,
        user_id: str | None = None,
    ) -> int:
        """Embed every user's memories, or only those of `user_id`, that have no vector from
        the embedder given, `batch` at a time; returns how many it embedded, telling
        `progress` the count so far after each batch. Interrupted, what it finished is kept;
        a memory deleted meanwhile gets no vector."""
        embedder = self._reembedder()
        model = embedder.model
        done = 0
        after = 0
        while True:
            rows = await self._db.read(_unembedded, after, user_id, model, batch)
            if not rows:
                return done
            vectors = await embedder.embed_documents([Document(text) for _, _, text in rows])
            await self._db.write(_store_embedded, model, [
                (id, user_id, to_blob(v))
                for (id, user_id, _), v in zip(rows, vectors, strict=True)
            ])
            done += len(rows)
            after = rows[-1][0]
            if progress is not None:
                progress(done)

    def embed_carried(self, user_id: str) -> None:
        """Embed, in the background, the user's memories that have no vector yet, as those
        carried in by a data package (`add_carried`), with the deployer's embedder; those it
        fails to embed are embedded the next time the user's memories are searched. Their
        turns, listing and deleting wait briefly for it, as for what is being written down.
        Nothing to do while memory is off."""
        if self._embedder is None:
            return

        async def embed() -> None:
            try:
                await self.embed_missing(user_id=user_id)
            except Exception:
                log.warning("embedding carried memories failed; they are embedded when"
                            " searched", exc_info=True)

        task = asyncio.create_task(embed(), context=detached())
        self._hold(user_id, task)

    async def forget_other_models(self) -> int:
        """Drop every memory's vector not from the embedder given; returns how many there
        were."""
        return await self._db.write(_forget_other_models, self._reembedder().model)

    def _reembedder(self) -> Embedder:
        """The embedder given, which embedding the memories again needs."""
        if self._embedder is None:
            raise RuntimeError("embedding memories again needs the embedder they were opened with")
        return self._embedder

    async def aclose(self) -> None:
        """Let whatever is being written down finish, a while at most, then give up on it."""
        tasks = {t for pending in self._pending.values() for t in pending}
        if tasks:
            _, unfinished = await asyncio.wait(tasks, timeout=SETTLE_TIMEOUT)
            for task in unfinished:
                task.cancel()
            await asyncio.gather(*unfinished, return_exceptions=True)

    async def _settle(self, user_id: str) -> None:
        if pending := self._pending.get(user_id):
            await asyncio.wait(set(pending), timeout=SETTLE_TIMEOUT)

    async def _candidates(self, user_id: str, query_vector: Sequence[float]) -> list[Memory]:
        if (services := self._services) is None:
            return []
        try:
            return await self._nearest(services.embedder, user_id, query_vector, CANDIDATES)
        except EmbeddingError:
            log.warning("embedding memories failed; replying without them", exc_info=True)
            return []

    async def _find(
        self,
        conversation: Conversation,
        timezone: str,
        query_text: str,
        candidates: Sequence[Memory],
    ) -> str | None:
        found = await self._relevant(query_text, candidates)
        character = self._characters[conversation.character_id]
        return _memories_prompt(found, character, timezone, _FOUND) if found else None

    async def _relevant(self, query_text: str, candidates: Sequence[Memory]) -> list[Memory]:
        """As many as `MAX_FOUND` of the `candidates` relevant to `query_text`, most relevant
        first; none if memory is off or reranking failed."""
        if (services := self._services) is None or not candidates:
            return []
        try:
            best = await services.reranker.rerank(
                query_text, [m.text for m in candidates], len(candidates)
            )
        except RerankError:
            log.warning("finding memories failed; going on without them", exc_info=True)
            return []
        min_relevance = services.min_relevance()
        return [candidates[i] for i, score in best if score >= min_relevance][:MAX_FOUND]

    async def for_reaching_out(
        self,
        conversation: Conversation,
        timezone: str,
        last_turn: str | None,
        posting: bool = False,
    ) -> str | None:
        """The prompt section carrying the memories she reaches out with in `conversation`,
        whose user is in `timezone` (GLOSSARY.md「搭话」), or, `posting`, posts a moment with
        (GLOSSARY.md「朋友圈」), told only to echo them: of those the user told her herself
        that still hold, those relevant to `last_turn`, as a turn finds them (`last_turn_query`),
        then as many as `MAX_LATEST` of the latest written down within `LATELY`, each once;
        None if there are none, or memory is off."""
        if (services := self._services) is None:
            return None
        user_id, character_id = conversation.user_id, conversation.character_id
        await self._settle(user_id)
        found: list[Memory] = []
        if last_turn is not None:
            try:
                query_vector = await services.embedder.embed_query(last_turn)
                candidates = await self._nearest(
                    services.embedder, user_id, query_vector, CANDIDATES, character_id
                )
            except EmbeddingError:
                log.warning("embedding memories failed; reaching out with the latest only",
                            exc_info=True)
                candidates = []
            found = await self._relevant(last_turn, candidates)
        since = (self._clock() - LATELY).astimezone(UTC)
        latest = [
            m for m in _holding(await self.kept(user_id), character_id)
            if datetime.fromisoformat(m.created_at) >= since
        ][-MAX_LATEST:]
        ids = {m.id for m in found}
        recalled = [*found, *(m for m in reversed(latest) if m.id not in ids)]
        character = self._characters[character_id]
        return (
            _memories_prompt(recalled, character, timezone, _POSTING if posting else _REACHING_OUT)
            if recalled else None
        )

    async def _nearest(
        self,
        embedder: Embedder,
        user_id: str,
        query_vector: Sequence[float],
        limit: int,
        character_id: str | None = None,
    ) -> list[Memory]:
        """As many as `limit` of the user's holding memories nearest a query embedded with
        `embedder`, nearest first, only those told to the character `character_id` if given.
        Raises EmbeddingError, embedding those not embedded yet."""
        memories, stored = await self._db.read(
            self._kept_with_vectors, user_id, embedder.model
        )
        vectors = await self._vectors(embedder, user_id, memories, stored)
        holding = _holding(memories, character_id)
        found = await asyncio.to_thread(
            nearest, [vectors[m.id] for m in holding], query_vector, limit
        )
        return [holding[i] for i, _ in found]

    async def _shown(
        self,
        embedder: Embedder,
        user_id: str,
        query_text: str,
        query_vector: Sequence[float] | None,
    ) -> list[Memory]:
        """The memories the background model is shown: the user's holding memories nearest
        `query_text`, embedded unless it already was (`query_vector`), or while that cannot
        be embedded, their latest."""
        try:
            if query_vector is None:
                query_vector = await embedder.embed_query(query_text)
            return await self._nearest(embedder, user_id, query_vector, SHOWN_MEMORIES)
        except EmbeddingError:
            log.warning("embedding the turn failed; the background model is shown the latest"
                        " memories", exc_info=True)
            return _holding(await self.kept(user_id))[-SHOWN_MEMORIES:]

    def _write_down(
        self,
        conversation: Conversation,
        timezone: str,
        clearings: int,
        llm: ChatLLM,
        hold: Callable[[], Callable[[], None]] | None,
        history: Sequence[Message],
        text: str,
        reply: Message,
        query_vector: Sequence[float] | None,
    ) -> None:
        if (services := self._services) is None:
            return
        now = self._clock().astimezone(ZoneInfo(timezone))
        release = hold() if hold is not None else None
        task = asyncio.create_task(
            self._write(
                services, llm, conversation, clearings, history, text, reply, query_vector, now
            ),
            context=detached(),  # after the turn, not part of it (see `activity`)
        )
        self._hold(conversation.user_id, task)
        if release is not None:
            task.add_done_callback(lambda _: release())

    def _hold(self, user_id: str, task: asyncio.Task[None]) -> None:
        """Have the user's turns, listing and deleting wait (a while) for `task`."""
        pending = self._pending.setdefault(user_id, set())
        pending.add(task)

        def done(task: asyncio.Task[None]) -> None:
            pending.discard(task)
            if not pending and self._pending.get(user_id) is pending:
                del self._pending[user_id]

        task.add_done_callback(done)

    async def _write(
        self,
        services: _Services,
        llm: ChatLLM,
        conversation: Conversation,
        clearings: int,
        history: Sequence[Message],
        text: str,
        reply: Message,
        query_vector: Sequence[float] | None,
        now: datetime,
    ) -> None:
        user_id = conversation.user_id
        try:
            character = self._characters[conversation.character_id]
            shown = await self._shown(
                services.embedder, user_id, memory_query(history, text), query_vector
            )
            prompt = _extraction_prompt(character, history, text, reply, shown, now)
            with_images = await self._db.read(lambda db: db.execute(
                "SELECT 1 FROM user_images WHERE conversation_id = ? AND message_id IS NOT NULL LIMIT 1",
                (conversation.id,),
            ).fetchone() is not None)
            if with_images:
                prompt.insert(0, ChatMessage("system", "本对话包含图片。每条记忆必须增加 evidence 字段，"
                    "逐字引用制作人最新原话中明确描述或确认该事实的文字；没有证据就不记。"
                    "偶像的话或图片观察不能作为证据。"))
            answer = "".join([
                chunk async for chunk in llm.stream(prompt)
                if isinstance(chunk, str)
            ])
            written = parse_memories(answer, evidence_in=text if with_images else None)
            if written is None:
                log.warning(  # its length only: the answer is about the user
                    "background model wrote no memories JSON (%d characters) in conversation %s",
                    len(answer), conversation.id,
                )
                return
            if self._clearings.get(user_id, 0) != clearings:
                return  # cleared since the turn began
            added = await self._db.write(self._add, conversation, written, shown, now)
        except Exception:
            log.warning("writing memories failed in conversation %s", conversation.id,
                        exc_info=True)
            return
        try:
            if added:
                model = services.embedder.model
                stored = await self._db.read(_stored_vectors, user_id, model)
                await self._vectors(services.embedder, user_id, added, stored, forget=False)
        except EmbeddingError:
            log.warning("embedding new memories failed; they are embedded when searched",
                        exc_info=True)

    async def kept(self, user_id: str) -> list[Memory]:
        """All of the user's memories, oldest first, as they are now: not waiting for what
        is being written down (`entries` does)."""
        return await self._db.read(self.read_kept, user_id)

    def read_kept(self, db: sqlite3.Connection, user_id: str) -> list[Memory]:
        """What `kept` reads, read through `db`: for a data package too."""
        rows = db.execute(
            "SELECT id, user_id, source_character_id, text, created_at, superseded_by,"
            " conversation_id FROM memories WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
        return [self._memory(*row) for row in rows]

    def _kept_with_vectors(
        self, db: sqlite3.Connection, user_id: str, model: str
    ) -> tuple[list[Memory], dict[int, bytes]]:
        """All of the user's memories, and their vectors stored for `model`, as they were at
        one moment."""
        return self.read_kept(db, user_id), _stored_vectors(db, user_id, model)

    def _memory(
        self, id: int, user_id: str, source_character_id: str, text: str, created_at: str,
        superseded_by: int | None = None, conversation_id: str | None = None,
    ) -> Memory:
        source = self._characters.get(source_character_id)
        return Memory(
            id, user_id, source_character_id, source.name if source else None, text,
            created_at, superseded_by, conversation_id,
        )

    def _add(
        self,
        db: sqlite3.Connection,
        conversation: Conversation,
        written: Sequence["NewMemory"],
        shown: Sequence[Memory],
        now: datetime,
    ) -> list[Memory]:
        """Remember what the user told the conversation's character just `now`, each new
        memory superseding those it names among the memories the background model was
        `shown` that still hold; returns the memories that are new. One the user already
        has is left out, or if superseded, holds again and supersedes what it names."""
        added = []
        user_id = conversation.user_id
        created_at = now.astimezone(UTC).isoformat()
        for memory in written:
            kept = db.execute(
                "SELECT id, superseded_by IS NOT NULL FROM memories"
                " WHERE user_id = ? AND text = ?",
                (user_id, memory.text),
            ).fetchone()
            if kept is None:
                # Its conversation may have been deleted since the turn (GLOSSARY.md
                # 「历史对话」): the memory is kept all the same, saying where no longer.
                cursor = db.execute(
                    "INSERT INTO memories"
                    " (user_id, source_character_id, conversation_id, text, created_at)"
                    " VALUES (?, ?, (SELECT id FROM conversations WHERE id = ?), ?, ?)",
                    (user_id, conversation.character_id, conversation.id, memory.text,
                     created_at),
                )
                assert cursor.lastrowid is not None
                id = cursor.lastrowid
                added.append(self._memory(
                    id, user_id, conversation.character_id, memory.text, created_at
                ))
            else:
                id, superseded = kept
                if not superseded:
                    continue  # already remembered
                db.execute(
                    "UPDATE memories SET superseded_by = NULL WHERE id = ?", (id,)
                )
            # Only what still holds: a memory deleted or superseded since it was shown
            # is left as it is.
            db.executemany(
                "UPDATE memories SET superseded_by = ?"
                " WHERE id = ? AND user_id = ? AND superseded_by IS NULL",
                [
                    (id, shown[n - 1].id, user_id)
                    for n in memory.supersedes
                    if 1 <= n <= len(shown) and shown[n - 1].id != id
                ],
            )
        return added

    async def _vectors(
        self,
        embedder: Embedder,
        user_id: str,
        memories: list[Memory],
        stored: dict[int, bytes],
        forget: bool = True,
    ) -> dict[int, bytes]:
        """The vector of each memory, of those `stored` for the embedder's model, embedding
        those not yet stored and, if `memories` are all the user's, forgetting those of
        memories that are gone. A memory deleted while it was being embedded gets no
        vector."""
        model = embedder.model
        stored = dict(stored)
        missing = [m for m in memories if m.id not in stored]
        stale = stored.keys() - {m.id for m in memories} if forget else set()
        if missing:
            embedded = await embedder.embed_documents([Document(m.text) for m in missing])
            stored.update(zip((m.id for m in missing), map(to_blob, embedded), strict=True))
        if missing or stale:
            await self._db.write(
                _store_vectors, user_id, model, [(m.id, stored[m.id]) for m in missing],
                stale,
            )
        return stored


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)
    columns = {row[1] for row in db.execute("PRAGMA table_info(memories)")}
    if "superseded_by" not in columns:  # kept from before superseding: all hold
        db.execute("ALTER TABLE memories ADD COLUMN superseded_by INTEGER")


def _delete(db: sqlite3.Connection, user_id: str, memory_id: int) -> bool:
    """Delete the memory, what it superseded superseded instead by what superseded it, or
    holding again if nothing did."""
    deleted = db.execute(
        "SELECT superseded_by FROM memories WHERE id = ? AND user_id = ?",
        (memory_id, user_id),
    ).fetchone()
    if deleted is None:
        return False
    [superseded_by] = deleted
    db.execute(
        "UPDATE memories SET superseded_by = ? WHERE superseded_by = ? AND user_id = ?",
        (superseded_by, memory_id, user_id),
    )
    db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    db.execute(
        "DELETE FROM memory_vectors WHERE user_id = ? AND memory_id = ?", (user_id, memory_id)
    )
    return True


def _clear(db: sqlite3.Connection, user_id: str) -> None:
    db.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM memory_vectors WHERE user_id = ?", (user_id,))


def _unembedded(
    db: sqlite3.Connection, after: int, user_id: str | None, model: str, batch: int
) -> list[tuple[int, str, str]]:
    """As many as `batch` of every user's memories, or only those of `user_id`, after the
    id `after`, with no vector for `model`: each one's id, user and text."""
    return db.execute(
        "SELECT id, user_id, text FROM memories m WHERE id > ?"
        " AND (? IS NULL OR user_id = ?) AND NOT EXISTS"
        " (SELECT 1 FROM memory_vectors v WHERE v.memory_id = m.id AND v.model = ?)"
        " ORDER BY id LIMIT ?",
        (after, user_id, user_id, model, batch),
    ).fetchall()


def _store_embedded(
    db: sqlite3.Connection, model: str, embedded: Sequence[tuple[int, str, bytes]]
) -> None:
    """Store the vectors for `model` of the memories `embedded`, each with its user, but of
    those deleted meanwhile."""
    db.executemany(
        "INSERT OR REPLACE INTO memory_vectors (memory_id, user_id, model, embedding)"
        " SELECT ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM memories WHERE id = ?)",
        [(id, user_id, model, vector, id) for id, user_id, vector in embedded],
    )


def _forget_other_models(db: sqlite3.Connection, model: str) -> int:
    return db.execute("DELETE FROM memory_vectors WHERE model != ?", (model,)).rowcount


def _stored_vectors(db: sqlite3.Connection, user_id: str, model: str) -> dict[int, bytes]:
    """The vector of each of the user's memories stored for `model`."""
    return dict(
        db.execute(
            "SELECT memory_id, embedding FROM memory_vectors WHERE user_id = ? AND model = ?",
            (user_id, model),
        ).fetchall()
    )


def _store_vectors(
    db: sqlite3.Connection,
    user_id: str,
    model: str,
    added: Sequence[tuple[int, bytes]],
    stale: Iterable[int],
) -> None:
    """Store the `added` vectors for `model`, but of memories deleted meanwhile, and forget
    those of the `stale` memories."""
    _store_embedded(db, model, [(memory_id, user_id, vector) for memory_id, vector in added])
    db.executemany(
        "DELETE FROM memory_vectors WHERE memory_id = ? AND model = ?",
        [(memory_id, model) for memory_id in stale],
    )


class MemoryTurn:
    """One turn's use of the user's memories; see `Memories.begin`."""

    def __init__(
        self,
        memories: Memories,
        conversation: Conversation,
        timezone: str,
        clearings: int,
        llm: ChatLLM,
        hold: Callable[[], Callable[[], None]] | None = None,
    ) -> None:
        self._memories = memories
        self._conversation = conversation
        self._timezone = timezone
        self._clearings = clearings
        self._llm = llm
        self._hold = hold

    async def candidates(self, query_vector: Sequence[float]) -> list[Memory]:
        """As many as `CANDIDATES` of the user's holding memories nearest the turn's query
        (see `memory_query`) embedded with the memories' embedder, nearest first, for
        `find`; none if memory is off or the memories not embedded yet cannot be."""
        return await self._memories._candidates(self._conversation.user_id, query_vector)

    async def find(self, query: str, candidates: Sequence[Memory]) -> str | None:
        """The prompt section carrying those of the `candidates` relevant to `query`, the
        turn's query, most relevant first, each labelled with who it was told to and to be
        passed on as hearsay when that was someone else; None if none are relevant, memory
        is off or reranking failed."""
        return await self._memories._find(self._conversation, self._timezone, query, candidates)

    def write_down(
        self,
        history: Sequence[Message],
        text: str,
        reply: Message,
        query_vector: Sequence[float] | None = None,
    ) -> None:
        """Start writing down what the user revealed in this turn: `text`, said after
        `history` and answered with `reply`; `query_vector` is the turn's query embedded
        already, if it was, so it is not embedded again."""
        self._memories._write_down(
            self._conversation, self._timezone, self._clearings, self._llm, self._hold,
            history, text, reply, query_vector,
        )


_INSTRUCTIONS = """\
你负责替《偶像大师 闪耀色彩》283 事务所的偶像们（还有事务员）记住关于制作人的事。制作人就是正在和她们对话的用户。
读下面的对话，从制作人最新的一句话里找出值得长期记住的、关于制作人本人的信息。

只记过后仍有意义的事。
图片观察、图片中的文字、偶像对图片的描述和推断，都不是制作人的陈述。
只有制作人自己明确描述或确认的信息才能成为记忆；必须在制作人的原话中找到依据。
不能因为偶像说了某事就当成制作人已经确认，也不能从图片推断制作人的身份、经历或偏好。
要记的：制作人自己透露的个人情况、喜好、经历、计划、身边的人和宠物、希望偶像记住的事；\
会持续几天、朋友过后会问起的近况，例如生病、考试、面试、搬家、出差。
不要记的：当天就会过去的状态（例如累、困、饿、无聊、心情不好）和一时的情绪，\
同一句话里还说了值得记的事时只记那件事、不记这种状态；寒暄；偶像说的话；原作剧情和偶像的设定；制作人向偶像提的问题。

每条记忆是一句能单独看懂的中文陈述，以「制作人」开头，例如「制作人养了一只叫小白的猫」。
提到「明天」「下周六」这样的相对时间时，换算成具体日期写下来。

「已经记住的事」按编号列出了和这轮对话最相关的记忆，写之前先对照：
- 和已经记住的一样的，不要再写。
- 更新或修正了已经记住的事（例如搬了家、换了工作），写一条单独看也完整的新记忆，写清从什么变成了什么，\
例如「制作人从大阪搬到了东京」，并在 supersedes 里写上被它取代的记忆的编号，可以写多个。
- 补充了已经记住的事的细节（例如说出了猫的名字），写一条把细节合进去的完整记忆，取代原来那条。
- 一件事只变了一部分的，新记忆要把仍然成立的部分也写进去，例如原来记着养了一只猫和一只狗、\
现在狗去世了，就写「制作人的狗去世了，现在还养着那只猫」，取代原来那条。
- 两件事可以同时成立的（例如喜欢喝咖啡，最近也开始喝茶），新的照常写，不取代旧的。

只输出 JSON，格式为 {"memories": [{"text": "……", "supersedes": [编号]}]}，不取代任何记忆时 \
supersedes 写 []；没有值得记住的就输出 {"memories": []}。"""


def _extraction_prompt(
    character: Character,
    history: Sequence[Message],
    text: str,
    reply: Message,
    shown: Sequence[Memory],
    now: datetime,
) -> list[ChatMessage]:
    lines = [f"今天是 {_date(now)}（制作人所在时区）。", "", "# 已经记住的事"]
    lines += [f"{n}. {m.text}" for n, m in enumerate(shown, 1)] or ["（还没有）"]
    lines += ["", f"# 制作人和{character.name}的对话"]
    for message in history[-EXCHANGE_HISTORY:]:
        if message.role == "user":
            lines.append(f"制作人：{message.text}")
        else:
            lines.append(f"{character.name}：{_said(message)}")
    lines += [f"制作人（最新的一句话）：{text}", f"{character.name}：{_said(reply)}"]
    return [ChatMessage("system", _INSTRUCTIONS), ChatMessage("user", "\n".join(lines))]


def memory_query(history: Sequence[Message], text: str) -> str:
    """What memories relevant to `text`, said after `history`, are searched and reranked
    with, and references and recalled episodes too (`retrieval`): the character's reply
    before it, which says what 「它」 or 「那件事」 is, then `text`."""
    previous = next((m for m in reversed(history) if m.role == "character"), None)
    return joined_query(_said(previous) if previous else None, text)


def last_turn_query(messages: Sequence[Message]) -> str | None:
    """What the memories she reaches out with are found with (`Memories.for_reaching_out`):
    the last turn of `messages`, what the user said, then her reply as people see it; None
    before the first."""
    said = next((i for i in reversed(range(len(messages))) if messages[i].role == "user"), None)
    if said is None:
        return None
    replies = [_said(m) for m in messages[said + 1 :] if m.role == "character"]
    return "\n".join([messages[said].text or "", *replies])


def joined_query(previous_reply: str | None, text: str) -> str:
    """`memory_query` of `text` said after `previous_reply`, as people see it, if any."""
    return f"{previous_reply}\n{text}" if previous_reply else text


def _said(reply: Message) -> str:
    reply = reply.without_cues()
    return reply.zh or reply.ja or ""


@dataclass(frozen=True)
class NewMemory:
    """A memory the background model wrote down."""

    text: str
    supersedes: tuple[int, ...] = ()
    """The numbers it was shown the memories this one supersedes under, counting from 1;
    any it was not shown are left for `Memories._add` to ignore."""


def parse_memories(answer: str, *, evidence_in: str | None = None) -> list[NewMemory] | None:
    """The memories in the background model's answer; None if it is not the JSON asked for.
    A memory written as text alone, as before superseding, supersedes nothing; what it
    supersedes is read as whole numbers only."""
    start, end = answer.find("{"), answer.rfind("}")
    try:
        memories = json.loads(answer[start : end + 1])["memories"] if start >= 0 else None
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(memories, list):
        return None
    written: dict[str, NewMemory] = {}
    for memory in memories:
        if evidence_in is not None:
            evidence = memory.get("evidence") if isinstance(memory, dict) else None
            if not isinstance(evidence, str) or not evidence.strip() or evidence not in evidence_in:
                continue
        if isinstance(memory, dict):
            text, supersedes = memory.get("text"), memory.get("supersedes")
        else:
            text, supersedes = memory, None
        if not isinstance(text, str) or not (text := text.strip()[:MAX_MEMORY_LENGTH]):
            continue
        numbers = supersedes if isinstance(supersedes, list) else []
        written.setdefault(text, NewMemory(
            text, tuple(dict.fromkeys(n for n in numbers if type(n) is int))
        ))
    return list(written.values())[:MAX_NEW_MEMORIES]


_FOUND = (
    "下面是制作人以前说过的、和这句话可能有关的事，按相关程度排列，和话题无关的可以忽略。"
    "像平时记得这些事一样自然地提起，不要逐条复述。"
)
_REACHING_OUT = (
    "下面是制作人以前对你说过的事：先是和你们上次聊的可能有关的，再是制作人这两周的近况。"
    "挑一两件自然地提起，不要逐条复述。"
)

_POSTING = (
    "下面是制作人以前对你说过的事：先是和你们上次聊的可能有关的，再是制作人这两周的近况。"
    "朋友圈大家都看得到：最多隐约呼应一件，不要复述，他私下告诉你的事不要点明。"
)


def _holding(memories: Sequence[Memory], character_id: str | None = None) -> list[Memory]:
    """Those of `memories` that still hold, only those told to `character_id` if given."""
    return [
        m for m in memories
        if m.superseded_by is None and character_id in (None, m.source_character_id)
    ]


def _memories_prompt(
    memories: Sequence[Memory], character: Character, timezone: str, opening: str
) -> str:
    """The prompt section carrying `memories`, after its heading and `opening`, which says
    what they are."""
    lines = ["# 关于制作人的记忆", opening]
    for memory in memories:
        when = _date(datetime.fromisoformat(memory.created_at).astimezone(ZoneInfo(timezone)))
        if memory.source_character_id == character.id:
            lines.append(f"- {memory.text}（{when}，制作人对你说的）")
        else:
            name = memory.source_character_name or ANOTHER_IDOL
            lines.append(
                f"- {memory.text}（{when}，制作人对{name}说的，你是听{name}说的：提起时要用转述的口吻，"
                f"例如「听{name}说……」，不要说成是制作人亲口告诉你的）"
            )
    return "\n".join(lines)


def _date(moment: datetime) -> str:
    return f"{moment.year}年{moment.month}月{moment.day}日"


def posting_memories_prompt(
    memories: Sequence[Memory], character: Character, timezone: str
) -> str:
    """The prompt section carrying `memories` as she posts a moment with them, in the order
    given, the user in `timezone`: as `Memories.for_reaching_out` writes it, `posting`."""
    return _memories_prompt(memories, character, timezone, _POSTING)
