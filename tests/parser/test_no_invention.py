"""The parser must never read a card as saying more than it says.

Full consumption (``test_parser.py``) proves an ability was read *completely*.
It says nothing about whether it was read *correctly*, and a well-formed wrong
answer is the more dangerous of the two failures: an ability that does not
parse is inert and appears in the coverage report, while an ability that parses
into the wrong opcode runs, looks like coverage, and moves the numbers.

Every case here is a construct that was previously read as its own opposite, or
as a strictly stronger card than the one printed. The rule they share: where
the engine cannot express what a card says, the ability fails. It is better to
say a card does not work than to invent a rule for it.
"""

from __future__ import annotations

import pytest

from mtgfish.parser.clauses import parse_effects
from mtgfish.parser.compile import ParsedFace, _parse_line
from mtgfish.parser.explain import explain_ability
from mtgfish.parser.normalize import normalize
from mtgfish.parser.split import split_abilities
from mtgfish.parser.tokens import Stream
from mtgfish.rules.cr500_turn_structure.restrictions import Act
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.kernel.enums import Zone
from mtgfish.rules.kernel.query import ConditionKind


def effects_of(text: str):
    """Every effect of a sentence, or ``None`` if it was not fully read."""
    stream = Stream.of(text)
    parsed = parse_effects(stream)
    if parsed is None or not stream.done:
        return None
    return parsed


def abilities_of(text: str, *, is_permanent: bool = True):
    """A text box compiled to abilities, with the failures it produced."""
    result = ParsedFace()
    out = []
    for line in split_abilities(normalize(text), is_permanent=is_permanent):
        out.extend(_parse_line(line, result, is_permanent=is_permanent))
    return out, result.failures


def nodes(text: str):
    parsed = effects_of(text)
    assert parsed is not None, f"no longer parses: {text}"
    return [node for effect in parsed for node in effect.walk()]


# ---------------------------------------------------------------------------
# Requirements are not prohibitions
# ---------------------------------------------------------------------------


def test_a_creature_that_must_attack_is_not_a_creature_that_cannot():
    """CR 508.1d. This was an inversion, not an omission.

    "Attacks each combat if able" was emitted as an ``Act.ATTACK``
    restriction - the data for "can't attack". A drawback became an
    impossibility, and the parse looked perfect from every angle: the sentence
    was fully consumed, the opcode had an executor, and the restriction was
    well formed.
    """
    found = nodes("This creature attacks each combat if able.")
    assert all(node.kind is not EffectKind.RESTRICTION for node in found), (
        "a requirement must never be emitted as a prohibition"
    )
    granted = [n for n in found if n.kind is EffectKind.GRANT_ABILITY]
    assert granted, "the requirement has to reach the attack declaration somehow"
    # combat.py asks for exactly this keyword, so the spelling is load-bearing.
    assert "Attacks each combat if able" in granted[0].keywords


def test_the_attack_requirement_is_the_one_combat_actually_reads():
    """The engine's own spelling, not a near miss - checked against the
    function that enforces it rather than against a string in this file."""
    import inspect

    from mtgfish.rules.cr500_turn_structure import cr506_combat as combat

    source = inspect.getsource(combat._must_attack)
    granted = next(
        n
        for n in nodes("This creature attacks each combat if able.")
        if n.kind is EffectKind.GRANT_ABILITY
    )
    assert f'"{granted.keywords[0]}"' in source


# ---------------------------------------------------------------------------
# "Only" is a narrowing, and narrowings do not survive being guessed at
# ---------------------------------------------------------------------------


def test_can_block_only_forbids_everything_else(subtype_registry):
    """"Can block only creatures with flying" is "can't block anything
    without flying". It was emitted as "can't block creatures with flying",
    which is the same card upside down: the one thing it could block became
    the one thing it could not.
    """
    found = nodes("This creature can block only creatures with flying.")
    restriction = next(n for n in found if n.kind is EffectKind.RESTRICTION)
    entry = restriction.restrictions[0]
    assert entry.act is Act.BLOCK
    # The counterpart is what the block is *forbidden* against, so it has to
    # be the complement of what the card allows.
    assert entry.counterpart.lacks_keyword == ("flying",)
    assert entry.counterpart.has_keyword == ()


