"""The producer profile and what it tells the character about the user, and the prompt
section about now: the time where the user is, and whether they are typing, in a call, or
not talking to her at all, as when she reaches out (GLOSSARY.md「搭话」).

A profile field left unset keeps its default: the character sheet decides how
the character addresses the producer, the producer is the man of the original
story, the time zone is the one the user's device reports (Asia/Shanghai
when it reports none), and the character does not know the producer's birthday
(a month and a day, never a year). On the birthday, counted in the producer's
own time zone, the character is asked to wish them a happy birthday once. The
profile only shapes how the character treats the user now; the stories she
recalls stay as they happened.
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache
from typing import Literal
from zoneinfo import ZoneInfo, available_timezones

from eidolon.weather import Spot, Weather, weather_line

Gender = Literal["male", "female"]
Talking = Literal["typing", "in_call", "apart", "group"]
"""How the user and she are talking now: typing privately, in a call, in a group, or apart."""

DEFAULT_TIMEZONE = "Asia/Shanghai"
_TOKYO = ZoneInfo("Asia/Tokyo")
MAX_FORM_OF_ADDRESS_LENGTH = 20
MAX_CITY_PART_LENGTH = 40
"""How long each part of a city's name may be, in characters."""

_GENDER_NAMES: dict[Gender, str] = {"male": "男性", "female": "女性"}
_WEEKDAYS = "一二三四五六日"


@dataclass(frozen=True)
class Birthday:
    """A birthday, the producer's or a character's: a month and a day, never a year."""

    month: int
    day: int


def is_day_of_the_year(month: int, day: int) -> bool:
    """Whether some year has this day; February 29 counts."""
    return 1 <= month <= 12 and 1 <= day <= calendar.monthrange(2000, month)[1]


def is_birthday(birthday: Birthday, today: date) -> bool:
    """Whether `today` is `birthday`: the producer's, on their local date, or a character's,
    on the date in Tokyo.

    February 29 falls on February 28 in a year that has no February 29.
    """
    if (birthday.month, birthday.day) == (today.month, today.day):
        return True
    return (
        (birthday.month, birthday.day) == (2, 29)
        and (today.month, today.day) == (2, 28)
        and not calendar.isleap(today.year)
    )


@dataclass(frozen=True)
class City:
    """A city, a district or a county, as QWeather names it in Chinese: its `name`, the city
    it is in (`adm2`), the province or the like (`adm1`), the country, and where it is, in
    WGS-84."""

    name: str
    adm2: str
    adm1: str
    country: str
    latitude: float
    longitude: float

    def parts(self) -> list[str]:
        """Its name, then each of what it is in that is not as good as said already (东城,
        北京, 中国 for 东城 in 北京 in 北京市)."""
        kept: list[str] = []
        for part in (self.name, self.adm2, self.adm1, self.country):
            if part and not any(k.startswith(part) or part.startswith(k) for k in kept):
                kept.append(part)
        return kept

    @property
    def told(self) -> str:
        """How she is told it: 东城（北京，中国）."""
        name, *rest = self.parts()
        return f"{name}（{'，'.join(rest)}）" if rest else name

    @property
    def spot(self) -> Spot:
        return Spot(self.latitude, self.longitude)


@dataclass(frozen=True)
class ProducerProfile:
    form_of_address: str | None = None
    gender: Gender | None = None
    timezone: str | None = None
    birthday: Birthday | None = None
    city: City | None = None
    """Where the producer is, as QWeather found it; she knows of it only while the deployer
    has a QWeather account (`qweather`)."""


@cache
def _known_timezones() -> frozenset[str]:
    # The server's own local time is no place a user lives in.
    return frozenset(available_timezones() - {"localtime", "Factory"})


def is_timezone(name: str) -> bool:
    """Whether `name` is an IANA time zone, such as Asia/Tokyo."""
    return name in _known_timezones()


def effective_timezone(profile: ProducerProfile, device_timezone: str | None) -> str:
    """The profile's time zone if set, else the device's, else Asia/Shanghai.

    Either may name a zone this server does not know (tzdata drops or renames
    zones now and then); such a zone is passed over rather than failing the turn.
    """
    for timezone in (profile.timezone, device_timezone):
        if timezone and is_timezone(timezone):
            return timezone
    return DEFAULT_TIMEZONE


def local_date(now: datetime, timezone: str) -> date:
    """The day it is at `now` where the producer is: the day `now_prompt` tells."""
    return now.astimezone(ZoneInfo(timezone)).date()


