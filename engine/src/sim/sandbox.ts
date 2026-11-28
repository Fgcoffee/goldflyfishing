/**
 * A board you drive by hand, on the TypeScript engine: the port of
 * mtgfish/ui/sandbox.py's board half. Search, inspect and judge read the
 * parser and stay on the server; everything that touches the game is here,
 * so the sandbox runs in the browser on the same engine as a run.
 *
 * Every method answers in the JSON Python's `Sandbox` returns (`state()`,
 * `legal()`, `targets_for()`), so the website's sandbox screen draws either.
 *
 * Replayable: the sandbox records its seed, its starting rules, the IR of
 * every card put on the board, and every operation in order (`session()`).
 * `Sandbox.replay(session)` rebuilds the exact position. That is what a
 * report of a sandbox step stores.
 *
 * The opponent passes, on purpose: a sandbox where the other player fights
 * back is a game, not an instrument.
 */

// Installed by import, as rules/play.ts does: filter matching (rules/match)
// and the replacement-effect hooks the engine calls through kernel/hooks.ts.
import "../rules/match";
import "../rules/cr600_spells_and_abilities/replacement";
import { complete, faceAbilities, type CardDocument } from "../ir";
import type { Ability } from "../ir/types";
import { Color, Phase, Step, Zone } from "../kernel/enums";
import { event, EventKind } from "../kernel/events";
import { createObject, invalidateAllCharacteristics, newGame, nextPlayer, permanents, player as playerOf, type Game } from "../kernel/game";
import type { GameObject } from "../kernel/gameobject";
import { isPlayerTarget, targetPlayer, type ObjectId, type PlayerId } from "../kernel/ids";
import type { AbilityProvider } from "../kernel/game";
import { STRICT, RELAXATION_DESCRIPTIONS, type Relaxations } from "../kernel/relaxations";
import type { CardDef } from "../kernel/card";
import { newPlayer } from "../rules/cr100_game_concepts/player";
import { ActionKind, PassingAgent, perform, settle, type Action } from "../rules/cr100_game_concepts/priority";
import { addCounters } from "../rules/cr100_game_concepts/actions";
import { abilityKeyword, type Characteristics } from "../rules/cr200_parts_of_a_card/characteristics";
import { typeLineText } from "../rules/cr200_parts_of_a_card/typeline";
import { endCombat } from "../rules/cr500_turn_structure/combat";
import { TURN_SEQUENCE, takeTurn, turnBasedActions, DEFAULT_TURN_OPTIONS } from "../rules/cr500_turn_structure/turn";
import { expireAtEndOfCombat } from "../rules/cr600_spells_and_abilities/durations";
import { characteristicsOf } from "../rules/cr600_spells_and_abilities/layers";
import { applySelfEntryReplacements } from "../rules/cr600_spells_and_abilities/replacement";
import { resolveTop } from "../rules/cr600_spells_and_abilities/stack";
import { abilityTargetingEffects, candidatesFor, targetingEffects } from "../rules/cr600_spells_and_abilities/targeting";
import { setCardCatalog } from "../rules/cr700_additional_rules/digital_mechanics";
import { emit } from "../rules/emit";
import { legalActions } from "../rules/legality";
import { addMana, clearPool, manaKind, poolTotal } from "../rules/mana/pool";
import { enteringCounters } from "../rules/zones";
import { flag, num, str } from "../rules/ir_read";

// -- the bench rules ------------------------------------------------------------

/** Python `BenchRules`: the first three are engine relaxations, the rest board-side. */
export interface BenchRules {
  readonly players_cannot_lose: boolean;
  readonly draws_from_an_empty_library_do_nothing: boolean;
  readonly mana_pools_persist: boolean;
  readonly no_maximum_hand_size: boolean;
  readonly unlimited_land_drops: boolean;
  readonly no_summoning_sickness: boolean;
}

