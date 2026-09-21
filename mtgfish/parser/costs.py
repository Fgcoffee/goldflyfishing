"""Parsing activation costs (CR 601.2f, 602.1).

A cost is a comma-separated list of components, and the components are a small
closed set: mana, tapping, sacrificing, discarding, paying life, removing
counters. Anything else, and the ability fails - a cost the engine cannot
charge would let a card be activated for free, which is worse than the card not
working at all.
"""

from __future__ import annotations

from ..rules.cr106_mana import ManaCost, UnknownManaSymbol
from ..rules.cr118_costs import EXILE_ZONES, Cost, CostComponent, CostKind
from ..rules.query import ObjectFilter, Value
from .nouns import SELF_NOUNS, parse_object_filter, parse_value
from .tokens import Stream, TokenKind

SELF = ObjectFilter(source_only=True)


def parse_cost(text: str) -> tuple[Cost | None, str]:
    """Read a whole cost. Returns ``(None, reason)`` if any part is unreadable."""
    stream = Stream.of(text)
    _skip_keyword_prefix(stream)
    components: list[CostComponent] = []

    while not stream.done:
        stream.skip_punct(",")
        # "Pay 1 life and exile a blue card from your hand" - components are
        # joined by "and" as often as by a comma, and reading only one
        # separator failed the whole cost at the first conjunction.
        stream.accept("and")
        stream.skip_punct(",")
        if stream.done:
            break
        component = _component(stream)
        if component is None:
            return None, f"unreadable cost component at {stream.peek().text!r}"
        components.append(component)

    if not components:
        return None, "empty cost"
    return Cost(tuple(components)), ""


def _skip_keyword_prefix(stream: Stream) -> None:
    """A keyword name before the cost: "Boast - {1}{R}", "Exhaust - {2}".

    The keyword itself carries the timing rule and is expanded elsewhere by
    the keyword registry; here it is only in the way. Consumed rather than
    modelled, and only when a dash follows, so an ordinary cost that happens
    to start with a word is untouched.
    """
    mark = stream.mark()
    words = 0
    while words < 3 and stream.peek().kind is TokenKind.WORD:
        stream.next()
        words += 1
        if stream.peek().text in ("-", "--"):
            stream.next()
            return
    stream.reset(mark)


def _component(stream: Stream) -> CostComponent | None:
    return (
        _loyalty(stream)
        or _mana(stream)
        or _tap(stream)
        or _tap_other(stream)
        or _exert(stream)
        or _sacrifice(stream)
        or _discard(stream)
        or _pay_life(stream)
        or _pay_mana(stream)
        or _remove_counters(stream)
        or _exile_self(stream)
        or _exile_from_zone(stream)
        or _return_to_hand(stream)
    )


def _exert(stream: Stream) -> CostComponent | None:
    """"Exert this land" as a cost (CR 701.39).

    Exerting is tapping plus a promise not to untap next turn. The promise is
    not modelled, so this is charged as the tap it also is - which is the
    conservative half: the ability still costs something, and nothing is made
    cheaper than the card says.
    """
    mark = stream.mark()
    if not stream.accept("exert"):
        return None
    if not stream.accept("this"):
        stream.reset(mark)
        return None
    stream.accept(*_SELF_NOUNS)
    stream.accept("card")
    return CostComponent(CostKind.TAP_SELF, text="exert this")


def _tap_other(stream: Stream) -> CostComponent | None:
    """"Tap an untapped creature you control" - the Convoke-shaped cost.

    Distinct from ``{T}``, which taps the source. This taps something else,
    and the something else has to be untapped already for the cost to be
    payable at all.
    """
    mark = stream.mark()
    if not stream.accept("tap", "untap"):
        return None
    tapping = stream.tokens[mark].lower == "tap"

    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    return CostComponent(
        CostKind.TAP_OTHER if tapping else CostKind.UNTAP_OTHER,
        filter=spec,
        amount=spec.count or Value.of(1),
        text="tap another permanent" if tapping else "untap another permanent",
    )


def _pay_mana(stream: Stream) -> CostComponent | None:
    """"Pay {2}" written out, as opposed to a bare run of symbols."""
    mark = stream.mark()
    if not stream.accept("pay"):
        return None
    component = _mana(stream)
    if component is None:
        stream.reset(mark)
        return None
    return component


