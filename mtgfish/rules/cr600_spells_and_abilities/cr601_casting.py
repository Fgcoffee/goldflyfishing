"""Casting spells and activating abilities (CR 601, 602, 605).

CR 601.2 lays out casting as a strict sequence, and the order is not
decorative:

  a. move the card to the stack
  b. choose modes, and how to divide or distribute
  c. choose targets
  d. divide any division among targets
  e. (choices that depend on targets)
  f. determine the total cost
  g. activate mana abilities
  h. pay the total cost
  i. the spell has been cast

Targets are chosen *before* the cost is known (c before f), which is why a
spell whose cost depends on its target is possible at all. Mana abilities are
activated only after the cost is known (g after f), which is why you cannot be
forced to float mana you turn out not to need. And if any step cannot be
completed, the whole cast is rewound as though it never happened (CR 601.2h) -
not partially undone.

Mana abilities are the other trap: they do not use the stack, cannot be
responded to, and may be activated during cost payment (CR 605.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..cr100_game_concepts.cr106_mana import ManaCost, find_payment
from ..cr100_game_concepts.cr118_costs import (
    EXILE_ZONES,
    CostComponent,
    CostKind,
    TotalCost,
    required_amount,
)
from ..kernel.enums import Zone
from ..kernel.events import Event, EventKind
from ..kernel.gameobject import GameObject
from ..kernel.ids import PlayerId
from .abilities import Ability, AbilityKind
from .effects import Effect, EffectKind

if TYPE_CHECKING:
    from ..cr100_game_concepts.cr117_priority import Action
    from ..kernel.game import Game


class CastError(RuntimeError):
    """A cast or activation that could not legally be completed.

    Raised rather than returned because CR 601.2h requires the whole attempt to
    be rewound, and an exception is the honest way to say "none of that
    happened".
    """


# ---------------------------------------------------------------------------
# Playing a land (CR 305, 116.2a)
# ---------------------------------------------------------------------------


def play_land(game: Game, player_id: PlayerId, action: Action) -> bool:
    """Play a land (CR 305.1).

    A special action: it uses no stack, cannot be responded to, and happens
    immediately. Legal only when the player has priority during their own main
    phase with an empty stack, and only within their land allowance.
    """
    player = game.player(player_id)
    obj = game.objects.get(action.source)
    if obj is None:
        raise CastError("no such card")
    if player.lands_played >= player.max_lands:
        raise CastError("land drop already used this turn")

    permanent = game.move_object(obj, Zone.BATTLEFIELD, to_player=player_id)
    permanent.controller = player_id
    # CR 712.12: an MDFC played as a land enters with the chosen face up.
    permanent.face_index = action.face_index
    game.invalidate_characteristics()
    player.lands_played += 1
    game.log.record(game, f"{player.name} plays {permanent}", kind="land", player=player_id)
    game.emit(Event(EventKind.LAND_PLAYED, object_id=permanent.id, player=player_id))
    return True


# ---------------------------------------------------------------------------
# Casting (CR 601.2)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CastAttempt:
    """Bookkeeping for a cast in progress, so it can be rewound cleanly."""

    game: Game
    player: PlayerId
    original_zone: Zone
    original_owner: PlayerId
    stack_object: GameObject | None = None
    paid_mana: bool = False


#: Keywords whose alternative cost casts the card face down (CR 702.36a morph,
#: 702.37a megamorph, 702.168a disguise). Cloak and manifest put cards onto the
#: battlefield face down without casting, so they are not here.
FACE_DOWN_CAST_KEYWORDS = frozenset({"Morph", "Megamorph", "Disguise"})


def _is_face_down_cast(game: Game, spell: GameObject, action: Action) -> bool:
    if action.alternative_cost < 0:
        return False
    alternatives = game.characteristics(spell).alternative_costs
    if action.alternative_cost >= len(alternatives):
        return False
    return alternatives[action.alternative_cost].keyword in FACE_DOWN_CAST_KEYWORDS


def cast_spell(game: Game, player_id: PlayerId, action: Action) -> GameObject:
    """Cast a spell, following CR 601.2 exactly."""
    card_object = game.objects.get(action.source)
    if card_object is None:
        raise CastError("no such card")

    origin_zone = card_object.zone
    from_command_zone = origin_zone is Zone.COMMAND

    # 601.2a: move the card to the stack. It becomes a spell there.
    spell = game.move_object(card_object, Zone.STACK, to_player=player_id)
    spell.controller = player_id
    spell.face_index = action.face_index

    # CR 702.36a: a morph spell is cast *face down*, as a 2/2 with no
    # characteristics of its own. That has to be true from the moment it hits
    # the stack, because CR 708.2 says the face-down spell's characteristics
    # are what everything - cost, targets, "can't be countered" - is judged
    # against.
    if _is_face_down_cast(game, spell, action):
        spell.face_down = True
        spell.invalidate()
        game.invalidate_characteristics()

    try:
        # 601.2b: choose modes and X. The caller may have announced the modes
        # already; when it has not, the controller is asked, because a modal
        # spell whose modes nobody chose does nothing at all (CR 700.2).
        spell.chosen_modes = choose_modes(
            game, spell, spell_effects(game, spell), player_id,
            announced=action.mode_choices,
        )
        spell.x_value = action.x_value
        # CR 601.2b: optional additional costs are chosen here, and whether
        # they were is a fact the spell carries for the rest of its life -
        # kicker riders read it on resolution.
        spell.additional_costs_paid = tuple(action.additional_costs)

        # 601.2c-d: choose targets, and divide anything that needs dividing.
        # The caller may have chosen them already - a scripted test does, and
        # so does a bot that has a plan. When it has not, the controller is
        # asked here, because this is the moment the rules say the choice
        # happens and because a spell whose targets nobody chose can never be
        # cast at all.
        chosen = action.targets or _ask_for_targets(game, spell, player_id)
        _validate_targets(game, spell, chosen)
        spell.targets = chosen
        _announce_targets(game, spell, player_id)

        # 601.2f: determine the total cost - only now, after targets are set.
        total = compute_total_cost(game, spell, player_id, action, from_command_zone)

        # CR 118.6: an unpayable cost cannot be paid however it is modified.
        if not total.is_payable:
            raise CastError("this spell has no mana cost, so its cost is unpayable")

        # 601.2g-h: activate mana abilities, then pay. A spell being cast
        # "without paying its mana cost" skips only the *mana*: CR 601.2h
        # still charges every additional cost, which is why a cascaded spell
        # with an additional sacrifice still needs something to sacrifice.
        if spell.cast_without_paying:
            total.base = ManaCost(())
            total.increase = 0
            total.reduction = 0
            total.unpayable = False
        _pay(game, player_id, total, spell)
        # CR 601.2g: what was actually spent. Zero means the spell was free,
        # which several cards key off - and which is not the same as a cost
        # that happened to be reduced to nothing on a card with no mana cost.
        spell.mana_spent = 0 if spell.cast_without_paying else total.base.mana_value

    except CastError:
        # 601.2h: an incomplete cast is rewound entirely.
        game.log.record(game, "Cast rewound; the game state is unchanged", kind="rewind")
        game.move_object(spell, origin_zone, to_player=card_object.owner)
        raise

    if spell.is_commander and from_command_zone:
        # CR 903.8: only casts from the command zone increase the tax.
        game.player(player_id).record_commander_cast(game.commander_identity(spell.id))
        game.emit(
            Event(EventKind.COMMANDER_CAST, object_id=spell.id, player=player_id)
        )

    game.log.record(game, f"{game.player(player_id).name} casts {spell}", kind="cast",
                    player=player_id)
    # CR 731.2 counts spells cast during a turn to decide the day/night flip.
    game.spells_cast_this_turn += 1
    # 601.2i: the spell has been cast; only now do cast triggers fire.
    game.emit(Event(EventKind.CAST_SPELL, object_id=spell.id, player=player_id))
    return spell


def compute_total_cost(
    game: Game,
    spell: GameObject,
    player_id: PlayerId,
    action: Action,
    from_command_zone: bool = False,
) -> TotalCost:
    """CR 601.2f: mana cost, plus additional costs and increases, then reductions.

    The order matters. Applying reductions before increases would let a
    "spells cost {1} more" tax be dodged by a "costs {1} less" effect that
    should have run out first.
    """
    chars = game.characteristics(spell)

    # CR 601.2b: the alternative cost is chosen when the spell is proposed,
    # before the total is worked out. CR 118.9a allows at most one.
    alternative = None
    available = chars.alternative_costs
    if 0 <= action.alternative_cost < len(available):
        alternative = available[action.alternative_cost]

    if alternative is not None:
        # CR 118.9c: this replaces what must be *paid*, not the spell's mana
        # cost. Anything that later asks for the mana cost still sees the
        # printed one.
        total = TotalCost(
            base=alternative.cost.mana_component,
            x_value=action.x_value,
            is_alternative=True,
        )
        for component in alternative.cost.non_mana_components:
            total.add_additional(component)
    else:
        total = TotalCost(base=chars.mana_cost, x_value=action.x_value)
        # CR 118.6: no mana cost at all is an *unpayable* cost, not zero. Only
        # an alternative cost can make such a spell castable (CR 118.6a).
        total.unpayable = not chars.has_mana_cost

    # CR 118.8a: any number of additional costs, mandatory ones always, and
    # optional ones as announced. CR 118.9d applies them to the alternative
    # cost too, so an alternative cost is never a way to dodge a tax.
    for index, additional in enumerate(chars.additional_costs):
        if additional.optional and index not in action.additional_costs:
            continue
        total.base = total.base.plus(additional.cost.mana_component)
        for component in additional.cost.non_mana_components:
            total.add_additional(component)

    if from_command_zone and spell.is_commander:
        # CR 903.8: {2} for each previous cast of this commander from the
        # command zone.
        total.increase += game.player(player_id).commander_tax(
            game.commander_identity(spell.id)
        )

    for amount in cost_increases(game, spell, player_id):
        total.increase += amount
    for amount in cost_reductions(game, spell, player_id):
        total.reduction += amount

    return total


def _matches_being_cast(game, spell, effect, obj) -> bool:
    """Whether a cost modification applies to the spell about to be cast.

    The filter comes from a phrase like "artifact spells you cast", and the
    word "spells" pins it to the stack - but the cost is worked out while the
    card is still in hand, so the zone check rejected everything and every
    cost modifier in the format did nothing. The zone is dropped here: a
    modification is about what is being cast, not about where it is standing
    while the question is asked.
    """
    from dataclasses import replace as _replace

    from ..kernel.matching import matches

    if effect.targets is None:
        return True
    spec = _replace(effect.targets, zones=frozenset())
    return matches(game, spell, spec, source=obj.id, controller=obj.controller)


def _cost_deltas(game: Game, spell: GameObject, player_id: PlayerId) -> list[int]:
    """Every cost modification that applies to this spell, as signed amounts.

    Evaluated rather than read off ``amount.constant``, because the amount is
    frequently a count of something: "costs {1} less for each artifact you
    control". A COUNT value has a constant of zero, so reading the constant
    dropped every dynamic modifier in the game - Affinity among them - from
    both the reduction path and the increase path at once.

    Negative is cheaper, matching the rest of the engine.
    """
    from ..kernel.values import evaluate
    from .effects import EffectKind

    out: list[int] = []
    # The spell itself is included, not just the battlefield. Affinity,
    # Improvise and every "this spell costs {1} less for each ..." are static
    # abilities *of the card being cast*, which is in hand or on the stack -
    # so a scan of permanents saw none of them and they did nothing at all.
    for obj in [*game.permanents(), spell]:
        for ability in game.characteristics(obj).abilities:
            if ability.kind is not AbilityKind.STATIC or ability.unparsed:
                continue
            for effect in ability.effects:
                if effect.kind is not EffectKind.MODIFY_COST:
                    continue
                if not _matches_being_cast(game, spell, effect, obj):
                    continue
                amount = evaluate(
                    game, effect.amount, source=obj.id, controller=obj.controller
                )
                if amount:
                    out.append(amount)
    return out


def cost_increases(game: Game, spell: GameObject, player_id: PlayerId) -> list[int]:
    """Generic mana added by "spells cost {N} more" effects."""

    return [amount for amount in _cost_deltas(game, spell, player_id) if amount > 0]


def cost_reductions(game: Game, spell: GameObject, player_id: PlayerId) -> list[int]:
    return [
        -amount for amount in _cost_deltas(game, spell, player_id) if amount < 0
    ]


# ---------------------------------------------------------------------------
# Modes (CR 700.2)
# ---------------------------------------------------------------------------


def modal_effect(effects) -> Effect | None:
    """The modal instruction among these effects, or None if there is none.

    CR 700.2: the modes are the children of that instruction. The first one
    found is the object's mode choice: the resolver runs one list of chosen
    indices, so one list is what is chosen.
    """
    for effect in effects:
        for node in effect.walk():
            if node.kind is EffectKind.CHOOSE_MODE and node.children:
                return node
    return None


def legal_modes(
    game: Game, source: GameObject | None, modal: Effect, player_id: PlayerId
) -> list[int]:
    """Which modes may be chosen (CR 700.2a, 700.2b).

    A mode whose targets cannot all be supplied is off the menu entirely -
    it is not chosen and then fizzled, it cannot be chosen at all.
    """
    chosen: list[int] = []
    for index, mode in enumerate(modal.children):
        if _mode_is_choosable(game, source, mode, player_id):
            chosen.append(index)
    return chosen


def _mode_is_choosable(
    game: Game, source: GameObject | None, mode: Effect, player_id: PlayerId
) -> bool:
    for node in mode.walk():
        if not node.is_targeted:
            continue
        if node.targets is not None and (node.targets.up_to or node.targets.includes_players):
            # "Up to one target" is satisfiable with none, and a player is
            # always there to be targeted (CR 115.4).
            continue
        if source is not None:
            if not _candidates_for(game, source, node, player_id):
                return False
            continue
        # The source is gone and nothing kept it - a dies-trigger outlives its
        # creature - so there is no protection to check against it and the
        # filters alone decide.
        from ..kernel.matching import find

        if node.targets is None:
            if not _targetable_players(game, node.players, player_id):
                return False
            continue
        if not find(game, node.targets, controller=player_id):
            return False
    return True


def mode_budget(modal: Effect) -> int:
    """How much this instruction may spend on modes (CR 700.2, 700.2i).

    "Choose one" is the overwhelming majority and the default; an instruction
    that asks for a fixed larger number says so in its amount, and a pawprint
    spell's "up to five {P} worth" says five.
    """
    amount = modal.amount
    if amount.is_constant and amount.constant > 0:
        return amount.constant
    return 1


def mode_weight(modal: Effect, index: int) -> int:
    """What choosing this mode costs against the budget (CR 700.2i).

    One, unless the card printed pawprints. An ordinary bulleted mode and a
    single-pawprint mode are the same thing, which is why this is one
    mechanism and not two.
    """
    weights = modal.mode_weights
    if index < len(weights) and weights[index] > 0:
        return weights[index]
    return 1


#: Retained under its old name: "count" was right while every mode cost one.
def mode_count(modal: Effect) -> int:
    return mode_budget(modal)


def choose_modes(
    game: Game,
    source: GameObject | None,
    effects,
    player_id: PlayerId,
    *,
    announced: tuple[int, ...] = (),
    source_id: int = 0,
) -> tuple[int, ...]:
    """Choose the mode(s) of a modal object as it goes on the stack.

    One implementation for all three routes into the stack: a spell announced
    at CR 601.2b, an activated ability at CR 602.2b, and a triggered ability
    at CR 603.3c. They differ only in what the caller does with an empty
    result, which means no mode could legally be chosen (CR 700.2b).

    ``announced`` is a choice the caller already made - a scripted action, or
    a bot with a plan. It is filtered for legality like any other, because
    CR 700.2a forbids an illegal mode however it was arrived at.
    """
    modal = modal_effect(effects)
    if modal is None:
        return ()
    available = legal_modes(game, source, modal, player_id)
    if not available:
        return ()

    budget = mode_budget(modal)
    if announced:
        return _cleaned_modes(announced, available, modal, budget)

    agent = game.agent_for(player_id)
    chooser = getattr(agent, "choose_modes", None)
    if chooser is None:
        # No agent, or one that predates this hook. A mode has to be chosen
        # where a legal one exists, so the engine makes the deterministic
        # choice itself rather than letting the object resolve as a no-op.
        return _cleaned_modes((), available, modal, budget)
    # The agent is handed each choosable mode with its index, its effect and
    # what it costs against the budget (CR 700.2i), so it can weigh both what
    # the mode does and what it is spending; the index is what it returns.
    options = [
        (index, modal.children[index], mode_weight(modal, index))
        for index in available
    ]
    identifier = source.id if source is not None else source_id
    return _cleaned_modes(
        chooser(game, player_id, identifier, options, budget),
        available,
        modal,
        budget,
    )


def _cleaned_modes(
    picked, available: list[int], modal: Effect, budget: int
) -> tuple[int, ...]:
    """Whatever was asked for, reduced to a legal choice.

    CR 700.2i makes this a budget rather than a count: each mode costs its
    pawprint weight, the total may not exceed the budget, and CR 700.2d
    decides whether a mode may be taken twice - normally not, unless the card
    says so in so many words.

    An illegal, repeated or unaffordable choice is dropped rather than obeyed.
    A choice that comes up short is topped up only when the instruction is not
    "up to": CR 700.2 requires a mode to be chosen where a legal one exists,
    while CR 700.2i's ceiling may be left unspent.
    """
    legal = set(available)
    kept: list[int] = []
    spent = 0
    for index in picked or ():
        if index not in legal:
            continue
        if not modal.modes_may_repeat and index in kept:
            continue
        cost = mode_weight(modal, index)
        if spent + cost > budget:
            continue
        kept.append(index)
        spent += cost
    if modal.modes_up_to:
        return tuple(kept)
    for index in available:
        if not modal.modes_may_repeat and index in kept:
            continue
        cost = mode_weight(modal, index)
        if spent + cost > budget:
            continue
        kept.append(index)
        spent += cost
    return tuple(kept)


def targeted_nodes(effects, chosen_modes: tuple[int, ...] | None = None) -> list:
    """Targeted effect nodes, in the order the resolver will reach them.

    Order is the contract: targets are supplied as one tuple per targeting
    effect and handed out in execution order, so a slot kept for a mode that
    never runs would shift every later effect onto the wrong target.
    CR 700.2c: an unchosen mode's targets are not chosen at all, so its nodes
    are left out.

    ``None`` means no choice has been made yet - which is the question
    legality asks of a card in hand - and then every mode is included.
    """
    out: list = []

    def visit(node) -> None:
        if node.kind is EffectKind.CHOOSE_MODE and node.children and chosen_modes is not None:
            for index in chosen_modes:
                if 0 <= index < len(node.children):
                    visit(node.children[index])
            return
        if node.is_targeted:
            out.append(node)
        for child in node.children + node.otherwise:
            visit(child)

    for effect in effects:
        visit(effect)
    return out


def spell_effects(game: Game, spell: GameObject) -> list:
    """The effects of a spell's spell abilities (CR 113.6a)."""
    return [
        effect
        for ability in game.characteristics(spell).abilities
        if ability.kind is AbilityKind.SPELL
        for effect in ability.effects
    ]


