// "That player" / "they" in an object filter that names no player (owner or
// controller SPECIFIC, none given), on real cards (schema/fixtures): bound to
// the player an instruction is carried out for, else the one chosen earlier,
// else the trigger event's player. Python bound none, so these matched
// nothing (audit/unbound_that_player.md, the parser's list of about 110
// cards; fixed in TS).
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { Color, Phase, Step, Zone } from "../../src/kernel/enums";
import { createObject, player, type Game } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { dealDamage } from "../../src/rules/cr100_game_concepts/actions";
import { addMana, manaKind } from "../../src/rules/cr100_game_concepts/mana_pool";
import { ActionKind, PassingAgent, perform, settle } from "../../src/rules/cr100_game_concepts/priority";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { legalActions } from "../../src/rules/legality";
import { realCard, RealBoard } from "./real_cards";

function board(): RealBoard {
  const b = new RealBoard();
  b.game.activePlayer = 0;
  b.game.phase = Phase.PRECOMBAT_MAIN;
  b.game.step = Step.MAIN;
  for (const p of b.game.players) b.game.agents.set(p.id, Object.assign(new PassingAgent(), { chooseOptional: () => true }));
  return b;
}
const name = (o: GameObject): string => (o.card as { name?: string } | null)?.name ?? "";
const live = (game: Game, card: string): GameObject[] => [...game.objects.values()].filter((o) => !o.supersededBy && name(o) === card);
function resolveAll(b: RealBoard): void {
  settle(b.game);
  for (let i = 0; i < 20 && b.game.stack.length > 0; i += 1) {
    resolveTop(b.game);
    settle(b.game);
  }
}
const casts = (b: RealBoard, p: number, card: GameObject) =>
  legalActions(b.game, p).filter((a) => a.kind === ActionKind.CAST_SPELL && a.source === card.id);

describe("'they' is each player in turn", () => {
  it("All Is Dust: each player sacrifices their own coloured permanents", () => {
    const b = board();
    b.play("Grizzly Bears", 0);
    b.play("Grizzly Bears", 1);
    b.play("Sol Ring", 1); // colourless: stays
    const dust = b.hand("All Is Dust");
    addMana(player(b.game, 0).manaPool, manaKind(Color.NONE), 7);
    expect(perform(b.game, 0, casts(b, 0, dust)[0]!)).toBe(true);
    resolveAll(b);
    expect(live(b.game, "Grizzly Bears").map((o) => o.zone)).toEqual([Zone.GRAVEYARD, Zone.GRAVEYARD]);
    expect(live(b.game, "Sol Ring").map((o) => o.zone)).toEqual([Zone.BATTLEFIELD]);
  });

  it("Stronghold Discipline: each player loses 1 life per creature they control", () => {
    const b = board();
    b.play("Grizzly Bears", 0);
    b.play("Grizzly Bears", 1);
    b.play("Hill Giant", 1);
    const card = b.hand("Stronghold Discipline");
    addMana(player(b.game, 0).manaPool, manaKind(Color.BLACK), 2);
    addMana(player(b.game, 0).manaPool, manaKind(Color.NONE), 2);
    expect(perform(b.game, 0, casts(b, 0, card)[0]!)).toBe(true);
    resolveAll(b);
    expect(b.game.players.map((p) => p.life)).toEqual([39, 38]);
  });

  it("Acidic Soil: damage to each player equal to the lands they control", () => {
    const b = board();
    for (let i = 0; i < 3; i += 1) b.play("Mountain", 0);
    b.play("Forest", 1);
    const soil = b.hand("Acidic Soil");
    addMana(player(b.game, 0).manaPool, manaKind(Color.RED), 1);
    addMana(player(b.game, 0).manaPool, manaKind(Color.NONE), 2);
    expect(perform(b.game, 0, casts(b, 0, soil)[0]!)).toBe(true);
    resolveAll(b);
    expect(b.game.players.map((p) => p.life)).toEqual([37, 39]);
  });
});

describe("'that player' is the trigger event's player", () => {
  it("Ragavan: exiles the top card of the damaged player's library, not its controller's", () => {
    const b = board();
    const ragavan = b.play("Ragavan, Nimble Pilferer", 0);
    const mine = createObject(b.game, realCard("Forest"), 0, Zone.LIBRARY);
    const theirs = createObject(b.game, realCard("Island"), 1, Zone.LIBRARY);
    dealDamage(b.game, 1, 2, { source: ragavan.id, sourceController: 0, combat: true });
    resolveAll(b);
    expect(b.game.objects.get(theirs.supersededBy || theirs.id)!.zone).toBe(Zone.EXILE);
    expect(mine.zone).toBe(Zone.LIBRARY);
  });

  it("Throat Slitter: the target is among the damaged player's nonblack creatures", () => {
    const b = board();
    const slitter = b.play("Throat Slitter", 0);
    b.play("Grizzly Bears", 0); // its controller's: not a candidate
    b.play("Grizzly Bears", 1);
    dealDamage(b.game, 1, 2, { source: slitter.id, sourceController: 0, combat: true });
    resolveAll(b);
    expect(live(b.game, "Grizzly Bears").map((o) => `${o.owner}:${o.zone}`).sort()).toEqual([`0:${Zone.BATTLEFIELD}`, `1:${Zone.GRAVEYARD}`]);
  });
});
