/**
 * One game, start to finish: the engine entry point the runner calls.
 * Ported from `play_one` in mtgfish/sim/runner.py, with the parts of
 * mtgfish/sim/observer.py (`Observer`, `finish`, `new_record`) that fill a
 * `GameRecord`.
 *
 * Seed in, record out, and nothing else: every random choice comes from the
 * game's seeded PRNG, so `play(setup, index, seed)` always plays the same
 * game and gives the same digest.
 *
 * As in Python, no mulligans are taken (`play_one` never calls them) and
 * the record's per-turn series and removal events are left out (the web
 * session's records.ts leaves them out until it wants them).
 */

// Installed once, here, for every game (the parser's note): the matching,
// values and conditions hooks, and the replacement hooks.
import "./match";
import "./cr600_spells_and_abilities/replacement";

import { complete, faceAbilities } from "../ir";
import type { Ability, CardDocument } from "../ir";
import type { CardDef } from "../kernel/card";
import { EventKind, type GameEvent } from "../kernel/events";
import { player as playerOf, type AbilityProvider, type Game } from "../kernel/game";
import type { GameObject } from "../kernel/gameobject";
import { logDigest } from "../kernel/log";
import type { CardCast, Elimination, GameRecord, WinReason } from "../sim/records";
import type { DeckSpec, GameEngine, RunSetup } from "../sim/runner";
import { PassingAgent, type Agent } from "./cr100_game_concepts/priority";
import { setUpGame, type SetupDeck } from "./cr100_game_concepts/setup";
import { lossReasonName, LossReason } from "../kernel/enums";
import { characteristicsOf } from "./cr600_spells_and_abilities/layers";
import { runGame, type TurnOptions } from "./cr500_turn_structure/turn";
import { setCardCatalog } from "./cr700_additional_rules/digital_mechanics";

/** The run is about seat 0 (Python `RunConfig.hero`). */
export const HERO = 0;

export interface PlayOptions {
  /** Who decides for each seat. Python seats its SimpleAgent; until the AI is ported, everyone passes. */
  readonly agentFor?: (seat: number) => Agent;
  /** The full rules log (a replay). Off for a run, as in Python. */
  readonly log?: boolean;
  /** A second observer, called after the record's with every event; it must only read. */
  readonly watch?: (game: Game, e: GameEvent) => void;
}

/** What a run of games shares: the decks as card definitions, and the IR by card. */
interface Prepared {
  readonly decks: SetupDeck[];
  readonly provider: AbilityProvider;
  readonly byName: Map<string, CardDef>;
}

/** Per setup object, so a run of games builds its decks and reads its IR once. */
const PREPARED = new WeakMap<RunSetup, Prepared>();

function prepare(setup: RunSetup): Prepared {
  const cached = PREPARED.get(setup);
  if (cached !== undefined) return cached;
  const documentOf = (oracleId: string): CardDocument => {
    const doc = setup.cards[oracleId];
    if (doc === undefined) throw new Error(`no IR for card ${oracleId}`);
    return doc;
  };
  // The printed card with the IR's defaults filled in: the kernel's CardDef. One object per card.
  const cards = new Map<string, CardDef>();
  const cardOf = (oracleId: string): CardDef => {
    let card = cards.get(oracleId);
    if (card === undefined) cards.set(oracleId, (card = complete(documentOf(oracleId).card) as unknown as CardDef));
    return card;
  };
  const deckOf = (deck: DeckSpec, seat: number): SetupDeck => {
    const library: CardDef[] = [];
    for (const entry of deck.cards) {
      const card = cardOf(entry.oracle_id);
      for (let i = 0; i < entry.count; i += 1) library.push(card);
    }
    return { name: `P${seat}`, library, commanders: deck.commanders.map(cardOf) };
  };
  // Abilities with every default filled in, read once per face.
  const abilities = new Map<string, readonly Ability[]>();
  const provider: AbilityProvider = {
    abilitiesFor(card: unknown, faceIndex: number): readonly Ability[] {
      const oracleId = (card as CardDef | null)?.oracle_id;
      if (oracleId === undefined) return [];
      const key = `${oracleId}|${faceIndex}`;
      let found = abilities.get(key);
      if (found === undefined) {
        const face = setup.cards[oracleId]?.faces.find((f) => f.face === faceIndex);
        found = face === undefined ? [] : faceAbilities(face);
        abilities.set(key, found);
      }
      return found;
    },
  };
  const byName = new Map<string, CardDef>();
  for (const doc of Object.values(setup.cards)) byName.set(doc.name, cardOf(doc.oracle_id));
  const prepared: Prepared = { decks: setup.decks.map(deckOf), provider, byName };
  PREPARED.set(setup, prepared);
  return prepared;
}

