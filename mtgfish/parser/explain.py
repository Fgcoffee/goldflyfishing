"""Saying back what the parser understood, in English.

Full consumption proves a card was read *completely*. The cross-check catches
some cards that were read *wrongly*. Neither tells you what the parser actually
thinks a specific card does, and that is the question anyone debugging a card
actually has.

So this renders an ``Ability`` back into a sentence - and the one rule that
makes it worth anything is that it renders from the **opcodes**, never from
``Effect.text``. Every effect carries the oracle snippet it came from, and
echoing that back would produce a perfect-looking paraphrase of a parse that
dropped the duration, targeted the wrong player, or lost half a sentence. The
text is the input; using it as the output would make the check circular.

What that buys: if the parser reads "destroy target creature an opponent
controls" and loses the ownership constraint, this says "destroy target
creature", and the difference is visible at a glance.
"""

from __future__ import annotations

from ..rules.cr600_spells_and_abilities.abilities import Ability, AbilityKind
from ..rules.cr600_spells_and_abilities.effects import Effect, EffectKind
from ..rules.kernel.enums import Duration, Timing, Zone
from ..rules.kernel.query import ObjectFilter, PlayerFilter, Value


def explain_ability(ability: Ability) -> str:
    """One ability, as a sentence built from what the engine will execute."""
    if ability.unparsed:
        return "(not understood - this ability is inert)"

    body = _effects(ability.effects)

    if ability.kind is AbilityKind.TRIGGERED:
        when = _trigger(ability.trigger)
        if not ability.static_condition.is_always:
            when += f" (only while {ability.static_condition})"
        return f"{when}, {body}." if body else f"{when}, (nothing)."

    if ability.kind is AbilityKind.ACTIVATED:
        cost = str(ability.cost) if ability.cost.components else "no cost"
        parts = [f"Pay {cost}"]
        if ability.timing is Timing.SORCERY:
            parts.append("(sorcery speed only)")
        if ability.once_each_turn:
            parts.append("(once each turn)")
        if ability.only_once:
            # CR 602.5b: once for the life of the object, not once a turn.
            parts.append("(only once)")
        where = _functions_in(ability.functions_in)
        if where:
            parts.append(f"(from {where})")
        if not ability.activation_condition.is_always:
            # CR 602.5b. Without this the round-trip said a restricted ability
            # could be activated whenever you liked, which is exactly the
            # reading the restriction exists to forbid.
            parts.append(f"(only if {ability.activation_condition})")
        if ability.is_mana_ability:
            parts.append("(mana ability - does not use the stack)")
        return f"{' '.join(parts)}: {body}."

    if ability.kind is AbilityKind.STATIC:
        if ability.keyword and not ability.effects:
            return f"Has {ability.keyword}."
        if not body:
            return f"Has {ability.keyword or 'no effect'}."
        gate = (
            f" (while {ability.static_condition})"
            if not ability.static_condition.is_always
            else ""
        )
        where = _functions_in(ability.functions_in)
        if where:
            gate += f" (from {where})"
        return f"Continuously{gate}: {body}."

    return f"{body[:1].upper()}{body[1:]}." if body else "(nothing)."


