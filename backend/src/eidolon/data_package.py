"""Data packages (GLOSSARY.md「数据包」, ADR-0025): a user's own data as a zip they keep,
exported here and imported into this deployment or another, under this account or another.
The format is described for people in `docs/data-package-format.md`.

A package holds `manifest.json` first, stored uncompressed so that a browser can read it
from the start of the file alone; then the picture of each photo sent, a WebP of its own in
`photos/`; then `data.json`, indented, in English keys; and nothing else. The manifest
says which format the package is in (`FORMAT`), when it was exported, which categories it
holds and how many of each. A deployment imports packages of its own format and older ones,
and refuses newer ones (`TooNew`) rather than drop what it cannot read. Anything else wrong with a package refuses it as broken (`BrokenPackage`).

Importing unzips and checks a package first, off the database, then writes everything in
one write task (`data_access`), one transaction, so that a package fails as a whole and
leaves nothing behind, and holds up no one else's writes while it is unzipped. Neither
exporting nor importing is recorded: no activity record, nothing an admin sees."""

import asyncio
import base64
import io
import json
import sqlite3
import zipfile
from collections.abc import Callable, Container, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, get_args

from PIL import Image
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    model_validator,
)

from eidolon.affinity import Change, Tier, next_day
from eidolon import cycle_records
from eidolon.characters import Character
from eidolon.data_access import Database
from eidolon.gift_catalog import Gift
from eidolon.letter_prompt import Occasion, Paragraph, Written
from eidolon.letters import Carried as CarriedLetter
from eidolon.letters import add_carried as add_carried_letters
from eidolon.letters import read_carried as read_carried_letters
from eidolon.memory import (
    MAX_MEMORY_LENGTH,
    CarriedMemory,
    Memories,
    Memory,
    PackageKey,
    add_carried,
)
from eidolon.producer import (
    MAX_CITY_PART_LENGTH,
    MAX_FORM_OF_ADDRESS_LENGTH,
    Birthday,
    City,
    Gender,
    ProducerProfile,
    is_day_of_the_year,
)
from eidolon.reply_format import Segment
from eidolon.store import (
    ConnectedCall,
    EventKind,
    KeptEvent,
    KeptMessage,
    KeptPhoto,
    KeptReachOut,
    PhotoFailure,
    Role,
    Store,
    WholeConversation,
    add_carried_conversation,
    read_profile,
    read_whole_conversations,
    write_profile,
)

FORMAT = 6
"""The newest format this version reads, and the one it exports. Changing the format adds
one to it, and keeps reading the older ones: 2 added her reach-outs (`reach_outs`), 3 her
being unwell among the events (`unwell`) and the periods the user wrote down (`cycles`), 4
the producer's city in the profile (`city`), 5 the letters she wrote (`letters`)."""

REACH_OUTS_SINCE = 2
"""The format whose conversations first held her reach-outs: before it, none may."""

UNWELL_SINCE = 3
"""The format whose conversations first held her being unwell: before it, none may."""

CYCLES_SINCE = 3
"""The format whose packages first held the periods the user wrote down: before it, none may."""

CITY_SINCE = 4
"""The format whose profiles first held the producer's city: before it, none may."""

LETTERS_SINCE = 5
"""The format whose packages first held the letters she wrote: before it, none may."""

Category = Literal["conversations", "photos", "memories", "profile", "cycles", "letters"]
CATEGORIES: tuple[Category, ...] = get_args(Category)
"""What a package may hold, in the order a manifest lists them. Photos go with the
conversations they were sent in, never without them."""

MANIFEST = "manifest.json"
DATA = "data.json"
PHOTOS = "photos/"
"""Where in a package the picture of each photo sent is: `photos/<its id>.webp`."""

MAX_PACKAGE_BYTES = 200 * 1024 * 1024
"""How large a package may be to import: a package with many photos is large, and one too
large for this can be exported again without them."""

MAX_MANIFEST_BYTES = 64 * 1024
"""How large `manifest.json` may be unzipped: it is a few lines."""

MAX_DATA_BYTES = 200 * 1024 * 1024
"""How large `data.json` may be unzipped, so that a small zip cannot unzip to fill the
memory."""

MAX_PHOTO_BYTES = 20 * 1024 * 1024
"""How large the picture of a photo may be unzipped: one is some hundred KB."""

MAX_PICTURES_BYTES = 200 * 1024 * 1024
"""How large the pictures of a package's photos may be unzipped, all together: they are held
unzipped until they are written. A package exported here stores them as they are, so its
pictures are never larger than it is."""

PICTURE_WAIT = 60.0
"""Seconds a package being written waits for the picture of a photo to be read before it
gives up, the server stopping meanwhile, say."""

IMPORTS_PER_DAY = 3
"""How many imports a user may make a day (`affinity.day_of`): only those that succeed count,
even one that added nothing."""


class TooNew(Exception):
    """A package in a format newer than this version reads: the deployer should upgrade."""


class Busy(Exception):
    """The user is already importing or exporting a package: one at a time."""


