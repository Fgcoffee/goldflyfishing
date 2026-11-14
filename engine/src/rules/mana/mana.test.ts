// Ported from tests/rules/test_mana.py (CR 106, 107.4, 202, 601.2f), case for case.
import { describe, expect, it } from "vitest";
import { Color, Supertype } from "../../kernel/enums";
import { CardType } from "../../kernel/enums";
import {
  addMana,
  canPay,
  clearPool,
  costColors,
  costText,
  findPayment,
  genericAmount,
  increasedBy,
  isEmpty,
  lifeCost,
  manaKind,
  ManaSymbolKind,
  manaValue,
  manaValueWithX,
  newManaPool,
  parseCost,
  parseManaSymbol,
  paymentTotalMana,
  poolSnapshot,
  poolTotal,
  reducedBy,
  reducedByMana,
  substituteX,
  UnknownManaSymbol,
  type ManaKind,
  type ManaPool,
  type ManaRestriction,
} from "./index";

const W = manaKind(Color.WHITE);
const U = manaKind(Color.BLUE);
const B = manaKind(Color.BLACK);
const R = manaKind(Color.RED);
const G = manaKind(Color.GREEN);
const C = manaKind(Color.NONE);
const SNOW_G = manaKind(Color.GREEN, { snow: true });
void Supertype;

function pool(counts: Partial<Record<"W" | "U" | "B" | "R" | "G" | "C" | "SG", number>>): ManaPool {
  const lookup: Record<string, ManaKind> = { W, U, B, R, G, C, SG: SNOW_G };
  const p = newManaPool();
  for (const [letter, n] of Object.entries(counts)) addMana(p, lookup[letter] as ManaKind, n);
  return p;
}

const cost = parseCost;

describe("parsing and derived characteristics (CR 202.2, 202.3)", () => {
  it("plain cost", () => {
    const c = cost("{2}{W}{W}"); // Wrath of God
    expect(manaValue(c)).toBe(4);
    expect(costColors(c)).toBe(Color.WHITE);
    expect(genericAmount(c)).toBe(2);
    expect(costText(c)).toBe("{2}{W}{W}");
  });

  it("Sol Ring is colorless", () => {
    expect(manaValue(cost("{1}"))).toBe(1);
    expect(costColors(cost("{1}"))).toBe(Color.NONE);
  });

  it("no mana cost is empty, not zero (CR 202.1)", () => {
    expect(isEmpty(cost(null))).toBe(true);
    expect(isEmpty(cost(""))).toBe(true);
    expect(manaValue(cost("{0}"))).toBe(0);
    expect(isEmpty(cost("{0}"))).toBe(false);
  });

  it("hybrid counts as one and is both colors", () => {
    expect(manaValue(cost("{W/U}"))).toBe(1);
    expect(costColors(cost("{W/U}"))).toBe(Color.WHITE | Color.BLUE);
  });

  it("monocolored hybrid uses the larger component (CR 202.3b)", () => {
    const c = cost("{2/R}{2/R}{2/R}"); // Flame Javelin is a 6-drop
    expect(manaValue(c)).toBe(6);
    expect(costColors(c)).toBe(Color.RED);
  });

  it("Phyrexian is colored and counts as one", () => {
    const c = cost("{U/P}"); // Gitaxian Probe
    expect(manaValue(c)).toBe(1);
    expect(costColors(c)).toBe(Color.BLUE);
    expect(lifeCost(c.symbols[0]!)).toBe(2);
  });

  it("hybrid Phyrexian {G/W/P}", () => {
    const sym = parseManaSymbol("G/W/P");
    expect(sym.kind).toBe(ManaSymbolKind.PHYREXIAN);
    expect(sym.colors).toBe(Color.GREEN | Color.WHITE);
    expect(manaValue({ symbols: [sym] })).toBe(1);
  });

  it("the colorless symbol is not a color (CR 105.1)", () => {
    expect(manaValue(cost("{1}{C}"))).toBe(2); // Warping Wail
    expect(costColors(cost("{1}{C}"))).toBe(Color.NONE);
  });

  it("snow contributes no color", () => {
    expect(manaValue(cost("{S}{S}"))).toBe(2);
    expect(costColors(cost("{S}{S}"))).toBe(Color.NONE);
  });

  it("X counts as zero until chosen", () => {
    const c = cost("{X}{R}"); // Fireball
    expect(manaValue(c)).toBe(1);
    expect(manaValueWithX(c, 3)).toBe(4);
    expect(manaValue(substituteX(c, 3))).toBe(4);
    expect(costText(substituteX(c, 3))).toBe("{3}{R}");
  });

  it("X of zero leaves no generic symbol", () => {
    expect(costText(substituteX(cost("{X}{R}"), 0))).toBe("{R}");
  });

  it("an unknown symbol is fatal", () => {
    expect(() => parseManaSymbol("QQ")).toThrow(UnknownManaSymbol);
    expect(() => parseManaSymbol("T")).toThrow(UnknownManaSymbol); // a cost symbol, not mana
  });
});

