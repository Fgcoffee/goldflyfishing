// Ported from mtgfish/rules/kernel/enums.py: the values are the card IR's
// vocabulary, so they must stay exactly the Python ones (snapshot below).
import { describe, expect, it } from "vitest";
import * as E from "./enums";

const PYTHON = {
  "Color": {
    "NONE": 0,
    "WHITE": 1,
    "BLUE": 2,
    "BLACK": 4,
    "RED": 8,
    "GREEN": 16
  },
  "CardType": {
    "NONE": 0,
    "ARTIFACT": 1,
    "BATTLE": 2,
    "CREATURE": 4,
    "ENCHANTMENT": 8,
    "LAND": 16,
    "PLANESWALKER": 32,
    "INSTANT": 64,
    "SORCERY": 128,
    "KINDRED": 256,
    "CONSPIRACY": 512,
    "DUNGEON": 1024,
    "PHENOMENON": 2048,
    "PLANE": 4096,
    "SCHEME": 8192,
    "VANGUARD": 16384,
    "EMBLEM": 32768,
    "STICKERS": 65536,
    "HERO": 131072,
    "EVENT": 262144,
    "BOSS": 524288,
    "CARD": 1048576
  },
  "Supertype": {
    "NONE": 0,
    "BASIC": 1,
    "LEGENDARY": 2,
    "ONGOING": 4,
    "SNOW": 8,
    "WORLD": 16,
    "TOKEN": 32,
    "HOST": 64,
    "ELITE": 128
  },
  "Zone": {
    "LIBRARY": 0,
    "HAND": 1,
    "BATTLEFIELD": 2,
    "GRAVEYARD": 3,
    "STACK": 4,
    "EXILE": 5,
    "COMMAND": 6
  },
  "Phase": {
    "BEGINNING": 0,
    "PRECOMBAT_MAIN": 1,
    "COMBAT": 2,
    "POSTCOMBAT_MAIN": 3,
    "ENDING": 4
  },
  "Step": {
    "UNTAP": 0,
    "UPKEEP": 1,
    "DRAW": 2,
    "MAIN": 3,
    "BEGINNING_OF_COMBAT": 4,
    "DECLARE_ATTACKERS": 5,
    "DECLARE_BLOCKERS": 6,
    "FIRST_STRIKE_COMBAT_DAMAGE": 7,
    "COMBAT_DAMAGE": 8,
    "END_OF_COMBAT": 9,
    "END_STEP": 10,
    "CLEANUP": 11
  },
  "Timing": {
    "SORCERY": 0,
    "INSTANT": 1
  },
  "Layer": {
    "COPY": 10,
    "FACE_DOWN": 11,
    "CONTROL": 20,
    "TEXT": 30,
    "TYPE": 40,
    "COLOR": 50,
    "ABILITY": 60,
    "PT_CDA": 70,
    "PT_SET": 71,
    "PT_MODIFY": 72,
    "PT_COUNTERS": 73,
    "PT_SWITCH": 74
  },
  "Duration": {
    "PERMANENT": 0,
    "END_OF_TURN": 1,
    "END_OF_COMBAT": 2,
    "YOUR_NEXT_TURN": 3,
    "END_OF_YOUR_NEXT_TURN": 4,
    "WHILE_SOURCE_PERSISTS": 5,
    "CUSTOM": 6
  },
  "LossReason": {
    "LIFE": 1,
    "EMPTY_LIBRARY": 2,
    "POISON": 3,
    "COMMANDER_DAMAGE": 4,
    "EFFECT": 5,
    "CONCEDE": 6,
    "DRAW_AGREED": 7
  },
  "WinReason": {
    "LAST_PLAYER_STANDING": 1,
    "ALTERNATE_WIN_CONDITION": 2,
    "STALL_OUT": 3,
    "DRAW": 4
  },
  "Layout": {
    "NORMAL": 0,
    "SPLIT": 1,
    "FLIP": 2,
    "TRANSFORM": 3,
    "MODAL_DFC": 4,
    "MELD": 5,
    "LEVELER": 6,
    "CLASS": 7,
    "SAGA": 8,
    "ADVENTURE": 9,
    "MUTATE": 10,
    "PROTOTYPE": 11,
    "CASE": 12,
    "ROOM": 13,
    "BATTLE": 14,
    "TOKEN": 15,
    "EMBLEM": 16,
    "REVERSIBLE": 17,
    "AUGMENT": 18,
    "HOST": 19,
    "ART_SERIES": 20,
    "DOUBLE_FACED_TOKEN": 21,
    "PLANAR": 22,
    "SCHEME": 23,
    "VANGUARD": 24,
    "PREPARE": 25
  }
} as const;

describe("enums", () => {
  for (const [name, members] of Object.entries(PYTHON)) {
    it(`${name} matches the Python values`, () => {
      expect((E as unknown as Record<string, unknown>)[name]).toEqual(members);
    });
  }

  it("colour helpers read and write WUBRG", () => {
    expect(E.colorsFromLetters(["u", "R", "C"])).toBe(E.Color.BLUE | E.Color.RED);
    expect(E.colorLetters(E.Color.RED | E.Color.WHITE)).toBe("WR");
    expect(E.colorCount(E.WUBRG)).toBe(5);
    expect(E.colorsOf(E.Color.GREEN | E.Color.BLUE)).toEqual([E.Color.BLUE, E.Color.GREEN]);
    expect(() => E.colorsFromLetters(["X"])).toThrow();
  });

  it("type sets and names", () => {
    expect(E.PERMANENT_TYPES & E.CardType.INSTANT).toBe(0);
    expect(E.CARD_TYPE_NAMES.get("tribal")).toBe(E.CardType.KINDRED);
    expect(E.stepName(E.Step.COMBAT_DAMAGE)).toBe("COMBAT_DAMAGE");
    expect(E.ORDERED_ZONES.has(E.Zone.GRAVEYARD)).toBe(true);
    expect(E.ORDERED_ZONES.has(E.Zone.HAND)).toBe(false);
  });
});
