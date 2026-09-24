"""Rules an instrument may switch off - never a game.

The sandbox is not a game, it is a bench. On a bench the rules that end games
are in the way: a board built by hand has no library, so the first draw step
kills the operator for decking; a life total set to 1 to test a drain effect
ends the session instead of showing the effect; and mana handed over for a test
evaporates the moment the step ends.

None of that is a rules bug, and none of it should be fixed by making the
engine wrong. So the engine stays exactly as it is and grows one explicit,
default-empty set of suspensions, consulted at the handful of places that
enforce the rules in question. A real run never constructs anything but
``STRICT``, and every field below is False there, so a simulation cannot
accidentally inherit a bench setting - which would be far worse than a sandbox
that decks you.

Each flag names the rule it suspends, so a log or a UI can say what is off.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True, slots=True)
class Relaxations:
    """Which rules are currently suspended. All off is a real game."""

    #: CR 704.5a-c and 903.10: the four ways a player loses as a state-based
    #: action - zero life, an empty library drawn from, ten poison counters,
    #: and 21 commander damage. Suspended together, because a bench that saves
    #: you from one of them and not the others is still a bench that ends.
    players_cannot_lose: bool = False
    #: CR 121.3 / 704.5b: a draw from an empty library does nothing at all,
    #: rather than marking the player for the loss that follows. A hand-built
    #: board has no library, and every draw step would otherwise be fatal.
    draws_from_an_empty_library_do_nothing: bool = False
    #: CR 500.4: mana pools empty as each step and phase ends. On a bench the
    #: mana was handed over to make a test possible, and losing it at the step
    #: boundary means re-handing it after every advance.
    mana_pools_persist: bool = False

    def with_field(self, name: str, value: bool) -> Relaxations:
        """A copy with one flag changed, rejecting names that do not exist.

        Named rather than positional because the caller is a UI sending
        strings, and a typo that silently did nothing would look exactly like
        a relaxation that does not work.
        """
        if name not in NAMES:
            raise KeyError(f"no such relaxation: {name!r}")
        return replace(self, **{name: bool(value)})

    def as_dict(self) -> dict[str, bool]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @property
    def any_active(self) -> bool:
        return any(self.as_dict().values())


#: Every rule enforced: what a game uses, and the only value a run ever sees.
STRICT = Relaxations()

#: What the bench starts with. Nothing here changes how a card behaves - only
#: whether the session survives long enough to watch it.
BENCH = Relaxations(
    players_cannot_lose=True,
    draws_from_an_empty_library_do_nothing=True,
    mana_pools_persist=True,
)

NAMES: tuple[str, ...] = tuple(f.name for f in fields(Relaxations))

#: One line each, for a UI that has to explain what a checkbox does.
DESCRIPTIONS: dict[str, str] = {
    "players_cannot_lose": (
        "Nobody loses the game - not to zero life, an empty library, poison "
        "or commander damage (CR 704.5a-c, 903.10)"
    ),
    "draws_from_an_empty_library_do_nothing": (
        "Drawing from an empty library does nothing instead of losing the "
        "game (CR 704.5b), so a board with no deck behind it still works"
    ),
    "mana_pools_persist": (
        "Mana stays in the pool across steps and phases (CR 500.4), so mana "
        "handed over for a test survives the next advance"
    ),
}
