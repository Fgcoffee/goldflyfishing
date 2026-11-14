/**
 * A deliberately simple bot. Ported from mtgfish/ai/simple.py.
 *
 * Not the real AI: it exists so the rules engine is exercised by something
 * that plays - lands get played, spells cast, creatures attack and block,
 * and games end in wins rather than stall-outs. Kept boring and predictable
 * on purpose. Every decision is deterministic given the game state: nothing
 * reads a clock or a global random source, because replay reconstructs a
 * game from its seed alone.
 */

import type { Effect } from "../ir";
import type { CardDef } from "../kernel/card";
import { CardType, Phase, Step } from "../kernel/enums";
import { nextPlayer, opponents, permanents, player, turnOrderFrom, type Game } from "../kernel/game";
import type { GameObject } from "../kernel/gameobject";
import { isPlayerTarget, targetPlayer, type ObjectId, type PlayerId } from "../kernel/ids";
import { flag, ir, list, num } from "../rules/ir_read";
import { ActionKind, PASS, type Action, type Agent } from "../rules/cr100_game_concepts/priority";
import { charsManaValue, hasType, isCreature, type Characteristics } from "../rules/cr200_parts_of_a_card/characteristics";
import { canBlock, lethalDamage, type Combat, type CombatAgent, type Defender, type Recipient } from "../rules/cr500_turn_structure/combat";
import { characteristicsOf, printedCharacteristics } from "../rules/cr600_spells_and_abilities/layers";
import { project, type Projection } from "./lookahead";

/**
 * Python `kernel.loops.LOOP_REPETITIONS`. PORT: loops.ts is still the rules
 * session's placeholder; `isContinuing` (a loop being re-aimed) is false
 * until it lands.
 */
export const LOOP_REPETITIONS = 8;

const TRIGGERED = ir("AbilityKind.TRIGGERED");
const charsOf = (game: Game, obj: GameObject): Characteristics => characteristicsOf(game, obj);

/** Plays out its hand and attacks when the maths is in its favour. */
export class SimpleAgent implements Agent, CombatAgent {
  readonly aggressive: boolean;
  /** Play a spell or ability out on a copy of the game before choosing it (lookahead.ts). */
  readonly lookahead: boolean;
  /** What the last looked-ahead action was projected to do. */
  lastProjection: Projection | null = null;
  /** Actions that failed in the current step, not to be tried again in it. */
  private failed = new Set<string>();
  private failedWindow = "";
  /** `${turn}|${card name}` -> times chosen for casting this turn. */
  private casts = new Map<string, number>();

  /** How many times in one turn the bot casts the same card (a recursion loop cast one card 214 times). */
  static readonly MAX_CASTS_PER_TURN = 8;
  /** How many chosen actions one decision may look ahead at and reject before passing. */
  static readonly MAX_LOOKS = 4;
  /**
   * How many times in one turn the bot activates the same ability - above
   * the loop detector's threshold on purpose, so a recognised loop is
   * shortcut first; this is only the backstop.
   */
  static readonly MAX_ACTIVATIONS_PER_TURN = LOOP_REPETITIONS + 4;

  constructor(options: { aggressive?: boolean; lookahead?: boolean } = {}) {
    this.aggressive = options.aggressive ?? true;
    this.lookahead = options.lookahead ?? true;
  }

  /** A copy with its own memory, for a projection's shadow game (Python's pickle copies agents). */
  clone(): SimpleAgent {
    const copy = new SimpleAgent({ aggressive: this.aggressive, lookahead: this.lookahead });
    copy.failed = new Set(this.failed);
    copy.failedWindow = this.failedWindow;
    copy.casts = new Map(this.casts);
    return copy;
  }

  // -- learning from failure -------------------------------------------------

  /** The engine refused an action this bot chose: don't repeat it this step. */
  actionFailed(game: Game, _player: PlayerId, act: Action): void {
    this.syncWindow(game);
    this.failed.add(key(act));
  }

