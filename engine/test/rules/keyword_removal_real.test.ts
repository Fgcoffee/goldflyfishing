// Ported from tests/rules/test_cr613_keyword_removal.py, on real cards
// (schema/fixtures): "loses flying" removes flying alone (CR 613.1f), and
// granted protection is from its qualities alone (CR 702.16). Python uses
// Serra Angel and Swords to Plowshares; Sublime Archangel (4/3, flying,
// exalted) and Wrath of God (white) stand in.
import "../../src/rules/match";
import "../../src/rules/cr600_spells_and_abilities/replacement";
import { describe, expect, it } from "vitest";
import { attach } from "../../src/rules/cr100_game_concepts/actions";
import { protectedFrom } from "../../src/rules/cr100_game_concepts/protection";
import { abilityKeyword } from "../../src/rules/cr200_parts_of_a_card/characteristics";
import { RealBoard } from "./real_cards";

describe("removing and granting keywords (CR 613.1f, 702.16)", () => {
  it("Colossus Hammer removes only flying, and gives +10/+10", () => {
    const b = new RealBoard();
    const angel = b.play("Sublime Archangel");
    const hammer = b.play("Colossus Hammer");
    attach(b.game, hammer, angel);
    b.game.epoch += 1;
    const keywords = new Set(b.chars(angel).abilities.map((a) => abilityKeyword(a).toLowerCase()));
    expect(keywords.has("flying")).toBe(false);
    expect(keywords.has("exalted")).toBe(true);
    expect([b.chars(angel).power, b.chars(angel).toughness]).toEqual([14, 13]);
  });

  it("Sword of Fire and Ice protects from red and blue only", () => {
    const b = new RealBoard();
    const bears = b.play("Grizzly Bears");
    const sword = b.play("Sword of Fire and Ice");
    attach(b.game, sword, bears);
    b.game.epoch += 1;
    expect(protectedFrom(b.game, bears, b.hand("Lightning Bolt", 1))).toBe(true);
    expect(protectedFrom(b.game, bears, b.hand("Wrath of God", 1))).toBe(false);
  });
});
