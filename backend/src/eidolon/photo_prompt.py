"""What a photo is generated from (GLOSSARY.md「照片」): the photo tag she wrote, read field by
field, and the prompts the image service is sent for it.

She writes how the photo is taken (`shot`: a selfie, or someone else taking it), how much of
her it shows (`framing`), and three fields of English tags: where and when (`place`), what
she wears (`outfit`), what she does and how she looks (`action`); and a note in Chinese for
herself alone, never sent. What she writes is cleaned before it goes anywhere: the prompt
syntax NovelAI reads (emphasis, weights, alternation, comments, quotes, `Text:`) is
stripped, the tags put in lower case with spaces for underscores, and those the model
does not know as tags put as it does, by the local tag table (`photo_tags.json`, made by
`scripts/photo_tags.py` from NovelAI's tag suggestions); one it has no word on is kept as it
is, and logged. A tag crossing the line (ADR-0023: nudity, underwear, see-through, bathing,
a sexual pose or look) is stripped, as is one of her looks, an art style or an artist, or
anyone else in the picture, holding hands among it: only that tag, the photo taken all the
same of the rest (`PhotoTag.stripped` says which part of the line each stripped crossed, for
the activity records, never the tag). Each is written once, and each field cut to its most; a field left
empty gets its default. Swimsuits, pajamas, lying on a bed, a pool and the beach are not
crossing it. Neither the deployer nor any user changes the table.

The positive prompt is fixed around them: who she is (her character tag and the work's
tag), the deployment's art style, written in as the deployer wrote it (weights, artists
and all; none if empty), her looks, the tags for how the photo is taken, `rating:general` and V5's standard
quality tags, written out rather than left to the service's switch. The negative prompt is
fixed altogether: `nsfw`, the Human Focus preset, and what no photo of hers shows
(ADR-0023). Neither the deployer nor any user changes them.

A moment's picture (GLOSSARY.md「配图」) is written and cleaned as a photo is, in one of the
compositions she is let choose (`Composition`), each with prompts of its own
(`picture_prompt`). Three have no one in them (`UNPEOPLED`): scenery, in Japan, outdoors;
food; an object. Their `action` is what is in the picture, their `outfit` and `framing` not
read; anyone she writes into one is stripped too (`1girl`, `solo`, `selfie`, the work's tag
and the idols' own, where anyone looks, what anyone holds); food with nothing in it is `food`, an object with nothing in it no
picture. Three have her alone in them: a selfie, as a photo's; one someone else takes of her
posing, looking at the camera; one someone takes of her unawares, looking elsewhere, at a depth
of field. Those two may show her small (`wide shot`); a framing not of its composition is from
the waist up. Where she is alone or no one is, no one holds hands (`people`).

The seventh is a group photo (`group`), of her with one or two of those she may be pictured
with (`with`, by identifier): a selfie if her `action` says so, else someone else taking it of
the two side by side, or of the three as a group; from the waist up or the thighs up, a selfie
no more than an arm's length takes in. Each of them is a character of her own in NovelAI's
multiple characters, her character tag and her looks in it, she first; how many they are is
said once, of them all, as is what they wear, and they may hold hands. One with no one written
with her, or one she may not be pictured with, is a posed photo of her alone instead, at the
framing she wrote (`PictureTag.alone_instead`). Scenery and group photos are landscape
(`LANDSCAPE`), the others portrait, as photos are.
"""

import json
import logging
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, get_args

from eidolon.characters import Character

log = logging.getLogger(__name__)

WORK_TAG = "idolmaster shiny colors"
QUALITY_TAGS = ("rating:general", "very aesthetic", "masterpiece", "no text")
"""What every positive prompt ends with: all ages, then V5's standard quality tags."""

NEGATIVE_PROMPT = ", ".join((
    "nsfw",
    # The Human Focus preset: the Heavy one, with what goes wrong with faces.
    "lowres", "artistic error", "film grain", "scan artifacts", "worst quality", "bad quality",
    "jpeg artifacts", "very displeasing", "chromatic aberration", "dithering", "halftone",
    "screentone", "multiple views", "logo", "too many watermarks", "negative space",
    "blank page", "@_@", "mismatched pupils", "glowing eyes", "bad anatomy",
    # All ages (ADR-0023).
    "nude", "nipples", "underwear", "bra", "panties", "bra strap", "strap slip", "see-through",
    "pantyshot", "cleavage",
))

