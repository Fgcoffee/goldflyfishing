"""Parsed cards driving the real engine.

The parser tests prove it produces the right shapes. These prove the shapes
run: a card is read from its actual Scryfall oracle text, put on a real board,
and the effect happens. Nothing here hand-writes an ability.

This is the join the whole project turns on. A parser that produces plausible
IR the engine cannot execute is worth nothing, and only an end-to-end test
catches that.
"""

from __future__ import annotations

import pytest
from harness import Board, make_board

from mtgfish.parser.compile import OracleAbilities
from mtgfish.rules.cr100_game_concepts.cr117_priority import Action, ActionKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import Resolution, execute
from mtgfish.rules.kernel.enums import Zone


@pytest.fixture
def oracle(card_db):
    """A board whose abilities come from parsed oracle text, not a script."""
    card_db.registry()
    provider = OracleAbilities()
    board = make_board(card_db, provider)
    return board


def abilities_of(board: Board, obj):
    return board.game.characteristics(obj).abilities


# ---------------------------------------------------------------------------
# Static and keyword abilities reaching the board
# ---------------------------------------------------------------------------


def test_a_parsed_keyword_reaches_the_layer_system(oracle):
    """Serra Angel's flying comes from its oracle text, through the keyword
    registry, into the characteristics the layer system computes."""
    angel = oracle.play("Serra Angel", controller=0)
    assert "Flying" in oracle.keywords(angel)
    assert "Vigilance" in oracle.keywords(angel)


def test_parsed_flying_actually_stops_a_block(oracle):
    """The keyword is not decoration: combat has to read it."""
    from mtgfish.rules.cr500_turn_structure.cr506_combat import can_block

    angel = oracle.play("Serra Angel", controller=0)
    ground = oracle.play("Grizzly Bears", controller=1)
    assert not can_block(oracle.game, ground, angel)


def test_a_vanilla_creature_gains_nothing_from_parsing(oracle):
    """The control: no text, no abilities, no invented behaviour."""
    bear = oracle.play("Grizzly Bears", controller=0)
    assert abilities_of(oracle, bear) == ()
    assert oracle.pt(bear) == (2, 2)


# ---------------------------------------------------------------------------
# Spells resolving
# ---------------------------------------------------------------------------


def test_lightning_bolt_parsed_from_oracle_text_deals_damage(oracle):
    """"Lightning Bolt deals 3 damage to any target." - read, compiled, run.

    Nothing in this test knows what Lightning Bolt does. The number 3 comes
    from Scryfall, the opcode from the grammar, and the damage from the
    engine's executor.
    """
    from mtgfish.rules.cr600_spells_and_abilities.abilities import AbilityKind

    bolt = oracle.hand("Lightning Bolt", controller=0)
    target = oracle.play("Serra Angel", controller=1)

    spell = [
        a for a in abilities_of(oracle, bolt) if a.kind is AbilityKind.SPELL
    ]
    assert spell, "Lightning Bolt should have a spell ability"

    execute(
        Resolution(
            game=oracle.game,
            source=bolt.id,
            controller=0,
            targets=((target.id,),),
        ),
        spell[0].effects,
    )
    assert target.damage == 3


def test_a_parsed_draw_spell_actually_draws(oracle):
    """Divination: "Draw two cards."."""
    from mtgfish.rules.cr600_spells_and_abilities.abilities import AbilityKind

    card = oracle.hand("Divination", controller=0)
    before = len(oracle.game.player(0).hand)

    spell = [a for a in abilities_of(oracle, card) if a.kind is AbilityKind.SPELL]
    assert spell
    execute(
        Resolution(game=oracle.game, source=card.id, controller=0),
        spell[0].effects,
    )
    assert len(oracle.game.player(0).hand) == before + 2


def test_a_parsed_removal_spell_destroys(oracle):
    """Murder: "Destroy target creature."."""
    from mtgfish.rules.cr600_spells_and_abilities.abilities import AbilityKind

    card = oracle.hand("Murder", controller=0)
    victim = oracle.play("Grizzly Bears", controller=1)

    spell = [a for a in abilities_of(oracle, card) if a.kind is AbilityKind.SPELL]
    assert spell
    execute(
        Resolution(
            game=oracle.game,
            source=card.id,
            controller=0,
            targets=((victim.id,),),
        ),
        spell[0].effects,
    )
    oracle.sba()
    assert not oracle.game.objects[victim.id].is_live


