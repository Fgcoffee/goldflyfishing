/**
 * The monarch, the initiative, day and night, rad counters. Ported from
 * tests/rules/test_designations.py; the combat cases call the combat-damage
 * hooks directly, since combat is the rules session's and still a placeholder.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, CARDS } from "../../../test/rules/board";
import { Zone } from "../../kernel/enums";
import { EventKind } from "../../kernel/events";
import { createObject } from "../../kernel/game";
import { NO_PLAYER } from "../../kernel/ids";
import { ir, num } from "../ir_read";
import { installMatch } from "../match";
import {
  addRadCounters,
  becomeDay,
  becomeMonarch,
  becomeNight,
  checkDayNightTransition,
  combatDamageToInitiativeHolder,
  combatDamageToMonarch,
  initiativeHolder,
  initiativeLeftTheGame,
  initiativeUpkeep,
  isDay,
  monarch,
  monarchEndStepDraw,
  monarchLeftTheGame,
  radCounterMilling,
  takeInitiative,
  type SourcelessTrigger,
} from "./designations";

let board: Board;
const p = (id: number) => board.game.players[id]!;
const library = (owner: number, ...names: string[]) => names.forEach((n) => createObject(board.game, CARDS[n]!, owner, Zone.LIBRARY));

beforeEach(() => {
  board = new Board();
  installMatch();
  board.game.activePlayer = 0;
});

describe("the monarch (CR 725)", () => {
  it("nobody starts as the monarch; there's only ever one", () => {
    expect(monarch(board.game)).toBe(NO_PLAYER);
    expect(becomeMonarch(board.game, 0)).toBe(true);
    expect(becomeMonarch(board.game, 0)).toBe(false);
    becomeMonarch(board.game, 1);
    expect(monarch(board.game)).toBe(1);
    expect(board.game.players.filter((x) => x.isMonarch).length).toBe(1);
  });

  it("the monarch draws at their own end step, and nobody else does", () => {
    library(0, "Forest");
    library(1, "Forest");
    becomeMonarch(board.game, 0);
    monarchEndStepDraw(board.game);
    expect(p(0).hand.length).toBe(1);
    becomeMonarch(board.game, 1);
    monarchEndStepDraw(board.game); // player 0's turn, player 1 is monarch
    expect(p(1).hand.length).toBe(0);
  });

  it("combat damage to the monarch takes the crown (CR 725.4)", () => {
    becomeMonarch(board.game, 1);
    combatDamageToMonarch(board.game, 1, 0);
    expect(monarch(board.game)).toBe(0);
  });

  it("the crown passes to the active player when the monarch leaves (CR 725.5)", () => {
    becomeMonarch(board.game, 1);
    p(1).hasLost = true;
    monarchLeftTheGame(board.game);
    expect(monarch(board.game)).toBe(0);
  });
});

describe("the initiative (CR 726)", () => {
  it("one holder; taking it ventures into Undercity, even when already held (CR 726.5)", () => {
    expect(takeInitiative(board.game, 1)).toBe(true);
    expect(initiativeHolder(board.game)).toBe(1);
    expect(takeInitiative(board.game, 1)).toBe(false);
    const pending = board.game.pendingTriggers as SourcelessTrigger[];
    expect(pending.length).toBe(2);
    expect(pending[0]!.controller).toBe(1);
    expect(pending[0]!.ability.effects![0]!.dungeon_quality).toBe("Undercity");
    expect(pending[0]!.event.kind).toBe(EventKind.TOOK_INITIATIVE);
  });

  it("the holder ventures at their upkeep", () => {
    takeInitiative(board.game, 0);
    board.game.pendingTriggers.length = 0;
    initiativeUpkeep(board.game);
    expect(board.game.pendingTriggers.length).toBe(1);
    board.game.pendingTriggers.length = 0;
    board.game.activePlayer = 1;
    initiativeUpkeep(board.game);
    expect(board.game.pendingTriggers.length).toBe(0);
  });

  it("combat damage to the holder triggers the holder's 'take the initiative' for the attacker", () => {
    takeInitiative(board.game, 1);
    board.game.pendingTriggers.length = 0;
    combatDamageToInitiativeHolder(board.game, 1, [0]);
    const [t] = board.game.pendingTriggers as SourcelessTrigger[];
    expect(t!.controller).toBe(1);
    expect(num(t!.ability.effects![0]!.kind)).toBe(ir("EffectKind.TAKE_INITIATIVE"));
    expect(t!.ability.effects![0]!.players!.specific).toBe(0);
  });

  it("passes on when the holder leaves (CR 726.4)", () => {
    takeInitiative(board.game, 1);
    p(1).hasLost = true;
    initiativeLeftTheGame(board.game);
    expect(initiativeHolder(board.game)).toBe(0);
  });
});

describe("day and night (CR 731)", () => {
  it("starts as neither, and neither never transitions", () => {
    expect(isDay(board.game)).toBeNull();
    checkDayNightTransition(board.game, 0);
    expect(isDay(board.game)).toBeNull();
  });

  it("day becomes night after a turn with no spells; one spell keeps it day", () => {
    becomeDay(board.game);
    board.game.spellsCastLastTurn = 1;
    checkDayNightTransition(board.game, 0);
    expect(isDay(board.game)).toBe(true);
    board.game.spellsCastLastTurn = 0;
    checkDayNightTransition(board.game, 0);
    expect(isDay(board.game)).toBe(false);
  });

  it("night becomes day after two spells, not one", () => {
    becomeNight(board.game);
    board.game.spellsCastLastTurn = 1;
    checkDayNightTransition(board.game, 0);
    expect(isDay(board.game)).toBe(false);
    board.game.spellsCastLastTurn = 2;
    checkDayNightTransition(board.game, 0);
    expect(isDay(board.game)).toBe(true);
  });
});

describe("rad counters (CR 728)", () => {
  it("mill one per counter; each nonland costs 1 life and a counter", () => {
    library(0, "Forest", "Grizzly Bears", "Serra Angel", "Forest");
    addRadCounters(board.game, 0, 3);
    radCounterMilling(board.game, 0);
    expect(p(0).library.length).toBe(1);
    const nonlands = p(0).graveyard.filter((id) => board.chars(board.game.objects.get(id)!).name !== "Forest").length;
    expect(p(0).life).toBe(40 - nonlands);
    expect(p(0).rad).toBe(3 - nonlands);
  });

  it("no rad counters does nothing; milling only lands costs no life", () => {
    library(0, "Forest", "Forest", "Forest");
    radCounterMilling(board.game, 0);
    expect(p(0).library.length).toBe(3);
    addRadCounters(board.game, 0, 3);
    radCounterMilling(board.game, 0);
    expect(p(0).life).toBe(40);
    expect(p(0).rad).toBe(3);
  });
});
