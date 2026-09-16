"""Parsing the things effects act on: object filters, players, zones, counts.

``ObjectFilter`` is the most reused node in the whole IR and the hardest to
read, because a noun phrase in oracle text can carry almost any combination of
constraints: "another target nonlegendary creature you control with power 3 or
greater that's attacking". Every one of those fragments is optional, they
appear in a fixed order, and the order is the only thing that makes the phrase
unambiguous.

That order, which this module follows exactly:

    [each|all|target|another|up to N|N]
    [nonlegendary|legendary|basic|snow]
    [colour...]  [nontoken|token]
    <type or subtype>
    [you control | an opponent controls | you own]
    [with <constraint> | that's <state> | named <name>]
    [in <zone> | from <zone>]

Anything left over after that walk means the phrase was not understood, and the
whole ability fails. That is deliberate: a filter that silently drops "you
control" turns a board wipe into a one-sided one.
"""

from __future__ import annotations

from dataclasses import replace

from ..rules.enums import CardType, Color, Supertype, Zone
from ..rules.query import (
    Comparison,
    ControllerRelation,
    NumericConstraint,
    ObjectFilter,
    PlayerFilter,
    PlayerScope,
    Value,
    ValueKind,
)
from ..rules.typeline import active_registry
from .tokens import Stream, TokenKind

#: Card types by the word oracle text uses. Plural and singular both appear -
#: "destroy all creatures", "target creature" - and both mean the same type.
TYPE_WORDS: dict[str, CardType] = {
    "artifact": CardType.ARTIFACT,
    "artifacts": CardType.ARTIFACT,
    "battle": CardType.BATTLE,
    "battles": CardType.BATTLE,
    "creature": CardType.CREATURE,
    "creatures": CardType.CREATURE,
    "enchantment": CardType.ENCHANTMENT,
    "enchantments": CardType.ENCHANTMENT,
    "instant": CardType.INSTANT,
    "instants": CardType.INSTANT,
    "land": CardType.LAND,
    "lands": CardType.LAND,
    "planeswalker": CardType.PLANESWALKER,
    "planeswalkers": CardType.PLANESWALKER,
    "sorcery": CardType.SORCERY,
    "sorceries": CardType.SORCERY,
    "kindred": CardType.KINDRED,
    "tribal": CardType.KINDRED,
}

COLOUR_WORDS: dict[str, Color] = {
    "white": Color.WHITE,
    "blue": Color.BLUE,
    "black": Color.BLACK,
    "red": Color.RED,
    "green": Color.GREEN,
}

SUPERTYPE_WORDS: dict[str, Supertype] = {
    "basic": Supertype.BASIC,
    "legendary": Supertype.LEGENDARY,
    "snow": Supertype.SNOW,
    "world": Supertype.WORLD,
}

ZONE_WORDS: dict[str, Zone] = {
    "battlefield": Zone.BATTLEFIELD,
    "graveyard": Zone.GRAVEYARD,
    "graveyards": Zone.GRAVEYARD,
    "hand": Zone.HAND,
    "hands": Zone.HAND,
    "library": Zone.LIBRARY,
    "libraries": Zone.LIBRARY,
    "exile": Zone.EXILE,
    "stack": Zone.STACK,
    "command": Zone.COMMAND,
}

#: The words that name a permanent generically. "Permanent" is any of the
#: permanent types, and the type filter stays empty rather than listing them,
#: because CardType.NONE means "no constraint" and that is exactly right.
#: "source" belongs here for the same reason "permanent" does: it names a
#: thing without describing it. Damage replacements ("if a source you control
#: would deal damage...") are written about sources, and without the word the
#: whole family had no subject.
GENERIC_NOUNS = (
    "permanent", "permanents", "card", "cards", "spell", "spells", "object",
    "source", "sources",
)


#: Nouns that mean "the object this ability is printed on" (CR 201.5). After
#: normalization the card's own name is already ``this``, so these are the
#: whole set.
#:
#: Exported, and deliberately the *only* copy. Three modules each kept their
#: own shorter list and they drifted: the damage clause knew "this creature"
#: and "this permanent" but not "this land", so every damage-dealing land and
#: artifact in the format failed on its own first word. A card type is not a
#: reason for a clause to stop working.
SELF_NOUNS = (
    "creature", "permanent", "artifact", "enchantment", "land", "card",
    "spell", "planeswalker", "battle", "equipment", "aura", "vehicle",
    "token", "saga", "creature's", "spacecraft", "sorcery", "instant",
    "kindred", "class", "room", "case", "emblem", "dungeon",
)

#: Backwards-compatible alias for the module's own internal uses.
_SELF_NOUNS = SELF_NOUNS

#: Nouns that name an ability on the stack. They are objects (CR 113.7) and
#: can be copied and targeted, but they are not permanents and no card type
#: describes them.
_ABILITY_NOUNS = ("ability", "abilities")


def singular(word: str) -> str | None:
    """The subtype a plural noun names, or ``None`` if it names none.

    Oracle text pluralises freely - "Goblins you control", "Attacking Ninjas",
    "Elves" - and the subtype registry holds only singulars, so every tribal
    card in the format failed on its own creature type. English is irregular
    enough that guessing one rule is wrong; the registry is the arbiter, so
    candidates are simply tried against it in order of likelihood.
    """
    registry = active_registry()
    if registry.known(word):
        return word

    candidates: list[str] = []
    if word.endswith("ies"):
        candidates.append(word[:-3] + "y")
    if word.endswith("ves"):
        candidates.extend((word[:-3] + "f", word[:-3] + "fe"))
    if word.endswith("es"):
        candidates.append(word[:-2])
    if word.endswith("s"):
        candidates.append(word[:-1])

    for candidate in candidates:
        if registry.known(candidate):
            return candidate
    return None


def parse_object_filter(stream: Stream) -> ObjectFilter | None:
    """Read a noun phrase. Returns ``None`` and rewinds if it is not one."""
    special = _self_or_attached(stream)
    if special is not None:
        return special

    library = _top_of_library(stream)
    if library is not None:
        return library

    mark = stream.mark()
    spec = ObjectFilter()

    spec, _targeted, count, up_to = _quantifier(stream, spec)
    spec = _qualifiers(stream, spec)

    # Nouns and type alternatives interleave: "instant or sorcery spell" is a
    # noun, a disjunction, and then the *head* noun. Running each once left
    # "spell" and "card" stranded on two of the commonest templates in the
    # language, which failed the whole ability every time.
    ability = _ability_noun(stream, spec)
    if ability is not None:
        return _ownership(stream, ability)

    saw_noun = False
    first = True
    while True:
        # A type list crosses commas - "artifact, creature, or land" - and
        # the alternatives pass below reads it inside a single iteration.
        # What must not cross is the search for a *second* head noun: doing
        # so let "six or more lands, lands you control have ..." read the
        # subject of the next clause as part of this noun phrase, and the
        # condition swallowed half the sentence.
        if not first and stream.peek().text == ",":
            break
        first = False
        spec, saw = _noun(stream, spec)
        saw_noun = saw_noun or saw
        before = stream.mark()
        spec = _alternatives(stream, spec)
        # "creature tokens you control", "artifact creature tokens" - English
        # puts the qualifier after the head noun as readily as before it, and
        # running the qualifier pass only once, up front, stranded every one
        # of them.
        spec = _qualifiers(stream, spec)
        if not saw and stream.mark() == before:
            break

    # "token" is both an adjective and a noun - "token creature you control"
    # and "for each token you control" - and the qualifier pass eats it either
    # way. When nothing else followed it, it *was* the noun.
    if not saw_noun and spec.is_token:
        saw_noun = True

    if not saw_noun:
        stream.reset(mark)
        return None

    # "target player or planeswalker" - a disjunction spanning a player and an
    # object type. CR 115.4 already models that on the filter, so the player
    # half is a flag rather than a second filter.
    look = stream.mark()
    if stream.accept("or"):
        if stream.accept("player", "players", "opponent", "opponents"):
            spec = replace(spec, includes_players=True)
        else:
            stream.reset(look)
    spec = _ownership(stream, spec)
    # "a creature card *from among them*" - the pile the resolution is
    # holding, which is the same set "those cards" names. Read here because
    # it can follow any noun phrase at all.
    if stream.accept_phrase("from among them") or stream.accept_phrase(
        "from among those cards"
    ):
        spec = replace(spec, remembered=True)

    # "Creatures you control *of the chosen type*", "permanents of that
    # type", "creatures that aren't of the chosen type" - a reference to the
    # choice the permanent made as it entered, which it records.
    if stream.accept_phrase("that aren't of the chosen type") or stream.accept_phrase(
        "that isn't of the chosen type"
    ):
        spec = replace(spec, not_of_chosen_type=True)
    elif stream.accept_phrase("of the chosen type") or stream.accept_phrase(
        "of that type"
    ):
        spec = replace(spec, of_chosen_type=True)
    elif (
        stream.accept_phrase("of the chosen color")
        or stream.accept_phrase("of the chosen colour")
        or stream.accept_phrase("of that color")
        or stream.accept_phrase("of that colour")
    ):
        spec = replace(spec, of_chosen_color=True)

    # "with mana cost {0} or {1}" - the printed cost rather than its value.
    # Read as a mana-value constraint only when every symbol is generic,
    # where the two questions have the same answer; a coloured cost is a
    # different question and is left unread rather than approximated.
    spec = _printed_mana_cost(stream, spec)
    spec = _in_a_players_zone(stream, spec)
    spec = _whole_phrase_disjunction(stream, spec)
    stream.accept_phrase("of the chosen color")
    stream.accept_phrase("of the chosen colour")

    # "each creature destroyed this way", "the cards exiled this way" - the
    # participle names what this very resolution did to them, which the
    # resolution already remembers. It is the same phrase whatever the verb,
    # so it is read here rather than in each clause that can precede one.
    look = stream.mark()
    # "cards *they* exiled this way", "cards *you* exiled this way" - the
    # relative clause names its own subject, which sits between the noun and
    # the participle.
    stream.accept("they", "you", "it", "he", "she")
    participle = stream.peek()
    if participle.kind is TokenKind.WORD and participle.lower.endswith("ed"):
        stream.next()
        if stream.accept_phrase("this way"):
            spec = replace(spec, remembered=True)
        else:
            stream.reset(look)
    # Constraints and zones interleave: "creature *on the battlefield* with
    # mana value 3 or less" writes the zone in the middle. Running each once
    # left the trailing constraint stranded - the same shape as the noun and
    # disjunction interleaving above.
    while True:
        before = stream.mark()
        spec = _constraints(stream, spec)
        spec = _zone(stream, spec)
        if stream.mark() == before:
            break

    if count is not None:
        spec = replace(spec, count=Value.of(count), up_to=up_to)
    return spec