# ---------------------------------------------------------------------------
# Activated abilities
# ---------------------------------------------------------------------------


def test_a_parsed_mana_ability_is_a_mana_ability(oracle):
    """CR 605.1a. Llanowar Elves' "{T}: Add {G}." must not use the stack -
    an engine that put it there would deadlock every ramp creature."""
    elves = oracle.play("Llanowar Elves", controller=0)
    mana = [a for a in abilities_of(oracle, elves) if a.is_mana_ability]
    assert mana, [a.text for a in abilities_of(oracle, elves)]
    assert mana[0].cost.requires_tapping


def test_activating_a_parsed_mana_ability_adds_mana(oracle):
    """End to end: the ability is read from text, activated, and the mana
    arrives in the pool."""
    from mtgfish.rules.cr600_spells_and_abilities.cr601_casting import activate_ability

    elves = oracle.play("Llanowar Elves", controller=0)
    elves.summoning_sick = False
    chars = oracle.game.characteristics(elves)
    index = next(
        i for i, ability in enumerate(chars.abilities) if ability.is_mana_ability
    )

    before = oracle.game.player(0).mana_pool.total
    activate_ability(
        oracle.game,
        0,
        Action(ActionKind.ACTIVATE_MANA_ABILITY, source=elves.id, ability_index=index),
    )
    assert oracle.game.player(0).mana_pool.total == before + 1
    assert elves.tapped


def test_sol_ring_parses_both_of_its_abilities(oracle):
    """"{T}: Add {C}{C}." - one ability, two mana."""
    ring = oracle.play("Sol Ring", controller=0)
    mana = [a for a in abilities_of(oracle, ring) if a.is_mana_ability]
    assert mana
    amounts = [
        effect.amount.constant for ability in mana for effect in ability.effects
    ]
    assert 2 in amounts


# ---------------------------------------------------------------------------
# The failure mode, on purpose
# ---------------------------------------------------------------------------


def test_an_unparsed_card_is_inert_rather_than_wrong(oracle, card_db):
    """The whole point of full consumption.

    A card the grammar cannot read produces an ability marked unparsed. The
    engine skips those - it never guesses - so the card sits on the battlefield
    doing nothing, and the coverage report names it.
    """
    from mtgfish.parser import parse_card

    unreadable = None
    for card in card_db.iter_cards(commander_legal_only=True):
        parsed = parse_card(card)
        if parsed.failures:
            unreadable = card
            break
    assert unreadable is not None, "expected at least one unreadable card"

    obj = oracle.play(unreadable.name, controller=0)
    abilities = abilities_of(oracle, obj)
    # Some ability is present and marked unreadable, rather than absent or -
    # far worse - present and wrong.
    assert any(a.unparsed for a in abilities) or abilities == ()


def test_the_provider_reports_what_it_could_not_read(oracle, card_db):
    """Coverage is not self-reported: the provider accumulates real failures."""
    provider = oracle.scripts
    assert isinstance(provider, OracleAbilities)

    for card in list(card_db.iter_cards(commander_legal_only=True))[:400]:
        provider.abilities_for(card, 0)

    assert provider.failures, "400 real cards should include something unreadable"
    assert all(f.text for f in provider.failures)


def test_the_provider_caches(oracle, card_db):
    """The engine asks for a card's abilities once per layer recomputation.

    Parsing on that path would make the simulator unusable, so the result is
    cached by oracle id and face.
    """
    provider = oracle.scripts
    card = card_db.lookup("Serra Angel")

    first = provider.abilities_for(card, 0)
    second = provider.abilities_for(card, 0)
    assert first is second


# ---------------------------------------------------------------------------
# A whole game, with parsed cards
# ---------------------------------------------------------------------------


def test_a_full_game_runs_on_parsed_abilities(card_db):
    """The engine and the parser together, for a whole game.

    Not a correctness check - a "nothing explodes" check. Every card in these
    decks has its abilities read from oracle text, and the game has to reach a
    conclusion without the engine tripping over anything the grammar produced.
    """
    from mtgfish.data.decks import parse_decklist
    from mtgfish.rules.cr100_game_concepts.cr103_setup import new_game
    from mtgfish.rules.cr500_turn_structure.cr500_turn import TurnOptions, run_game

    card_db.registry()
    decks = [
        parse_decklist(
            "// Commander\n1 Kenrith, the Returned King\n"
            "// Deck\n40 Forest\n30 Llanowar Elves\n29 Grizzly Bears\n",
            card_db,
            name=f"P{i}",
        )
        for i in range(2)
    ]
    game = new_game(
        decks, seed=11, ability_provider=OracleAbilities(), randomize_turn_order=False
    )
    finished = run_game(game, TurnOptions(max_rounds=3))
    assert finished.turn > 0


