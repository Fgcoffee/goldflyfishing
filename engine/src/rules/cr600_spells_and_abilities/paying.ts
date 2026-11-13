/**
 * Paying costs (CR 601.2g-h, 602.2b, 118). Ported from cr601_casting.py:
 * _pay, _pay_with_helpers, helper_capacity, colour_matching,
 * cost_left_for_sources, _helper_sources, _tap_for_mana, _record_paid,
 * _pay_component, _scaled_mana, can_pay_cost, pay_cost, the mana
 * checkpoint, activation_refusal, the activation limits,
 * activation_mana_cost, _can_pay_activation, _could_produce,
 * _pay_activation.
 *
 * A cost is paid in full or not at all (CR 601.2h): every component is
 * checked before any mana leaves the pool, and a failure throws CastError.
 */

import type { Ability, Cost, CostComponent } from "../../ir";
import { CardType, Color, Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { agentFor, invalidateAllCharacteristics, permanents, player, type Game } from "../../kernel/game";
import { addCounters as putObjectCounters, counterCount, enteredThisTurn, isLive, removeCounters as takeObjectCounters, type GameObject } from "../../kernel/gameobject";
import { matchHooks } from "../../kernel/hooks";
import { NO_OBJECT, type ObjectId, type PlayerId } from "../../kernel/ids";
import { addCounters, bounce, detach, discard, exile, mill, removeCounters, sacrifice } from "../cr100_game_concepts/actions";
import {
  CostKind,
  componentKind,
  components,
  costIsUnparsed,
  EXILE_ZONES,
  finalMana,
  manaComponent,
  nonManaComponents,
  requiredAmount,
  requiresTapping,
  type TotalCost,
} from "../cr100_game_concepts/costs";
import { loseLife } from "../cr100_game_concepts/player";
import { charsManaValue, hasKeyword, hasType, isCreature } from "../cr200_parts_of_a_card/characteristics";
import { emit } from "../emit";
import { flag, ir, list, num, numSet } from "../ir_read";
import {
  fromIR as fromIRCost,
  increasedBy,
  isEmpty,
  manaValue,
  ManaSymbolKind,
  reducedBy,
  reducedByMana,
  substituteX,
  type ManaCost,
} from "../mana/cost";
import { findPayment, paymentTotalMana } from "../mana/payment";
import { addMana, manaKind, poolSnapshot, poolTotal, removeMana } from "../mana/pool";
import { moveObject } from "../zones";
import { CastError } from "./cast_error";
import {
  checkPayable,
  chooseExiled,
  choosePermanents,
  componentAmount,
  costPermanents,
  costSubjectId,
  creaturesWithTotalPower,
  discardCandidates,
  isThisCard,
  materialCandidates,
  PAYABLE_KINDS,
  pickDiscard,
  type CostPermanentChooser,
} from "./cost_checks";
import { walk } from "./effect_props";
import { characteristicsOf } from "./layers";
import { canProduceFor, executePlanFor, paymentContextOf, planPaymentFor } from "./mana_host";
import { asItWouldBeCast } from "./total_cost";

const chars = (game: Game, obj: GameObject) => characteristicsOf(game, obj);
const UNPARSED_KIND: number = ir("EffectKind.UNPARSED");

// -- paying for a spell -------------------------------------------------------------

/** Activate mana abilities as needed, then pay the whole cost (CR 601.2g-h). */
export function pay(game: Game, playerId: PlayerId, total: TotalCost, context: GameObject | null): void {
  const p = player(game, playerId);
  const manaCost = finalMana(total);
  let helpedWith = 0;
  // CR 118.3, 601.2h: every non-mana component is checked before any mana is spent.
  // KNOWN: F61 - Python passes a None context on to the component checks; no
  // caller with a null context (the companion's {3}) has non-mana components.
  for (const component of total.additional) checkPayable(game, playerId, component, context!);

  if (!isEmpty(manaCost)) {
    const paymentContext = paymentContextOf(context);
    const find = () => findPayment(p.manaPool, manaCost, { lifeAvailable: p.life, context: paymentContext });
    let payment = find();
    if (payment === null) {
      tapForMana(game, playerId, manaCost, context);
      payment = find();
    }
    if (payment === null && context !== null) {
      // CR 702.51a, 702.126a, 702.66a: convoke, improvise, delve - as few as will do.
      let found = false;
      const capacity = helperCapacity(game, context, playerId);
      for (let helped = 1; helped <= capacity; helped += 1) {
        if (canProduceFor(game, playerId, reducedBy(manaCost, helped), context)) {
          helpedWith = payWithHelpers(game, playerId, manaCost, context, helped);
          tapForMana(game, playerId, manaCost, context);
          found = true;
          break;
        }
      }
      if (!found) {
        // CR 702.51a: a creature pays a coloured pip of its own colour too.
        helpedWith = payWithHelpers(game, playerId, manaCost, context);
        tapForMana(game, playerId, manaCost, context);
      }
      payment = find();
    }
    if (payment === null) throw new CastError("cannot pay the mana cost");

    const onStack = context !== null && context.zone === Zone.STACK;
    // CR 601.2h, 702.51a, 702.66a: the mana actually spent.
    if (onStack) context!.manaSpent = Math.max(0, payment.mana.reduce((t, [, n]) => t + n, 0) - helpedWith);
    for (const [kind, amount] of payment.mana) {
      removeMana(p.manaPool, kind, amount);
      if (amount > 0 && onStack) context!.manaSpentColors |= kind.color; // CR 702.44a, converge
    }
    if (payment.life) {
      loseLife(p, payment.life);
      if (onStack) context!.phyrexianPaidWithLife += Math.floor(payment.life / 2); // CR 702.150a
    }
    emit(game, event(EventKind.MANA_SPENT, { player: playerId, amount: paymentTotalMana(payment) }));
  }
  for (const component of total.additional) payComponent(game, playerId, component, context!);
}

/** Which keyword draws on which resource, and whether its mana takes the source's colour. */
const COST_HELPERS: readonly [string, "creature" | "artifact" | "graveyard", boolean][] = [
  ["Convoke", "creature", true],
  ["Improvise", "artifact", false],
  ["Delve", "graveyard", false],
];

function helperEligible(game: Game, source: GameObject, resource: string): boolean {
  return resource === "graveyard" || (!source.tapped && (resource !== "creature" || hasType(chars(game, source), CardType.CREATURE)));
}

const lowestBit = (n: number): number => n & -n;

/**
 * Tap or exile things that stand in for mana, adding the mana to the pool
 * (the spell still costs its full mana value, CR 702.51b). Returns how much.
 */
function payWithHelpers(game: Game, playerId: PlayerId, cost: ManaCost, context: GameObject, most: number | null = null): number {
  const spellChars = chars(game, context);
  const p = player(game, playerId);
  let shortfall = Math.max(0, manaValue(cost) - poolTotal(p.manaPool));
  if (most !== null) shortfall = Math.min(shortfall, most);
  if (shortfall <= 0) return 0;
  let paid = 0;
  const pipColours = cost.symbols.filter((s) => s.kind === ManaSymbolKind.COLORED).map((s) => s.colors as number);
  for (const [keyword, resource, coloured] of COST_HELPERS) {
    if (!hasKeyword(spellChars, keyword.toLowerCase())) continue;
    const sources = helperSources(game, playerId, resource).filter((s) => helperEligible(game, s, resource));
    const colourOf = new Map<ObjectId, Color>();
    if (coloured) {
      // CR 702.51a: each creature makes the colour of the pip it is matched to, and goes first.
      const masks = sources.map((s) => chars(game, s).colors as number);
      const assigned = colourMatching(masks, pipColours);
      sources.forEach((s, i) => {
        const pip = assigned[i]!;
        if (pip >= 0) colourOf.set(s.id, lowestBit(pipColours[pip]! & masks[i]!) as Color);
      });
      // Stable: matched first, otherwise in order (Python's sort on a bool key).
      sources.sort((a, b) => Number(!colourOf.has(a.id)) - Number(!colourOf.has(b.id)));
    }
    for (const source of sources) {
      if (shortfall <= 0) return paid;
      if (resource === "graveyard") {
        moveObject(game, source, Zone.EXILE);
        addMana(p.manaPool, manaKind(Color.NONE), 1);
      } else {
        source.tapped = true;
        let colour = colourOf.get(source.id);
        if (colour === undefined && coloured) {
          const own = chars(game, source).colors as number;
          colour = own ? (lowestBit(own) as Color) : undefined;
        }
        addMana(p.manaPool, manaKind(colour ?? Color.NONE), 1);
      }
      shortfall -= 1;
      paid += 1;
    }
  }
  return paid;
}

/** How much of a cost convoke, improvise and delve could cover: an upper bound. */
export function helperCapacity(game: Game, spell: GameObject, playerId: PlayerId): number {
  const spellChars = asItWouldBeCast(game, spell, chars(game, spell)); // CR 601.2a-b
  let total = 0;
  for (const [keyword, resource] of COST_HELPERS) {
    if (hasKeyword(spellChars, keyword.toLowerCase())) total += helperSources(game, playerId, resource).length;
  }
  return total;
}

/** Which coloured pip each helper pays, as a maximum matching (CR 702.51a); -1 for none. */
export function colourMatching(helperColours: readonly number[], pipColours: readonly number[]): number[] {
  const owner = pipColours.map(() => -1);
  const place = (helper: number, seen: Set<number>): boolean => {
    for (let pip = 0; pip < pipColours.length; pip += 1) {
      if (seen.has(pip) || !(pipColours[pip]! & helperColours[helper]!)) continue;
      seen.add(pip);
      if (owner[pip]! < 0 || place(owner[pip]!, seen)) {
        owner[pip] = helper;
        return true;
      }
    }
    return false;
  };
  helperColours.forEach((colours, helper) => {
    if (colours) place(helper, new Set());
  });
  const assigned = helperColours.map(() => -1);
  owner.forEach((helper, pip) => {
    if (helper >= 0) assigned[helper] = pip;
  });
  return assigned;
}

/** What other mana sources must still make once convoke, improvise and delve pay all they can. */
export function costLeftForSources(game: Game, spell: GameObject, playerId: PlayerId, cost: ManaCost): ManaCost {
  const spellChars = asItWouldBeCast(game, spell, chars(game, spell));
  const helperColours: number[] = [];
  for (const [keyword, resource, coloured] of COST_HELPERS) {
    if (!hasKeyword(spellChars, keyword.toLowerCase())) continue;
    for (const source of helperSources(game, playerId, resource)) {
      if (resource !== "graveyard") {
        if (source.tapped) continue;
        if (resource === "creature" && !hasType(chars(game, source), CardType.CREATURE)) continue;
      }
      helperColours.push(coloured ? (chars(game, source).colors as number) : 0);
    }
  }
  const pips = cost.symbols.flatMap((s, i) => (s.kind === ManaSymbolKind.COLORED ? [i] : []));
  const assigned = colourMatching(helperColours, pips.map((i) => cost.symbols[i]!.colors as number));
  const paid = new Set(assigned.filter((pip) => pip >= 0).map((pip) => pips[pip]!));
  const spare = assigned.filter((pip) => pip < 0).length;
  return reducedBy({ symbols: cost.symbols.filter((_, i) => !paid.has(i)) }, spare);
}

/** What a helper keyword may draw on, in a stable order (by id) for replays. */
function helperSources(game: Game, playerId: PlayerId, resource: string): GameObject[] {
  if (resource === "graveyard") {
    return player(game, playerId).graveyard.filter((id) => game.objects.has(id)).map((id) => game.objects.get(id)!);
  }
  const wanted = resource === "creature" ? CardType.CREATURE : CardType.ARTIFACT;
  return permanents(game, playerId)
    .filter((obj) => {
      const c = chars(game, obj);
      return (
        !obj.tapped &&
        hasType(c, wanted) &&
        !(resource === "creature" && obj.summoningSick && hasType(c, CardType.CREATURE)) &&
        obj.zone === Zone.BATTLEFIELD
      );
    })
    .sort((a, b) => a.id - b.id);
}

/** CR 601.2g: activate mana abilities for what is still needed, as the planner says; nothing if no plan. */
export function tapForMana(game: Game, playerId: PlayerId, cost: ManaCost, context: GameObject | null): void {
  const plan = planPaymentFor(game, playerId, cost, context);
  if (plan !== null && plan.length > 0) executePlanFor(game, playerId, plan);
}

/** Note an object a cost is about to consume, as it is now (CR 608.2h). */
function recordPaid(game: Game, context: GameObject, obj: GameObject): void {
  context.costPaidObjects.push(obj.id);
  game.costPaidLastKnown.set(obj.id, chars(game, obj));
}

/** Pay one non-mana component, checking first (CR 118.3); throws CastError rather than paying part. */
export function payComponent(game: Game, playerId: PlayerId, component: CostComponent, context: GameObject): void {
  const p = player(game, playerId);
  const amount = componentAmount(game, component, playerId);
  const kind = componentKind(component);
  const subject = () => game.objects.get(costSubjectId(context));
  if (kind === CostKind.UNPARSED) throw new CastError("cost could not be understood");
  if (!PAYABLE_KINDS.has(kind)) throw new CastError(`cannot pay a cost of kind ${kind}`);

  if (kind === CostKind.PAY_LIFE) {
    // CR 118.3b: down to 0 is legal; the loss comes with the next SBA check.
    if (p.life < amount) throw new CastError("not enough life");
    loseLife(p, amount);
  } else if (kind === CostKind.PAY_ENERGY) {
    if (p.energy < amount) throw new CastError("not enough energy");
    p.energy -= amount;
  } else if (kind === CostKind.TAP_SELF) {
    const obj = subject();
    if (obj === undefined || obj.tapped) throw new CastError("cannot tap");
    obj.tapped = true;
  } else if (kind === CostKind.TAP_OTHER || kind === CostKind.UNTAP_OTHER) {
    // No summoning-sickness check: CR 302.6 is only about a permanent's own {T}.
    const tapping = kind === CostKind.TAP_OTHER;
    const wanted = Math.max(1, amount);
    let candidates = matchHooks()
      .find(game, component.filter!, { source: context.id, controller: playerId })
      .filter((obj) => obj.tapped !== tapping);
    let chosen: GameObject[];
    if (flag(component.total_power) && tapping) {
      candidates = candidates.filter((obj) => obj.id !== context.id); // CR 702.122a, 702.171a
      const picked = creaturesWithTotalPower(game, candidates, amount);
      if (picked === null) throw new CastError(`not enough total power to tap (${amount})`);
      chosen = picked;
    } else if (candidates.length < wanted) {
      throw new CastError("not enough permanents to tap");
    } else {
      chosen = candidates.slice(0, wanted);
    }
    for (const obj of chosen) {
      obj.tapped = tapping;
      recordPaid(game, context, obj);
      emit(game, event(tapping ? EventKind.TAPPED : EventKind.UNTAPPED, { objectId: obj.id, player: playerId }));
    }
  } else if (kind === CostKind.SACRIFICE || kind === CostKind.RETURN_TO_HAND || kind === CostKind.UNATTACH) {
    for (const obj of choosePermanents(game, playerId, component, amount, context)) {
      if (kind === CostKind.SACRIFICE) {
        recordPaid(game, context, obj); // "the sacrificed creature's power" (CR 608.2h)
        sacrifice(game, obj, { source: context.id });
      } else if (kind === CostKind.RETURN_TO_HAND) {
        recordPaid(game, context, obj); // ninjutsu's Ninja attacks what this was attacking (CR 702.49c)
        bounce(game, obj, { source: context.id });
      } else {
        detach(game, obj);
      }
    }
  } else if (kind === CostKind.UNTAP_SELF) {
    // CR 107.6 {Q}, with CR 302.6 as for {T}.
    const obj = subject();
    if (obj === undefined || !obj.tapped || (obj.summoningSick && isCreature(chars(game, obj)))) throw new CastError("cannot untap");
    obj.tapped = false;
    emit(game, event(EventKind.UNTAPPED, { objectId: obj.id, player: playerId }));
  } else if (kind === CostKind.DISCARD && isThisCard(component)) {
    if (context.zone !== Zone.HAND) throw new CastError("this card is not in its owner's hand");
    discard(game, context, { source: context.id });
  } else if (kind === CostKind.DISCARD || kind === CostKind.DISCARD_AT_RANDOM) {
    const candidates = discardCandidates(game, playerId, component, context);
    if (candidates.length < amount) throw new CastError("not enough cards in hand to discard");
    for (let i = 0; i < amount; i += 1) {
      const obj = pickDiscard(game, playerId, component, candidates);
      candidates.splice(candidates.indexOf(obj), 1);
      recordPaid(game, context, obj);
      discard(game, obj, { source: context.id });
    }
  } else if (EXILE_ZONES.has(kind)) {
    for (const obj of chooseExiled(game, playerId, component, amount, context)) {
      recordPaid(game, context, obj);
      exile(game, obj, { source: context.id });
    }
  } else if (kind === CostKind.EXILE_MATERIALS) {
    const candidates = materialCandidates(game, playerId, component, context);
    if (candidates.length < amount) throw new CastError("not enough materials to exile");
    // Graveyard cards before permanents, then cheapest, then id.
    const ordered = [...candidates].sort(
      (a, b) =>
        Number(a.zone === Zone.BATTLEFIELD) - Number(b.zone === Zone.BATTLEFIELD) ||
        charsManaValue(chars(game, a)) - charsManaValue(chars(game, b)) ||
        a.id - b.id,
    );
    for (const obj of ordered.slice(0, amount)) {
      recordPaid(game, context, obj);
      exile(game, obj, { source: context.id });
    }
  } else if (kind === CostKind.MILL) {
    if (p.library.length < amount) throw new CastError("not enough cards in the library");
    mill(game, playerId, amount, { source: context.id });
  } else if (kind === CostKind.REMOVE_COUNTERS) {
    const obj = subject();
    const type = component.counter_type ?? "";
    if (obj === undefined || counterCount(obj, type) < amount) throw new CastError(`not enough ${type} counters`);
    removeCounters(game, obj, type, amount);
  } else if (kind === CostKind.PUT_COUNTERS) {
    const obj = subject();
    if (obj !== undefined) addCounters(game, obj, component.counter_type ?? "", amount);
  }
  // REVEAL, CHOOSE: nothing is spent; the choice is the cost.
}

// -- costs charged to a player (CR 118.12, 702.21a) -----------------------------------

/** A cost's mana, multiplied by any per-permanent scale. */
function scaledMana(game: Game, playerId: PlayerId, cost: Cost): ManaCost {
  const mana = manaComponent(cost);
  if (isEmpty(mana)) return mana;
  const scale = components(cost).find((c) => componentKind(c) === CostKind.MANA && c.scale != null)?.scale;
  if (scale == null) return mana;
  const times = matchHooks().evaluate(game, scale, { controller: playerId });
  if (times <= 1) return times === 1 ? mana : increasedBy(mana, -manaValue(mana));
  return increasedBy(mana, manaValue(mana) * (times - 1));
}

/** Non-mana components a player can be charged with no activation behind them. */
export const PLAYER_PAYABLE_KINDS: ReadonlySet<number> = new Set([
  CostKind.MANA, CostKind.PAY_LIFE, CostKind.DISCARD, CostKind.DISCARD_AT_RANDOM, CostKind.SACRIFICE,
]);

function playerAmount(game: Game, playerId: PlayerId, component: CostComponent, source: ObjectId): number {
  const hooks = matchHooks();
  const evaluated = component.amount == null ? 0 : hooks.evaluate(game, component.amount, { source, controller: playerId });
  return requiredAmount(component, evaluated, component.amount != null && hooks.wasUndeterminable());
}

/** Whether a player could pay a cost with no source permanent ("unless that player pays"). */
export function canPayCost(game: Game, playerId: PlayerId, cost: Cost, options: { source?: ObjectId } = {}): boolean {
  const source = options.source ?? NO_OBJECT;
  const choices = list(cost.choices);
  if (choices.length > 0) return choices.some((each) => canPayCost(game, playerId, each, options));
  const p = player(game, playerId);
  for (const component of nonManaComponents(cost)) {
    const kind = componentKind(component);
    if (!PLAYER_PAYABLE_KINDS.has(kind)) return false;
    let amount: number;
    try {
      amount = playerAmount(game, playerId, component, source);
    } catch (e) {
      if (e instanceof CastError) return false; // CR 903.4f
      throw e;
    }
    if (kind === CostKind.PAY_LIFE) {
      if (p.life <= amount) return false;
    } else if (kind === CostKind.DISCARD || kind === CostKind.DISCARD_AT_RANDOM) {
      if (discardCandidates(game, playerId, component).length < amount) return false;
    } else if (kind === CostKind.SACRIFICE) {
      if (component.filter == null || costPermanents(game, playerId, component, source).length < amount) return false;
    }
  }
  const manaCost = scaledMana(game, playerId, cost);
  if (isEmpty(manaCost)) return true;
  if (findPayment(p.manaPool, manaCost, { lifeAvailable: p.life }) !== null) return true;
  return canProduceFor(game, playerId, manaCost);
}

/** Charge a player-scoped cost; false if it could not be paid in full. */
export function payCost(game: Game, playerId: PlayerId, cost: Cost, options: { source?: ObjectId } = {}): boolean {
  const source = options.source ?? NO_OBJECT;
  const choices = list(cost.choices);
  if (choices.length > 0) {
    for (const each of choices) if (canPayCost(game, playerId, each, options)) return payCost(game, playerId, each, options);
    return false;
  }
  const p = player(game, playerId);
  const manaCost = scaledMana(game, playerId, cost);
  if (!isEmpty(manaCost)) {
    let payment = findPayment(p.manaPool, manaCost, { lifeAvailable: p.life });
    if (payment === null) {
      // No cast to rewind during a resolution: undo the tapping and the player simply didn't pay.
      const checkpoint = manaCheckpoint(game, playerId);
      try {
        tapForMana(game, playerId, manaCost, null);
      } catch (e) {
        if (!(e instanceof CastError)) throw e;
        restoreManaCheckpoint(game, playerId, checkpoint);
        return false;
      }
      payment = findPayment(p.manaPool, manaCost, { lifeAvailable: p.life });
      if (payment === null) {
        restoreManaCheckpoint(game, playerId, checkpoint);
        return false;
      }
    }
    for (const [kind, amount] of payment.mana) removeMana(p.manaPool, kind, amount);
    if (payment.life) loseLife(p, payment.life);
  }
  for (const component of nonManaComponents(cost)) {
    const amount = playerAmount(game, playerId, component, source);
    const kind = componentKind(component);
    if (kind === CostKind.PAY_LIFE) {
      loseLife(p, amount);
    } else if (kind === CostKind.DISCARD || kind === CostKind.DISCARD_AT_RANDOM) {
      const candidates = discardCandidates(game, playerId, component);
      for (let i = 0, n = Math.min(amount, candidates.length); i < n; i += 1) {
        const obj = pickDiscard(game, playerId, component, candidates);
        candidates.splice(candidates.indexOf(obj), 1);
        discard(game, obj, { source }); // a real discard (CR 701.8)
      }
    } else if (kind === CostKind.SACRIFICE && component.filter != null) {
      const candidates = costPermanents(game, playerId, component, source);
      const agent = agentFor(game, playerId) as CostPermanentChooser | null;
      let picked = agent?.chooseCostPermanents?.(game, playerId, candidates, amount) ?? null;
      if (!picked || picked.length !== amount) {
        picked = [...candidates]
          .sort((a, b) => charsManaValue(chars(game, a)) - charsManaValue(chars(game, b)) || a.id - b.id)
          .slice(0, amount);
      }
      for (const obj of picked) sacrifice(game, obj, { source });
    }
  }
  return true;
}

interface ManaCheckpoint {
  tapped: Map<ObjectId, boolean>;
  pool: ReturnType<typeof poolSnapshot>;
  life: number;
  pending: number;
  graveyard: ObjectId[];
}

export function manaCheckpoint(game: Game, playerId: PlayerId): ManaCheckpoint {
  const p = player(game, playerId);
  return {
    tapped: new Map(permanents(game, playerId).map((o) => [o.id, o.tapped])),
    pool: poolSnapshot(p.manaPool),
    life: p.life,
    pending: game.pendingTriggers.length,
    graveyard: p.graveyard.slice(),
  };
}

/** Undo the mana abilities of an unmade payment (a sacrificed Treasure can't be put back). */
export function restoreManaCheckpoint(game: Game, playerId: PlayerId, checkpoint: ManaCheckpoint): void {
  const p = player(game, playerId);
  // CR 601.2h: a card exiled for delve goes back.
  for (const id of checkpoint.graveyard) {
    const obj = game.objects.get(id);
    const became = obj !== undefined ? game.objects.get(obj.supersededBy) : undefined;
    if (became !== undefined && isLive(became) && became.zone === Zone.EXILE) moveObject(game, became, Zone.GRAVEYARD);
  }
  for (const [id, wasTapped] of checkpoint.tapped) {
    const obj = game.objects.get(id);
    if (obj !== undefined) obj.tapped = wasTapped;
  }
  for (const [kind, n] of poolSnapshot(p.manaPool)) removeMana(p.manaPool, kind, n);
  for (const [kind, n] of checkpoint.pool) addMana(p.manaPool, kind, n);
  p.life = checkpoint.life;
  game.pendingTriggers.length = checkpoint.pending;
  invalidateAllCharacteristics(game);
}

// -- activating abilities (CR 602, 605, 606) -------------------------------------------

/** Why this player may not activate this ability where it is, or null (CR 113.6, 602.2, 602.5b). */
export function activationRefusal(game: Game, playerId: PlayerId, source: GameObject, ability: Ability): string | null {
  if (!numSet(ability.functions_in, [Zone.BATTLEFIELD]).has(source.zone)) return "that ability does not function in that zone";
  // CR 602.2, 108.4a: a card in hand has no controller; its owner activates.
  const activator = source.zone === Zone.BATTLEFIELD || source.zone === Zone.STACK ? source.controller : source.owner;
  if (activator !== playerId) return "only its controller may activate that ability";
  if (list(ability.effects).some((e) => [...walk(e)].some(isUnparsedEffect))) return "ability could not be understood";
  const condition = ability.activation_condition;
  if (condition !== undefined && num(condition.kind) !== 0 && !matchHooks().holds(game, condition, { source: source.id, controller: playerId })) {
    return "that ability's activation restriction is not met";
  }
  return null;
}

const isUnparsedEffect = (e: { kind?: unknown }): boolean => num(e.kind) === UNPARSED_KIND;

/** CR 606.3: the loyalty limit is per permanent, counted under its own key. */
export const LOYALTY_ACTIVATIONS = -1;

/** Whether a limit on how often this ability is activated has been hit (CR 602.5b, 606.3). */
export function activationLimitReached(source: GameObject, ability: Ability, index: number): boolean {
  const thisTurn = source.activationsThisTurn;
  if (flag(ability.is_loyalty_ability) && ((thisTurn.get(LOYALTY_ACTIVATIONS) ?? 0) || (thisTurn.get(index) ?? 0))) return true;
  if (flag(ability.once_each_turn) && (thisTurn.get(index) ?? 0) >= 1) return true;
  return flag(ability.only_once) && (source.activationsEver.get(index) ?? 0) >= 1;
}

export function recordActivation(source: GameObject, ability: Ability, index: number): void {
  const thisTurn = source.activationsThisTurn;
  thisTurn.set(index, (thisTurn.get(index) ?? 0) + 1);
  source.activationsEver.set(index, (source.activationsEver.get(index) ?? 0) + 1);
  if (flag(ability.is_loyalty_ability)) thisTurn.set(LOYALTY_ACTIVATIONS, (thisTurn.get(LOYALTY_ACTIVATIONS) ?? 0) + 1);
}

/** The mana an activation costs after its own reductions: power-up (CR 702.193a-b, 118.7). */
export function activationManaCost(game: Game, source: GameObject, ability: Ability): ManaCost {
  const mana = manaComponent(ability.cost);
  if (isEmpty(mana) || !flag(ability.reduced_by_own_mana_cost_on_entry) || source.zone !== Zone.BATTLEFIELD || !enteredThisTurn(source, game.turn)) {
    return mana;
  }
  const own = substituteX(fromIRCost(chars(game, source).manaCost), 0);
  return isEmpty(own) ? mana : reducedByMana(mana, own);
}

/** Whether an ability's cost could be paid now, for an announced X (CR 107.3a). */
export function canPayActivation(game: Game, source: GameObject, ability: Ability, xValue = 0): boolean {
  const cost = ability.cost;
  if (costIsUnparsed(cost)) return false;
  const p = player(game, source.controller);
  const hooks = matchHooks();
  if (requiresTapping(cost)) {
    if (source.tapped) return false;
    if (isCreature(chars(game, source)) && source.summoningSick) return false; // CR 302.6
  }
  const evalAmount = (c: CostComponent, x = 0): number =>
    c.amount == null ? 0 : hooks.evaluate(game, c.amount, { controller: p.id, xValue: x });
  for (const component of nonManaComponents(cost)) {
    const kind = componentKind(component);
    if (kind === CostKind.PAY_LIFE) {
      if (p.life < evalAmount(component)) return false;
    } else if (kind === CostKind.LOYALTY) {
      // CR 606.4, 606.6: a minus ability needs the counters; [-X] no more than it has.
      const change = evalAmount(component, xValue);
      if (change < 0 && counterCount(source, "loyalty") < -change) return false;
    } else if (kind === CostKind.TAP_OTHER || kind === CostKind.UNTAP_OTHER) {
      const wanted = Math.max(1, evalAmount(component));
      const tapping = kind === CostKind.TAP_OTHER;
      let available = hooks.find(game, component.filter!, { source: source.id, controller: p.id }).filter((o) => o.tapped !== tapping);
      if (flag(component.total_power) && tapping) {
        available = available.filter((o) => o.id !== source.id); // CR 702.122a
        if (creaturesWithTotalPower(game, available, evalAmount(component)) === null) return false;
      } else if (available.length < wanted) {
        return false;
      }
    } else if (kind === CostKind.UNPARSED) {
      return false;
    } else {
      // The same dry run payment uses, so legality and payment can't disagree.
      try {
        checkPayable(game, p.id, component, source);
      } catch (e) {
        if (e instanceof CastError) return false;
        throw e;
      }
    }
  }
  const manaCost = substituteX(activationManaCost(game, source, ability), xValue);
  if (isEmpty(manaCost)) return true;
  if (findPayment(p.manaPool, manaCost, { lifeAvailable: p.life }) !== null) return true;
  // A mana ability's own cost is left to the planner's search rather than recursing.
  if (flag(ability.is_mana_ability)) return couldProduce(game, source.controller, manaCost);
  return canProduceFor(game, source.controller, manaCost, source);
}

/** A cheap upper bound on whether untapped sources could cover a cost: never too low. */
function couldProduce(game: Game, playerId: PlayerId, cost: ManaCost): boolean {
  let available = 0;
  for (const obj of permanents(game, playerId)) {
    if (obj.tapped) continue;
    const c = chars(game, obj);
    if (isCreature(c) && obj.summoningSick) continue;
    if (c.abilities.some((a) => flag(a.is_mana_ability))) available += 1;
  }
  return available >= manaValue(cost);
}

/**
 * Pay an activation cost, with X as announced (CR 107.3a, 601.2f-h). {T}
 * is paid by setting it tapped and emitting TAPPED, as Python does (not
 * through `actions.tap`). Throws CastError if it can't be paid.
 */
export function payActivation(game: Game, source: GameObject, ability: Ability, xValue = 0): void {
  const cost = ability.cost;
  const p = player(game, source.controller);
  if (requiresTapping(cost)) {
    source.tapped = true;
    emit(game, event(EventKind.TAPPED, { objectId: source.id, player: source.controller }));
  }
  const manaCost = substituteX(activationManaCost(game, source, ability), xValue);
  if (!isEmpty(manaCost)) {
    let payment = findPayment(p.manaPool, manaCost, { lifeAvailable: p.life });
    if (payment === null) {
      tapForMana(game, source.controller, manaCost, source);
      payment = findPayment(p.manaPool, manaCost, { lifeAvailable: p.life });
    }
    if (payment === null) throw new CastError("cannot pay the mana cost");
    for (const [kind, amount] of payment.mana) removeMana(p.manaPool, kind, amount);
    if (payment.life) loseLife(p, payment.life);
  }
  for (const component of nonManaComponents(cost)) {
    const kind = componentKind(component);
    if (kind === CostKind.TAP_SELF) continue;
    if (kind === CostKind.LOYALTY) {
      const change = component.amount == null ? 0 : matchHooks().evaluate(game, component.amount, { controller: source.controller, xValue });
      if (change >= 0) putObjectCounters(source, "loyalty", change);
      else if (takeObjectCounters(source, "loyalty", -change) < -change) throw new CastError("not enough loyalty counters");
      invalidateAllCharacteristics(game);
      continue;
    }
    payComponent(game, source.controller, component, source);
  }
}