export const RULE_NAMES = [
  "players_cannot_lose", "draws_from_an_empty_library_do_nothing", "mana_pools_persist",
  "no_maximum_hand_size", "unlimited_land_drops", "no_summoning_sickness",
] as const satisfies readonly (keyof BenchRules)[];

/** Every switch on: the bench's default. */
export const DEFAULT_BENCH: BenchRules = Object.freeze(Object.fromEntries(RULE_NAMES.map((n) => [n, true])) as unknown as BenchRules);
/** Every rule enforced, as in a game. */
export const STRICT_BENCH: BenchRules = Object.freeze(Object.fromEntries(RULE_NAMES.map((n) => [n, false])) as unknown as BenchRules);

export const RULE_DESCRIPTIONS: Readonly<Record<string, string>> = {
  players_cannot_lose: RELAXATION_DESCRIPTIONS.playersCannotLose,
  draws_from_an_empty_library_do_nothing: RELAXATION_DESCRIPTIONS.drawsFromAnEmptyLibraryDoNothing,
  mana_pools_persist: RELAXATION_DESCRIPTIONS.manaPoolsPersist,
  no_maximum_hand_size: "No discarding down to seven in the cleanup step (CR 402.2, 514.1), so a hand stocked for a test survives the turn",
  unlimited_land_drops: "Any number of lands each turn (CR 305.2), so a mana base can be built in one go",
  no_summoning_sickness: "Creatures can attack and tap the turn they arrive (CR 302.6), cast as well as placed",
};

const DEFAULT_MAX_HAND_SIZE = 7;

function relaxationsOf(rules: BenchRules): Relaxations {
  return {
    ...STRICT,
    playersCannotLose: rules.players_cannot_lose,
    drawsFromAnEmptyLibraryDoNothing: rules.draws_from_an_empty_library_do_nothing,
    manaPoolsPersist: rules.mana_pools_persist,
  };
}

// -- the session --------------------------------------------------------------

/** Where a card can be put when setting up a position. */
export const PLACEABLE: Readonly<Record<string, Zone>> = {
  battlefield: Zone.BATTLEFIELD,
  hand: Zone.HAND,
  graveyard: Zone.GRAVEYARD,
  exile: Zone.EXILE,
  library: Zone.LIBRARY,
  command: Zone.COMMAND,
};

/** One thing done to the board, in the order it was done. */
export type SandboxOp =
  | { readonly op: "put"; readonly oracle_id: string; readonly zone: string; readonly player: number; readonly count: number }
  | { readonly op: "perform"; readonly player: number; readonly action: Action }
  | { readonly op: "resolve" }
  | { readonly op: "settle" }
  | { readonly op: "advance" }
  | { readonly op: "next_turn" }
  | { readonly op: "give_mana"; readonly amount: number; readonly player: number }
  | { readonly op: "set_life"; readonly life: number; readonly player: number }
  | { readonly op: "set_rules"; readonly values: Readonly<Record<string, boolean>> };

/** Everything needed to rebuild a sandbox position exactly. Plain data. */
export interface SandboxSession {
  readonly seed: number;
  readonly rules: BenchRules;
  /** The IR of every card put on the board, by oracle id. */
  readonly cards: Readonly<Record<string, CardDocument>>;
  readonly ops: readonly SandboxOp[];
}

/** Most copies one `put` places. */
export const MAX_PUT = 60;
/** Log entries a state carries back. */
const LOG_TAIL = 200;

const ACTION_KIND_NAMES: Readonly<Record<number, string>> = Object.fromEntries(
  Object.entries(ActionKind).map(([name, value]) => [value, name]),
);

/** Names for the phase and step enums, for the JSON the page reads. */
const nameOf = (table: Readonly<Record<string, number>>, value: number): string =>
  Object.entries(table).find(([, v]) => v === value)?.[0] ?? String(value);

export class Sandbox {
  game!: Game;
  rules: BenchRules;
  private readonly seed: number;
  private readonly startRules: BenchRules;
  private readonly cards = new Map<string, CardDocument>();
  private readonly cardDefs = new Map<string, CardDef>();
  private readonly abilityCache = new Map<string, readonly Ability[]>();
  private ops: SandboxOp[] = [];

