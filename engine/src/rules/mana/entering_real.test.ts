/**
 * How much mana real sources make, taplands, entering with counters, and
 * Command Tower's commander identity. Ported from
 * tests/rules/test_entering_and_mana.py and
 * test_cr903_commander_identity_mana.py, on real parsed cards (schema/fixtures/).
 */

import { beforeEach, describe, expect, it } from "vitest";
import { RealBoard } from "../../../test/rules/real_cards";
import { Color, Phase, Step, Zone } from "../../kernel/enums";
import type { GameObject } from "../../kernel/gameobject";
import { action, ActionKind } from "../cr100_game_concepts/priority";
import { manaAbilities } from "../cr200_parts_of_a_card/characteristics";
import { activateAbility, playLand } from "../cr600_spells_and_abilities/casting";
import { execute, newResolution } from "../cr600_spells_and_abilities/resolve";
import { installReplacements } from "../cr600_spells_and_abilities/replacement";
import { installMatch } from "../match";
import { amountOfColor, poolTotal } from "./pool";
import { moveObject } from "../zones";

let board: RealBoard;
const pool = () => board.game.players[0]!.manaPool;
function tapForMana(obj: GameObject): void {
  const [index] = manaAbilities(board.chars(obj))[0]!;
  activateAbility(board.game, 0, action(ActionKind.ACTIVATE_ABILITY, { source: obj.id, abilityIndex: index }));
}

beforeEach(() => {
  board = new RealBoard();
  installMatch();
  installReplacements();
  board.game.activePlayer = 0;
  board.game.priorityPlayer = 0;
  board.game.phase = Phase.PRECOMBAT_MAIN;
  board.game.step = Step.MAIN;
});

describe("'Add {G}{U}' is two mana, not four", () => {
  it("a dual land makes one of each colour", () => {
    const chamber = board.play("Simic Growth Chamber");
    chamber.tapped = false;
    tapForMana(chamber);
    expect(poolTotal(pool())).toBe(2);
    expect(amountOfColor(pool(), Color.GREEN)).toBe(1);
    expect(amountOfColor(pool(), Color.BLUE)).toBe(1);
  });

  it("a colourless source makes colourless mana, a single-colour one one mana", () => {
    tapForMana(board.play("Sol Ring"));
    expect(poolTotal(pool())).toBe(2);
    tapForMana(board.play("Llanowar Elves"));
    expect(poolTotal(pool())).toBe(3);
  });
});

describe("entering (CR 614.1c)", () => {
  const landPlayed = (name: string): GameObject => {
    expect(playLand(board.game, 0, action(ActionKind.PLAY_LAND, { source: board.hand(name).id }))).toBe(true);
    return board.game.objects.get(board.game.battlefield[board.game.battlefield.length - 1]!)!;
  };

  it("a tapland played from hand enters tapped; an ordinary land doesn't", () => {
    expect(landPlayed("Temple of Mystery").tapped).toBe(true);
    board.game.players[0]!.landsPlayed = 0;
    expect(landPlayed("Forest").tapped).toBe(false);
  });

  it("a creature that enters with two +1/+1 counters gets them", () => {
    const entered = moveObject(board.game, board.hand("Spike Feeder"), Zone.BATTLEFIELD);
    expect(entered.counters.get("+1/+1")).toBe(2);
  });
});

describe("Command Tower and the commander's identity (CR 903.4)", () => {
  function tapTheTower(): Map<Color, number> {
    const tower = board.play("Command Tower");
    const [, ability] = manaAbilities(board.chars(tower))[0]!;
    const colours = [Color.WHITE, Color.BLUE, Color.BLACK, Color.RED, Color.GREEN];
    const before = new Map(colours.map((c) => [c, amountOfColor(pool(), c)]));
    execute(newResolution(board.game, tower.id, 0), ability.effects ?? []);
    const out = new Map<Color, number>();
    for (const c of colours) if (amountOfColor(pool(), c) !== before.get(c)) out.set(c, amountOfColor(pool(), c) - before.get(c)!);
    return out;
  }

  it("makes a colour of the commander's identity", () => {
    const elves = board.play("Llanowar Elves"); // mono-green identity
    elves.isCommander = true;
    board.game.players[0]!.commanders = [elves.id];
    expect(tapTheTower()).toEqual(new Map([[Color.GREEN, 1]]));
  });

  it("without a commander it makes nothing (CR 903.4f)", () => {
    expect(tapTheTower()).toEqual(new Map());
  });
});

