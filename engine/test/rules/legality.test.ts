// Ported from tests/rules/test_legality.py, test_cr101_rule_overrides.py and
// test_cr116_special_actions.py and test_cr119_pay_all_life.py: what a player with priority is offered.
import { describe, expect, it } from "vitest";
import { CardType, Phase, Step, Zone } from "../../src/kernel/enums";
import { createObject, player } from "../../src/kernel/game";
import { ActionKind, perform, type Action } from "../../src/rules/cr100_game_concepts/priority";
import { registerOverride, Rule } from "../../src/rules/cr100_game_concepts/rule_overrides";
import { SpecialKind } from "../../src/rules/cr100_game_concepts/special_actions";
import { legalActions } from "../../src/rules/legality";
import { ir } from "../../src/rules/ir_read";
import { Board, CARDS, cardDef } from "./board";
import { castSpell } from "../../src/rules/cr600_spells_and_abilities/casting";

function mainPhase(board: Board, active = 0): void {
  board.game.activePlayer = active;
  board.game.phase = Phase.PRECOMBAT_MAIN;
  board.game.step = Step.MAIN;
}
const kinds = (actions: Action[], kind: number) => actions.filter((a) => a.kind === kind);

describe("legal actions", () => {
  it("passing is always first; lands only at sorcery speed, one per turn (CR 305.1-2)", () => {
    const board = new Board();
    const forest = createObject(board.game, CARDS["Forest"]!, 0, Zone.HAND);
    board.game.activePlayer = 0;
    board.game.phase = Phase.BEGINNING;
    expect(legalActions(board.game, 0)[0]!.kind).toBe(ActionKind.PASS);
    expect(kinds(legalActions(board.game, 0), ActionKind.PLAY_LAND)).toHaveLength(0);
    mainPhase(board);
    expect(kinds(legalActions(board.game, 0), ActionKind.PLAY_LAND).map((a) => a.source)).toEqual([forest.id]);
    player(board.game, 0).landsPlayed = 1;
    expect(kinds(legalActions(board.game, 0), ActionKind.PLAY_LAND)).toHaveLength(0);
    // CR 101.1: a card can switch the limit off.
    registerOverride(board.game, { rule: Rule.ONE_LAND_PER_TURN, subject: null, players: null, text: "no land limit" });
    expect(kinds(legalActions(board.game, 0), ActionKind.PLAY_LAND)).toHaveLength(1);
  });

  it("a spell is offered when it can be paid for, and only at its speed", () => {
    const board = new Board();
    board.play("Forest");
    const bears = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.HAND);
    mainPhase(board);
    expect(kinds(legalActions(board.game, 0), ActionKind.CAST_SPELL)).toHaveLength(0);
    board.play("Forest");
    expect(kinds(legalActions(board.game, 0), ActionKind.CAST_SPELL).map((a) => a.source)).toEqual([bears.id]);
    mainPhase(board, 1); // not your turn: a creature is sorcery-speed (CR 307.1)
    expect(kinds(legalActions(board.game, 0), ActionKind.CAST_SPELL)).toHaveLength(0);
  });

  it("an untapped basic land offers its intrinsic mana ability (CR 305.6); a tapped one doesn't", () => {
    const board = new Board();
    const forest = board.play("Forest");
    mainPhase(board);
    expect(kinds(legalActions(board.game, 0), ActionKind.ACTIVATE_MANA_ABILITY).map((a) => a.source)).toEqual([forest.id]);
    forest.tapped = true;
    expect(kinds(legalActions(board.game, 0), ActionKind.ACTIVATE_MANA_ABILITY)).toHaveLength(0);
  });

  it("an offered action can be performed", () => {
    const board = new Board();
    board.play("Forest");
    board.play("Forest");
    createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.HAND);
    mainPhase(board);
    const cast = kinds(legalActions(board.game, 0), ActionKind.CAST_SPELL)[0]!;
    expect(perform(board.game, 0, cast)).toBe(true);
    expect(board.game.stack).toHaveLength(1);
  });
});

describe("special actions (CR 116)", () => {
  it("suspend exiles the card from hand with time counters", () => {
    const board = new Board();
    board.script("Serra Angel", { $: "Ability", kind: ir("AbilityKind.STATIC"), keyword: "Suspend", text: "Suspend" });
    const angel = createObject(board.game, CARDS["Serra Angel"]!, 0, Zone.HAND);
    mainPhase(board);
    const suspend = kinds(legalActions(board.game, 0), ActionKind.SPECIAL).find((a) => a.abilityIndex === SpecialKind.SUSPEND)!;
    expect(suspend.source).toBe(angel.id);
    expect(perform(board.game, 0, suspend)).toBe(true);
    const exiled = board.game.objects.get(angel.supersededBy)!;
    expect(exiled.zone).toBe(Zone.EXILE);
    expect(exiled.counters.get("time")).toBe(1);
  });
});

// From tests/rules/test_cr119_pay_all_life.py: CR 119.4, a player can pay
// life only up to their life total - so the last two for Phyrexian mana, not more.
describe("paying life for Phyrexian mana (CR 119.4, 107.4f)", () => {
  CARDS["Probe"] = cardDef("Probe", {
    types: CardType.SORCERY,
    cost: { $: "ManaCost", symbols: [{ $: "ManaSymbol", kind: ir("ManaSymbolKind.PHYREXIAN"), colors: ir("Color.BLUE") }] },
  });
  function setup(life: number) {
    const board = new Board();
    board.game.activePlayer = 0;
    board.game.phase = Phase.PRECOMBAT_MAIN;
    board.game.step = Step.MAIN;
    player(board.game, 0).life = life;
    const probe = createObject(board.game, CARDS["Probe"]!, 0, Zone.HAND);
    const casts = legalActions(board.game, 0).filter((a) => a.kind === ActionKind.CAST_SPELL && a.source === probe.id);
    return { board, casts };
  }

  it("may be paid with the last two life", () => {
    const { board, casts } = setup(2);
    expect(casts).toHaveLength(1);
    castSpell(board.game, 0, casts[0]!);
    expect(player(board.game, 0).life).toBe(0);
  });

  it("not with less life than the cost", () => {
    expect(setup(1).casts).toHaveLength(0);
  });
});