def producer_prompt(profile: ProducerProfile) -> str:
    """The prompt section about the user: who they are, the same from turn to turn."""
    lines = ["# 制作人", "和你对话的是你的制作人，也就是 283 事务所的制作人。"]
    if profile.form_of_address:
        lines.append(
            f"制作人希望你称呼对方为「{profile.form_of_address}」。这只是叫法，"
            "对方仍然是你的制作人；请按你自己的性格和习惯，在合适的时候这样称呼。"
        )
    if profile.gender:
        lines.append(
            f"制作人是{_GENDER_NAMES[profile.gender]}。这只影响你现在怎么对待对方；"
            "回忆原作剧情时，剧情中发生过的事保持原样，不要改写。"
        )
    else:
        lines.append("制作人是男性，和原作一致。")
    if profile.birthday:
        lines.append(
            f"制作人的生日是{profile.birthday.month}月{profile.birthday.day}日，"
            "这是制作人自己在档案里填的；记忆里如果有不一样的说法，以这里为准。"
            "被问起时可以回答，平时不要主动提起。"
        )
    return "\n".join(lines)


_TYPING = "制作人正在用文字和你聊天，你们没有在打电话；不想聊的时候就说不想聊，不要说挂电话或挂断。"
_IN_A_CALL = "制作人正在和你打电话。"
_APART = "你们这会儿没在说话。"
_TALKING: dict[Talking, str] = {
    "typing": _TYPING, "in_call": _IN_A_CALL, "apart": _APART,
    "group": "你在和制作人、其他偶像一起群聊。",
}
_BIRTHDAY = (
    "今天是制作人的生日。请在合适的时候用你自己的方式祝福一次：先回应制作人说的话，不要每句都提。"
)
_BIRTHDAY_APART = "今天是制作人的生日。今天还没祝福过的话，用你自己的方式祝福一次。"
_POSTING = "你这会儿在发朋友圈，制作人没在和你说话。"
_BIRTHDAY_POSTING = "今天是制作人的生日（按他那边的日期）。想的话，这条可以祝他生日快乐。"
_REPLYING = "你这会儿在看自己朋友圈下面的评论，制作人没在和你说话。"
_BIRTHDAY_REPLYING = "今天是制作人的生日（按他那边的日期）。"


def _how_long(gap: timedelta) -> str:
    """`gap` as she is told it: in minutes under an hour, in hours under two days, else days."""
    minutes = int(gap.total_seconds() // 60)
    if minutes < 1:
        return "不到 1 分钟"
    if minutes < 60:
        return f"{minutes} 分钟"
    if minutes < 48 * 60:
        return f"{minutes // 60} 小时"
    return f"{gap.days} 天"


def now_prompt(
    profile: ProducerProfile,
    now: datetime,
    timezone: str,
    talking: Talking,
    affinity_lines: Sequence[str] = (),
    last_contact: datetime | None = None,
) -> str:
    """The prompt section about now: the time where the user is, how they are `talking` with
    the character, when they were `last_contact` in touch, if ever, and how long ago, and
    whether it is their birthday; then `affinity_lines`, how the character feels toward them
    now (`affinity.affinity_prompt`)."""
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    lines = [
        "# 此刻",
        f"现在是 {local.year}年{local.month}月{local.day}日"
        f"（星期{_WEEKDAYS[local.weekday()]}）{local:%H:%M}（制作人所在时区 {timezone}）。",
        _TALKING[talking],
    ]
    if last_contact is not None:
        then = last_contact.astimezone(zone)
        year = f"{then.year}年" if then.year != local.year else ""
        lines.append(
            f"上次和制作人说话是 {year}{then.month}月{then.day}日 {then:%H:%M}，"
            f"隔了 {_how_long(now - last_contact)}。"
        )
    if profile.birthday and is_birthday(profile.birthday, local.date()):
        lines.append(_BIRTHDAY_APART if talking == "apart" else _BIRTHDAY)
    lines += affinity_lines
    return "\n".join(lines)


def posting_now_prompt(
    profile: ProducerProfile,
    now: datetime,
    timezone: str,
    affinity_lines: Sequence[str] = (),
    weather: Weather | None = None,
    replying: bool = False,
) -> str:
    """The prompt section about now as she posts a moment (GLOSSARY.md「朋友圈」), or is
    `replying` to the producer's comment on one: the time in Tokyo, where she lives and posts,
    never when they were last in touch; Tokyo's `weather`, if known; whether it is the
    producer's birthday, in their own `timezone`; then `affinity_lines`."""
    local = now.astimezone(_TOKYO)
    lines = [
        "# 此刻",
        f"现在是 {local.year}年{local.month}月{local.day}日"
        f"（星期{_WEEKDAYS[local.weekday()]}）{local:%H:%M}（东京时间）。",
        _REPLYING if replying else _POSTING,
    ]
    if weather is not None:
        lines.append(weather_line(weather))
    if profile.birthday and is_birthday(profile.birthday, local_date(now, timezone)):
        lines.append(_BIRTHDAY_REPLYING if replying else _BIRTHDAY_POSTING)
    lines += affinity_lines
    return "\n".join(lines)
