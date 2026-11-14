// Ported from tests/rules/test_cr500_phases.py: main phases (CR 505.1a),
// extra phases and steps (CR 500.8-9), and the end step not backing up
// (CR 513.2). Python calls the private _run_extras; here an agent adds the
// extras mid-turn, as a resolving effect would.
import { describe, expect, it } from "vitest";
import type { Ability } from "../../src/ir";
import { Phase, Step, Zone } from "../../src/kernel/enums";
import { EventKind } from "../../src/kernel/events";
import { createObject, type Game } from "../../src/kernel/game";
import { PASS, PassingAgent } from "../../src/rules/cr100_game_concepts/priority";
import { takeTurn } from "../../src/rules/cr500_turn_structure/turn";
import { ir } from "../../src/rules/ir_read";
import { Board, CARDS } from "./board";

function table(): Board {
  const board = new Board();
  for (const p of board.game.players) {
    board.game.agents.set(p.id, new PassingAgent());
    for (let i = 0; i < 10; i += 1) createObject(board.game, CARDS["Forest"]!, p.id, Zone.LIBRARY);
  }
  board.game.activePlayer = 0;
  return board;
}

function stepsOf(game: Game): [Phase, Step][] {
  const seen: [Phase, Step][] = [];
  const before = game.observer;
  game.observer = (g, e) => {
    before?.(g, e);
    if (e.kind === EventKind.STEP_BEGAN) seen.push([g.phase, g.step]);
  };
  return seen;
}

/** The active player's agent: once, at the first priority in `when`, does `act`. */
function onceAt(board: Board, when: (g: Game) => boolean, act: (g: Game) => void): void {
  let done = false;
  board.game.agents.set(
    0,
    Object.assign(new PassingAgent(), {
      chooseAction: (g: Game) => {
        if (!done && when(g)) {
          done = true;
          act(g);
        }
        return PASS;
      },
    }),
  );
}

const mains = (seen: [Phase, Step][]): Phase[] => seen.filter(([, s]) => s === Step.MAIN).map(([p]) => p);

describe("main phases and extras (CR 500.8-9, 505.1a)", () => {
  it("an ordinary turn has one precombat main phase", () => {
    const board = table();
    const seen = stepsOf(board.game);
    takeTurn(board.game);
    expect(mains(seen)).toEqual([Phase.PRECOMBAT_MAIN, Phase.POSTCOMBAT_MAIN]);
  });

  it("an extra main phase is a postcombat main phase", () => {
    const board = table();
    const seen = stepsOf(board.game);
    onceAt(board, (g) => g.phase === Phase.POSTCOMBAT_MAIN, (g) => g.extraPhases.push(Phase.PRECOMBAT_MAIN));
    takeTurn(board.game);
    expect(mains(seen)).toEqual([Phase.PRECOMBAT_MAIN, Phase.POSTCOMBAT_MAIN, Phase.POSTCOMBAT_MAIN]);
  });

  it("the most recently created extra phase happens first: combat, then main (CR 500.8)", () => {
    const board = table();
    const seen = stepsOf(board.game);
    onceAt(board, (g) => g.phase === Phase.POSTCOMBAT_MAIN, (g) => g.extraPhases.push(Phase.PRECOMBAT_MAIN, Phase.COMBAT));
    takeTurn(board.game);
    const after = seen.slice(seen.findIndex(([p]) => p === Phase.POSTCOMBAT_MAIN) + 1).map(([, s]) => s);
    expect(after.indexOf(Step.BEGINNING_OF_COMBAT)).toBeGreaterThanOrEqual(0);
    expect(after.indexOf(Step.BEGINNING_OF_COMBAT)).toBeLessThan(after.indexOf(Step.MAIN));
  });

  it("the most recently created extra step happens first (CR 500.9)", () => {
    const board = table();
    const seen = stepsOf(board.game);
    onceAt(board, (g) => g.step === Step.UPKEEP, (g) => g.extraSteps.push(Step.DRAW, Step.UPKEEP));
    takeTurn(board.game);
    const steps = seen.map(([, s]) => s);
    const first = steps.indexOf(Step.UPKEEP);
    expect(steps.slice(first, first + 3)).toEqual([Step.UPKEEP, Step.UPKEEP, Step.DRAW]);
  });
});

