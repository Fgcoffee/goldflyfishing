"""Face-down spells and permanents (CR 708), and what makes them.

The earlier engine had the 2/2 in layer 1b and a special action that flipped
the flag back, and very little between. Every test here is a gap that was
open:

- a morph spell resolved into a *face-up* permanent (CR 708.4), because the
  move to the battlefield built a new object and nothing carried the flag;
- manifest put the card onto the battlefield face up - its enters abilities
  firing - and then turned every creature its controller had face down;
- turning a permanent face up cost nothing at all (CR 702.37e);
- disguise's ward {2} was granted by an ability the face-down permanent does
  not have, so it never applied (CR 702.168a);
- "exile it face down" exiled it face up, and a copy of a face-down permanent
  copied the card underneath (CR 708.2, 406.3a).
"""

from __future__ import annotations

import pytest
from harness import ScriptedAbilities, keyword, make_board

from mtgfish.rules.cr100_game_concepts.cr106_mana import ManaCost, ManaKind
from mtgfish.rules.cr100_game_concepts.cr116_special_actions import (
    SpecialKind,
    available,
    perform,
)
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr100_game_concepts.cr118_costs import Cost, CostComponent, CostKind
from mtgfish.rules.cr600_spells_and_abilities.abilities import (
    Ability,
    AbilityKind,
    TriggerCondition,
)
from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import cast_spell
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.cr700_additional_rules import cr701_keyword_actions as keyword_actions
from mtgfish.rules.cr700_additional_rules.cr702_keyword_impl import KeywordInstance, build
from mtgfish.rules.cr700_additional_rules.cr707_faces import copy_permanent
from mtgfish.rules.cr700_additional_rules.cr708_face_down import turn_face_up
from mtgfish.rules.kernel.enums import LETTER_TO_COLOR, CardType, Phase, Step, Zone
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import ObjectFilter

YOU = PlayerId(0)
SELF = ObjectFilter(source_only=True)


def cost(text: str) -> Cost:
    return Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse(text)),))


def enters_trigger() -> Ability:
    """"When this creature enters, ..." - only whether it fires matters."""
    return Ability(
        AbilityKind.TRIGGERED,
        effects=(Effect(EffectKind.GAIN_LIFE, text="gain 1 life"),),
        trigger=TriggerCondition(
            event_kinds=frozenset({EventKind.ENTERS_BATTLEFIELD}),
            subject=SELF,
            text="when this creature enters",
        ),
        text="When this creature enters, you gain 1 life.",
    )


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    board.game.active_player = YOU
    board.game.phase = Phase.PRECOMBAT_MAIN
    board.game.step = Step.MAIN
    return board


def _mana(board, amount: int, letter: str = "") -> None:
    kind = ManaKind(LETTER_TO_COLOR[letter]) if letter else ManaKind()
    board.game.player(YOU).mana_pool.add(kind, amount)


def _on_top(board, name: str):
    card = board.db.lookup(name)
    return board.game.create_object(card, YOU, Zone.LIBRARY, to_top=True)


def _run(board, effects, source=None):
    source_id = source.id if source is not None else 0
    execute(Resolution(game=board.game, source=source_id, controller=YOU), effects)
    board.refresh()


def _live(board, obj):
    while obj.superseded_by:
        obj = board.game.objects[obj.superseded_by]
    return obj


def _cast_face_down(board, name: str, keyword_name: str):
    card = board.hand(name)
    alternatives = board.game.characteristics(card).alternative_costs
    index = next(i for i, a in enumerate(alternatives) if a.keyword == keyword_name)
    return cast_spell(
        board.game, YOU, Action(ActionKind.CAST_SPELL, source=card.id, alternative_cost=index)
    )


# ---------------------------------------------------------------------------
# CR 708.4: cast face down, and the permanent it becomes
# ---------------------------------------------------------------------------