Shot = Literal["selfie", "other"]
Framing = Literal["portrait", "upper body", "cowboy shot", "full body"]
FRAMINGS: tuple[Framing, ...] = get_args(Framing)
SELFIE_FRAMINGS: tuple[Framing, ...] = ("portrait", "upper body")
"""How much of her a selfie can show: no more than an arm's length takes in."""
PictureFraming = Literal["portrait", "upper body", "cowboy shot", "full body", "wide shot"]
SHOT_FRAMINGS: tuple[PictureFraming, ...] = get_args(PictureFraming)
"""How much of her a moment's picture someone else takes can show: a photo's, or her small in
a wide shot."""

Composition = Literal["scenery", "food", "object", "selfie", "posed", "candid", "group"]
COMPOSITIONS: tuple[Composition, ...] = get_args(Composition)
Unpeopled = Literal["scenery", "food", "object"]
UNPEOPLED: tuple[Unpeopled, ...] = get_args(Unpeopled)
"""The compositions of a moment's picture no one is in."""
GROUP_FRAMINGS: tuple[PictureFraming, ...] = ("upper body", "cowboy shot")
"""How much of them a group photo someone else takes can show: enough to tell each by her
face."""
GROUP_MOST = 2
"""How many may be in a group photo with her at most."""

NO_HUMANS_NEGATIVE = f"{NEGATIVE_PROMPT}, 1girl, 1boy, people"
"""The negative prompt of a picture no one is in."""
CANDID_NEGATIVE = f"{NEGATIVE_PROMPT}, looking at viewer"
"""The negative prompt of a picture taken of her unawares."""

PORTRAIT, LANDSCAPE = (832, 1216), (1216, 832)
"""The sizes a picture is generated at, width by height: about as many pixels either way."""

FIELD_TAGS = 4
"""The most tags each of her three fields keeps: twelve at most in all."""
DEFAULTS = {"place": "indoors", "outfit": "casual", "action": "smile"}
"""What a field she left empty, or that comes to nothing once cleaned, is taken to say."""

LOOKING_AT_VIEWER = "looking at viewer"
LOOKING_AWAY = "looking away"
HOLDING_HANDS = "holding hands"
NO_HUMANS = "no humans"
_GAZES = ("looking", "eyes closed", "closed eyes")
"""What in her action says where she looks; without any, a photo someone else takes has her
looking at the camera."""

Crossing = Literal[
    "nudity", "underwear", "exposure", "bathing", "pose", "expression", "appearance", "style",
    "people",
]
"""Which part of the line a tag stripped from her photo crosses (ADR-0023): nudity or half of
it, underwear and costumes made to be sexy, see-through clothes and what shows by accident,
bathing, a sexual angle or pose, a sexual look; or what is never hers to write: her looks
(her hair, eyes and body), an art style or an artist, anyone else in the picture."""
CROSSINGS: tuple[Crossing, ...] = get_args(Crossing)

_WEIGHT = re.compile(r"-?\d+(\.\d+)?::|::|:-?\d+(\.\d+)?\b")
_TEXT = re.compile(r"\btext:", re.IGNORECASE)
_SYNTAX = re.compile(r"[{}\[\]|#\"'“”‘’`]")


@dataclass(frozen=True)
class PhotoTag:
    """A photo tag as read: how the photo is taken and how much of her it shows, and her
    three fields of tags, cleaned."""

    shot: Shot
    framing: Framing
    place: tuple[str, ...]
    outfit: tuple[str, ...]
    action: tuple[str, ...]
    stripped: tuple[Crossing, ...] = ()
    """Which part of the line each tag stripped crossed, one for each."""