class ImportsUsedUp(Exception):
    """The user has made today's imports (`IMPORTS_PER_DAY`); they can again at `retry_at`,
    as the next day begins."""

    def __init__(self, retry_at: datetime, now: datetime) -> None:
        super().__init__(f"今天的导入次数已经用完了（每天 {IMPORTS_PER_DAY} 次）")
        self.retry_at = retry_at
        self.now = now


class TooLarge(Exception):
    """A package larger than `MAX_PACKAGE_BYTES`, refused before the rest of it is read."""


class BrokenPackage(Exception):
    """Not a package this version can import: not a zip, entries it should not have, or
    JSON not in the format."""


def _stored_time(value: str) -> str:
    """A time as the database writes it (`_written`), from any ISO 8601 time with its zone:
    conversations and events are ordered by their times as text."""
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError("a time without its zone")
    return _written(at)


def _written(at: datetime) -> str:
    """`at` as packages and the database write times: UTC, ISO 8601 with microseconds."""
    return at.astimezone(UTC).isoformat(timespec="microseconds")


Time = Annotated[str, AfterValidator(_stored_time)]


def _day(value: str) -> str:
    """A calendar day, as the database writes it: ISO 8601, nothing else."""
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("not a day as written")
    return value


Day = Annotated[str, AfterValidator(_day)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


CityPart = Annotated[str, Field(max_length=MAX_CITY_PART_LENGTH)]


class PackagedCity(_Strict):
    """The producer's city, as QWeather found it (`producer.City`)."""

    name: Annotated[str, Field(min_length=1, max_length=MAX_CITY_PART_LENGTH)]
    adm2: CityPart
    adm1: CityPart
    country: CityPart
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]


class PackagedProfile(_Strict):
    form_of_address: Annotated[str, Field(max_length=MAX_FORM_OF_ADDRESS_LENGTH)] | None
    gender: Gender | None
    timezone: Annotated[str, Field(max_length=100)] | None
    """As kept, even a zone this server does not know: it is passed over for the device's
    (`producer.effective_timezone`)."""
    birthday_month: int | None
    birthday_day: int | None
    city: PackagedCity | None = None
    """Since format 4 (`CITY_SINCE`)."""

    @model_validator(mode="after")
    def _birthday(self) -> "PackagedProfile":
        month, day = self.birthday_month, self.birthday_day
        if (month is None) != (day is None):
            raise ValueError("half a birthday")
        if month is not None and day is not None and not is_day_of_the_year(month, day):
            raise ValueError("no such day")
        return self

    @classmethod
    def of(cls, profile: ProducerProfile) -> "PackagedProfile":
        birthday = profile.birthday
        return cls(
            form_of_address=profile.form_of_address,
            gender=profile.gender,
            timezone=profile.timezone,
            birthday_month=birthday and birthday.month,
            birthday_day=birthday and birthday.day,
            city=PackagedCity(**asdict(profile.city)) if profile.city else None,
        )

    def profile(self) -> ProducerProfile:
        birthday = (
            Birthday(self.birthday_month, self.birthday_day)
            if self.birthday_month is not None and self.birthday_day is not None
            else None
        )
        city = City(**self.city.model_dump()) if self.city else None
        return ProducerProfile(
            self.form_of_address or None, self.gender, self.timezone or None, birthday, city
        )


PackageId = StrictInt | Annotated[str, Field(min_length=1, max_length=100)]
"""What a package calls one of its memories or conversations: a key within the package
only, the id it had where it was exported."""


class PackagedChange(_Strict):
    """How a turn or a gift changed her affinity, as kept with it (`affinity.Change`)."""

    up: bool
    tier_changed: bool
    tier: Tier
    cooldown: bool
    thawed: bool = False

    @classmethod
    def of(cls, change: Change | None) -> "PackagedChange | None":
        return change and cls(**asdict(change))

    def change(self) -> Change:
        return Change(**self.model_dump())


class PackagedSegment(_Strict):
    ja: str
    zh: str

    @classmethod
    def all_of(cls, segments: list[Segment]) -> "list[PackagedSegment]":
        return [cls(ja=s.ja, zh=s.zh) for s in segments]

    def kept(self) -> Segment:
        return Segment(self.ja, self.zh)


PhotoId = Annotated[str, Field(pattern=r"^[0-9A-Za-z_-]{1,100}$")]
"""What a package calls one of its photos, the name of its picture in it."""


class PackagedPhoto(_Strict):
    """The photo she sent with a reply: sent (`ready`), its picture in the package, or not
    sent after all (`failed`), and why not."""

    id: PhotoId
    tag: str
    status: Literal["ready", "failed"]
    reason: PhotoFailure | None
    created_at: Time
    updated_at: Time

    @property
    def sent(self) -> bool:
        return self.status == "ready"

    @model_validator(mode="after")
    def _why_not_if_not(self) -> "PackagedPhoto":
        if self.sent == (self.reason is not None):
            raise ValueError("a photo not sent without why, or why for one sent")
        return self

    @classmethod
    def of(cls, photo: KeptPhoto) -> "PackagedPhoto":
        """The photo; one still being generated as not sent for taking too long, as when the
        server restarts."""
        if photo.status == "pending":
            status, reason = "failed", "timeout"
        else:
            status, reason = photo.status, photo.reason
        return cls(
            id=photo.id, tag=photo.tag, status=status, reason=reason,
            created_at=photo.created_at, updated_at=photo.updated_at,
        )

    def not_sent(self) -> "PackagedPhoto":
        """The photo, sent but its picture gone as the package was written: as not sent."""
        return self.model_copy(update={"status": "failed", "reason": "timeout"})

    def kept(self) -> KeptPhoto:
        """The photo to add, its id still the package's."""
        return KeptPhoto(
            self.id, self.tag, self.status, self.reason, self.created_at, self.updated_at
        )