  constructor(options: { seed?: number; rules?: BenchRules } = {}) {
    this.seed = options.seed ?? 0;
    this.rules = options.rules ?? DEFAULT_BENCH;
    this.startRules = this.rules;
    this.reset();
  }

  /** Rebuild a position from its session. */
  static replay(session: SandboxSession): Sandbox {
    const sandbox = new Sandbox({ seed: session.seed, rules: session.rules });
    for (const doc of Object.values(session.cards)) sandbox.learn(doc);
    for (const op of session.ops) sandbox.apply(op);
    return sandbox;
  }

  /** What rebuilds this position: seed, starting rules, cards, every operation. */
  session(): SandboxSession {
    return { seed: this.seed, rules: this.startRules, cards: Object.fromEntries(this.cards), ops: this.ops.slice() };
  }

  // -- lifecycle ----------------------------------------------------------------

  /** Two empty boards at 40 life, in the first main phase. Keeps the rules. */
  reset(): Record<string, unknown> {
    const provider: AbilityProvider = {
      abilitiesFor: (card: unknown, faceIndex: number): readonly Ability[] => {
        const oracleId = (card as CardDef | null)?.oracle_id;
        if (oracleId === undefined) return [];
        const key = `${oracleId}|${faceIndex}`;
        let found = this.abilityCache.get(key);
        if (found === undefined) {
          const face = this.cards.get(oracleId)?.faces.find((f) => f.face === faceIndex);
          found = face === undefined ? [] : faceAbilities(face);
          this.abilityCache.set(key, found);
        }
        return found;
      },
    };
    const game = newGame([newPlayer(0, { name: "You", life: 40 }), newPlayer(1, { name: "Opponent", life: 40 })], {
      seed: this.seed,
      logEnabled: true,
      abilityProvider: provider,
    });
    game.turnOrder = [0, 1];
    game.activePlayer = 0;
    game.priorityPlayer = 0;
    game.turn = 1;
    game.phase = Phase.PRECOMBAT_MAIN;
    game.step = Step.MAIN;
    game.agents.set(1, new PassingAgent());
    setCardCatalog(game, { lookup: (name: string) => this.cardByName(name) });
    this.game = game;
    this.ops = [];
    this.applyRules();
    return this.state();
  }

  // -- rules --------------------------------------------------------------------

  setRule(name: string, on: boolean): Record<string, unknown> {
    return this.setRules({ [name]: on });
  }

  setRules(values: Readonly<Record<string, boolean>>): Record<string, unknown> {
    const unknown = Object.keys(values).filter((n) => !(RULE_NAMES as readonly string[]).includes(n));
    if (unknown.length > 0) return this.state({ error: `no such bench rule: '${unknown[0]}'` });
    this.ops.push({ op: "set_rules", values: { ...values } });
    return this.state({ message: this.changeRules(values) });
  }

  private changeRules(values: Readonly<Record<string, boolean>>): string {
    const was = this.rules;
    this.rules = { ...this.rules, ...values } as BenchRules;
    // Put back the real value of a rule just switched back on (Python _restore).
    if (was.unlimited_land_drops && !this.rules.unlimited_land_drops) for (const p of this.game.players) p.maxLands = 1;
    if (was.no_maximum_hand_size && !this.rules.no_maximum_hand_size) {
      for (const p of this.game.players) p.maxHandSize = DEFAULT_MAX_HAND_SIZE;
    }
    this.applyRules();
    const changed = Object.entries(values)
      .filter(([name, on]) => Boolean(on) !== was[name as keyof BenchRules])
      .map(([name, on]) => `${name.replaceAll("_", " ")} is ${on ? "off" : "back on"}`);
    return changed.join("; ") || "nothing changed";
  }

