"""A filter's numeric bound is read off the right object.

``ObjectFilter`` bounds come in two shapes and they are about different
things. "Power greater than its toughness" is a question about the candidate.
"Mana value X or less", "power less than or equal to the number of creatures
you control" are questions about the *ability* - the X chosen as the spell was
cast, the board of the player who controls it.

Every bound used to be evaluated against the candidate, which turned the
second shape into nonsense: each card was compared against its own ``x_value``
(zero, for anything never cast for X) and each opponent's creature against
that opponent's board. Chord of Calling searched a library and found nothing,
whatever X was paid; Beguiler of Wills counted the wrong player's creatures.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from harness import ScriptedAbilities

from mtgfish.parser.verdicts import VerdictStore
from mtgfish.rules.cr100_game_concepts.cr117_priority import ActionKind, _perform
from mtgfish.rules.cr100_game_concepts.cr118_costs import TAP_COST
from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.kernel.enums import CardType, Zone
from mtgfish.rules.kernel.gameobject import GameObject
from mtgfish.rules.kernel.ids import NO_OBJECT, PlayerId
from mtgfish.rules.kernel.legality import legal_actions
from mtgfish.rules.kernel.matching import matches
from mtgfish.rules.kernel.query import (
    Comparison,
    ControllerRelation,
    NumericConstraint,
    ObjectFilter,
    Value,
    ValueKind,
)
from mtgfish.ui.sandbox import Sandbox

YOU = PlayerId(0)
THEM = PlayerId(1)


@pytest.fixture
def box(card_db, tmp_path):
    card_db.registry()
    return Sandbox(db=card_db, verdicts=VerdictStore(tmp_path / "v.json"))


def _need(box, *names):
    for name in names:
        if box.db.lookup(name) is None:
            pytest.skip(f"{name} is not in this card pool")


def _place(box, name, zone="battlefield", player=0) -> GameObject:
    """Place a card and return the object, not the sandbox's state summary."""
    box.put(name, zone, player)
    return next(
        obj
        for obj in reversed(list(box.game.objects.values()))
        if obj.card is not None and obj.card.name == name and obj.zone is Zone[zone.upper()]
    )


def _names_on(game, player: PlayerId) -> list[str]:
    return sorted(game.characteristics(obj).name for obj in game.permanents(player))


def _library(game, player: PlayerId) -> list[str]:
    return sorted(game.objects[i].card.name for i in game.player(player).library)


def _action(game, kind: ActionKind, source: int) -> tuple[int, object]:
    """The one legal action of a kind from a source, with its index."""
    for index, action in enumerate(legal_actions(game, YOU)):
        if action.kind is kind and action.source == source:
            return index, action
    raise AssertionError(f"no {kind.name} available from {source}")


# ---------------------------------------------------------------------------
# X: a fact about the spell, not about the card being searched for
# ---------------------------------------------------------------------------


def test_x_bound_library_search_uses_the_spells_x(box):
    """Chord of Calling for two finds a two-drop and leaves the fatty behind.

    The library is stocked expensive-card-first so the search has to reject
    something: finding nothing and finding everything both fail here.
    """
    _need(box, "Chord of Calling", "Craterhoof Behemoth", "Llanowar Elves")
    _place(box, "Craterhoof Behemoth", "library")
    _place(box, "Llanowar Elves", "library")
    chord = _place(box, "Chord of Calling", "hand")
    box.give_mana(10)

    _, action = _action(box.game, ActionKind.CAST_SPELL, chord.id)
    assert _perform(box.game, YOU, replace(action, x_value=2))
    box.resolve_top()

    assert _names_on(box.game, YOU) == ["Llanowar Elves"]
    assert _library(box.game, YOU) == ["Craterhoof Behemoth"]