def test_an_uninvertible_block_restriction_is_not_read_at_all():
    """``ObjectFilter`` has no general negation. Where the complement cannot
    be expressed the ability fails, rather than being written the only way
    that fits and meaning the opposite."""
    assert effects_of("This creature can block only creatures you control.") is None


# ---------------------------------------------------------------------------
# Timing qualifiers are not decoration
# ---------------------------------------------------------------------------


def test_an_anthem_confined_to_your_turn_stays_confined():
    """"Creatures you control get +1/+1 during your turn" used to have its
    tail swallowed by a rule that consumed anything after "during". What came
    out was an unconditional anthem - a better card than the one printed, on
    the axis this project measures."""
    found = nodes("Creatures you control get +1/+1 during your turn.")
    conditional = next(n for n in found if n.kind is EffectKind.CONDITIONAL)
    assert conditional.condition.kind is ConditionKind.IS_YOUR_TURN
    assert any(n.kind is EffectKind.MODIFY_PT for n in found)


def test_the_opposite_turn_is_the_negation_and_not_the_same_condition():
    found = nodes("Creatures you control get +1/+1 during each opponent's turn.")
    conditional = next(n for n in found if n.kind is EffectKind.CONDITIONAL)
    assert conditional.condition.kind is ConditionKind.NOT
    assert conditional.condition.operands[0].kind is ConditionKind.IS_YOUR_TURN


def test_a_timing_the_engine_cannot_schedule_fails_the_ability():
    """Seedborn Muse. There is no schedule for a continuous effect confined to
    another player's untap step, and an untap with the timing quietly removed
    is a different card."""
    assert (
        effects_of("Untap all permanents you control during each other player's untap step.")
        is None
    )


# ---------------------------------------------------------------------------
# Activation restrictions (CR 602.5b)
# ---------------------------------------------------------------------------


def test_once_each_turn_reaches_the_limit_the_engine_keeps():
    made, failures = abilities_of(
        "{1}: This creature gets +1/+0 until end of turn. Activate only once each turn."
    )
    assert not failures
    assert made[0].once_each_turn


def test_only_during_your_turn_becomes_a_condition():
    made, failures = abilities_of("{1}, {T}: Draw a card. Activate only during your turn.")
    assert not failures
    assert made[0].activation_condition.kind is ConditionKind.IS_YOUR_TURN


def test_only_if_goes_through_the_ordinary_condition_grammar(subtype_registry):
    made, failures = abilities_of("{2}: Draw a card. Activate only if you control a Goblin.")
    assert not failures
    assert not made[0].activation_condition.is_always


def test_two_restrictions_on_one_line_are_both_read(subtype_registry):
    """"Activate only as a sorcery and only if ..." was read by two different
    readers, each of which stopped where the other began."""
    from mtgfish.rules.kernel.enums import Timing

    made, failures = abilities_of(
        "{2}: Draw a card. Activate only as a sorcery and only if you control a Goblin."
    )
    assert not failures
    assert made[0].timing is Timing.SORCERY
    assert not made[0].activation_condition.is_always


def test_an_unreadable_activation_restriction_fails_the_whole_ability():
    """The direction of error that matters. Ignoring the sentence offers the
    ability every time priority comes round, whatever the card demands, which
    turns a once-a-turn engine into an arbitrarily repeatable one."""
    made, failures = abilities_of(
        "{2}: Draw a card. Activate only during combat before blockers are declared."
    )
    assert made[0].unparsed
    assert failures and failures[0].rule == "activation-restriction"


# ---------------------------------------------------------------------------
# Prevention shields are narrower than "damage"
# ---------------------------------------------------------------------------