  private syncWindow(game: Game): void {
    const window = `${game.turn}|${game.phase}|${game.step}`;
    if (window !== this.failedWindow) {
      this.failedWindow = window;
      this.failed = new Set();
    }
  }

  private cardName(game: Game, act: Action): string {
    return ((game.objects.get(act.source)?.card ?? null) as CardDef | null)?.name ?? "";
  }

  private castTooOften(game: Game, act: Action): boolean {
    return (this.casts.get(`${game.turn}|${this.cardName(game, act)}`) ?? 0) >= SimpleAgent.MAX_CASTS_PER_TURN;
  }

  // -- priority ----------------------------------------------------------

  /**
   * Take the most developing action, preferring lands. With lookahead on, a
   * spell or ability is first played out on a copy of the game; one the
   * engine would refuse, or one that changes nothing, is set aside for this
   * step and the next best considered.
   */
  chooseAction(game: Game, playerId: PlayerId, legalIn: readonly Action[]): Action {
    this.lastProjection = null;
    let legal = [...legalIn];
    let chosen: Action = PASS;
    let settled = false;
    for (let i = 0; i < SimpleAgent.MAX_LOOKS; i++) {
      chosen = this.pick(game, playerId, legal);
      if (!this.needsALook(game, chosen)) {
        settled = true;
        break;
      }
      const projection = project(game, playerId, chosen);
      if (projection.legal && projection.changed) {
        this.lastProjection = projection;
        settled = true;
        break;
      }
      this.actionFailed(game, playerId, chosen);
      const rejected = chosen;
      legal = legal.filter((a) => a !== rejected);
    }
    if (!settled) chosen = PASS;
    if (chosen.kind === ActionKind.CAST_SPELL) {
      const k = `${game.turn}|${this.cardName(game, chosen)}`;
      this.casts.set(k, (this.casts.get(k) ?? 0) + 1);
    }
    return chosen;
  }

  /**
   * Whether an action's outcome is uncertain enough to play out first: every
   * instant, sorcery and activated ability, and a permanent that triggers.
   */
  private needsALook(game: Game, act: Action): boolean {
    if (!this.lookahead) return false;
    if (act.kind === ActionKind.ACTIVATE_ABILITY) return true;
    if (act.kind !== ActionKind.CAST_SPELL) return false;
    if (!this.isPermanent(game, act)) return true;
    const obj = game.objects.get(act.source);
    return obj !== undefined && charsOf(game, obj).abilities.some((a) => num(a.kind) === TRIGGERED);
  }

  /** The preferred action before any looking ahead. Mana abilities are never activated speculatively. */
  private pick(game: Game, playerId: PlayerId, legalIn: readonly Action[]): Action {
    this.syncWindow(game);
    const legal = this.failed.size ? legalIn.filter((a) => !this.failed.has(key(a))) : legalIn;

    const lands = legal.filter((a) => a.kind === ActionKind.PLAY_LAND);
    if (lands.length) return this.bestLand(game, lands);

    let casts = legal.filter((a) => a.kind === ActionKind.CAST_SPELL && !this.castTooOften(game, a));
    if (casts.length) casts = this.worthCastingNow(game, playerId, casts);
    if (casts.length) return this.bestSpell(game, casts);

    const activations = legal.filter((a) => a.kind === ActionKind.ACTIVATE_ABILITY && !this.isMana(game, a) && !this.repeatedTooOften(game, a));
    if (activations.length) return activations[0]!;
    return PASS;
  }

  private repeatedTooOften(game: Game, act: Action): boolean {
    const obj = game.objects.get(act.source);
    if (obj === undefined) return false;
    // PORT: Python spares a loop being re-aimed (loops.is_continuing).
    return (obj.activationsThisTurn.get(act.abilityIndex) ?? 0) >= SimpleAgent.MAX_ACTIVATIONS_PER_TURN;
  }