# ---------------------------------------------------------------------------
# Targeting (CR 601.2c, 115)
# ---------------------------------------------------------------------------


def targeting_effects(game: Game, spell: GameObject) -> list:
    """The targeted effect nodes of a spell, in announcement order.

    Order is the contract: targets are supplied as one tuple per targeting
    effect, lined up with this list, so anything that builds or reads targets
    has to walk the effects the same way.
    """
    chars = game.characteristics(spell)
    # CR 700.2c: once the modes are chosen, only the chosen ones have targets.
    # Before they are chosen - which is what legality asks of a card in hand -
    # every mode counts.
    nodes = targeted_nodes(spell_effects(game, spell), spell.chosen_modes or None)
    # CR 303.4a: "An Aura spell requires a target, which is defined by its
    # enchant ability." That target is not written as a targeting effect
    # anywhere - it comes from the keyword - so nothing offered it, an Aura
    # was cast with no target at all, and it resolved onto the battlefield
    # attached to nothing for the state-based actions to bin immediately.
    enchant = aura_target_effect(chars)
    if enchant is not None:
        nodes.insert(0, enchant)
    return nodes


def aura_target_effect(chars):
    """The synthetic targeting effect an Aura spell gets from Enchant.

    ``None`` for anything that is not an Aura, or an Aura whose enchant
    ability the parser could not read - those keep the old behaviour rather
    than being made uncastable.

    That covers 1,167 of the 1,222 Commander-legal Auras in the pool. The
    remaining 55 are "enchant player" - the Curses - where the enchant clause
    parses with no filter because a player is not an object filter, and four
    whose enchant ability does not parse at all. Attaching to a *player* needs
    more than a filter here: ``attached_to`` holds an ObjectId, so there is
    nowhere to put one. Left alone deliberately rather than half-built, so a
    Curse behaves as it did before instead of targeting a player and then
    failing to attach to them.
    """
    from .effects import Effect, EffectKind

    if not chars.has_subtype("Aura"):
        return None
    for ability in chars.abilities:
        if ability.keyword.lower() == "enchant" and ability.quality is not None:
            return Effect(
                EffectKind.NOTHING,
                targets=ability.quality,
                is_targeted=True,
                text="enchant",
            )
    return None


