"""The optional opening circumstances of a single group chat."""

from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemberSetting(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    place: str = Field(default="", max_length=60)
    activity: str = Field(default="", max_length=100)


class StoryTime(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    local: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$")
    timezone: str = Field(min_length=1, max_length=100)

    def instant(self) -> datetime:
        return datetime.fromisoformat(self.local).replace(tzinfo=ZoneInfo(self.timezone))

    @model_validator(mode="after")
    def valid_time(self) -> "StoryTime":
        try:
            instant = self.instant()
            if instant.astimezone(UTC).astimezone(instant.tzinfo) != instant:
                raise ValueError("该时区不存在这个本地时间。")
        except (ZoneInfoNotFoundError, OverflowError) as error:
            raise ValueError("请检查剧情日期和时区。") from error
        return self


class GroupBackground(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    time: StoryTime | None = None
    place: str = Field(default="", max_length=60)
    members: dict[str, MemberSetting] = Field(default_factory=dict, max_length=3)
    premise: str = Field(default="", max_length=300)

    def for_members(self, roster: Sequence[str]) -> "GroupBackground | None":
        if self.members.keys() - set(roster):
            raise ValueError("只能设置本群成员的位置和活动。")
        settings = {key: value for key, value in self.members.items() if value.place or value.activity}
        if self.place and settings:
            raise ValueError("请选择共同地点或分别设置成员。")
        if self.time or self.place or self.premise or settings:
            return self.model_copy(update={"members": settings})
        return None