  /**
   * Spend on your own turn, hold on everyone else's - except the end step
   * before your own turn (use it or lose it), and permanents with flash.
   */
  private worthCastingNow(game: Game, playerId: PlayerId, casts: Action[]): Action[] {
    const ownTurn = game.activePlayer === playerId;
    if (ownTurn && (game.phase === Phase.PRECOMBAT_MAIN || game.phase === Phase.POSTCOMBAT_MAIN)) return casts;
    const lastChance = game.step === Step.END_STEP && nextPlayer(game, game.activePlayer) === playerId;
    if (lastChance) return casts;
    return casts.filter((a) => this.isPermanent(game, a) && !ownTurn);
  }

  private isPermanent(game: Game, act: Action): boolean {
    const obj = game.objects.get(act.source);
    if (obj === undefined) return false;
    return (charsOf(game, obj).typeLine.types & (CardType.INSTANT | CardType.SORCERY)) === 0;
  }

  private isMana(game: Game, act: Action): boolean {
    const obj = game.objects.get(act.source);
    if (obj === undefined) return false;
    const ability = charsOf(game, obj).abilities[act.abilityIndex];
    return ability !== undefined && flag(ability.is_mana_ability);
  }

  /** Prefer a land with more mana abilities; ties by object id. */
  private bestLand(game: Game, lands: Action[]): Action {
    const rank = (a: Action): [number, number] => {
      const obj = game.objects.get(a.source)!;
      return [-printedCharacteristics(game, obj).abilities.filter((x) => flag(x.is_mana_ability)).length, obj.id];
    };
    return minBy(lands, rank);
  }

  /** The most expensive thing affordable, preferring creatures; ties by id. */
  private bestSpell(game: Game, casts: Action[]): Action {
    const rank = (a: Action): [number, number, number] => {
      const obj = game.objects.get(a.source)!;
      const chars = charsOf(game, obj);
      return [-Number(hasType(chars, CardType.CREATURE)), -charsManaValue(chars), obj.id];
    };
    return minBy(casts, rank);
  }

  // -- resolution choices -------------------------------------------------

  chooseOptional(): boolean {
    return true;
  }

  /** Discard the most expensive card, as the least castable. */
  chooseDiscard(game: Game, playerId: PlayerId): ObjectId {
    const hand = player(game, playerId).hand;
    if (hand.length === 0) return 0;
    return maxBy(hand, (id) => [charsManaValue(printedCharacteristics(game, game.objects.get(id)!)), id]);
  }

  orderTriggers(_game: Game, _player: PlayerId, triggers: unknown[]): unknown[] {
    return triggers;
  }

  // -- combat -------------------------------------------------------------

  /**
   * Attack the player closest to losing with every creature that has power;
   * a non-aggressive bot keeps back what the best blocker would kill.
   */
  declareAttackers(game: Game, playerId: PlayerId, candidates: readonly GameObject[]): ReadonlyMap<ObjectId, Defender> {
    const opps = opponents(game, playerId);
    const out = new Map<ObjectId, Defender>();
    if (opps.length === 0) return out;
    const target = this.threatTarget(game, playerId, opps);
    const blockers = opps.flatMap((o) => permanents(game, o)).filter((b) => isCreature(charsOf(game, b)) && !b.tapped);
    const bestBlocker = Math.max(0, ...blockers.map((b) => charsOf(game, b).power ?? 0));
    for (const obj of [...candidates].sort((a, b) => a.id - b.id)) {
      const chars = charsOf(game, obj);
      if ((chars.power ?? 0) <= 0) continue;
      if (!this.aggressive && (chars.toughness ?? 0) <= bestBlocker) continue;
      out.set(obj.id, target);
    }
    return out;
  }

  /** CR 702.154a: enlist the strongest creature staying home, if it has power to lend. */
  chooseEnlist(game: Game, _player: PlayerId, _attacker: GameObject, eligible: readonly GameObject[]): ObjectId | null {
    if (eligible.length === 0) return null;
    const best = maxBy([...eligible], (o) => [charsOf(game, o).power ?? 0, -o.id]);
    return (charsOf(game, best).power ?? 0) > 0 ? best.id : null;
  }

