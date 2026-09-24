"""The numbers the game uses (CR 107.1, 107.2).

Magic counts in whole numbers, it does not count below zero, and when it
cannot work a number out at all it uses zero instead. Those three sentences
are three rules, and the engine had none of them written down anywhere: every
function that needed one re-derived it as a local ``if amount <= 0`` guard,
and the places that forgot silently did the opposite of the rule. A cost to
pay energy whose amount evaluated to -3 *gave* the player three energy.

The third rule is the interesting one, because it is not the whole story. CR
107.2 makes an undeterminable number zero, which is the right answer for
everything that merely reads a number - but CR 903.4f says a *cost* that
refers to an undefined quality is unpayable, and a cost of zero is the most
payable cost there is. Reporting a plain ``0`` for "the number of colors in
your commander's color identity" when the player has no commander therefore
makes such a cost free.

So an undeterminable number is carried as ``Undeterminable``: an ``int`` worth
zero, so every reader gets CR 107.2's answer without knowing this type exists,
and a distinct type, so the cost machinery in ``cr118_costs`` can tell it apart
from a genuine zero and refuse to pay it.
"""

from __future__ import annotations

from typing import Self

__all__ = [
    "UNDETERMINABLE",
    "Undeterminable",
    "chosen_number",
    "effect_result",
    "is_undeterminable",
]


class Undeterminable(int):
    """A number that could not be determined (CR 107.2).

    Worth zero on purpose. Arithmetic on it produces an ordinary ``int``,
    which is also deliberate: CR 107.2 speaks of a number used "as a result or
    in a calculation", so an undeterminable number that has been added to
    something is simply zero in that sum and stops being distinguishable. Only
    the direct result keeps the marker, and a cost is the one caller that
    reads it (CR 903.4f).

    ``reason`` is for the log and the refusal message - which quality was
    undefined - and never affects the value.
    """

    def __new__(cls, reason: str = "a number that cannot be determined") -> Self:
        value = super().__new__(cls, 0)
        value.reason = reason
        return value

    def __repr__(self) -> str:
        return f"Undeterminable({self.reason!r})"

    def __str__(self) -> str:
        # Anything printing an amount should see the number, not the marker.
        return "0"


#: The plain undeterminable number, for callers with nothing to explain.
UNDETERMINABLE = Undeterminable()


def is_undeterminable(amount: int) -> bool:
    """Whether this number is the CR 107.2 sentinel rather than a real zero."""
    return isinstance(amount, Undeterminable)


def effect_result(amount: int) -> int:
    """CR 107.1b: a calculation determining an effect's result is never negative.

    Not for the exceptions the same rule lists - doubling, tripling, or setting
    a life total or a creature's power and toughness, where a negative result
    stands. Nor for a game value such as power, which CR 107.1b explicitly
    allows to be below zero; this is for the amount an effect *does*.
    """
    return max(0, amount)


def chosen_number(amount: int) -> int:
    """CR 107.1b and 107.1c: a number a player chooses is zero or positive.

    "Any number" means zero or more, and no choice anywhere may be negative -
    which is the same clamp, written twice in the rules because one is about
    what a player may pick and the other about what the game does with it.
    """
    return max(0, amount)

