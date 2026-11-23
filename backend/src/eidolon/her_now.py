"""What she knows of now (GLOSSARY.md「此刻」), each time she speaks, whatever the occasion:
typing, in a group, in a call, reaching out (GLOSSARY.md「搭话」), visiting the bar (GLOSSARY.md「光顾」),
posting a moment (GLOSSARY.md「朋友圈」).

`HerNow` gives it in two steps. The first reads: her jobs, counting those done, so that her
affinity read after it has them counted; where her cycle has her; her job done today; her
latest moments, where the occasion tells her of them; and, for a visit, her affinity too. The
second gives the lines of now for the occasion, as its table (`CARRIES`) has them, from what
the first read and what the caller read besides (the producer profile, when they were last in
touch, the affinity lines, the weather). The weather, typing, in a call or reaching out, the
caller reads on its own (`weather`), once it has the producer profile (`her_weather`). Her latest moments, as the first
read them, make a section of their own (`recent_moments.moments_section`). Where in the prompt
they go is the caller's to say (ADR-0020).

What every prompt of hers opens with (her sheet, her birthday, the world setting) is not now:
it stays the same all day, for a service to cache (`today.always_known`)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from eidolon.affinity import Affinities, Status
from eidolon.characters import Character
from eidolon.cycles import Cycles, HerDays, always_neither, cycle_lines
from eidolon.her_weather import HerWeather, KnownWeather, weather_lines
from eidolon.producer import City, ProducerProfile, Talking, now_prompt, posting_now_prompt
from eidolon.recent_moments import RecentMoment, RecentMoments
from eidolon.schedule import HerSchedule, Job, Schedules, schedule_lines
from eidolon.today import today_lines
from eidolon.weather import Weather
from eidolon.world import World

Occasion = Talking | Literal["visit", "moments"]
"""Where she speaks: typing, in a group, in a call, reaching out (`apart`), visiting the bar, or posting
a moment."""


@dataclass(frozen=True)
class Carries:
    """Which of what she knows of now an occasion tells her: the time, the producer's
    birthday and when they were last in touch (`now_prompt`); the day it is in Tokyo
    (`today_lines`); her jobs of this week and the next (`schedule_lines`); where her cycle
    has her (`cycle_lines`); how she feels toward the producer (the affinity lines); her
    latest moments (`recent_moments`), in a section of their own after the others; the
    weather, Tokyo's and the producer's, and where the producer is (`her_weather`), but
    for posting a moment, which is told Tokyo's weather its own way."""

    time: bool
    today: bool
    schedule: bool
    cycle: bool
    affinity: bool
    moments: bool
    weather: bool


CARRIES: dict[Occasion, Carries] = {
    "group": Carries(
        time=True, today=True, schedule=True, cycle=True, affinity=False, moments=False,
        weather=False,
    ),
    "typing": Carries(
        time=True, today=True, schedule=True, cycle=True, affinity=True, moments=True,
        weather=True,
    ),
    "in_call": Carries(
        time=True, today=True, schedule=True, cycle=True, affinity=True, moments=True,
        weather=True,
    ),
    "apart": Carries(
        time=True, today=True, schedule=True, cycle=True, affinity=True, moments=True,
        weather=True,
    ),
    # The bar's story is only ever on the night shift: no time, nor the producer's birthday;
    # her cycle does not follow her there, and her jobs only tell why she came.
    "visit": Carries(
        time=False, today=True, schedule=False, cycle=False, affinity=True, moments=False,
        weather=False,
    ),
    # Her moments go by Tokyo's day, as she lives there: the time is Tokyo's, and no jobs of
    # the week; when they were last in touch is not hers to post about. Her latest moments
    # she is told of as she posts, a few more of them (`moments.RECENT`).
    "moments": Carries(
        time=True, today=True, schedule=False, cycle=True, affinity=True, moments=False,
        weather=False,
    ),
}
"""What each occasion tells her of now. Anything more she is to know of now goes here, for
every occasion."""


@dataclass(frozen=True)
class KnownNow:
    """What the first step read of now (GLOSSARY.md「此刻」), for the user's `character`: her
    jobs of their week and the next, those done counted (None while schedules are off); where
    her cycle has her at any time (neither, whenever, while cycles are off, or on an occasion
    that does not tell her of it); her job done today, the latest if more than one (None
    without); for a visit, her affinity, read after her jobs (None otherwise, or while
    affinity is off); and her latest moments, the earliest first (none on an occasion that
    does not tell her of them, or while moments are off for the user)."""

    character: Character
    her_jobs: HerSchedule | None
    her_days: HerDays
    done_today: Job | None
    affinity: Status | None
    moments: tuple[RecentMoment, ...]