  /**
   * Whoever is closest to dying, then whoever has the biggest board; an exact
   * tie by turn order relative to the attacker, so a table spreads its
   * attacks instead of piling onto the lowest seat.
   */
  private threatTarget(game: Game, playerId: PlayerId, opps: PlayerId[]): PlayerId {
    const distance = new Map(turnOrderFrom(game, playerId).map((p, i) => [p, i]));
    const rank = (o: PlayerId): [number, number, number] => {
      const board = permanents(game, o)
        .filter((x) => isCreature(charsOf(game, x)))
        .reduce((sum, x) => sum + (charsOf(game, x).power ?? 0), 0);
      return [player(game, o).life, -board, distance.get(o) ?? 99];
    };
    return minBy(opps, rank);
  }

  /**
   * Block what would kill us and take free trades: the biggest attackers
   * first, each with the first creature that survives or kills it - or any,
   * when the incoming damage is lethal.
   */
  declareBlockers(game: Game, playerId: PlayerId, combat: Combat, available: readonly GameObject[]): ReadonlyMap<ObjectId, readonly ObjectId[]> {
    const incoming = [...combat.attacking.entries()]
      .sort((a, b) => a[0] - b[0])
      .filter(([id, defender]) => defender === playerId && game.objects.has(id))
      .map(([id]) => game.objects.get(id)!);
    const out = new Map<ObjectId, ObjectId[]>();
    if (incoming.length === 0) return out;
    const totalDamage = incoming.reduce((sum, a) => sum + (charsOf(game, a).power ?? 0), 0);
    const desperate = totalDamage >= player(game, playerId).life;
    const used = new Set<ObjectId>();
    const byPower = [...incoming].sort((a, b) => (charsOf(game, b).power ?? 0) - (charsOf(game, a).power ?? 0));
    const pool = [...available].sort((a, b) => a.id - b.id);
    for (const attacker of byPower) {
      const ac = charsOf(game, attacker);
      for (const blocker of pool) {
        if (used.has(blocker.id) || !canBlock(game, blocker, attacker)) continue;
        const bc = charsOf(game, blocker);
        const survives = (bc.toughness ?? 0) > (ac.power ?? 0);
        const kills = (bc.power ?? 0) >= (ac.toughness ?? 0);
        if (survives || kills || desperate) {
          out.set(blocker.id, [attacker.id]);
          used.add(blocker.id);
          break;
        }
      }
    }
    return out;
  }

  /**
   * Kill the biggest blocker first (CR 510.1c): lethal damage to the largest
   * creature, then the next; whatever isn't a creature last (CR 702.19b),
   * with any remainder on the last in order.
   */
  assignCombatDamage(game: Game, _chooser: PlayerId, _source: ObjectId, recipients: readonly Recipient[], total: number): ReadonlyMap<number, number> {
    const priority = (who: Recipient, i: number): [number, number, number] =>
      typeof who === "number" ? [1, 0, i] : [0, -(charsOf(game, who).power ?? 0), i];
    const order = recipients.map((_, i) => i).sort((a, b) => compare(priority(recipients[a]!, a), priority(recipients[b]!, b)));
    const division = new Map<number, number>();
    let remaining = total;
    for (const index of order) {
      if (remaining <= 0) break;
      const who = recipients[index]!;
      if (typeof who === "number") continue; // a player a trampler spills onto
      const assigned = Math.min(remaining, lethalDamage(game, who));
      if (assigned > 0) {
        division.set(index, assigned);
        remaining -= assigned;
      }
    }
    if (remaining > 0 && order.length) {
      const last = order[order.length - 1]!;
      division.set(last, (division.get(last) ?? 0) + remaining);
    }
    return division;
  }

  // -- targets --------------------------------------------------------------

  /**
   * Aim harmful effects at opponents and helpful ones at yourself; within
   * each group the biggest creature (or the lowest-life player).
   */
  chooseTargets(game: Game, playerId: PlayerId, source: ObjectId, candidates: readonly (readonly number[])[]): number[][] {
    const effects = targetedEffects(game, source);
    return candidates.map((group, index) => {
      if (group.length === 0) return [];
      const e = effects[index];
      const harmful = e !== undefined && HARMFUL.has(num(e.kind));
      return [this.best(game, playerId, group, harmful)];
    });
  }