def sent(photos: list[PackagedPhoto]) -> list[PackagedPhoto]:
    """Those of `photos` she sent, whose pictures are in the package: what it counts as photos."""
    return [p for p in photos if p.sent]


class PackagedMessage(_Strict):
    role: Role
    text: str | None
    ja: str | None
    zh: str | None
    segments: list[PackagedSegment]
    created_at: Time
    affinity: PackagedChange | None
    hurt: float | None
    out_of_line: bool | None
    user_image: Annotated[str, Field(max_length=14_000_000)] | None = None
    image_observation: Annotated[str, Field(max_length=16000)] | None = None
    photo: PackagedPhoto | None = None
    """The photo she sent with her reply; no key for a message without one, or in a package
    without photos."""

    @model_validator(mode="after")
    def _photos_hers(self) -> "PackagedMessage":
        if self.user_image is not None:
            if self.role != "user":
                raise ValueError("user image on a character message")
            from eidolon.user_images import normalize
            normalize(base64.b64decode(self.user_image, validate=True))
            if self.image_observation:
                observation = json.loads(self.image_observation)
                if (not isinstance(observation, dict) or set(observation) != {"visible", "text", "relevant", "uncertain"}
                    or any(not isinstance(v, str) or len(v) > 2000 for v in observation.values())):
                    raise ValueError("invalid image observation")
        if self.photo is not None and self.role != "character":
            raise ValueError("a photo the user sent")
        return self

    @classmethod
    def of(cls, message: KeptMessage, with_photos: bool) -> "PackagedMessage":
        """The message, with its photo, if any, if `with_photos`."""
        packaged = cls(
            role=message.role, text=message.text, ja=message.ja, zh=message.zh,
            segments=PackagedSegment.all_of(message.segments),
            created_at=message.created_at, affinity=PackagedChange.of(message.affinity),
            hurt=message.hurt, out_of_line=message.out_of_line,
        )
        if with_photos and message.user_image is not None:
            packaged.user_image = message.user_image
            if message.image_observation is not None:
                packaged.image_observation = message.image_observation
        if with_photos and message.photo is not None:
            packaged.photo = PackagedPhoto.of(message.photo)
        return packaged

    def kept(self, with_photos: bool) -> KeptMessage:
        """The message to add, with its photo, if any, if `with_photos`."""
        return KeptMessage(
            self.role, self.text, self.ja, self.zh,
            [s.kept() for s in self.segments], self.created_at,
            self.affinity and self.affinity.change(), self.hurt, self.out_of_line,
            self.photo.kept() if with_photos and self.photo is not None else None,
            self.user_image if with_photos else None, self.image_observation if with_photos else None,
        )


class PackagedEvent(_Strict):
    kind: EventKind
    created_at: Time
    affinity: PackagedChange | None
    gift_id: Annotated[str, Field(min_length=1, max_length=100)] | None
    """Which gift, for a gift's alone."""
    liked: bool
    first_day: Day | None = None
    """For her being unwell alone: the first day of her period."""
    days_ago: Annotated[int, Field(ge=0)] | None = None
    """For her being unwell alone: how many days before the day it was recorded on."""

    @model_validator(mode="after")
    def _gift_for_a_gift(self) -> "PackagedEvent":
        if (self.kind == "gift") != (self.gift_id is not None):
            raise ValueError("a gift without which, or which gift for no gift")
        unwell = self.kind == "unwell"
        if unwell != (self.first_day is not None) or unwell != (self.days_ago is not None):
            raise ValueError("being unwell without since when, or since when for no such")
        return self

    @classmethod
    def of(cls, event: KeptEvent) -> "PackagedEvent":
        return cls(
            kind=event.kind, created_at=event.created_at,
            affinity=PackagedChange.of(event.affinity), gift_id=event.gift_id, liked=event.liked,
            first_day=event.first_day, days_ago=event.days_ago,
        )

    def kept(self) -> KeptEvent:
        return KeptEvent(
            self.kind, self.created_at, self.affinity and self.affinity.change(), self.gift_id,
            self.liked, self.first_day, self.days_ago,
        )


class PackagedCall(_Strict):
    connected_at: Time
    duration_s: Annotated[float, Field(ge=0)]


class PackagedReachOut(_Strict):
    """What she said first in the conversation, as the model wrote it, delivery cues and all."""

    ja: str
    zh: str
    segments: list[PackagedSegment]
    created_at: Time

    @classmethod
    def of(cls, reach_out: KeptReachOut) -> "PackagedReachOut":
        return cls(
            ja=reach_out.ja, zh=reach_out.zh, segments=PackagedSegment.all_of(reach_out.segments),
            created_at=reach_out.created_at,
        )

    def kept(self) -> KeptReachOut:
        return KeptReachOut(self.ja, self.zh, [s.kept() for s in self.segments], self.created_at)


