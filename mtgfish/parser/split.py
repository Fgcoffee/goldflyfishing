"""Splitting a text box into individual abilities, and saying what each is.

CR 113.2: each paragraph of a text box is a separate ability. That is almost
the whole rule, and the exceptions are what this module exists for.

A line of comma-separated keywords is several abilities, not one. A modal
spell's "Choose one -" and its bullet list are one ability spanning several
lines. A level-up or Saga block groups its lines by symbol. And the first line
of an activated ability can contain colons that belong to its *effect* rather
than to its cost separator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from ..rules.abilities import AbilityKind


class LineKind(IntEnum):
    """What a text-box paragraph turns out to be."""

    KEYWORD = 0
    ACTIVATED = 1
    TRIGGERED = 2
    STATIC = 3
    SPELL = 4
    MODAL = 5


#: The words that start a triggered ability (CR 603.1). "At", "When" and
#: "Whenever" are the whole list, and every trigger begins with one of them -
#: which is why classification is reliable in a way the rest of parsing is not.
TRIGGER_WORDS = ("at ", "when ", "whenever ")

#: Modal spells and abilities (CR 700.2).
MODAL_MARKERS = (
    "choose one",
    "choose two",
    "choose three",
    "choose up to one",
    "choose up to two",
    "choose up to three",
    "choose one or both",
    "choose one or more",
    "choose any number",
)

#: A bullet in a modal list. Scryfall uses a bullet character; normalization
#: leaves it alone because it is structural.
_BULLET = re.compile(r"^\s*[•*]\s*")

#: "Level up {2}" and the level bands beneath it, and Saga chapter symbols,
#: are structural glyphs rather than sentences (CR 711, 714).
_LEVEL = re.compile(r"^LEVEL\s+\d+(-\d+)?\+?", re.I)
_CHAPTER = re.compile(r"^([IVX]+(,\s*[IVX]+)*)\s*[-:]")


@dataclass(frozen=True, slots=True)
class Line:
    """One ability's worth of text, with what it looks like."""

    text: str
    kind: LineKind
    #: CR 702.178a: "Max speed - ..." gates whether the ability functions at
    #: all. Unlike an ability word - which is pure flavour and is stripped -
    #: dropping this would make the ability work from turn one.
    max_speed: bool = False
    #: For modal abilities, the individual modes.
    modes: tuple[str, ...] = ()
    #: For Saga chapters, which chapters this line covers.
    chapters: tuple[int, ...] = ()

    @property
    def ability_kind(self) -> AbilityKind:
        if self.kind is LineKind.TRIGGERED:
            return AbilityKind.TRIGGERED
        if self.kind is LineKind.ACTIVATED:
            return AbilityKind.ACTIVATED
        if self.kind in (LineKind.SPELL, LineKind.MODAL):
            return AbilityKind.SPELL
        return AbilityKind.STATIC


def split_abilities(text: str, *, is_permanent: bool = True) -> list[Line]:
    """Break normalized oracle text into classified ability lines.

    ``is_permanent`` decides what a plain sentence means. On a creature,
    "Draw a card." with no trigger is nonsense and reads as a static ability;
    on an instant it is the spell's whole effect. The card type is the only
    thing that distinguishes them, so the caller has to say.
    """
    if not text.strip():
        return []

    lines: list[Line] = []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    index = 0

    while index < len(paragraphs):
        paragraph = paragraphs[index]

        modal = _modal_block(paragraphs, index, is_permanent=is_permanent)
        if modal is not None:
            line, index = modal
            lines.append(line)
            continue

        chapter = _CHAPTER.match(paragraph)
        if chapter:
            numbers = _roman_list(chapter.group(1))
            body = paragraph[chapter.end() :].strip()
            lines.append(Line(body, LineKind.TRIGGERED, chapters=numbers))
            index += 1
            continue

        lines.extend(_classify_paragraph(paragraph, is_permanent=is_permanent))
        index += 1

    return lines


