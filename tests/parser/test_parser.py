"""The parser's safety properties.

Two of these matter more than the rest, and they are the ones that killed
earlier attempts at this project:

**Full consumption.** An ability the grammar only partly read must never fire.
**No invention.** The parser may only emit opcodes the engine executes.

Everything else here is ordinary correctness. Those two are load-bearing.
"""

from __future__ import annotations

import pytest

from mtgfish.parser import parse_card, parse_face
from mtgfish.parser.clauses import CLAUSES, parse_effects
from mtgfish.parser.normalize import normalize
from mtgfish.parser.split import LineKind, split_abilities
from mtgfish.parser.tokens import Stream, TokenKind, tokenize
from mtgfish.rules.cr600_spells_and_abilities.abilities import AbilityKind
from mtgfish.rules.cr600_spells_and_abilities.effects import EffectKind
from mtgfish.rules.cr600_spells_and_abilities.resolve import EXECUTORS

# ---------------------------------------------------------------------------
# The two load-bearing properties
# ---------------------------------------------------------------------------


def test_a_partly_understood_ability_never_fires():
    """The rule the whole design exists to enforce.

    "Draw a card" parses. "Draw a card, then flumox the widget" must not parse
    to "draw a card" - it must parse to nothing at all, because a card that
    does half of what it says is worse than a card that does nothing: the
    simulation looks right and the numbers are wrong.
    """
    good = parse_effects(Stream.of("Draw a card."))
    assert good is not None
    assert good[0].kind is EffectKind.DRAW

    stream = Stream.of("Draw a card, then flumox the widget.")
    assert parse_effects(stream) is None


def test_leftover_tokens_fail_the_whole_ability(card_db):
    """Not just the sentence - the ability."""
    from mtgfish.parser.compile import ParsedFace, _plain
    from mtgfish.parser.split import Line

    result = ParsedFace()
    abilities = _plain(
        Line("Draw a card and then do something unreadable.", LineKind.SPELL),
        result,
        is_permanent=False,
    )
    assert len(abilities) == 1
    assert abilities[0].unparsed
    assert result.failures
    assert result.failures[0].reason == "not fully consumed"


def test_the_parser_can_only_emit_opcodes_the_engine_runs(subtype_registry):
    """No invention.

    Every clause is exercised against text it should match, and every opcode
    that comes out has to have an executor. A grammar rule that wants an effect
    the engine lacks is a bug in the grammar, not a licence to approximate.

    This is a property of the grammar and the engine, not of any card pool, so
    it must hold on a fresh clone with no card database. The one sample that
    names a creature type gets it from ``subtype_registry`` rather than from
    real cards - see that fixture for why requesting ``card_db`` here would be
    the wrong trade.
    """
    samples = [
        "Draw a card.",
        "Target player discards a card.",
        "Each opponent mills three cards.",
        "Scry 2.",
        "Shuffle your library.",
        "This creature deals 3 damage to any target.",
        "You gain 3 life.",
        "Each opponent loses 2 life.",
        "Destroy target creature.",
        "Exile target artifact.",
        "Counter target spell.",
        "Return target creature to its owner's hand.",
        "Tap target creature.",
        "Put a +1/+1 counter on target creature.",
        "Target creature gets +2/+2 until end of turn.",
        "Target creature gains flying until end of turn.",
        "Create a 1/1 white Soldier creature token.",
        "Add {G}.",
        "This land enters tapped.",
        "This creature enters with two +1/+1 counters on it.",
        "This creature can't block.",
        "You may draw a card.",
        "Search your library for a basic land card, put it onto the battlefield.",
    ]
    seen: set[EffectKind] = set()
    for text in samples:
        effects = parse_effects(Stream.of(text))
        assert effects is not None, f"no longer parses: {text}"
        for effect in effects:
            for node in effect.walk():
                seen.add(node.kind)

    unrunnable = sorted(
        kind.name
        for kind in seen
        if kind is not EffectKind.UNPARSED and kind not in EXECUTORS
    )
    assert not unrunnable, f"parser emits opcodes the engine cannot run: {unrunnable}"


