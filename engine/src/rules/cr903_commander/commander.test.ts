/**
 * Commander rules end to end (CR 903): the command-zone choice (903.9a as a
 * state-based action, 903.9b as a replacement), commander tax across a trip
 * home (903.8), and commander damage across zone changes (903.10a,
 * 704.6c). Ported from the commander cases of tests/rules/test_engine_core.py
 * and test_cr903_*.py.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, CARDS, effect } from "../../../test/rules/board";
import type { Ability, CostComponent } from "../../ir";
import { LossReason, Zone } from "../../kernel/enums";
import { NO_OBJECT } from "../../kernel/ids";
import { createObject, player } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { dealDamage, destroy } from "../cr100_game_concepts/actions";
import { CostKind } from "../cr100_game_concepts/costs";
import { action, ActionKind } from "../cr100_game_concepts/priority";
import { castSpell } from "../cr600_spells_and_abilities/casting";
import { CastError } from "../cr600_spells_and_abilities/cast_error";
import { installReplacements } from "../cr600_spells_and_abilities/replacement";
import { checkStateBasedActions } from "../cr700_additional_rules/sba";
import { ir } from "../ir_read";
import { installMatch } from "../match";
import { commanderIdentity, moveObject } from "../zones";

const tapFor = (symbol: string): Ability => ({
  $: "Ability",
  kind: ir("AbilityKind.ACTIVATED"),
  is_mana_ability: true,
  cost: { $: "Cost", components: [{ $: "CostComponent", kind: CostKind.TAP_SELF as never } as CostComponent] },
  effects: [effect({ kind: ir("EffectKind.ADD_MANA"), mana_produced: [symbol] })],
});

let board: Board;
let commander: GameObject;

/** The live object for the commander, wherever it has gone (CR 400.7). */
function live(): GameObject {
  for (const obj of board.game.objects.values()) {
    if (obj.isCommander && obj.supersededBy === NO_OBJECT && commanderIdentity(board.game, obj.id) === commander.id) return obj;
  }
  throw new Error("no live commander");
}

beforeEach(() => {
  board = new Board();
  installMatch();
  installReplacements();
  board.script("Forest", tapFor("{G}"));
  board.game.activePlayer = 0;
  commander = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.COMMAND); // {1}{G}
  commander.isCommander = true;
  player(board.game, 0).commanders.push(commander.id);
});

const lands = (n: number) => Array.from({ length: n }, () => board.play("Forest"));
const untapAll = () => board.game.battlefield.forEach((id) => (board.game.objects.get(id)!.tapped = false));
const cast = (obj: GameObject) => castSpell(board.game, 0, action(ActionKind.CAST_SPELL, { source: obj.id }));

describe("commander tax across a trip home (CR 903.8, 903.9a)", () => {
  it("costs {2} more for each previous cast from the command zone", () => {
    lands(4);
    const spell = cast(commander);
    expect(spell.zone).toBe(Zone.STACK);
    // It resolves, then dies; the next SBA check offers the command zone (CR 903.9a).
    const permanent = moveObject(board.game, spell, Zone.BATTLEFIELD);
    destroy(board.game, permanent);
    expect(player(board.game, 0).graveyard.length).toBe(1); // it really died first
    checkStateBasedActions(board.game);
    expect(board.game.command.length).toBe(1);
    // Second cast: {1}{G} + {2} = four lands.
    untapAll();
    const again = cast(live());
    expect(again.zone).toBe(Zone.STACK);
    expect(board.game.battlefield.map((id) => board.game.objects.get(id)!).filter((o) => o.tapped).length).toBe(4);
    expect(player(board.game, 0).commanderCasts.get(commander.id)).toBe(2);
  });

  it("can't be cast a second time without the tax", () => {
    lands(3);
    const spell = cast(commander);
    moveObject(board.game, spell, Zone.HAND); // bounced: CR 903.9b sends it home instead
    expect(board.game.command.length).toBe(1);
    untapAll();
    expect(() => cast(live())).toThrow(CastError); // three lands, needs four
  });
});

describe("commander damage (CR 903.10a, 704.6c)", () => {
  it("21 combat damage from one commander loses, even across zone changes", () => {
    const first = moveObject(board.game, commander, Zone.BATTLEFIELD);
    dealDamage(board.game, 1, 11, { source: first.id, sourceController: 0, combat: true, isCommanderSource: true }); // as combat passes source.isCommander
    // It leaves and comes back: a new object, still the same commander.
    const back = moveObject(board.game, moveObject(board.game, first, Zone.COMMAND), Zone.BATTLEFIELD);
    expect(back.id).not.toBe(first.id);
    dealDamage(board.game, 1, 10, { source: back.id, sourceController: 0, combat: true, isCommanderSource: true }); // as combat passes source.isCommander
    board.game.players[1]!.life = 40; // only commander damage can kill here
    checkStateBasedActions(board.game);
    expect(board.game.players[1]!.lossReason).toBe(LossReason.COMMANDER_DAMAGE);
  });

  it("noncombat damage from a commander doesn't count", () => {
    const onField = moveObject(board.game, commander, Zone.BATTLEFIELD);
    dealDamage(board.game, 1, 25, { source: onField.id, sourceController: 0, isCommanderSource: true });
    board.game.players[1]!.life = 40;
    checkStateBasedActions(board.game);
    expect(board.game.players[1]!.hasLost).toBe(false);
  });
});