def explain_card(parsed) -> list[dict]:
    """Every ability of a parsed card, paired with the text it came from.

    Returned side by side deliberately. The judgement being asked for is
    "do these two say the same thing?", and that is only answerable when both
    are in front of you.
    """
    rows = []
    for face_index, face in enumerate(parsed.faces):
        for ability in face.abilities:
            rows.append(
                {
                    "face": face_index,
                    "kind": ability.kind.name,
                    "oracle": ability.text,
                    "understood": explain_ability(ability),
                    "inert": bool(ability.unparsed)
                    or any(
                        node.is_unparsed
                        for effect in ability.effects
                        for node in effect.walk()
                    ),
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


def _trigger(trigger) -> str:
    if trigger is None:
        return "At some time"

    events = "/".join(
        sorted(k.name.lower().replace("_", " ") for k in trigger.event_kinds)
    )
    subject = _filter(trigger.subject) if trigger.subject is not None else ""
    if trigger.subject is not None and trigger.subject.source_only:
        subject = "this permanent"

    if subject and events:
        parts = [f"Whenever {subject} {events}"]
    elif events:
        parts = [f"Whenever {events}"]
    else:
        parts = ["Whenever something happens"]

    if trigger.players is not None:
        parts.append(f"(player: {_players(trigger.players)})")
    if trigger.source is not None:
        parts.append(f"(from {_filter(trigger.source)})")
    if trigger.to_player:
        parts.append("(to a player)")
    if trigger.counter_kind:
        parts.append(f"({trigger.counter_kind} counters)")
    if trigger.phases or trigger.steps:
        from ..rules.kernel.enums import Phase, Step

        names = [Phase(p).name.lower() for p in sorted(trigger.phases)]
        names += [Step(s).name.lower() for s in sorted(trigger.steps)]
        parts.append(f"(of {'/'.join(n.replace('_', ' ') for n in names)})")
    if trigger.ordinal:
        parts.append(f"(only the #{trigger.ordinal} such event each turn)")
    if trigger.alternatives:
        parts.append(f"(or {len(trigger.alternatives)} other condition(s))")
    if trigger.expend:
        parts.append(f"(expend {trigger.expend})")
    if trigger.chapter:
        parts.append(f"(chapter {trigger.chapter})")
    if trigger.once_each_turn:
        parts.append("(once each turn)")
    where = _functions_in(trigger.functions_in)
    if where:
        parts.append(f"(works from {where})")
    condition = str(trigger.intervening_if)
    if condition not in ("always", ""):
        parts.append(f"- but only if {condition}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------


def _effects(effects) -> str:
    rendered = [_effect(effect) for effect in effects]
    return ", then ".join(part for part in rendered if part)


#: Effects whose whole rendering is "<somebody> <does something>".
_PLAYER_VERBS = {
    EffectKind.DRAW: "draws {n} card(s)",
    EffectKind.DISCARD: "discards {n} card(s)",
    EffectKind.MILL: "mills {n} card(s)",
    EffectKind.SCRY: "scries {n}",
    EffectKind.SURVEIL: "surveils {n}",
    EffectKind.GAIN_LIFE: "gains {n} life",
    EffectKind.LOSE_LIFE: "loses {n} life",
    EffectKind.SET_LIFE: "life becomes {n}",
    EffectKind.ADD_POISON: "gets {n} poison counter(s)",
    EffectKind.ADD_ENERGY: "gets {n} energy",
    EffectKind.ADD_EXPERIENCE: "gets {n} experience counter(s)",
    EffectKind.VENTURE: "ventures into the dungeon",
    EffectKind.RING_TEMPTS: "is tempted by the Ring",
    EffectKind.BECOME_MONARCH: "becomes the monarch",
    EffectKind.TAKE_INITIATIVE: "takes the initiative",
    EffectKind.PLAYER_WINS: "wins the game",
    EffectKind.PLAYER_LOSES: "loses the game",
    EffectKind.EXTRA_TURN: "takes {n} extra turn(s)",
    EffectKind.EXTRA_LAND_DROP: "may play {n} additional land(s)",
    EffectKind.SHUFFLE: "shuffles",
    EffectKind.CASCADE: "cascades",
    EffectKind.DISCOVER: "discovers {n}",
    EffectKind.OPEN_ATTRACTION: "opens {n} Attraction(s)",
    EffectKind.ROLL_TO_VISIT: "rolls to visit their Attractions",
}

#: Effects that are "<verb> <the objects>" and nothing more.
_OBJECT_VERBS = {
    EffectKind.DESTROY: "destroy",
    EffectKind.EXILE: "exile",
    EffectKind.SACRIFICE: "sacrifice",
    EffectKind.RETURN_TO_HAND: "return to hand",
    EffectKind.PUT_ONTO_BATTLEFIELD: "put onto the battlefield",
    EffectKind.TAP: "tap",
    EffectKind.UNTAP: "untap",
    EffectKind.GAIN_CONTROL: "gain control of",
    EffectKind.EXCHANGE_CONTROL: "exchange control of",
    EffectKind.TRANSFORM: "transform",
    EffectKind.TURN_FACE_UP: "turn face up",
    EffectKind.TURN_FACE_DOWN: "turn face down",
    EffectKind.MANIFEST: "put onto the battlefield face down",
    EffectKind.PHASE_OUT: "phase out",
    EffectKind.REGENERATE: "regenerate",
    EffectKind.GOAD: "goad",
    EffectKind.ATTACH: "attach to",
    EffectKind.UNATTACH: "unattach",
    EffectKind.COPY_PERMANENT: "copy",
    EffectKind.COUNTER_SPELL: "counter",
    EffectKind.COPY_SPELL: "copy",
    EffectKind.CHANGE_TARGETS: "change the targets of",
    EffectKind.FIGHT: "fight",
    EffectKind.EXPLORE: "explore with",
    EffectKind.REVEAL: "reveal",
}


def _effect(effect: Effect) -> str:
    text = _effect_body(effect)
    # A guard written on the node itself rather than on a CONDITIONAL around
    # it. Every executor that reads one skips the node when it is false, so
    # leaving it out rendered a conditional effect as an unconditional one.
    if (
        text
        and effect.kind is not EffectKind.CONDITIONAL
        and not effect.condition.is_always
    ):
        text += f" (only if {effect.condition})"
    return text


def _effect_body(effect: Effect) -> str:
    kind = effect.kind

    flow = _control_flow(effect)
    if flow is not None:
        return flow

    who = _subject(effect)
    amount = _amount(effect.amount)
    objects = _objects(effect)

    if kind in _PLAYER_VERBS:
        return f"{who} {_PLAYER_VERBS[kind].replace('{n}', amount)}"
    if kind in _OBJECT_VERBS:
        return _object_verb(effect, objects)

    one_shot = _one_shot(effect, who, amount, objects)
    if one_shot is not None:
        return one_shot

    continuous = _continuous(effect, objects)
    if continuous is not None:
        return continuous

    if kind is EffectKind.DELAYED_TRIGGER:
        # CR 603.7b: a delayed trigger fires once unless it says otherwise,
        # and the two read identically without this - "draw a card at the
        # beginning of the next upkeep" and "at the beginning of each upkeep".
        how_often = "each time" if effect.repeats else "once"
        return (
            f"later ({how_often}) - {_trigger(effect.trigger)}, "
            f"{_effects(effect.children)}"
        )
    if kind is EffectKind.REFLEXIVE_TRIGGER:
        return f"when you do, {_effects(effect.children)}"

    # An opcode with an executor but no phrasing here. Named rather than
    # guessed at, so a missing case reads as missing instead of plausible.
    return f"[{kind.name.lower()}]" + (f" {objects}" if effect.targets else "")


#: Verbs whose actor is the player named on the effect rather than the
#: ability's controller: "each opponent sacrifices a creature" is done by the
#: opponents, and rendering it as a bare "sacrifice" hid which of the two
#: readings the parser had chosen - the difference between a board wipe for
#: the table and one for yourself.
_PLAYER_ACTED_VERBS = {
    EffectKind.SACRIFICE: "sacrifices",
    EffectKind.REVEAL: "reveals",
}


def _object_verb(effect: Effect, objects: str) -> str:
    """"<verb> <the objects>", with the actor and the count when they are said."""
    verb = _OBJECT_VERBS[effect.kind]
    if effect.kind is EffectKind.PUT_ON_LIBRARY:
        # The executor reads the sign of the amount: negative is the bottom.
        where = "bottom" if effect.amount.constant < 0 else "top"
        verb = f"put on {where} of library"
    if effect.targets is None and effect.from_zone is not None:
        # "Exile the top card of each player's library", "target player
        # reveals their hand": no object filter, a zone and a player instead.
        # Rendered as "this permanent" it read like the card exiling itself.
        objects = _zone_of_players(effect)
    if effect.players is not None and effect.kind in _PLAYER_ACTED_VERBS:
        text = f"{_who(effect)} {_PLAYER_ACTED_VERBS[effect.kind]} {objects}"
    else:
        text = f"{verb} {objects}"
    return text + _object_riders(effect)


def _zone_of_players(effect: Effect) -> str:
    """"the top 2 card(s) of each player's library", "target player's hand"."""
    whose = _who(effect) if effect.players is not None else "your"
    whose = "your" if whose == "you" else f"{whose}'s"
    if effect.from_zone is Zone.LIBRARY:
        return f"the top {_amount(effect.amount)} card(s) of {whose} library"
    return f"{whose} {_zone(effect.from_zone)}"


def _object_riders(effect: Effect) -> str:
    """How the objects arrive or how long the change lasts.

    Each of these is a field an executor reads, and each was invisible: a
    creature put onto the battlefield tapped and attacking read the same as
    one put onto it untapped, and a temporary steal the same as a permanent
    one.
    """
    parts: list[str] = []
    for word in ("tapped", "attacking", "face down"):
        if word in effect.keywords:
            parts.append(word)
    if effect.under_owners_control:
        parts.append("under its owner's control")
    if effect.counter_type and effect.kind is EffectKind.PUT_ONTO_BATTLEFIELD:
        parts.append(
            f"with {_amount(effect.amount)} {effect.counter_type} counter(s)"
        )
    if (
        effect.kind in (EffectKind.EXILE, EffectKind.GAIN_CONTROL)
        and effect.duration == Duration.WHILE_SOURCE_PERSISTS
    ):
        parts.append(
            "until this leaves the battlefield"
            if effect.kind is EffectKind.EXILE
            else "for as long as this remains"
        )
    else:
        duration = _duration(effect.duration)
        if duration:
            parts.append(duration.strip())
    return (" " + " ".join(parts)) if parts else ""


def _control_flow(effect: Effect) -> str | None:
    kind = effect.kind
    if kind is EffectKind.SEQUENCE:
        return _effects(effect.children)
    if kind is EffectKind.OPTIONAL:
        # Whose option it is. "You may" and "that player may" are different
        # cards, and printing "you" whatever the IR held made them identical.
        return f"{_who(effect)} may {_effects(effect.children)}"
    if kind is EffectKind.CONDITIONAL:
        text = f"if {effect.condition}, {_effects(effect.children)}"
        if effect.otherwise:
            text += f"; otherwise {_effects(effect.otherwise)}"
        return text
    if kind is EffectKind.REPEAT:
        return f"do this {_amount(effect.amount)} times: {_effects(effect.children)}"
    if kind is EffectKind.UNLESS_PAYS:
        who = _who(effect)
        return (
            f"{_effects(effect.children)}, unless {who} pays {effect.pay_cost}"
        )
    if kind is EffectKind.IF_YOU_DONT:
        # The consequence of declining - without its children the round-trip
        # said "[if_you_dont]" and hid what declining costs.
        return f"if that was not done, {_effects(effect.children)}"
    if kind is EffectKind.CHOOSE_MODE:
        # Each mode rendered whole: rendering only a mode's children dropped
        # an OPTIONAL's "may" and a CONDITIONAL's "if".
        modes = "; or ".join(_effect(child) for child in effect.children)
        return f"{_mode_choice(effect)} - {modes}"
    if kind is EffectKind.NOTHING:
        return ""
    if kind is EffectKind.UNPARSED:
        return "(NOT UNDERSTOOD - inert)"
    return None


def _one_shot(effect: Effect, who: str, amount: str, objects: str) -> str | None:
    kind = effect.kind
    if kind is EffectKind.DAMAGE:
        # CR 601.2d: divided among the targets is a different card from the
        # same amount to each of them.
        split = " divided among" if effect.divided else " to"
        return f"deal {amount} damage{split} {objects}"
    if kind is EffectKind.PREVENT_DAMAGE:
        # -1 is the "all" sentinel the executor reads; printing it as a number
        # made a Fog read like a card that heals one damage.
        how_much = "all" if effect.amount.constant < 0 else f"the next {amount}"
        event = _damage_event(effect)
        if effect.is_targeted and effect.targets is not None:
            event = event.replace(_filter(effect.targets), objects, 1)
        return f"prevent {how_much} {event}{_gated(effect)}{_duration(effect.duration)}"
    if kind is EffectKind.SEARCH_LIBRARY:
        where = f" and puts it into {_zone(effect.zone)}" if effect.zone else ""
        tapped = " tapped" if "tapped" in effect.keywords else ""
        # The executor takes at most ``amount`` cards whatever the filter's
        # own count says, so both are shown when they differ.
        cap = ""
        spec = effect.targets
        if spec is not None and spec.count is not None and str(spec.count) != amount:
            cap = f" (takes at most {amount})"
        return (
            f"{who} searches their library for {_filter(effect.targets)}"
            f"{cap}{where}{tapped}"
        )
    if kind is EffectKind.MOVE_ZONE:
        origin = f"from {_zone(effect.from_zone)} " if effect.from_zone else ""
        shown = "reveal and " if "reveal" in effect.keywords else ""
        return f"{shown}move {objects} {origin}to {_zone(effect.zone)}"
    if kind is EffectKind.PUT_ON_LIBRARY:
        where = "the bottom" if (
            "bottom" in effect.keywords or effect.amount.constant < 0
        ) else "top"
        order = " in a random order" if "random" in effect.keywords else ""
        shown = "reveal and " if "reveal" in effect.keywords else ""
        return f"{shown}put {objects} on {where} of its owner's library{order}"
    if kind is EffectKind.LOOK_AT_TOP:
        verb = "reveals" if "reveal" in effect.keywords else "looks at"
        whose = "target player's" if effect.is_targeted else "their"
        return (
            f"{who} {verb} the top {amount} card(s) of {whose} library "
            "(setting them aside to choose from; nothing moves)"
        )
    if kind is EffectKind.ADD_COUNTERS:
        split = " divided among" if effect.divided else " on"
        return f"put {amount} {_counter(effect)} counter(s){split} {objects}"
    if kind is EffectKind.REMOVE_COUNTERS:
        return f"remove {amount} {_counter(effect)} counter(s) from {objects}"
    if kind is EffectKind.PROLIFERATE:
        return "proliferate"
    if kind is EffectKind.PAY_COST:
        return f"{who} pays {effect.pay_cost}"
    if kind is EffectKind.START_ENGINES:
        return f"{who} starts their engines (speed becomes 1 if it is 0)"
    if kind is EffectKind.CREATE_TOKEN:
        return f"{who} creates {amount} {_token(effect.token)} token(s)"
    if kind is EffectKind.ADD_MANA:
        return _add_mana(effect, who, amount)
    digital = _digital(effect, who, amount, objects)
    if digital is not None:
        return digital
    return None


def _digital(effect: Effect, who: str, amount: str, objects: str) -> str | None:
    """MTG Arena's seek, conjure and perpetually, from the opcodes."""
    kind = effect.kind
    if kind is EffectKind.SEEK:
        what = _filter(effect.targets) if effect.targets is not None else "card"
        return (
            f"{who} seeks {amount} (at random from their library, among: {what})"
            f" into {_zone((Zone.HAND if effect.zone is None else effect.zone))}"
        )
    if kind is EffectKind.CONJURE:
        from ..rules.cr700_additional_rules.digital_mechanics import conjured_name

        if conjured_name(effect):
            what = f"{amount} card(s) named {conjured_name(effect)}"
        elif "duplicate" in effect.keywords:
            what = f"{amount} duplicate(s) (with perpetual changes) of {objects}"
        else:
            what = f"{amount} card(s) that are the same card as {objects}"
        where = _zone((Zone.HAND if effect.zone is None else effect.zone))
        if "tapped" in effect.keywords:
            where += " tapped"
        if not (effect.amount2.is_constant and not effect.amount2.constant):
            where += f", {effect.amount2} from the top"
        return f"{who} conjures {what} into {where}"
    if kind is EffectKind.PERPETUALLY:
        from ..rules.cr700_additional_rules.digital_mechanics import PERPETUAL_KINDS

        if "at random" in effect.keywords:
            objects = f"{objects}, picked at random"
        changes = [
            _continuous(node, objects) or f"[{node.kind.name.lower()}]"
            for node in effect.walk()
            if node.kind in PERPETUAL_KINDS
        ]
        body = ", and ".join(changes) or f"{objects} (no change)"
        duration = _duration(effect.duration)
        return f"perpetually (in every zone, for the rest of the game): {body}{duration}"
    return None


def _token(token) -> str:
    """The token, with how it arrives - tapped, attacking, or as a copy."""
    if token is None:
        return "token"
    text = str(token)
    if token.copy_of is not None:
        text = f"copy of {_filter(token.copy_of)}"
    riders = [
        word
        for word, flag in (
            ("tapped", token.enters_tapped),
            ("attacking", token.enters_attacking),
        )
        if flag
    ]
    return f"{text} ({' and '.join(riders)})" if riders else text


def _add_mana(effect: Effect, who: str, amount: str) -> str:
    """"Add {G}{U}", "add one mana of the chosen color", "... for each Swamp".

    Every field here is one the executor reads: the chosen colour, the
    commander's identity narrowing "any color", the repeat count a "for each"
    puts in ``amount2``, and a spending restriction.
    """
    symbols = "".join(effect.mana_produced)
    if symbols:
        text = f"{who} adds {symbols}"
        repeat = effect.amount2
        if not repeat.is_constant or repeat.constant:
            text += f" {_amount(repeat)} time(s)"
    elif effect.colors_chosen:
        text = f"{who} adds {amount} mana of the chosen color"
    elif effect.colors:
        menu = _colors(effect.colors)
        if effect.colors_in_commander_identity:
            menu += " in your commander's color identity"
        text = f"{who} adds {amount} mana of {menu}"
    else:
        text = f"{who} adds {amount} colorless mana"
    if effect.mana_restriction is not None:
        key = getattr(effect.mana_restriction, "key", "") or "a restricted use"
        text += f" (spend only on {key})"
    return text


def _continuous(effect: Effect, objects: str) -> str | None:
    kind = effect.kind
    duration = _duration(effect.duration)
    if kind is EffectKind.MODIFY_PT:
        return f"{objects} gets {_signed(effect.amount)}/{_signed(effect.amount2)}{duration}"
    if kind is EffectKind.SET_PT:
        return f"{objects} becomes {effect.amount}/{effect.amount2}{duration}"
    if kind is EffectKind.SWITCH_PT:
        return f"{objects} has power and toughness switched{duration}"
    if kind is EffectKind.GRANT_ABILITY:
        granted = ", ".join(_granted(a) for a in effect.granted_abilities)
        granted = granted or ", ".join(effect.keywords)
        return f"{objects} gains {granted or 'an ability'}{duration}"
    if kind is EffectKind.REMOVE_ABILITIES:
        named = [name for name in effect.keywords if name != "*"]
        if named and "*" not in effect.keywords:
            return f"{objects} loses {', '.join(named)}{duration}"
        return f"{objects} loses all abilities{duration}"
    if kind is EffectKind.SET_COLOR:
        return f"{objects} becomes {effect.colors}{duration}"
    if kind in (EffectKind.ADD_TYPE, EffectKind.SET_TYPE):
        verb = "becomes" if kind is EffectKind.SET_TYPE else "is also"
        return f"{objects} {verb} {effect.types}{duration}"
    if kind is EffectKind.REMOVE_TYPE:
        return f"{objects} loses {effect.types}{duration}"
    if kind in (EffectKind.RESTRICTION, EffectKind.PERMISSION):
        rules = ", ".join(_act(r) for r in effect.restrictions)
        if not rules and effect.keywords:
            # A keyword the payment step consults (Convoke, Improvise): the
            # keyword is the whole of what the engine reads.
            rules = f"help pay by {', '.join(effect.keywords)}"
        rules = rules or "act"
        verb = "can't" if kind is EffectKind.RESTRICTION else "may"
        return f"{objects} {verb} {rules}{duration}"
    if kind is EffectKind.MODIFY_COST:
        return f"{objects} costs {_signed(effect.amount)} to cast{duration}"
    if kind is EffectKind.REPLACEMENT:
        if effect.replacement_kind:
            return f"replacement - {_replacement(effect)}{_gated(effect)}{duration}"
        if not effect.children:
            # Kind zero with nothing to do instead is never registered
            # (``static_replacements`` skips it): the ability is inert.
            return "replacement of a shape the engine has no kind for (does nothing)"
        return f"replacement - instead, {_effects(effect.children)}{duration}"
    return None


def _replacement(effect: Effect) -> str:
    """A replacement effect, from every field that decides which events it
    catches - the recipient, the source, the counter kind, the actor, combat
    or not - so a dropped qualifier shows as missing rather than implied."""
    from ..rules.cr600_spells_and_abilities.cr614_replacement import ReplacementKind

    kind = ReplacementKind(effect.replacement_kind)
    scaled = _scaled(effect)
    if kind is ReplacementKind.MODIFY_COUNTERS:
        what = f"{effect.counter_type} counters" if effect.counter_type else "counters of any kind"
        by = ""
        if effect.actor is not None:
            by = f" by {_players(effect.actor)}"
        elif "an effect" in effect.keywords:
            by = " by an effect"
        return f"{what} put{by} on {_filter(effect.targets)} become {scaled}"
    if kind is ReplacementKind.MODIFY_TOKENS:
        what = f"{_filter(effect.targets)} tokens" if effect.targets else "tokens"
        whose = f" for {_players(effect.players)}" if effect.players else " for any player"
        return f"{what} created{whose} become {scaled}"
    if kind in (ReplacementKind.MODIFY_DAMAGE, ReplacementKind.PREVENT_DAMAGE):
        return f"{_damage_event(effect)} becomes {scaled}"
    if kind is ReplacementKind.MODIFY_LIFE_CHANGE:
        return f"life gained by {_players(effect.players)} becomes {scaled}"
    if kind is ReplacementKind.MODIFY_LIFE_LOSS:
        return f"life lost by {_players(effect.players)} becomes {scaled}"
    if kind is ReplacementKind.LIFE_GAIN_BECOMES_LOSS:
        return f"life {_players(effect.players)} would gain is lost instead"
    if kind is ReplacementKind.REDIRECT_ZONE_CHANGE:
        where = f" to {_zone(effect.event_zone)}" if effect.event_zone else ""
        origin = f" from {_zone(effect.from_zone)}" if effect.from_zone else ""
        return (
            f"{_filter(effect.targets)} that would move{origin}{where} "
            f"goes to {_zone(effect.zone)} instead"
        )
    if kind in (ReplacementKind.ENTERS_TAPPED, ReplacementKind.ENTERS_UNTAPPED):
        state = "tapped" if kind is ReplacementKind.ENTERS_TAPPED else "untapped"
        return f"{_filter(effect.targets)} enter {state}"
    return f"[{kind.name.lower()}] {scaled}"


def _damage_event(effect: Effect) -> str:
    """"combat damage dealt by <source> to <recipient>"."""
    what = "damage"
    if "combat" in effect.keywords:
        what = "combat damage"
    elif "noncombat" in effect.keywords:
        what = "noncombat damage"
    by = f" by {_filter(effect.damage_source)}" if effect.damage_source else ""
    recipients = []
    if effect.targets is not None:
        recipients.append(_filter(effect.targets))
    if effect.players is not None:
        recipients.append(_players(effect.players))
    to = f" to {' or '.join(recipients)}" if recipients else ""
    if effect.both_ways:
        return f"{what} dealt to or by {_filter(effect.targets)}"
    return f"{what} dealt{by}{to}"


def _gated(effect: Effect) -> str:
    return "" if effect.condition.is_always else f" (only while {effect.condition})"


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


def _granted(ability) -> str:
    if isinstance(ability, Ability):
        if ability.keyword and ability.keyword.lower() in ("protection", "hexproof from"):
            # CR 702.16a: the quality is the keyword. "Protection" alone
            # hid whether the parse had kept "from red" or dropped it.
            name = "Protection" if ability.keyword.lower() == "protection" else "Hexproof"
            if ability.quality is None:
                return f"{name} from everything"
            return f"{name} from {_quality(ability.quality)}"
        if ability.keyword and ability.kind is AbilityKind.ACTIVATED:
            return f"{ability.keyword} ({explain_ability(ability).rstrip('.')})"
        if ability.keyword and ability.kind is AbilityKind.TRIGGERED:
            return f"{ability.keyword} ({explain_ability(ability).rstrip('.')})"
        return ability.keyword or explain_ability(ability).rstrip(".")
    return str(ability)


def _quality(spec: ObjectFilter) -> str:
    """A protection quality: a colour, a type, "each color"."""
    if spec.must_be_coloured:
        return "each color"
    if spec.must_be_colorless:
        return "colorless"
    return spec.describe(quantified=False)


def _act(restriction) -> str:
    return getattr(restriction, "act_phrase", None) or str(restriction)


def _scaled(effect: Effect) -> str:
    """How a replacement changes a number: "twice that many, plus one"."""
    parts = []
    if effect.multiplier != 1:
        parts.append(
            "twice that many"
            if effect.multiplier == 2
            else f"{effect.multiplier} times that many"
        )
    else:
        parts.append("that many")
    extra = effect.amount.constant
    if extra > 0:
        parts.append(f"plus {extra}")
    elif extra < 0:
        parts.append(f"minus {-extra}")
    return " ".join(parts)


def _colors(colors) -> str:
    """Colour names, not the integer behind the flag.

    ``Color`` is an IntFlag, so an f-string prints "31" for WUBRG - which read
    as "add 1 mana of 31" and told a reviewer nothing at all.
    """
    if not colors:
        return "any color"
    names = [c.name.lower() for c in colors]
    if len(names) == 5:
        return "any color"
    return " or ".join(names)


def _counter(effect: Effect) -> str:
    return effect.counter_type or "+1/+1"


def _subject(effect: Effect) -> str:
    if effect.players is not None:
        return _who(effect)
    return "you"


def _who(effect: Effect) -> str:
    """The player an effect names, as the engine will resolve it.

    A "target" scope on an effect that chose no target is not a target at
    all: the resolver answers it from the scope alone, which for "target
    player" is nobody and for "target opponent" is every opponent. Printing
    it as "target player" made that parse read exactly like a real target.
    """
    from ..rules.kernel.query import PlayerScope

    players = effect.players
    text = _players(players)
    if (
        players is not None
        and players.scope in (PlayerScope.TARGET_PLAYER, PlayerScope.TARGET_OPPONENT)
        and not effect.is_targeted
    ):
        text += " (not targeted)"
    return text


def _mode_choice(effect: Effect) -> str:
    """"choose one", "choose up to two", "choose two (a mode may repeat)"."""
    count = _amount(effect.amount) if not (
        effect.amount.is_constant and effect.amount.constant in (0, 1)
    ) else "one"
    text = f"choose {'up to ' if effect.modes_up_to else ''}{count}"
    if effect.mode_weights and any(w != 1 for w in effect.mode_weights):
        text += f" (mode costs {list(effect.mode_weights)})"
    if effect.modes_may_repeat:
        text += " (a mode may be chosen more than once)"
    return text


def _functions_in(zones) -> str:
    """Zones other than the default battlefield an ability works from."""
    from ..rules.cr600_spells_and_abilities.abilities import (
        BATTLEFIELD_ONLY,
        STACK_ONLY,
    )

    if not zones or zones in (BATTLEFIELD_ONLY, STACK_ONLY):
        return ""
    return ", ".join(sorted(_zone(zone) for zone in zones))


def _players(players: PlayerFilter | None) -> str:
    return str(players) if players is not None else "you"


def _objects(effect: Effect) -> str:
    """What the effect acts on, objects or players.

    An effect with no object filter is not automatically about its own source.
    "This creature deals 3 damage to each opponent" puts its recipients in
    ``players``, and rendering that as "this permanent" told a reviewer the
    card burned itself - a false alarm on a correct parse, which costs exactly
    as much trust as a missed one.
    """
    if effect.targets is None:
        if effect.players is not None:
            return _players(effect.players)
        return "this permanent"
    if effect.is_targeted:
        # "target" already says how many; the filter's own quantity word would
        # produce "target all creature".
        described = f"target {effect.targets.describe(quantified=False)}"
    else:
        described = _filter(effect.targets)
    if effect.players is not None and effect.targets.includes_players:
        return f"{described} or {_players(effect.players)}"
    return described


def _filter(spec: ObjectFilter | None) -> str:
    return spec.describe() if spec is not None else "it"


def _amount(value: Value) -> str:
    return str(value)


def _signed(value: Value) -> str:
    text = str(value)
    return text if text.startswith(("+", "-")) else f"+{text}"


def _zone(zone: Zone | None) -> str:
    return zone.name.lower().replace("_", " ") if zone is not None else "somewhere"


def _duration(duration: int) -> str:
    if duration in (Duration.PERMANENT, Duration.WHILE_SOURCE_PERSISTS):
        return ""
    names = {
        Duration.END_OF_TURN: " until end of turn",
        Duration.END_OF_COMBAT: " until end of combat",
        Duration.YOUR_NEXT_TURN: " until your next turn",
        Duration.END_OF_YOUR_NEXT_TURN: " until the end of your next turn",
        Duration.CUSTOM: " (for a stated duration)",
    }
    return names.get(Duration(duration), "")