@dataclass(frozen=True)
class PictureTag:
    """A moment's picture as read: its composition, how much of her it shows (None for one no
    one is in), and her three fields of tags, cleaned (`outfit` empty for one no one is in)."""

    composition: Composition
    framing: PictureFraming | None
    place: tuple[str, ...]
    outfit: tuple[str, ...]
    action: tuple[str, ...]
    stripped: tuple[Crossing, ...] = ()
    """Which part of the line each tag stripped crossed, one for each."""
    others: tuple[Character, ...] = ()
    """Who is in a group photo with her, in the order written."""
    selfie: bool = False
    """Whether a group photo is a selfie."""
    alone_instead: bool = False
    """Whether it is a posed photo of her alone in place of a group photo written amiss."""


@dataclass(frozen=True)
class ImagePrompt:
    """The prompts a picture is generated from, and its size, width by height; of a group
    photo, each one's own in NovelAI's multiple characters (`characters`), hers first."""

    positive: str
    negative: str
    size: tuple[int, int] = PORTRAIT
    characters: tuple[str, ...] = ()


def tag_fields(text: str) -> dict[str, str]:
    """The fields `text` writes one a line (`shot: selfie`), by name in lower case, the first
    of each."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        name, colon, value = line.partition(":")
        if colon:
            fields.setdefault(name.strip().lower(), value.strip())
    return fields


def read_tag(text: str) -> PhotoTag:
    """The photo tag `text` says, one field a line (`shot: selfie`), whatever it leaves out or
    gets wrong taken as most likely meant: someone else taking it, from the waist up."""
    fields = tag_fields(text)
    shot: Shot = "selfie" if fields.get("shot", "").strip().lower() == "selfie" else "other"
    written = _cleaned(fields.get("framing", ""))
    framing: Framing = "upper body"
    for known in FRAMINGS:
        if known == written:
            framing = known
    if shot == "selfie" and framing not in SELFIE_FRAMINGS:
        framing = "upper body"
    seen = {*_FIXED, *_shot_tags(shot), framing}
    stripped: list[Crossing] = []
    place, outfit, action = (
        _field(fields.get(name, ""), seen, stripped, DEFAULTS[name], alone=True)
        for name in DEFAULTS
    )
    return PhotoTag(shot, framing, place, outfit, action, tuple(stripped))


def read_picture(
    fields: Mapping[str, str],
    allowed: Collection[Composition],
    idols: Collection[str] = (),
    pictured: Sequence[Character] = (),
) -> PictureTag | None:
    """The moment's picture its `fields` say (`tag_fields`), in one of the compositions
    `allowed`; None for none: a composition not allowed, or an object with nothing in it.
    `idols` are the character tags that would draw one of them into a picture no one is
    in; `pictured` those she may be in a group photo with, `with` naming them by
    identifier."""
    written = _cleaned(fields.get("composition", ""))
    composition: Composition | None = next((c for c in allowed if c == written), None)
    if composition is None:
        return None
    if composition == "group":
        others = _with(fields.get("with", ""), pictured)
        if others:
            return _group(fields, others, idols)
        alone = read_picture({**fields, "composition": "posed"}, ["posed"])
        assert alone is not None  # a posed photo is always one
        return replace(alone, alone_instead=True)
    stripped: list[Crossing] = []
    if composition in UNPEOPLED:
        seen = {*_UNPEOPLED_FIXED[composition], WORK_TAG, *idols}  # never written, then
        place, action = (
            _field(fields.get(name, ""), seen, stripped, default, _peopled, alone=True)
            for name, default in (("place", None), ("action", _SUBJECTS[composition]))
        )
        if not action and composition == "object":
            return None
        return PictureTag(composition, None, place, (), action, tuple(stripped))
    framing = _framing(fields, SELFIE_FRAMINGS if composition == "selfie" else SHOT_FRAMINGS)
    seen = {*_FIXED, *_PEOPLED_FIXED[composition], framing}
    drops = _unaware if composition == "candid" else _no_one
    place, outfit, action = (
        _field(fields.get(name, ""), seen, stripped, DEFAULTS[name], drops, alone=True)
        for name in DEFAULTS
    )
    return PictureTag(composition, framing, place, outfit, action, tuple(stripped))


def _framing(
    fields: Mapping[str, str], framings: Sequence[PictureFraming]
) -> PictureFraming:
    """The framing its `fields` say, of `framings`; from the waist up for one not of them."""
    asked = _cleaned(fields.get("framing", ""))
    for known in framings:
        if known == asked:
            return known
    return "upper body"


def _with(written: str, pictured: Sequence[Character]) -> tuple[Character, ...]:
    """Who `written` names to be in a group photo with her, of those `pictured`, by
    identifier, each once, as many as `GROUP_MOST`; none if it names none, or one not of
    them."""
    by_id = {each.id: each for each in pictured}
    named = list(dict.fromkeys(n for n in re.split(r"[\s,，、`]+", written) if n))
    if not named or any(n not in by_id for n in named):
        return ()
    return tuple(by_id[n] for n in named[:GROUP_MOST])


def _group(
    fields: Mapping[str, str], others: tuple[Character, ...], idols: Collection[str]
) -> PictureTag:
    """The group photo its `fields` say, of her with `others`: a selfie if her `action` has
    one (then not kept in it, but said of them all); `idols`' character tags never written
    in it, each one's in her box alone."""
    selfie = "selfie" in _tags(fields.get("action", ""))
    framing = _framing(fields, SELFIE_FRAMINGS if selfie else GROUP_FRAMINGS)
    people = len(others) + 1
    seen = {
        _count(people), WORK_TAG, *QUALITY_TAGS, *_group_shot(people, selfie), framing, *idols,
    }
    stripped: list[Crossing] = []
    place, outfit, action = (
        _field(fields.get(name, ""), seen, stripped, DEFAULTS[name], _crowded)
        for name in DEFAULTS
    )
    return PictureTag(
        "group", framing, place, outfit, action, tuple(stripped), others, selfie
    )


