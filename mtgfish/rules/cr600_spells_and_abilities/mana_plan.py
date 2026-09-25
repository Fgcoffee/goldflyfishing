"""CR 601.2g: which mana abilities to activate to pay a cost - solved, not guessed.

The old approach tapped every untapped source in id order, each for its
*first* mana ability, until the pool happened to cover the cost. That picks
the wrong ability on every land with more than one: a Karplusan Forest tapped
for {C} when the spell needed {R}, a Jungle Hollow always made {B}, and "any
colour" always made white. The cast was then rewound as unpayable although the
player could plainly pay it, and a bot that tried again tried the same way.

Here every untapped source contributes its *options* - each mana ability, each
mode of a modal one, each colour of an "any colour" one - with what each would
add to the pool, and a small search picks a set of options that pays the cost.
The same search answers "could this be paid?" for the legality checks, so the
list of legal actions and the payment can no longer disagree.

Nothing here changes the game. Predictions read the ability's effects; the
choices are then carried out by ``execute_plan`` through the ordinary
activation path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..cr100_game_concepts.cr106_mana import ManaCost, ManaKind, ManaPool, find_payment
from ..kernel.enums import Color
from ..cr100_game_concepts.cr118_costs import CostKind
from .effects import EffectKind

if TYPE_CHECKING:
    from ..kernel.game import Game
    from ..kernel.ids import ObjectId, PlayerId

#: How many partial plans the search may look at before giving up. Real boards
#: are solved in a few dozen; the budget only guards a pathological one (dozens
#: of multi-option sources and a cost none of them can pay).
SEARCH_BUDGET = 4000

#: Drawbacks, cheapest first. The search prefers the cheaper of two plans that
#: both work: a Karplusan Forest is tapped for {C} before it is tapped for {G}
#: and 1 damage, and a Treasure is kept while a land will do.
_FREE, _PAINFUL, _SACRIFICE = 0, 1, 2


@dataclass(frozen=True, slots=True)
class ManaOption:
    """One way of activating one mana ability, and what it would add."""

    source: ObjectId
    ability_index: int
    modes: tuple[int, ...]
    #: The colour picked for an "add one mana of any color" effect.
    color: Color
    produces: tuple[ManaKind, ...]
    #: The ability's own mana cost - a Signet's {1} - paid from the pool.
    mana_cost: ManaCost | None
    drawback: int


def mana_options(game: Game, player_id: PlayerId) -> list[list[ManaOption]]:
    """Every untapped mana source the player could activate now, with its options.

    One inner list per source, because a source is activated at most once: its
    options are alternatives, not additions.
    """
    from .cr601_casting import _can_pay_activation, activation_mana_cost

    per_source: list[list[ManaOption]] = []
    for obj in sorted(game.permanents(player_id), key=lambda o: o.id):
        chars = game.characteristics(obj)
        options: list[ManaOption] = []
        for index, ability in enumerate(chars.abilities):
            if not ability.is_mana_ability or ability.unparsed:
                continue
            if not _can_pay_activation(game, obj, ability):
                continue
            drawback = _drawback(ability)
            mana_cost = activation_mana_cost(game, obj, ability) or None
            for modes, color, produced in _outcomes(game, obj, ability, player_id):
                if not produced:
                    continue
                options.append(
                    ManaOption(
                        source=obj.id,
                        ability_index=index,
                        modes=modes,
                        color=color,
                        produces=produced,
                        mana_cost=mana_cost,
                        drawback=max(drawback, _effect_drawback(ability, modes)),
                    )
                )
        if options:
            options.sort(key=lambda o: (o.drawback, o.mana_cost is not None, -len(o.produces)))
            per_source.append(options)
    return per_source


def plan_payment(
    game: Game,
    player_id: PlayerId,
    cost: ManaCost,
    context=None,
    *,
    pool: ManaPool | None = None,
    options: list[list[ManaOption]] | None = None,
) -> list[ManaOption] | None:
    """The mana abilities to activate so ``cost`` can be paid, or None if none do.

    An empty list means the pool already covers it. Sources with no drawback
    are tried first and the first plan found is taken, so the result is
    deterministic - replay depends on that - and seldom taps a pain land or
    sacrifices a Treasure that a basic land would have replaced.
    """
    player = game.player(player_id)
    life = player.life - 1
    start = (pool or player.mana_pool).copy()
    if find_payment(start, cost, life_available=life, context=context):
        return []
    sources = options if options is not None else mana_options(game, player_id)
    if not sources:
        return None
    # Cheap, dependable sources first. A source that can only make one kind of
    # mana is tried before a flexible one, which is saved for the colour
    # nothing else makes.
    sources = sorted(
        sources,
        key=lambda opts: (
            min(o.drawback for o in opts),
            len({o.produces for o in opts}),
            opts[0].source,
        ),
    )
    ceilings = _ceilings(sources)
    needs = _colour_needs(cost)
    budget = [SEARCH_BUDGET]
    chosen: list[ManaOption] = []

    def search(index: int, current: ManaPool) -> bool:
        budget[0] -= 1
        if budget[0] < 0:
            return False
        if find_payment(current, cost, life_available=life, context=context):
            return True
        if index == len(sources) or not _can_still_reach(current, cost, needs, ceilings[index]):
            return False
        for option in sources[index]:
            after = _apply(current, option)
            if after is None:
                continue
            chosen.append(option)
            if search(index + 1, after):
                return True
            chosen.pop()
        return search(index + 1, current)

    if not search(0, start):
        return None
    return _trimmed(chosen, start, cost, life, context)


def _trimmed(plan, start: ManaPool, cost: ManaCost, life: int, context) -> list[ManaOption]:
    """Drop every activation the plan does not need.

    The search stops at the first plan that pays, and taking sources in order
    means it may have tapped two Forests on the way to the Mountain a {R} cost
    wanted. Removing whatever still leaves a working plan leaves those Forests
    for the next spell.
    """
    plan = list(plan)
    for index in range(len(plan) - 1, -1, -1):
        candidate = plan[:index] + plan[index + 1 :]
        pool = start
        for option in candidate:
            pool = _apply(pool, option)
            if pool is None:
                break
        if pool is not None and find_payment(pool, cost, life_available=life, context=context):
            plan = candidate
    return plan


def can_produce(game: Game, player_id: PlayerId, cost: ManaCost, context=None) -> bool:
    """Whether the pool plus untapped sources can pay ``cost`` exactly."""
    return plan_payment(game, player_id, cost, context) is not None


def execute_plan(game: Game, player_id: PlayerId, plan: list[ManaOption]) -> None:
    """Activate the planned mana abilities, in order (CR 605.3b: no stack)."""
    from .cr601_casting import CastError, _pay_activation, choose_modes
    from .cr603_triggers import resolve_mana_triggers
    from .resolve import Resolution, execute

    for option in plan:
        obj = game.objects.get(option.source)
        if obj is None:
            raise CastError("a planned mana source is gone")
        ability = game.characteristics(obj).abilities[option.ability_index]
        _pay_activation(game, obj, ability)
        execute(
            Resolution(
                game=game,
                source=obj.id,
                controller=player_id,
                chosen_modes=choose_modes(
                    game, obj, ability.effects, player_id, announced=option.modes
                )
                if option.modes
                else (),
                mana_color=option.color,
            ),
            ability.effects,
        )
        resolve_mana_triggers(game)


# ---------------------------------------------------------------------------
# Predicting what an ability adds
# ---------------------------------------------------------------------------


def _outcomes(game: Game, obj, ability, player_id: PlayerId):
    """(modes, colour, mana added) for each way the ability can be activated."""
    from .cr601_casting import legal_modes, modal_effect

    modal = modal_effect(ability.effects)
    if modal is None:
        for color, produced in _produced(game, obj, ability.effects, player_id):
            yield (), color, produced
        return
    for mode in legal_modes(game, obj, modal, player_id):
        branch = _replace_modal(ability.effects, modal, modal.children[mode])
        for color, produced in _produced(game, obj, branch, player_id):
            yield (mode,), color, produced


def _replace_modal(effects, modal, chosen) -> tuple:
    return tuple(chosen if effect is modal else effect for effect in effects)


def _produced(game: Game, obj, effects, player_id: PlayerId):
    """(colour choice, mana) pairs for a list of effects with no modes left.

    Only one "any colour" choice is branched on; a second in the same ability
    takes the same colour, which is what every printed card wants anyway.
    """
    adds = list(_add_mana_effects(effects))
    if not adds:
        return
    from .resolve import mana_color_choices

    menu = Color.NONE
    for effect in adds:
        if not effect.mana_produced and not effect.colors_chosen and effect.colors:
            # The same menu resolution offers - Command Tower's is the
            # commander's colour identity - so a plan cannot count on a colour
            # the ability will not make.
            menu |= mana_color_choices(game, player_id, effect)
    choices = [c for c in Color if c and c in menu] if menu else [Color.NONE]
    for color in choices:
        produced: list[ManaKind] = []
        for effect in adds:
            produced.extend(_predict(game, obj, effect, player_id, color))
        yield color, tuple(produced)


def _add_mana_effects(effects):
    for effect in effects:
        if effect.kind is EffectKind.ADD_MANA:
            yield effect
        elif effect.kind is EffectKind.SEQUENCE:
            yield from _add_mana_effects(effect.children)


def _predict(game: Game, obj, effect, player_id: PlayerId, color: Color) -> list[ManaKind]:
    """What ``_do_add_mana`` would add, without adding it."""
    from ..kernel.enums import Supertype
    from ..kernel.query import ValueKind
    from .resolve import Resolution, _amount2, _count, _mana_kind

    snow = game.characteristics(obj).has_supertype(Supertype.SNOW)
    resolution = Resolution(game=game, source=obj.id, controller=player_id)
    restriction = effect.mana_restriction
    if effect.mana_produced:
        repeat = 1
        if effect.amount2.kind is not ValueKind.CONSTANT or effect.amount2.constant:
            repeat = max(0, _amount2(resolution, effect))
        return [
            _mana_kind(symbol, snow=snow, restriction=restriction)
            for _ in range(repeat)
            for symbol in effect.mana_produced
        ]
    amount = _count(resolution, effect)
    if effect.colors_chosen:
        recorded = getattr(obj, "chosen_color", 0) or Color.NONE
        return [ManaKind(Color(recorded), snow=snow, restriction=restriction)] * amount
    if effect.colors:
        from .resolve import mana_color_choices

        menu = mana_color_choices(game, player_id, effect)
        if not menu:
            return []  # CR 903.4f: no colour to choose, no mana.
        pick = color if color and color in menu else _first(menu)
        return [ManaKind(pick, snow=snow, restriction=restriction)] * amount
    return [ManaKind(snow=snow, restriction=restriction)] * amount


def _first(colors: Color) -> Color:
    return next((c for c in Color if c and c in colors), Color.NONE)


def _drawback(ability) -> int:
    worst = _FREE
    for component in ability.cost.non_mana_components:
        if component.kind is CostKind.SACRIFICE:
            worst = max(worst, _SACRIFICE)
        elif component.kind is CostKind.PAY_LIFE:
            worst = max(worst, _PAINFUL)
    return worst


def _effect_drawback(ability, modes: tuple[int, ...]) -> int:
    """A pain land's damage rides on the effect, not the cost."""
    from .cr601_casting import modal_effect

    effects = ability.effects
    modal = modal_effect(effects)
    if modal is not None and modes:
        effects = _replace_modal(effects, modal, modal.children[modes[0]])
    hurts = {EffectKind.DAMAGE, EffectKind.LOSE_LIFE}
    return _PAINFUL if any(_contains(e, hurts) for e in effects) else _FREE