class PackagedConversation(_Strict):
    id: PackageId
    character_id: Annotated[str, Field(min_length=1, max_length=100)]
    character_name: Annotated[str, Field(max_length=100)] | None
    """Her name where it was exported, for whoever reads the package, and for telling the
    user whose were skipped; None if she was no longer there."""
    created_at: Time
    title: str | None
    messages: list[PackagedMessage]
    """In the order they were recorded."""
    events: list[PackagedEvent]
    calls: list[PackagedCall]
    reach_outs: list[PackagedReachOut] = []
    """In the order they were recorded; none without the key, as in a package of a format
    before them."""

    @classmethod
    def of(
        cls, conversation: WholeConversation, name: str | None, with_photos: bool
    ) -> "PackagedConversation":
        """The conversation, with the photos she sent in it if `with_photos`."""
        return cls(
            id=conversation.id,
            character_id=conversation.character_id,
            character_name=name,
            created_at=conversation.created_at,
            title=conversation.title,
            messages=[PackagedMessage.of(m, with_photos) for m in conversation.messages],
            events=[PackagedEvent.of(e) for e in conversation.events],
            calls=[
                PackagedCall(connected_at=k.connected_at, duration_s=k.duration_s)
                for k in conversation.calls
            ],
            reach_outs=[PackagedReachOut.of(r) for r in conversation.reach_outs],
        )

    def photos(self) -> list[PackagedPhoto]:
        """The photos she sent in it, in their order."""
        return [m.photo for m in self.messages if m.photo is not None]

    def whole(self, gifts: Container[str], with_photos: bool) -> WholeConversation:
        """The conversation to add, with its photos if `with_photos`, but the gifts given in
        it that are not among `gifts`: her prompt names what she was given by the gift
        catalog."""
        return WholeConversation(
            self.id, self.character_id, self.created_at, self.title,
            [m.kept(with_photos) for m in self.messages],
            [e.kept() for e in self.events if e.gift_id is None or e.gift_id in gifts],
            [ConnectedCall(k.connected_at, k.duration_s) for k in self.calls],
            [r.kept() for r in self.reach_outs],
        )


class PackagedMemory(_Strict):
    id: PackageId
    source_character_id: Annotated[str, Field(min_length=1, max_length=100)]
    source_character_name: Annotated[str, Field(max_length=100)] | None
    """Her name where it was exported, for whoever reads the package; None if she was no
    longer there."""
    conversation: PackageId | None
    """The package's conversation it was told in; None without it."""
    text: Annotated[str, Field(min_length=1, max_length=MAX_MEMORY_LENGTH)]
    created_at: Time
    superseded_by: PackageId | None

    @classmethod
    def of(cls, memory: Memory, with_conversations: bool) -> "PackagedMemory":
        """The memory, saying which conversation it was told in if `with_conversations`, the
        package carrying them."""
        return cls(
            id=memory.id,
            source_character_id=memory.source_character_id,
            source_character_name=memory.source_character_name,
            conversation=memory.conversation_id if with_conversations else None,
            text=memory.text,
            created_at=_stored_time(memory.created_at),
            superseded_by=memory.superseded_by,
        )

    def carried(self, conversations: Mapping[PackageKey, str]) -> CarriedMemory:
        """The memory to add, told in the conversation here that `conversations` has for the
        package's; none if it has none, the package's not imported."""
        return CarriedMemory(
            key=self.id,
            source_character_id=self.source_character_id,
            text=self.text,
            created_at=self.created_at,
            superseded_by=self.superseded_by,
            conversation_id=None if self.conversation is None
            else conversations.get(self.conversation),
        )


class PackagedCycle(_Strict):
    """A period the user wrote down of a character (GLOSSARY.md「周期」): whose, from when, how
    many days; days out of bounds are skipped as it is imported, not refused."""

    character_id: Annotated[str, Field(min_length=1, max_length=100)]
    first_day: Day
    days: StrictInt

    @classmethod
    def of(cls, record: cycle_records.Carried) -> "PackagedCycle":
        return cls(
            character_id=record.character_id, first_day=record.first_day.isoformat(),
            days=record.days,
        )

    def carried(self) -> cycle_records.Carried:
        return cycle_records.Carried(
            self.character_id, date.fromisoformat(self.first_day), self.days
        )


class PackagedParagraph(_Strict):
    ja: str
    zh: str


