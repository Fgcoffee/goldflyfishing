"""Dungeons (CR 309), venturing (CR 701.49) and the initiative (CR 726)."""

from __future__ import annotations

import pytest
from harness import FixedAgent, ScriptedAbilities, make_board

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.tokens import Stream
from mtgfish.parser.triggers import designation_condition, parse_trigger
from mtgfish.rules.cr300_card_types.cr309_dungeons import (
    available_dungeons,
    current_dungeon,
    parse_dungeon_text,
    venture,
)
from mtgfish.rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.cr700_additional_rules.cr725_designations import (
    combat_damage_to_initiative_holder,
    initiative_holder,
    initiative_upkeep,
    take_initiative,
)
from mtgfish.rules.kernel.conditions import holds
from mtgfish.rules.kernel.enums import LossReason, Zone
from mtgfish.rules.kernel.events import EventKind
from mtgfish.rules.kernel.ids import PlayerId
from mtgfish.rules.kernel.query import YOU, Condition, ConditionKind, Value

P0 = PlayerId(0)
P1 = PlayerId(1)


class DungeonAgent(FixedAgent):
    """Chooses dungeons and rooms by name, from scripted lists."""

    def __init__(self, dungeons=(), rooms=()) -> None:
        super().__init__()
        self.dungeons = list(dungeons)
        self.rooms = list(rooms)
        self.room_offers: list[list[str]] = []
        self.dungeon_offers: list[list[str]] = []

    def choose_dungeon(self, game, player, options):
        self.dungeon_offers.append(list(options))
        return self.dungeons.pop(0) if self.dungeons else options[0]

    def choose_dungeon_room(self, game, player, options):
        self.room_offers.append(list(options))
        return self.rooms.pop(0) if self.rooms else options[0]


@pytest.fixture
def board(card_db):
    board = make_board(card_db, ScriptedAbilities())
    for player in board.game.players:
        board.game.agents[player.id] = FixedAgent()
    return board


def dungeon_named(game, name):
    return next(d for d in available_dungeons(game) if d.name == name)


def room_name(game, player=P0) -> str:
    obj = current_dungeon(game, player)
    assert obj is not None
    return obj.card.dungeon.rooms[game.player(player).venture_room].name


def run(board) -> None:
    """Resolve until nothing is waiting: put triggers on the stack, resolve
    them, check state-based actions, and repeat for whatever that set off."""
    game = board.game
    for _ in range(20):
        board.settle()
        if not game.stack:
            return
        board.resolve_stack()


def dungeons_in_command_zone(game, player=P0) -> list:
    return [
        game.objects[oid]
        for oid in game.command
        if game.objects[oid].owner == player and getattr(game.objects[oid].card, "dungeon", None)
    ]


# ---------------------------------------------------------------------------
# Reading the dungeon cards
# ---------------------------------------------------------------------------


def test_room_graphs_are_read_from_the_oracle_text(board):
    """CR 309.4: rooms joined by arrows, read from every dungeon's text."""
    game = board.game
    mine = dungeon_named(game, "Lost Mine of Phandelver")
    names = [room.name for room in mine.rooms]
    assert names[0] == "Cave Entrance"
    assert {names[i] for i in mine.rooms[0].leads_to} == {"Goblin Lair", "Mine Tunnels"}
    assert mine.rooms[mine.bottommost].name == "Temple of Dumathoin"

    mad_mage = dungeon_named(game, "Dungeon of the Mad Mage")
    assert len(mad_mage.rooms) == 9
    assert mad_mage.rooms[mad_mage.bottommost].name == "Mad Wizard's Lair"

    tomb = dungeon_named(game, "Tomb of Annihilation")
    assert len(tomb.rooms) == 5
    assert tomb.rooms[tomb.bottommost].name == "Cradle of the Death God"

    undercity = dungeon_named(game, "Undercity")
    assert undercity.entry_quality == "Undercity"
    assert undercity.rooms[undercity.bottommost].name == "Throne of the Dead Three"

    for dungeon in (mine, mad_mage, tomb, undercity):
        assert dungeon.is_mappable


def test_a_dungeon_printed_without_arrows_is_not_offered(board):
    """Baldur's Gate Wilderness lists its rooms with no arrows: there is no
    graph to follow, so it is never chosen rather than walked in some order
    nobody printed."""
    wilderness = dungeon_named(board.game, "Baldur's Gate Wilderness")
    assert len(wilderness.rooms) > 1
    assert not wilderness.is_mappable