def _classify_paragraph(paragraph: str, *, is_permanent: bool) -> list[Line]:
    gated = False
    for prefix in ("max speed - ", "max speed -", "max speed — "):
        if paragraph.lower().startswith(prefix):
            paragraph = paragraph[len(prefix) :].strip()
            gated = True
            break

    lines = _classify_body(paragraph, is_permanent=is_permanent)
    if not gated:
        return lines
    return [
        Line(line.text, line.kind, max_speed=True, modes=line.modes,
             chapters=line.chapters)
        for line in lines
    ]


def _classify_body(paragraph: str, *, is_permanent: bool) -> list[Line]:
    lower = paragraph.lower()

    if lower.startswith(TRIGGER_WORDS):
        return [Line(paragraph, LineKind.TRIGGERED)]

    cost, effect = _split_activation(paragraph)
    if cost is not None:
        return [Line(paragraph, LineKind.ACTIVATED)]

    keywords = _keyword_line(paragraph)
    if keywords is not None:
        return [Line(part, LineKind.KEYWORD) for part in keywords]

    if _LEVEL.match(paragraph):
        return [Line(paragraph, LineKind.STATIC)]

    return [Line(paragraph, LineKind.SPELL if not is_permanent else LineKind.STATIC)]


#: A mode whose name is printed before a dash: "Sell Contraband - Create a
#: Treasure token." The label is flavour with no rules meaning, unlike
#: "Max speed -", which is why it is stripped rather than read.
_MODE_LABEL = re.compile(r"^[A-Z][A-Za-z'!, ]{2,30} - (?=[A-Z{])")


def _strip_mode_label(mode: str) -> str:
    match = _MODE_LABEL.match(mode)
    return mode[match.end() :].strip() if match else mode


def _split_activation(paragraph: str) -> tuple[str | None, str]:
    """Split "cost: effect" at the *activation* colon (CR 602.1).

    The first colon is not always the right one - "Sacrifice a creature: Target
    player loses 2 life" has one colon and "Choose a creature type: ..." has a
    colon inside the effect. The activation colon is the one whose left side
    reads as a cost, which in practice means it contains a mana symbol, a tap
    symbol, or a cost verb, and no sentence-ending punctuation.
    """
    for match in re.finditer(r":", paragraph):
        # A colon inside quotation marks belongs to a *granted* ability, not
        # to this one. "Enchanted creature has '{T}: Add {G}'" is a static
        # ability that grants an activated one; splitting at the inner colon
        # made the parser read "Enchanted creature has '{T}" as a cost, which
        # is the single largest source of unreadable costs in the pool.
        if paragraph.count('"', 0, match.start()) % 2 == 1:
            continue
        left = paragraph[: match.start()].strip()
        if not left or "." in left:
            continue
        if _looks_like_cost(left):
            return left, paragraph[match.end() :].strip()
    return None, paragraph


#: Verbs that can be a cost (CR 118.3, 601.2h). Not exhaustive prose - these
#: are the ones that appear left of an activation colon.
_COST_VERBS = (
    "sacrifice", "discard", "exile", "tap", "untap", "pay", "remove",
    "return", "reveal", "put", "mill",
)


def _looks_like_cost(text: str) -> bool:
    lower = text.lower()
    if "{" in text:
        return True
    if lower.startswith(_COST_VERBS):
        return True
    # "Level up {2}" style: a keyword followed by its cost.
    return bool(re.match(r"^[+-]?\d+$", lower))