  /** Push the switches onto the game. Only ever relaxes (Python _apply_rules). */
  private applyRules(): void {
    const game = this.game;
    game.relaxations = relaxationsOf(this.rules);
    for (const p of game.players) {
      if (this.rules.unlimited_land_drops) p.maxLands = Math.max(p.maxLands, 99);
      if (this.rules.no_maximum_hand_size) p.maxHandSize = Math.max(p.maxHandSize, 999);
    }
    if (this.rules.no_summoning_sickness) {
      let woken = false;
      for (const obj of permanents(game)) {
        if (obj.summoningSick) {
          obj.summoningSick = false;
          woken = true;
        }
      }
      if (woken) invalidateAllCharacteristics(game);
    }
  }

  // -- cards --------------------------------------------------------------------

  /** Make a card available (its IR, fetched by the page from /api/ir). */
  learn(document: CardDocument): void {
    if (this.cards.get(document.oracle_id)?.card_hash === document.card_hash) return;
    this.cards.set(document.oracle_id, document);
    this.cardDefs.delete(document.oracle_id);
    for (const key of [...this.abilityCache.keys()]) if (key.startsWith(`${document.oracle_id}|`)) this.abilityCache.delete(key);
  }

  private cardDef(oracleId: string): CardDef | null {
    let card = this.cardDefs.get(oracleId);
    if (card === undefined) {
      const doc = this.cards.get(oracleId);
      if (doc === undefined) return null;
      card = complete(doc.card) as unknown as CardDef;
      this.cardDefs.set(oracleId, card);
    }
    return card;
  }

  private cardByName(name: string): CardDef | null {
    const doc = [...this.cards.values()].find((d) => d.name.toLowerCase() === name.toLowerCase());
    return doc === undefined ? null : this.cardDef(doc.oracle_id);
  }

  /** Put copies of a known card into a zone, ready to use (Python `put`). */
  put(oracleId: string, zone = "battlefield", player = 0, count = 1): Record<string, unknown> {
    const card = this.cardDef(oracleId);
    if (card === null) return { error: `the sandbox doesn't have card ${oracleId} yet` };
    const target = PLACEABLE[zone];
    if (target === undefined) return { error: `cannot place into '${zone}'` };
    const copies = Math.max(1, Math.min(Math.floor(count), MAX_PUT));
    this.ops.push({ op: "put", oracle_id: oracleId, zone, player, count: copies });
    for (let i = 0; i < copies; i += 1) this.putOne(card, target, player);
    invalidateAllCharacteristics(this.game);
    this.applyRules();
    return this.state({ message: `put ${card.name}${copies === 1 ? "" : ` x${copies}`} into ${zone}` });
  }

  private putOne(card: CardDef, target: Zone, owner: PlayerId): void {
    const game = this.game;
    const obj = createObject(game, card, owner, target);
    if (target !== Zone.BATTLEFIELD) return;
    obj.summoningSick = false;
    obj.enteredBattlefieldTurn = game.turn;
    // Placing skips moveObject, and with it CR 614.1c (a tapland enters
    // tapped), the counters a permanent enters with (CR 306.5b, 310.4b) and
    // the enters event its triggers watch for.
    applySelfEntryReplacements(game, obj);
    for (const [kind, amount] of enteringCounters(characteristicsOf(game, obj))) {
      addCounters(game, obj, kind, amount, { source: obj.id });
    }
    emit(game, event(EventKind.ENTERS_BATTLEFIELD, { objectId: obj.id, player: obj.controller }));
  }

  giveMana(amount = 10, player = 0): Record<string, unknown> {
    this.ops.push({ op: "give_mana", amount, player });
    const pool = playerOf(this.game, player).manaPool;
    for (const color of [Color.NONE, Color.WHITE, Color.BLUE, Color.BLACK, Color.RED, Color.GREEN]) {
      addMana(pool, manaKind(color), amount);
    }
    return this.state({ message: `added ${amount} of each colour` });
  }

  setLife(life: number, player = 0): Record<string, unknown> {
    this.ops.push({ op: "set_life", life, player });
    playerOf(this.game, player).life = life;
    return this.state({ message: `life set to ${life}` });
  }