class HerNow:
    def __init__(
        self,
        characters: Mapping[str, Character],
        world: World | None = None,
        schedules: Schedules | None = None,
        cycles: Cycles | None = None,
        affinities: Affinities | None = None,
        moments: RecentMoments | None = None,
        weather: HerWeather | None = None,
    ) -> None:
        """Without `world`, she knows only the birthdays and holiday of the day; without
        `schedules`, no jobs; without `cycles`, no days of hers; without `affinities`, she
        reads no affinity for a visit; without `moments`, none of her moments; without
        `weather`, no weather, nor where the producer is."""
        self._characters = characters
        self._world = world
        self._schedules = schedules
        self._cycles = cycles
        self._affinities = affinities
        self._moments = moments
        self._weather = weather

    async def weather(
        self, occasion: Occasion, city: City | None, now: datetime, last_apart: datetime | None
    ) -> KnownWeather | None:
        """The weather she knows of on `occasion` at `now` (`her_weather`), the producer in
        `city` (None for nowhere set), they last in touch at `last_apart` (in a call, before
        it connected; None if never); None on an occasion that does not tell her of it, or
        without the weather."""
        if not CARRIES[occasion].weather or self._weather is None:
            return None
        return await self._weather.known(city, now, last_apart)

    async def read(
        self, user_id: str, character_id: str, device_timezone: str | None, occasion: Occasion
    ) -> KnownNow:
        """The first step, for the user's character on `occasion`, their device in
        `device_timezone` (None reaching out): her jobs read, those done counted towards her
        affinity, her cycle where the occasion tells her of it, her job done today, her latest
        moments where it tells her of them, and for a visit her affinity. Whoever reads her
        affinity otherwise is to read it after this, so that it has her jobs done counted."""
        her_jobs = None
        if self._schedules is not None:
            read_jobs = self._schedules.read_only_hers if occasion == "group" else self._schedules.hers
            her_jobs = await read_jobs(user_id, character_id, device_timezone)
        her_days = (
            await self._cycles.her_days(user_id, character_id)
            if CARRIES[occasion].cycle and self._cycles is not None else always_neither
        )
        affinity = (
            await self._affinities.status(user_id, character_id)
            if occasion == "visit" and self._affinities is not None else None
        )
        moments = (
            await self._moments.hers(user_id, character_id)
            if CARRIES[occasion].moments and self._moments is not None else ()
        )
        return KnownNow(
            self._characters[character_id], her_jobs, her_days,
            her_jobs.done_today() if her_jobs is not None else None, affinity, moments,
        )

    def lines(
        self,
        known: KnownNow,
        occasion: Occasion,
        now: datetime,
        timezone: str,
        profile: ProducerProfile,
        last_contact: datetime | None,
        affinity_lines: Sequence[str],
        weather: Weather | None = None,
        replying: bool = False,
        known_weather: KnownWeather | None = None,
    ) -> list[str]:
        """The second step: the lines of now for `occasion` (`CARRIES`), at `now`, the
        producer in `timezone` with `profile`, last in touch at `last_contact` (None if never,
        or in a call), she feeling as `affinity_lines` say, posting a moment in `weather` (None
        for none known), or `replying` to the producer's comment on one; her cycle where it has
        her at `now`; typing, in a call or reaching out, `known_weather` (`weather`). Joined by
        new lines, they make one section: the time and the rest of `now_prompt`
        (`posting_now_prompt` posting a moment), the affinity lines last in it; where the
        producer is and the weather; the day in Tokyo; her jobs; her cycle."""
        carries = CARRIES[occasion]
        character = known.character
        felt = affinity_lines if carries.affinity else ()
        lines: list[str] = []
        if occasion == "moments":
            lines.append(posting_now_prompt(profile, now, timezone, felt, weather, replying))
        elif carries.time:
            assert occasion != "visit"
            lines.append(now_prompt(profile, now, timezone, occasion, felt, last_contact))
        else:
            lines += felt
        if carries.weather and known_weather is not None:
            lines += weather_lines(known_weather)
        if carries.today:
            lines += today_lines(now, character, self._characters.values(), self._world)
        if carries.schedule and known.her_jobs is not None:
            lines += schedule_lines(known.her_jobs)
        if carries.cycle:
            lines += cycle_lines(known.her_days(now, timezone), public=occasion == "group")
        return lines