def _self_or_attached(stream: Stream) -> ObjectFilter | None:
    """"this creature", "enchanted creature", "equipped creature".

    Three phrases that name an object without describing it. "This" is the
    source (CR 201.5). "Enchanted" and "equipped" are the permanent this Aura
    or Equipment is attached to (CR 702.5, 702.6) - which is a different object
    from the source, and confusing the two is how an Aura ends up buffing
    itself.
    """
    mark = stream.mark()

    if stream.accept("this"):
        stream.accept(*_SELF_NOUNS)
        return ObjectFilter(source_only=True)

    # Pronouns. "It gains flying", "put a counter on it", "sacrifice them".
    # The referent is whatever the resolution last acted on, which is usually
    # *not* the source - "Destroy target creature. Its controller loses 2
    # life" means the target's controller. So this is its own filter kind,
    # answered from the resolution's memory rather than from the source.
    if stream.accept("it", "them", "they"):
        return ObjectFilter(remembered=True)
    if stream.accept_phrase("that creature") or stream.accept_phrase("that permanent"):
        return ObjectFilter(remembered=True)
    if stream.accept_phrase("that card") or stream.accept_phrase("those cards"):
        return ObjectFilter(remembered=True)
    if stream.accept_phrase("that spell") or stream.accept_phrase("those spells"):
        # A spell lives on the stack, and the pronoun refers to the one the
        # trigger fired on - so the zone matters as much as the reference.
        return ObjectFilter(remembered=True, zones=frozenset({Zone.STACK}))
    if stream.accept_phrase("that player") or stream.accept_phrase("that opponent"):
        return None  # A player, not an object; the player parser handles it.

    # References back to something the resolution produced a moment ago.
    # "Copy the exiled card. You may cast *the copy*" is one sentence
    # referring to the object the sentence before it made, which is exactly
    # what the remembered reference is for.
    if stream.accept_phrase("the exiled card") or stream.accept_phrase(
        "the exiled cards"
    ):
        return ObjectFilter(remembered=True, zones=frozenset({Zone.EXILE}))
    if stream.accept_phrase("the copy") or stream.accept_phrase("the copies"):
        return ObjectFilter(remembered=True, zones=frozenset({Zone.STACK}))
    if stream.accept_phrase("that token") or stream.accept_phrase("those tokens"):
        return ObjectFilter(remembered=True)

    if stream.accept("enchanted", "equipped", "fortified"):
        # "Equipped creature" is the permanent this Equipment is attached
        # *to* - which, from that permanent's point of view, is the one with
        # this Equipment among its attachments.
        #
        # It was written the other way round: ``attached_to`` asks "is this
        # object attached to the source", which is true of the Equipment and
        # false of the creature. So the filter matched nothing and every
        # static ability on every Aura and Equipment in the format was inert
        # - the text was read, the opcode was right, and it applied to no
        # object at all.
        stream.accept(*_SELF_NOUNS)
        return ObjectFilter(has_attached=ObjectFilter(source_only=True))

    stream.reset(mark)
    return None


def parse_description(stream: Stream) -> ObjectFilter | None:
    """A description with no head noun: "blue", "tapped", "legendary".

    ``parse_object_filter`` insists on a noun, which is right for a target -
    "destroy target blue" is not a card - but wrong for a test about a known
    object, where "if it's blue" names no noun because it does not need to.
    """
    mark = stream.mark()
    spec = _qualifiers(stream, ObjectFilter())
    if stream.mark() == mark:
        return None
    return spec


def parse_target(stream: Stream) -> tuple[ObjectFilter | None, bool]:
    """A noun phrase, plus whether it said "target" (CR 115.1).

    Also reads the player-first disjunction, "target player or planeswalker":
    the object grammar cannot start on the word "player", so without this the
    phrase failed on the "or".

    Targeting is not a property of the noun - it is a property of the *effect*,
    which is why it comes back separately rather than being folded into the
    filter. An effect that targets is checked twice, on announcement and on
    resolution (CR 608.2b), and one that does not is checked once.
    """
    mark = stream.mark()
    targeted = _peek_targeted(stream)
    spec = parse_object_filter(stream)
    if spec is not None:
        return spec, targeted

    stream.reset(mark)
    player_first = _player_or_object(stream)
    if player_first is not None:
        return player_first, targeted

    stream.reset(mark)
    return None, False


def _player_or_object(stream: Stream) -> ObjectFilter | None:
    """"target player or planeswalker" - a player named before the object.

    CR 115.4 models "or player" as a flag on the object filter, so the two
    halves collapse into one filter whichever order the card writes them.
    """
    mark = stream.mark()
    players, _ = parse_player_filter(stream)
    if players is None:
        stream.reset(mark)
        return None
    if not stream.accept("or"):
        stream.reset(mark)
        return None

    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    return replace(spec, includes_players=True)


def _peek_targeted(stream: Stream) -> bool:
    """Whether the coming noun phrase is a target, without consuming it."""
    mark = stream.mark()
    found = False
    for _ in range(4):  # "up to two target creatures" - target is never deeper
        token = stream.peek()
        if token.kind is TokenKind.END:
            break
        if token.lower == "target":
            found = True
            break
        if token.lower in ("up", "to", "another", "each", "all", "any") or (
            token.kind is TokenKind.NUMBER
        ):
            stream.next()
            continue
        break
    stream.reset(mark)
    return found


def _ability_noun(stream: Stream, spec: ObjectFilter) -> ObjectFilter | None:
    """"target triggered ability you control", "target activated ability".

    An ability on the stack is an object and can be copied or countered, but
    it has no card type, so the type-driven noun reader could never see one.
    """
    mark = stream.mark()
    stream.accept("a", "an", "the")
    targeted = bool(stream.accept("target"))
    stream.accept("triggered", "activated", "loyalty", "mana")
    if stream.peek().lower not in _ABILITY_NOUNS:
        stream.reset(mark)
        return None
    stream.next()
    return replace(
        spec,
        zones=frozenset({Zone.STACK}),
        is_ability=True,
        count=Value.of(1) if targeted else None,
    )


