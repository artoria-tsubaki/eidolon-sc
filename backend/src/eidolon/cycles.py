"""Where her cycle has her today, as the rest of the backend asks it (GLOSSARY.md「周期」):
her true cycle (`her_cycle`) on the producer's calendar day, unless the deployment or the
user has turned cycles off, when it has no part in how she is; and what she is told of it
in what she is told of now (「此刻」, `cycle_lines`).

The user's own switch (`switch`) is on by default, kept on the server so that it holds on
every device, for all the characters; it cannot be set while the deployment has cycles off.
Her cycle goes on all the same while either is off: turned on again, she is where she would
have been.

Where she is is told to her prompts alone: no route, the admin console, the dashboard nor
the activity records ever show it.

In development (`EIDOLON_DEV_LOGIN=1`), where she is may be forced for a user's character
(`force`): kept in memory, gone as the server restarts; the switches still hold."""

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from eidolon.characters import Character
from eidolon.data_access import Database
from eidolon.her_cycle import HerDay, Period, Premenstrual, cycle_seed, her_day, rhythm
from eidolon.producer import local_date

ADULT_AGE = 18
"""From what age her cycle strays as an adult's (`her_cycle.ADULT_STRAYS`)."""

PERIOD_EARLY = (
    "你今天身体很不舒服：浑身没力气，说话比平时少、慢，偶尔会走神。"
    "身上可能发冷、腰酸、身子发沉、只想窝着不动，喝点热的会好些。"
)
"""What she is told on the first two days of her period."""

PERIOD_LATE = "你这几天身体还有点虚：比平时容易累，不太想出门，不过已经比前两天好多了。"
"""What she is told from the third day of her period on."""

PREMENSTRUAL_FAR = "你这几天心情有点起伏：比平时敏感，小事也容易在意。"
"""What she is told premenstrual, 3 days or more before her period."""

PREMENSTRUAL_NEAR = (
    "你这两天特别容易烦躁、委屈，小事也会在意很久，自己也说不清为什么心情不好；"
    "身上也可能有点发胀、睡不踏实。"
)
"""What she is told on the last two days before her period."""

UNSAID = (
    "这是你自己身体的事，你不会说出经期、月经、生理期这些词，也不提出血、卫生用品这类细节；"
    "只让状态自然地流露在语气和小事里。制作人问起时，照你的性格含糊带过（比如说有点累、没睡好）。"
)
"""How she keeps it to herself, in her period and before it."""

ASKED_IN_PERIOD = (
    "制作人直接问到是不是那几天时，照你的性格决定认不认：可以含糊地认下，也可以岔开，"
    "或者嫌他问得太直接；认的话也用你自己的说法，不说出那个词，也不细讲。"
)
"""What she does in her period, asked straight out."""

ASKED_BEFORE = (
    "那几天还没到，只是快了；制作人直接问到时，照你的性格决定怎么应：可以含糊地说快了，"
    "也可以岔开，或者嫌他问得太直接；不说出那个词，也不细讲。"
)
"""What she does before her period, asked straight out."""

STILL_HERSELF = (
    "不舒服不是冲制作人发火的理由，也不拿它要挟或讨好处；工作照常，人设里的规则照旧。"
    "身上的不适一次最多顺口带出一样，换着说，不要每次都提同一样，也不逐条描述身体。"
)
"""How she stays herself all the same."""


IN_GROUP = "这是公开群聊，哪怕被直接问起也不确认周期阶段或透露私下细节。"


def cycle_lines(today: HerDay, *, public: bool = False) -> list[str]:
    """What she is told of where her cycle has her, after the rest of what she is told of
    now: how she feels by the day, then how she keeps it to herself; nothing in her other
    days. The same under 18 or not; never in what her photos are prompted with. Worded as
    `prototype/cycles-voice` settled it."""
    if isinstance(today, Period):
        feeling = PERIOD_EARLY if today.day <= 2 else PERIOD_LATE
        return [feeling, UNSAID + (IN_GROUP if public else ASKED_IN_PERIOD) + STILL_HERSELF]
    if isinstance(today, Premenstrual):
        feeling = PREMENSTRUAL_NEAR if today.days_left <= 2 else PREMENSTRUAL_FAR
        return [feeling, UNSAID + (IN_GROUP if public else ASKED_BEFORE) + STILL_HERSELF]
    return []