def picture_prompt(character: Character, tag: PictureTag, style: str = "") -> ImagePrompt:
    """The prompts the moment's picture `tag` says is generated from, of `character` (who has
    her photo tags, if she is in it), in the art style `style`."""
    styled = _styled(style)
    if tag.composition == "group":
        return _group_prompt(character, tag, styled)
    if tag.composition == "scenery":
        opening = _UNPEOPLED_FIXED["scenery"][:4]
        return _prompt((*opening, *styled, *tag.place, *tag.action), NO_HUMANS_NEGATIVE, LANDSCAPE)
    if tag.composition in UNPEOPLED:
        focus = _UNPEOPLED_FIXED[tag.composition][:2]
        positive = (*focus, *styled, *tag.action, *tag.place, "depth of field")
        return _prompt(positive, NO_HUMANS_NEGATIVE)
    assert character.photo_tag is not None and character.photo_appearance is not None
    assert tag.framing is not None
    looks = any(g in t for t in tag.action for g in _GAZES)
    action, after, negative = tag.action, (), NEGATIVE_PROMPT
    if tag.composition == "posed" and not looks:
        action = (*action, LOOKING_AT_VIEWER)
    elif tag.composition == "candid":
        action = action if looks else (*action, LOOKING_AWAY)
        after, negative = ("depth of field",), CANDID_NEGATIVE
    shot = _shot_tags("selfie") if tag.composition == "selfie" else ()
    positive = (
        "1girl", "solo", character.photo_tag, WORK_TAG, *styled, character.photo_appearance,
        *shot, tag.framing, *tag.place, *tag.outfit, *action, *after,
    )
    return _prompt(positive, negative)


def group_chat_photo_prompt(
    sender: Character, subjects: tuple[Character, ...] | Unpeopled, text: str, style: str,
) -> tuple[ImagePrompt, tuple[Crossing, ...]] | None:
    """Build a chat photo from the chosen subjects and the sender's scene tags."""
    fields = tag_fields(text)
    if isinstance(subjects, str):
        picture = read_picture({**fields, "composition": subjects}, [subjects])
        return (picture_prompt(sender, picture, style), picture.stripped) if picture else None
    if len(subjects) == 1:
        photo = read_tag(text)
        return image_prompt(subjects[0], photo, style), photo.stripped
    first, *others = subjects
    if fields.get("shot", "").lower() == "selfie":
        fields["action"] = fields.get("action", "") + ", selfie"
    idol_tags = tuple(person.photo_tag for person in subjects if person.photo_tag)
    picture = _group(fields, tuple(others), idol_tags)
    return picture_prompt(first, picture, style), picture.stripped