def _exile_self(stream: Stream) -> CostComponent | None:
    """"Exile this artifact", "Exile this creature" - a cost, not an effect.

    With no zone named, "this artifact" is a permanent and the cost is paid
    from the battlefield. The default was the graveyard, so Inquisitive Puppet
    paid by exiling some other card from the graveyard and stayed in play.
    """
    mark = stream.mark()
    if not stream.accept("exile"):
        return None
    if not stream.accept("this"):
        stream.reset(mark)
        return None
    stream.accept(*_SELF_NOUNS)
    # "Exile this card from your hand" (Simian Spirit Guide, the Force cycle).
    # Which zone it leaves decides whether the ability works from hand at all,
    # so the phrase has to be read rather than skipped.
    kind = CostKind.EXILE_FROM_BATTLEFIELD
    if stream.accept("from"):
        if stream.accept_phrase("your hand"):
            kind = CostKind.EXILE_FROM_HAND
        elif stream.accept_phrase("your graveyard"):
            kind = CostKind.EXILE_FROM_GRAVEYARD
        elif stream.accept_phrase("your library"):
            kind = CostKind.EXILE_FROM_LIBRARY
        else:
            return None
    return CostComponent(
        kind, filter=SELF, amount=Value.of(1), text="exile this"
    )


#: One shared list, imported rather than repeated - see nouns.SELF_NOUNS for
#: why keeping a second copy here was a bug rather than a convenience.
_SELF_NOUNS = SELF_NOUNS


def _loyalty(stream: Stream) -> CostComponent | None:
    """CR 606.1: "+1", "-3" or "0" before the colon of a loyalty ability.

    Only at the very start of a cost, because a bare number anywhere else is
    part of something else entirely - "{1}, Remove 3 charge counters" must not
    read its 3 as a loyalty change.

    The sign is the whole point: a plus adds counters and a minus removes them,
    and CR 606.3 forbids activating a minus ability without the loyalty to pay
    it. Reading "-3" as "3" would make every ultimate free.
    """
    if stream.mark() != 0:
        return None

    mark = stream.mark()
    sign = 1
    if stream.accept("-", "−"):
        sign = -1
    else:
        stream.accept("+")

    amount = stream.accept_number()
    if amount is None:
        stream.reset(mark)
        return None
    # A loyalty cost is the entire cost; anything following means this was a
    # number belonging to some other component.
    if not stream.done:
        stream.reset(mark)
        return None

    return CostComponent(
        CostKind.LOYALTY,
        amount=Value.of(sign * amount),
        text=f"{sign * amount:+d}",
    )


def _mana(stream: Stream) -> CostComponent | None:
    """A run of mana symbols. {T} is tapping, not mana, so it is excluded."""
    symbols: list[str] = []
    while stream.peek().kind is TokenKind.SYMBOL and stream.peek().text not in (
        "{T}",
        "{Q}",
    ):
        symbols.append(stream.next().text)
    if not symbols:
        return None
    try:
        mana = ManaCost.parse("".join(symbols))
    except UnknownManaSymbol:
        # A symbol the mana system does not know - "{TK}" and friends from
        # nontraditional cards. An unreadable symbol makes the whole cost
        # unreadable, which fails the ability rather than charging less.
        return None
    return CostComponent(CostKind.MANA, mana=mana)


def _tap(stream: Stream) -> CostComponent | None:
    token = stream.peek()
    if token.text == "{T}":
        stream.next()
        return CostComponent(CostKind.TAP_SELF, text="{T}")
    if token.text == "{Q}":
        stream.next()
        return CostComponent(CostKind.UNTAP_SELF, text="{Q}")
    return None


def _sacrifice(stream: Stream) -> CostComponent | None:
    if not stream.accept("sacrifice"):
        return None
    if stream.accept("this"):
        stream.accept(*_SELF_NOUNS)
        return CostComponent(
            CostKind.SACRIFICE,
            filter=SELF,
            amount=Value.of(1),
            text="sacrifice this",
        )
    spec = parse_object_filter(stream)
    if spec is None:
        return None
    return CostComponent(
        CostKind.SACRIFICE,
        filter=spec,
        amount=Value.of(1),
        text="sacrifice",
    )


def _discard(stream: Stream) -> CostComponent | None:
    if not stream.accept("discard"):
        return None
    if stream.accept("this"):
        stream.accept("card")
        return CostComponent(
            CostKind.DISCARD,
            filter=SELF,
            amount=Value.of(1),
            text="discard this",
        )
    # "Discard your hand" - however many cards happen to be in it, which is
    # not a number the counted-noun path can express.
    from ..rules.query import PlayerFilter, PlayerScope, ValueKind

    _YOU = PlayerFilter(PlayerScope.YOU)
    if stream.accept_phrase("your hand") or stream.accept_phrase("their hand"):
        return CostComponent(
            CostKind.DISCARD,
            amount=Value(kind=ValueKind.CARDS_IN_HAND, players=_YOU),
            text="discard your hand",
        )
    amount = parse_value(stream) or Value.of(1)
    spec = parse_object_filter(stream)
    if spec is None and not stream.accept("card", "cards"):
        return None
    # "at random" says *how* the card is chosen. The engine's discard already
    # picks without the player's judgement, so the phrase is consumed rather
    # than modelled - but leaving it unread failed the whole cost.
    stream.accept_phrase("at random")
    return CostComponent(
        CostKind.DISCARD, filter=spec, amount=amount, text="discard"
    )