def test_x_is_read_off_the_spell_not_off_the_candidate(box):
    """The bug in one line: a card in the library has an ``x_value`` of its own.

    It is zero, because nothing ever cast it for X, so "mana value X or less"
    read off the candidate excluded every card in the library including the
    one-drops.
    """
    _need(box, "Green Sun's Zenith", "Llanowar Elves")
    elves = _place(box, "Llanowar Elves", "library")
    spell = _place(box, "Green Sun's Zenith", "hand")
    spell.x_value = 2

    spec = ObjectFilter(
        zones=frozenset({Zone.LIBRARY}),
        mana_value=NumericConstraint(Comparison.LE, Value(kind=ValueKind.X)),
    )
    assert elves.x_value == 0
    assert matches(box.game, elves, spec, source=spell.id, controller=YOU)

    spell.x_value = 0
    assert not matches(box.game, elves, spec, source=spell.id, controller=YOU)


# ---------------------------------------------------------------------------
# "you control": the ability's controller, whoever controls the candidate
# ---------------------------------------------------------------------------


def test_counted_bound_uses_the_ability_controllers_board(box):
    """Beguiler of Wills counts *your* creatures, not the victim's.

    Four creatures on this side of the table and two on the other, so the two
    readings disagree about a three-power creature: legal under the real rule,
    illegal if the count is taken from the creature's own controller.
    """
    _need(box, "Beguiler of Wills", "Grizzly Bears", "Llanowar Elves", "Memnite")
    _need(box, "Hill Giant", "Colossal Dreadmaw")
    beguiler = _place(box, "Beguiler of Wills")
    for name in ("Grizzly Bears", "Llanowar Elves", "Memnite"):
        _place(box, name)
    _place(box, "Hill Giant", player=1)
    _place(box, "Colossal Dreadmaw", player=1)

    index, _ = _action(box.game, ActionKind.ACTIVATE_ABILITY, beguiler.id)
    offered = {
        candidate["name"] for group in box.targets_for(index) for candidate in group["candidates"]
    }
    # Power 3 against a bound of 4 - and against a bound of 2, which is what
    # counting the opponent's two creatures would have given.
    assert "Hill Giant" in offered
    assert "Colossal Dreadmaw" not in offered


def test_lands_you_control_bound_counts_the_controllers_lands(box):
    """"... less than or equal to the number of lands you control", swept.

    No card in the pool parses into this shape with an opponent's permanent as
    the candidate, so the ability is scripted onto a chassis: what is being
    tested is the bound, not the reading of any particular card. The victim
    controls no lands at all, so the broken reading destroyed nothing.
    """
    _need(box, "Nevinyrral's Disk", "Forest", "Grizzly Bears", "Colossal Dreadmaw")
    sweep = Effect(
        EffectKind.DESTROY,
        targets=ObjectFilter(
            types_any=CardType.CREATURE,
            mana_value=NumericConstraint(
                Comparison.LE,
                Value(
                    kind=ValueKind.COUNT,
                    filter=ObjectFilter(
                        types_any=CardType.LAND, controller=ControllerRelation.YOU
                    ),
                ),
            ),
        ),
        text="destroy each creature with mana value <= the number of lands you control",
    )
    box.game.ability_provider = ScriptedAbilities().add(
        "Nevinyrral's Disk",
        Ability(
            AbilityKind.ACTIVATED,
            cost=TAP_COST,
            effects=(sweep,),
            text="{T}: destroy each creature with mana value <= the lands you control",
        ),
    )
    box.game.invalidate_characteristics()

    for _ in range(3):
        _place(box, "Forest")
    disk = _place(box, "Nevinyrral's Disk")
    disk.tapped = False  # The printed card enters tapped; the cost needs it untapped.
    _place(box, "Grizzly Bears", player=1)
    _place(box, "Colossal Dreadmaw", player=1)
    box.game.invalidate_characteristics()

    _, action = _action(box.game, ActionKind.ACTIVATE_ABILITY, disk.id)
    assert _perform(box.game, YOU, action)
    box.resolve_top()

    # Three lands: mana value 2 goes, mana value 6 stays.
    assert _names_on(box.game, THEM) == ["Colossal Dreadmaw"]


# ---------------------------------------------------------------------------
# The other subject, and the values that have no single one
# ---------------------------------------------------------------------------