def test_room_lines_parse_into_names_effects_and_arrows():
    rooms, quality = parse_dungeon_text(
        'You can\'t enter this dungeon unless you "venture into Somewhere."\n'
        "Top — Scry 1. (Leads to: Left, Right)\n"
        "Left — Draw a card. (Leads to: Bottom)\n"
        "Right — You gain 1 life. (Leads to: Bottom)\n"
        "Bottom — Draw a card."
    )
    assert quality == "Somewhere"
    assert [room.name for room in rooms] == ["Top", "Left", "Right", "Bottom"]
    assert rooms[0].leads_to == (1, 2)
    assert rooms[1].leads_to == (3,)
    assert rooms[3].leads_to == ()
    assert rooms[2].effect_text == "You gain 1 life."


def test_room_effects_come_from_the_parser(board):
    """CR 309.4c: each room ability's effect is ordinary card text."""
    mine = dungeon_named(board.game, "Lost Mine of Phandelver")
    cave = mine.abilities[0]
    assert [effect.kind for effect in cave.effects] == [EffectKind.SCRY]
    assert cave.trigger.room == 1
    assert Zone.COMMAND in cave.functions_in


def test_an_unreadable_room_stays_unparsed(board):
    """No invention: Mad Wizard's Lair is beyond the grammar, so its room
    ability carries an UNPARSED effect rather than a guess."""
    mad_mage = dungeon_named(board.game, "Dungeon of the Mad Mage")
    lair = mad_mage.abilities[mad_mage.bottommost]
    assert [effect.kind for effect in lair.effects] == [EffectKind.UNPARSED]


# ---------------------------------------------------------------------------
# Venturing (CR 701.49)
# ---------------------------------------------------------------------------


def test_first_venture_brings_a_dungeon_into_the_command_zone(board):
    """CR 701.49a, 309.4a: choose a dungeon, marker on its topmost room."""
    game = board.game
    agent = DungeonAgent(dungeons=["Tomb of Annihilation"])
    game.agents[P0] = agent

    venture(game, P0)

    dungeon = current_dungeon(game, P0)
    assert dungeon is not None and dungeon.zone is Zone.COMMAND
    assert dungeon.id in game.command
    assert room_name(game) == "Trapped Entry"
    # Undercity may only be entered by venturing into Undercity (701.49d).
    assert "Undercity" not in agent.dungeon_offers[0]
    assert "Baldur's Gate Wilderness" not in agent.dungeon_offers[0]
    assert "Lost Mine of Phandelver" in agent.dungeon_offers[0]


def test_entering_the_topmost_room_triggers_its_ability(board):
    """CR 309.4c: the marker moving into a room triggers that room."""
    game = board.game
    game.agents[P0] = DungeonAgent(dungeons=["Tomb of Annihilation"])
    venture(game, P0)
    board.settle()
    assert len(game.stack) == 1
    top = game.objects[game.stack[-1]]
    assert top.ability.text.startswith("Trapped Entry")
    # CR 309.4c: controlled by the dungeon's owner.
    assert top.controller == P0

    board.resolve_stack()
    assert game.player(P0).life == 39
    assert game.player(P1).life == 39


def test_venturing_through_the_effect(board):
    """VENTURE runs the whole procedure, not a counter."""
    game = board.game
    game.agents[P0] = DungeonAgent(dungeons=["Lost Mine of Phandelver"])
    execute(
        Resolution(game=game, source=0, controller=P0),
        (Effect(EffectKind.VENTURE, players=YOU),),
    )
    assert room_name(game) == "Cave Entrance"


def test_the_player_chooses_at_a_fork(board):
    """CR 701.49b: with several arrows, the player picks one."""
    game = board.game
    agent = DungeonAgent(dungeons=["Lost Mine of Phandelver"], rooms=["Mine Tunnels"])
    game.agents[P0] = agent
    venture(game, P0)
    run(board)
    venture(game, P0)

    assert agent.room_offers == [["Goblin Lair", "Mine Tunnels"]]
    assert room_name(game) == "Mine Tunnels"
    run(board)
    assert board.alive(0) == ["Treasure"]


def test_the_defaults_are_deterministic(board):
    """No agent hooks: the first dungeon in the rules' order and the first
    arrow, so a replay chooses the same way."""
    game = board.game
    venture(game, P0)
    venture(game, P0)
    assert current_dungeon(game, P0).card.dungeon.name == "Lost Mine of Phandelver"
    assert room_name(game) == "Goblin Lair"


def test_one_dungeon_at_a_time(board):
    """CR 309.3: venturing again moves the marker, never brings a second."""
    game = board.game
    game.agents[P0] = DungeonAgent(dungeons=["Lost Mine of Phandelver"])
    venture(game, P0)
    venture(game, P0)
    venture(game, P0)
    assert len(dungeons_in_command_zone(game)) == 1