def _quantifier(
    stream: Stream, spec: ObjectFilter
) -> tuple[ObjectFilter, bool, int | None, bool]:
    """How many, and whether they are targets."""
    targeted = False
    count: int | None = None
    up_to = False
    at_most = False

    # "each of up to two target creatures", "each of those creatures" - the
    # quantifier that follows is the real one, so "each of" is read and then
    # the rest of this function runs as usual.
    stream.accept_phrase("each of")

    # "those creatures", "these cards" - a back-reference to what the
    # resolution just acted on, which it already remembers. Without it every
    # two-sentence effect that referred back to its own first half failed.
    if stream.accept("those", "these"):
        spec = replace(spec, remembered=True)
    else:
        # "that Equipment", "that creature" - the singular demonstrative,
        # naming one thing the sentence already mentioned. Only the plural
        # was read, so every trigger that referred back to its own subject
        # failed on the word "that".
        look = stream.mark()
        if stream.accept("that"):
            if stream.peek().kind is TokenKind.WORD:
                spec = replace(spec, remembered=True)
            else:
                stream.reset(look)

    if stream.accept("each", "all", "every"):
        pass  # No count constraint: the filter matches everything it can.
    elif stream.accept_phrase("up to"):
        up_to = True
        count = stream.accept_number()
        if count is None:
            count = 1
    elif stream.accept("any"):
        stream.accept_phrase("number of")
    elif stream.accept("X"):
        # "Sacrifice X Treasures", "Exile X target creatures". X is a
        # quantity like any other; reading only literal numbers meant every
        # X-cost effect in the format failed on its own quantifier.
        spec = replace(spec, count=Value(kind=ValueKind.X))
    else:
        number = stream.accept_number()
        if number is not None:
            count = number
            # "One or two target creatures" - a range written as a choice.
            # The larger bound is what the chooser may take, and "up to" is
            # exactly that.
            look = stream.mark()
            if stream.accept("or"):
                higher = stream.accept_number()
                if higher is not None:
                    count = max(count, higher)
                    up_to = True
                else:
                    stream.reset(look)
            # "three or more artifacts": the count is a floor rather than an
            # exact number. Whether that floor is compared with >= or == is
            # the asking side's business - a condition counts permanents, a
            # target choice counts choices - so the quantifier only records
            # that the phrase was there and consumes it.
            if stream.accept_phrase("or more") or stream.accept_phrase("or greater"):
                pass
            elif stream.accept_phrase("or fewer") or stream.accept_phrase("or less"):
                at_most = True

    if stream.accept("other"):
        spec = replace(spec, other_than_source=True)
    if stream.accept("another"):
        spec = replace(spec, other_than_source=True)
    if stream.accept("target"):
        targeted = True
        # "another target creature" and "target another creature" both occur.
        if stream.accept("another"):
            spec = replace(spec, other_than_source=True)

    # "or more" / "or fewer" change how the count is compared, not what the
    # filter matches. The comparison lives on whatever asks the question - a
    # condition, or an "up to" target choice - so it is carried out through
    # ``up_to`` and the count itself.
    if at_most:
        up_to = True
    return spec, targeted, count, up_to


def _colour_run(stream: Stream) -> Color:
    """"red", "red or green", "white, blue, or black" - a list of colours.

    Returned as a single mask because the filter asks "any of these", which
    is what every card using the phrase means.
    """
    found = Color.NONE
    while True:
        word = stream.peek().lower
        if word not in COLOUR_WORDS:
            break
        stream.next()
        found |= COLOUR_WORDS[word]
        look = stream.mark()
        stream.skip_punct(",")
        if not stream.accept("or", "and"):
            stream.reset(look)
            break
    return found


def _qualifiers(stream: Stream, spec: ObjectFilter) -> ObjectFilter:
    """Supertypes, colours and token-ness, in any order and any number."""
    while True:
        token = stream.peek()
        word = token.lower

        negated = word.startswith("non") and len(word) > 3
        # Cards write both "nonland" and "non-Human". Stripping three
        # characters left "-human" on the hyphenated form, so no type and no
        # subtype ever matched one - and every "non-X" noun phrase in the
        # game failed.
        base = word[3:].lstrip("-") if negated else word
        # "untapped", "unblocked", "unattached" - English negates a state
        # adjective with "un-", not "non-". Only the "non-" form was handled,
        # so "an untapped creature you control" failed the whole noun phrase
        # and with it every cost and effect that used one.
        if not negated and word.startswith("un") and word[2:] in _UN_NEGATABLE:
            negated = True
            base = word[2:]

        if negated and base in TYPE_WORDS:
            # "nonartifact, nonland card" - a *conjunction* of negations,
            # which is why it has no "or" anywhere and why the disjunction
            # reader cannot have it. Each one narrows the same filter.
            stream.next()
            spec = replace(spec, types_none=spec.types_none | TYPE_WORDS[base])
            look = stream.mark()
            stream.skip_punct(",")
            nxt = stream.peek().lower
            if not (nxt.startswith("non") and nxt[3:].lstrip("-") in TYPE_WORDS):
                stream.reset(look)
            continue

        if base in SUPERTYPE_WORDS:
            stream.next()
            value = SUPERTYPE_WORDS[base]
            spec = (
                replace(spec, supertypes_none=spec.supertypes_none | value)
                if negated
                else replace(spec, supertypes_all=spec.supertypes_all | value)
            )
            continue

        if base in COLOUR_WORDS:
            stream.next()
            value = COLOUR_WORDS[base]
            spec = (
                replace(spec, colors_none=spec.colors_none | value)
                if negated
                else replace(spec, colors_any=spec.colors_any | value)
            )
            continue

        if negated:
            # "non-Human", "non-Zombie" - a negated *subtype*. The affirmative
            # form is read by the noun pass, which never sees these because
            # the word is one token with the negation attached.
            # The registry holds subtypes capitalised, and the qualifier
            # pass works in lower case, so the original spelling is what has
            # to be looked up.
            subtype = singular(token.text[3:].lstrip("-"))
            if subtype is not None:
                stream.next()
                spec = replace(spec, subtypes_none=spec.subtypes_none + (subtype,))
                continue

        if base == "colorless":
            stream.next()
            spec = replace(spec, must_be_colorless=not negated)
            continue
        if base == "multicolored":
            stream.next()
            spec = replace(spec, must_be_multicolored=not negated)
            continue
        if base == "monocolored":
            stream.next()
            spec = replace(spec, must_be_monocolored=not negated)
            continue
        if base == "modified":
            # CR 701.48: a permanent with a counter on it, an Aura you
            # control attached to it, or an Equipment attached to it. The
            # first is by far the commonest and is what the filter can
            # express; the definition is one the matcher already has pieces
            # of, so it is written as "has a counter" rather than invented.
            stream.next()
            spec = replace(spec, has_counter="*")
            continue

        if base in ("token", "tokens"):
            stream.next()
            spec = replace(spec, is_token=not negated)
            continue
        if base == "tapped":
            stream.next()
            spec = replace(spec, tapped=not negated)
            continue
        if base == "attacking":
            stream.next()
            spec = replace(spec, attacking=not negated)
            continue
        if base == "blocking":
            stream.next()
            spec = replace(spec, blocking=not negated)
            continue
        # "blocked" is the passive of "block" and a different constraint from
        # "blocking": an unblocked attacker is not the same as one that is not
        # blocking anything.
        if base == "blocked":
            stream.next()
            spec = replace(spec, blocked=not negated)
            continue

        return spec


#: State adjectives that oracle text negates with "un-" rather than "non-".
#: Deliberately a closed list: "unearth" and "unless" also start with "un" and
#: are not negations of anything.
_UN_NEGATABLE = frozenset({"tapped", "blocked", "blocking", "attacking"})


def _noun(stream: Stream, spec: ObjectFilter) -> tuple[ObjectFilter, bool]:
    """The head noun: a card type, a subtype, or a generic word.

    Several may appear - "artifact creature", "Goblin Warrior" - and a type
    word after a subtype is still a type word, so this loops rather than taking
    the first thing it sees.
    """
    saw = False
    while True:
        token = stream.peek()
        word = token.lower
        negated = word.startswith("non") and len(word) > 3
        base = word[3:] if negated else word

        if base in TYPE_WORDS:
            stream.next()
            value = TYPE_WORDS[base]
            spec = (
                replace(spec, types_none=spec.types_none | value)
                if negated
                else replace(spec, types_all=spec.types_all | value)
            )
            saw = True
            continue

        # "a commander", "your commanders" (CR 903.3). A designation rather
        # than a card type, which is why it is not in TYPE_WORDS - but it is
        # the head noun of the phrase and without it the free-spell cycle,
        # among the most played cards in the format, had no subject at all.
        if base in ("commander", "commanders"):
            stream.next()
            spec = replace(spec, is_commander=not negated)
            saw = True
            continue

        if base in GENERIC_NOUNS:
            stream.next()
            if base in ("spell", "spells"):
                spec = replace(spec, zones=frozenset({Zone.STACK}))
            elif base in ("card", "cards") and spec.zones == frozenset(
                {Zone.BATTLEFIELD}
            ):
                # A "card" is not on the battlefield (CR 109.1) - permanents
                # there are permanents. The zone is settled later by an
                # explicit "in your graveyard" if there is one.
                spec = replace(spec, zones=frozenset())
            saw = True
            continue

        # Subtypes are capitalised in oracle text, which is the only reliable
        # signal that "Forest" is a land type and not the English word.
        if token.kind is TokenKind.WORD and token.text[:1].isupper():
            name = singular(token.text)
            if name is not None:
                stream.next()
                spec = replace(spec, subtypes_all=(*spec.subtypes_all, name))
                saw = True
                continue

        return spec, saw