def _keyword_line(paragraph: str) -> list[str] | None:
    """A line that is nothing but keyword abilities.

    Comma-separated keywords are several abilities on one line (CR 702.2b), so
    they are split. A line is only treated this way if *every* part is a known
    keyword - one unknown part and the whole line goes to the ordinary path,
    because "Flying, and it can't be blocked" is not two keywords.
    """
    from ..rules.keywords import is_known

    if paragraph.endswith("."):
        # A sentence, not a keyword list. "Flying" never has a full stop;
        # "Enchant creature" and "Equip {2}" do not either.
        #
        # A keyword whose argument is a *cost* is the exception: "Ward - Pay
        # 2 life." is written as a sentence and always ends in one. The dash
        # is what distinguishes it, and without this every Ward-with-a-cost
        # went to the effect grammar and failed there.
        stripped = paragraph[:-1].strip()
        if " - " in stripped or " — " in stripped:
            head = stripped.replace(" — ", " - ").split(" - ", 1)[0].strip()
            from ..rules.keywords import is_known as _known

            if _is_keyword_phrase(head, _known):
                return [stripped]
        return None

    parts = [part.strip() for part in paragraph.split(",") if part.strip()]
    if not parts:
        return None

    for part in parts:
        if not _is_keyword_phrase(part, is_known):
            return None
    return parts


def _is_keyword_phrase(part: str, is_known) -> bool:
    """Whether one comma-separated part names a keyword, with its parameter.

    "Ward {2}", "Protection from red", "Annihilator 2" and "Bushido 1" are all
    a keyword plus an argument, so the test is on the longest leading prefix
    that is a known keyword rather than on the whole phrase.
    """
    words = part.split()
    for length in range(len(words), 0, -1):
        candidate = " ".join(words[:length])
        if is_known(candidate):
            return True
    return False


def _modal_block(
    paragraphs: list[str], index: int, *, is_permanent: bool
) -> tuple[Line, int] | None:
    """A "Choose one -" header plus the bullets that follow it (CR 700.2).

    The bullets may share the header's paragraph or sit in their own, and both
    forms appear in the corpus - so both are gathered, and the block ends at
    the first paragraph that is not a bullet.
    """
    paragraph = paragraphs[index]
    lower = paragraph.lower()

    # The marker does not have to start the paragraph. "When this creature
    # enters, choose one -" is a *triggered* ability whose effect is modal,
    # and requiring the marker at position zero meant every such card had its
    # modes thrown away and its ability fail on the words "choose one".
    at = -1
    for marker in MODAL_MARKERS:
        found = lower.find(marker)
        if found != -1 and (at == -1 or found < at):
            at = found
    if at == -1:
        return None

    header, _, inline = paragraph.partition(" - ")
    modes: list[str] = []
    if inline.strip():
        modes.extend(part.strip() for part in inline.split("•") if part.strip())

    cursor = index + 1
    while cursor < len(paragraphs) and _BULLET.match(paragraphs[cursor]):
        modes.append(_strip_mode_label(_BULLET.sub("", paragraphs[cursor]).strip()))
        cursor += 1

    if not modes:
        return None

    # Everything before the marker is an ordinary ability header, and it
    # decides what kind of ability this is. Only when the marker starts the
    # paragraph is the whole line modal.
    prefix = header[:at].strip().rstrip(",").strip()
    if not prefix:
        return Line(header.strip(), LineKind.MODAL, modes=tuple(modes)), cursor

    kind = (
        LineKind.TRIGGERED
        if prefix.lower().startswith(TRIGGER_WORDS)
        else LineKind.ACTIVATED
        if _split_activation(prefix + ":")[0] is not None
        else LineKind.STATIC
    )
    return Line(header.strip(), kind, modes=tuple(modes)), cursor


_ROMAN = {"I": 1, "V": 5, "X": 10}


def _roman_list(text: str) -> tuple[int, ...]:
    """"I, II" -> (1, 2). Saga chapter symbols (CR 714.2c)."""
    out = []
    for part in text.split(","):
        value = _roman(part.strip())
        if value:
            out.append(value)
    return tuple(out)


def _roman(numeral: str) -> int:
    total = 0
    previous = 0
    for char in reversed(numeral.upper()):
        value = _ROMAN.get(char, 0)
        if not value:
            return 0
        total += -value if value < previous else value
        previous = max(previous, value)
    return total