  // -- playing ------------------------------------------------------------------

  private actions(player: number): Action[] {
    this.applyRules();
    return legalActions(this.game, player);
  }

  /** Everything this player could do now, as the engine sees it (Python `legal`). */
  legal(player = 0): Record<string, unknown>[] {
    return this.actions(player).flatMap((action, index) => {
      if (action.kind === ActionKind.PASS) return [];
      const obj = this.game.objects.get(action.source);
      return [{
        index,
        kind: ACTION_KIND_NAMES[action.kind] ?? String(action.kind),
        source: action.source,
        name: this.name(obj),
        ability_index: action.abilityIndex,
        description: this.describe(action, obj),
      }];
    });
  }

  /** What an action could target, per targeting effect (Python `targets_for`). */
  targetsFor(index: number, player = 0): Record<string, unknown>[] {
    const action = this.actions(player)[index];
    if (action === undefined) return [];
    const obj = this.game.objects.get(action.source);
    if (obj === undefined) return [];
    const chars = characteristicsOf(this.game, obj);
    const isActivation = action.kind === ActionKind.ACTIVATE_ABILITY || action.kind === ActionKind.ACTIVATE_MANA_ABILITY;
    const ability = isActivation ? chars.abilities[action.abilityIndex] : undefined;
    const effects = isActivation ? (ability === undefined ? [] : abilityTargetingEffects(ability)) : targetingEffects(this.game, obj);
    return effects
      .filter((e) => e.targets != null || e.players != null)
      .map((effect) => ({
        description: str(effect.text) || nameOf(EFFECT_KIND_NAMES, num(effect.kind)).toLowerCase(),
        optional: flag((effect as { target_optional?: boolean }).target_optional),
        candidates: candidatesFor(this.game, obj, effect, player).map((t) => this.candidate(t)),
      }));
  }

  private candidate(target: number): Record<string, unknown> {
    if (isPlayerTarget(target)) {
      const who = playerOf(this.game, targetPlayer(target));
      return { id: target, name: `${who.name} (player)`, controller: who.id, is_player: true };
    }
    const obj = this.game.objects.get(target);
    return { id: target, name: this.name(obj), controller: obj?.controller ?? -1, is_player: false };
  }

  /** Take legal action `index`, with chosen targets (Python `perform`). */
  perform(index: number, player = 0, targets: readonly (readonly number[])[] | null = null): Record<string, unknown> {
    const offered = this.actions(player)[index];
    if (offered === undefined) return this.state({ error: "no such action" });
    const action: Action = targets && targets.length > 0 ? { ...offered, targets: targets.map((g) => g.map(Number)) } : offered;
    this.ops.push({ op: "perform", player, action });
    return this.doPerform(player, action);
  }

  private doPerform(player: number, action: Action): Record<string, unknown> {
    const before = this.game.log.entries.length;
    if (!perform(this.game, player, action)) {
      return this.state({ error: this.lastLog(before, ["illegal", "rewind"]) || "the engine refused that action" });
    }
    return this.state({ message: `did ${this.describe(action, this.game.objects.get(action.source))}` });
  }

  resolveTop(): Record<string, unknown> {
    this.ops.push({ op: "resolve" });
    return this.doResolve();
  }

  private doResolve(): Record<string, unknown> {
    // CR 117.5: SBAs and waiting triggers first, so a ward trigger goes on top.
    settle(this.game);
    if (this.game.stack.length === 0) return this.state({ message: "nothing on the stack" });
    resolveTop(this.game);
    settle(this.game);
    return this.state({ message: "resolved the top of the stack" });
  }

  settle(): Record<string, unknown> {
    this.ops.push({ op: "settle" });
    settle(this.game);
    return this.state({ message: "state-based actions and triggers settled" });
  }

  /** Move to the next step, running its turn-based actions (Python `advance`). */
  advance(): Record<string, unknown> {
    this.ops.push({ op: "advance" });
    return this.doAdvance();
  }