def _alternatives(stream: Stream, spec: ObjectFilter) -> ObjectFilter:
    """"artifact or creature", "artifact, creature, or enchantment".

    A list of alternatives is a *disjunction* of types, which is ``types_any``
    rather than ``types_all`` - and the difference is the whole clause. Read as
    a conjunction it would match only objects that are both, which is almost
    nothing.
    """
    mark = stream.mark()
    collected = spec.types_all
    subtypes = list(spec.subtypes_all)
    matched = False
    # An Oxford list spells its conjunction once, before the last item, so a
    # comma alone has to be allowed to continue it. But a comma *without* any
    # conjunction anywhere is not a list at all - it is the end of this noun
    # phrase and the start of the next clause, and reading it as a list let
    # "six or more lands, lands you control have ..." swallow the subject of
    # the sentence it was a condition on. The state before the first
    # comma-only step is kept so it can be undone if no conjunction arrives.
    joined_once = False
    before_comma: tuple | None = None

    while True:
        look = stream.mark()
        # A comma alone continues the list: "artifact, enchantment, or
        # planeswalker" spells "or" once, before the last item only. Requiring
        # a conjunction at every step stopped dead at the first comma, which
        # is how one of the commonest templates in the language failed.
        comma = False
        if stream.peek().text == ",":
            stream.skip_punct(",")
            comma = True
        # "and", "or", and Scryfall's "and/or" all mean the same thing here:
        # an object matching any of the listed types qualifies. "Instant and
        # sorcery cards" is not asking for a card that is both.
        joined = stream.accept("or") or stream.accept("and")
        if joined:
            stream.accept("or")
            joined_once = True
        elif not comma:
            stream.reset(look)
            break
        elif before_comma is None:
            before_comma = (look, collected, tuple(subtypes), matched)

        # "a Swamp or *a* Mountain" - each alternative may carry its own
        # determiner. The check-land cycle is written this way and every one
        # of them stopped at the second "a".
        stream.accept("a", "an", "the", "another")

        # And its own qualifiers: "artifact, enchantment, or *nonbasic* land",
        # "noncreature artifact or *noncreature* enchantment". Reading only a
        # bare type word meant an alternative with any adjective at all ended
        # the list one item early.
        spec = _qualifiers(stream, spec)

        # "noncreature artifact or *noncreature* enchantment" - the negation
        # is repeated on each alternative, and it is a type rather than a
        # qualifier so ``_qualifiers`` does not see it.
        token = stream.peek()
        word = token.lower
        if word.startswith("non") and word[3:] in TYPE_WORDS:
            stream.next()
            spec = replace(spec, types_none=spec.types_none | TYPE_WORDS[word[3:]])
            token = stream.peek()

        if token.lower in TYPE_WORDS:
            stream.next()
            collected |= TYPE_WORDS[token.lower]
            matched = True
            continue
        # "Elf or Goblin", "Zombies and Skeletons" - a disjunction of creature
        # types reads exactly like one of card types.
        if token.kind is TokenKind.WORD and token.text[:1].isupper():
            name = singular(token.text)
            if name is not None:
                stream.next()
                subtypes.append(name)
                matched = True
                continue
        stream.reset(look)
        break

    if before_comma is not None and not joined_once:
        # Commas all the way and never a conjunction: this was not a list.
        position, collected, kept, matched = before_comma
        stream.reset(position)
        subtypes = list(kept)

    if not matched:
        stream.reset(mark)
        return spec

    if len(subtypes) > len(spec.subtypes_all):
        # A list of subtypes is a disjunction too: "Elf or Goblin" must not
        # become "a permanent that is both an Elf and a Goblin".
        spec = replace(spec, subtypes_all=(), subtypes_any=tuple(subtypes))
    if collected:
        spec = replace(spec, types_all=CardType.NONE, types_any=collected)
    return spec


def _ownership(stream: Stream, spec: ObjectFilter) -> ObjectFilter:
    """"you control", "an opponent controls", "you own"."""
    mark = stream.mark()

    if stream.accept_phrase("you don't control") or stream.accept_phrase(
        "you do not control"
    ):
        # Not the same as "an opponent controls" - it includes permanents no
        # one controls - but for every card that says it, an opponent's is
        # what it means, and reading it as "any" would make Vandalblast blow
        # up your own board.
        return replace(spec, controller=ControllerRelation.OPPONENT)
    # "Artifact spells *you cast*" - who cast a spell is who controls it
    # (CR 601.2a), so the cost reducers' phrasing is a controller constraint
    # written a different way. Without it Foundry Inspector and its whole
    # family had no readable noun phrase at all.
    if stream.accept_phrase("you cast"):
        return replace(spec, controller=ControllerRelation.YOU)
    if stream.accept_phrase("your opponents cast") or stream.accept_phrase(
        "an opponent casts"
    ):
        return replace(spec, controller=ControllerRelation.OPPONENT)

    if stream.accept_phrase("you control"):
        return replace(spec, controller=ControllerRelation.YOU)
    if stream.accept_phrase("you own"):
        return replace(spec, owner=ControllerRelation.YOU)
    if stream.accept_phrase("an opponent controls"):
        return replace(spec, controller=ControllerRelation.OPPONENT)
    if stream.accept_phrase("your opponents control"):
        return replace(spec, controller=ControllerRelation.OPPONENT)
    if stream.accept_phrase("target player controls"):
        return replace(spec, controller=ControllerRelation.SPECIFIC)
    if stream.accept_phrase("target opponent controls"):
        return replace(spec, controller=ControllerRelation.OPPONENT)
    if stream.accept_phrase("that player controls"):
        return replace(spec, controller=ControllerRelation.SPECIFIC)
    if stream.accept_phrase("they control"):
        return replace(spec, controller=ControllerRelation.SPECIFIC)
    if stream.accept_phrase("defending player controls"):
        return replace(spec, controller=ControllerRelation.DEFENDING_PLAYER)

    stream.reset(mark)
    return spec


def _zone(stream: Stream, spec: ObjectFilter) -> ObjectFilter:
    """"in your graveyard", "from a library", "on the battlefield".

    An explicit zone overrides whatever the noun implied, which matters for
    "creature card in your graveyard": the noun said card, the zone says
    where, and only together do they mean anything.
    """
    mark = stream.mark()
    if not stream.accept("in", "from", "on"):
        return spec

    owner = None
    look = stream.mark()
    if stream.accept("your"):
        owner = ControllerRelation.YOU
        stream.reset(look)
    elif stream.accept_phrase("an opponent's"):
        owner = ControllerRelation.OPPONENT
        stream.reset(look)
    elif stream.accept("all") or stream.accept_phrase("each player's"):
        # "cards in all graveyards" - every player's, which is the absence of
        # an owner constraint rather than a special kind of one.
        owner = None

    zone = parse_zone(stream)
    if zone is None:
        stream.reset(mark)
        return spec

    spec = replace(spec, zones=frozenset({zone}))
    if owner is not None:
        spec = replace(spec, owner=owner)
    return spec


def _constraints(stream: Stream, spec: ObjectFilter) -> ObjectFilter:
    """"with power 3 or greater", "that's attacking", "named X"."""
    while True:
        # "creatures you control *each* with power 3 or greater" - "each" is
        # decoration on a plural subject. Skipped before the mark is taken,
        # because the readers below probe and rewind to it, which would undo
        # the skip and leave the word to fail the phrase.
        if stream.at("each") and stream.peek(1).lower == "with":
            stream.next()

        mark = stream.mark()

        if stream.accept("named"):
            name = _card_name(stream)
            if name:
                spec = replace(spec, named=(*spec.named, name))
                continue
            stream.reset(mark)
            return spec

        if stream.accept_phrase("with power") or stream.accept_phrase("with toughness"):
            stream.reset(mark)
            stream.accept("with")
            attribute = stream.next().lower
            constraint = _comparison(stream)
            if constraint is None:
                stream.reset(mark)
                return spec
            spec = (
                replace(spec, power=constraint)
                if attribute == "power"
                else replace(spec, toughness=constraint)
            )
            continue

        if stream.accept_phrase("with mana value"):
            constraint = _comparison(stream)
            if constraint is None:
                stream.reset(mark)
                return spec
            spec = replace(spec, mana_value=constraint)
            continue

        # "creatures with flying", "a creature with deathtouch". Expressible
        # for power and mana value already; keywords were the obvious gap.
        look = stream.mark()
        if stream.accept("with"):
            names = _keyword_names(stream)
            if names:
                spec = replace(spec, has_keyword=(*spec.has_keyword, *names))
                continue
            stream.reset(look)
        if stream.accept("without"):
            names = _keyword_names(stream)
            if names:
                spec = replace(spec, lacks_keyword=(*spec.lacks_keyword, *names))
                continue
            stream.reset(look)

        # "with counters on them" - the bare plural, with no article and no
        # kind. The same question as "with a counter on it", written for a
        # set rather than for one permanent.
        look = stream.mark()
        if stream.accept("with") and stream.accept("counter", "counters"):
            stream.accept_phrase("on them")
            stream.accept_phrase("on it")
            spec = replace(
                spec,
                has_counter="*",
                counter_constraint=NumericConstraint.at_least(1),
            )
            continue
        stream.reset(look)

        if stream.accept_phrase("with a") or stream.accept_phrase("with an"):
            counter = _counter_type(stream)
            if not counter and stream.at("counter", "counters"):
                # "with a counter on it" - any kind at all, which is what
                # the counters-matter cards and Modified both ask.
                stream.next()
                counter = "*"
            if counter:
                stream.accept_phrase("on it")
                stream.accept_phrase("on them")
                spec = replace(
                    spec,
                    has_counter=counter,
                    counter_constraint=NumericConstraint.at_least(1),
                )
                continue
            stream.reset(mark)
            return spec

        # "spells you cast *that's red or green*", "a creature that's white"
        # - a relative clause about colour, which is a constraint the filter
        # has had all along with no wording that reached it.
        look = stream.mark()
        if stream.accept_phrase("that's") or stream.accept_phrase("that are"):
            colours = _colour_run(stream)
            if colours:
                spec = replace(spec, colors_any=spec.colors_any | colours)
                continue
            stream.reset(look)

        if stream.accept_phrase("that entered this turn") or stream.accept_phrase(
            "that entered the battlefield this turn"
        ):
            spec = replace(spec, entered_this_turn=True)
            continue

        # "permanents they control *that are one or more colors*" - coloured
        # rather than colourless, which the filter says the other way round.
        if stream.accept_phrase("that are one or more colors") or stream.accept_phrase(
            "that are one or more colours"
        ):
            spec = replace(spec, must_be_colorless=False, must_be_coloured=True)
            continue

        if stream.accept_phrase("that's attacking") or stream.accept_phrase(
            "that are attacking"
        ):
            spec = replace(spec, attacking=True)
            # "that's attacking *you*" - who is being attacked. Consumed
            # rather than modelled: the attacking flag already narrows to
            # creatures in combat, and the defending player is not a
            # characteristic the matcher holds. Leaving the word unread
            # failed the whole noun phrase, and with it Propaganda.
            look = stream.mark()
            if parse_player_filter(stream)[0] is None:
                stream.reset(look)
            continue
        if stream.accept_phrase("that's blocking") or stream.accept_phrase(
            "that are blocking"
        ):
            spec = replace(spec, blocking=True)
            continue
        if stream.accept_phrase("that's tapped") or stream.accept_phrase(
            "that are tapped"
        ):
            spec = replace(spec, tapped=True)
            continue
        if stream.accept_phrase("that's untapped") or stream.accept_phrase(
            "that are untapped"
        ):
            spec = replace(spec, tapped=False)
            continue

        stream.reset(mark)
        return spec


