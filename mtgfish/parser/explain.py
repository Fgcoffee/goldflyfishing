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
        return f"{when}, {body}." if body else f"{when}, (nothing)."

    if ability.kind is AbilityKind.ACTIVATED:
        cost = str(ability.cost) if ability.cost.components else "no cost"
        parts = [f"Pay {cost}"]
        if ability.timing is Timing.SORCERY:
            parts.append("(sorcery speed only)")
        if ability.once_each_turn:
            parts.append("(once each turn)")
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
    if trigger.chapter:
        parts.append(f"(chapter {trigger.chapter})")
    if trigger.once_each_turn:
        parts.append("(once each turn)")
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
    EffectKind.PUT_ON_LIBRARY: "put on top of library",
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
        return f"later - {_trigger(effect.trigger)}, {_effects(effect.children)}"
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
    if effect.players is not None and effect.kind in _PLAYER_ACTED_VERBS:
        return f"{_players(effect.players)} {_PLAYER_ACTED_VERBS[effect.kind]} {objects}"
    return f"{verb} {objects}"


def _control_flow(effect: Effect) -> str | None:
    kind = effect.kind
    if kind is EffectKind.SEQUENCE:
        return _effects(effect.children)
    if kind is EffectKind.OPTIONAL:
        return f"you may {_effects(effect.children)}"
    if kind is EffectKind.CONDITIONAL:
        text = f"if {effect.condition}, {_effects(effect.children)}"
        if effect.otherwise:
            text += f"; otherwise {_effects(effect.otherwise)}"
        return text
    if kind is EffectKind.REPEAT:
        return f"do this {_amount(effect.amount)} times: {_effects(effect.children)}"
    if kind is EffectKind.UNLESS_PAYS:
        who = _players(effect.players)
        return (
            f"{_effects(effect.children)}, unless {who} pays {effect.pay_cost}"
        )
    if kind is EffectKind.CHOOSE_MODE:
        modes = "; or ".join(
            _effects(child.children) or _effect(child) for child in effect.children
        )
        return f"choose one - {modes}"
    if kind is EffectKind.NOTHING:
        return ""
    if kind is EffectKind.UNPARSED:
        return "(NOT UNDERSTOOD - inert)"
    return None


def _one_shot(effect: Effect, who: str, amount: str, objects: str) -> str | None:
    kind = effect.kind
    if kind is EffectKind.DAMAGE:
        return f"deal {amount} damage to {objects}"
    if kind is EffectKind.PREVENT_DAMAGE:
        # -1 is the "all" sentinel the executor reads; printing it as a number
        # made a Fog read like a card that heals one damage.
        how_much = "all" if effect.amount.constant < 0 else amount
        what = "combat damage" if "combat" in effect.keywords else "damage"
        if effect.targets is None and effect.players is None:
            return f"prevent {how_much} {what}"
        return f"prevent {how_much} {what} to {objects}"
    if kind is EffectKind.SEARCH_LIBRARY:
        where = f" and puts it into {_zone(effect.zone)}" if effect.zone else ""
        tapped = " tapped" if "tapped" in effect.keywords else ""
        return (
            f"{who} searches their library for {_filter(effect.targets)}"
            f"{where}{tapped}"
        )
    if kind is EffectKind.MOVE_ZONE:
        origin = f"from {_zone(effect.from_zone)} " if effect.from_zone else ""
        return f"move {objects} {origin}to {_zone(effect.zone)}"
    if kind is EffectKind.ADD_COUNTERS:
        return f"put {amount} {_counter(effect)} counter(s) on {objects}"
    if kind is EffectKind.REMOVE_COUNTERS:
        return f"remove {amount} {_counter(effect)} counter(s) from {objects}"
    if kind is EffectKind.PROLIFERATE:
        return "proliferate"
    if kind is EffectKind.PAY_COST:
        return f"{who} pays {effect.pay_cost}"
    if kind is EffectKind.START_ENGINES:
        return f"{who} starts their engines (speed becomes 1 if it is 0)"
    if kind is EffectKind.CREATE_TOKEN:
        return f"{who} creates {amount} {effect.token or 'token'} token(s)"
    if kind is EffectKind.ADD_MANA:
        symbols = "".join(effect.mana_produced)
        if symbols:
            return f"{who} adds {symbols}"
        return f"{who} adds {amount} mana of {_colors(effect.colors)}"
    return None


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
        return f"{objects} loses all abilities{duration}"
    if kind is EffectKind.SET_COLOR:
        return f"{objects} becomes {effect.colors}{duration}"
    if kind in (EffectKind.ADD_TYPE, EffectKind.SET_TYPE):
        verb = "becomes" if kind is EffectKind.SET_TYPE else "is also"
        return f"{objects} {verb} {effect.types}{duration}"
    if kind is EffectKind.REMOVE_TYPE:
        return f"{objects} loses {effect.types}{duration}"
    if kind in (EffectKind.RESTRICTION, EffectKind.PERMISSION):
        rules = ", ".join(_act(r) for r in effect.restrictions) or "act"
        verb = "can't" if kind is EffectKind.RESTRICTION else "may"
        return f"{objects} {verb} {rules}{duration}"
    if kind is EffectKind.MODIFY_COST:
        return f"{objects} costs {_signed(effect.amount)} to cast{duration}"
    if kind is EffectKind.REPLACEMENT:
        if effect.replacement_kind:
            what = {
                7: f"{_counter(effect)} counters put on {objects}",
                8: "cards drawn",
                13: "tokens created",
                5: "damage dealt",
            }.get(effect.replacement_kind, "the amount")
            return f"replacement - {what} become {_scaled(effect)}"
        return f"replacement - instead, {_effects(effect.children)}{duration}"
    return None


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


def _granted(ability) -> str:
    if isinstance(ability, Ability):
        return ability.keyword or explain_ability(ability).rstrip(".")
    return str(ability)


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
        return _players(effect.players)
    return "you"


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
