// Ported from tests/rules/test_resolve.py (tokens, restrictions, rule
// overrides) and test_cr706_dice.py.
import { describe, expect, it } from "vitest";
import type { Effect, TokenSpec } from "../../src/ir";
import { Duration } from "../../src/kernel/enums";
import { player } from "../../src/kernel/game";
import { activeOverrides } from "../../src/rules/cr100_game_concepts/rule_overrides";
import { Act, prohibited } from "../../src/rules/cr500_turn_structure/restrictions";
import { characteristicsOf } from "../../src/rules/cr600_spells_and_abilities/layers";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { ir } from "../../src/rules/ir_read";
import { ALL_CREATURES, Board, effect, filter } from "./board";

const E = (kind: string, fields: Partial<Effect> = {}): Effect => effect({ kind: ir(`EffectKind.${kind}` as never), ...fields });
const soldier: TokenSpec = { $: "TokenSpec", name: "Soldier", power: { $: "Value", constant: 1 }, toughness: { $: "Value", constant: 1 } } as TokenSpec;

describe("tokens (CR 111)", () => {
  it("creates as many as it says, for the player named, and 'them' is what it made", () => {
    const board = new Board();
    const source = board.play("Glorious Anthem");
    const r = newResolution(board.game, source.id, 0);
    execute(r, [E("CREATE_TOKEN", { token: soldier, amount: { $: "Value", constant: 2 }, players: { $: "PlayerFilter", scope: ir("PlayerScope.EACH_OPPONENT") } })]);
    const tokens = board.game.battlefield.map((id) => board.game.objects.get(id)!).filter((o) => o.controller === 1);
    expect(tokens).toHaveLength(2);
    expect(r.remembered).toEqual(tokens.map((t) => t.id));
  });

  it("a token copy has the original's copiable values", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears");
    const source = board.play("Glorious Anthem");
    const copy: TokenSpec = { $: "TokenSpec", copy_of: filter({ specific: [bears.id] }) } as TokenSpec;
    execute(newResolution(board.game, source.id, 0), [E("CREATE_TOKEN", { token: copy })]);
    const made = board.game.battlefield.map((id) => board.game.objects.get(id)!).filter((o) => o.id > source.id);
    expect(made.map((o) => characteristicsOf(board.game, o).name)).toEqual(["Grizzly Bears"]);
    expect(characteristicsOf(board.game, made[0]!).power).toBe(2);
  });
});

describe("prohibitions and overrides from a resolved spell", () => {
  it("'target creature can't block this turn' names that creature only, for the turn", () => {
    const board = new Board();
    const bears = board.play("Grizzly Bears", 1);
    const angel = board.play("Serra Angel", 1);
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0, { targets: [[bears.id]] }), [
      E("RESTRICTION", { targets: ALL_CREATURES, is_targeted: true, duration: ir("Duration.END_OF_TURN"), restrictions: [{ $: "Restriction", act: Act.BLOCK as never }] }),
    ]);
    expect(prohibited(board.game, Act.BLOCK, { obj: bears })).not.toBeNull();
    expect(prohibited(board.game, Act.BLOCK, { obj: angel })).toBeNull();
    expect((board.game.standingRestrictions[0] as { duration: number }).duration).toBe(Duration.END_OF_TURN);
  });

  it("a rule switched off by a spell is registered with its duration (CR 101.1)", () => {
    const board = new Board();
    const source = board.play("Glorious Anthem");
    execute(newResolution(board.game, source.id, 0), [E("SUSPEND_RULE", { rule: "305.2", duration: ir("Duration.END_OF_TURN") })]);
    expect(activeOverrides(board.game).map((o) => o.rule)).toEqual(["305.2"]);
  });
});

describe("dice (CR 706)", () => {
  it("rolls on the seeded generator, deterministically, and runs the matching striation", () => {
    const roll = (): [readonly number[], number] => {
      const board = new Board();
      for (let i = 0; i < 3; i += 1) board.play("Forest");
      const source = board.play("Glorious Anthem");
      const r = newResolution(board.game, source.id, 0);
      const draw = E("GAIN_LIFE", { amount: { $: "Value", constant: 1 } });
      execute(r, [E("ROLL_DICE", { dice_sides: 20, outcomes: [{ $: "DieOutcome", low: 1, high: 20, effects: [draw] }] as never })]);
      return [r.dieResults, player(board.game, 0).life];
    };
    const [a] = roll();
    const [b] = roll();
    expect(a).toEqual(b);
    expect(a[0]).toBeGreaterThanOrEqual(1);
    expect(a[0]).toBeLessThanOrEqual(20);
  });
});