  private doAdvance(): Record<string, unknown> {
    const game = this.game;
    const sequence = TURN_SEQUENCE;
    let position = sequence.findIndex(([phase, step]) => phase === game.phase && step === game.step);
    if (position < 0) position = sequence.length - 1;
    this.endOfStep(game.step);
    const next = sequence[position + 1];
    if (next === undefined) {
      takeTurn(game, DEFAULT_TURN_OPTIONS);
      game.activePlayer = nextPlayer(game, game.activePlayer);
      return this.state({ message: `turn ${game.turn}` });
    }
    const [phase, step] = next;
    game.phase = phase;
    game.step = step;
    turnBasedActions(game, step, DEFAULT_TURN_OPTIONS);
    settle(game);
    return this.state({ message: `${nameOf(Phase, phase).toLowerCase()} / ${nameOf(Step, step).toLowerCase()}` });
  }

  /**
   * What happens as a step ends: combat ends with its end-of-combat step
   * (CR 511.3), and pools empty unless the bench keeps them (CR 500.4).
   * PORT: mirrors the private step end in rules/cr500_turn_structure/turn.ts;
   * asked rules to export one `endOfStepActions` to call instead.
   */
  private endOfStep(step: Step): void {
    const game = this.game;
    if (step === Step.END_OF_COMBAT) {
      endCombat(game);
      expireAtEndOfCombat(game);
    }
    if (!game.relaxations.manaPoolsPersist) {
      const keepCombatMana = game.phase === Phase.COMBAT && step !== Step.END_OF_COMBAT;
      for (const p of game.players) clearPool(p.manaPool, keepCombatMana, step !== Step.CLEANUP);
    }
  }

  /** Run a whole turn for the active player (Python `next_turn`). */
  nextTurn(): Record<string, unknown> {
    this.ops.push({ op: "next_turn" });
    return this.doNextTurn();
  }

  private doNextTurn(): Record<string, unknown> {
    const game = this.game;
    takeTurn(game, DEFAULT_TURN_OPTIONS);
    game.activePlayer = nextPlayer(game, game.activePlayer);
    game.phase = Phase.PRECOMBAT_MAIN;
    game.step = Step.MAIN;
    return this.state({ message: `turn ${game.turn} ended` });
  }

  /** Apply a recorded operation without recording it again (for replay). */
  private apply(op: SandboxOp): void {
    switch (op.op) {
      case "put": {
        const card = this.cardDef(op.oracle_id);
        const target = PLACEABLE[op.zone];
        if (card === null || target === undefined) throw new Error(`can't replay put of ${op.oracle_id} into ${op.zone}`);
        for (let i = 0; i < op.count; i += 1) this.putOne(card, target, op.player);
        invalidateAllCharacteristics(this.game);
        this.applyRules();
        break;
      }
      case "perform":
        this.applyRules();
        this.doPerform(op.player, op.action);
        break;
      case "resolve":
        this.doResolve();
        break;
      case "settle":
        settle(this.game);
        break;
      case "advance":
        this.doAdvance();
        break;
      case "next_turn":
        this.doNextTurn();
        break;
      case "give_mana": {
        const pool = playerOf(this.game, op.player).manaPool;
        for (const color of [Color.NONE, Color.WHITE, Color.BLUE, Color.BLACK, Color.RED, Color.GREEN]) addMana(pool, manaKind(color), op.amount);
        break;
      }
      case "set_life":
        playerOf(this.game, op.player).life = op.life;
        break;
      case "set_rules":
        this.changeRules(op.values);
        break;
    }
    this.ops.push(op);
  }

  // -- describing ---------------------------------------------------------------

