/**
 * Whether a cost component can be paid, and what can pay it (CR 118.3,
 * 601.2h). Ported from cr601_casting.py: _check_payable, _choose_permanents,
 * cost_permanents, discard_candidates, pick_discard, _exile_candidates,
 * material_candidates, _choose_exiled, _exile_shortfall,
 * creatures_with_total_power, _is_this_card, _PAYABLE_KINDS; and
 * _life_change_prohibited from actions.py.
 *
 * The dry run mirrors the payment's checks rather than sharing them: the
 * payment re-checks, since an earlier component may have used up what this
 * one needs (CR 118.10).
 */

import { ControllerRelationValues } from "../../ir";
import type { CostComponent, ObjectFilter } from "../../ir";
import { Zone } from "../../kernel/enums";
import { agentFor, player, zoneList, type Game } from "../../kernel/game";
import { counterCount, isLive, type GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_OBJECT, type ObjectId, type PlayerId } from "../../kernel/ids";
import { choice } from "../../kernel/random";
import { CostKind, EXILE_ZONES, componentKind, requiredAmount } from "../cr100_game_concepts/costs";
import { Act, prohibited } from "../cr500_turn_structure/restrictions";
import { charsManaValue, isCreature } from "../cr200_parts_of_a_card/characteristics";
import { flag } from "../ir_read";
import { CastError } from "./cast_error";
import { characteristicsOf } from "./layers";

export const PAYABLE_KINDS: ReadonlySet<number> = new Set([
  CostKind.PAY_LIFE, CostKind.PAY_ENERGY, CostKind.TAP_SELF, CostKind.UNTAP_SELF, CostKind.TAP_OTHER,
  CostKind.UNTAP_OTHER, CostKind.SACRIFICE, CostKind.RETURN_TO_HAND, CostKind.UNATTACH, CostKind.DISCARD,
  CostKind.DISCARD_AT_RANDOM, ...EXILE_ZONES.keys(), CostKind.EXILE_MATERIALS, CostKind.MILL,
  CostKind.REMOVE_COUNTERS, CostKind.PUT_COUNTERS, CostKind.REVEAL, CostKind.CHOOSE,
]);

/** Agents may implement this to pick what pays a sacrifice-style cost. */
export interface CostPermanentChooser {
  chooseCostPermanents?(game: Game, player: PlayerId, candidates: GameObject[], amount: number): GameObject[] | null;
}

/** "Discard this card", "exile this card from your hand": paid by the source. */
export const isThisCard = (c: CostComponent): boolean => c.filter != null && flag(c.filter.source_only);

/** The object a cost is about: the ability's source, else the object itself. */
export const costSubjectId = (context: GameObject): ObjectId => context.source || context.id;

/** CR 101.2, 119.7-8: a prohibition stopping this player's life total moving this way. */
export function lifeChangeProhibited(game: Game, playerId: PlayerId, gain: boolean): boolean {
  return [Act.LIFE_TOTAL_CHANGE, gain ? Act.GAIN_LIFE : Act.LOSE_LIFE].some((act) => prohibited(game, act, { player: playerId }) !== null);
}

/** What a component charges for this player now (`required_amount` of the evaluated amount). */
export function componentAmount(game: Game, component: CostComponent, playerId: PlayerId): number {
  const hooks = matchHooks();
  if (component.amount == null) return requiredAmount(component, 0);
  const evaluated = hooks.evaluate(game, component.amount, { controller: playerId });
  return requiredAmount(component, evaluated, hooks.wasUndeterminable());
}

const powerOf = (game: Game, obj: GameObject): number => Math.max(0, characteristicsOf(game, obj).power ?? 0);
const byManaValueThenId = (game: Game) => (a: GameObject, b: GameObject): number =>
  charsManaValue(characteristicsOf(game, a)) - charsManaValue(characteristicsOf(game, b)) || a.id - b.id;

/**
 * Raise CastError if this component could not be paid, paying nothing.
 * `spell` is a card asked about before it is cast: it can't pay for itself.
 */
