/**
 * Starting a game (CR 103, 806.3, 903.6, 903.7). Ported from
 * mtgfish/rules/cr100_game_concepts/cr103_setup.py.
 *
 * The sequence is fixed: decide turn order, everyone shuffles, everyone
 * draws seven, then mulligans. Commanders begin in the command zone rather
 * than the library (CR 903.6), which is why a Commander deck is 99 + 1.
 *
 * CR 903.2 makes it a Free-for-All (CR 806) with the attack multiple
 * players option and without limited range of influence. Both are honoured
 * by omission; the only code they need is CR 806.3, random seating.
 *
 * Everything random draws from the game's seeded PRNG, the turn-order roll
 * included, so a replay reproduces who went first.
 */

import type { CardDef } from "../../kernel/card";
import { Phase, Step, Zone } from "../../kernel/enums";
import { createObject, logRecord, newGame, type AbilityProvider, type Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import type { PlayerId } from "../../kernel/ids";
import { shuffle } from "../../kernel/random";
import type { Relaxations } from "../../kernel/relaxations";
import { isLand } from "../cr200_parts_of_a_card/characteristics";
import { printedCharacteristics } from "../cr600_spells_and_abilities/layers";
import { moveObject } from "../zones";
import { draw, shuffleLibrary } from "./actions";
import { COMMANDER_STARTING_LIFE, newPlayer, STARTING_HAND_SIZE } from "./player";

/** A deck as setup needs it: Python's `Deck` (`library_cards()`, `commanders`, ...). */
export interface SetupDeck {
  readonly name?: string;
  /** The library: one entry per copy. */
  readonly library: readonly CardDef[];
  readonly commanders?: readonly CardDef[];
  /** CR 400.11a: stays outside the game, as card definitions. */
  readonly sideboard?: readonly CardDef[];
  /** CR 103.2b. PENDING(rules): revealing it needs cr400_outside_game. */
  readonly companion?: CardDef | null;
  /** CR 717.2. PENDING(rules): set up with the Attractions port. */
  readonly attractions?: readonly CardDef[];
}

export interface SetupOptions {
  seed?: number;
  logEnabled?: boolean;
  abilityProvider?: AbilityProvider;
  relaxations?: Relaxations;
  /** CR 103.1; tests turn it off for a fixed order. */
  randomizeTurnOrder?: boolean;
}

/**
 * A decision: given the game, the player and how many mulligans they've
 * taken, whether to mulligan again.
 */
export type MulliganPolicy = (game: Game, playerId: PlayerId, mulligansTaken: number) => boolean;

/**
 * Build a game from decks, up to but not including the first turn (Python
 * `cr103_setup.new_game`). Libraries are shuffled and opening hands drawn;
 * mulligans are a separate step so the caller can supply a policy.
 */
export function setUpGame(decks: readonly SetupDeck[], options: SetupOptions = {}): Game {
  const players = decks.map((deck, index) => newPlayer(index, { name: deck.name || `Player ${index + 1}` }));
  const game = newGame(players, {
    seed: options.seed ?? 0,
    logEnabled: options.logEnabled ?? false,
    ...(options.abilityProvider !== undefined ? { abilityProvider: options.abilityProvider } : {}),
    ...(options.relaxations !== undefined ? { relaxations: options.relaxations } : {}),
  });
  // CR 727.2: a restarted game has every card of this one, so keep the decks.
  game.sourceDecks = [...decks];

  decks.forEach((deck, index) => {
    const p = players[index]!;
    for (const card of deck.library) createObject(game, card, index, Zone.LIBRARY);
    // CR 903.6: commanders start in the command zone, face up.
    const commanderIds = [];
    for (const card of deck.commanders ?? []) {
      const obj = createObject(game, card, index, Zone.COMMAND);
      obj.isCommander = true;
      commanderIds.push(obj.id);
    }
    p.commanders = commanderIds;
    // CR 903.11a: what a card brought in later may not share a name with.
    p.startingDeckNames = new Set([...deck.library, ...(deck.commanders ?? [])].map((c) => c.name));
    p.outsideGame.push(...(deck.sideboard ?? []));
    // PENDING(rules): CR 103.2b companion reveal (cr400_outside_game) and the
    // CR 717.2 Attraction deck aren't ported; a companion still waits outside.
    if (deck.companion) p.outsideGame.push(deck.companion);
  });

  // CR 103.1, CR 806.3: turn order at random, then fixed for the game.
  const order: PlayerId[] = players.map((p) => p.id);
  if (options.randomizeTurnOrder ?? true) shuffle(game.rng, order);
  game.turnOrder = order;
  game.activePlayer = order[0]!;
  game.priorityPlayer = order[0]!;
  game.turn = 0;
  game.phase = Phase.BEGINNING;
  game.step = Step.UNTAP;

  // PORT: CR 103.3a also shuffles each Attraction deck, with the Attractions port.
  for (const id of order) shuffleLibrary(game, id);
  // CR 903.7: once the starting player is known, life becomes 40 and each draws seven.
  for (const id of order) players[id]!.life = COMMANDER_STARTING_LIFE;
  for (const id of order) draw(game, id, STARTING_HAND_SIZE);

  logRecord(game, "Turn order: " + order.map((id) => players[id]!.name).join(", "), { kind: "setup" });
  return game;
}

/**
 * The London mulligan (CR 103.5). Each mulligan shuffles the hand back and
 * draws a fresh seven; on keeping, the player puts one card on the bottom
 * per mulligan. Returns how many each player took.
 */
export function takeMulligans(
  game: Game,
  policy: MulliganPolicy,
  options: { maximum?: number } = {},
): Map<PlayerId, number> {
  const maximum = options.maximum ?? STARTING_HAND_SIZE;
  const counts = new Map<PlayerId, number>();
  for (const playerId of game.turnOrder) {
    const p = game.players[playerId]!;
    let taken = 0;
    while (taken < maximum && policy(game, playerId, taken)) {
      for (const id of [...p.hand]) moveObject(game, game.objects.get(id) as GameObject, Zone.LIBRARY, { toPlayer: playerId });
      shuffleLibrary(game, playerId);
      draw(game, playerId, STARTING_HAND_SIZE);
      taken += 1;
    }
    counts.set(playerId, taken);
    if (taken) {
      bottomCards(game, playerId, taken);
      logRecord(game, `${p.name} mulligans to ${STARTING_HAND_SIZE - taken}`, { kind: "mulligan", player: playerId });
    }
  }
  return counts;
}

/**
 * Put `count` cards from hand on the bottom of the library (CR 103.5): the
 * last cards in hand, deterministically, until the AI chooses.
 */
function bottomCards(game: Game, playerId: PlayerId, count: number): void {
  const p = game.players[playerId]!;
  for (let i = 0; i < Math.min(count, p.hand.length); i++) {
    const id = p.hand[p.hand.length - 1]!;
    moveObject(game, game.objects.get(id) as GameObject, Zone.LIBRARY, { toPlayer: playerId });
  }
}

/** Never mulligans; for tests that want a fixed hand. */
export const keepSeven: MulliganPolicy = () => false;

/**
 * Mulligan a hand with too few or too many lands, at most twice. A
 * placeholder until the bots decide.
 */
export function simpleLandPolicy(
  game: Game,
  playerId: PlayerId,
  mulligansTaken: number,
  low = 2,
  high = 5,
): boolean {
  if (mulligansTaken >= 2) return false;
  let lands = 0;
  for (const id of game.players[playerId]!.hand) {
    const obj = game.objects.get(id);
    if (obj !== undefined && isLand(printedCharacteristics(game, obj))) lands += 1;
  }
  return lands < low || lands > high;
}
