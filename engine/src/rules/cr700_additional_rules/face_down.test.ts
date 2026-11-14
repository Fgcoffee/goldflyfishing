/**
 * Manifest, cloak, manifest dread and turning face down (CR 701.40, 701.58,
 * 701.62, 708). Ported from the manifest / cloak / face-down cases of
 * tests/rules/test_cr708_face_down.py.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, CARDS, cardDef, mana } from "../../../test/rules/board";
import { CardType, Color, Zone } from "../../kernel/enums";
import { EventKind } from "../../kernel/events";
import { createObject } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { abilityKeyword } from "../cr200_parts_of_a_card/characteristics";
import { installMatch } from "../match";
import { installReplacements } from "../cr600_spells_and_abilities/replacement";
import { characteristicsSeenBy, faceDownOrder, isHidden, manifest, manifestDread, mayLookAt, turnFaceDown } from "./face_down";

const BOLT = cardDef("Lightning Bolt", { cost: mana(0, Color.RED), types: CardType.INSTANT, colors: Color.RED });
let board: Board;
const top = (card: GameObject) => {
  const lib = board.game.players[0]!.library;
  lib.splice(lib.indexOf(card.id), 1);
  lib.unshift(card.id);
};

beforeEach(() => {
  board = new Board();
  installMatch();
  installReplacements();
});

describe("manifest and cloak (CR 701.40, 701.58)", () => {
  it("manifest puts a card onto the battlefield face down: a nameless 2/2", () => {
    const card = createObject(board.game, CARDS["Serra Angel"]!, 0, Zone.LIBRARY);
    const permanent = manifest(board.game, 0, card)!;
    expect(permanent.zone).toBe(Zone.BATTLEFIELD);
    expect(permanent.faceDown).toBe(true);
    expect(permanent.faceDownBy).toBe("Manifest");
    expect(board.chars(permanent).name).toBe("");
    expect(board.pt(permanent)).toEqual([2, 2]);
  });

  it("a manifested instant still enters, face down (CR 701.40a)", () => {
    const permanent = manifest(board.game, 0, createObject(board.game, BOLT, 0, Zone.LIBRARY))!;
    expect(permanent).not.toBeNull();
    expect(board.game.battlefield).toContain(permanent.id);
  });

  it("a card already on the battlefield can't be manifested", () => {
    expect(manifest(board.game, 0, board.play("Grizzly Bears"))).toBeNull();
  });

  it("cloak is manifest with ward {2}", () => {
    const permanent = manifest(board.game, 0, createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.LIBRARY), "Cloak")!;
    expect(board.chars(permanent).abilities.map(abilityKeyword)).toContain("Ward");
  });

  it("manifest dread manifests one, prefers a creature, and bins the other", () => {
    const bolt = createObject(board.game, BOLT, 0, Zone.LIBRARY);
    const angel = createObject(board.game, CARDS["Serra Angel"]!, 0, Zone.LIBRARY);
    top(angel);
    top(bolt);
    const kinds: number[] = [];
    board.game.observer = (_g, e) => void kinds.push(e.kind);
    const manifested = manifestDread(board.game, 0)!;
    expect(manifested.supersededBy).toBe(0);
    expect(board.game.battlefield).toEqual([manifested.id]);
    expect(board.chars(manifested).name).toBe(""); // face down
    expect(board.game.players[0]!.graveyard.length).toBe(1);
    expect(kinds[kinds.length - 1]).toBe(EventKind.MANIFESTED_DREAD);
  });
});

describe("turning face down, and who may look (CR 708)", () => {
  it("turns a face-up permanent face down, with a new timestamp; twice does nothing", () => {
    const bears = board.play("Grizzly Bears");
    const before = bears.timestamp;
    expect(turnFaceDown(board.game, bears)).toBe(true);
    expect(bears.timestamp).toBeGreaterThan(before);
    expect(board.chars(bears).name).toBe("");
    expect(turnFaceDown(board.game, bears)).toBe(false);
  });

  it("its controller may look; an opponent sees the 2/2", () => {
    const bears = board.play("Grizzly Bears", 0);
    turnFaceDown(board.game, bears);
    expect(mayLookAt(board.game, 0, bears)).toBe(true);
    expect(mayLookAt(board.game, 1, bears)).toBe(false);
    expect(characteristicsSeenBy(board.game, 0, bears).name).toBe("Grizzly Bears");
    expect(characteristicsSeenBy(board.game, 1, bears).name).toBe("");
  });

  it("a face-down card in exile is hidden from everyone (CR 406.3a)", () => {
    const card = createObject(board.game, CARDS["Grizzly Bears"]!, 0, Zone.EXILE);
    card.faceDown = true;
    expect(isHidden(card)).toBe(true);
    expect(mayLookAt(board.game, 0, card)).toBe(false);
  });

  it("face-down permanents are told apart by when they entered (CR 708.6)", () => {
    const a = board.play("Grizzly Bears");
    const b = board.play("Serra Angel");
    turnFaceDown(board.game, b);
    turnFaceDown(board.game, a);
    expect(faceDownOrder(board.game, 0)).toEqual([b, a]);
  });
});