def test_a_room_triggers_only_for_its_own_dungeon(board):
    """Control: P1 entering room 1 of their dungeon does not trigger P0's
    room 1, although both are "the first room"."""
    game = board.game
    game.agents[P0] = DungeonAgent(dungeons=["Tomb of Annihilation"])
    game.agents[P1] = DungeonAgent(dungeons=["Lost Mine of Phandelver"])
    venture(game, P0)
    run(board)
    life = game.player(P1).life

    venture(game, P1)
    board.settle()
    assert len(game.stack) == 1
    assert game.objects[game.stack[-1]].ability.text.startswith("Cave Entrance")
    board.resolve_stack()
    assert game.player(P1).life == life


# ---------------------------------------------------------------------------
# Completing a dungeon (CR 309.6, 309.7, 704.5t)
# ---------------------------------------------------------------------------


def _walk_lost_mine_to_the_bottom(board) -> None:
    game = board.game
    game.agents[P0] = DungeonAgent(
        dungeons=["Lost Mine of Phandelver"], rooms=["Mine Tunnels", "Dark Pool"]
    )
    for _ in range(3):
        venture(game, P0)
        run(board)
    venture(game, P0)
    assert room_name(game) == "Temple of Dumathoin"


def test_the_dungeon_stays_while_its_last_room_ability_waits(board):
    """CR 704.5t: not removed while the bottommost room's ability has
    triggered and not yet left the stack."""
    game = board.game
    _walk_lost_mine_to_the_bottom(board)
    board.settle()
    assert len(game.stack) == 1
    assert current_dungeon(game, P0) is not None
    assert game.player(P0).completed_dungeons == []


def test_the_dungeon_is_completed_once_its_last_room_resolves(board):
    """CR 309.6, 309.7: then it is removed and its owner completes it."""
    game = board.game
    seen = []
    game.observer = lambda _g, event: seen.append(event)
    _walk_lost_mine_to_the_bottom(board)
    hand = len(game.player(P0).hand)
    run(board)

    assert len(game.player(P0).hand) == hand + 1  # Temple of Dumathoin
    assert current_dungeon(game, P0) is None
    assert dungeons_in_command_zone(game) == []
    assert game.player(P0).completed_dungeons == ["Lost Mine of Phandelver"]
    completed = [e for e in seen if e.kind is EventKind.DUNGEON_COMPLETED]
    assert len(completed) == 1 and completed[0].player == P0


def test_a_dungeon_short_of_the_bottom_is_not_completed(board):
    """Control: an empty stack alone does not complete a dungeon."""
    game = board.game
    game.agents[P0] = DungeonAgent(dungeons=["Lost Mine of Phandelver"])
    venture(game, P0)
    run(board)
    venture(game, P0)
    run(board)
    assert current_dungeon(game, P0) is not None
    assert game.player(P0).completed_dungeons == []


def test_venturing_from_the_bottommost_room_completes_and_starts_again(board):
    """CR 701.49c: venture while the last room's ability is still on the
    stack - remove it, complete it, and enter a new dungeon."""
    game = board.game
    _walk_lost_mine_to_the_bottom(board)
    board.settle()
    game.agents[P0].dungeons = ["Tomb of Annihilation"]

    venture(game, P0)

    assert game.player(P0).completed_dungeons == ["Lost Mine of Phandelver"]
    assert room_name(game) == "Trapped Entry"
    assert len(dungeons_in_command_zone(game)) == 1


def test_completed_a_dungeon_condition(board):
    game = board.game
    any_dungeon = Condition(kind=ConditionKind.COMPLETED_DUNGEON)
    the_tomb = Condition(kind=ConditionKind.COMPLETED_DUNGEON, keyword="Tomb of Annihilation")
    assert not holds(game, any_dungeon, controller=P0)

    _walk_lost_mine_to_the_bottom(board)
    run(board)

    assert holds(game, any_dungeon, controller=P0)
    assert not holds(game, any_dungeon, controller=P1)
    assert not holds(game, the_tomb, controller=P0)


# ---------------------------------------------------------------------------
# The initiative (CR 726)
# ---------------------------------------------------------------------------


def test_taking_the_initiative_ventures_into_undercity(board):
    """CR 726.2: taking it triggers a venture into Undercity (701.49d)."""
    game = board.game
    take_initiative(game, P0)
    assert current_dungeon(game, P0) is None  # a trigger, not yet resolved
    board.settle()
    board.resolve_stack()
    board.settle()
    assert room_name(game) == "Secret Entrance"
    assert current_dungeon(game, P0).card.dungeon.name == "Undercity"