class PackagedLetter(_Strict):
    """A letter she wrote the producer (GLOSSARY.md「信」), as she wrote it, delivery cues and
    all; not her voice."""

    character_id: Annotated[str, Field(min_length=1, max_length=100)]
    character_name: Annotated[str, Field(max_length=100)] | None
    """Her name where it was exported, for whoever reads the package; None if she was no
    longer there."""
    occasions: Annotated[list[Occasion], Field(min_length=1)]
    day: Day
    """The day she wrote it, where the producer was."""
    written_at: Time
    salutation: str
    paragraphs: Annotated[list[PackagedParagraph], Field(min_length=1)]

    @model_validator(mode="after")
    def _each_occasion_once(self) -> "PackagedLetter":
        if len(set(self.occasions)) != len(self.occasions):
            raise ValueError("an occasion twice")
        return self

    @classmethod
    def of(cls, letter: CarriedLetter, name: str | None) -> "PackagedLetter":
        return cls(
            character_id=letter.character_id,
            character_name=name,
            occasions=list(letter.occasions),
            day=letter.day.isoformat(),
            written_at=_written(letter.written_at),
            salutation=letter.written.salutation,
            paragraphs=[
                PackagedParagraph(ja=p.ja, zh=p.zh) for p in letter.written.paragraphs
            ],
        )

    def carried(self) -> CarriedLetter:
        return CarriedLetter(
            self.character_id, tuple(self.occasions), date.fromisoformat(self.day),
            datetime.fromisoformat(self.written_at),
            Written(self.salutation, tuple(Paragraph(p.ja, p.zh) for p in self.paragraphs)),
        )


class PackageData(_Strict):
    """`data.json`: a key for each category the package holds, none for the others."""

    profile: PackagedProfile | None = None
    conversations: list[PackagedConversation] | None = None
    memories: list[PackagedMemory] | None = None
    cycles: list[PackagedCycle] | None = None
    letters: list[PackagedLetter] | None = None

    @model_validator(mode="after")
    def _memories_told_in_conversations(self) -> "PackageData":
        ids = [c.id for c in self.conversations or []]
        if len(set(ids)) != len(ids):
            raise ValueError("two conversations under one id")
        for m in self.memories or []:
            if m.conversation is not None and m.conversation not in ids:
                raise ValueError("a memory told in no conversation of the package")
        return self

    @model_validator(mode="after")
    def _photos_one_by_one(self) -> "PackageData":
        ids = [p.id for p in self.photos()]
        if len(set(ids)) != len(ids):
            raise ValueError("two photos under one id")
        return self

    def photos(self) -> list[PackagedPhoto]:
        """The photos she sent in all its conversations."""
        return [p for c in self.conversations or [] for p in c.photos()]

    @model_validator(mode="after")
    def _memories_superseded_by_memories(self) -> "PackageData":
        ids = [m.id for m in self.memories or []]
        if len(set(ids)) != len(ids):
            raise ValueError("two memories under one id")
        for m in self.memories or []:
            if m.superseded_by is not None and (
                m.superseded_by == m.id or m.superseded_by not in ids
            ):
                raise ValueError("a memory superseded by no other memory of the package")
        return self


class Manifest(_Strict):
    format: StrictInt
    exported_at: Time
    categories: list[Category]
    counts: dict[Category, StrictInt]


class _Format(BaseModel):
    """Just the format of a manifest, read before the rest, whose shape it decides."""

    format: StrictInt


@dataclass
class Skipped:
    """Her conversations in a package that were not imported: she is not here."""

    character_name: str
    conversations: int


@dataclass
class Imported:
    """What an import added: only what is new, so importing the same package twice adds
    nothing the second time. `profile` is whether the producer profile was overwritten with
    one other than it was."""

    conversations: int = 0
    photos: int = 0
    """Only those sent, with their pictures: one not sent says only that she did not send it."""
    memories: int = 0
    profile: bool = False
    skipped: list[Skipped] = field(default_factory=list)
    cycles: int = 0
    cycles_skipped: int = 0
    """The periods written down that were not imported: of a character not here, out of
    bounds, after today, or overlapping another of hers."""
    letters: int = 0
    """Only those not here already: of her, written at the same time."""
    letters_skipped: int = 0
    """The letters not imported, of characters not here."""


def chosen(include: Iterable[Category]) -> list[Category]:
    """The categories asked for, in their order; photos only with conversations."""
    asked = set(include)
    if "conversations" not in asked:
        asked.discard("photos")
    return [c for c in CATEGORIES if c in asked]


_SCHEMA = """
-- One row per import a user made that succeeded, at when it did: how many they have made
-- today. Those of earlier days are deleted as the next is made.
CREATE TABLE IF NOT EXISTS data_package_imports (
    user_id TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS data_package_imports_by_user ON data_package_imports (user_id, at);
"""