def test_a_fog_does_not_stop_a_burn_spell():
    """"Prevent all combat damage" had the word "combat" consumed and
    forgotten, so every Fog in the format also blanked a Lightning Bolt."""
    found = nodes("Prevent all combat damage that would be dealt this turn.")
    prevent = next(n for n in found if n.kind is EffectKind.PREVENT_DAMAGE)
    assert "combat" in prevent.keywords


def test_a_shield_over_one_player_names_that_player():
    found = nodes("Prevent all damage that would be dealt to you this turn.")
    prevent = next(n for n in found if n.kind is EffectKind.PREVENT_DAMAGE)
    assert prevent.players is not None, (
        "a shield with neither subject nor players applies to the whole table"
    )


def test_an_unqualified_shield_stays_unqualified():
    found = nodes("Prevent all damage that would be dealt to target creature this turn.")
    prevent = next(n for n in found if n.kind is EffectKind.PREVENT_DAMAGE)
    assert "combat" not in prevent.keywords


# ---------------------------------------------------------------------------
# Ramp arrives tapped when the card says tapped
# ---------------------------------------------------------------------------


def test_a_searched_land_that_arrives_tapped_says_so():
    """The word was in the step list purely to be swallowed, so every
    Rampant Growth in the format produced an untapped land - a full turn of
    mana the deck does not have."""
    found = nodes(
        "Search your library for a basic land card, put it onto the battlefield "
        "tapped, then shuffle."
    )
    search = next(n for n in found if n.kind is EffectKind.SEARCH_LIBRARY)
    assert search.zone is Zone.BATTLEFIELD
    assert "tapped" in search.keywords


def test_a_searched_land_that_does_not_is_not_tapped_anyway():
    found = nodes(
        "Search your library for a basic land card, put it onto the battlefield, "
        "then shuffle."
    )
    search = next(n for n in found if n.kind is EffectKind.SEARCH_LIBRARY)
    assert "tapped" not in search.keywords


def test_a_tutor_to_hand_is_not_marked_tapped():
    """"Tapped" belongs to the battlefield step, not to the sentence."""
    found = nodes(
        "Search your library for a creature card, reveal it, put it into your "
        "hand, then shuffle."
    )
    search = next(n for n in found if n.kind is EffectKind.SEARCH_LIBRARY)
    assert search.zone is Zone.HAND
    assert "tapped" not in search.keywords


# ---------------------------------------------------------------------------
# Counters come off as well as on
# ---------------------------------------------------------------------------


def test_removing_a_counter_reads(subtype_registry):
    """``REMOVE_COUNTERS`` has always had an executor and the grammar had no
    way to ask for it, although the mirror sentence parsed."""
    found = nodes("Remove a +1/+1 counter from target creature.")
    removal = next(n for n in found if n.kind is EffectKind.REMOVE_COUNTERS)
    assert removal.counter_type == "+1/+1"
    assert removal.amount.constant == 1
    assert removal.is_targeted


def test_removing_all_counters_is_not_guessed_at():
    """The executor takes a count. How many are there is a question about the
    game, not about the card, so the sentence is left unread."""
    assert effects_of("Remove all +1/+1 counters from target creature.") is None


# ---------------------------------------------------------------------------
# A replacement the engine cannot register is not coverage
# ---------------------------------------------------------------------------


def test_a_replacement_with_no_engine_shape_is_reported_as_unread():
    """``replacement._static`` skips a replacement with no kind, so this has
    always been inert. What changes is that it no longer counts as understood:
    a card whose entire text is one "instead" sentence was scored as fully
    read while doing nothing at all."""
    found = nodes("If a creature would die this turn, exile it instead.")
    assert any(node.kind is EffectKind.UNPARSED for node in found)