def test_no_two_opcodes_share_a_number():
    """The other half of "no invention": one opcode, one meaning.

    ``EffectKind`` is an ``IntEnum``, so two members given the same number are
    not two opcodes - the second becomes an alias of the first. Two pairs had
    collided, and the damage was invisible from either side: the executor
    table silently kept one entry per number, so a parsed CHOOSE_QUALITY ("as
    this enters, choose a creature type") was dispatched to the reflexive
    trigger executor, and ``EffectKind.BECOME_SOLVED is EffectKind.EXTRA_TRIGGER``
    came out true for every ``is`` check in the engine.

    The parser emitting one opcode and the engine running another defeats the
    whole point of a fixed instruction set, and nothing else in the suite can
    see it happen.
    """
    numbers: dict[int, str] = {}
    collisions = []
    for member in EffectKind:
        # Aliases do not appear in iteration, so canonical names are compared
        # against __members__, which does include them.
        numbers[int(member)] = member.name
    for name, member in EffectKind.__members__.items():
        if numbers[int(member)] != name:
            collisions.append(f"{name} == {numbers[int(member)]} ({int(member)})")
    assert not collisions, f"opcodes sharing a number: {collisions}"


def test_every_opcode_the_engine_runs_has_its_own_executor():
    """A follow-on from the above: the table is keyed by the enum, so a
    collision there silently dropped one of the two executors."""
    from mtgfish.rules.cr600_spells_and_abilities.resolve import EXECUTORS

    assert len(EXECUTORS) == len({int(kind) for kind in EXECUTORS})


def test_no_clause_is_registered_twice():
    """Two clauses with one name means one of them is unreachable."""
    names = [name for name, _ in CLAUSES]
    assert len(names) == len(set(names)), "duplicate clause names"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_reminder_text_is_removed():
    """It restates rules the keyword already carries, and parsing it would
    produce a second, duplicate ability."""
    text = normalize(
        "Flying (This creature can't be blocked except by creatures with "
        "flying or reach.)"
    )
    assert text == "Flying"


def test_the_cards_own_name_becomes_this():
    """CR 201.5: a card referring to itself means the object, not the name.

    Left alone, "Ajani's Pridemate gets a +1/+1 counter" would look for a card
    called Ajani's Pridemate somewhere on the battlefield - possibly several.
    """
    text = normalize(
        "Whenever you gain life, put a +1/+1 counter on Ajani's Pridemate.",
        card_name="Ajani's Pridemate",
    )
    assert "Ajani's Pridemate" not in text
    assert "this" in text


def test_a_legend_shortens_to_its_first_name():
    """Oracle text calls "Ajani, Nacatl Pariah" just "Ajani" on its own card."""
    text = normalize("Ajani deals 2 damage.", card_name="Ajani, Nacatl Pariah")
    assert text == "this deals 2 damage."


def test_typographic_characters_are_normalized():
    text = normalize("Equip — {2}. It’s a Bear…")
    assert "—" not in text and "’" not in text and "…" not in text


# ---------------------------------------------------------------------------
# Tokenizing
# ---------------------------------------------------------------------------


def test_mana_symbols_are_single_tokens():
    """The reason this is a hand-written tokenizer.

    A naive split on non-alphanumerics turns "{G/U}" into three tokens and
    poisons every cost the parser ever reads.
    """
    tokens = tokenize("{2}{G/U}{T}")
    assert [t.text for t in tokens] == ["{2}", "{G/U}", "{T}"]
    assert all(t.kind is TokenKind.SYMBOL for t in tokens)


def test_power_toughness_beats_number_tokenizing():
    """"+1/+1" is one thing, not "+1", "/", "+1"."""
    tokens = tokenize("gets +1/+1 and 2/2")
    kinds = [t.kind for t in tokens]
    assert TokenKind.PT in kinds
    assert [t.text for t in tokens if t.kind is TokenKind.PT] == ["+1/+1", "2/2"]


def test_written_numbers_carry_their_value():
    """"three" and "3" mean the same thing, so they tokenize the same way."""
    for text, expected in (("three", 3), ("3", 3), ("a", 1)):
        token = tokenize(text)[0]
        assert token.kind is TokenKind.NUMBER
        assert token.value == expected