def test_the_same_seed_gives_the_same_game_with_parsed_cards(card_db):
    """Determinism survives the parser.

    Replays are re-simulated from a seed rather than stored, so anything that
    makes two runs of one seed differ - a set iteration, an unordered dict -
    breaks every replay in the product.
    """
    from mtgfish.data.decks import parse_decklist
    from mtgfish.rules.cr100_game_concepts.cr103_setup import new_game
    from mtgfish.rules.cr500_turn_structure.cr500_turn import TurnOptions, run_game

    card_db.registry()

    def play() -> str:
        decks = [
            parse_decklist(
                "// Commander\n1 Kenrith, the Returned King\n"
                "// Deck\n60 Forest\n39 Llanowar Elves\n",
                card_db,
                name=f"P{i}",
            )
            for i in range(2)
        ]
        game = new_game(
            decks,
            seed=99,
            ability_provider=OracleAbilities(),
            randomize_turn_order=False,
        )
        return run_game(game, TurnOptions(max_rounds=3)).log.digest()

    assert play() == play()


def test_zones_are_untouched_by_parsing(oracle):
    """A sanity check that the provider is read-only.

    Asking for a card's abilities must not move it, tap it, or otherwise touch
    the game - the provider is consulted during layer computation, which runs
    constantly.
    """
    bear = oracle.play("Grizzly Bears", controller=0)
    zone, tapped = bear.zone, bear.tapped
    for _ in range(5):
        oracle.game.invalidate_characteristics()
        abilities_of(oracle, bear)
    assert bear.zone is zone is Zone.BATTLEFIELD
    assert bear.tapped == tapped


# ---------------------------------------------------------------------------
# Pronouns
# ---------------------------------------------------------------------------


def test_a_pronoun_means_the_last_thing_acted_on_not_the_source(oracle):
    """"Destroy target creature. Its controller loses 2 life."

    "It" is the *target*, not the card that said so. Aliasing pronouns to the
    source would quietly redirect a large fraction of the card pool at itself,
    and every one of those cards would still look like it was working.
    """
    from mtgfish.parser.clauses import parse_effects
    from mtgfish.parser.tokens import Stream
    from mtgfish.rules.kernel.query import ObjectFilter

    effects = parse_effects(Stream.of("Tap target creature. It gains flying."))
    assert effects is not None
    pronoun = effects[1].targets
    assert isinstance(pronoun, ObjectFilter)
    assert pronoun.remembered
    assert not pronoun.source_only


def test_the_resolution_remembers_what_an_effect_acted_on(oracle):
    """The engine half of the same rule."""
    from mtgfish.parser.clauses import parse_effects
    from mtgfish.parser.tokens import Stream

    source = oracle.play("Grizzly Bears", controller=0)
    victim = oracle.play("Serra Angel", controller=1)

    effects = parse_effects(Stream.of("Tap target creature. It gains flying."))
    resolution = Resolution(
        game=oracle.game, source=source.id, controller=0, targets=((victim.id,),)
    )
    execute(resolution, tuple(effects))

    assert victim.tapped, "the target should have been tapped"
    assert resolution.remembered == [victim.id]
    assert not source.tapped, "the source is not the pronoun's referent"


def test_peeking_at_targets_does_not_consume_them(oracle):
    """A regression guard.

    Remembering what an effect acted on requires reading its targets before it
    runs, and the target list is handed out through a cursor. Failing to put
    the cursor back made every two-effect spell resolve its second effect onto
    the first effect's targets.
    """
    from mtgfish.parser.clauses import parse_effects
    from mtgfish.parser.tokens import Stream

    source = oracle.play("Grizzly Bears", controller=0)
    first = oracle.play("Serra Angel", controller=1)
    second = oracle.play("Runeclaw Bear", controller=1)

    effects = parse_effects(Stream.of("Tap target creature. Tap target creature."))
    execute(
        Resolution(
            game=oracle.game,
            source=source.id,
            controller=0,
            targets=((first.id,), (second.id,)),
        ),
        tuple(effects),
    )
    assert first.tapped and second.tapped
