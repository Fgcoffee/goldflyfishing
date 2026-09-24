"""A land that becomes a creature (CR 305.7, with CR 302.6).

CR 305.7 has two halves, and only one of them is about mana. The first says
what happens when an effect *sets* a land's subtype - it loses its old land
types, its rules text and the abilities they gave it - and that half is
already covered, in ``test_layers.py`` and ``test_interactions.py``. The
second half is the quieter one: setting or gaining a land type never adds or
removes a *card* type, and a land that gains types in addition to its own
keeps everything it had.

Which puts an animated land in an awkward position. It is a land and a
creature at once, so the land rules and the creature rules both apply to it,
and the one that bites is CR 302.6: summoning sickness. A land played this
turn and animated this turn cannot attack and cannot pay a {T} cost - not
because of anything in CR 305, but because it is now a creature and has not
been controlled since the turn began. A land played last turn and animated
today can do both.

The engine gets this right, and these are the regression tests that say so.
Nothing here needed fixing; the summoning-sickness flag is kept on every
object rather than on creatures, which is exactly what makes the answer come
out right the moment the type line changes.
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, make_board

from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind
from mtgfish.rules.cr500_turn_structure.cr506_combat import can_attack, can_block_at_all
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType, Phase, Zone
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.query import ObjectFilter, Value

#: The animator is a separate permanent, so the land itself is untouched and
#: the type change really does come from a continuous effect in layer 4 rather
#: than from anything printed on the land.
ANIMATOR = "Sol Ring"


@pytest.fixture
def board(card_db):
    return make_board(card_db, ScriptedAbilities())


def animated_forest(board, *, played_this_turn: bool):
    """A Forest that some other permanent has made a 3/3 creature."""
    land = board.game.move_object(
        board.hand("Forest", controller=0), Zone.BATTLEFIELD, to_player=0
    )
    if not played_this_turn:
        # CR 302.6: the untap step clears this for the active player, so a land
        # played on an earlier turn arrives here already clear.
        land.summoning_sick = False
    that_land = ObjectFilter(specific=(land.id,))
    board.scripts.add(
        ANIMATOR,
        Ability.static(
            Effect(EffectKind.ADD_TYPE, targets=that_land, types=CardType.CREATURE),
            Effect(
                EffectKind.SET_PT,
                targets=that_land,
                amount=Value.of(3),
                amount2=Value.of(3),
            ),
            text="that land is a 3/3 creature",
        ),
    )
    board.play(ANIMATOR, controller=0)
    board.refresh()
    board.game.phase = Phase.PRECOMBAT_MAIN
    return land


def may_tap_for_mana(board, land):
    return any(
        action.kind is ActionKind.ACTIVATE_MANA_ABILITY and action.source == land.id
        for action in legal_actions(board.game, 0)
    )


# ---------------------------------------------------------------------------
# CR 305.7: gaining a card type takes nothing away
# ---------------------------------------------------------------------------


def test_an_animated_land_is_still_a_land(board):
    """CR 305.7: gaining a card type does not remove one. It is a creature
    *and* a land, which is why it is summoning sick and still taps for mana in
    the same breath."""
    land = animated_forest(board, played_this_turn=False)
    chars = board.chars(land)

    assert chars.has_type(CardType.LAND)
    assert chars.has_type(CardType.CREATURE)


def test_an_animated_land_keeps_its_land_type_and_its_mana(board):
    """CR 305.7: it keeps its land types and rules text. The Forest is still a
    Forest, and CR 305.6 still gives it {T}: Add {G}."""
    land = animated_forest(board, played_this_turn=False)

    assert board.chars(land).type_line.subtypes == ("Forest",)
    assert board.taps_for(land) == {"G"}


def test_an_animated_land_keeps_its_supertype(board):
    """CR 305.7 names supertypes explicitly: basic is not disturbed either."""
    land = animated_forest(board, played_this_turn=False)

    assert "Basic" in str(board.chars(land).type_line)


def test_an_untouched_land_is_not_a_creature(board):
    """The control: the type comes from the animator, not from the harness."""
    land = board.game.move_object(
        board.hand("Forest", controller=0), Zone.BATTLEFIELD, to_player=0
    )

    assert not board.chars(land).has_type(CardType.CREATURE)


# ---------------------------------------------------------------------------
# CR 302.6: what being a creature costs it
# ---------------------------------------------------------------------------


def test_a_land_played_this_turn_cannot_attack_once_animated(board):
    """CR 302.6: it has not been controlled since the turn began. Nothing in
    CR 305 says so - it is summoning sick because it is now a creature."""
    land = animated_forest(board, played_this_turn=True)

    assert not can_attack(board.game, land)


def test_a_land_played_earlier_can_attack_once_animated(board):
    """The control, and the half that matters in a real game: the land has
    been there since before the turn, so animating it is an attack."""
    land = animated_forest(board, played_this_turn=False)

    assert can_attack(board.game, land)


def test_a_land_played_this_turn_cannot_tap_for_mana_once_animated(board):
    """CR 302.6's other half: a summoning-sick creature cannot pay a {T} cost,
    and the mana ability CR 305.6 gave the land has one. This is the trap -
    animating your own untapped land can take its mana away."""
    land = animated_forest(board, played_this_turn=True)

    assert not may_tap_for_mana(board, land)


def test_a_land_played_this_turn_taps_for_mana_while_it_is_not_a_creature(board):
    """The control that isolates the cause: same land, same turn, no
    animator - CR 305.1 lets a land tap the turn it is played."""
    land = board.game.move_object(
        board.hand("Forest", controller=0), Zone.BATTLEFIELD, to_player=0
    )
    board.game.phase = Phase.PRECOMBAT_MAIN

    assert may_tap_for_mana(board, land)


def test_a_land_played_earlier_still_taps_for_mana_once_animated(board):
    """The other control: animation by itself takes nothing away."""
    land = animated_forest(board, played_this_turn=False)

    assert may_tap_for_mana(board, land)


def test_summoning_sickness_does_not_stop_an_animated_land_blocking(board):
    """CR 302.6 restricts attacking and {T} costs and nothing else - CR 509.1a
    lets a summoning-sick creature block, animated land included."""
    land = animated_forest(board, played_this_turn=True)

    assert can_block_at_all(board.game, land)