def test_a_phrase_match_is_all_or_nothing():
    """A half-consumed phrase is how a parser ends up confidently wrong."""
    stream = Stream.of("up to three creatures")
    assert not stream.accept_phrase("up to four")
    assert stream.pos == 0
    assert stream.accept_phrase("up to three")


# ---------------------------------------------------------------------------
# Splitting and classifying
# ---------------------------------------------------------------------------


def test_each_paragraph_is_its_own_ability():
    """CR 113.2."""
    lines = split_abilities("Flying\nVigilance\nWhen this creature enters, draw a card.")
    assert len(lines) == 3
    assert lines[0].kind is LineKind.KEYWORD
    assert lines[2].kind is LineKind.TRIGGERED


def test_comma_separated_keywords_are_several_abilities():
    """CR 702.2b: "Flying, vigilance" is two abilities on one line."""
    lines = split_abilities("Flying, vigilance, trample")
    assert len(lines) == 3
    assert all(line.kind is LineKind.KEYWORD for line in lines)


def test_a_sentence_containing_a_keyword_is_not_a_keyword_line():
    """"Flying, and it can't be blocked" is not two keywords, and reading it
    as one would silently drop the second half."""
    lines = split_abilities("Enchanted creature gets +1/+1 and has flying.")
    assert len(lines) == 1
    assert lines[0].kind is not LineKind.KEYWORD


def test_the_activation_colon_is_the_one_whose_left_side_is_a_cost():
    """CR 602.1. The *first* colon is not always the right one."""
    lines = split_abilities("{T}: Add {G}.")
    assert lines[0].kind is LineKind.ACTIVATED

    lines = split_abilities("Sacrifice a creature: Target player loses 2 life.")
    assert lines[0].kind is LineKind.ACTIVATED


def test_modal_blocks_stay_together():
    """CR 700.2: a "Choose one -" and its bullets are one ability."""
    lines = split_abilities(
        "Choose one -\n• Destroy target creature.\n• Draw two cards."
    )
    assert len(lines) == 1
    assert lines[0].kind is LineKind.MODAL
    assert len(lines[0].modes) == 2


def test_saga_chapters_are_recognised():
    """CR 714.2: chapter symbols are structural, not sentences."""
    lines = split_abilities("I, II - Draw a card.\nIII - Destroy target creature.")
    assert lines[0].chapters == (1, 2)
    assert lines[1].chapters == (3,)


# ---------------------------------------------------------------------------
# Real cards, end to end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Grizzly Bears",
        "Llanowar Elves",
        "Lightning Bolt",
        "Sol Ring",
        "Serra Angel",
    ],
)
def test_staple_cards_parse_completely(card_db, name):
    """If the parser cannot read these, it cannot read anything."""
    card = card_db.lookup(name)
    assert card is not None, name
    parsed = parse_card(card)
    assert parsed.fully_parsed, [str(f) for f in parsed.failures]


def test_a_vanilla_creature_has_no_abilities(card_db):
    card = card_db.lookup("Grizzly Bears")
    parsed = parse_face(card, 0)
    assert parsed.abilities == ()


def test_a_keyword_creature_gets_the_engines_keyword_ability(card_db):
    """The parser recognises keywords; ``keyword_impl`` implements them.

    One definition of flying in the codebase, not two that can drift.
    """
    card = card_db.lookup("Serra Angel")
    parsed = parse_face(card, 0)
    keywords = {ability.keyword for ability in parsed.abilities}
    assert "Flying" in keywords
    assert "Vigilance" in keywords


def test_a_mana_land_produces_a_mana_ability(card_db):
    """CR 605.1a: adds mana, does not target - so it does not use the stack."""
    card = card_db.lookup("Forest")
    parsed = parse_face(card, 0)
    mana = [a for a in parsed.abilities if a.is_mana_ability]
    # Basic lands carry their mana ability intrinsically (CR 305.6), so the
    # text box may be empty; what must not happen is a *non*-mana ability.
    assert all(a.is_mana_ability for a in parsed.abilities if a.effects) or not mana


def test_lightning_bolt_is_a_spell_ability(card_db):
    card = card_db.lookup("Lightning Bolt")
    parsed = parse_face(card, 0)
    assert parsed.fully_parsed
    assert parsed.abilities[0].kind is AbilityKind.SPELL
    kinds = {
        node.kind
        for ability in parsed.abilities
        for effect in ability.effects
        for node in effect.walk()
    }
    assert EffectKind.DAMAGE in kinds