export function checkPayable(
  game: Game,
  playerId: PlayerId,
  component: CostComponent,
  context: GameObject,
  options: { spell?: GameObject | null } = {},
): void {
  const spell = options.spell ?? null;
  const p = player(game, playerId);
  const amount = componentAmount(game, component, playerId);
  const kind = componentKind(component);
  const subject = (): GameObject | undefined => game.objects.get(costSubjectId(context));

  if (kind === CostKind.UNPARSED) throw new CastError("cost could not be understood");
  if (!PAYABLE_KINDS.has(kind)) throw new CastError(`cannot pay a cost of kind ${kind}`);
  if (kind === CostKind.PAY_LIFE && p.life < amount) throw new CastError("not enough life");
  // CR 119.8 (CR 119.4b: paying 0 always can).
  if (kind === CostKind.PAY_LIFE && amount > 0 && lifeChangeProhibited(game, playerId, false)) {
    throw new CastError("that player's life total can't change");
  }
  if (kind === CostKind.PAY_ENERGY && p.energy < amount) throw new CastError("not enough energy");
  if (kind === CostKind.DISCARD && isThisCard(component)) {
    if (context.zone !== Zone.HAND) throw new CastError("this card is not in its owner's hand");
    return;
  }
  if ((kind === CostKind.DISCARD || kind === CostKind.DISCARD_AT_RANDOM) && discardCandidates(game, playerId, component, context, spell).length < amount) {
    throw new CastError("not enough cards in hand to discard");
  }
  if (EXILE_ZONES.has(kind) && exileCandidates(game, playerId, component, context, spell).length < amount) {
    throw new CastError(exileShortfall(component));
  }
  if (kind === CostKind.EXILE_MATERIALS && materialCandidates(game, playerId, component, context).length < amount) {
    throw new CastError("not enough materials to exile");
  }
  if (kind === CostKind.UNTAP_SELF) {
    const obj = subject();
    if (obj === undefined || !obj.tapped || (obj.summoningSick && isCreature(characteristicsOf(game, obj)))) {
      throw new CastError("cannot untap");
    }
  }
  if (kind === CostKind.MILL && p.library.length < amount) throw new CastError("not enough cards in the library");
  if (kind === CostKind.REMOVE_COUNTERS) {
    const obj = subject();
    const type = component.counter_type ?? "";
    if (obj === undefined || counterCount(obj, type) < amount) throw new CastError(`not enough ${type} counters`);
  }
  if ((kind === CostKind.SACRIFICE || kind === CostKind.RETURN_TO_HAND) && component.filter != null) {
    if (costPermanents(game, playerId, component, context.id).length < amount) throw new CastError("not enough permanents to pay the cost");
  }
  if (kind === CostKind.TAP_SELF) {
    const obj = subject();
    if (obj === undefined || obj.tapped) throw new CastError("cannot tap");
  }
  if ((kind === CostKind.TAP_OTHER || kind === CostKind.UNTAP_OTHER) && component.filter != null) {
    // CR 702.78a: conspire's two creatures, asked before the cast is offered.
    const tapping = kind === CostKind.TAP_OTHER;
    const available = matchHooks()
      .find(game, component.filter, { source: context.id, controller: playerId })
      .filter((obj) => obj.tapped !== tapping && obj.id !== context.id);
    if (flag(component.total_power) && tapping) {
      if (creaturesWithTotalPower(game, available, amount) === null) throw new CastError(`not enough total power to tap (${amount})`);
    } else if (available.length < Math.max(1, amount)) {
      throw new CastError("not enough permanents to tap");
    }
  }
}

/**
 * Untapped creatures whose total power is at least `needed` (crew, saddle),
 * or null: the smallest single one that suffices, else strongest first.
 */
export function creaturesWithTotalPower(game: Game, candidates: readonly GameObject[], needed: number): GameObject[] | null {
  if (needed <= 0) return [];
  const power = new Map(candidates.map((o) => [o.id, powerOf(game, o)]));
  const sum = [...power.values()].reduce((a, b) => a + b, 0);
  if (sum < needed) return null;
  const alone = candidates.filter((o) => power.get(o.id)! >= needed).sort((a, b) => power.get(a.id)! - power.get(b.id)! || a.id - b.id);
  if (alone.length > 0) return [alone[0]!];
  const chosen: GameObject[] = [];
  let total = 0;
  for (const obj of [...candidates].sort((a, b) => power.get(b.id)! - power.get(a.id)! || a.id - b.id)) {
    chosen.push(obj);
    total += power.get(obj.id)!;
    if (total >= needed) return chosen;
  }
  return null;
}

/**
 * Pick permanents to pay a sacrifice-style cost (CR 118.10). Without a
 * filter, the source. Default: cheapest first, then id, so replays match.
 */
export function choosePermanents(game: Game, playerId: PlayerId, component: CostComponent, amount: number, context: GameObject): GameObject[] {
  if (component.filter == null) {
    const obj = game.objects.get(costSubjectId(context));
    return obj !== undefined ? [obj] : [];
  }
  const candidates = costPermanents(game, playerId, component, context.id);
  if (candidates.length < amount) throw new CastError("not enough permanents to pay the cost");
  const agent = agentFor(game, playerId) as CostPermanentChooser | null;
  if (agent?.chooseCostPermanents !== undefined) {
    const picked = agent.chooseCostPermanents(game, playerId, candidates, amount);
    if (picked && picked.length === amount) return [...picked];
  }
  return [...candidates].sort(byManaValueThenId(game)).slice(0, amount);
}

/** Permanents that could pay a sacrifice or return cost; you sacrifice only your own (CR 701.21a). */
export function costPermanents(game: Game, playerId: PlayerId, component: CostComponent, source: ObjectId): GameObject[] {
  const found = matchHooks().find(game, component.filter!, { source, controller: playerId });
  return componentKind(component) === CostKind.SACRIFICE ? found.filter((o) => o.controller === playerId) : found;
}