def _candidates_for(
    game: Game, source: GameObject, effect, player_id: PlayerId
) -> list[int]:
    """Everything one targeting effect could legally point at.

    Objects first, then players when the effect allows them (CR 115.4). One
    flat list, because the chooser picks from it and the encoding keeps the
    two apart.
    """
    from ..kernel.ids import player_target
    from ..kernel.matching import find

    if effect.targets is None:
        # "Target player draws two cards": the parser describes a target that
        # can only be a player with ``players`` and no object filter at all.
        # Asking ``find`` about a missing filter crashed the whole run - one
        # Prismari Command in one deck was enough to fail every simulation.
        return [player_target(pid) for pid in _targetable_players(game, effect.players, player_id)]

    out = [
        candidate.id
        for candidate in find(
            game, effect.targets, source=source.id, controller=player_id
        )
        if not _has_protection_from(game, candidate, source)
    ]
    if effect.targets is not None and effect.targets.includes_players:
        out.extend(
            player_target(player.id)
            for player in game.living_players
        )
    return out


def _targetable_players(game: Game, spec, player_id: PlayerId) -> list[PlayerId]:
    """Players a player-only target may point at (CR 115.4).

    "Target opponent" narrows it to opponents; anything else that reached here
    as a target - "target player", or a filter the parser left bare - is any
    player still in the game.
    """
    from ..kernel.query import PlayerScope

    living = [player.id for player in game.living_players]
    scope = getattr(spec, "scope", None)
    if scope in (PlayerScope.TARGET_OPPONENT, PlayerScope.OPPONENT, PlayerScope.EACH_OPPONENT):
        return [pid for pid in living if pid != player_id]
    return living


def _ask_for_targets(game: Game, spell: GameObject, player_id: PlayerId) -> tuple:
    """Ask the controller to choose targets (CR 601.2c).

    The candidate list is built by the engine from each effect's own filter,
    so an agent cannot choose something illegal even if it tries - what it
    returns is intersected with what was offered.
    """
    return choose_targets(game, spell, targeting_effects(game, spell), player_id)


def ability_targeting_effects(
    ability: Ability, chosen_modes: tuple[int, ...] | None = None
) -> list:
    """The targeted effect nodes of one activated or triggered ability."""
    return targeted_nodes(ability.effects, chosen_modes)


def ability_targets_available(
    game: Game, source: GameObject, ability: Ability, player_id: PlayerId
) -> bool:
    """CR 602.2b / 601.2c: an ability needing a target needs a legal one.

    The activated-ability counterpart of legality's check for spells. Without
    it an ability with nothing to point at was offered, chosen, and refused.
    """
    modal = modal_effect(ability.effects)
    if modal is not None and not legal_modes(game, source, modal, player_id):
        # CR 700.2a: one legal mode is enough, and none at all makes the
        # ability unactivatable. Demanding a legal target for every mode
        # would hide a charm behind whichever of its modes had nothing to
        # point at.
        return False
    for node in targeted_nodes(ability.effects, ()):
        if node.targets is not None and (node.targets.up_to or node.targets.includes_players):
            continue
        if not _candidates_for(game, source, node, player_id):
            return False
    return True