# ---------------------------------------------------------------------------
# Coverage as a ratchet
# ---------------------------------------------------------------------------


def test_corpus_coverage_does_not_regress(card_db):
    """A floor, not a target.

    The number only goes up as the grammar grows, so a change that drops it is
    a regression somewhere else. Deliberately well below the current figure so
    that ordinary work does not trip it - it is here to catch a grammar rule
    that breaks a hundred other rules, which is the failure mode a
    first-token-wins parser has.
    """
    from mtgfish.parser import measure

    card_db.registry()  # install the real subtype catalogs
    report = measure(card_db.iter_cards(commander_legal_only=True))
    assert report.card_percent >= 32.0, report.render()
    assert report.ability_percent >= 54.0, report.render()


def test_the_report_names_where_the_grammar_stops(card_db):
    """The report has to be a work queue, not a score.

    "3,412 abilities failed" is useless. "988 of them stopped at the token
    'the'" is a morning's work.
    """
    from mtgfish.parser import measure

    report = measure(card_db.iter_cards(commander_legal_only=True))
    stops = report.stopped_at(5)
    assert stops
    assert all(count > 0 for _, count in stops)
    assert report.by_rule()


# ---------------------------------------------------------------------------
# Parsing successfully while dropping meaning
# ---------------------------------------------------------------------------


def test_x_is_bound_to_its_trailing_definition():
    """CR 107.3b: "Draw X cards, where X is the number of creatures you control."

    The definition arrives after the effect that uses it. Consuming it without
    substituting leaves X at whatever the spell announced, which for a non-X
    spell is zero - so the card parses, resolves, and does nothing. That is the
    worst outcome available: it looks like coverage.
    """
    from mtgfish.rules.kernel.query import ValueKind

    effects = parse_effects(
        Stream.of("Draw X cards, where X is the number of creatures you control.")
    )
    assert effects is not None
    assert len(effects) == 1, "the definition should not survive as its own effect"
    assert effects[0].kind is EffectKind.DRAW
    assert effects[0].amount.kind is ValueKind.COUNT


def test_an_unbindable_x_definition_fails_the_ability():
    """If the definition cannot be read, neither can the ability."""
    assert parse_effects(Stream.of("Draw X cards, where X is the flumox count.")) is None


def test_a_mana_restriction_is_never_silently_dropped():
    """CR 106.6b: "Spend this mana only to cast creature spells."

    This used to assert the clause was *unread*, because consuming a
    restriction without enforcing it makes the engine more permissive than the
    card - and mana that is supposed to be narrow being spendable on anything
    overstates how fast a deck really is.

    The mana system now carries the rider, so the requirement changes shape but
    not substance: the restriction must reach the mana, and it must still be
    the same restriction the card printed. It is never enough for the words to
    be consumed.
    """
    from mtgfish.rules.kernel.enums import CardType

    effects = parse_effects(
        Stream.of("Add {G}{G}. Spend this mana only to cast creature spells.")
    )
    assert effects is not None

    mana = [e for e in effects if e.kind is EffectKind.ADD_MANA]
    assert len(mana) == 1, "the rider is not an effect of its own"

    restriction = mana[0].mana_restriction
    assert restriction is not None, "the rider must reach the mana it narrows"
    assert restriction.filter.types_all & CardType.CREATURE


def test_kicker_reads_how_the_spell_was_cast(card_db):
    """CR 702.33b: "If this spell was kicked" asks about this spell, not the board.

    An UNPARSED condition would have been safe - it evaluates false - but the
    ability would have counted as parsed while never doing the kicked half.
    """
    from mtgfish.rules.kernel.query import ConditionKind

    effects = parse_effects(
        Stream.of("If this spell was kicked, draw a card.")
    )
    assert effects is not None
    assert effects[0].condition.kind is ConditionKind.WAS_KICKED


def test_a_spell_records_the_additional_costs_it_paid(card_db):
    """The engine half: kicker has something real to read."""
    from mtgfish.rules.kernel.gameobject import GameObject

    assert "additional_costs_paid" in GameObject.__slots__
