"""Opcode numbering (``EffectKind``).

``EffectKind`` is an ``IntEnum``, and two members sharing a number are not two
opcodes: Python folds the second into an *alias* of the first. Both names go
on working, so nothing looks wrong - but they are one key, and the dispatch
table in ``resolve.py`` silently keeps whichever handler was registered last.

That is not hypothetical. SUSPEND_RULE was added as 153, which REPLACEMENT
already held, and its handler overwrote the replacement handler: every "if it
would die, exile it instead" stopped working, and the only symptom was one
unrelated-looking test about Unearth.

The class comment has always said the numbers must be unique. This makes
saying it enough.
"""

from __future__ import annotations

from collections import Counter

from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import EXECUTORS


def test_no_two_opcodes_share_a_number():
    """``list(EffectKind)`` hides aliases, so this has to read __members__ -
    checking the de-duplicated list against itself always passes."""
    counts = Counter(member.value for member in EffectKind.__members__.values())
    shared = {
        value: sorted(
            name for name, m in EffectKind.__members__.items() if m.value == value
        )
        for value, count in counts.items()
        if count > 1
    }
    assert not shared, f"opcodes sharing a number, so one is an alias: {shared}"


def test_every_handler_is_reachable():
    """A handler keyed by an alias is a handler that shadows another one.

    Registering N handlers and finding fewer than N entries means two keys
    collapsed into one, which is the same fault seen from the other side.
    """
    names = {EffectKind(kind).name for kind in EXECUTORS}
    assert len(names) == len(EXECUTORS)