def choose_targets(game: Game, source: GameObject, effects: list, player_id: PlayerId) -> tuple:
    """Targets for a list of targeting effects, one tuple per effect.

    Shared by spells and activated abilities. Abilities used to have no
    chooser at all: an action arrived with no targets and was refused, so a
    bot could never activate anything that targets.
    """
    if not effects:
        return ()

    candidates = [_candidates_for(game, source, effect, player_id) for effect in effects]

    agent = game.agent_for(player_id)
    chooser = getattr(agent, "choose_targets", None)
    if chooser is None:
        picked = tuple((group[0],) if group else () for group in candidates)
    else:
        picked = chooser(game, player_id, source.id, candidates)

    cleaned = []
    for index, effect in enumerate(effects):
        legal = set(candidates[index]) if index < len(candidates) else set()
        offered = tuple(picked[index]) if index < len(picked) else ()
        kept = tuple(object_id for object_id in offered if object_id in legal)
        # CR 601.2c: a target is required unless the spell said "up to".
        if not kept and legal and not (
            effect.targets is not None and effect.targets.up_to
        ):
            kept = (candidates[index][0],)
        cleaned.append(kept)
    return tuple(cleaned)


def _validate_targets(game: Game, spell: GameObject, targets: tuple) -> None:
    """CR 601.2c: a spell cannot be cast without legal targets for every target
    it requires.

    This is the rule that stops a Doom Blade being cast into an empty board
    just to trigger something. It has to be enforced at announcement, not at
    resolution - the spell never reaches the stack otherwise.
    """
    from ..kernel.matching import matches

    effects = targeted_nodes(spell_effects(game, spell), spell.chosen_modes or None)

    if len(targets) < len(effects):
        raise CastError("not every required target was chosen")

    for index, effect in enumerate(effects):
        chosen = targets[index] if index < len(targets) else ()
        # "up to N targets" is a property of the target specification, not of
        # the effect: it is what may be chosen, not what is done with it.
        optional = effect.targets is not None and effect.targets.up_to
        if not chosen and not optional:
            raise CastError("a required target was not chosen")
        for object_id in chosen:
            if object_id < 0:
                # A player target (CR 115.4). Players cannot be destroyed or
                # bounced, so the only legality question is whether they are
                # still in the game.
                from ..kernel.ids import target_player

                if game.player(target_player(object_id)).has_lost:
                    raise CastError("that player has left the game")
                continue
            candidate = game.objects.get(object_id)
            if candidate is None:
                raise CastError("target no longer exists")
            if effect.targets is not None and not matches(
                game, candidate, effect.targets, source=spell.id, controller=spell.controller
            ):
                raise CastError(f"{candidate} is not a legal target")
            if _has_protection_from(game, candidate, spell):
                raise CastError(f"{candidate} can't be targeted by this spell")


def _announce_targets(game: Game, spell: GameObject, controller: PlayerId) -> None:
    """Emit a TARGETED event for each chosen target (CR 115.1).

    Ward is a *triggered* ability (CR 702.21a), not a targeting restriction:
    "whenever this becomes the target of a spell or ability an opponent
    controls, counter it unless that player pays [cost]". So becoming a target
    has to be an event something can watch, rather than a check that silently
    forbids the choice.
    """
    for group in spell.targets:
        for object_id in group:
            target = game.objects.get(object_id)
            if target is None or target.controller == controller:
                continue
            game.emit(
                Event(
                    EventKind.TARGETED,
                    object_id=object_id,
                    player=target.controller,
                    source=spell.id,
                    source_controller=controller,
                )
            )


def _has_protection_from(game: Game, target: GameObject, source: GameObject) -> bool:
    """Hexproof, shroud, and protection, as they apply to targeting (CR 702.11,
    702.16, 702.18).

    Ward is not here: ward does not stop the target being chosen, it adds a
    cost that must be paid or the spell is countered (CR 702.21).
    """
    from ..cr100_game_concepts.actions import protected_from
    from ..kernel.matching import matches

    chars = game.characteristics(target)
    if chars.has_keyword("Shroud"):
        return True
    if chars.has_keyword("Hexproof") and target.controller != source.controller:
        return True

    # CR 702.11c: "hexproof from [quality]" only stops sources with it.
    for ability in chars.keyword_abilities("Hexproof from"):
        if target.controller == source.controller:
            continue
        if ability.quality is None or matches(game, source, ability.quality):
            return True

    # CR 702.16b: protection from a quality stops targeting by sources with it.
    return protected_from(game, target, source)


# ---------------------------------------------------------------------------
# Paying (CR 601.2g-h)
# ---------------------------------------------------------------------------


def _pay(game: Game, player_id: PlayerId, total: TotalCost, context: GameObject) -> None:
    """Activate mana abilities as needed, then pay the whole cost.

    CR 601.2h: the cost has to be payable in full. A partial payment is not a
    thing, so nothing is spent until the whole plan is known to work.
    """
    player = game.player(player_id)
    mana_cost = total.final_mana()

    # CR 118.3 / 601.2h: a cost is paid in full or not at all. Every non-mana
    # component is checked *before* any mana leaves the pool, or a spell with an
    # unpayable additional cost would spend the mana and then rewind, quietly
    # emptying the pool for free.
    for component in total.additional:
        _check_payable(game, player_id, component, context)

    if mana_cost:
        payment = find_payment(
            player.mana_pool, mana_cost, life_available=player.life - 1, context=context
        )
        if payment is None:
            _tap_for_mana(game, player_id, mana_cost, context)
            payment = find_payment(
                player.mana_pool, mana_cost, life_available=player.life - 1, context=context
            )
        if payment is None:
            # CR 702.51a / 702.126a / 702.66a: convoke, improvise and delve
            # let other permanents and cards stand in for mana. Tried after
            # ordinary mana sources so nothing is tapped or exiled that was
            # not needed.
            _pay_with_helpers(game, player_id, mana_cost, context)
            payment = find_payment(
                player.mana_pool, mana_cost, life_available=player.life - 1, context=context
            )
        if payment is None:
            raise CastError(f"cannot pay {mana_cost}")

        for kind, amount in payment.mana:
            player.mana_pool.remove(kind, amount)
        if payment.life:
            player.lose_life(payment.life)
        game.emit(Event(EventKind.MANA_SPENT, player=player_id, amount=payment.total_mana))

    for component in total.additional:
        _pay_component(game, player_id, component, context)


#: Which keyword draws on which resource, and what each one pays for.
#: (keyword, what it consumes, whether the mana takes the source's colour)
_COST_HELPERS = (
    ("Convoke", "creature", True),
    ("Improvise", "artifact", False),
    ("Delve", "graveyard", False),
)


def _pay_with_helpers(game: Game, player_id: PlayerId, cost, context) -> None:
    """Tap or exile things that may stand in for mana.

    Adds the mana to the pool rather than reducing the cost, because that is
    what these keywords do: the spell still owes its full mana value and
    something else pays part of it. Anything that later asks the spell what it
    cost sees the printed number (CR 702.51b).
    """
    from ..cr100_game_concepts.cr106_mana import ManaKind
    from ..kernel.enums import CardType, Zone

    chars = game.characteristics(context) if context is not None else None
    if chars is None:
        return

    player = game.player(player_id)
    shortfall = max(0, cost.mana_value - player.mana_pool.total)
    if shortfall <= 0:
        return

    for keyword, resource, coloured in _COST_HELPERS:
        if not chars.has_keyword(keyword.lower()):
            continue
        for source in _helper_sources(game, player_id, resource):
            if shortfall <= 0:
                return
            if resource == "graveyard":
                game.move_object(source, Zone.EXILE)
                player.mana_pool.add(ManaKind(), 1)
            else:
                if source.tapped:
                    continue
                if resource == "creature" and not game.characteristics(
                    source
                ).has_type(CardType.CREATURE):
                    continue
                source.tapped = True
                colours = list(game.characteristics(source).colors) if coloured else []
                player.mana_pool.add(
                    ManaKind(colours[0]) if colours else ManaKind(), 1
                )
            shortfall -= 1


