/**
 * Tokens (CR 111) and emblems (CR 114). Ported from the token and emblem
 * cases of tests/rules/test_core_rules.py, test_token_replacements.py and
 * test_players_leaving.py.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Board, ALL_CREATURES, constant } from "../../../test/rules/board";
import type { Ability, TokenSpec } from "../../ir";
import { CardType, Color, Layout, Zone } from "../../kernel/enums";
import { EventKind, type GameEvent } from "../../kernel/events";
import { addCounters, isToken } from "../../kernel/gameobject";
import { hasSubtype, hasType } from "../cr200_parts_of_a_card/characteristics";
import { Act, registerStanding } from "../cr500_turn_structure/restrictions";
import { installReplacementHooks, replacementHooks } from "../cr600_spells_and_abilities/replacement_hooks";
import { checkStateBasedActions } from "../cr700_additional_rules/sba";
import { installMatch } from "../match";
import { ir, numSet } from "../ir_read";
import { moveObject } from "../zones";
import { buildTokenCard, copyTokenCard, createEmblem, createTokens, tokenName } from "./tokens";

const SOLDIER: TokenSpec = {
  $: "TokenSpec",
  types: CardType.CREATURE,
  subtypes: ["Soldier"],
  colors: Color.WHITE,
  power: constant(1),
  toughness: constant(1),
};
const flying: Ability = { $: "Ability", kind: ir("AbilityKind.STATIC"), keyword: "Flying", text: "Flying" };

let board: Board;
const original = replacementHooks();
beforeEach(() => {
  board = new Board();
  installMatch();
});
afterEach(() => installReplacementHooks(original));

describe("tokens (CR 111)", () => {
  it("a token's name defaults to its subtypes plus Token (CR 111.4)", () => {
    expect(tokenName(SOLDIER)).toBe("Soldier Token");
    expect(tokenName({ $: "TokenSpec", name: "Clue" })).toBe("Clue");
    expect(tokenName({ $: "TokenSpec" })).toBe("Token");
  });

  it("creates a token with the spec's characteristics", () => {
    const created = createTokens(board.game, SOLDIER, 0);
    expect(created.length).toBe(1);
    const token = created[0]!;
    const chars = board.chars(token);
    expect(chars.name).toBe("Soldier Token");
    expect(hasType(chars, CardType.CREATURE)).toBe(true);
    expect(hasSubtype(chars, "Soldier")).toBe(true);
    expect(chars.colors).toBe(Color.WHITE);
    expect(board.pt(token)).toEqual([1, 1]);
    expect(isToken(token)).toBe(true);
    expect(token.summoningSick).toBe(true);
  });

  it("creates several, and emits TOKEN_CREATED and ENTERS_BATTLEFIELD for each", () => {
    const seen: number[] = [];
    board.game.observer = (_g, e: GameEvent) => {
      seen.push(e.kind);
    };
    expect(createTokens(board.game, SOLDIER, 0, 5).length).toBe(5);
    expect(seen.filter((k) => k === EventKind.TOKEN_CREATED).length).toBe(5);
    expect(seen.filter((k) => k === EventKind.ENTERS_BATTLEFIELD).length).toBe(5);
  });

  it("a token with keywords has them", () => {
    const angel: TokenSpec = { $: "TokenSpec", types: CardType.CREATURE, subtypes: ["Angel"], power: constant(4), toughness: constant(4), keywords: ["Flying"] };
    const token = createTokens(board.game, angel, 0)[0]!;
    expect(board.keywords(token).has("Flying")).toBe(true);
  });

  it("a token enters tapped when the spec says so", () => {
    const token = createTokens(board.game, { ...SOLDIER, enters_tapped: true }, 0)[0]!;
    expect(token.tapped).toBe(true);
  });

  it("a non-creature token has no power or toughness", () => {
    const clue = createTokens(board.game, { $: "TokenSpec", name: "Clue", types: CardType.ARTIFACT, subtypes: ["Clue"] }, 0)[0]!;
    expect(board.pt(clue)).toEqual([null, null]);
  });

  it("a token that can't enter is never created (CR 111.5)", () => {
    registerStanding(board.game, { $: "Restriction", act: Act.ENTER_BATTLEFIELD as never, subject: ALL_CREATURES });
    expect(createTokens(board.game, SOLDIER, 0)).toEqual([]);
    expect(board.game.battlefield.length).toBe(0);
    expect(board.game.objects.size).toBe(0);
  });

  it("a token that leaves still ceases to exist (CR 704.5d)", () => {
    const token = createTokens(board.game, SOLDIER, 0)[0]!;
    const moved = moveObject(board.game, token, Zone.GRAVEYARD);
    checkStateBasedActions(board.game);
    expect(board.game.objects.has(moved.id)).toBe(false);
  });

  it("no token is created for a player who has left (CR 800.4b)", () => {
    board.game.players[1]!.hasLost = true;
    expect(createTokens(board.game, SOLDIER, 1)).toEqual([]);
    expect(createTokens(board.game, SOLDIER, 0).length).toBe(1);
  });

  it("a double-faced token is built with both faces (CR 111.10i)", () => {
    const back: TokenSpec = { $: "TokenSpec", types: CardType.CREATURE, subtypes: ["Horror"], power: constant(3), toughness: constant(3) };
    const card = buildTokenCard(board.game, { ...SOLDIER, back });
    expect(card.layout).toBe(Layout.DOUBLE_FACED_TOKEN);
    expect(card.faces.map((f) => f.name)).toEqual(["Soldier Token", "Horror Token"]);
    const token = createTokens(board.game, { ...SOLDIER, back }, 0)[0]!;
    expect(board.chars(token).name).toBe("Soldier Token");
  });
});

describe("token copies (CR 707.2, 707.9b)", () => {
  it("a token copy takes the copiable values, not counters", () => {
    const bears = board.play("Grizzly Bears", 1);
    addCounters(bears, "+1/+1", 2);
    board.refresh();
    const card = copyTokenCard(board.game, bears, { $: "TokenSpec" });
    const token = createTokens(board.game, { $: "TokenSpec" }, 0, 1, { card })[0]!;
    expect(board.chars(token).name).toBe("Grizzly Bears");
    expect(board.pt(token)).toEqual([2, 2]);
    expect(isToken(token)).toBe(true);
  });

  it("an exception on the spec is part of the copy ('except it's 1/1')", () => {
    const angel = board.play("Serra Angel", 1);
    const card = copyTokenCard(board.game, angel, { $: "TokenSpec", power: constant(1), toughness: constant(1) });
    const token = createTokens(board.game, { $: "TokenSpec" }, 0, 1, { card })[0]!;
    expect(board.chars(token).name).toBe("Serra Angel");
    expect(board.pt(token)).toEqual([1, 1]);
  });
});

describe("token replacements (CR 614.1c, test_token_replacements.py)", () => {
  /** A doubler for `controller`'s tokens, as Doubling Season's replacement does. */
  const doubler = (controller: number) => (_g: unknown, e: GameEvent) =>
    e.kind === EventKind.TOKEN_CREATED && e.player === controller ? { ...e, amount: e.amount * 2 } : e;

  it("without a doubler the count is what was asked for", () => {
    expect(createTokens(board.game, SOLDIER, 0, 2).length).toBe(2);
  });

  it("a doubler doubles, and only its controller's tokens", () => {
    installReplacementHooks({ replace: doubler(0) as never });
    expect(createTokens(board.game, SOLDIER, 0, 2).length).toBe(4);
    expect(createTokens(board.game, SOLDIER, 1, 2).length).toBe(2);
  });

  it("two doublers quadruple (CR 614.5)", () => {
    const one = doubler(0);
    installReplacementHooks({ replace: ((g: unknown, e: GameEvent) => one(g, one(g, e))) as never });
    expect(createTokens(board.game, SOLDIER, 0, 1).length).toBe(4);
  });

  it("the replacement sees what the tokens would be", () => {
    let data: readonly unknown[] = [];
    installReplacementHooks({ replace: ((_g: unknown, e: GameEvent) => ((data = e.data), e)) as never });
    createTokens(board.game, SOLDIER, 0);
    expect(data).toEqual([SOLDIER]);
  });

  it("a prevented creation makes none", () => {
    installReplacementHooks({ replace: () => null });
    expect(createTokens(board.game, SOLDIER, 0, 3)).toEqual([]);
  });
});

describe("emblems (CR 114)", () => {
  it("goes to the command zone", () => {
    const emblem = createEmblem(board.game, 0, [flying]);
    expect(emblem.zone).toBe(Zone.COMMAND);
    expect(board.game.command).toContain(emblem.id);
  });

  it("has no characteristics but its abilities (CR 114.3)", () => {
    const chars = board.chars(createEmblem(board.game, 0, [flying]));
    expect(chars.name).toBe("");
    expect(chars.colors).toBe(Color.NONE);
    expect(chars.hasManaCost).toBe(false);
    expect(chars.abilities.length).toBeGreaterThan(0);
  });

  it("its abilities function in the command zone (CR 114.4)", () => {
    for (const ability of board.chars(createEmblem(board.game, 0, [flying])).abilities) {
      expect(numSet(ability.functions_in).has(Zone.COMMAND)).toBe(true);
    }
  });
});
