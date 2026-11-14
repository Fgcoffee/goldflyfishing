/**
 * Replacement and prevention (CR 614-616). Ported from the core cases of
 * tests/rules/test_cr614_replacement_events.py, test_token_replacements.py
 * and the taplands / commander-zone tests.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, cardDef, constant, effect, filter, mana, staticAbility } from "../../../test/rules/board";
import type { Effect, TokenSpec } from "../../ir";
import { CardType, Color, Supertype, Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { createObject } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { NO_OBJECT, type PlayerId } from "../../kernel/ids";
import { createTokens } from "../cr100_game_concepts/tokens";
import { ir } from "../ir_read";
import { installMatch } from "../match";
import { moveObject } from "../zones";
import {
  applyReplacements,
  hasReplacements,
  installReplacements,
  newReplacementEffect,
  register,
  replacementFromEffect,
  ReplacementKind,
  staticReplacements,
  tokenSpecMatches,
} from "./replacement";
import { replacementHooks } from "./replacement_hooks";

const CARDS = Object.fromEntries(
  [
    cardDef("Grizzly Bears", { cost: mana(1, Color.GREEN), types: CardType.CREATURE, subtypes: ["Bear"], power: "2", toughness: "2", colors: Color.GREEN }),
    cardDef("Doubling Season", { cost: mana(4, Color.GREEN), types: CardType.ENCHANTMENT, colors: Color.GREEN }),
    cardDef("Guildgate", { types: CardType.LAND, subtypes: ["Gate"] }),
    cardDef("Kenrith", { cost: mana(4, Color.WHITE), types: CardType.CREATURE, supertypes: Supertype.LEGENDARY, power: "5", toughness: "5" }),
  ].map((c) => [c.name, c]),
);

let board: Board;
function put(name: string, zone: Zone = Zone.BATTLEFIELD, controller: PlayerId = 0): GameObject {
  const obj = createObject(board.game, CARDS[name]!, controller, zone);
  board.refresh();
  return obj;
}
const replacement = (kind: number, fields: Partial<Effect> = {}): Effect =>
  effect({ kind: ir("EffectKind.REPLACEMENT"), replacement_kind: kind, ...fields });
const SOLDIER: TokenSpec = { $: "TokenSpec", types: CardType.CREATURE, subtypes: ["Soldier"], power: constant(1), toughness: constant(1) };
const damageTo = (obj: GameObject, amount: number) =>
  event(EventKind.DAMAGE_DEALT, { objectId: obj.id, player: obj.controller, amount, source: NO_OBJECT });

beforeEach(() => {
  board = new Board();
  installMatch();
  installReplacements();
});

describe("installation", () => {
  it("installs into replacement_hooks.ts on import", () => {
    expect(replacementHooks().replace).not.toBe(undefined);
    expect(hasReplacements(board.game)).toBe(false);
    register(board.game, newReplacementEffect({ kind: ReplacementKind.PREVENT_ENTIRELY, eventKinds: new Set([EventKind.DREW_CARD]) }));
    expect(replacementHooks().hasReplacements(board.game)).toBe(true);
  });
});

describe("replacementFromEffect (static and resolving alike)", () => {
  it("builds a token doubler watching TOKEN_CREATED", () => {
    const built = replacementFromEffect(replacement(ReplacementKind.MODIFY_TOKENS, { multiplier: 2 }), { source: 5, controller: 0 })!;
    expect(built.kind).toBe(ReplacementKind.MODIFY_TOKENS);
    expect([...built.eventKinds]).toEqual([EventKind.TOKEN_CREATED]);
    expect(built.multiplier).toBe(2);
  });

  it("'combat damage' prevention watches combat damage only", () => {
    const built = replacementFromEffect(effect({ kind: ir("EffectKind.PREVENT_DAMAGE"), keywords: ["combat"], amount: constant(-1) }), { source: 1, controller: 0 })!;
    expect([...built.eventKinds]).toEqual([EventKind.COMBAT_DAMAGE_DEALT]);
  });

  it("an effect that isn't a replacement builds nothing", () => {
    expect(replacementFromEffect(effect({ kind: ir("EffectKind.DRAW") }), { source: 1, controller: 0 })).toBeNull();
    expect(replacementFromEffect(replacement(0), { source: 1, controller: 0 })).toBeNull();
  });
});

describe("static replacements (CR 614.1)", () => {
  beforeEach(() =>
    board.script(
      "Doubling Season",
      staticAbility("If an effect would create one or more tokens under your control, it creates twice that many instead.", replacement(ReplacementKind.MODIFY_TOKENS, { multiplier: 2, players: { $: "PlayerFilter" } })),
    ),
  );

  it("a permanent's static replacement applies while it's on the battlefield", () => {
    put("Doubling Season");
    expect(staticReplacements(board.game).length).toBe(1);
    expect(createTokens(board.game, SOLDIER, 0, 1).length).toBe(2);
    expect(createTokens(board.game, SOLDIER, 1, 1).length).toBe(1); // "under your control"
  });

  it("two doublers quadruple: each applies once (CR 614.5)", () => {
    put("Doubling Season");
    put("Doubling Season");
    expect(createTokens(board.game, SOLDIER, 0, 1).length).toBe(4);
  });

  it("one that has left stops applying (CR 611.3)", () => {
    const season = put("Doubling Season");
    moveObject(board.game, season, Zone.GRAVEYARD);
    expect(createTokens(board.game, SOLDIER, 0, 1).length).toBe(1);
  });
});

describe("prevention (CR 615)", () => {
  it("prevents all damage, and emits DAMAGE_PREVENTED", () => {
    const bears = put("Grizzly Bears");
    register(board.game, replacementFromEffect(effect({ kind: ir("EffectKind.PREVENT_DAMAGE"), amount: constant(-1) }), { source: 0, controller: 0 })!);
    const kinds: number[] = [];
    board.game.observer = (_g, e) => void kinds.push(e.kind);
    expect(applyReplacements(board.game, damageTo(bears, 3))).toBeNull();
    expect(kinds).toContain(EventKind.DAMAGE_PREVENTED);
  });

  it("a shield of 3 is spent across events (CR 615.7)", () => {
    const bears = put("Grizzly Bears");
    const shield = register(board.game, newReplacementEffect({
      kind: ReplacementKind.PREVENT_DAMAGE, eventKinds: new Set([EventKind.DAMAGE_DEALT]), amount: 3, oneShot: true,
    }));
    expect(applyReplacements(board.game, damageTo(bears, 2))).toBeNull();
    expect(applyReplacements(board.game, damageTo(bears, 2))!.amount).toBe(1);
    expect(shield.used).toBe(true);
    expect(applyReplacements(board.game, damageTo(bears, 2))!.amount).toBe(2);
  });

  it("damage doublers multiply, then add", () => {
    const bears = put("Grizzly Bears");
    register(board.game, newReplacementEffect({ kind: ReplacementKind.MODIFY_DAMAGE, eventKinds: new Set([EventKind.DAMAGE_DEALT]), multiplier: 2, amount: 1 }));
    expect(applyReplacements(board.game, damageTo(bears, 3))!.amount).toBe(7);
  });
});

describe("zone changes", () => {
  it("'if it would die, exile it instead': it never reaches the graveyard", () => {
    const bears = put("Grizzly Bears");
    register(board.game, newReplacementEffect({
      kind: ReplacementKind.REDIRECT_ZONE_CHANGE, eventKinds: new Set([EventKind.ZONE_CHANGE]),
      watchedToZone: Zone.GRAVEYARD, watchedFromZone: Zone.BATTLEFIELD, destination: Zone.EXILE,
    }));
    moveObject(board.game, bears, Zone.GRAVEYARD);
    expect(board.game.players[0]!.graveyard.length).toBe(0);
    expect(board.game.exile.length).toBe(1);
  });

  it("a commander headed for its owner's hand goes to the command zone (CR 903.9b)", () => {
    const kenrith = put("Kenrith");
    kenrith.isCommander = true;
    moveObject(board.game, kenrith, Zone.HAND);
    expect(board.game.command.length).toBe(1);
    expect(board.game.players[0]!.hand.length).toBe(0);
  });

  it("but one headed for the graveyard dies first (CR 903.9a is the SBA's)", () => {
    const kenrith = put("Kenrith");
    kenrith.isCommander = true;
    moveObject(board.game, kenrith, Zone.GRAVEYARD);
    expect(board.game.players[0]!.graveyard.length).toBe(1);
  });

  it("an owner may decline", () => {
    const kenrith = put("Kenrith");
    kenrith.isCommander = true;
    board.game.agents.set(0, { moveCommanderToCommandZone: () => false });
    moveObject(board.game, kenrith, Zone.HAND);
    expect(board.game.players[0]!.hand.length).toBe(1);
  });
});

describe("entering (CR 614.1c-e)", () => {
  it("a tapland enters tapped from its own text", () => {
    board.script("Guildgate", staticAbility("This land enters tapped.", effect({ kind: ir("EffectKind.TAP"), text: "enters tapped" })));
    const gate = put("Guildgate", Zone.HAND);
    const entered = moveObject(board.game, gate, Zone.BATTLEFIELD);
    expect(entered.tapped).toBe(true);
  });

  it("'enters untapped' beats 'enters tapped', whatever the order (CR 616.1)", () => {
    const bears = put("Grizzly Bears");
    register(board.game, newReplacementEffect({ kind: ReplacementKind.ENTERS_UNTAPPED, eventKinds: new Set([EventKind.ENTERS_BATTLEFIELD]) }));
    register(board.game, newReplacementEffect({ kind: ReplacementKind.ENTERS_TAPPED, eventKinds: new Set([EventKind.ENTERS_BATTLEFIELD]), subject: filter({}) }));
    applyReplacements(board.game, event(EventKind.ENTERS_BATTLEFIELD, { objectId: bears.id, player: 0 }));
    expect(bears.tapped).toBe(false);
  });

  it("enters with counters", () => {
    const bears = put("Grizzly Bears");
    register(board.game, newReplacementEffect({ kind: ReplacementKind.ENTERS_WITH_COUNTERS, eventKinds: new Set([EventKind.ENTERS_BATTLEFIELD]), subject: filter({}), amount: 2 }));
    applyReplacements(board.game, event(EventKind.ENTERS_BATTLEFIELD, { objectId: bears.id, player: 0 }));
    expect(bears.counters.get("+1/+1")).toBe(2);
  });
});

describe("token filters", () => {
  it("tokenSpecMatches asks types, subtypes and colours", () => {
    expect(tokenSpecMatches(SOLDIER, filter({ types_all: ir("CardType.CREATURE") }))).toBe(true);
    expect(tokenSpecMatches(SOLDIER, filter({ subtypes_any: ["Treasure"] }))).toBe(false);
    expect(tokenSpecMatches({ ...SOLDIER, colors: Color.WHITE }, filter({ colors_any: ir("Color.WHITE") }))).toBe(true);
  });
});