def helper_capacity(game: Game, spell: GameObject, player_id: PlayerId) -> int:
    """How much of a cost convoke, improvise and delve could cover.

    An upper bound, like every other affordability check: over-reporting costs
    a rewound cast, under-reporting hides a legal play entirely.
    """
    chars = game.characteristics(spell)
    total = 0
    for keyword, resource, _coloured in _COST_HELPERS:
        if chars.has_keyword(keyword.lower()):
            total += len(_helper_sources(game, player_id, resource))
    return total


def _helper_sources(game: Game, player_id: PlayerId, resource: str):
    """The permanents or cards a helper keyword may draw on, in a stable order.

    Sorted by id so a replay pays for a spell the same way twice.
    """
    from ..kernel.enums import CardType, Zone

    if resource == "graveyard":
        return [
            game.objects[object_id]
            for object_id in list(game.player(player_id).graveyard)
            if object_id in game.objects
        ]

    wanted = CardType.CREATURE if resource == "creature" else CardType.ARTIFACT
    return sorted(
        (
            obj
            for obj in game.permanents(player_id)
            if not obj.tapped
            and game.characteristics(obj).has_type(wanted)
            and not (
                resource == "creature"
                and obj.summoning_sick
                and game.characteristics(obj).has_type(CardType.CREATURE)
            )
            and obj.zone is Zone.BATTLEFIELD
        ),
        key=lambda o: o.id,
    )


def _tap_for_mana(
    game: Game, player_id: PlayerId, cost, context: GameObject
) -> None:
    """CR 601.2g: activate mana abilities to produce what is still needed.

    Mana abilities do not use the stack and cannot be responded to (CR 605.3b),
    so this happens inline. The choice of *which* sources to tap is the
    player's; the default taps in a stable order so replays reproduce, and the
    AI overrides it with something better.
    """
    from .resolve import Resolution, execute

    player = game.player(player_id)
    # CR 302.6 applies to creatures only. A land tapped for mana the turn it
    # arrives is completely normal, and skipping those here made every one-drop
    # uncastable on turn one.
    sources = sorted(
        (
            obj
            for obj in game.permanents(player_id)
            if not obj.tapped
            and not (obj.summoning_sick and game.characteristics(obj).is_creature)
        ),
        key=lambda o: o.id,
    )

    for obj in sources:
        if find_payment(player.mana_pool, cost, life_available=0, context=context):
            return
        for ability in game.characteristics(obj).abilities:
            if not ability.is_mana_ability or ability.unparsed:
                continue
            if not _can_pay_activation(game, obj, ability):
                continue
            _pay_activation(game, obj, ability)
            # CR 602.2b: the modes are chosen on activation, even when the
            # activation is the engine's own during cost payment.
            execute(
                Resolution(
                    game=game,
                    source=obj.id,
                    controller=player_id,
                    chosen_modes=choose_modes(game, obj, ability.effects, player_id),
                ),
                ability.effects,
            )
            break


#: Every non-mana cost ``_pay_component`` knows how to charge. Anything else is
#: refused by it and by the dry run alike: a component with no branch was
#: skipped, which is paying it with nothing (CR 118.3) - and a keyword's cost
#: can carry any component the cost grammar reads.
_PAYABLE_KINDS = frozenset(
    {
        CostKind.PAY_LIFE,
        CostKind.PAY_ENERGY,
        CostKind.TAP_SELF,
        CostKind.UNTAP_SELF,
        CostKind.TAP_OTHER,
        CostKind.UNTAP_OTHER,
        CostKind.SACRIFICE,
        CostKind.RETURN_TO_HAND,
        CostKind.UNATTACH,
        CostKind.DISCARD,
        *EXILE_ZONES,
        CostKind.MILL,
        CostKind.REMOVE_COUNTERS,
        CostKind.PUT_COUNTERS,
        CostKind.REVEAL,
        CostKind.CHOOSE,
    }
)


def _pay_component(
    game: Game, player_id: PlayerId, component: CostComponent, context: GameObject
) -> None:
    """Pay one non-mana cost component.

    CR 118.3 governs every branch: a player cannot pay a cost without the
    resources to pay it *fully*. So each one checks first and raises rather
    than paying what it can - a partial payment is not a thing, and the caller
    rewinds the whole attempt (CR 601.2h).
    """
    from ..cr100_game_concepts import actions as game_actions
    from ..kernel.values import evaluate

    player = game.player(player_id)
    # Floored for the costs that consume something: an amount of zero
    # there is a clause that forgot to set one, and paying by consuming
    # nothing makes the ability free and repeatable for ever.
    amount = required_amount(
        component, evaluate(game, component.amount, controller=player_id)
    )
    kind = component.kind

    if kind is CostKind.UNPARSED:
        raise CastError("cost could not be understood")
    if kind not in _PAYABLE_KINDS:
        raise CastError(f"cannot pay a {kind.name.lower()} cost")

    if kind is CostKind.PAY_LIFE:
        # CR 118.3b. Paying down to 0 is legal; the loss happens at the next
        # state-based action check, not during payment.
        if player.life < amount:
            raise CastError("not enough life")
        player.lose_life(amount)

    elif kind is CostKind.PAY_ENERGY:
        if player.energy < amount:
            raise CastError("not enough energy")
        player.energy -= amount

    elif kind is CostKind.TAP_SELF:
        obj = game.objects.get(context.source or context.id)
        if obj is None or obj.tapped:
            raise CastError("cannot tap")
        obj.tapped = True

    elif kind in (CostKind.TAP_OTHER, CostKind.UNTAP_OTHER):
        # CR 118.3: tapping *another* permanent as a cost. Note what is
        # missing here on purpose - the summoning-sickness check. That rule
        # (CR 302.6) applies only to the {T} symbol in a permanent's own cost,
        # so a creature that entered this turn can be tapped to pay someone
        # else's cost, which is exactly what makes Station work the turn a
        # creature lands.
        from ..kernel.matching import find

        tapping = kind is CostKind.TAP_OTHER
        wanted = max(1, amount)
        # ``source`` matters: the filter says "another creature you control",
        # and without the source to compare against, "another" excludes
        # everything rather than just this permanent.
        candidates = [
            obj
            for obj in find(
                game, component.filter, source=context.id, controller=player_id
            )
            if obj.tapped is not tapping
        ]
        if len(candidates) < wanted:
            raise CastError("not enough permanents to tap")
        for obj in candidates[:wanted]:
            obj.tapped = tapping
            context.cost_paid_objects.append(obj.id)
            game.emit(
                Event(
                    EventKind.TAPPED if tapping else EventKind.UNTAPPED,
                    object_id=obj.id,
                    player=player_id,
                )
            )

    elif kind in (CostKind.SACRIFICE, CostKind.RETURN_TO_HAND, CostKind.UNATTACH):
        for obj in _choose_permanents(game, player_id, component, amount, context):
            if kind is CostKind.SACRIFICE:
                game_actions.sacrifice(game, obj, source=context.id)
            elif kind is CostKind.RETURN_TO_HAND:
                # Recorded before it goes, as tapping another permanent is:
                # ninjutsu's Ninja attacks whatever *this* creature was
                # attacking (CR 702.49c), and once it is back in hand nothing
                # else in the resolution can name it.
                context.cost_paid_objects.append(obj.id)
                game_actions.bounce(game, obj, source=context.id)
            else:
                game_actions.detach(game, obj)

    elif kind is CostKind.UNTAP_SELF:
        # CR 107.6: {Q} untaps the source, so it has to be tapped to pay - and
        # CR 302.6 holds a creature to that exactly as it does to {T}.
        obj = game.objects.get(context.source or context.id)
        if (
            obj is None
            or not obj.tapped
            or (obj.summoning_sick and game.characteristics(obj).is_creature)
        ):
            raise CastError("cannot untap")
        obj.tapped = False
        game.emit(Event(EventKind.UNTAPPED, object_id=obj.id, player=player_id))

    elif kind is CostKind.DISCARD and _is_this_card(component):
        # The card pays with itself. Asking the agent which card to discard
        # would keep the card being cycled and throw away another.
        if context.zone is not Zone.HAND:
            raise CastError("this card is not in its owner's hand")
        game_actions.discard(game, context, source=context.id)

    elif kind is CostKind.DISCARD:
        if len(player.hand) < amount:
            raise CastError("not enough cards in hand")
        agent = game.agent_for(player_id)
        for _ in range(amount):
            choice = (
                agent.choose_discard(game, player_id) if agent is not None else player.hand[-1]
            )
            obj = game.objects.get(choice) or game.objects[player.hand[-1]]
            game_actions.discard(game, obj, source=context.id)

    elif kind in EXILE_ZONES:
        # From the zone the cost names, matching what it names. Only the
        # graveyard was ever charged, and by count alone: Force of Will's blue
        # card cost nothing, and "Exile this artifact" took a graveyard card
        # and kept the artifact. "Exile this card from your graveyard" is the
        # source paying for itself, and only from there.
        for obj in _choose_exiled(game, player_id, component, amount, context):
            game_actions.exile(game, obj, source=context.id)

    elif kind is CostKind.MILL:
        if len(player.library) < amount:
            raise CastError("not enough cards in the library")
        game_actions.mill(game, player_id, amount, source=context.id)

    elif kind is CostKind.REMOVE_COUNTERS:
        obj = game.objects.get(context.source or context.id)
        if obj is None or obj.counter_count(component.counter_type) < amount:
            raise CastError(f"not enough {component.counter_type} counters")
        game_actions.remove_counters(game, obj, component.counter_type, amount)

    elif kind is CostKind.PUT_COUNTERS:
        obj = game.objects.get(context.source or context.id)
        if obj is not None:
            game_actions.add_counters(game, obj, component.counter_type, amount)

    elif kind in (CostKind.REVEAL, CostKind.CHOOSE):
        return  # Nothing is spent; making the choice is the whole cost.