def _contains(effect, kinds) -> bool:
    if effect.kind in kinds:
        return True
    return any(_contains(child, kinds) for child in effect.children)


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------


def _apply(pool: ManaPool, option: ManaOption) -> ManaPool | None:
    """The pool after activating ``option``, or None if its own cost is unpaid."""
    after = pool.copy()
    if option.mana_cost:
        payment = find_payment(after, option.mana_cost)
        if payment is None:
            return None
        for kind, amount in payment.mana:
            after.remove(kind, amount)
    for kind in option.produces:
        after.add(kind, 1)
    return after


def _colour_needs(cost: ManaCost) -> dict[Color, int]:
    """Coloured symbols that admit exactly one colour, counted by colour."""
    needs: dict[Color, int] = {}
    for symbol in cost.symbols:
        if symbol.is_generic or symbol.is_choice:
            continue
        colors = [c for c in Color if c and c in symbol.colors]
        if len(colors) == 1:
            needs[colors[0]] = needs.get(colors[0], 0) + 1
    return needs


def _ceilings(sources: list[list[ManaOption]]) -> list[tuple[int, dict[Color, int]]]:
    """For each suffix of ``sources``: the most mana, and of each colour, it adds.

    Upper bounds for pruning. Net of an option's own cost, so a Signet counts
    for one, not two.
    """
    ceilings: list[tuple[int, dict[Color, int]]] = [(0, {})]
    for options in reversed(sources):
        total, per_colour = ceilings[0]
        best = max(
            len(o.produces) - (o.mana_cost.mana_value if o.mana_cost else 0) for o in options
        )
        colours = dict(per_colour)
        for colour in {k.color for o in options for k in o.produces if k.color}:
            most = max(sum(1 for k in o.produces if k.color == colour) for o in options)
            colours[colour] = colours.get(colour, 0) + most
        ceilings.insert(0, (total + max(0, best), colours))
    return ceilings


def _can_still_reach(pool: ManaPool, cost: ManaCost, needs, ceiling) -> bool:
    total, per_colour = ceiling
    if pool.total + total < cost.mana_value - _life_payable(cost):
        return False
    for colour, count in needs.items():
        have = sum(n for k, n in pool.buckets.items() if k.color == colour)
        if have + per_colour.get(colour, 0) < count:
            return False
    return True


def _life_payable(cost: ManaCost) -> int:
    """Phyrexian symbols may be paid with life instead of mana (CR 107.4f)."""
    from ..cr100_game_concepts.cr106_mana import ManaSymbolKind

    return sum(1 for s in cost.symbols if s.kind is ManaSymbolKind.PHYREXIAN)
