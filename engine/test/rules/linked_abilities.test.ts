// Ported from tests/rules/test_cr607_linked_abilities.py: "the exiled card",
// "created with" and the like name only what this object's linked ability
// did (CR 607.2). On the real matcher (match/), which reads the links.
import { beforeEach, describe, expect, it } from "vitest";
import type { Ability, Effect, ObjectFilter } from "../../src/ir";
import { Zone } from "../../src/kernel/enums";
import { EventKind } from "../../src/kernel/events";
import { createObject, permanents } from "../../src/kernel/game";
import { newGameObject, ObjectKind, type GameObject } from "../../src/kernel/gameobject";
import { matchHooks } from "../../src/kernel/hooks";
import { allocateObjectId } from "../../src/kernel/ids";
import { destroy, exile, linkedObjects } from "../../src/rules/cr100_game_concepts/actions";
import { settle } from "../../src/rules/cr100_game_concepts/priority";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";
import { resolveTop } from "../../src/rules/cr600_spells_and_abilities/stack";
import { ir } from "../../src/rules/ir_read";
import { installMatch } from "../../src/rules/match";
import { moveObject } from "../../src/rules/zones";
import { Board, CARDS } from "./board";

const HOST = "Glorious Anthem";

let board: Board;
beforeEach(() => {
  board = new Board();
  installMatch(); // the real matcher, after the board's stand-in
  board.script(HOST); // its printed anthem would only get in the way
  board.game.activePlayer = 0;
});

const linked = (linkId = 0, zone: Zone = Zone.EXILE): ObjectFilter => ({
  $: "ObjectFilter",
  linked_to_source: true,
  link_id: linkId,
  zones: { $set: [zone] },
});
const returnLinked = (linkId = 0): Effect => ({
  $: "Effect",
  kind: ir("EffectKind.PUT_ONTO_BATTLEFIELD"),
  targets: linked(linkId),
  under_owners_control: true,
  text: "return the exiled card to the battlefield under its owner's control",
});
const exileIt = (obj: GameObject): Effect => ({
  $: "Effect",
  kind: ir("EffectKind.EXILE"),
  targets: { $: "ObjectFilter", specific: [obj.id] },
});

/** Resolve `effects` as an ability of `source` carrying `linkId`. */
function resolveAbility(source: GameObject, effects: Effect[], linkId = 0): void {
  const stackObject = newGameObject(allocateObjectId(board.game.ids), {
    kind: ObjectKind.ABILITY,
    owner: 0,
    controller: 0,
    zone: Zone.STACK,
    ability: { $: "Ability", kind: ir("AbilityKind.ACTIVATED"), effects, link_id: linkId },
    source: source.id,
  });
  execute(newResolution(board.game, source.id, 0, { stackObject }), effects);
  board.refresh();
}

function current(obj: GameObject): GameObject {
  let o = obj;
  while (o.supersededBy) o = board.game.objects.get(o.supersededBy)!;
  return o;
}

describe("linked abilities (CR 607.2a)", () => {
  it("returns what this object exiled, not what another object exiled", () => {
    const mine = board.play(HOST);
    const other = board.play(HOST);
    const first = board.play("Grizzly Bears", 1);
    const second = board.play("Grizzly Bears", 1);
    resolveAbility(mine, [exileIt(first)]);
    resolveAbility(other, [exileIt(second)]);
    resolveAbility(mine, [returnLinked()]);
    expect(current(first).zone).toBe(Zone.BATTLEFIELD);
    expect(current(second).zone).toBe(Zone.EXILE);
  });

  it("a named link ignores the object's other abilities (CR 607.5)", () => {
    const host = board.play(HOST);
    const partnered = board.play("Grizzly Bears", 1);
    const unlinked = board.play("Grizzly Bears", 1);
    resolveAbility(host, [exileIt(partnered)], 7);
    resolveAbility(host, [exileIt(unlinked)], 0);
    resolveAbility(host, [returnLinked(7)], 7);
    expect(current(partnered).zone).toBe(Zone.BATTLEFIELD);
    expect(current(unlinked).zone).toBe(Zone.EXILE);
  });

  it("a card that left exile is no longer linked; a blinked host is linked to nothing (CR 400.7)", () => {
    const host = board.play(HOST);
    const bears = board.play("Grizzly Bears", 1);
    resolveAbility(host, [exileIt(bears)]);
    moveObject(board.game, moveObject(board.game, current(bears), Zone.GRAVEYARD), Zone.EXILE);
    board.refresh();
    resolveAbility(host, [returnLinked()]);
    expect(current(bears).zone).toBe(Zone.EXILE);

    const host2 = board.play(HOST);
    const held = board.play("Grizzly Bears", 1);
    resolveAbility(host2, [exileIt(held)]);
    const back = moveObject(board.game, exile(board.game, host2), Zone.BATTLEFIELD);
    board.refresh();
    resolveAbility(back, [returnLinked()]);
    expect(current(held).zone).toBe(Zone.EXILE);
  });

  it("'when this leaves the battlefield, return the exiled card' finds its own card (CR 603.10a)", () => {
    const whenThisLeaves: Ability = {
      $: "Ability",
      kind: ir("AbilityKind.TRIGGERED"),
      trigger: {
        $: "TriggerCondition",
        event_kinds: { $set: [EventKind.LEAVES_BATTLEFIELD] },
        subject: { $: "ObjectFilter", source_only: true },
        uses_last_known_information: true,
        functions_in: { $set: Object.values(Zone).filter((z) => typeof z === "number") as number[] },
        text: "when this leaves the battlefield",
      },
      effects: [returnLinked()],
    };
    board.script(HOST, whenThisLeaves);
    const host = board.play(HOST);
    const other = board.play(HOST);
    const mine = board.play("Grizzly Bears", 1);
    const theirs = board.play("Grizzly Bears", 1);
    resolveAbility(host, [exileIt(mine)]);
    resolveAbility(other, [exileIt(theirs)]);
    destroy(board.game, host);
    settle(board.game);
    while (board.game.stack.length > 0) resolveTop(board.game);
    expect(current(mine).zone).toBe(Zone.BATTLEFIELD);
    expect(current(theirs).zone).toBe(Zone.EXILE);
  });
});

describe("'created with' (CR 607.2c, 607.2q)", () => {
  it("tokens created with this are its own", () => {
    const host = board.play(HOST);
    const other = board.play(HOST);
    const token: Effect = {
      $: "Effect",
      kind: ir("EffectKind.CREATE_TOKEN"),
      token: { $: "TokenSpec", types: ir("CardType.CREATURE"), subtypes: ["Spirit"] },
    };
    resolveAbility(host, [token]);
    resolveAbility(other, [token]);
    const spirits = permanents(board.game).filter((o) => o.kind === ObjectKind.TOKEN);
    expect(spirits).toHaveLength(2);
    const ours = spirits.filter((o) => matchHooks().matches(board.game, o, linked(0, Zone.BATTLEFIELD), { source: host.id }));
    expect(ours).toHaveLength(1);
    expect(linkedObjects(board.game, host.id)).toEqual(ours);
  });

  it("cards exiled paying for the spell stay linked to the permanent it becomes", () => {
    const fodder = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.GRAVEYARD);
    const spell = createObject(board.game, CARDS[HOST]!, 0, Zone.STACK);
    const exiled = exile(board.game, fodder, { source: spell.id });
    board.refresh();
    resolveTop(board.game);
    const permanent = current(spell);
    expect(permanent.zone).toBe(Zone.BATTLEFIELD);
    expect(linkedObjects(board.game, permanent.id).map((o) => o.id)).toEqual([exiled.id]);
  });
});