def test_a_morph_spell_becomes_a_face_down_permanent(board):
    board.scripts.add(
        "Grizzly Bears",
        *build(KeywordInstance("Morph", cost=cost("{1}{G}"))),
        enters_trigger(),
    )
    _mana(board, 3)
    spell = _cast_face_down(board, "Grizzly Bears", "Morph")
    assert spell.face_down
    assert board.chars(spell).name == ""

    board.resolve_stack()
    permanent = _live(board, spell)
    assert permanent.zone is Zone.BATTLEFIELD
    assert permanent.face_down
    assert board.pt(permanent) == (2, 2)
    assert board.chars(permanent).name == ""
    # CR 708.3/708.4: it entered face down, so its own enters ability had
    # nothing to trigger from.
    assert not board.game.pending_triggers


def test_the_control_a_face_up_cast_triggers_its_enters_ability(board):
    board.scripts.add("Grizzly Bears", enters_trigger())
    _mana(board, 2, "G")
    card = board.hand("Grizzly Bears")
    cast_spell(board.game, YOU, Action(ActionKind.CAST_SPELL, source=card.id))
    board.resolve_stack()
    assert board.game.pending_triggers


def test_a_disguised_permanent_has_ward_two(board):
    """CR 702.168a: ward {2} is one of the characteristics it is given."""
    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Disguise", cost=cost("{G}"))))
    _mana(board, 3)
    spell = _cast_face_down(board, "Grizzly Bears", "Disguise")
    assert "Ward" in board.keywords(spell)
    board.resolve_stack()
    permanent = _live(board, spell)
    assert permanent.face_down
    assert "Ward" in board.keywords(permanent)


def test_a_morphed_permanent_has_no_ward(board):
    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Morph", cost=cost("{G}"))))
    _mana(board, 3)
    spell = _cast_face_down(board, "Grizzly Bears", "Morph")
    board.resolve_stack()
    assert "Ward" not in board.keywords(_live(board, spell))


# ---------------------------------------------------------------------------
# CR 702.37e: turning face up costs its morph cost
# ---------------------------------------------------------------------------


def test_turning_face_up_pays_the_morph_cost(board):
    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Morph", cost=cost("{2}"))))
    bears = board.play("Grizzly Bears", face_down=True, face_down_by="Morph")
    _mana(board, 3)
    (action,) = [a for a in available(board.game, YOU) if a.kind is SpecialKind.TURN_FACE_UP]
    assert perform(board.game, YOU, action.as_action())
    assert not bears.face_down
    assert board.game.player(YOU).mana_pool.total == 1


def test_an_unpaid_cost_leaves_it_face_down(board):
    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Morph", cost=cost("{2}"))))
    bears = board.play("Grizzly Bears", face_down=True, face_down_by="Morph")
    action = Action(ActionKind.SPECIAL, source=bears.id, ability_index=int(SpecialKind.TURN_FACE_UP))
    assert not perform(board.game, YOU, action)
    assert bears.face_down


def test_turning_face_up_is_a_new_timestamp_and_the_same_object(board):
    """CR 613.7f and CR 708.8: counters and damage stay; the id stays."""
    bears = board.play("Grizzly Bears", face_down=True)
    bears.add_counters("+1/+1", 1)
    before = bears.timestamp
    assert turn_face_up(board.game, bears)
    assert bears.is_live
    assert bears.timestamp > before
    assert bears.counter_count("+1/+1") == 1
    assert board.pt(bears) == (3, 3)


# ---------------------------------------------------------------------------
# CR 701.40, 701.58, 701.62: manifest, cloak, manifest dread
# ---------------------------------------------------------------------------


def test_manifest_puts_the_top_card_onto_the_battlefield_face_down(board):
    board.scripts.add("Grizzly Bears", enters_trigger())
    top = _on_top(board, "Grizzly Bears")
    _run(board, keyword_actions.build("Manifest"))
    permanent = _live(board, top)
    assert permanent.zone is Zone.BATTLEFIELD
    assert permanent.face_down
    assert permanent.face_down_by == "Manifest"
    assert board.pt(permanent) == (2, 2)
    # CR 708.3: its enters ability never saw it.
    assert not board.game.pending_triggers


def test_manifest_turns_nothing_else_face_down(board):
    """The old expansion followed the move with "turn face down" over every
    creature you control."""
    elves = board.play("Llanowar Elves")
    _on_top(board, "Grizzly Bears")
    _run(board, keyword_actions.build("Manifest"))
    assert not elves.face_down
    assert board.chars(elves).name == "Llanowar Elves"