def _keyword_names(stream: Stream) -> tuple[str, ...]:
    """A run of keyword names: "flying", "flying and haste"."""
    from ..rules.keywords import is_known

    names: list[str] = []
    while True:
        token = stream.peek()
        if token.kind is not TokenKind.WORD:
            break
        pair = f"{token.text} {stream.peek(1).text}".strip()
        if is_known(pair):
            stream.next()
            stream.next()
            names.append(pair)
        elif is_known(token.text):
            stream.next()
            names.append(token.text)
        else:
            break
        look = stream.mark()
        stream.skip_punct(",")
        if not stream.accept("and", "or"):
            stream.reset(look)
            break
    return tuple(names)


def parse_comparison(stream: Stream) -> NumericConstraint | None:
    """Public entry to the comparison reader, for callers outside this module."""
    return _comparison(stream)


def _comparison(stream: Stream) -> NumericConstraint | None:
    """"3 or greater", "2 or less", "exactly 4", "X", "greater than <count>"."""
    # "greater than the number of lands that player controls" - the bound is a
    # counted quantity rather than a literal, and the whole point of the card
    # is that it moves.
    mark = stream.mark()
    # "less than or equal to" and "greater than or equal to" have to be tried
    # before their prefixes, or "less than" matches and the "or equal to"
    # half is left behind - which fails the whole noun phrase.
    if stream.accept_phrase("greater than or equal to"):
        value = parse_value(stream)
        if value is None:
            stream.reset(mark)
            return None
        return NumericConstraint(Comparison.GE, value)
    if stream.accept_phrase("less than or equal to"):
        value = parse_value(stream)
        if value is None:
            stream.reset(mark)
            return None
        return NumericConstraint(Comparison.LE, value)
    if stream.accept_phrase("greater than"):
        value = parse_value(stream)
        if value is None:
            stream.reset(mark)
            return None
        return NumericConstraint(Comparison.GT, value)
    if stream.accept_phrase("less than"):
        value = parse_value(stream)
        if value is None:
            stream.reset(mark)
            return None
        return NumericConstraint(Comparison.LT, value)
    if stream.accept_phrase("equal to"):
        value = parse_value(stream)
        if value is None:
            stream.reset(mark)
            return None
        return NumericConstraint(Comparison.EQ, value)

    # A bound is a bound whether the number is written out or is X. Reading
    # the two separately meant "mana value 3 or less" worked and "mana value X
    # or less" - the whole tutor-for-X family - did not.
    amount = stream.accept_number()
    if amount is not None:
        bound = Value.of(amount)
    elif stream.accept("X"):
        bound = Value(kind=ValueKind.X)
    else:
        return None

    if stream.accept_phrase("or greater") or stream.accept_phrase("or more"):
        return NumericConstraint(Comparison.GE, bound)
    if stream.accept_phrase("or less") or stream.accept_phrase("or fewer"):
        return NumericConstraint(Comparison.LE, bound)
    return NumericConstraint(Comparison.EQ, bound)


def _counter_type(stream: Stream) -> str:
    """"+1/+1 counter", "loyalty counter", "charge counter"."""
    mark = stream.mark()
    token = stream.peek()

    if token.kind is TokenKind.PT:
        stream.next()
        if stream.accept("counter", "counters"):
            return token.text
        stream.reset(mark)
        return ""

    if token.kind is TokenKind.WORD:
        stream.next()
        if stream.accept("counter", "counters"):
            return token.lower
        stream.reset(mark)
    return ""


def _card_name(stream: Stream) -> str:
    """A quoted or capitalised card name."""
    if stream.accept('"'):
        words = []
        while stream and not stream.at('"'):
            words.append(stream.next().text)
        stream.accept('"')
        return " ".join(words)

    words = []
    while stream.peek().kind is TokenKind.WORD and stream.peek().text[:1].isupper():
        words.append(stream.next().text)
    return " ".join(words)


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------

_PLAYER_PHRASES: tuple[tuple[str, PlayerScope], ...] = (
    ("each opponent", PlayerScope.EACH_OPPONENT),
    ("each other player", PlayerScope.EACH_OPPONENT),
    ("each player", PlayerScope.EACH_PLAYER),
    ("target opponent", PlayerScope.TARGET_OPPONENT),
    ("target player", PlayerScope.TARGET_PLAYER),
    # The tokenizer keeps a possessive as one word, so "target player's
    # graveyard" never matches "target player". Listing the possessive forms
    # is cheaper than teaching the tokenizer to split them, which would
    # ripple through every phrase in the grammar.
    ("target player's", PlayerScope.TARGET_PLAYER),
    ("target opponent's", PlayerScope.TARGET_OPPONENT),
    ("each player's", PlayerScope.EACH_PLAYER),
    ("each opponent's", PlayerScope.EACH_OPPONENT),
    ("that player's", PlayerScope.TARGET_PLAYER),
    # "That creature's controller", "That land's controller" - the possessive
    # names an object the sentence already referred to, and the tokenizer
    # keeps it whole, so each form has to be listed.
    ("that creature's controller", PlayerScope.CONTROLLER_OF),
    ("that permanent's controller", PlayerScope.CONTROLLER_OF),
    ("that land's controller", PlayerScope.CONTROLLER_OF),
    ("that spell's controller", PlayerScope.CONTROLLER_OF),
    ("that card's owner", PlayerScope.OWNER_OF),
    ("that creature's owner", PlayerScope.OWNER_OF),
    ("that permanent's owner", PlayerScope.OWNER_OF),
    ("an opponent", PlayerScope.OPPONENT),
    ("that player", PlayerScope.TARGET_PLAYER),
    # The pronoun for a player the same sentence has already named. Read the
    # same way as "that player", which is what it stands in for.
    ("they", PlayerScope.TARGET_PLAYER),
    ("them", PlayerScope.TARGET_PLAYER),
    ("its controller", PlayerScope.CONTROLLER_OF),
    ("its owner", PlayerScope.OWNER_OF),
    ("their controller", PlayerScope.CONTROLLER_OF),
    ("their owner", PlayerScope.OWNER_OF),
    ("the monarch", PlayerScope.MONARCH),
    ("defending player", PlayerScope.DEFENDING_PLAYER),
    ("active player", PlayerScope.ACTIVE_PLAYER),
    # "a player" / "another player" / "a player other than you". The
    # indefinite forms were missing entirely, so "Whenever a player casts a
    # spell" - one of the most common trigger shapes there is - could not
    # find a subject at all.
    ("another player", PlayerScope.EACH_OPPONENT),
    ("a player", PlayerScope.EACH_PLAYER),
    ("any player", PlayerScope.EACH_PLAYER),
    ("one or more players", PlayerScope.EACH_PLAYER),
    ("an opponent's", PlayerScope.OPPONENT),
    ("opponents", PlayerScope.EACH_OPPONENT),
    ("players", PlayerScope.EACH_PLAYER),
    ("your opponents", PlayerScope.EACH_OPPONENT),
    ("you", PlayerScope.YOU),
)