describe("cost arithmetic (CR 601.2f)", () => {
  it("reduction only touches generic", () => {
    expect(costText(reducedBy(cost("{4}{G}{G}"), 2))).toBe("{2}{G}{G}");
  });

  it("reduction floors at zero and never eats colored", () => {
    const c = cost("{2}{U}{U}");
    expect(costText(reducedBy(c, 10))).toBe("{U}{U}");
    expect(manaValue(reducedBy(c, 10))).toBe(2);
  });

  it("reduction does not touch hybrid or Phyrexian", () => {
    expect(costText(reducedBy(cost("{2/W}{W/P}"), 3))).toBe("{2/W}{W/P}");
  });

  it("increase then reduce", () => {
    expect(manaValue(reducedBy(increasedBy(cost("{1}{B}"), 2), 1))).toBe(3);
  });

  // Not in test_mana.py; reduced_by_mana's own rules (CR 118.7b-g).
  it("colored reduction takes its color, else generic", () => {
    expect(costText(reducedByMana(cost("{2}{B}{B}"), cost("{B}")))).toBe("{2}{B}");
    expect(costText(reducedByMana(cost("{2}{G}"), cost("{B}")))).toBe("{1}{G}");
    expect(costText(reducedByMana(cost("{1}{W/U}{U}"), cost("{U}")))).toBe("{1}{W/U}");
    expect(costText(reducedByMana(cost("{3}{C}"), cost("{C}{S}")))).toBe("{2}");
  });
});

describe("payment (CR 601.2h)", () => {
  it("generic accepts any mana", () => {
    expect(canPay(pool({ G: 2, R: 1 }), cost("{3}"))).toBe(true);
    expect(canPay(pool({ G: 2 }), cost("{3}"))).toBe(false);
  });

  it("a colored requirement is not met by the wrong color", () => {
    expect(canPay(pool({ W: 1, U: 1 }), cost("{W}{W}"))).toBe(false);
    expect(canPay(pool({ W: 2 }), cost("{W}{W}"))).toBe(true);
  });

  it("a hybrid pair needs real matching, not greedy", () => {
    expect(canPay(pool({ W: 1, U: 1 }), cost("{W/U}{W/U}"))).toBe(true);
  });

  it("a flexible symbol yields to the rigid one", () => {
    expect(canPay(pool({ W: 1, U: 1 }), cost("{W/U}{W}"))).toBe(true);
    expect(canPay(pool({ W: 1, U: 1 }), cost("{W}{W/U}"))).toBe(true);
  });

  it("a hybrid pair from a single color", () => {
    expect(canPay(pool({ U: 2 }), cost("{W/U}{W/U}"))).toBe(true);
    expect(canPay(pool({ U: 1, G: 1 }), cost("{W/U}{W/U}"))).toBe(false);
  });

  it("specific symbols do not steal mana the generic needs", () => {
    expect(canPay(pool({ G: 2, R: 1 }), cost("{1}{G}{G}"))).toBe(true);
    expect(canPay(pool({ G: 2 }), cost("{1}{G}{G}"))).toBe(false);
  });

  it("Phyrexian prefers mana over life", () => {
    const p = findPayment(pool({ U: 1 }), cost("{U/P}"), { lifeAvailable: 40 });
    expect(p).not.toBeNull();
    expect(p!.life).toBe(0);
    expect(paymentTotalMana(p!)).toBe(1);
  });

  it("Phyrexian falls back to life", () => {
    const p = findPayment(pool({ G: 1 }), cost("{U/P}"), { lifeAvailable: 40 });
    expect(p!.life).toBe(2);
    expect(paymentTotalMana(p!)).toBe(0);
  });

  it("Phyrexian needs the life to be there", () => {
    expect(canPay(pool({ G: 1 }), cost("{U/P}"), { lifeAvailable: 1 })).toBe(false);
  });

  it("an all-Phyrexian cost is payable from an empty pool", () => {
    const p = findPayment(newManaPool(), cost("{U/P}{U/P}"), { lifeAvailable: 40 });
    expect(p!.life).toBe(4);
    expect(paymentTotalMana(p!)).toBe(0);
  });

  it("monocolored hybrid prefers the cheaper colored half", () => {
    expect(paymentTotalMana(findPayment(pool({ R: 1, G: 3 }), cost("{2/R}"))!)).toBe(1);
  });

  it("monocolored hybrid falls back to two generic", () => {
    expect(paymentTotalMana(findPayment(pool({ G: 2 }), cost("{2/R}"))!)).toBe(2);
    expect(canPay(pool({ G: 1 }), cost("{2/R}"))).toBe(false);
  });

  it("snow requires a snow source (CR 107.4h)", () => {
    expect(canPay(pool({ SG: 1 }), cost("{S}"))).toBe(true);
    expect(canPay(pool({ G: 1 }), cost("{S}"))).toBe(false);
    expect(canPay(pool({ SG: 1 }), cost("{G}"))).toBe(true);
  });

  it("{C} rejects colored mana (CR 107.4c)", () => {
    expect(canPay(pool({ C: 1 }), cost("{C}"))).toBe(true);
    expect(canPay(pool({ G: 5 }), cost("{C}"))).toBe(false);
    expect(canPay(pool({ C: 3 }), cost("{3}"))).toBe(true);
  });

  it("the X value is included in payment", () => {
    expect(canPay(pool({ R: 1, G: 3 }), cost("{X}{R}"), { xValue: 3 })).toBe(true);
    expect(canPay(pool({ R: 1, G: 2 }), cost("{X}{R}"), { xValue: 3 })).toBe(false);
  });

  it("the same pool and cost always give the same payment", () => {
    const a = findPayment(pool({ G: 2, R: 2, W: 1 }), cost("{2}{G}"));
    const b = findPayment(pool({ W: 1, R: 2, G: 2 }), cost("{2}{G}"));
    expect(a).toEqual(b);
  });
});