def test_a_manifested_instant_still_enters(board):
    """CR 701.40a/f: it is the face-down 2/2 that enters, not the instant."""
    top = _on_top(board, "Lightning Bolt")
    _run(board, keyword_actions.build("Manifest"))
    permanent = _live(board, top)
    assert permanent.zone is Zone.BATTLEFIELD
    assert board.chars(permanent).type_line.has_type(CardType.CREATURE)


def test_a_manifested_planeswalker_has_no_loyalty(board):
    """CR 708.3: it enters as the 2/2, with no loyalty counters to die of."""
    top = _on_top(board, "Jace Beleren")
    _run(board, keyword_actions.build("Manifest"))
    permanent = _live(board, top)
    assert not permanent.counters
    board.sba()
    assert permanent.is_live


def test_a_manifested_creature_turns_up_for_its_mana_cost(board):
    """CR 701.40b."""
    top = _on_top(board, "Grizzly Bears")
    _run(board, keyword_actions.build("Manifest"))
    permanent = _live(board, top)
    _mana(board, 2, "G")
    (action,) = [a for a in available(board.game, YOU) if a.kind is SpecialKind.TURN_FACE_UP]
    assert str(action.cost) == "{1}{G}"
    assert perform(board.game, YOU, action.as_action())
    assert board.chars(permanent).name == "Grizzly Bears"


def test_a_manifested_megamorph_turned_up_for_its_mana_cost_gets_no_counter(board):
    """CR 701.40c: either way works, and CR 702.37b only rewards the
    megamorph cost."""
    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Megamorph", cost=cost("{5}"))))
    top = _on_top(board, "Grizzly Bears")
    _run(board, keyword_actions.build("Manifest"))
    permanent = _live(board, top)
    _mana(board, 2, "G")
    options = [a for a in available(board.game, YOU) if a.kind is SpecialKind.TURN_FACE_UP]
    assert [a.keyword for a in options] == ["Manifest"]  # {5} is unaffordable
    assert perform(board.game, YOU, options[0].as_action())
    assert permanent.counter_count("+1/+1") == 0


def test_a_manifested_instant_cannot_be_turned_face_up(board):
    """CR 701.40g: no special action, and an effect reveals it and leaves it."""
    top = _on_top(board, "Lightning Bolt")
    _run(board, keyword_actions.build("Manifest"))
    permanent = _live(board, top)
    _mana(board, 5, "R")
    assert not [a for a in available(board.game, YOU) if a.kind is SpecialKind.TURN_FACE_UP]
    _run(board, (Effect(EffectKind.TURN_FACE_UP, targets=ObjectFilter(face_down=True)),))
    assert permanent.face_down


def test_a_manifested_noncreature_permanent_card_has_no_mana_cost_route(board):
    top = _on_top(board, "Sol Ring")
    _run(board, keyword_actions.build("Manifest"))
    _mana(board, 5)
    assert _live(board, top).face_down
    assert not [a for a in available(board.game, YOU) if a.kind is SpecialKind.TURN_FACE_UP]


def test_cloak_is_manifest_with_ward(board):
    """CR 701.58a."""
    top = _on_top(board, "Grizzly Bears")
    _run(board, keyword_actions.build("Cloak"))
    permanent = _live(board, top)
    assert permanent.face_down_by == "Cloak"
    assert "Ward" in board.keywords(permanent)


def test_manifest_dread_manifests_one_and_bins_the_other(board):
    """CR 701.62a, and 701.62b's event after the whole process."""
    first = _on_top(board, "Lightning Bolt")
    second = _on_top(board, "Grizzly Bears")
    board.game.log.enabled = True
    start = len(board.game.log.entries)
    _run(board, keyword_actions.build("Manifest dread"))
    manifested, binned = _live(board, second), _live(board, first)
    # With no agent to ask, the creature card is the one manifested.
    assert manifested.zone is Zone.BATTLEFIELD and manifested.face_down
    assert binned.zone is Zone.GRAVEYARD
    events = [e.text for e in board.game.log.entries[start:] if e.kind == "event"]
    assert any(EventKind.MANIFESTED_DREAD.name in text for text in events)


