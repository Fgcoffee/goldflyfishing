"""mtgfish - a Magic: The Gathering Commander simulator and goldfisher.

The package is layered, and the layering is load-bearing:

    rules/   The Comprehensive Rules, implemented. Knows nothing about oracle
             text, bots, or statistics. Every legality check lives here.
    parser/  Turns Scryfall oracle text into an Effect IR made of opcodes the
             rules layer already implements. It cannot express an effect the
             engine does not have, which is what makes a mis-parse inert
             rather than corrupting.
    data/    Card acquisition (Scryfall) and deck import.
    ai/      Decision-making. Enumerates legal actions *from the engine* so it
             can never drift from the real rules.
    sim/     Deterministic parallel game running, statistics, replay.
    ui/      PySide6 + QtWebEngine frontend.
"""

__version__ = "0.1.0"

# Bumped whenever a change could alter the outcome of a game given identical
# input. Recorded in every simulation run so a replay can refuse to reconstruct
# a game it would no longer reproduce faithfully.
#
# 2: activated abilities are only offered when their non-mana costs can be paid
#    and they have a legal target, and they now choose targets when activated.
#    The simple bot stops retrying actions that failed in the same step and
#    caps repeats of one ability or one card per turn.
ENGINE_VERSION = 2
