/**
 * Game setup (CR 103, 903.6, 903.7). Ported from the setup, determinism and
 * mulligan cases of tests/rules/test_engine_core.py.
 */

import { describe, expect, it } from "vitest";
import { cardDef, mana } from "../../../test/rules/board";
import { CardType, Color, Supertype, Zone } from "../../kernel/enums";
import { logDigest } from "../../kernel/log";
import { keepSeven, setUpGame, simpleLandPolicy, takeMulligans, type SetupDeck } from "./setup";

const KENRITH = cardDef("Kenrith, the Returned King", {
  cost: mana(4, Color.WHITE),
  types: CardType.CREATURE,
  supertypes: Supertype.LEGENDARY,
  power: "5",
  toughness: "5",
  colors: Color.WHITE,
});
const FOREST = cardDef("Forest", { types: CardType.LAND, supertypes: Supertype.BASIC, subtypes: ["Forest"] });
const MOUNTAIN = cardDef("Mountain", { types: CardType.LAND, supertypes: Supertype.BASIC, subtypes: ["Mountain"] });
const BEARS = cardDef("Grizzly Bears", { cost: mana(1, Color.GREEN), types: CardType.CREATURE, power: "2", toughness: "2", colors: Color.GREEN });

/** Python's build_deck: Kenrith, 40 Forest, 59 Mountain. */
function deck(name: string): SetupDeck {
  return { name, commanders: [KENRITH], library: [...Array(40).fill(FOREST), ...Array(59).fill(MOUNTAIN)] };
}
const fourDecks = (): SetupDeck[] => [0, 1, 2, 3].map((i) => deck(`Deck ${i}`));

describe("setup (CR 103, 903.6, 903.7)", () => {
  const game = setUpGame(fourDecks(), { seed: 1234 });

  it("four players start at forty life", () => {
    expect(game.players.length).toBe(4);
    expect(game.players.every((p) => p.life === 40)).toBe(true);
  });

  it("commanders start in the command zone (CR 903.6)", () => {
    expect(game.command.length).toBe(4);
    for (const p of game.players) {
      expect(p.commanders.length).toBe(1);
      const commander = game.objects.get(p.commanders[0]!)!;
      expect(commander.zone).toBe(Zone.COMMAND);
      expect(commander.isCommander).toBe(true);
    }
  });

  it("opening hands are seven, libraries the rest of the 99", () => {
    for (const p of game.players) {
      expect(p.hand.length).toBe(7);
      expect(p.library.length).toBe(92);
    }
  });

  it("turn order covers every player, and the first player is active", () => {
    expect([...game.turnOrder].sort()).toEqual([0, 1, 2, 3]);
    expect(game.activePlayer).toBe(game.turnOrder[0]);
    expect(game.turn).toBe(0);
  });

  it("records starting deck names (CR 903.11a) and keeps the decks (CR 727.2)", () => {
    expect([...game.players[0]!.startingDeckNames].sort()).toEqual(["Forest", "Kenrith, the Returned King", "Mountain"]);
    expect(game.sourceDecks.length).toBe(4);
  });

  it("a fixed turn order when asked", () => {
    expect(setUpGame(fourDecks(), { seed: 1, randomizeTurnOrder: false }).turnOrder).toEqual([0, 1, 2, 3]);
  });
});

describe("determinism", () => {
  const run = (seed: number) => {
    const g = setUpGame(fourDecks(), { seed });
    return [logDigest(g.log), [...g.turnOrder], g.players.map((p) => p.hand.map((id) => g.objects.get(id)!.card))];
  };

  it("the same seed produces an identical game", () => {
    expect(run(99)).toEqual(run(99));
  });

  it("different seeds produce different games", () => {
    expect(run(1)[0]).not.toEqual(run(2)[0]);
  });
});

describe("the London mulligan (CR 103.5)", () => {
  it("draws seven each time, then bottoms one per mulligan", () => {
    const g = setUpGame(fourDecks(), { seed: 5 });
    const counts = takeMulligans(g, (_g, _p, taken) => taken < 2);
    expect([...counts.values()]).toEqual([2, 2, 2, 2]);
    for (const p of g.players) {
      expect(p.hand.length).toBe(5);
      expect(p.hand.length + p.library.length).toBe(99);
    }
  });

  it("no mulligan leaves seven", () => {
    const g = setUpGame(fourDecks(), { seed: 5 });
    const counts = takeMulligans(g, keepSeven);
    expect([...counts.values()].every((c) => c === 0)).toBe(true);
    expect(g.players.every((p) => p.hand.length === 7)).toBe(true);
  });

  it("the land policy mulligans a landless hand, at most twice", () => {
    const g = setUpGame([{ name: "Bears", library: Array(99).fill(BEARS) }, deck("Lands")], { seed: 3 });
    expect(simpleLandPolicy(g, 0, 0)).toBe(true); // no lands
    expect(simpleLandPolicy(g, 0, 2)).toBe(false); // stops after two
    expect(simpleLandPolicy(g, 1, 0)).toBe(true); // all lands, more than five
  });
});