class DataPackages:
    """Exports a user's data package, and imports one into a user's data."""

    def __init__(
        self,
        db: Database,
        store: Store,
        memories: Memories,
        characters: Mapping[str, Character],
        gift_catalog: Mapping[str, Gift],
        clock: Callable[[], datetime],
    ) -> None:
        self._db = db
        self._store = store
        self._memories = memories
        self._characters = characters
        self._gift_catalog = gift_catalog
        self._clock = clock
        self._working: set[str] = set()
        """Whose packages are being imported or exported; on the event loop only."""
        db.prepare(_create)

    async def check_imports_left(self, user_id: str) -> None:
        """Raise `ImportsUsedUp` if the user has made today's imports."""
        now = self._clock()
        if await self._db.read(_imports_made, user_id, _today_began(now)) >= IMPORTS_PER_DAY:
            raise ImportsUsedUp(next_day(now), now)

    def check_not_busy(self, user_id: str) -> None:
        """Raise `Busy` while the user is importing or exporting a package (`one_at_a_time`)."""
        if user_id in self._working:
            raise Busy()

    @contextmanager
    def one_at_a_time(self, user_id: str) -> Iterator[None]:
        """Held while the user imports a package, from reading its first byte to the end, or
        while their package is written to export; raises `Busy` while another of theirs is
        held. In this process only: a deployment runs one."""
        self.check_not_busy(user_id)
        self._working.add(user_id)
        try:
            yield
        finally:
            self._working.discard(user_id)

    async def export(self, user_id: str, include: Iterable[Category], to: Path) -> None:
        """Write the user's package, with the categories in `include`, to the file `to`: all
        but the pictures of their photos read at once, then written on a thread, reading
        the pictures one at a time."""
        categories = chosen(include)
        profile, conversations, memories, cycles, letters = await self._db.read(
            self._read_data, user_id, categories
        )
        loop = asyncio.get_running_loop()

        def picture_of(photo_id: str) -> bytes | None:
            """The photo's picture, read as the package is written, on a thread."""
            reading = asyncio.run_coroutine_threadsafe(
                self._store.photo_image(photo_id, user_id), loop
            )
            try:
                return reading.result(timeout=PICTURE_WAIT)
            except TimeoutError:
                reading.cancel()
                raise

        await asyncio.to_thread(
            self._export, categories, profile, conversations, memories, cycles, letters,
            picture_of, to,
        )

    def _read_data(
        self, db: sqlite3.Connection, user_id: str, categories: list[Category]
    ) -> tuple[
        ProducerProfile | None,
        list[WholeConversation],
        list[Memory],
        list[cycle_records.Carried],
        list[CarriedLetter],
    ]:
        """What of the categories the user has, read through `db`, but the pictures of their
        photos: their profile, conversations, memories, the periods they wrote down and the
        letters she wrote."""
        return (
            read_profile(db, user_id) if "profile" in categories else None,
            read_whole_conversations(db, user_id) if "conversations" in categories else [],
            self._memories.read_kept(db, user_id) if "memories" in categories else [],
            cycle_records.read_carried(db, user_id) if "cycles" in categories else [],
            read_carried_letters(db, user_id) if "letters" in categories else [],
        )

    def _export(
        self,
        categories: list[Category],
        profile: ProducerProfile | None,
        conversations: list[WholeConversation],
        memories: list[Memory],
        cycles: list[cycle_records.Carried],
        letters: list[CarriedLetter],
        picture_of: Callable[[str], bytes | None],
        to: Path,
    ) -> None:
        data = PackageData()
        counts: dict[Category, int] = {}
        if profile is not None:
            data.profile = PackagedProfile.of(profile)
            counts["profile"] = 1
        if "conversations" in categories:
            data.conversations = [
                PackagedConversation.of(c, self._name(c.character_id), "photos" in categories)
                for c in conversations
            ]
            counts["conversations"] = len(data.conversations)
        if "photos" in categories:
            counts["photos"] = len(sent(data.photos())) + sum(m.user_image is not None for c in data.conversations or [] for m in c.messages)
        if "memories" in categories:
            data.memories = [
                PackagedMemory.of(m, "conversations" in categories)
                for m in memories
            ]
            counts["memories"] = len(data.memories)
        if "cycles" in categories:
            data.cycles = [PackagedCycle.of(r) for r in cycles]
            counts["cycles"] = len(data.cycles)
        if "letters" in categories:
            data.letters = [
                PackagedLetter.of(letter, self._name(letter.character_id)) for letter in letters
            ]
            counts["letters"] = len(data.letters)
        now = self._clock()
        manifest = Manifest(
            format=FORMAT, exported_at=_written(now), categories=categories, counts=counts
        )
        with zipfile.ZipFile(to, "w") as package:
            package.writestr(
                zipfile.ZipInfo(MANIFEST, _zip_time(now)),
                _json(manifest.model_dump()),
                compress_type=zipfile.ZIP_STORED,
            )
            self._write_pictures(picture_of, data, package, now)
            package.writestr(
                zipfile.ZipInfo(DATA, _zip_time(now)),
                _json(data.model_dump(exclude_unset=True)),
                compress_type=zipfile.ZIP_DEFLATED,
            )

    def _write_pictures(
        self,
        picture_of: Callable[[str], bytes | None],
        data: PackageData,
        package: zipfile.ZipFile,
        now: datetime,
    ) -> None:
        """Write into `package` the picture of each photo sent in `data`, read one at a time
        by `picture_of`; one whose picture is gone by then, its conversation deleted as the
        package is written, goes as not sent."""
        for conversation in data.conversations or []:
            for message in conversation.messages:
                photo = message.photo
                if photo is None or not photo.sent:
                    continue
                picture = picture_of(photo.id)
                if picture is None:
                    message.photo = photo.not_sent()
                    continue
                package.writestr(
                    zipfile.ZipInfo(_picture_entry(photo.id), _zip_time(now)),
                    picture,
                    compress_type=zipfile.ZIP_STORED,  # a WebP is compressed already
                )

    async def import_(
        self,
        user_id: str,
        package: Path,
        include: Iterable[Category],
        device_timezone: str | None,
    ) -> Imported:
        """Import the categories of the package at `package` that `include` names into the
        user's data, counted among today's imports; the periods written down checked against
        today where the producer is, by the profile imported with them, if any, else theirs,
        else `device_timezone`. Raises `TooNew` or `BrokenPackage`
        having written nothing. The package is unzipped and checked on a thread first, then
        written in one write task, all at once or not at all. The memories it adds are
        embedded afterwards, in the background. Whether the user has imports left today is
        for the caller to check first (`check_imports_left`), while it holds `one_at_a_time`."""
        unpacked = await asyncio.to_thread(self._unpack, package, include)
        imported = await self._db.write(
            _write_import, user_id, unpacked, device_timezone, self._clock()
        )
        if imported.memories:
            self._memories.embed_carried(user_id)
        return imported

    def _unpack(self, package: Path, include: Iterable[Category]) -> "_Unpacked":
        try:
            zipped = zipfile.ZipFile(package)
        except (zipfile.BadZipFile, ValueError) as e:
            raise BrokenPackage(type(e).__name__) from None
        with zipped:
            manifest, data, pictures = _read(zipped)
        categories = set(chosen(include)) & set(manifest.categories)
        unpacked = _Unpacked(pictures=pictures if "photos" in categories else {})
        if "profile" in categories and data.profile is not None:
            unpacked.profile = data.profile.profile()
        if "conversations" in categories and data.conversations is not None:
            self._unpack_conversations(
                data.conversations, unpacked, with_photos="photos" in categories
            )
        if "memories" in categories and data.memories is not None:
            unpacked.memories = data.memories
        if "cycles" in categories and data.cycles is not None:
            unpacked.cycles = [
                c.carried() for c in data.cycles if c.character_id in self._characters
            ]
            unpacked.cycles_skipped = len(data.cycles) - len(unpacked.cycles)
        if "letters" in categories and data.letters is not None:
            unpacked.letters = [
                letter.carried() for letter in data.letters
                if letter.character_id in self._characters
            ]
            unpacked.letters_skipped = len(data.letters) - len(unpacked.letters)
        return unpacked

    def _unpack_conversations(
        self, packaged: list[PackagedConversation], unpacked: "_Unpacked", with_photos: bool
    ) -> None:
        """Put into `unpacked` the package's conversations to add, with their photos if
        `with_photos`; and those skipped, she not being here."""
        skipped: dict[str, int] = {}
        for conversation in packaged:
            if conversation.character_id not in self._characters:
                name = conversation.character_name or conversation.character_id
                skipped[name] = skipped.get(name, 0) + 1
                continue
            unpacked.conversations.append(_Carried(
                conversation.id,
                conversation.whole(self._gift_catalog, with_photos),
                (len(sent(conversation.photos())) + sum(m.user_image is not None for m in conversation.messages)) if with_photos else 0,
            ))
        unpacked.skipped = [Skipped(name, count) for name, count in skipped.items()]

    def _name(self, character_id: str) -> str | None:
        character = self._characters.get(character_id)
        return character.name if character else None