_SCHEMA = """
-- The users who have turned cycles off, by their own switch; on for all the others.
CREATE TABLE IF NOT EXISTS cycles_off (
    user_id TEXT PRIMARY KEY REFERENCES users(id)
);
"""


class CyclesOff(Exception):
    """The deployment has cycles off: no user's own switch may be set."""


Phase = Literal["period", "premenstrual", "neither"]

HerDays = Callable[[datetime, str], HerDay]
"""Where she is at a time, in a time zone (`Cycles.her_days`)."""


def always_neither(at: datetime, timezone: str) -> HerDay:
    """Her days without cycles: neither, whenever."""
    return None


@dataclass(frozen=True)
class Forced:
    """Where she is forced to be in development, whatever the day: on day `day` of her
    period, `day` days before it, or neither."""

    phase: Phase
    day: int = 0


class Cycles:
    def __init__(
        self,
        db: Database,
        characters: Mapping[str, Character],
        secret_key: str | None,
        on: Callable[[], bool],
    ) -> None:
        """Her seed is keyed with the deployment's `secret_key`, if any; `on` says whether
        the deployment has cycles on now."""
        self._db = db
        self._characters = characters
        self._secret_key = secret_key
        self._on = on
        self._forced: dict[tuple[str, str], Forced] = {}
        db.prepare(_open)

    async def her_days(self, user_id: str, character_id: str) -> HerDays:
        """Where the user's character is on the producer's day at any time in their
        effective time zone: in her period, premenstrual, or neither; neither too, whatever
        her cycle, while the deployment has cycles off, or the user, or for a character no
        longer configured. The switches are read once, now, so that many times are told at
        once, such as each gift given in a conversation."""
        character = self._characters.get(character_id)
        if (
            not self._on() or character is None
            or not await self._db.read(_switched_on, user_id)
        ):
            return always_neither
        seed = cycle_seed(self._secret_key, user_id, character_id)
        under_18 = character.age < ADULT_AGE
        forced = self._forced.get((user_id, character_id))

        def where(at: datetime, timezone: str) -> HerDay:
            day = local_date(at, timezone)
            if forced is None:
                return her_day(seed, under_18, day)
            if forced.phase == "period":
                length = max(forced.day, rhythm(seed).base_period)
                return Period(forced.day, length, day - timedelta(days=forced.day - 1))
            if forced.phase == "premenstrual":
                return Premenstrual(forced.day)
            return None

        return where

    async def switch_of(self, user_id: str) -> bool | None:
        """Whether the user has their own switch on, as it is by default; None where the
        deployment has cycles off."""
        if not self._on():
            return None
        return await self._db.read(_switched_on, user_id)

    async def switch(self, user_id: str, on: bool) -> None:
        """Switch the user's own switch on or off, for all the characters, from the next
        turn; raise `CyclesOff` where the deployment has cycles off."""
        if not self._on():
            raise CyclesOff
        await self._db.write(_switch, user_id, on)

    def force(self, user_id: str, character_id: str, forced: Forced | None) -> None:
        """Have the user's character be where `forced` says from now on, in development;
        None, where her true cycle has her again."""
        if forced is None:
            self._forced.pop((user_id, character_id), None)
        else:
            self._forced[(user_id, character_id)] = forced


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


def _switched_on(db: sqlite3.Connection, user_id: str) -> bool:
    row = db.execute("SELECT 1 FROM cycles_off WHERE user_id = ?", (user_id,)).fetchone()
    return row is None


def _switch(db: sqlite3.Connection, user_id: str, on: bool) -> None:
    if on:
        db.execute("DELETE FROM cycles_off WHERE user_id = ?", (user_id,))
    else:
        db.execute("INSERT OR IGNORE INTO cycles_off (user_id) VALUES (?)", (user_id,))