describe("the end step (CR 513.1a, 513.2)", () => {
  const probe: Ability = {
    $: "Ability",
    kind: ir("AbilityKind.TRIGGERED"),
    trigger: { $: "TriggerCondition", event_kinds: { $set: [EventKind.END_STEP] }, text: "at the beginning of the end step" },
    effects: [{ $: "Effect", kind: ir("EffectKind.NOTHING"), text: "end step probe" }],
    text: "end step probe",
  };

  function resolutions(game: Game): string[] {
    const out: string[] = [];
    game.observer = (g, e) => {
      if (e.kind === EventKind.ABILITY_RESOLVED) {
        const obj = g.objects.get(e.objectId);
        if (obj?.ability) out.push(obj.ability.text ?? "");
      }
    };
    return out;
  }

  it("an end step trigger already on the battlefield fires", () => {
    const board = table();
    board.script("Grizzly Bears", probe);
    board.play("Grizzly Bears");
    const resolved = resolutions(board.game);
    takeTurn(board.game);
    expect(resolved).toContain("end step probe");
  });

  it("a permanent arriving during the end step doesn't trigger: the step doesn't back up", () => {
    const board = table();
    board.script("Grizzly Bears", probe);
    const resolved = resolutions(board.game);
    let arrived = false;
    onceAt(board, (g) => g.step === Step.END_STEP, () => {
      board.play("Grizzly Bears");
      arrived = true;
    });
    takeTurn(board.game);
    expect(arrived).toBe(true);
    expect(resolved).not.toContain("end step probe");
  });
});

// From tests/rules/test_cr500_phase_triggers.py: a phase trigger fires once,
// as its own phase begins (CR 500.6, 505.1a), and "your" means your turn.
describe("phase triggers (CR 500.6)", () => {
  const you = { $: "PlayerFilter" as const, scope: ir("PlayerScope.YOU") };
  const atPhase = (text: string, phases: Phase[], yours: boolean): Ability => ({
    $: "Ability",
    kind: ir("AbilityKind.TRIGGERED"),
    trigger: { $: "TriggerCondition", event_kinds: { $set: [EventKind.PHASE_BEGAN] }, phases: { $set: phases }, ...(yours ? { players: you } : {}), text },
    effects: [{ $: "Effect", kind: ir("EffectKind.NOTHING"), text }],
    text,
  });
  const COMBAT = atPhase("combat on your turn", [Phase.COMBAT], true);
  const FIRST_MAIN = atPhase("your precombat main", [Phase.PRECOMBAT_MAIN], true);
  const SECOND_MAIN = atPhase("your postcombat main", [Phase.POSTCOMBAT_MAIN], true);
  const EACH_MAIN = atPhase("each main phase", [Phase.PRECOMBAT_MAIN, Phase.POSTCOMBAT_MAIN], false);

  function resolvedCounts(board: Board): Map<string, number> {
    const counts = new Map<string, number>();
    board.game.observer = (g, e) => {
      if (e.kind !== EventKind.ABILITY_RESOLVED) return;
      const text = g.objects.get(e.objectId)?.ability?.text ?? "";
      counts.set(text, (counts.get(text) ?? 0) + 1);
    };
    return counts;
  }

  it("each phase trigger fires once in its own phase; 'each main phase' twice", () => {
    const board = table();
    board.script("Grizzly Bears", COMBAT, FIRST_MAIN, SECOND_MAIN, EACH_MAIN);
    board.play("Grizzly Bears");
    const counts = resolvedCounts(board);
    takeTurn(board.game);
    expect(Object.fromEntries(counts)).toEqual({
      "combat on your turn": 1,
      "your precombat main": 1,
      "your postcombat main": 1,
      "each main phase": 2,
    });
  });

  it("'your' phase trigger doesn't fire on an opponent's turn", () => {
    const board = table();
    board.script("Grizzly Bears", COMBAT);
    board.play("Grizzly Bears", 1);
    const counts = resolvedCounts(board);
    takeTurn(board.game);
    expect(counts.size).toBe(0);
  });
});