/** Play game `index` of a run with `seed`, and return its record. */
export function play(setup: RunSetup, index: number, seed: number, options: PlayOptions = {}): GameRecord {
  return playGame(setup, index, seed, options).record;
}

/** `play`, also returning the finished game (Python's `log=True` form, for replays and tests). */
export function playGame(setup: RunSetup, index: number, seed: number, options: PlayOptions = {}): { record: GameRecord; game: Game } {
  if (setup.decks.length === 0) throw new Error("a game needs at least one deck");
  const { decks, provider, byName } = prepare(setup);
  const game = setUpGame(decks, { seed, logEnabled: options.log ?? false, abilityProvider: provider });
  // MTG Arena's conjure names cards by name; the run's cards are where they are found.
  setCardCatalog(game, { lookup: (name: string) => byName.get(name) ?? null });

  const observer = new Observer(game.players.length, HERO);
  const watch = options.watch;
  game.observer = watch === undefined ? (g, e) => observer.observe(g, e) : (g, e) => (observer.observe(g, e), watch(g, e));

  for (const p of game.players) game.agents.set(p.id, options.agentFor?.(p.id) ?? new PassingAgent());
  observer.recordOpeningHand(game);

  const turnOptions: TurnOptions = { firstPlayerSkipsFirstDraw: false, maxRounds: setup.maxRounds };
  const finished = runGame(game, turnOptions);
  return { record: observer.finish(finished, index, seed), game: finished };
}

/** The runner's view of this engine. */
export const rulesEngine: GameEngine = {
  name: "rules",
  play: (setup, index, seed) => play(setup, index, seed),
};

/**
 * Python `Observer`: reads the event stream and writes down what happened.
 * It never asks the engine to do anything differently, so a watched game is
 * the same game as an unwatched one.
 */
class Observer {
  private readonly drawn: string[] = [];
  private readonly drawnSeen = new Set<string>();
  private readonly cast: CardCast[] = [];
  private readonly eliminations: Elimination[] = [];
  private readonly commanderLanded: (number | null)[];
  private readonly names = new Map<number, string>();
  /** Turns each player has ended (Python `_own_turns`). */
  private readonly ownTurns = new Map<number, number>();
  /** Turns each player has begun: what a person means by "turn N". */
  private readonly turnsBegun = new Map<number, number>();
  private openingHand: string[] = [];
  lastCombatDamageTurn = -1;

  constructor(
    seats: number,
    private readonly hero: number,
  ) {
    this.commanderLanded = Array<number | null>(seats).fill(null);
  }

  observe(game: Game, e: GameEvent): void {
    switch (e.kind) {
      case EventKind.DREW_CARD:
        this.drew(game, e);
        break;
      case EventKind.CAST_SPELL:
        this.castSpell(game, e);
        break;
      case EventKind.COMBAT_DAMAGE_DEALT:
        this.lastCombatDamageTurn = game.turn;
        break;
      case EventKind.TURN_BEGAN:
        this.turnsBegun.set(e.player, (this.turnsBegun.get(e.player) ?? 0) + 1);
        break;
      case EventKind.TURN_ENDED:
        this.endOfTurn(game);
        break;
      case EventKind.PLAYER_LEFT_GAME:
        this.eliminated(game, e);
        break;
    }
  }

  recordOpeningHand(game: Game): void {
    const names: string[] = [];
    for (const id of playerOf(game, this.hero).hand) {
      const obj = game.objects.get(id);
      if (obj === undefined) continue;
      names.push((obj.card as CardDef | null)?.name || "unknown");
    }
    this.openingHand = names;
    for (const name of names) this.addDrawn(name);
  }

