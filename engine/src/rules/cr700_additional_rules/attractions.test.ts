/**
 * Attractions (CR 717, 701.51, 701.52). Ported from the board cases of
 * tests/rules/test_cr717_attractions.py (deck setup, opening, the junkyard,
 * visiting); the parser and decklist cases stay in Python.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, CARDS, cardDef } from "../../../test/rules/board";
import type { CardDef } from "../../kernel/card";
import { CardType, Zone } from "../../kernel/enums";
import { EventKind, type GameEvent } from "../../kernel/events";
import { installMatch } from "../match";
import { installReplacements } from "../cr600_spells_and_abilities/replacement";
import { destroy } from "../cr100_game_concepts/actions";
import { setUpGame } from "../cr100_game_concepts/setup";
import { moveObject } from "../zones";
import {
  arrivedInCommandZone,
  attractionDeck,
  controlsAnAttraction,
  junkyard,
  litNumbers,
  openAttraction,
  rollToVisit,
  setUpAttractionDeck,
} from "./attractions";

const attraction = (name: string, lights: number[]): CardDef =>
  ({ ...cardDef(name, { types: CardType.ARTIFACT, subtypes: ["Attraction"] }), attraction_lights: lights }) as CardDef;
const ALL_LIT = attraction("Lit Everywhere", [1, 2, 3, 4, 5, 6]);
const UNLIT = attraction("Never Lit", []);

let board: Board;
let events: GameEvent[];
beforeEach(() => {
  board = new Board();
  installMatch();
  installReplacements();
  events = [];
  board.game.observer = (_g, e) => void events.push(e);
});

describe("the Attraction deck (CR 717.2)", () => {
  it("begins in the command zone at setup, shuffled with the library", () => {
    const cards = Array.from({ length: 10 }, (_, i) => attraction(`Attraction ${i}`, [i % 6 + 1]));
    const game = setUpGame([{ name: "A", library: Array(60).fill(CARDS["Forest"]!), attractions: cards }, { name: "B", library: Array(60).fill(CARDS["Forest"]!) }], { seed: 4 });
    expect(attractionDeck(game, 0).length).toBe(10);
    expect(attractionDeck(game, 1).length).toBe(0);
    const order = attractionDeck(game, 0).map((o) => (o.card as CardDef).name);
    expect(order).not.toEqual(cards.map((c) => c.name)); // shuffled (CR 103.3a)
  });

  it("lit numbers are read from the card", () => {
    setUpAttractionDeck(board.game, 0, [attraction("Two Lights", [2, 5])]);
    expect([...litNumbers(attractionDeck(board.game, 0)[0]!)]).toEqual([2, 5]);
  });
});

describe("opening (CR 701.51)", () => {
  it("takes the top card, then the next", () => {
    setUpAttractionDeck(board.game, 0, [ALL_LIT, UNLIT]);
    const first = openAttraction(board.game, 0)!;
    expect(first.zone).toBe(Zone.BATTLEFIELD);
    expect((first.card as CardDef).name).toBe("Lit Everywhere");
    expect(events.some((e) => e.kind === EventKind.ATTRACTION_OPENED)).toBe(true);
    expect((openAttraction(board.game, 0)!.card as CardDef).name).toBe("Never Lit");
    expect(controlsAnAttraction(board.game, 0)).toBe(true);
  });

  it("with an empty deck does nothing", () => {
    expect(openAttraction(board.game, 0)).toBeNull();
    expect(events.length).toBe(0);
  });
});

describe("the junkyard (CR 717.6)", () => {
  it("a destroyed Attraction goes to the junkyard, not the graveyard", () => {
    setUpAttractionDeck(board.game, 0, [ALL_LIT]);
    const opened = openAttraction(board.game, 0)!;
    destroy(board.game, opened);
    expect(board.game.players[0]!.graveyard.length).toBe(0);
    const inCommand = board.game.objects.get(board.game.command[0]!)!;
    expect(inCommand.zone).toBe(Zone.COMMAND);
    // zones.ts (the rules session's) is to call arrivedInCommandZone as it
    // arrives (CR 717.6a), as Python's move_object does; called here until then.
    arrivedInCommandZone(inCommand);
    expect(junkyard(board.game, 0).length).toBe(1);
  });

  it("one bound for a hand goes to the junkyard; one exiled stays in exile", () => {
    setUpAttractionDeck(board.game, 0, [ALL_LIT, UNLIT]);
    moveObject(board.game, openAttraction(board.game, 0)!, Zone.HAND);
    expect(board.game.players[0]!.hand.length).toBe(0);
    moveObject(board.game, openAttraction(board.game, 0)!, Zone.EXILE);
    expect(board.game.exile.length).toBe(1);
  });

  it("another artifact still goes to the graveyard", () => {
    const bears = board.play("Grizzly Bears");
    destroy(board.game, bears);
    expect(board.game.players[0]!.graveyard.length).toBe(1);
  });
});

describe("rolling to visit (CR 701.52)", () => {
  it("a lit Attraction is visited; one without lights never is", () => {
    setUpAttractionDeck(board.game, 0, [ALL_LIT, UNLIT]);
    const lit = openAttraction(board.game, 0)!;
    openAttraction(board.game, 0);
    events.length = 0;
    const roll = rollToVisit(board.game, 0);
    expect(roll).toBeGreaterThanOrEqual(1);
    expect(roll).toBeLessThanOrEqual(6);
    const visits = events.filter((e) => e.kind === EventKind.ATTRACTION_VISITED);
    expect(visits.map((e) => e.objectId)).toEqual([lit.id]);
    expect(events.some((e) => e.kind === EventKind.ROLLED_TO_VISIT && e.amount === roll)).toBe(true);
  });

  it("only the roller's Attractions are visited", () => {
    setUpAttractionDeck(board.game, 1, [ALL_LIT]);
    openAttraction(board.game, 1);
    events.length = 0;
    rollToVisit(board.game, 0);
    expect(events.filter((e) => e.kind === EventKind.ATTRACTION_VISITED)).toEqual([]);
  });
});