  /** CR 707.10c: aim a copy as if casting it. */
  chooseNewTargets(game: Game, playerId: PlayerId, copy: ObjectId, _current: unknown, candidates: readonly (readonly number[])[]): number[][] {
    return this.chooseTargets(game, playerId, copy, candidates);
  }

  private best(game: Game, playerId: PlayerId, group: readonly number[], harmful: boolean): number {
    const mine = group.filter((id) => controlledBy(game, id, playerId));
    const theirs = group.filter((id) => !mine.includes(id));
    const preferred = harmful ? theirs : mine;
    const pool = preferred.length ? preferred : [...group];
    const size = (id: number): [number, number] => {
      if (isPlayerTarget(id)) return [0, -player(game, targetPlayer(id)).life];
      const obj = game.objects.get(id);
      if (obj === undefined) return [0, id];
      const c = charsOf(game, obj);
      return [(c.power ?? 0) + (c.toughness ?? 0), -id];
    };
    return maxBy(pool, size);
  }
}

/**
 * Effects that are bad for whatever they point at; the bot aims these at
 * opponents and everything else at itself.
 */
export const HARMFUL: ReadonlySet<number> = new Set(
  ["DESTROY", "EXILE", "DAMAGE", "COUNTER_SPELL", "SACRIFICE", "RETURN_TO_HAND", "TAP", "GOAD", "LOSE_LIFE", "MILL", "DISCARD", "REMOVE_COUNTERS", "GAIN_CONTROL", "FIGHT"].map(
    (n) => ir(`EffectKind.${n}` as never) as number,
  ),
);

function* walk(effect: Effect): Generator<Effect> {
  yield effect;
  for (const child of [...list(effect.children), ...list(effect.otherwise)]) yield* walk(child);
}

/** The targeted effects of whatever is being cast or activated, in slot order. */
function targetedEffects(game: Game, source: ObjectId): Effect[] {
  const obj = game.objects.get(source);
  if (obj === undefined) return [];
  const abilities = obj.ability !== null ? [obj.ability] : [...charsOf(game, obj).abilities];
  const out: Effect[] = [];
  for (const ability of abilities) for (const top of list(ability.effects)) for (const node of walk(top)) {
    // A verb sharing the previous one's "target" holds no slot.
    if (flag(node.is_targeted) && !flag(node.same_target)) out.push(node);
  }
  return out;
}

/** A player target is "mine" when it is me; an object when I control it. */
function controlledBy(game: Game, id: number, playerId: PlayerId): boolean {
  if (isPlayerTarget(id)) return targetPlayer(id) === playerId;
  return game.objects.get(id)?.controller === playerId;
}

function key(act: Action): string {
  return `${act.kind}|${act.source}|${act.abilityIndex}|${act.faceIndex}|${act.alternativeCost}`;
}

/** Lexicographic comparison of number tuples (Python's tuple ordering). */
function compare(a: readonly number[], b: readonly number[]): number {
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i]! - b[i]!;
  return a.length - b.length;
}

/** Python's min(items, key=...): the first of the smallest. */
function minBy<T>(items: readonly T[], rank: (item: T) => readonly number[]): T {
  let best: T = items[0] as T;
  let bestKey = rank(best);
  for (const item of items.slice(1)) {
    const k = rank(item);
    if (compare(k, bestKey) < 0) {
      best = item;
      bestKey = k;
    }
  }
  return best;
}

/** Python's max(items, key=...): the first of the largest. */
function maxBy<T>(items: readonly T[], rank: (item: T) => readonly number[]): T {
  let best: T = items[0] as T;
  let bestKey = rank(best);
  for (const item of items.slice(1)) {
    const k = rank(item);
    if (compare(k, bestKey) > 0) {
      best = item;
      bestKey = k;
    }
  }
  return best;
}