def _check_payable(
    game: Game,
    player_id: PlayerId,
    component: CostComponent,
    context: GameObject,
    *,
    spell: GameObject | None = None,
) -> None:
    """Raise if this component could not be paid, without paying anything.

    The dry run that makes payment atomic. It deliberately mirrors
    ``_pay_component``'s checks rather than sharing them, because the paying
    version has to re-check anyway - an earlier component may have consumed the
    resource this one needs (CR 118.10).

    ``spell`` is a card asked about before it is cast, still in the zone it is
    cast from. It is on the stack by the time the cost is paid, so it cannot
    pay for itself: a Force of Will is not the blue card it exiles.
    """
    from ..kernel.matching import find
    from ..kernel.values import evaluate

    player = game.player(player_id)
    # Floored for the costs that consume something: an amount of zero
    # there is a clause that forgot to set one, and paying by consuming
    # nothing makes the ability free and repeatable for ever.
    amount = required_amount(
        component, evaluate(game, component.amount, controller=player_id)
    )
    kind = component.kind

    if kind is CostKind.UNPARSED:
        raise CastError("cost could not be understood")
    if kind not in _PAYABLE_KINDS:
        raise CastError(f"cannot pay a {kind.name.lower()} cost")
    if kind is CostKind.PAY_LIFE and player.life < amount:
        raise CastError("not enough life")
    if kind is CostKind.PAY_ENERGY and player.energy < amount:
        raise CastError("not enough energy")
    if kind is CostKind.DISCARD and _is_this_card(component):
        # The card itself pays, so some other card in hand will not do.
        if context.zone is not Zone.HAND:
            raise CastError("this card is not in its owner's hand")
        return
    if kind is CostKind.DISCARD:
        in_hand = [i for i in player.hand if spell is None or i != spell.id]
        if len(in_hand) < amount:
            raise CastError("not enough cards in hand")
    if kind in EXILE_ZONES:
        if len(_exile_candidates(game, player_id, component, context, spell)) < amount:
            raise CastError(_exile_shortfall(component))
    if kind is CostKind.UNTAP_SELF:
        obj = game.objects.get(context.source or context.id)
        if (
            obj is None
            or not obj.tapped
            or (obj.summoning_sick and game.characteristics(obj).is_creature)
        ):
            raise CastError("cannot untap")
    if kind is CostKind.MILL and len(player.library) < amount:
        raise CastError("not enough cards in the library")
    if kind is CostKind.REMOVE_COUNTERS:
        obj = game.objects.get(context.source or context.id)
        if obj is None or obj.counter_count(component.counter_type) < amount:
            raise CastError(f"not enough {component.counter_type} counters")
    if kind in (CostKind.SACRIFICE, CostKind.RETURN_TO_HAND) and component.filter is not None:
        if len(find(game, component.filter, source=context.id, controller=player_id)) < amount:
            raise CastError("not enough permanents to pay the cost")
    if kind is CostKind.TAP_SELF:
        obj = game.objects.get(context.source or context.id)
        if obj is None or obj.tapped:
            raise CastError("cannot tap")


def _choose_permanents(
    game: Game,
    player_id: PlayerId,
    component: CostComponent,
    amount: int,
    context: GameObject,
) -> list[GameObject]:
    """Pick permanents to pay a sacrifice-style cost.

    CR 118.10: each payment applies to one spell or ability only, so the same
    creature cannot pay two costs. That falls out of actually sacrificing it
    before the next component is paid.
    """
    from ..kernel.matching import find

    spec = component.filter
    if spec is None:
        obj = game.objects.get(context.source or context.id)
        return [obj] if obj is not None else []

    candidates = find(game, spec, source=context.id, controller=player_id)
    if len(candidates) < amount:
        raise CastError("not enough permanents to pay the cost")

    agent = game.agent_for(player_id)
    if agent is not None and hasattr(agent, "choose_cost_permanents"):
        picked = agent.choose_cost_permanents(game, player_id, candidates, amount)
        if picked and len(picked) == amount:
            return list(picked)
    # Deterministic default: cheapest first, ties broken by id so replays match.
    return sorted(candidates, key=lambda o: (game.characteristics(o).mana_value, o.id))[
        :amount
    ]


def _exile_candidates(
    game: Game,
    player_id: PlayerId,
    component: CostComponent,
    context: GameObject,
    spell: GameObject | None = None,
) -> list[GameObject]:
    """Everything that could pay an exile cost, from the zone it names only.

    "Exile this ..." is paid by the source, and only while it is in that zone.
    Anything else is something of the payer's there that matches the cost -
    "a blue card", "five other cards" - less ``spell``, which will be on the
    stack by the time it pays (see ``_check_payable``).
    """
    from dataclasses import replace

    from ..kernel.matching import find, matches

    zone = EXILE_ZONES[component.kind]
    if _is_this_card(component):
        # Live as well as in the zone: a card that has already left keeps its
        # old zone for last-known information, and is not there to exile.
        obj = game.objects.get(context.source or context.id)
        return [obj] if obj is not None and obj.is_live and obj.zone is zone else []

    spec = component.filter
    if spec is None:
        # A bare count: any of the payer's cards there. Never a permanent,
        # which a cost always describes.
        pool = (
            []
            if zone is Zone.BATTLEFIELD
            else [game.objects[i] for i in game.player(player_id).zone(zone)]
        )
    else:
        # The kind names the zone. A filter left at its battlefield default
        # would otherwise match nothing in a hand or a graveyard.
        spec = replace(spec, zones=frozenset({zone}))
        if zone is Zone.BATTLEFIELD:
            pool = find(game, spec, source=context.id, controller=player_id)
        else:
            # The payer's own zone: "from your hand" is never someone else's.
            pool = [
                obj
                for obj in (game.objects[i] for i in game.player(player_id).zone(zone))
                if matches(game, obj, spec, source=context.id, controller=player_id)
            ]
    return [obj for obj in pool if spell is None or obj.id != spell.id]


def _choose_exiled(
    game: Game,
    player_id: PlayerId,
    component: CostComponent,
    amount: int,
    context: GameObject,
) -> list[GameObject]:
    """Pick what pays an exile cost, refusing unless there is enough (CR 118.3)."""
    candidates = _exile_candidates(game, player_id, component, context)
    if len(candidates) < amount:
        raise CastError(_exile_shortfall(component))
    if EXILE_ZONES[component.kind] is Zone.LIBRARY:
        # "The top N cards": a library's order is the choice (CR 401.2).
        return candidates[:amount]
    # The same deterministic default as sacrifices: cheapest first, then id.
    return sorted(candidates, key=lambda o: (game.characteristics(o).mana_value, o.id))[
        :amount
    ]