def parse_player_filter(stream: Stream) -> tuple[PlayerFilter | None, bool]:
    """A player phrase, plus whether it targets.

    Longest phrase first, because "a player" is a prefix of nothing but
    "each player" contains "player" and "your opponents" contains
    "opponents" - a shorter match would strand the rest of the phrase and
    fail the ability that contains it.
    """
    mark = stream.mark()
    for phrase, scope in sorted(
        _PLAYER_PHRASES, key=lambda entry: -len(entry[0].split())
    ):
        if stream.accept_phrase(phrase):
            return PlayerFilter(scope), phrase.startswith("target")

    possessive = _possessive_player(stream)
    if possessive is not None:
        return possessive, False

    stream.reset(mark)
    return None, False


def _possessive_player(stream: Stream) -> PlayerFilter | None:
    """"that creature's controller", "the exiled card's owner".

    Any noun can take a possessive, so listing the ones that do was always
    going to be one short - "that creature's controller" was listed and
    "that token's controller" was not. The possessive is recognised by its
    shape and only the *role* after it has to be one of the two that exist.
    """
    mark = stream.mark()
    stream.accept("the", "that", "this", "each", "a", "an")

    # "the owner of target permanent", "the controller of that spell" - the
    # of-form of the same phrase, and the only one that can open a sentence.
    if stream.accept("owner", "owners", "controller", "controllers"):
        role = (
            PlayerScope.OWNER_OF
            if stream.peek(-1).lower.startswith("owner")
            else PlayerScope.CONTROLLER_OF
        )
        if stream.accept("of") and parse_object_filter(stream) is not None:
            return PlayerFilter(role)
        stream.reset(mark)
        return None

    steps = 0
    while not stream.done and steps < 4:
        token = stream.peek()
        if token.kind is not TokenKind.WORD:
            break
        stream.next()
        steps += 1
        if token.text.endswith("'s"):
            if stream.accept("controller"):
                return PlayerFilter(PlayerScope.CONTROLLER_OF)
            if stream.accept("owner"):
                return PlayerFilter(PlayerScope.OWNER_OF)
            break

    stream.reset(mark)
    return None


# ---------------------------------------------------------------------------
# Counts and zones
# ---------------------------------------------------------------------------


#: Which characteristic a superlative is taken over.
_CHARACTERISTIC_WORDS = {
    "power": ValueKind.POWER,
    "toughness": ValueKind.TOUGHNESS,
}


#: Characteristics of an object the sentence already referred to, and the
#: possessive forms cards write them with. The tokenizer keeps a possessive
#: whole, so these are phrases rather than a noun plus an apostrophe.
_REMEMBERED_OWNERS = (
    "that card's", "that creature's", "that permanent's", "that spell's",
    "its", "their",
)

_CHARACTERISTIC_OF = {
    "power": ValueKind.POWER,
    "toughness": ValueKind.TOUGHNESS,
}


def _amount_this_way(stream: Stream) -> Value | None:
    """"the life lost this way", "the damage dealt this way".

    A quantity the sentence before it produced, which the resolution already
    remembers. Gray Merchant of Asphodel is the shape: drain each opponent,
    then gain that much.
    """
    mark = stream.mark()
    stream.accept("the")
    if not stream.accept("life", "damage", "mana", "cards"):
        stream.reset(mark)
        return None
    if not stream.accept("lost", "dealt", "gained", "drawn", "spent", "added"):
        stream.reset(mark)
        return None
    if not stream.accept_phrase("this way"):
        stream.reset(mark)
        return None
    return Value(kind=ValueKind.COUNT, filter=ObjectFilter(remembered=True))


#: Words that never appear inside a possessive owner phrase, and so end the
#: scan for one.
_NOT_IN_A_POSSESSIVE = frozenset(
    {
        "equal", "to", "than", "for", "with", "from", "on", "in", "by",
        "as", "life", "damage", "and", "or", "plus", "minus", "times",
    }
)


def _possessive_characteristic(stream: Stream) -> Value | None:
    """"the sacrificed creature's power", "that spell's mana value".

    Any noun phrase at all can carry a possessive, so a fixed list of owners
    was always going to be one short - "the sacrificed creature's",
    "equipped creature's", "the exiled card's" and so on are unbounded. The
    owner is read generically; only the *characteristic* has to be one the
    engine can actually read.
    """
    mark = stream.mark()
    stream.accept("the", "a", "an", "each", "that", "this")

    words: list[str] = []
    while not stream.done and len(words) < 5:
        token = stream.peek()
        if token.kind is not TokenKind.WORD:
            break
        # A possessive owner is one noun phrase, and these words never sit
        # inside one. Without the stop list the scan walked straight through
        # "life equal to" and swallowed the noun the caller was waiting for,
        # so "you lose life equal to that permanent's mana value" read its
        # own verb's object as part of the amount.
        if token.lower in _NOT_IN_A_POSSESSIVE:
            break
        stream.next()
        words.append(token.lower)
        if token.text.endswith("'s"):
            break

    if not words or not words[-1].endswith("'s"):
        stream.reset(mark)
        return None

    if stream.accept_phrase("mana value"):
        kind = ValueKind.MANA_VALUE
    else:
        word = stream.peek().lower
        if word not in _CHARACTERISTIC_WORDS:
            stream.reset(mark)
            return None
        stream.next()
        kind = _CHARACTERISTIC_WORDS[word]

    # "the sacrificed creature's power" asks about what the cost consumed,
    # which nothing else in the resolution can name: by then the creature is
    # in the graveyard and the ability's own source is a different object.
    consumed = {"sacrificed", "tapped", "exiled", "discarded"}
    if kind is ValueKind.POWER and consumed.intersection(words):
        return Value(kind=ValueKind.COST_PAID_POWER)

    return Value(kind=kind, of_affected=True)


def _arithmetic_tail(stream: Stream, value: Value) -> Value:
    """"... plus one", "... minus 1", "... times two".

    Cards build amounts by arithmetic on other amounts, and the tail can
    follow any of them, so it belongs here rather than in each phrase that
    might be followed by one.
    """
    while True:
        mark = stream.mark()
        if stream.accept("plus"):
            kind = ValueKind.SUM
        elif stream.accept("minus"):
            kind = ValueKind.DIFFERENCE
        elif stream.accept("times"):
            kind = ValueKind.PRODUCT
        else:
            return value

        operand = _plain_number(stream)
        if operand is None:
            stream.reset(mark)
            return value
        value = Value(kind=kind, operands=(value, operand))


def _plain_number(stream: Stream) -> Value | None:
    """A bare number or X, without the phrase machinery around it."""
    if stream.accept("X"):
        return Value(kind=ValueKind.X)
    amount = stream.accept_number()
    return None if amount is None else Value.of(amount)


def _remembered_characteristic(stream: Stream) -> Value | None:
    """"that card's mana value", "its power" - a characteristic of whatever
    the sentence last acted on.

    Reanimate's life loss is the clearest case: the amount is a fact about the
    creature just reanimated, which the resolution remembers.
    """
    mark = stream.mark()
    for owner in _REMEMBERED_OWNERS:
        if stream.accept_phrase(owner):
            break
    else:
        return None

    if stream.accept_phrase("mana value"):
        return Value(kind=ValueKind.MANA_VALUE, of_affected=True)
    word = stream.peek().lower
    if word in _CHARACTERISTIC_OF:
        stream.next()
        return Value(kind=_CHARACTERISTIC_OF[word], of_affected=True)

    stream.reset(mark)
    return None


def _devotion_value(stream: Stream) -> Value | None:
    """"your devotion to black", "your devotion to blue and black" (CR 700.5).

    "to that color" appears on cards that chose a colour earlier; the engine
    holds the choice, so every colour is allowed and the choice narrows it at
    resolution.
    """
    mark = stream.mark()
    stream.accept("your", "their", "his", "her")
    if not stream.accept("devotion"):
        stream.reset(mark)
        return None
    if not stream.accept("to"):
        stream.reset(mark)
        return None

    colours = Color.NONE
    if stream.accept_phrase("that color") or stream.accept_phrase("that colour"):
        colours = (
            Color.WHITE | Color.BLUE | Color.BLACK | Color.RED | Color.GREEN
        )
    else:
        while True:
            word = stream.peek().lower
            if word not in COLOUR_WORDS:
                break
            stream.next()
            colours |= COLOUR_WORDS[word]
            stream.skip_punct(",")
            if not stream.accept("and", "or"):
                break

    if not colours:
        stream.reset(mark)
        return None
    return Value(kind=ValueKind.DEVOTION, colors=colours)


def _superlative_value(stream: Stream) -> Value | None:
    """"the greatest mana value among permanents you control".

    "Among" is the giveaway: it names a *set* of objects to compare, so the
    number of things being compared is itself a game-state question and the
    plain MAXIMUM opcode - which compares a fixed list - cannot express it.
    """
    mark = stream.mark()
    stream.accept("the")

    if stream.accept("greatest", "highest"):
        kind = ValueKind.GREATEST_AMONG
    elif stream.accept("least", "lowest", "smallest"):
        kind = ValueKind.LEAST_AMONG
    else:
        stream.reset(mark)
        return None

    if stream.accept_phrase("mana value"):
        inner = ValueKind.MANA_VALUE
    else:
        word = stream.peek().lower
        if word not in _CHARACTERISTIC_WORDS:
            stream.reset(mark)
            return None
        stream.next()
        inner = _CHARACTERISTIC_WORDS[word]

    if not stream.accept("among"):
        stream.reset(mark)
        return None

    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None

    return Value(
        kind=kind,
        filter=spec,
        operands=(Value(kind=inner, of_affected=True),),
    )