def _pay_life(stream: Stream) -> CostComponent | None:
    mark = stream.mark()
    if not stream.accept("pay"):
        return None
    # "Pay life equal to the number of colors in your commander's identity" -
    # the amount comes after the word, not before it.
    look = stream.mark()
    if stream.accept("life") and stream.accept_phrase("equal to"):
        amount = parse_value(stream)
        if amount is not None:
            return CostComponent(CostKind.PAY_LIFE, amount=amount, text="pay life")
    stream.reset(look)

    amount = parse_value(stream)
    if amount is None or not stream.accept("life"):
        stream.reset(mark)
        return None
    return CostComponent(CostKind.PAY_LIFE, amount=amount, text="pay life")


def _remove_counters(stream: Stream) -> CostComponent | None:
    mark = stream.mark()
    if not stream.accept("remove"):
        return None
    amount = parse_value(stream) or Value.of(1)
    token = stream.peek()
    if token.kind is TokenKind.PT:
        stream.next()
        counter = token.text
    elif token.kind is TokenKind.WORD:
        stream.next()
        counter = token.lower
    else:
        stream.reset(mark)
        return None
    if not stream.accept("counter", "counters"):
        stream.reset(mark)
        return None
    stream.accept("from")
    stream.accept("this")
    # The module already knows every noun a card uses for itself; this one
    # place listed three of them, so "remove a wish counter from this
    # *artifact*" failed and Wishclaw Talisman could never be activated.
    stream.accept(*SELF_NOUNS)
    return CostComponent(
        CostKind.REMOVE_COUNTERS,
        counter_type=counter,
        amount=amount,
        filter=SELF,
        text="remove counters",
    )


def _exile_from_zone(stream: Stream) -> CostComponent | None:
    mark = stream.mark()
    if not stream.accept("exile"):
        return None
    if stream.accept("this"):
        stream.reset(mark)
        return None  # "exile this ..." belongs to _exile_self, which reads
        # the zone as well - two readers claiming the same phrase and
        # disagreeing about its tail is how "Exile this card from your hand"
        # became an unreadable cost.
    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    # The zone was hardcoded to the graveyard, so Force of Will - "exile a
    # blue card *from your hand*" - was charged against the wrong zone
    # entirely, which makes it payable when it should not be and unpayable
    # when it should.
    # "Exile *three* cards from your graveyard" - the count lives on the noun
    # phrase, and dropping it made the cost one card rather than three: not a
    # hang, but a card that is three times cheaper than it prints.
    zone = _only_zone(spec)
    kind = CostKind.EXILE_FROM_GRAVEYARD if zone is None else _EXILE_KINDS.get(zone)
    if kind is None:
        # "Exile an instant or sorcery spell you control" names a zone no cost
        # is paid from. Unreadable is the safe reading: defaulting to the
        # graveyard is how City of Shadows' "Exile a creature you control"
        # exiled a graveyard card and kept the creature.
        stream.reset(mark)
        return None
    return CostComponent(
        kind,
        filter=spec,
        amount=spec.count if spec.count is not None else Value.of(1),
        text="exile a card",
    )


#: Which cost kind an exile-from-zone maps to: a permanent's filter names the
#: battlefield, and that is where "a creature you control" is exiled from.
_EXILE_KINDS = {zone: kind for kind, zone in EXILE_ZONES.items()}


def _only_zone(spec):
    """The single zone a filter names, or ``None`` if it names none or many."""
    zones = spec.zones
    return next(iter(zones)) if len(zones) == 1 else None


def _return_to_hand(stream: Stream) -> CostComponent | None:
    mark = stream.mark()
    if not stream.accept("return"):
        return None
    spec = parse_object_filter(stream)
    if spec is None:
        stream.reset(mark)
        return None
    if not (
        stream.accept_phrase("to its owner's hand")
        or stream.accept_phrase("to your hand")
    ):
        stream.reset(mark)
        return None
    return CostComponent(CostKind.RETURN_TO_HAND, filter=spec, text="return to hand")
