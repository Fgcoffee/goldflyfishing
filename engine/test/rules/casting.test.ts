// Ported from tests/rules/test_cr601_casting.py and test_cr602_activation.py:
// cast_spell (CR 601.2), the rewind (601.2h), play_land (305.1),
// activate_ability (602.2, 605.3b), commander tax (903.8).
import { describe, expect, it } from "vitest";
import type { Ability, CostComponent, Effect } from "../../src/ir";
import { Color, Zone } from "../../src/kernel/enums";
import { EventKind, type GameEvent } from "../../src/kernel/events";
import { createObject, player } from "../../src/kernel/game";
import { CostKind } from "../../src/rules/cr100_game_concepts/costs";
import { action, ActionKind } from "../../src/rules/cr100_game_concepts/priority";
import { registerStanding, Act } from "../../src/rules/cr500_turn_structure/restrictions";
import { activateAbility, castSpell, playLand } from "../../src/rules/cr600_spells_and_abilities/casting";
import { CastError } from "../../src/rules/cr600_spells_and_abilities/cast_error";
import { ir } from "../../src/rules/ir_read";
import { amountOfColor } from "../../src/rules/mana/pool";
import { ALL_CREATURES, Board, CARDS, effect, mana } from "./board";

const component = (kind: number, fields: Partial<CostComponent> = {}): CostComponent => ({ $: "CostComponent", kind: kind as never, ...fields });
const tapFor = (symbol: string): Ability => ({
  $: "Ability", kind: ir("AbilityKind.ACTIVATED"), is_mana_ability: true,
  cost: { $: "Cost", components: [component(CostKind.TAP_SELF)] },
  effects: [effect({ kind: ir("EffectKind.ADD_MANA"), mana_produced: [symbol] })],
});
const spellAbility = (...effects: Effect[]): Ability => ({ $: "Ability", kind: ir("AbilityKind.SPELL"), effects });

function forests(board: Board, n: number) {
  board.script("Forest", tapFor("{G}"));
  return Array.from({ length: n }, () => board.play("Forest"));
}
function watch(board: Board): GameEvent[] {
  const seen: GameEvent[] = [];
  board.game.observer = (_g, e) => void seen.push(e);
  return seen;
}
const inHand = (board: Board, name: string, owner = 0) => createObject(board.game, CARDS[name]!, owner, Zone.HAND);

describe("casting a spell (CR 601.2)", () => {
  it("moves it to the stack, pays by tapping lands, and only then fires the cast event", () => {
    const board = new Board();
    const lands = forests(board, 2);
    const card = inHand(board, "Grizzly Bears");
    const seen = watch(board);
    const spell = castSpell(board.game, 0, action(ActionKind.CAST_SPELL, { source: card.id }));
    expect(spell.zone).toBe(Zone.STACK);
    expect(board.game.stack).toEqual([spell.id]);
    expect(spell.wasCast).toBe(true);
    expect(spell.castFrom).toBe(Zone.HAND);
    expect(lands.every((l) => l.tapped)).toBe(true);
    // CR 601.2i: cast triggers see a spell already paid for (expend follows from it, CR 700.14).
    const kinds = seen.map((e) => e.kind);
    expect(kinds.indexOf(EventKind.CAST_SPELL)).toBeGreaterThan(kinds.lastIndexOf(EventKind.MANA_SPENT));
    expect(kinds.filter((k) => k === EventKind.EXPENDED)).toHaveLength(2);
    expect(board.game.spellsCastThisTurn).toBe(1);
  });

  it("is rewound entirely when it can't be paid: back in hand, lands untapped, no mana floating (CR 601.2h)", () => {
    const board = new Board();
    const [forest] = forests(board, 1);
    const card = inHand(board, "Serra Angel");
    expect(() => castSpell(board.game, 0, action(ActionKind.CAST_SPELL, { source: card.id }))).toThrow(CastError);
    expect(board.game.stack).toHaveLength(0);
    expect(player(board.game, 0).hand).toHaveLength(1);
    expect(forest!.tapped).toBe(false);
    expect(amountOfColor(player(board.game, 0).manaPool, Color.GREEN)).toBe(0);
  });

  it("needs a legal target for every required target (CR 601.2c)", () => {
    const board = new Board();
    forests(board, 3);
    board.script("Levitation", spellAbility(effect({ kind: ir("EffectKind.DESTROY"), targets: ALL_CREATURES, is_targeted: true })));
    const card = inHand(board, "Levitation");
    card.castWithoutPaying = true;
    expect(() => castSpell(board.game, 0, action(ActionKind.CAST_SPELL, { source: card.id }))).toThrow("required target");
    const bears = board.play("Grizzly Bears", 1);
    const again = player(board.game, 0).hand.map((id) => board.game.objects.get(id)!)[0]!;
    again.castWithoutPaying = true;
    const spell = castSpell(board.game, 0, action(ActionKind.CAST_SPELL, { source: again.id }));
    expect(spell.targets).toEqual([[bears.id]]);
  });

  it("is refused when an effect says players can't cast spells (CR 601.3)", () => {
    const board = new Board();
    forests(board, 2);
    registerStanding(board.game, { $: "Restriction", act: Act.CAST_SPELL as never, players: { $: "PlayerFilter", scope: ir("PlayerScope.EACH_PLAYER") } });
    const card = inHand(board, "Grizzly Bears");
    expect(() => castSpell(board.game, 0, action(ActionKind.CAST_SPELL, { source: card.id }))).toThrow("prohibits");
    expect(card.zone).toBe(Zone.HAND);
  });

  it("a commander cast from the command zone pays and records its tax (CR 903.8)", () => {
    const board = new Board();
    forests(board, 4);
    const commander = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.COMMAND);
    commander.isCommander = true;
    player(board.game, 0).commanders.push(commander.id);
    const spell = castSpell(board.game, 0, action(ActionKind.CAST_SPELL, { source: commander.id }));
    expect(spell.zone).toBe(Zone.STACK);
    expect([...player(board.game, 0).commanderCasts.values()]).toEqual([1]);
  });
});