# ---------------------------------------------------------------------------
# CR 708.2, 708.10: copies
# ---------------------------------------------------------------------------


def test_a_copy_of_a_face_down_permanent_is_a_nameless_two_two(board):
    """CR 708.2: the face-down values are its copiable values."""
    hidden = board.play("Llanowar Elves", face_down=True)
    clone = board.play("Grizzly Bears")
    copy_permanent(board.game, clone, hidden)
    board.refresh()
    assert board.chars(clone).name == ""
    assert board.pt(clone) == (2, 2)
    assert not clone.face_down


def test_the_control_a_copy_of_a_face_up_permanent_is_the_card(board):
    original = board.play("Llanowar Elves")
    clone = board.play("Grizzly Bears")
    copy_permanent(board.game, clone, original)
    board.refresh()
    assert board.chars(clone).name == "Llanowar Elves"


def test_a_face_down_copier_stays_face_down_until_turned_up(board):
    """CR 708.10: still the 2/2; turned up, it is what it copied."""
    original = board.play("Llanowar Elves")
    hidden = board.play("Grizzly Bears", face_down=True)
    copy_permanent(board.game, hidden, original)
    board.refresh()
    assert board.chars(hidden).name == ""
    turn_face_up(board.game, hidden)
    assert board.chars(hidden).name == "Llanowar Elves"


# ---------------------------------------------------------------------------
# CR 406.3, 708.9: exile face down, and revealing
# ---------------------------------------------------------------------------


def test_a_card_exiled_face_down_has_no_characteristics(board):
    """CR 406.3a - not a 2/2 creature, and no name."""
    bears = board.play("Grizzly Bears")
    _run(board, (Effect(EffectKind.EXILE, targets=ObjectFilter(specific=(bears.id,)),
                        keywords=("face down",)),))
    exiled = _live(board, bears)
    assert exiled.zone is Zone.EXILE and exiled.face_down
    chars = board.chars(exiled)
    assert chars.name == ""
    assert not chars.type_line.types


def test_the_control_an_ordinary_exile_is_face_up(board):
    bears = board.play("Grizzly Bears")
    _run(board, (Effect(EffectKind.EXILE, targets=ObjectFilter(specific=(bears.id,))),))
    exiled = _live(board, bears)
    assert not exiled.face_down
    assert board.chars(exiled).name == "Grizzly Bears"


def test_put_onto_the_battlefield_face_down(board):
    """CR 708.3: "put it onto the battlefield face down" - no enters trigger."""
    board.scripts.add("Grizzly Bears", enters_trigger())
    card = board.graveyard("Grizzly Bears")
    _run(board, (Effect(EffectKind.PUT_ONTO_BATTLEFIELD,
                        targets=ObjectFilter(specific=(card.id,), zones=frozenset({Zone.GRAVEYARD})),
                        keywords=("face down",)),))
    permanent = _live(board, card)
    assert permanent.zone is Zone.BATTLEFIELD and permanent.face_down
    assert not board.game.pending_triggers


def test_a_face_down_permanent_is_revealed_as_it_leaves(board):
    """CR 708.9: it arrives face up, and every player is shown what it was."""
    hidden = board.play("Grizzly Bears", face_down=True)
    board.game.log.enabled = True
    start = len(board.game.log.entries)
    board.game.move_object(hidden, Zone.GRAVEYARD)
    dead = _live(board, hidden)
    assert not dead.face_down
    assert board.chars(dead).name == "Grizzly Bears"
    reveals = [e.text for e in board.game.log.entries[start:] if e.kind == "reveal"]
    assert any("Grizzly Bears" in text for text in reveals)


def test_face_down_permanents_are_revealed_when_the_game_ends(board):
    board.play("Grizzly Bears", face_down=True)
    board.game.log.enabled = True
    start = len(board.game.log.entries)
    from mtgfish.rules.kernel.enums import LossReason

    board.game.player_loses(PlayerId(1), LossReason.CONCEDE)
    assert board.game.game_over
    reveals = [e.text for e in board.game.log.entries[start:] if e.kind == "reveal"]
    assert any("Grizzly Bears" in text for text in reveals)