describe("restricted mana (CR 106.6b)", () => {
  // Python's test used an ad-hoc restriction; here it is the real SpendOnlyOn
  // shape, "spend only to cast creature spells".
  const creaturesOnly: ManaRestriction = {
    key: "creature-spells-only",
    filter: { $: "ObjectFilter", types_all: "CardType.CREATURE" } as unknown as ManaRestriction["filter"],
  };
  const creatureSpell = { types: CardType.CREATURE, subtypes: [] };
  const instantSpell = { types: CardType.INSTANT, subtypes: [] };

  it("is unavailable for the wrong spell", () => {
    const p = newManaPool();
    addMana(p, manaKind(Color.GREEN, { restriction: creaturesOnly }), 3);
    expect(canPay(p, cost("{3}"), { context: creatureSpell })).toBe(true);
    expect(canPay(p, cost("{3}"), { context: instantSpell })).toBe(false);
  });

  it("mixes with unrestricted mana", () => {
    const p = pool({ R: 1 });
    addMana(p, manaKind(Color.GREEN, { restriction: creaturesOnly }), 1);
    expect(canPay(p, cost("{2}"), { context: creatureSpell })).toBe(true);
    expect(canPay(p, cost("{2}"), { context: instantSpell })).toBe(false);
  });

  it("a restriction with no filter permits nothing, but counts for a bare check", () => {
    const p = newManaPool();
    addMana(p, manaKind(Color.GREEN, { restriction: { key: "unreadable", filter: null } }), 1);
    expect(canPay(p, cost("{1}"), { context: creatureSpell })).toBe(false);
    expect(canPay(p, cost("{1}"), { context: null })).toBe(false);
  });
});

describe("pool mechanics (CR 500.4)", () => {
  it("the pool empties between steps", () => {
    const p = pool({ G: 2, R: 1 });
    expect(poolTotal(p)).toBe(3);
    expect(clearPool(p)).toBe(3);
    expect(poolTotal(p)).toBe(0);
  });

  it("the snapshot is deterministic", () => {
    const a = newManaPool();
    addMana(a, R, 1);
    addMana(a, W, 2);
    const b = newManaPool();
    addMana(b, W, 2);
    addMana(b, R, 1);
    expect(poolSnapshot(a)).toEqual(poolSnapshot(b));
  });
});
