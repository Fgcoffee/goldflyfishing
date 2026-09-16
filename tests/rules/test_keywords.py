"""The keyword registry must cover everything that exists.

This is the guard against silently missing a mechanic. Scryfall publishes the
authoritative catalogs; if one of them names a keyword the registry does not,
this fails with that keyword's name. A new set therefore produces a failing
test rather than a card that quietly does less than its text says.
"""

from __future__ import annotations

import pathlib

from mtgfish.rules import keywords
from mtgfish.rules.keywords import Status


def test_every_keyword_ability_is_registered(card_db):
    catalog = card_db.vocabulary("keyword-abilities")
    assert catalog, "keyword catalog missing; rebuild the card database"
    missing = keywords.unregistered(catalog)
    assert not missing, (
        f"{len(missing)} keyword abilities are not in the registry: {missing}. "
        "Add them to mtgfish/rules/keywords.py with an honest status."
    )


def test_every_keyword_action_is_registered(card_db):
    catalog = card_db.vocabulary("keyword-actions")
    assert catalog
    missing = keywords.unregistered(catalog)
    assert not missing, f"unregistered keyword actions: {missing}"


def test_every_ability_word_is_registered(card_db):
    catalog = card_db.vocabulary("ability-words")
    assert catalog
    missing = keywords.unregistered(catalog)
    assert not missing, f"unregistered ability words: {missing}"


def test_registry_has_no_entries_that_do_not_exist(card_db):
    """The registry must not carry inventions of its own.

    An entry with no counterpart in the catalog is either a typo or a keyword
    that has been renamed, and both would make coverage look better than it is.
    """
    known = {
        name.lower()
        for catalog in ("keyword-abilities", "keyword-actions", "ability-words")
        for name in card_db.vocabulary(catalog)
    }
    if not known:
        return
    invented = sorted(
        spec.name
        for registry in (keywords.KEYWORD_ABILITIES, keywords.KEYWORD_ACTIONS)
        for spec in registry.values()
        if spec.key not in known
    )
    assert not invented, f"registry entries with no catalog counterpart: {invented}"


# ---------------------------------------------------------------------------
# The implemented set is what the engine actually enforces
# ---------------------------------------------------------------------------


def test_combat_keywords_are_marked_implemented():
    for name in (
        "Flying",
        "Reach",
        "Menace",
        "Deathtouch",
        "Trample",
        "Lifelink",
        "First strike",
        "Double strike",
        "Vigilance",
        "Defender",
        "Indestructible",
        "Haste",
    ):
        assert keywords.is_implemented(name), f"{name} should be implemented"


def test_protection_is_quality_aware():
    """CR 702.16b: protection from red does not stop a white spell.

    This was a blanket ban once, and the registry said so. Both halves - the
    targeting check in casting.py and the block check in combat.py - now
    consult the quality filter, so the caveat is gone and this is what stops
    it coming back.
    """
    from mtgfish.rules.keyword_impl import KeywordInstance, build
    from mtgfish.rules.query import ObjectFilter
    from mtgfish.rules.enums import Color

    spec = keywords.lookup("Protection")
    assert spec is not None
    assert spec.status is Status.IMPLEMENTED

    red = ObjectFilter(colors_any=Color.RED)
    abilities = build(KeywordInstance("Protection", filter=red))
    assert abilities[0].quality == red


def test_a_keyword_with_no_modelled_effect_is_never_implemented():
    """The registry must not flatter itself.

    Several builders produce an ability of exactly the right shape - the right
    trigger, cost, and zone - wrapped around an effect the engine cannot
    execute. Those must read as PARTIAL, never IMPLEMENTED, and the status is
    derived from what the builder actually produces rather than from a flag
    somebody remembered to set.
    """
    from mtgfish.rules.keyword_impl import KeywordInstance, build

    for name in ("Sneak", "Paradigm", "Power-up"):
        spec = keywords.lookup(name)
        assert spec is not None, name
        assert spec.status is not Status.IMPLEMENTED, name

        abilities = build(KeywordInstance(name, amount=1))
        assert any(
            node.is_unparsed
            for ability in abilities
            for effect in ability.effects
            for node in effect.walk()
        ), f"{name} is marked partial, so something in it must be unmodelled"