def test_turning_a_face_down_permanent_face_down_does_nothing(board):
    """CR 708.2b: a manifested card stays manifested, ward and all."""
    top = _on_top(board, "Grizzly Bears")
    _run(board, keyword_actions.build("Cloak"))
    permanent = _live(board, top)
    stamp = permanent.timestamp
    _run(board, (Effect(EffectKind.TURN_FACE_DOWN, targets=ObjectFilter(specific=(permanent.id,))),))
    assert permanent.face_down_by == "Cloak"
    assert permanent.timestamp == stamp


def _bolt(board, target):
    board.scripts.add(
        "Lightning Bolt",
        Ability.spell(
            Effect(EffectKind.DAMAGE, targets=ObjectFilter(types_any=CardType.CREATURE),
                   is_targeted=True, text="3 damage to target creature"),
            text="Lightning Bolt deals 3 damage to any target.",
        ),
    )
    bolt = board.hand("Lightning Bolt", controller=1)
    board.game.player(PlayerId(1)).mana_pool.add(ManaKind(LETTER_TO_COLOR["R"]), 1)
    cast_spell(
        board.game, PlayerId(1),
        Action(ActionKind.CAST_SPELL, source=bolt.id, targets=((target.id,),)),
    )


def test_a_cloaked_permanents_ward_triggers(board):
    """CR 701.58a: the ward is a characteristic of the face-down permanent, so
    it triggers although the card underneath has no ward."""
    top = _on_top(board, "Grizzly Bears")
    _run(board, keyword_actions.build("Cloak"))
    _bolt(board, _live(board, top))
    assert [t.ability.keyword for t in board.game.pending_triggers] == ["Ward"]


def test_the_control_a_manifested_permanent_has_no_ward_to_trigger(board):
    top = _on_top(board, "Grizzly Bears")
    _run(board, keyword_actions.build("Manifest"))
    _bolt(board, _live(board, top))
    assert not board.game.pending_triggers


def test_a_morph_commander_can_be_cast_face_down_from_the_command_zone(board):
    """CR 702.37c: from any zone it could be cast from - with the tax."""
    from mtgfish.rules.kernel.legality import legal_actions

    board.scripts.add("Grizzly Bears", *build(KeywordInstance("Morph", cost=cost("{G}"))))
    commander = board.game.create_object(board.db.lookup("Grizzly Bears"), YOU, Zone.COMMAND)
    commander.is_commander = True
    _mana(board, 3)
    casts = [
        a for a in legal_actions(board.game, YOU)
        if a.kind is ActionKind.CAST_SPELL and a.source == commander.id
    ]
    assert [a.alternative_cost for a in casts] == [0]
    spell = cast_spell(board.game, YOU, casts[0])
    assert spell.face_down and spell.face_down_by == "Morph"


def _casts_of(board, card):
    from mtgfish.rules.kernel.legality import legal_actions

    return [
        a for a in legal_actions(board.game, YOU)
        if a.kind is ActionKind.CAST_SPELL and a.source == card.id
    ]


def test_a_flash_morph_card_is_not_cast_face_down_at_instant_speed(board):
    """CR 708.4: the face-down 2/2 has no flash, so it waits for sorcery
    speed; the card itself may still be cast face up with flash."""
    board.scripts.add(
        "Grizzly Bears", keyword("Flash"), *build(KeywordInstance("Morph", cost=cost("{G}")))
    )
    card = board.hand("Grizzly Bears")
    _mana(board, 3, "G")
    board.game.active_player = PlayerId(1)
    casts = _casts_of(board, card)
    assert [a.alternative_cost for a in casts] == [-1]


def test_the_control_at_sorcery_speed_it_may_be_cast_face_down(board):
    board.scripts.add(
        "Grizzly Bears", keyword("Flash"), *build(KeywordInstance("Morph", cost=cost("{G}")))
    )
    card = board.hand("Grizzly Bears")
    _mana(board, 3, "G")
    assert sorted(a.alternative_cost for a in _casts_of(board, card)) == [-1, 0]