def _group_prompt(
    character: Character, tag: PictureTag, styled: tuple[str, ...]
) -> ImagePrompt:
    """The prompts of the group photo `tag` of `character` and those with her: how many they
    are, how it is taken and what they wear said once, of them all; each her own character
    tag and looks, written as they are, never cleaned."""
    assert tag.framing is not None
    everyone = (character, *tag.others)
    looks = any(g in t for t in tag.action for g in _GAZES)
    positive = (
        _count(len(everyone)), WORK_TAG, *styled, *_group_shot(len(everyone), tag.selfie),
        tag.framing, *tag.place, *tag.outfit, *tag.action,
        *(() if looks else (LOOKING_AT_VIEWER,)),
    )
    characters: list[str] = []
    for each in everyone:
        assert each.photo_tag is not None and each.photo_appearance is not None
        characters.append(f"girl, {each.photo_tag}, {each.photo_appearance}")
    return replace(_prompt(positive, NEGATIVE_PROMPT, LANDSCAPE), characters=tuple(characters))


def _count(people: int) -> str:
    """The tag for how many are in a group photo."""
    return f"{people}girls"


def _group_shot(people: int, selfie: bool) -> tuple[str, ...]:
    """The tags for how a group photo of as many `people` is taken."""
    if selfie:
        return ("selfie",)
    return ("side-by-side",) if people == 2 else ("group picture",)


def _prompt(
    positive: tuple[str, ...], negative: str, size: tuple[int, int] = PORTRAIT
) -> ImagePrompt:
    """The prompts of a moment's picture: `positive`, then the quality tags."""
    return ImagePrompt(", ".join((*positive, *QUALITY_TAGS)), negative, size)


def image_prompt(character: Character, tag: PhotoTag, style: str = "") -> ImagePrompt:
    """The prompts a photo of `character` (who has her photo tags) is generated from, as
    `tag` says it is taken, in the art style `style`."""
    assert character.photo_tag is not None and character.photo_appearance is not None
    action = tag.action
    if tag.shot == "other" and not any(g in t for t in action for g in _GAZES):
        action = (*action, LOOKING_AT_VIEWER)
    positive = (
        "1girl", "solo", character.photo_tag, WORK_TAG, *_styled(style),
        character.photo_appearance, *_shot_tags(tag.shot), tag.framing, *tag.place,
        *tag.outfit, *action, *QUALITY_TAGS,
    )
    return ImagePrompt(", ".join(positive), NEGATIVE_PROMPT)


def check_prompt(style: str = "") -> ImagePrompt:
    """The prompts a change to an image service is tried with: no one in particular, in the
    art style `style`, otherwise as any photo is."""
    positive = (
        "1girl", "solo", *_styled(style), "upper body", "indoors", "smile", LOOKING_AT_VIEWER,
        *QUALITY_TAGS,
    )
    return ImagePrompt(", ".join(positive), NEGATIVE_PROMPT)


def _styled(style: str) -> tuple[str, ...]:
    """The art style as it is written into a prompt: as it is, or not at all if empty."""
    return (style.strip(),) if style.strip() else ()


def _shot_tags(shot: Shot) -> tuple[str, ...]:
    return ("selfie", LOOKING_AT_VIEWER) if shot == "selfie" else ()


def _tags(value: str) -> list[str]:
    """The tags `value` writes, cleaned and put as the model knows them."""
    cleaned = (_cleaned(raw) for raw in re.split(r"[,，、]", value))
    return [_TABLE.rewrites.get(tag, tag) for tag in cleaned if tag]


def _field(
    value: str,
    seen: set[str],
    stripped: list[Crossing],
    default: str | None,
    drops: Callable[[str], bool] = lambda _: False,
    alone: bool = False,
) -> tuple[str, ...]:
    """The tags of one of her fields, cleaned and put as the model knows them, those already
    written elsewhere left out (and added to `seen`), as are those it `drops`; those crossing
    the line stripped (and which part of it added to `stripped`), holding hands too where no
    one is there to hold them with (`alone`); cut to `FIELD_TAGS`; its `default`, if any, if
    none are left."""
    tags: list[str] = []
    for tag in _tags(value):
        if not tag or tag in seen or drops(tag):
            continue
        seen.add(tag)
        crossing = "people" if alone and tag == HOLDING_HANDS else _TABLE.crossing(tag)
        if crossing is not None:
            stripped.append(crossing)
            continue
        if tag not in _TABLE.known and len(tags) < FIELD_TAGS:
            log.info("a photo tag the tag table does not know, kept: %r", tag)
        tags.append(tag)
    return tuple(tags[:FIELD_TAGS]) or ((default,) if default is not None else ())