  /** The whole visible position, as plain data (Python `state`). */
  state(notes: { message?: string; error?: string } = {}): Record<string, unknown> {
    this.applyRules();
    const game = this.game;
    return {
      turn: game.turn,
      phase: nameOf(Phase, game.phase),
      step: nameOf(Step, game.step),
      active_player: game.activePlayer,
      message: notes.message ?? "",
      error: notes.error ?? "",
      game_over: game.gameOver,
      winners: game.winners.slice(),
      rules: { ...this.rules },
      rule_descriptions: RULE_DESCRIPTIONS,
      players: game.players.map((p) => ({
        id: p.id,
        name: p.name,
        life: p.life,
        mana: poolTotal(p.manaPool),
        poison: p.poison,
        library: p.library.length,
        has_lost: p.hasLost,
        loss_reason: p.lossReason ? String(p.lossReason) : "",
        battlefield: game.battlefield
          .map((id) => game.objects.get(id))
          .filter((o): o is GameObject => o !== undefined && o.controller === p.id)
          .map((o) => this.object(o)),
        hand: p.hand.map((id) => game.objects.get(id)).filter((o): o is GameObject => o !== undefined).map((o) => this.object(o)),
        graveyard: p.graveyard.map((id) => game.objects.get(id)).filter((o): o is GameObject => o !== undefined).map((o) => this.object(o)),
      })),
      stack: game.stack.map((id) => game.objects.get(id)).filter((o): o is GameObject => o !== undefined).map((o) => this.object(o)),
      log: game.log.entries
        .filter((e) => e.kind !== "event")
        .slice(-LOG_TAIL)
        .map((e) => ({ kind: e.kind, text: e.text, turn: e.turn, step: e.step === null ? "" : nameOf(Step, e.step), depth: e.depth })),
      // The log index this state ends at: a report of "this step" stores it.
      log_index: game.log.entries.length,
    };
  }

  private object(obj: GameObject): Record<string, unknown> {
    const chars: Characteristics = characteristicsOf(this.game, obj);
    return {
      id: obj.id,
      name: this.name(obj),
      oracle_id: (obj.card as CardDef | null)?.oracle_id ?? "",
      type_line: typeLineText(chars.typeLine),
      power: chars.power,
      toughness: chars.toughness,
      tapped: obj.tapped,
      damage: obj.damage,
      counters: Object.fromEntries(obj.counters),
      summoning_sick: obj.summoningSick,
      keywords: [...new Set(chars.abilities.map(abilityKeyword).filter((k) => k))].sort(),
      abilities: chars.abilities.map((a) => ({ text: str(a.text), unparsed: flag(a.unparsed) })),
      unreadable: chars.abilities.some((a) => flag(a.unparsed)),
    };
  }

  private name(obj: GameObject | undefined): string {
    if (obj === undefined) return "";
    try {
      const name = characteristicsOf(this.game, obj).name;
      if (name) return name;
    } catch {
      // A gone object still needs a label.
    }
    return (obj.card as CardDef | null)?.name || `#${obj.id as ObjectId}`;
  }

  private describe(action: Action, obj: GameObject | undefined): string {
    const name = this.name(obj);
    if (action.kind === ActionKind.PLAY_LAND) return `play ${name}`;
    if (action.kind === ActionKind.CAST_SPELL) return `cast ${name}`;
    if (action.kind === ActionKind.SPECIAL) return `special action on ${name}`;
    if (obj !== undefined) {
      const ability = characteristicsOf(this.game, obj).abilities[action.abilityIndex];
      if (ability !== undefined) return `${name}: ${str(ability.text) || "ability"}`;
    }
    return `${(ACTION_KIND_NAMES[action.kind] ?? "action").toLowerCase()} ${name}`;
  }

  private lastLog(since: number, kinds: readonly string[]): string {
    const entries = this.game.log.entries;
    for (let i = entries.length - 1; i >= since; i -= 1) {
      const entry = entries[i];
      if (entry !== undefined && kinds.includes(entry.kind)) return entry.text;
    }
    return "";
  }
}

import { EffectKindValues } from "../ir/types";
const EFFECT_KIND_NAMES: Readonly<Record<string, number>> = EffectKindValues;
