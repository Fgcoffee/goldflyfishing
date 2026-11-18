"""Dummy abilities: shapes the parser recognises but the engine can't do yet.

An ability the grammar can't read is unread, and the coverage numbers say
so. Some of those are not grammar gaps at all: the words are plain, and what
is missing is the engine's ability to do what they say ("untap during each
other player's untap step", "begin the game with it on the battlefield").
For those the parser emits a ``DUMMY`` effect naming the shape, so the card
reports "recognised, not implemented yet" instead of "unread", and the shape
goes on the rules session's backlog with how much play it would unlock
(``python -m mtgfish.tools.dummy_report``).

A dummy is a label, not a behaviour: the ability does nothing in a game,
exactly as an unread one does. Two forms:

- **whole**: the entire ability is the shape. It becomes a static ability
  whose only effect is the dummy (an activated or triggered one loses its
  cost and trigger too - it couldn't do anything anyway).
- **rider**: the shape is the ability's last sentence(s), after a part the
  grammar reads ("{T}: Add one mana ... When that mana is spent to cast ...,
  scry 1"). The readable part is kept and the dummy is appended to it, so
  Path of Ancestry still taps for mana.

Each pattern is matched against the whole (normalised) ability text or its
final sentences, case-insensitively, and only for abilities the grammar did
not read, so no reading that works today can change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Dummy:
    name: str
    #: What the engine would have to do; the rules session's backlog line.
    needs: str
    #: Comprehensive Rules section, where one applies.
    rule: str
    whole: tuple[str, ...] = ()
    rider: tuple[str, ...] = ()
    #: Emitted by a grammar rule rather than matched by a pattern (the words
    #: are read, with their amounts, and only the engine side is missing).
    grammar: bool = False


#: Costs before a colon, so an activated ability's effect can be matched.
_COST = r"(?:[^.:\"]+: )?"

DUMMIES: tuple[Dummy, ...] = (
    Dummy(
        "activation-cost-change",
        "an ability that costs less (or more) to activate, per something counted (the channel lands)",
        "601.2f",
        grammar=True,
    ),
    Dummy(
        "untap-during-others-untap-step",
        "untap permanents during each other player's untap step (Seedborn Muse)",
        "502.3",
        whole=(
            _COST + r"untap (?:all|each) (?:[a-z ]+ )?(?:permanents|artifacts|creatures|lands) you control"
            r" during each other player's untap step\.",
            r"untap this (?:artifact|creature|permanent) during each other player's untap step\.",
        ),
    ),
    Dummy(
        "begin-game-on-battlefield",
        "a card in the opening hand may start the game on the battlefield (the Leylines)",
        "103.6",
        whole=(
            r"if this card is in your opening hand, you may begin the game with it on the battlefield\.",
            r"if this card is in your opening hand and you're not the starting player, you may begin the"
            r" game with (?:it|this) on the battlefield with a luck counter on it\. if you do, exile a card"
            r" from your hand\.",
        ),
    ),
    Dummy(
        "next-spell-rider",
        "a rider on the next spell cast this turn (can't be countered, gains a keyword)",
        "",
        whole=(
            _COST + r"the next (?:[a-z ]+ )?spell you cast this turn (?:can't be countered|has [a-z]+)\.",
        ),
    ),
    Dummy(
        "mana-spent-trigger",
        "a trigger or rider on spending the mana an ability made (Path of Ancestry)",
        "106.12",
        rider=(
            r"when that mana is spent to cast [^.]+\.",
            r"if that mana is spent on [^.]+\.",
        ),
    ),
    Dummy(
        "mana-not-lost",
        "mana that isn't lost as steps and phases end (Birgi)",
        "500.4",
        rider=(r"until end of turn, you don't lose this mana as steps and phases end\.",),
    ),
)


def _norm(text: str) -> str:
    text = text.strip().lower().replace("’", "'")
    text = re.sub(r"\s+", " ", text)
    return text if text.endswith(".") else text + "."


_WHOLE = [(d, re.compile(p)) for d in DUMMIES for p in d.whole]
_RIDER = [(d, re.compile(p)) for d in DUMMIES for p in d.rider]


def whole(text: str) -> Dummy | None:
    """The dummy the whole ability is, if it is one."""
    norm = _norm(text)
    for dummy, pattern in _WHOLE:
        if pattern.fullmatch(norm):
            return dummy
    return None


def rider(text: str) -> tuple[Dummy, str, str] | None:
    """(dummy, the readable part, the rider) when the ability's last
    sentence(s) are a rider and something comes before them. The parts keep
    the card's own words."""
    parts = re.split(r"(?<=\.)\s+(?=[A-Z{])", text.strip())
    for k in range(1, len(parts)):
        tail = " ".join(parts[k:])
        norm = _norm(tail)
        for dummy, pattern in _RIDER:
            if pattern.fullmatch(norm):
                return dummy, " ".join(parts[:k]), tail
    return None


def by_name(name: str) -> Dummy | None:
    return next((d for d in DUMMIES if d.name == name), None)