@dataclass
class _Carried:
    """A conversation of a package to add."""

    key: PackageKey
    """Its id in the package."""
    conversation: WholeConversation
    photos: int
    """How many photos sent it carries, as they are to be added."""


@dataclass
class _Unpacked:
    """What of a package, unzipped and checked, is to be added: all that is left to do is
    write it."""

    pictures: dict[str, bytes]
    """The picture of each photo sent, by its id in the package; none without photos."""
    profile: ProducerProfile | None = None
    conversations: list[_Carried] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    memories: list[PackagedMemory] = field(default_factory=list)
    cycles: list[cycle_records.Carried] = field(default_factory=list)
    """The periods written down to add, of characters here."""
    cycles_skipped: int = 0
    """The periods written down of characters not here."""
    letters: list[CarriedLetter] = field(default_factory=list)
    """The letters to add, of characters here."""
    letters_skipped: int = 0
    """The letters of characters not here."""


def _create(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


def _imports_made(db: sqlite3.Connection, user_id: str, since: str) -> int:
    (made,) = db.execute(
        "SELECT COUNT(*) FROM data_package_imports WHERE user_id = ? AND at >= ?",
        (user_id, since),
    ).fetchone()
    return made


def _write_import(
    db: sqlite3.Connection,
    user_id: str,
    unpacked: _Unpacked,
    device_timezone: str | None,
    now: datetime,
) -> Imported:
    """Add what of a package is `unpacked` to the user's data through `db`, in the caller's
    transaction, as an import they made `now`; what it added."""
    imported = Imported(skipped=unpacked.skipped, letters_skipped=unpacked.letters_skipped)
    if unpacked.profile is not None and unpacked.profile != read_profile(db, user_id):
        write_profile(db, user_id, unpacked.profile)
        imported.profile = True
    here: dict[PackageKey, str] = {}
    for carried in unpacked.conversations:
        here[carried.key], new = add_carried_conversation(
            db, user_id, carried.conversation, unpacked.pictures.__getitem__
        )
        if new:
            imported.conversations += 1
            imported.photos += carried.photos
    if unpacked.memories:
        imported.memories = add_carried(
            db, user_id, [m.carried(here) for m in unpacked.memories]
        )
    if unpacked.cycles:
        imported.cycles = cycle_records.add_carried(
            db, user_id, unpacked.cycles, device_timezone, now
        )
    imported.cycles_skipped = unpacked.cycles_skipped + len(unpacked.cycles) - imported.cycles
    if unpacked.letters:
        imported.letters = add_carried_letters(db, user_id, unpacked.letters, now)
    db.execute(
        "DELETE FROM data_package_imports WHERE user_id = ? AND at < ?",
        (user_id, _today_began(now)),
    )
    db.execute(
        "INSERT INTO data_package_imports (user_id, at) VALUES (?, ?)",
        (user_id, _written(now)),
    )
    return imported


def _today_began(now: datetime) -> str:
    """When the day it is at `now` (`affinity.day_of`) began, as times are written."""
    return _written(next_day(now) - timedelta(days=1))


def _read(zipped: zipfile.ZipFile) -> tuple[Manifest, PackageData, dict[str, bytes]]:
    """The manifest and data of the package `zipped`, checked, and the pictures of its
    photos sent, checked one at a time, by their ids. Its format first, as soon as its
    manifest is read: a newer format may hold other entries, in another order."""
    try:
        entries = zipped.infolist()
        names = [e.filename for e in entries]
        if MANIFEST not in names:
            raise BrokenPackage("no manifest")
        raw_manifest = json.loads(_unzipped(zipped, MANIFEST, MAX_MANIFEST_BYTES))
        if _Format.model_validate(raw_manifest).format > FORMAT:
            raise TooNew()
        if len(set(names)) != len(names) or DATA not in names:
            raise BrokenPackage("entries it should not have")
        first = entries[0]
        if first.filename != MANIFEST or first.compress_type != zipfile.ZIP_STORED:
            raise BrokenPackage("its manifest not first, stored")
        manifest = Manifest.model_validate(raw_manifest)
        data = PackageData.model_validate_json(_unzipped(zipped, DATA, MAX_DATA_BYTES))
        if manifest.format < REACH_OUTS_SINCE and any(
            c.reach_outs for c in data.conversations or []
        ):
            raise BrokenPackage("reach-outs in a format before them")
        if manifest.format < UNWELL_SINCE and any(
            e.kind == "unwell" for c in data.conversations or [] for e in c.events
        ):
            raise BrokenPackage("her being unwell in a format before it")
        if manifest.format < CYCLES_SINCE and data.cycles is not None:
            raise BrokenPackage("periods written down in a format before them")
        if manifest.format < CITY_SINCE and data.profile is not None and data.profile.city:
            raise BrokenPackage("the producer's city in a format before it")
        if manifest.format < LETTERS_SINCE and data.letters is not None:
            raise BrokenPackage("letters in a format before them")
        listed = set(manifest.categories)
        held = {c for c in CATEGORIES if c != "photos" and getattr(data, c) is not None}
        photos = data.photos()
        user_images = [m for c in data.conversations or [] for m in c.messages if m.user_image]
        if user_images and (manifest.format < 6 or "photos" not in listed):
            raise BrokenPackage("user images without format 6 and photos category")
        if held != listed - {"photos"} or (photos and "photos" not in listed) or (
            "photos" in listed and "conversations" not in listed
        ):
            raise BrokenPackage("categories not as the manifest lists them")
        if set(names) - {MANIFEST, DATA} != {_picture_entry(p.id) for p in sent(photos)}:
            raise BrokenPackage("pictures not as its photos are")
        pictures: dict[str, bytes] = {}
        unzipped = 0
        for photo in sent(photos):
            picture = _picture(zipped, photo.id)
            unzipped += len(picture)
            if unzipped > MAX_PICTURES_BYTES:
                raise BrokenPackage("pictures too large")
            _check_picture(picture)
            pictures[photo.id] = picture
    except (zipfile.BadZipFile, ValueError, ValidationError, KeyError) as e:
        # json.JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise BrokenPackage(type(e).__name__) from None
    return manifest, data, pictures


def _picture_entry(photo_id: str) -> str:
    """Where in a package the picture of its photo is."""
    return f"{PHOTOS}{photo_id}.webp"


def _picture(zipped: zipfile.ZipFile, photo_id: str) -> bytes:
    """The picture of the package's photo, unzipped."""
    return _unzipped(zipped, _picture_entry(photo_id), MAX_PHOTO_BYTES)


def _check_picture(picture: bytes) -> None:
    """Refuse the package as broken unless `picture` opens as a WebP, of whatever size: but
    one of so many pixels that Pillow takes it for a decompression bomb, it refuses."""
    try:
        with Image.open(io.BytesIO(picture), formats=["WEBP"]) as opened:
            opened.load()
    except Exception as e:  # whatever Pillow raises for what is not a WebP
        raise BrokenPackage(type(e).__name__) from None


def _unzipped(zipped: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """The entry `name` unzipped, refusing it as broken past `limit` bytes, whatever its
    header says it unzips to."""
    with zipped.open(name) as entry:
        content = entry.read(limit + 1)
    if len(content) > limit:
        raise BrokenPackage(f"{name} too large")
    return content


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _zip_time(now: datetime) -> tuple[int, int, int, int, int, int]:
    return now.astimezone(UTC).timetuple()[:6]