const inZone = (spec: ObjectFilter, zone: Zone, extra: Partial<ObjectFilter> = {}): ObjectFilter =>
  ({ ...spec, zones: { $set: [zone] }, ...extra }) as ObjectFilter;

/** Cards in the payer's hand that could pay a discard cost (CR 118.3); never the spell itself. */
export function discardCandidates(
  game: Game,
  playerId: PlayerId,
  component: CostComponent,
  context: GameObject | null = null,
  spell: GameObject | null = null,
): GameObject[] {
  const hand = player(game, playerId)
    .hand.filter((id) => game.objects.has(id) && (spell === null || id !== spell.id))
    .map((id) => game.objects.get(id)!);
  if (component.filter == null) return hand;
  const spec = inZone(component.filter, Zone.HAND, { count: null });
  const source = context !== null ? context.id : NO_OBJECT;
  return hand.filter((obj) => matchHooks().matches(game, obj, spec, { source, controller: playerId }));
}

/** One card to discard for a cost: at random by the game (CR 701.9b), else the payer's pick or the last. */
export function pickDiscard(game: Game, playerId: PlayerId, component: CostComponent, candidates: readonly GameObject[]): GameObject {
  if (componentKind(component) === CostKind.DISCARD_AT_RANDOM) return choice(game.rng, candidates);
  const agent = agentFor(game, playerId) as { chooseDiscard?(game: Game, player: PlayerId): ObjectId } | null;
  const picked = agent?.chooseDiscard !== undefined ? agent.chooseDiscard(game, playerId) : undefined;
  return candidates.find((c) => c.id === picked) ?? candidates[candidates.length - 1]!;
}

/** What could pay an exile cost, from the zone it names only; never the spell itself. */
export function exileCandidates(
  game: Game,
  playerId: PlayerId,
  component: CostComponent,
  context: GameObject,
  spell: GameObject | null = null,
): GameObject[] {
  const zone = EXILE_ZONES.get(componentKind(component))!;
  if (isThisCard(component)) {
    const obj = game.objects.get(costSubjectId(context));
    return obj !== undefined && isLive(obj) && obj.zone === zone ? [obj] : [];
  }
  const own = (): GameObject[] => zoneList(game, zone, playerId).map((id) => game.objects.get(id)!);
  let pool: GameObject[];
  const scope = { source: context.id, controller: playerId };
  if (component.filter == null) {
    // A bare count: any of the payer's cards there; never a permanent.
    pool = zone === Zone.BATTLEFIELD ? [] : own();
  } else {
    const spec = inZone(component.filter, zone);
    pool = zone === Zone.BATTLEFIELD
      ? matchHooks().find(game, spec, scope)
      : own().filter((obj) => matchHooks().matches(game, obj, spec, scope));
  }
  return pool.filter((obj) => spell === null || obj.id !== spell.id);
}

/** Craft's materials (CR 702.167a-b): your matching permanents and graveyard cards, not the crafter. */
export function materialCandidates(game: Game, playerId: PlayerId, component: CostComponent, context: GameObject): GameObject[] {
  const spec = component.filter;
  const scope = { source: context.id, controller: playerId };
  const onBattlefield = spec != null
    ? matchHooks().find(game, inZone(spec, Zone.BATTLEFIELD, { controller: ControllerRelationValues.YOU as never }), scope)
    : [];
  const inGraveyard = zoneList(game, Zone.GRAVEYARD, playerId)
    .map((id) => game.objects.get(id)!)
    .filter((obj) => spec == null || matchHooks().matches(game, obj, inZone(spec, Zone.GRAVEYARD), scope));
  const crafter = costSubjectId(context);
  return [...onBattlefield, ...inGraveyard].filter((obj) => obj.id !== crafter);
}

/** Pick what pays an exile cost: the top of a library in order (CR 401.2), else cheapest first. */
export function chooseExiled(game: Game, playerId: PlayerId, component: CostComponent, amount: number, context: GameObject): GameObject[] {
  const candidates = exileCandidates(game, playerId, component, context);
  if (candidates.length < amount) throw new CastError(exileShortfall(component));
  if (EXILE_ZONES.get(componentKind(component)) === Zone.LIBRARY) return candidates.slice(0, amount);
  return [...candidates].sort(byManaValueThenId(game)).slice(0, amount);
}

const ZONE_NAMES: Record<number, string> = { [Zone.BATTLEFIELD]: "battlefield", [Zone.HAND]: "hand", [Zone.GRAVEYARD]: "graveyard", [Zone.LIBRARY]: "library" };

export function exileShortfall(component: CostComponent): string {
  const zone = ZONE_NAMES[EXILE_ZONES.get(componentKind(component))!] ?? "zone";
  return isThisCard(component) ? `this card is not in the ${zone}` : `not enough to exile from the ${zone}`;
}