  private addDrawn(name: string): void {
    // Python keeps a set of names.
    if (this.drawnSeen.has(name)) return;
    this.drawnSeen.add(name);
    this.drawn.push(name);
  }

  private drew(game: Game, e: GameEvent): void {
    if (e.player !== this.hero) return;
    const hand = playerOf(game, this.hero).hand;
    if (hand.length === 0) return;
    const obj = game.objects.get(hand[hand.length - 1]!);
    if (obj !== undefined) this.addDrawn(this.nameOf(game, obj));
  }

  private castSpell(game: Game, e: GameEvent): void {
    if (e.player !== this.hero) return;
    const obj = game.objects.get(e.objectId);
    if (obj === undefined) return;
    // The caster's own turn number, not the global counter (seat position).
    const round = this.turnsBegun.get(this.hero) ?? 0;
    this.cast.push({ name: this.nameOf(game, obj), round });
    if (obj.isCommander && this.commanderLanded[this.hero] === null) {
      this.commanderLanded[this.hero] = (this.ownTurns.get(this.hero) ?? 0) + 1;
    }
  }

  private eliminated(game: Game, e: GameEvent): void {
    // CR 800.4: leaving is PLAYER_LEFT_GAME, not PLAYER_LOST.
    const reason = playerOf(game, e.player).lossReason;
    this.eliminations.push({ turn: game.turn, player: e.player, reason: reason !== null ? lossReasonName(reason) : "unknown" });
  }

  private endOfTurn(game: Game): void {
    const p = playerOf(game, game.activePlayer);
    if (p.hasLost) return;
    this.ownTurns.set(p.id, (this.ownTurns.get(p.id) ?? 0) + 1);
  }

  /** A card's name, cached; falls back to the card for an object that is gone. */
  private nameOf(game: Game, obj: GameObject): string {
    const cached = this.names.get(obj.id);
    if (cached !== undefined) return cached;
    let name = "";
    try {
      name = characteristicsOf(game, obj).name;
    } catch {
      name = "";
    }
    if (!name) name = (obj.card as CardDef | null)?.name || "unknown";
    this.names.set(obj.id, name);
    return name;
  }

  /** Python `finish`: everything only knowable once the game is over. */
  finish(game: Game, index: number, seed: number): GameRecord {
    const living = game.players.filter((p) => !p.hasLost);
    const winner = game.winners.length > 0 ? game.winners[0]! : living.length === 1 ? living[0]!.id : null;
    return {
      index,
      seed,
      turns: game.turn,
      rounds: this.turnsBegun.get(this.hero) ?? 0,
      winner,
      winReason: this.winReason(game, winner),
      eliminations: this.eliminations.slice(),
      commanderLanded: this.commanderLanded.slice(),
      mulligans: game.players.map(() => 0),
      openingHand: this.openingHand.slice(),
      drawn: this.drawn.slice(),
      cast: this.cast.slice(),
      digest: logDigest(game.log),
    };
  }

  /**
   * Python `_win_reason`, mapped onto records.ts's `WinReason` names: how the
   * winner won, inferred from how the last player lost.
   */
  private winReason(game: Game, winner: number | null): WinReason {
    if (winner === null) return game.loopDraw ? "DRAW" : "STALL_OUT";
    const last = this.eliminations[this.eliminations.length - 1];
    if (last === undefined) return "ALTERNATE"; // Python LAST_STANDING
    const loser = playerOf(game, last.player);
    switch (loser.lossReason) {
      case LossReason.LIFE:
        return "LIFE"; // Python COMBAT_DAMAGE, NONCOMBAT_DAMAGE or LOOP_KILL
      case LossReason.EMPTY_LIBRARY:
        return "DECKING";
      case LossReason.POISON:
        return "POISON";
      case LossReason.COMMANDER_DAMAGE:
        return "COMMANDER_DAMAGE";
      case LossReason.CONCEDE:
        return "CONCEDE";
      default:
        return "ALTERNATE";
    }
  }
}