def test_morph_is_an_alternative_cost_plus_a_special_action():
    """CR 702.36a: morph is not an activated ability, and that is the point.

    Turning face up uses no stack, so it cannot be responded to. A build that
    produced an activated ability would be subtly wrong in a way no board-state
    test would catch until a removal spell got a response window it should
    never have had.
    """
    from mtgfish.rules.abilities import AbilityKind
    from mtgfish.rules.keyword_impl import KeywordInstance, build

    from mtgfish.rules.costs import Cost, CostComponent, CostKind
    from mtgfish.rules.mana import ManaCost

    morph_cost = Cost((CostComponent(CostKind.MANA, mana=ManaCost.parse("{2}")),))
    abilities = build(KeywordInstance("Morph", cost=morph_cost))
    assert all(a.kind is AbilityKind.STATIC for a in abilities)

    alternatives = [a.alternative_cost for a in abilities if a.alternative_cost]
    assert len(alternatives) == 1
    assert alternatives[0].keyword == "Morph"
    assert alternatives[0].cost.mana_component.mana_value == 3

    assert keywords.lookup("Morph").status is Status.IMPLEMENTED


def test_ability_words_are_known_but_never_implementable():
    """CR 207.2c: ability words have no rules meaning at all."""
    assert keywords.is_known("Landfall")
    assert keywords.lookup("Landfall") is None


def test_summary_reports_real_numbers():
    text = keywords.summary()
    assert "Keyword abilities" in text
    assert "implemented" in text



def test_every_engine_reads_keyword_is_actually_read_by_the_engine():
    """``ENGINE_READS`` is a claim, so it gets checked.

    A keyword on that list is exempt from the "produces nothing" test on the
    grounds that engine code consults its name directly - which is how flying
    works and always will. But the exemption is only true if the code exists.
    Split second sat on the implemented list as a bare name nothing read; this
    is the check that would have caught it.
    """
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "mtgfish"
    skip = {"keyword_impl.py", "keywords.py", "coverage.py", "keyword_actions.py"}
    source = "\n".join(
        path.read_text(encoding="utf8")
        for path in root.rglob("*.py")
        if path.name not in skip
    )

    unread = sorted(
        name
        for name in keywords.ENGINE_READS
        if not re.search(re.escape(name), source, re.IGNORECASE)
    )
    assert not unread, f"claimed to be read by the engine, but nothing reads them: {unread}"


def test_engine_reads_only_names_real_keywords():
    """The same reverse check the rest of the registry gets."""
    unknown = sorted(n for n in keywords.ENGINE_READS if not keywords.is_known(n))
    assert not unknown, f"not real keywords: {unknown}"


def test_a_keyword_that_wraps_card_text_is_graded_on_the_wrapper():
    """Channel, forecast and "max speed" are grammar, not behaviour.

    "[Cost], Discard this card: [effect]" is a shape; the effect inside is
    ordinary card text the parser reads. Grading the builder on whether it can
    invent that text would mark it permanently incomplete for doing its job
    correctly - so it is graded on the shape, and called without a body it
    still reports the gap.
    """
    from mtgfish.rules.effects import Effect, EffectKind
    from mtgfish.rules.keyword_impl import KeywordInstance, build
    from mtgfish.rules.query import YOU, Value

    body = (Effect(EffectKind.DRAW, players=YOU, amount=Value.of(1)),)

    for name in ("Channel", "Forecast", "Max speed"):
        assert keywords.lookup(name).status is Status.IMPLEMENTED, name

        with_body = build(KeywordInstance(name, effects=body))
        assert not any(
            node.is_unparsed
            for ability in with_body
            for effect in ability.effects
            for node in effect.walk()
        ), name

        without = build(KeywordInstance(name))
        assert any(
            node.is_unparsed
            for ability in without
            for effect in ability.effects
            for node in effect.walk()
        ), f"{name} with no body must report the gap"


def test_forecast_is_restricted_to_your_upkeep():
    """CR 702.57a: "Activate only during your upkeep and only once each turn."

    Both halves are real restrictions, and an engine that dropped the timing
    one would let a forecast ability be used at instant speed on an opponent's
    turn.
    """
    from mtgfish.rules.enums import Step
    from mtgfish.rules.keyword_impl import KeywordInstance, build
    from mtgfish.rules.query import ConditionKind

    ability = build(KeywordInstance("Forecast"))[0]
    assert ability.once_each_turn
    condition = ability.activation_condition
    assert condition.kind is ConditionKind.IS_STEP
    assert condition.constraint.value.constant == int(Step.UPKEEP)