describe("playing a land (CR 305.1)", () => {
  it("one per turn, onto the battlefield under its player's control", () => {
    const board = new Board();
    const a = inHand(board, "Forest");
    const b = inHand(board, "Island");
    expect(playLand(board.game, 0, action(ActionKind.PLAY_LAND, { source: a.id }))).toBe(true);
    expect(player(board.game, 0).landsPlayed).toBe(1);
    expect(board.game.battlefield).toHaveLength(1);
    expect(() => playLand(board.game, 0, action(ActionKind.PLAY_LAND, { source: b.id }))).toThrow("land drop");
  });
});

describe("activating abilities (CR 602.2, 605.3b)", () => {
  it("a mana ability resolves at once, with no stack object", () => {
    const board = new Board();
    const [forest] = forests(board, 1);
    const result = activateAbility(board.game, 0, action(ActionKind.ACTIVATE_ABILITY, { source: forest!.id, abilityIndex: 0 }));
    expect(result).toBeNull();
    expect(board.game.stack).toHaveLength(0);
    expect(amountOfColor(player(board.game, 0).manaPool, Color.GREEN)).toBe(1);
    expect(() => activateAbility(board.game, 0, action(ActionKind.ACTIVATE_ABILITY, { source: forest!.id, abilityIndex: 0 }))).toThrow(CastError);
  });

  it("any other ability goes on the stack with its targets, paid for first", () => {
    const board = new Board();
    forests(board, 1);
    const pinger: Ability = {
      $: "Ability", kind: ir("AbilityKind.ACTIVATED"),
      cost: { $: "Cost", components: [component(CostKind.MANA, { mana: mana(0, Color.GREEN) })] },
      effects: [effect({ kind: ir("EffectKind.DAMAGE"), targets: ALL_CREATURES, is_targeted: true, amount: { $: "Value", constant: 1 } })],
    };
    board.script("Grizzly Bears", pinger);
    const bears = board.play("Grizzly Bears");
    const angel = board.play("Serra Angel", 1);
    const stackObject = activateAbility(board.game, 0, action(ActionKind.ACTIVATE_ABILITY, { source: bears.id, abilityIndex: 0, targets: [[angel.id]] }))!;
    expect(board.game.stack).toEqual([stackObject.id]);
    expect(stackObject.source).toBe(bears.id);
    expect(stackObject.targets).toEqual([[angel.id]]);
  });

  it("only its controller may activate it (CR 602.2)", () => {
    const board = new Board();
    const [forest] = forests(board, 1);
    expect(() => activateAbility(board.game, 1, action(ActionKind.ACTIVATE_ABILITY, { source: forest!.id, abilityIndex: 0 }))).toThrow("controller");
  });
});