def qualifiers_only(stream: Stream) -> ObjectFilter | None:
    """A run of adjectives with no head noun: "legendary", "white", "tapped".

    "As long as equipped creature *is legendary*" describes the subject
    rather than naming a second one, so there is no noun for the ordinary
    reader to find.
    """
    mark = stream.mark()
    stream.accept("a", "an", "the")
    spec = _qualifiers(stream, ObjectFilter())
    if spec == ObjectFilter():
        stream.reset(mark)
        return None
    return spec


def _whole_phrase_disjunction(stream: Stream, spec: ObjectFilter) -> ObjectFilter:
    """"artifact spells and colorless spells", "target spell or nonland
    permanent an opponent controls".

    Two complete noun phrases joined, each with its own head noun. The
    alternatives pass can only share one head - by the time it runs, the
    first noun has been taken - so a second full phrase needs its own reader.

    Only the *identity* halves are merged: types, subtypes and zones, which
    is what the disjunction is about. Everything else stays as the first
    phrase set it, because a trailing "an opponent controls" belongs to both
    halves and is read once, on the second.
    """
    mark = stream.mark()
    if not stream.accept("or", "and"):
        return spec
    if stream.at("or", "and"):
        stream.reset(mark)
        return spec

    other = parse_object_filter(stream)
    if other is None:
        stream.reset(mark)
        return spec

    # A second phrase that constrains nothing is not a disjunction - it is
    # the start of another clause that happened to begin with a noun.
    # "Colorless spells" names no card type and is still a real half, so the
    # test is on whether it narrows anything at all.
    if not (
        other.types_all
        or other.types_any
        or other.types_none
        or other.subtypes_all
        or other.subtypes_any
        or other.colors_any
        or other.must_be_colorless
    ):
        stream.reset(mark)
        return spec

    return replace(
        spec,
        types_any=(spec.types_any | spec.types_all)
        | (other.types_any | other.types_all),
        types_all=CardType.NONE,
        types_none=spec.types_none | other.types_none,
        colors_any=spec.colors_any | other.colors_any,
        must_be_colorless=spec.must_be_colorless or other.must_be_colorless,
        subtypes_any=spec.subtypes_any + other.subtypes_any + other.subtypes_all,
        zones=spec.zones | other.zones,
        controller=(
            other.controller
            if other.controller is not ControllerRelation.ANY
            else spec.controller
        ),
    )


def _in_a_players_zone(stream: Stream, spec: ObjectFilter) -> ObjectFilter:
    """"card in target opponent's hand", "cards in each player's graveyard".

    A zone belonging to somebody other than you. The zone reader knows the
    zones and the player reader knows the owners; nothing put the two
    together, so every count of an opponent's hand failed.
    """
    mark = stream.mark()
    if not stream.accept("in"):
        return spec
    owner, _ = parse_player_filter(stream)
    zone = parse_zone(stream)
    if zone is None:
        stream.reset(mark)
        return spec
    return replace(
        spec,
        zones=frozenset({zone}),
        controller=(
            ControllerRelation.OPPONENT
            if owner is not None and owner.scope in (
                PlayerScope.OPPONENT,
                PlayerScope.EACH_OPPONENT,
                PlayerScope.TARGET_OPPONENT,
            )
            else spec.controller
        ),
    )


def _printed_mana_cost(stream: Stream, spec: ObjectFilter) -> ObjectFilter:
    """"with mana cost {0} or {1}", "with mana cost {2}".

    Generic-only costs are the only ones read: for those the printed cost and
    the mana value are the same question, and the filter can already ask the
    second. A coloured cost would need a constraint the matcher does not
    have, so it is left alone and the ability fails honestly.
    """
    mark = stream.mark()
    if not stream.accept("with"):
        return spec
    if not stream.accept_phrase("mana cost"):
        stream.reset(mark)
        return spec

    values: list[int] = []
    while True:
        token = stream.peek()
        if token.kind is not TokenKind.SYMBOL:
            break
        stream.next()
        inner = token.text.strip("{}")
        if not inner.isdigit():
            stream.reset(mark)
            return spec
        values.append(int(inner))
        look = stream.mark()
        if not stream.accept("or"):
            stream.reset(look)
            break

    if not values:
        stream.reset(mark)
        return spec
    if len(values) == 1:
        return replace(spec, mana_value=NumericConstraint.exactly(values[0]))
    # "{0} or {1}" is a range in every printed case, so the bound is the
    # larger of them.
    return replace(spec, mana_value=NumericConstraint.at_most(max(values)))


def _total_among(stream: Stream) -> Value | None:
    """"the total power of creatures you control", "the total mana value of
    historic permanents you control".

    A sum across a set, which is the third of the three ways to ask about a
    set - the greatest, the least, and the total. Two of them were readable.
    """
    mark = stream.mark()
    stream.accept("the")
    if not stream.accept("total"):
        stream.reset(mark)
        return None

    if stream.accept_phrase("mana value"):
        inner = ValueKind.MANA_VALUE
    else:
        word = stream.peek().lower
        if word not in _CHARACTERISTIC_WORDS:
            stream.reset(mark)
            return None
        stream.next()
        inner = _CHARACTERISTIC_WORDS[word]

    if not stream.accept("of", "among"):
        stream.reset(mark)
        return None

    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None

    return Value(
        kind=ValueKind.TOTAL_AMONG,
        filter=spec,
        operands=(Value(kind=inner, of_affected=True),),
    )


def _top_of_library(stream: Stream) -> ObjectFilter | None:
    """"the top card of your library", "the top three cards of their library".

    A library is ordered (CR 401.2), so this names a position and not merely a
    zone - and the difference matters: an effect that exiles "the top card"
    must not be free to pick any card in the library.
    """
    mark = stream.mark()
    stream.accept("the")
    if not stream.accept("top"):
        stream.reset(mark)
        return None

    depth = stream.accept_number() or 1
    # "the top *card* of your library" names one card; "from the top of your
    # library" names the position with no noun at all, and both are common.
    stream.accept("card", "cards")
    if not stream.accept("of"):
        stream.reset(mark)
        return None

    owner = ControllerRelation.ANY
    if stream.accept("your"):
        owner = ControllerRelation.YOU
    elif stream.accept("their", "his", "her") or stream.accept_phrase(
        "that player's"
    ):
        owner = ControllerRelation.SPECIFIC
    elif stream.accept_phrase("an opponent's") or stream.accept_phrase(
        "target opponent's"
    ):
        owner = ControllerRelation.OPPONENT
    elif stream.accept_phrase("target player's"):
        owner = ControllerRelation.SPECIFIC

    if not stream.accept("library", "libraries"):
        stream.reset(mark)
        return None

    return ObjectFilter(
        zones=frozenset({Zone.LIBRARY}),
        from_top=depth,
        owner=owner,
        count=Value.of(depth),
    )


def parse_for_each(stream: Stream) -> Value | None:
    """A trailing "for each <filter>" or "for each <kind> counter on X".

    Exported because the operator attaches to a whole *effect*, not to the
    number beside it: "deals 1 damage **to any target** for each creature you
    control" puts the target in between. The clause layer applies it.
    """
    mark = stream.mark()
    if not stream.accept_phrase("for each"):
        stream.reset(mark)
        return None
    # "for each *of* those creatures" - the partitive, which reads as the
    # same count over the same set.
    stream.accept("of")

    look = stream.mark()
    token = stream.peek()
    # A counter kind is a word ("charge") or a P/T ("+1/+1"), and only the
    # word form was read - so "for each +1/+1 counter on this creature", the
    # commoner of the two, fell through to the object reader and found no
    # object called "+1/+1".
    if token.kind in (TokenKind.WORD, TokenKind.PT):
        kind = token.text
        stream.next()
        if stream.accept("counter", "counters"):
            stream.accept("on")
            # The noun phrase first: a bare "this" would otherwise swallow
            # the determiner of "this creature" and strand the noun.
            if parse_object_filter(stream) is None:
                stream.accept("it", "this", "them")
            return Value(kind=ValueKind.COUNTERS, counter_type=kind)
    stream.reset(look)

    # "for each color among permanents you control" - how many distinct
    # colours, which is not a count of objects: two green permanents are one
    # colour.
    look = stream.mark()
    if stream.accept("color", "colour", "colors", "colours"):
        if stream.accept("among"):
            among = parse_object_filter(stream)
            if among is not None:
                return Value(kind=ValueKind.COLOURS_AMONG, filter=among)
    stream.reset(look)

    tally = _events_this_turn(stream)
    if tally is not None:
        return tally

    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    return Value(kind=ValueKind.COUNT, filter=spec)


#: The event a "this turn" tally counts, by the verb the card uses.
_TURN_TALLY_VERBS = {
    "created": "TOKEN_CREATED",
    "create": "TOKEN_CREATED",
    "sacrificed": "SACRIFICED",
    "cast": "CAST_SPELL",
    "attacked": "ATTACKS",
    "attacks": "ATTACKS",
    "drawn": "DREW_CARD",
    "drew": "DREW_CARD",
    "gained": "LIFE_GAINED",
    "lost": "LIFE_LOST",
}