def test_of_affected_bound_still_reads_the_candidate(box):
    """"power equal to 1 plus its toughness" is about the creature being tested.

    Birthing Pod's "1 plus the sacrificed creature's mana value" has this
    shape - a constant added to a characteristic marked ``of_affected`` - and
    an over-eager redirection to the ability's source would answer it with the
    source's numbers for every candidate alike.
    """
    _need(box, "Craterhoof Behemoth", "Vexing Devil", "Grizzly Bears")
    source = _place(box, "Craterhoof Behemoth")  # 5/5: not either answer below.
    devil = _place(box, "Vexing Devil")  # 4/3
    bears = _place(box, "Grizzly Bears")  # 2/2

    spec = ObjectFilter(
        types_any=CardType.CREATURE,
        power=NumericConstraint(
            Comparison.EQ,
            Value(
                kind=ValueKind.SUM,
                operands=(
                    Value.of(1),
                    Value(kind=ValueKind.TOUGHNESS, of_affected=True),
                ),
            ),
        ),
    )
    assert matches(box.game, devil, spec, source=source.id, controller=YOU)
    assert not matches(box.game, bears, spec, source=source.id, controller=YOU)


def test_source_characteristic_bound_still_reads_the_source(box):
    """Transmute's "the same mana value as the discarded card" (CR 702.53a).

    A characteristic with ``of_affected`` unset is the ability's, and read off
    each candidate instead it would compare every card with itself and match
    the whole library.
    """
    _need(box, "Grizzly Bears", "Elvish Visionary", "Craterhoof Behemoth")
    source = _place(box, "Grizzly Bears", "graveyard")  # mana value 2
    same = _place(box, "Elvish Visionary", "library")  # mana value 2
    bigger = _place(box, "Craterhoof Behemoth", "library")  # mana value 8

    spec = ObjectFilter(
        zones=frozenset({Zone.LIBRARY}),
        mana_value=NumericConstraint(Comparison.EQ, Value(kind=ValueKind.MANA_VALUE)),
    )
    assert matches(box.game, same, spec, source=source.id, controller=YOU)
    assert not matches(box.game, bigger, spec, source=source.id, controller=YOU)


def test_bound_mixing_both_subjects_fails_closed(box):
    """A value that reads the candidate *and* the ability has no one subject.

    Nothing builds this today. It fails closed rather than picking a side,
    because a bound evaluated against the wrong half is over-broad, and an
    over-broad filter is how a spell hits what it must not.
    """
    _need(box, "Green Sun's Zenith", "Vexing Devil")
    spell = _place(box, "Green Sun's Zenith", "hand")
    spell.x_value = 1
    devil = _place(box, "Vexing Devil")  # 4/3, and 1 + 3 = 4.

    mixed = Value(
        kind=ValueKind.SUM,
        operands=(
            Value(kind=ValueKind.X),
            Value(kind=ValueKind.TOUGHNESS, of_affected=True),
        ),
    )
    spec = ObjectFilter(
        types_any=CardType.CREATURE,
        power=NumericConstraint(Comparison.EQ, mixed),
    )
    assert not matches(box.game, devil, spec, source=spell.id, controller=YOU)


def test_bound_about_the_ability_fails_closed_with_no_ability(box):
    """No source is no ability, and a bound about one cannot be answered.

    Zero is not a safe default: "power X or greater" with X read as zero
    matches every creature on the board.
    """
    _need(box, "Grizzly Bears")
    bears = _place(box, "Grizzly Bears")

    for bound in (
        Value(kind=ValueKind.X),
        Value(
            kind=ValueKind.COUNT,
            filter=ObjectFilter(types_any=CardType.LAND, controller=ControllerRelation.YOU),
        ),
    ):
        spec = ObjectFilter(
            types_any=CardType.CREATURE,
            power=NumericConstraint(Comparison.GE, bound),
        )
        assert not matches(box.game, bears, spec, source=NO_OBJECT, controller=YOU)

    # Arithmetic over constants asks the game nothing, so it still answers.
    constant = Value(kind=ValueKind.HALF_ROUNDED_DOWN, operands=(Value.of(5),))
    spec = ObjectFilter(
        types_any=CardType.CREATURE,
        power=NumericConstraint(Comparison.EQ, constant),
    )
    assert matches(box.game, bears, spec, source=NO_OBJECT, controller=YOU)