def test_taking_it_again_ventures_again(board):
    """CR 726.5: taking the initiative while holding it still triggers."""
    game = board.game
    take_initiative(game, P0)
    run(board)
    take_initiative(game, P0)
    run(board)
    assert room_name(game) in ("Forge", "Lost Well")


def test_the_holder_ventures_at_their_upkeep(board):
    game = board.game
    take_initiative(game, P0)
    run(board)
    game.active_player = P0
    initiative_upkeep(game)
    run(board)
    assert room_name(game) in ("Forge", "Lost Well")


def test_no_upkeep_venture_on_someone_elses_turn(board):
    """Control: the trigger is at the *holder's* upkeep."""
    game = board.game
    take_initiative(game, P0)
    run(board)
    game.active_player = P1
    initiative_upkeep(game)
    assert not game.pending_triggers
    assert room_name(game) == "Secret Entrance"


def test_the_upkeep_turn_step_ventures(board):
    """The upkeep step itself runs the initiative's trigger."""
    from mtgfish.rules.cr500_turn_structure.cr500_turn import TurnOptions, _turn_based_actions
    from mtgfish.rules.kernel.enums import Step

    game = board.game
    take_initiative(game, P0)
    run(board)
    game.active_player = P0
    _turn_based_actions(game, Step.UPKEEP, TurnOptions())
    assert game.pending_triggers


def test_combat_damage_passes_it_once_per_player(board):
    """CR 726.2: one trigger per attacking player, however many creatures."""
    game = board.game
    take_initiative(game, P1)
    run(board)
    combat_damage_to_initiative_holder(game, P1, [P0])
    assert len(game.pending_triggers) == 1
    # It belongs to the player who had the initiative (CR 726.2).
    assert game.pending_triggers[0].controller == P1
    run(board)
    assert initiative_holder(game) == P0
    assert room_name(game, P0) == "Secret Entrance"


def test_the_initiative_passes_when_its_holder_leaves(board):
    """CR 726.4."""
    game = board.game
    take_initiative(game, P1)
    game.active_player = P0
    game.player_loses(P1, LossReason.LIFE)
    assert initiative_holder(game) == P0


def test_has_the_initiative_condition(board):
    game = board.game
    condition = Condition(kind=ConditionKind.HAS_INITIATIVE)
    assert not holds(game, condition, controller=P0)
    take_initiative(game, P0)
    assert holds(game, condition, controller=P0)
    assert not holds(game, condition, controller=P1)


# ---------------------------------------------------------------------------
# The parser reading dungeon and initiative text
# ---------------------------------------------------------------------------


def test_you_take_the_initiative_parses():
    effects = parse_effects(Stream.of("You take the initiative."))
    assert effects is not None
    assert [effect.kind for effect in effects] == [EffectKind.TAKE_INITIATIVE]


def test_whenever_you_complete_a_dungeon_parses():
    trigger = parse_trigger(Stream.of("Whenever you complete a dungeon, draw a card."))
    assert trigger is not None
    assert trigger.event_kinds == frozenset({EventKind.DUNGEON_COMPLETED})


@pytest.mark.parametrize(
    ("text", "kind", "negated"),
    [
        ("you've completed a dungeon", ConditionKind.COMPLETED_DUNGEON, False),
        ("you have the initiative", ConditionKind.HAS_INITIATIVE, False),
        ("you haven't completed Tomb of Annihilation", ConditionKind.COMPLETED_DUNGEON, True),
    ],
)
def test_designation_conditions_parse(text, kind, negated):
    stream = Stream.of(text)
    condition = designation_condition(stream)
    assert condition is not None and stream.done
    if negated:
        assert condition.kind is ConditionKind.NOT
        condition = condition.operands[0]
    assert condition.kind is kind


def test_a_completed_dungeon_trigger_fires(board):
    """"Whenever you complete a dungeon" sees the completion."""
    from mtgfish.rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind

    trigger = parse_trigger(Stream.of("Whenever you complete a dungeon, draw a card."))
    board.scripts.add(
        "Grizzly Bears",
        Ability(
            AbilityKind.TRIGGERED,
            effects=(
                Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1), text="draw a card"),
            ),
            trigger=trigger,
            text="Whenever you complete a dungeon, draw a card.",
        ),
    )
    board.play("Grizzly Bears")
    game = board.game
    _walk_lost_mine_to_the_bottom(board)
    hand = len(game.player(P0).hand)
    run(board)
    board.settle()
    board.resolve_stack()
    # One card from Temple of Dumathoin, one from the completion trigger.
    assert len(game.player(P0).hand) == hand + 2