#: Contractions the tokenizer keeps whole. Each is a subject and an auxiliary
#: at once, so "you've" never matched the player phrase "you".
_CONTRACTED_SUBJECTS = {
    "you've": PlayerScope.YOU,
    "they've": PlayerScope.TARGET_PLAYER,
    "it's": PlayerScope.YOU,
}


def _events_this_turn(stream: Stream) -> Value | None:
    """"for each spell you've cast this turn", "for each time it has attacked
    this turn".

    A count of *occurrences*. No filter over the battlefield can express it -
    the spells are in graveyards and the attacks are over - so the game keeps
    a per-turn tally, which until now only a yes-or-no condition could read.
    """
    from ..rules.events import EventKind

    mark = stream.mark()

    # "for each spell you've cast this turn" names the noun before the verb;
    # "for each time it has attacked this turn" uses "time" as the unit.
    stream.accept("time", "times")
    stream.accept("spell", "spells", "card", "cards", "creature", "creatures")

    players, _ = parse_player_filter(stream)
    if players is None:
        word = stream.peek().lower
        if word in _CONTRACTED_SUBJECTS:
            stream.next()
            players = PlayerFilter(_CONTRACTED_SUBJECTS[word])
        else:
            stream.accept("it", "this", "they")

    stream.accept("has", "have")
    verb = stream.peek().lower
    if verb not in _TURN_TALLY_VERBS:
        stream.reset(mark)
        return None
    stream.next()

    stream.accept("a", "an", "card", "cards", "spell", "spells", "life")
    if not stream.accept_phrase("this turn"):
        stream.reset(mark)
        return None

    return Value(
        kind=ValueKind.EVENT_COUNT_THIS_TURN,
        players=players,
        event_kinds=(int(getattr(EventKind, _TURN_TALLY_VERBS[verb])),),
    )


def parse_value(stream: Stream) -> Value | None:
    """A number, X, or a counted quantity like "the number of creatures"."""
    mark = stream.mark()

    if stream.accept("X"):
        return Value(kind=ValueKind.X)

    if stream.accept_phrase("for each"):
        # "1 damage for each artifact you control" - the count *is* the amount.
        spec = parse_object_filter(stream)
        if spec is not None:
            return Value(kind=ValueKind.COUNT, filter=spec)
        players = parse_player_filter(stream)[0]
        if players is not None:
            return Value(kind=ValueKind.COUNT, players=players)
        stream.reset(mark)
        return None

    this_way = _amount_this_way(stream)
    if this_way is not None:
        return this_way

    remembered = _remembered_characteristic(stream)
    if remembered is not None:
        return _arithmetic_tail(stream, remembered)

    possessive = _possessive_characteristic(stream)
    if possessive is not None:
        return _arithmetic_tail(stream, possessive)

    devotion = _devotion_value(stream)
    if devotion is not None:
        return devotion

    superlative = _superlative_value(stream)
    if superlative is not None:
        return _arithmetic_tail(stream, superlative)

    total = _total_among(stream)
    if total is not None:
        return _arithmetic_tail(stream, total)

    look = stream.mark()
    if stream.accept_phrase("the amount of mana spent to cast"):
        # CR 601.2g: what was actually paid, which is not the mana value when
        # X or a cost modification was involved. The engine records it on the
        # spell as it is cast.
        stream.accept("it", "this", "that")
        parse_object_filter(stream)
        return Value(kind=ValueKind.MANA_SPENT)
    stream.reset(look)

    if stream.accept_phrase("any number of"):
        # A quantity the player picks. Nothing in the game state fixes it,
        # so it is read as "as many as there are" - which is what a player
        # maximising the effect would choose, and what every card using the
        # phrase is built around.
        counter = _counter_type(stream)
        if counter:
            stream.accept("on")
            if parse_object_filter(stream) is None:
                stream.accept("it", "this", "them")
            return Value(kind=ValueKind.COUNTERS, counter_type=counter)
        spec = parse_object_filter(stream)
        if spec is not None:
            return Value(kind=ValueKind.COUNT, filter=spec)
        stream.reset(mark)
        return None

    if stream.accept_phrase("that many") or stream.accept_phrase("that much"):
        return _arithmetic_tail(stream, Value(kind=ValueKind.EVENT_AMOUNT))

    for phrase in (
        "the number of colors in your commander's color identity",
        "the number of colors in your commanders' color identity",
        "the number of colours in your commander's colour identity",
    ):
        if stream.accept_phrase(phrase):
            return Value(kind=ValueKind.COMMANDER_COLOUR_IDENTITY)

    if stream.accept_phrase("the number of") or stream.accept_phrase("a number of"):
        counter = _counter_type(stream)
        if counter:
            # "the number of +1/+1 counters on it" counts counters, not
            # objects - a different question with the same wording.
            stream.accept("on")
            stream.accept("it", "this")
            parse_object_filter(stream)
            return _arithmetic_tail(
                stream, Value(kind=ValueKind.COUNTERS, counter_type=counter)
            )
        spec = parse_object_filter(stream)
        if spec is not None:
            return _arithmetic_tail(stream, Value(kind=ValueKind.COUNT, filter=spec))
        stream.reset(mark)
        return None

    amount = stream.accept_number()
    if amount is not None:
        # "discard one of them" - the count is the whole quantity and "of
        # them" says which pile, which the effect already knows.
        look = stream.mark()
        if stream.accept("of"):
            if not (
                stream.accept("them", "those", "it")
                or stream.accept_phrase("those cards")
                or stream.accept_phrase("these cards")
            ):
                stream.reset(look)
        return Value.of(amount)

    if stream.accept_phrase("equal to"):
        return parse_value(stream)

    # "the power of the card you exiled", "the sacrificed creature's toughness".
    # The possessive and the "of" form mean the same thing and both appear.
    for phrase, kind in (
        ("the power of", ValueKind.POWER),
        ("the toughness of", ValueKind.TOUGHNESS),
        ("the mana value of", ValueKind.MANA_VALUE),
    ):
        if stream.accept_phrase(phrase):
            if parse_object_filter(stream) is None:
                stream.reset(mark)
                return None
            return Value(kind=kind)

    if stream.accept_phrase("the amount of life you gained this turn"):
        return Value(kind=ValueKind.LIFE_TOTAL)

    if stream.accept_phrase("its power") or stream.accept_phrase("that creature's power"):
        return Value(kind=ValueKind.POWER)
    if stream.accept_phrase("its toughness") or stream.accept_phrase(
        "that creature's toughness"
    ):
        return Value(kind=ValueKind.TOUGHNESS)
    if stream.accept_phrase("its mana value"):
        return Value(kind=ValueKind.MANA_VALUE)
    if stream.accept_phrase("your life total"):
        return Value(kind=ValueKind.LIFE_TOTAL)
    if stream.accept_phrase("the number of"):
        spec = parse_object_filter(stream)
        if spec is not None:
            return Value(kind=ValueKind.COUNT, filter=spec)
        stream.reset(mark)
        return None
    if stream.accept_phrase("the number of cards in your hand"):
        return Value(kind=ValueKind.CARDS_IN_HAND, players=PlayerFilter(PlayerScope.YOU))
    if stream.accept_phrase("its power") or stream.accept_phrase("their power"):
        return Value(kind=ValueKind.POWER)

    stream.reset(mark)
    return None


def colour_among_tail(stream: Stream) -> bool:
    """"of any color among legendary permanents you control".

    The menu is the colours present on some set of permanents. Which set it
    is changes nothing the grammar can act on - the engine reads the board at
    resolution either way - so the phrase is consumed and the mana is "any
    colour", which is what the ability offers.
    """
    mark = stream.mark()
    if not stream.accept("among"):
        return False
    if parse_object_filter(stream) is None:
        stream.reset(mark)
        return False
    return True


def could_produce_tail(stream: Stream) -> bool:
    """A relative clause narrowing "any color" to what some source can make.

    "That a land you control could produce", "that a land an opponent
    controls could produce", "that a Plains could produce" - the list of
    these was written out card by card and was always one short. Which
    source it names changes nothing the grammar can act on: the engine
    settles the available colours from the board at resolution either way.
    """
    mark = stream.mark()
    if not stream.accept("that", "which"):
        return False
    steps = 0
    while not stream.done and steps < 10:
        if stream.accept_phrase("could produce") or stream.accept_phrase(
            "could add"
        ):
            return True
        if stream.peek().text in (".", ";", ":", ","):
            break
        stream.next()
        steps += 1
    stream.reset(mark)
    return False


def parse_zone(stream: Stream) -> Zone | None:
    """"your graveyard", "the battlefield", "their libraries"."""
    mark = stream.mark()
    stream.accept("the", "your", "their", "its", "a", "an")
    stream.accept("owner's", "owners", "controller's")
    token = stream.peek()
    if token.lower in ZONE_WORDS:
        stream.next()
        return ZONE_WORDS[token.lower]
    stream.reset(mark)
    return None