def test_a_replacement_the_engine_does_know_still_reads(subtype_registry):
    """The honesty above must not cost the shapes that work."""
    found = nodes(
        "If one or more +1/+1 counters would be put on a creature you control, "
        "twice that many are put on it instead."
    )
    replacement = next(n for n in found if n.kind is EffectKind.REPLACEMENT)
    assert replacement.replacement_kind
    assert replacement.multiplier == 2


# ---------------------------------------------------------------------------
# The round-trip has to be trustworthy to be worth running
# ---------------------------------------------------------------------------


def test_the_round_trip_names_the_players_an_effect_hits():
    """It rendered "deals 3 damage to each opponent" as damage to the source.

    A review tool that cries wolf on a correct parse costs exactly as much
    trust as one that stays quiet on a wrong one, and this is the tool the
    project says found most of its real bugs.
    """
    made, _ = abilities_of("This creature deals 3 damage to each opponent.")
    said = explain_ability(made[0])
    assert "each opponent" in said
    assert "this permanent" not in said


def test_the_round_trip_says_where_a_tutor_puts_the_card():
    made, _ = abilities_of(
        "Search your library for a basic land card, put it onto the battlefield "
        "tapped, then shuffle.",
        is_permanent=False,
    )
    said = explain_ability(made[0])
    assert "battlefield" in said and "tapped" in said


def test_the_round_trip_shows_an_activation_restriction():
    made, _ = abilities_of("{1}, {T}: Draw a card. Activate only during your turn.")
    assert "only if" in explain_ability(made[0])


def test_the_round_trip_does_not_print_the_all_damage_sentinel_as_a_number():
    """-1 is how the executor spells "all". Printed as itself it read as a
    card that prevents minus one damage."""
    made, _ = abilities_of(
        "Prevent all combat damage that would be dealt this turn.", is_permanent=False
    )
    said = explain_ability(made[0])
    assert "-1" not in said
    assert "all combat damage" in said


# ---------------------------------------------------------------------------
# A token is what the sentence says it is
# ---------------------------------------------------------------------------


def test_a_token_keeps_the_ability_it_was_printed_with(subtype_registry):
    """"Create a 1/1 white Soldier creature token with flying."

    The noun reader takes "with flying" as a constraint and files it under
    ``has_keyword``; nothing carried it across to the token, so every token
    printed with an ability was created vanilla - and the sentence was fully
    consumed while it happened, so nothing downstream could tell.
    """
    found = nodes("Create a 1/1 white Soldier creature token with flying.")
    token = next(n for n in found if n.kind is EffectKind.CREATE_TOKEN).token
    assert token.keywords == ("flying",)


def test_several_token_keywords_all_survive(subtype_registry):
    found = nodes(
        "Create a 1/1 white Soldier creature token with vigilance and lifelink."
    )
    token = next(n for n in found if n.kind is EffectKind.CREATE_TOKEN).token
    assert set(token.keywords) == {"vigilance", "lifelink"}


def test_an_artifact_token_is_not_a_zero_zero_creature(subtype_registry):
    """"Create a Treasure token" names no card type, and creature was the
    default. A 0/0 creature dies to state-based actions the moment it arrives,
    so one of the most-played effects in the format made nothing at all."""
    from mtgfish.rules.kernel.enums import CardType

    found = nodes("Create a Treasure token.")
    token = next(n for n in found if n.kind is EffectKind.CREATE_TOKEN).token
    assert token.types & CardType.ARTIFACT
    assert not token.types & CardType.CREATURE


def test_a_creature_token_with_no_stated_size_is_not_invented(subtype_registry):
    """The same 0/0 from the other direction. If the sentence does not say how
    big the creature is, the parser does not decide for it."""
    assert effects_of("Create a Soldier creature token.") is None


def test_the_round_trip_does_not_print_a_treasure_as_a_creature(subtype_registry):
    made, _ = abilities_of("Create a Treasure token.", is_permanent=False)
    said = explain_ability(made[0])
    assert "0/0" not in said
    assert "Treasure" in said


