// Ported from tests/rules/test_cr608_each_opponents_cards.py, on real cards
// (schema/fixtures): untargeted "each opponent's" cards are only theirs.
// Soul-Guide Lantern's "{T}, Sacrifice this artifact: Exile each opponent's
// graveyard" leaves your own graveyard alone (CR 108.3, 608.2). Python uses
// Hill Giant; Wretched Gryff stands in.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Phase, Step, Zone } from "../../src/kernel/enums";
import type { Game } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { ActionKind, perform, settle } from "../../src/rules/cr100_game_concepts/priority";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { legalActions } from "../../src/rules/legality";
import { RealBoard } from "./real_cards";

const live = (game: Game, name: string): GameObject[] =>
  [...game.objects.values()].filter((o) => !o.supersededBy && (o.card as { name?: string } | null)?.name === name);

describe("each opponent's graveyard (CR 108.3, 608.2)", () => {
  it("exiling each opponent's graveyard spares yours", () => {
    const b = new RealBoard();
    b.game.activePlayer = 0;
    b.game.phase = Phase.PRECOMBAT_MAIN;
    b.game.step = Step.MAIN;
    const lantern = b.play("Soul-Guide Lantern", 0);
    b.graveyard("Grizzly Bears", 0);
    b.graveyard("Wretched Gryff", 1);
    const text = (index: number): string => String(b.chars(lantern).abilities[index]?.text ?? "");
    const sacrifice = legalActions(b.game, 0).filter(
      (a) =>
        a.kind === ActionKind.ACTIVATE_ABILITY &&
        a.source === lantern.id &&
        text(a.abilityIndex ?? -1).includes("graveyard") &&
        text(a.abilityIndex ?? -1).includes("each opponent"),
    );
    expect(sacrifice.length).toBeGreaterThan(0);
    expect(perform(b.game, 0, sacrifice[0]!)).toBe(true);
    settle(b.game);
    for (let i = 0; i < 10 && b.game.stack.length > 0; i += 1) {
      resolveTop(b.game);
      settle(b.game);
    }
    expect(live(b.game, "Wretched Gryff").map((o) => o.zone)).toEqual([Zone.EXILE]);
    expect(live(b.game, "Grizzly Bears").map((o) => o.zone)).toEqual([Zone.GRAVEYARD]);
  });
});