def _exile_shortfall(component: CostComponent) -> str:
    zone = EXILE_ZONES[component.kind].name.lower()
    if _is_this_card(component):
        return f"this card is not in the {zone}"
    return f"not enough to exile from the {zone}"


# ---------------------------------------------------------------------------
# Activating abilities (CR 602)
# ---------------------------------------------------------------------------


def activate_ability(game: Game, player_id: PlayerId, action: Action) -> GameObject | None:
    """Activate an ability (CR 602.2).

    Follows the same sequence as casting (CR 602.2b applies CR 601.2b-i), with
    one important divergence: a mana ability never goes on the stack and
    resolves immediately (CR 605.3).
    """
    from .cr608_stack import push_ability
    from .resolve import Resolution, execute

    source = game.objects.get(action.source)
    if source is None:
        raise CastError("no such permanent")

    chars = game.characteristics(source)
    try:
        ability = chars.abilities[action.ability_index]
    except IndexError:
        raise CastError("no such ability") from None

    if ability.unparsed:
        raise CastError("ability could not be understood")
    refusal = activation_refusal(game, player_id, source, ability)
    if refusal is not None:
        raise CastError(refusal)
    if not _can_pay_activation(game, source, ability):
        raise CastError("cannot pay the activation cost")

    # CR 602.2b: activating an ability follows CR 601.2b-i, so the modes are
    # chosen here, before the targets and before anything is paid. A modal
    # ability with no legal mode cannot be activated at all (CR 700.2a).
    chosen_modes = choose_modes(
        game, source, ability.effects, player_id, announced=action.mode_choices
    )
    if not chosen_modes and modal_effect(ability.effects) is not None:
        raise CastError("no legal mode for this ability")

    targets = action.targets
    if ability.is_targeted:
        if not targets:
            effects = targeted_nodes(ability.effects, chosen_modes or None)
            targets = choose_targets(game, source, effects, player_id)
            for node, chosen in zip(effects, targets):
                if not chosen and not (node.targets is not None and node.targets.up_to):
                    raise CastError("no legal target for this ability")
        _validate_ability_targets(game, source, ability, targets, chosen_modes)

    # A fresh record per activation: what the *last* activation tapped must
    # not leak into this one.
    source.cost_paid_objects = []
    _pay_activation(game, source, ability)
    source.activations_this_turn[action.ability_index] = (
        source.activations_this_turn.get(action.ability_index, 0) + 1
    )

    if ability.is_mana_ability:
        # CR 605.3b: resolves immediately, with no chance to respond. There is
        # no stack object, so the modes chosen above travel on the resolution
        # instead - otherwise "Add {R} or {G}" chose a mode and added nothing.
        execute(
            Resolution(
                game=game,
                source=source.id,
                controller=player_id,
                chosen_modes=chosen_modes,
            ),
            ability.effects,
        )
        # CR 605.4: anything that triggered off this mana ability and is itself
        # a mana ability resolves right here too. It cannot wait for the stack,
        # because during cost payment there is no window in which the stack is
        # used at all.
        from .cr603_triggers import resolve_mana_triggers

        resolve_mana_triggers(game)
        return None

    game.emit(
        Event(EventKind.ABILITY_ACTIVATED, object_id=source.id, player=player_id)
    )
    stack_object = push_ability(
        game,
        source,
        ability,
        targets=targets,
        x_value=action.x_value,
        chosen_modes=chosen_modes,
    )
    # What the cost consumed travels with the ability, as its targets do. The
    # source's own record belongs to its latest activation, and a Ninja still
    # in hand can activate ninjutsu again while the first is on the stack.
    stack_object.cost_paid_objects = list(source.cost_paid_objects)
    return stack_object


def _validate_ability_targets(
    game: Game,
    source: GameObject,
    ability: Ability,
    targets: tuple,
    chosen_modes: tuple[int, ...] = (),
) -> None:
    from ..kernel.matching import matches

    effects = targeted_nodes(ability.effects, chosen_modes or None)
    if len(targets) < len(effects):
        raise CastError("not every required target was chosen")
    for index, effect in enumerate(effects):
        for object_id in targets[index] if index < len(targets) else ():
            if object_id < 0:
                # A player target (CR 115.4), exactly as spells allow. Looked
                # up as an object it "no longer existed", so Walking Ballista
                # aimed at a player was refused 245 times in one game.
                from ..kernel.ids import target_player

                if game.player(target_player(object_id)).has_lost:
                    raise CastError("that player has left the game")
                continue
            candidate = game.objects.get(object_id)
            if candidate is None:
                raise CastError("target no longer exists")
            if effect.targets is not None and not matches(
                game,
                candidate,
                effect.targets,
                source=source.id,
                controller=source.controller,
            ):
                raise CastError("illegal target")


def _scaled_mana(game: Game, player_id: PlayerId, cost):
    """A cost's mana component, multiplied by any per-permanent scale."""
    mana = cost.mana_component
    if not mana:
        return mana
    scale = next(
        (c.scale for c in cost.components if c.is_mana and c.scale is not None),
        None,
    )
    if scale is None:
        return mana
    from ..kernel.values import evaluate

    times = evaluate(game, scale, controller=player_id)
    if times <= 1:
        return mana if times == 1 else mana.increased_by(-mana.mana_value)
    return mana.increased_by(mana.mana_value * (times - 1))


def can_pay_cost(game: Game, player_id: PlayerId, cost) -> bool:
    """Whether a player could pay a cost that has no source permanent.

    "Unless that player pays {1}" is charged to a player, not to a permanent,
    so none of the activation helpers apply: there is nothing to tap and no
    controller to infer. Only the components that actually appear in these
    clauses are supported - mana, life, sacrificing, discarding - and anything
    else answers no, which lets the effect happen rather than silently
    waiving it.
    """
    if cost.choices:
        # "Discard a card or pay 3 life": payable if either half is.
        return any(can_pay_cost(game, player_id, each) for each in cost.choices)

    player = game.player(player_id)
    for component in cost.non_mana_components:
        if component.kind is CostKind.PAY_LIFE:
            from ..kernel.values import evaluate

            if player.life <= evaluate(game, component.amount, controller=player_id):
                return False
        elif component.kind is CostKind.DISCARD:
            if not player.hand:
                return False
        elif component.kind is CostKind.SACRIFICE:
            from ..kernel.matching import find

            if component.filter is None or not find(
                game, component.filter, controller=player_id
            ):
                return False
        else:
            return False

    mana_cost = _scaled_mana(game, player_id, cost)
    if not mana_cost:
        return True
    if find_payment(player.mana_pool, mana_cost, life_available=player.life - 1):
        return True
    return _could_produce(game, player_id, mana_cost)


def pay_cost(game: Game, player_id: PlayerId, cost) -> bool:
    """Charge a player-scoped cost. False if it could not be paid in full."""
    if cost.choices:
        # The first half that can actually be paid. A player choosing between
        # two costs would weigh them; with nothing to ask, taking the first
        # payable one keeps the run deterministic.
        for each in cost.choices:
            if can_pay_cost(game, player_id, each):
                return pay_cost(game, player_id, each)
        return False

    player = game.player(player_id)
    mana_cost = _scaled_mana(game, player_id, cost)
    if mana_cost:
        payment = find_payment(
            player.mana_pool, mana_cost, life_available=player.life - 1
        )
        if payment is None:
            # No source permanent here, so the context is the payer's own
            # board - the same helper the cast path uses, with nothing to
            # anchor cost reductions to.
            #
            # ``can_pay_cost`` is an upper bound: it counts an Azorius Signet's
            # {W}{U} without asking where the Signet's own {1} comes from. So
            # tapping can fail partway, and during a resolution there is no
            # cast to rewind - the CastError went straight up through the
            # stack and ended the whole run (Smothering Tithe asking a player
            # whose only untapped source was a Signet to pay {2}). Rewound
            # here instead: the sources untap, the mana made is gone, and the
            # player simply did not pay.
            checkpoint = _mana_checkpoint(game, player_id)
            try:
                _tap_for_mana(game, player_id, mana_cost, None)
            except CastError:
                _restore_mana_checkpoint(game, player_id, checkpoint)
                return False
            payment = find_payment(
                player.mana_pool, mana_cost, life_available=player.life - 1
            )
            if payment is None:
                _restore_mana_checkpoint(game, player_id, checkpoint)
                return False
        for kind, amount in payment.mana:
            player.mana_pool.remove(kind, amount)
        if payment.life:
            player.lose_life(payment.life)

    for component in cost.non_mana_components:
        if component.kind is CostKind.PAY_LIFE:
            from ..kernel.values import evaluate

            player.lose_life(evaluate(game, component.amount, controller=player_id))
        elif component.kind is CostKind.DISCARD and player.hand:
            game.move_object(game.objects[player.hand[-1]], Zone.GRAVEYARD)
        elif component.kind is CostKind.SACRIFICE and component.filter is not None:
            from ..kernel.matching import find

            found = find(game, component.filter, controller=player_id)
            if found:
                game.move_object(found[0], Zone.GRAVEYARD)
    return True