def _peopled(tag: str) -> bool:
    """Whether `tag` would draw someone into a picture no one is in (but for holding hands,
    which is stripped as crossing the line, and counted)."""
    return tag in ("1girl", "solo", "selfie") or (
        tag.startswith(("looking", "holding")) and tag != HOLDING_HANDS
    )


def _no_one(tag: str) -> bool:
    """Whether `tag` would leave her out of a picture of her."""
    return tag == NO_HUMANS


def _crowded(tag: str) -> bool:
    """Whether `tag` would leave her out of a group photo, or have her alone in it."""
    return tag in (NO_HUMANS, "1girl", "solo")


def _unaware(tag: str) -> bool:
    """Whether `tag` would leave her out of a picture taken of her unawares, or have her look
    at the camera."""
    return tag in (NO_HUMANS, LOOKING_AT_VIEWER)


def _cleaned(tag: str) -> str:
    """`tag` without NovelAI's prompt syntax, in lower case, a space for each underscore."""
    tag = _TEXT.sub("", tag)
    tag = _WEIGHT.sub("", tag)
    tag = _SYNTAX.sub("", tag)
    return " ".join(tag.replace("_", " ").lower().split())


class _TagTable:
    """The local tag table: the tags the model knows, what she may write for one, what is
    never stripped, and what crosses the line, tag by tag (`tags`) or as a word anywhere in
    one (`words`)."""

    def __init__(self, path: Path) -> None:
        table = json.loads(path.read_text(encoding="utf-8"))
        self.known: frozenset[str] = frozenset(table["known"])
        self.rewrites: dict[str, str] = table["rewrites"]
        self._allowed = frozenset(table["allowed"])
        self._tags: dict[str, Crossing] = {}
        self._words: list[tuple[re.Pattern[str], Crossing]] = []
        for crossing in CROSSINGS:
            blocked = table["blocked"][crossing]
            self._tags.update(dict.fromkeys(blocked["tags"], crossing))
            if words := "|".join(re.escape(w) for w in blocked["words"]):
                pattern = re.compile(rf"(?<![a-z0-9])(?:{words})(?![a-z0-9])")
                self._words.append((pattern, crossing))

    def crossing(self, tag: str) -> Crossing | None:
        """Which part of the line `tag` crosses, if any."""
        if tag in self._allowed:
            return None
        if (crossing := self._tags.get(tag)) is not None:
            return crossing
        return next((c for words, c in self._words if words.search(tag)), None)


_TABLE = _TagTable(Path(__file__).with_name("photo_tags.json"))
_FIXED = frozenset({"1girl", "solo", WORK_TAG, *QUALITY_TAGS})
"""What every positive prompt of her has already, written by her or not."""
_PEOPLED_FIXED: dict[Composition, tuple[str, ...]] = {
    "selfie": _shot_tags("selfie"), "posed": (), "candid": ("depth of field",),
}
"""What else the positive prompt of a moment's picture of her has already, by composition."""
_UNPEOPLED_FIXED: dict[Composition, tuple[str, ...]] = {
    "scenery": (NO_HUMANS, "scenery", "japan", "outdoors", *QUALITY_TAGS),
    "food": (NO_HUMANS, "food focus", "depth of field", *QUALITY_TAGS),
    "object": (NO_HUMANS, "object focus", "depth of field", *QUALITY_TAGS),
}
"""What the positive prompt of a picture no one is in has already, by composition: what it
opens with first."""
_SUBJECTS: dict[Composition, str | None] = {"scenery": None, "food": "food", "object": None}
"""What is in a picture no one is in that she wrote nothing in, by composition."""