# ---------------------------------------------------------------------------
# An ability nothing can trigger is not coverage either
# ---------------------------------------------------------------------------


def test_an_ability_gated_by_an_unreadable_condition_is_not_understood():
    """Ward. ``keyword_impl`` builds the trigger and marks the "unless that
    player pays" half ``UNPARSED``, which is the honest thing for it to do -
    the condition never holds and the ability never fires.

    What was not honest was the score: only effects were checked for
    unreadability, so a Ward creature was counted as completely read while
    warding nothing at all. The engine gap is real and stays; it is now
    visible.
    """
    from mtgfish.parser.compile import understood

    made, failures = abilities_of("Ward {2}")
    assert not failures, "the keyword itself reads fine - that is the point"
    assert made and not any(understood(ability) for ability in made)


def test_a_readable_keyword_is_still_understood():
    """The check must not simply fail everything with a condition on it."""
    from mtgfish.parser.compile import understood

    made, _ = abilities_of("Flying")
    assert all(understood(ability) for ability in made)


def test_an_unreadable_condition_buried_in_a_conjunction_still_counts():
    """NOT, AND and OR carry their operands underneath, and an unreadable one
    inside a conjunction is exactly as inert as one on top."""
    from mtgfish.rules.kernel.query import Condition, ConditionKind

    buried = Condition(
        kind=ConditionKind.AND,
        operands=(
            Condition(kind=ConditionKind.IS_YOUR_TURN),
            Condition(kind=ConditionKind.UNPARSED),
        ),
    )
    assert buried.is_unparsed
    assert not Condition(kind=ConditionKind.IS_YOUR_TURN).is_unparsed


def test_a_cost_reduction_is_not_rendered_as_an_increase():
    """Affinity is a PRODUCT of -1 and a count, and Value.__str__ dropped the
    -1 - so a correct cost *reduction* read as a tax. The round-trip accusing
    a working card costs as much as it missing a broken one."""
    made, _ = abilities_of("Affinity for artifacts")
    said = explain_ability(made[0])
    assert "-1 times" in said


# ---------------------------------------------------------------------------
# A static ability's condition belongs to the ability
# ---------------------------------------------------------------------------


def test_a_static_condition_is_lifted_onto_the_ability(subtype_registry):
    """Where the condition sits decides whether it is checked at all.

    A CONDITIONAL is the right shape for a resolving spell and the wrong one
    for a static ability: ``layers._continuous_parts`` walks SEQUENCEs and not
    CONDITIONALs, so an anthem wrapped in one was dropped whole. The engine's
    own mechanism for this is ``Ability.static_condition``, re-checked every
    time characteristics are recomputed.
    """
    made, failures = abilities_of(
        "Creatures you control get +1/+1 as long as you control a Goblin."
    )
    assert not failures
    ability = made[0]
    assert not ability.static_condition.is_always
    assert [effect.kind for effect in ability.effects] == [EffectKind.MODIFY_PT]


def test_both_orders_of_the_same_sentence_agree(subtype_registry):
    """Oracle text puts the condition on either side and means the same card."""
    leading, _ = abilities_of(
        "As long as you control a Goblin, creatures you control get +1/+1."
    )
    trailing, _ = abilities_of(
        "Creatures you control get +1/+1 as long as you control a Goblin."
    )
    assert str(leading[0].static_condition) == str(trailing[0].static_condition)
    assert leading[0].effects[0].kind is trailing[0].effects[0].kind


def test_an_unconditional_static_ability_gains_no_condition():
    made, _ = abilities_of("Creatures you control get +1/+1.")
    assert made[0].static_condition.is_always


def test_a_spell_keeps_its_conditional_shape(subtype_registry):
    """Only static abilities are lifted. A spell resolves once, and its
    CONDITIONAL is exactly the right opcode for that."""
    made, _ = abilities_of(
        "If you control a Goblin, draw a card.", is_permanent=False
    )
    assert made[0].effects[0].kind is EffectKind.CONDITIONAL