def _mana_checkpoint(game: Game, player_id: PlayerId) -> tuple:
    """What activating mana abilities for a payment can change, before it does."""
    player = game.player(player_id)
    return (
        {obj.id: obj.tapped for obj in game.permanents(player_id)},
        dict(player.mana_pool.buckets),
        player.life,
        len(game.pending_triggers),
    )


def _restore_mana_checkpoint(game: Game, player_id: PlayerId, checkpoint: tuple) -> None:
    """Undo the mana abilities activated for a payment that was not made.

    Tapped sources untap, floating mana and life paid are put back, and any
    triggers the tapping set off are dropped. A mana ability that sacrificed
    its own source - a Treasure - cannot be put back, and is left.
    """
    tapped, pool, life, pending = checkpoint
    player = game.player(player_id)
    for object_id, was_tapped in tapped.items():
        obj = game.objects.get(object_id)
        if obj is not None:
            obj.tapped = was_tapped
    player.mana_pool.buckets.clear()
    player.mana_pool.buckets.update(pool)
    player.life = life
    del game.pending_triggers[pending:]
    game.invalidate_characteristics()


def activation_refusal(
    game: Game, player_id: PlayerId, source: GameObject, ability: Ability
) -> str | None:
    """Why this player may not activate this ability where it is, or None.

    Legality and activation both ask, so the actions offered and the actions
    accepted cannot disagree. Cost, timing and targets are separate questions;
    these come first because none of those matter for an ability that is not
    there to be activated.
    """
    # CR 113.6: an ability works only in the zones it functions in. Cycling is
    # an ability of a card in hand (CR 702.29a); once Irrigated Farmland has
    # been played there is nothing to cycle, and never asking offered its
    # Cycling from the battlefield 605 times in one four-player game.
    if not ability.functions_in_zone(source.zone):
        return "that ability does not function in that zone"
    # CR 602.2 with 108.4a: only an object's controller activates its
    # abilities, and a card in hand has no controller - its owner does.
    activator = (
        source.controller
        if source.zone in (Zone.BATTLEFIELD, Zone.STACK)
        else source.owner
    )
    if activator != player_id:
        return "only its controller may activate that ability"
    # A keyword whose shape is built but whose behaviour is not. Paying for an
    # effect the engine cannot carry out is not playing the card either.
    if any(node.is_unparsed for effect in ability.effects for node in effect.walk()):
        return "ability could not be understood"
    # "Activate only during your upkeep" and its kin (CR 602.5b) are part of
    # the ability, not advice.
    from ..kernel.conditions import holds

    if not holds(
        game, ability.activation_condition, source=source.id, controller=player_id
    ):
        return "that ability's activation restriction is not met"
    return None


def _is_this_card(component: CostComponent) -> bool:
    """"Discard this card", "Exile this card from your hand" - paid by the source."""
    return component.filter is not None and component.filter.source_only


def _can_pay_activation(game: Game, source: GameObject, ability: Ability) -> bool:
    """Whether an ability's cost could be paid right now."""
    cost = ability.cost
    if cost.is_unparsed:
        return False

    player = game.player(source.controller)

    if cost.requires_tapping:
        if source.tapped:
            return False
        # CR 302.6: a creature's {T} ability needs it to have been controlled
        # since the turn began. A non-creature permanent has no such
        # restriction, which is why a Signet works the turn it lands.
        if game.characteristics(source).is_creature and source.summoning_sick:
            return False

    for component in cost.non_mana_components:
        if component.kind is CostKind.PAY_LIFE:
            from ..kernel.values import evaluate

            if player.life < evaluate(game, component.amount, controller=player.id):
                return False
        elif component.kind is CostKind.LOYALTY:
            # CR 606.4: the cost is putting on or removing loyalty counters.
            # A minus ability cannot be activated without the counters to pay
            # it - that is the whole shape of planeswalker resource management.
            from ..kernel.values import evaluate

            change = evaluate(game, component.amount, controller=player.id)
            if change < 0 and source.counter_count("loyalty") < -change:
                return False
        elif component.kind in (CostKind.TAP_OTHER, CostKind.UNTAP_OTHER):
            # Offering an ability whose cost cannot be paid wastes a bot's
            # whole turn on a play it then has to abandon.
            from ..kernel.matching import find
            from ..kernel.values import evaluate

            wanted = max(1, evaluate(game, component.amount, controller=player.id))
            tapping = component.kind is CostKind.TAP_OTHER
            available = [
                obj
                for obj in find(
                    game, component.filter, source=source.id, controller=player.id
                )
                if obj.tapped is not tapping
            ]
            if len(available) < wanted:
                return False
        elif component.kind is CostKind.UNPARSED:
            return False
        else:
            # Sacrifice, discard, exile, mill, counters, energy: ask the same
            # dry run payment uses, so legality and payment cannot disagree.
            # Before this, "Sacrifice a Treasure" was offered with no Treasure
            # in play; the bot took it, payment refused, and it took it again
            # at the next priority - 258 times in one game, each a full board
            # recomputation, which is what kept a run at 49 of 50.
            try:
                _check_payable(game, player.id, component, source)
            except CastError:
                return False

    mana_cost = cost.mana_component
    if mana_cost:
        if find_payment(player.mana_pool, mana_cost, life_available=player.life - 1):
            return True
        return _could_produce(game, source.controller, mana_cost)
    return True


def _could_produce(game: Game, player_id: PlayerId, cost) -> bool:
    """A cheap upper bound on whether untapped sources could cover a cost.

    Counts available mana sources rather than solving the tap plan exactly. It
    can say yes when the colors do not actually work out, in which case the
    payment attempt fails and the cast is rewound - never the other way round,
    so it can never let an unpayable cost through.
    """
    available = 0
    for obj in game.permanents(player_id):
        if obj.tapped:
            continue
        chars = game.characteristics(obj)
        if chars.is_creature and obj.summoning_sick:
            continue
        if any(a.is_mana_ability for a in chars.abilities):
            available += 1
    return available >= cost.mana_value


def _pay_activation(game: Game, source: GameObject, ability: Ability) -> None:

    cost = ability.cost
    player = game.player(source.controller)

    if cost.requires_tapping:
        source.tapped = True
        game.emit(
            Event(EventKind.TAPPED, object_id=source.id, player=source.controller)
        )

    mana_cost = cost.mana_component
    if mana_cost:
        payment = find_payment(player.mana_pool, mana_cost, life_available=player.life - 1)
        if payment is None:
            _tap_for_mana(game, source.controller, mana_cost, source)
            payment = find_payment(
                player.mana_pool, mana_cost, life_available=player.life - 1
            )
        if payment is None:
            raise CastError("cannot pay the mana cost")
        for kind, amount in payment.mana:
            player.mana_pool.remove(kind, amount)
        if payment.life:
            player.lose_life(payment.life)

    for component in cost.non_mana_components:
        if component.kind is CostKind.TAP_SELF:
            continue  # Already handled above.
        if component.kind is CostKind.LOYALTY:
            from ..kernel.values import evaluate

            change = evaluate(game, component.amount, controller=source.controller)
            if change >= 0:
                source.add_counters("loyalty", change)
            elif source.remove_counters("loyalty", -change) < -change:
                raise CastError("not enough loyalty counters")
            game.invalidate_characteristics()
            continue
        _pay_component(game, source.controller, component, source)
