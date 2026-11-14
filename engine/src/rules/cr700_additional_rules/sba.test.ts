/**
 * State-based actions (CR 704). Ported from tests/rules/test_sba.py,
 * test_cr704_attachment_and_battles.py and test_role_rule.py.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { Board, cardDef, effect, filter, mana, staticAbility } from "../../../test/rules/board";
import type { Ability } from "../../ir";
import { CardType, Color, LossReason, Supertype, Zone } from "../../kernel/enums";
import { createObject } from "../../kernel/game";
import { addCounters, ObjectKind, type GameObject } from "../../kernel/gameobject";
import { allocateTimestamp, NO_OBJECT, type PlayerId } from "../../kernel/ids";
import { destroy, draw } from "../cr100_game_concepts/actions";
import { installMatch } from "../match";
import { ir } from "../ir_read";
import { moveObject, removeFromZone } from "../zones";
import { checkStateBasedActions } from "./sba";
import type { CardDef } from "../../kernel/card";

const keyword = (name: string, quality?: Ability["quality"]): Ability => ({
  $: "Ability",
  kind: ir("AbilityKind.STATIC"),
  keyword: name,
  text: name,
  ...(quality !== undefined ? { quality } : {}),
});

const SBA_CARDS: Record<string, CardDef> = Object.fromEntries(
  [
    cardDef("Grizzly Bears", { cost: mana(1, Color.GREEN), types: CardType.CREATURE, subtypes: ["Bear"], power: "2", toughness: "2", colors: Color.GREEN }),
    cardDef("Runeclaw Bear", { cost: mana(1, Color.GREEN), types: CardType.CREATURE, subtypes: ["Bear"], power: "2", toughness: "2", colors: Color.GREEN }),
    cardDef("Savannah Lions", { cost: mana(0, Color.WHITE), types: CardType.CREATURE, subtypes: ["Cat"], power: "2", toughness: "1", colors: Color.WHITE }),
    cardDef("Serra Angel", { cost: mana(3, Color.WHITE, Color.WHITE), types: CardType.CREATURE, subtypes: ["Angel"], power: "4", toughness: "4", colors: Color.WHITE }),
    cardDef("Darksteel Myr", { cost: mana(3), types: CardType.ARTIFACT | CardType.CREATURE, subtypes: ["Myr"], power: "0", toughness: "1" }),
    cardDef("Kenrith, the Returned King", { cost: mana(4, Color.WHITE), types: CardType.CREATURE, supertypes: Supertype.LEGENDARY, subtypes: ["Human", "Noble"], power: "5", toughness: "5", colors: Color.WHITE }),
    cardDef("Pacifism", { cost: mana(1, Color.WHITE), types: CardType.ENCHANTMENT, subtypes: ["Aura"], colors: Color.WHITE }),
    cardDef("Bonesplitter", { cost: mana(1), types: CardType.ARTIFACT, subtypes: ["Equipment"] }),
    cardDef("Ajani, Caller of the Pride", { cost: mana(1, Color.WHITE, Color.WHITE), types: CardType.PLANESWALKER, supertypes: Supertype.LEGENDARY, subtypes: ["Ajani"], colors: Color.WHITE }),
    cardDef("Invasion of Gobakhan", { cost: mana(1, Color.WHITE), types: CardType.BATTLE, subtypes: ["Siege"], colors: Color.WHITE }),
    cardDef("Forest", { types: CardType.LAND, supertypes: Supertype.BASIC, subtypes: ["Forest"] }),
    cardDef("Role", { types: CardType.ENCHANTMENT, subtypes: ["Aura", "Role"] }),
    cardDef("Spirit", { types: CardType.ENCHANTMENT, subtypes: ["Aura"] }),
  ].map((c) => [c.name, c]),
);

let board: Board;

function play(name: string, controller: PlayerId = 0): GameObject {
  const card = SBA_CARDS[name];
  if (card === undefined) throw new Error(`no test card ${name}`);
  const obj = createObject(board.game, card, controller, Zone.BATTLEFIELD);
  obj.summoningSick = false;
  board.refresh();
  return obj;
}

function token(name: string, controller: PlayerId = 0): GameObject {
  const obj = createObject(board.game, SBA_CARDS[name], controller, Zone.BATTLEFIELD, { kind: ObjectKind.TOKEN });
  board.refresh();
  return obj;
}

const sba = (): boolean => checkStateBasedActions(board.game);
const onBattlefield = (obj: GameObject): boolean => board.game.battlefield.includes(obj.id);

beforeEach(() => {
  board = new Board();
  installMatch(); // the real matcher, not the layer tests' stand-in
  board.script("Darksteel Myr", keyword("Indestructible"));
  board.script("Pacifism", keyword("Enchant", filter({ types_all: ir("CardType.CREATURE") })));
  // The Board's ability provider scripts by name; these come from SBA_CARDS.
});

describe("players (CR 704.5a-c)", () => {
  it("zero life loses", () => {
    board.game.players[0]!.life = 0;
    sba();
    expect(board.game.players[0]!.hasLost).toBe(true);
    expect(board.game.players[0]!.lossReason).toBe(LossReason.LIFE);
  });

  it("negative life loses", () => {
    board.game.players[0]!.life = -3;
    sba();
    expect(board.game.players[0]!.hasLost).toBe(true);
  });

  it("ten poison loses; nine doesn't", () => {
    board.game.players[0]!.poison = 10;
    board.game.players[1]!.poison = 9;
    sba();
    expect(board.game.players[0]!.lossReason).toBe(LossReason.POISON);
    expect(board.game.players[1]!.hasLost).toBe(false);
  });

  it("drawing from an empty library loses on the next check (CR 704.5b)", () => {
    const p = board.game.players[0]!;
    p.library.length = 0;
    draw(board.game, 0, 1);
    expect(p.hasLost).toBe(false);
    sba();
    expect(p.lossReason).toBe(LossReason.EMPTY_LIBRARY);
  });

  it("nobody loses under the sandbox relaxation, and the loop doesn't spin", () => {
    board.game.relaxations = { ...board.game.relaxations, playersCannotLose: true };
    board.game.players[0]!.life = 0;
    expect(sba()).toBe(false);
    expect(board.game.players[0]!.hasLost).toBe(false);
  });
});

describe("creatures (CR 704.5f-h)", () => {
  it("lethal damage destroys; less doesn't", () => {
    const dead = play("Grizzly Bears");
    const alive = play("Grizzly Bears");
    dead.damage = 2;
    alive.damage = 1;
    sba();
    expect(onBattlefield(dead)).toBe(false);
    expect(onBattlefield(alive)).toBe(true);
  });

  it("indestructible survives lethal damage (CR 702.12b)", () => {
    const myr = play("Darksteel Myr");
    myr.damage = 99;
    sba();
    expect(onBattlefield(myr)).toBe(true);
  });

  it("zero toughness dies through indestructible (CR 704.5f isn't destruction)", () => {
    const myr = play("Darksteel Myr");
    addCounters(myr, "-1/-1", 10);
    board.refresh();
    sba();
    expect(onBattlefield(myr)).toBe(false);
  });

  it("any deathtouch damage destroys; the flag alone doesn't (CR 704.5h)", () => {
    const hit = play("Serra Angel");
    hit.damage = 1;
    hit.dealtDeathtouchDamage = true;
    const flagged = play("Serra Angel");
    flagged.dealtDeathtouchDamage = true;
    sba();
    expect(onBattlefield(hit)).toBe(false);
    expect(onBattlefield(flagged)).toBe(true);
  });

  it("two creatures that kill each other both die (CR 704.3)", () => {
    const first = play("Grizzly Bears", 0);
    const second = play("Grizzly Bears", 1);
    first.damage = 2;
    second.damage = 2;
    sba();
    expect(onBattlefield(first)).toBe(false);
    expect(onBattlefield(second)).toBe(false);
  });

  it("a regeneration shield replaces lethal-damage destruction (CR 701.15)", () => {
    const bears = play("Grizzly Bears");
    bears.damage = 2;
    bears.regenerationShields = 1;
    sba();
    expect(onBattlefield(bears)).toBe(true);
    expect(bears.tapped).toBe(true);
    expect(bears.damage).toBe(0);
    expect(bears.regenerationShields).toBe(0);
  });

  it("destroy() ignores an object that is no longer a permanent", () => {
    const bears = play("Grizzly Bears");
    expect(destroy(board.game, bears)).toBe(true);
    expect(destroy(board.game, bears)).toBe(false);
    expect(board.game.players[0]!.graveyard.length).toBe(1);
  });
});

describe("counters (CR 704.5q)", () => {
  it("+1/+1 and -1/-1 counters annihilate in pairs", () => {
    const bears = play("Grizzly Bears");
    addCounters(bears, "+1/+1", 3);
    addCounters(bears, "-1/-1", 2);
    board.refresh();
    sba();
    expect(bears.counters.get("+1/+1") ?? 0).toBe(1);
    expect(bears.counters.get("-1/-1") ?? 0).toBe(0);
    expect(board.pt(bears)).toEqual([3, 3]);
  });

  it("equal counters annihilate completely", () => {
    const bears = play("Grizzly Bears");
    addCounters(bears, "+1/+1", 2);
    addCounters(bears, "-1/-1", 2);
    board.refresh();
    sba();
    expect(bears.counters.get("+1/+1") ?? 0).toBe(0);
    expect(bears.counters.get("-1/-1") ?? 0).toBe(0);
    expect(board.pt(bears)).toEqual([2, 2]);
  });

  it("a creature dying in the same round keeps its counters (CR 704.8)", () => {
    const bears = play("Grizzly Bears");
    addCounters(bears, "+1/+1", 1);
    addCounters(bears, "-1/-1", 3);
    board.refresh();
    sba();
    expect(onBattlefield(bears)).toBe(false);
    expect(bears.counters.get("+1/+1")).toBe(1);
  });
});

describe("tokens and copies (CR 704.5d-e)", () => {
  it("a token in the graveyard ceases to exist", () => {
    const t = token("Grizzly Bears");
    const moved = moveObject(board.game, t, Zone.GRAVEYARD);
    sba();
    expect(board.game.objects.has(moved.id)).toBe(false);
    expect(board.game.players[0]!.graveyard.length).toBe(0);
  });
});

describe("legend and world rules (CR 704.5j-k)", () => {
  it("the legend rule keeps one", () => {
    const first = play("Kenrith, the Returned King", 0);
    const second = play("Kenrith, the Returned King", 0);
    sba();
    expect([first, second].filter(onBattlefield).length).toBe(1);
    expect(onBattlefield(second)).toBe(true); // the newest, until the AI chooses
  });

  it("a card can switch the legend rule off (CR 101.1)", () => {
    board.script(
      "Bonesplitter",
      staticAbility("CR 704.5j does not apply", effect({ kind: ir("EffectKind.SUSPEND_RULE"), rule: "704.5j" })),
    );
    play("Bonesplitter");
    const first = play("Kenrith, the Returned King", 0);
    const second = play("Kenrith, the Returned King", 0);
    sba();
    expect(onBattlefield(first) && onBattlefield(second)).toBe(true);
  });

  it("the legend rule is per player", () => {
    const mine = play("Kenrith, the Returned King", 0);
    const theirs = play("Kenrith, the Returned King", 1);
    sba();
    expect(onBattlefield(mine)).toBe(true);
    expect(onBattlefield(theirs)).toBe(true);
  });

  it("non-legendary duplicates are fine", () => {
    const a = play("Grizzly Bears");
    const b = play("Grizzly Bears");
    sba();
    expect(onBattlefield(a) && onBattlefield(b)).toBe(true);
  });
});

describe("Auras and Equipment (CR 704.5m-p)", () => {
  it("an Aura attached to nothing goes to the graveyard", () => {
    const aura = play("Pacifism");
    sba();
    expect(onBattlefield(aura)).toBe(false);
  });

  it("an Aura on a creature that stops being legal falls off into the graveyard", () => {
    const land = play("Forest");
    const aura = play("Pacifism");
    aura.attachedTo = land.id; // enchant creature, on a land
    land.attachments.push(aura.id);
    board.refresh();
    sba();
    expect(onBattlefield(aura)).toBe(false);
  });

  it("an Aura on a legal host stays", () => {
    const bears = play("Grizzly Bears");
    const aura = play("Pacifism");
    aura.attachedTo = bears.id;
    bears.attachments.push(aura.id);
    board.refresh();
    sba();
    expect(onBattlefield(aura)).toBe(true);
  });

  it("Equipment attached to nothing simply stays (CR 704.5n)", () => {
    const equipment = play("Bonesplitter");
    sba();
    expect(onBattlefield(equipment)).toBe(true);
  });

  it("Equipment on a legal host stays put", () => {
    const host = play("Grizzly Bears");
    const equipment = play("Bonesplitter");
    equipment.attachedTo = host.id;
    host.attachments.push(equipment.id);
    sba();
    expect(equipment.attachedTo).toBe(host.id);
  });

  it("Equipment on a non-creature falls off and stays on the battlefield", () => {
    const land = play("Forest");
    const equipment = play("Bonesplitter");
    equipment.attachedTo = land.id;
    land.attachments.push(equipment.id);
    sba();
    expect(equipment.attachedTo).toBe(NO_OBJECT);
    expect(onBattlefield(equipment)).toBe(true);
  });

  it("a creature attached to something falls off (CR 704.5p)", () => {
    const host = play("Grizzly Bears");
    const creature = play("Runeclaw Bear");
    creature.attachedTo = host.id;
    sba();
    expect(creature.attachedTo).toBe(NO_OBJECT);
    expect(onBattlefield(creature)).toBe(true);
  });

  it("an ordinary permanent attached to something falls off (CR 704.5p)", () => {
    const host = play("Grizzly Bears");
    const land = play("Forest");
    land.attachedTo = host.id;
    sba();
    expect(land.attachedTo).toBe(NO_OBJECT);
    expect(onBattlefield(land)).toBe(true);
  });
});

describe("planeswalkers and battles (CR 704.5i, 704.5v-w)", () => {
  it("a planeswalker with no loyalty dies; one with loyalty survives", () => {
    const dead = play("Ajani, Caller of the Pride", 0);
    const alive = play("Ajani, Caller of the Pride", 1);
    dead.counters.delete("loyalty");
    addCounters(alive, "loyalty", 4);
    sba();
    expect(onBattlefield(dead)).toBe(false);
    expect(onBattlefield(alive)).toBe(true);
  });

  it("a battle at zero defense goes to the graveyard", () => {
    const battle = play("Invasion of Gobakhan");
    battle.protector = 1;
    sba();
    expect(onBattlefield(battle)).toBe(false);
  });

  it("a Siege stays while its own trigger is on the stack, and goes after", () => {
    const battle = play("Invasion of Gobakhan");
    battle.protector = 1;
    const trigger = play("Grizzly Bears");
    removeFromZone(board.game, trigger);
    trigger.zone = Zone.STACK;
    trigger.source = battle.id;
    board.game.stack.push(trigger.id);
    sba();
    expect(onBattlefield(battle)).toBe(true);
    board.game.stack.length = 0;
    sba();
    expect(onBattlefield(battle)).toBe(false);
  });
});

describe("the Role rule (CR 704.5z)", () => {
  function role(host: GameObject, controller: PlayerId = 0, name = "Role"): GameObject {
    const t = token(name, controller);
    t.attachedTo = host.id;
    host.attachments.push(t.id);
    t.timestamp = allocateTimestamp(board.game.ids);
    board.refresh();
    return t;
  }
  const gone = (t: GameObject): boolean => {
    const obj = board.game.objects.get(t.id);
    return obj === undefined || obj.zone !== Zone.BATTLEFIELD;
  };

  it("one Role stays", () => {
    const bear = play("Grizzly Bears");
    const r = role(bear);
    sba();
    expect(gone(r)).toBe(false);
  });

  it("the most recent Role from a player is the one kept", () => {
    const bear = play("Grizzly Bears");
    const first = role(bear);
    const second = role(bear);
    const third = role(bear);
    sba();
    expect([gone(first), gone(second), gone(third)]).toEqual([true, true, false]);
  });

  it("two players may each have a Role on the same creature", () => {
    const bear = play("Grizzly Bears");
    const mine = role(bear, 0);
    const theirs = role(bear, 1);
    sba();
    expect(gone(mine) || gone(theirs)).toBe(false);
  });

  it("Roles on different permanents don't interact", () => {
    const onBear = role(play("Grizzly Bears"));
    const onLion = role(play("Savannah Lions"));
    sba();
    expect(gone(onBear) || gone(onLion)).toBe(false);
  });

  it("an ordinary Aura is not a Role", () => {
    const bear = play("Grizzly Bears");
    const a = role(bear, 0, "Spirit");
    const b = role(bear, 0, "Spirit");
    sba();
    expect(gone(a) || gone(b)).toBe(false);
  });
});

describe("repetition (CR 704.3)", () => {
  it("runs to a fixed point and says whether it did anything", () => {
    const bears = play("Grizzly Bears");
    bears.damage = 2;
    board.game.players[1]!.life = 0;
    expect(sba()).toBe(true);
    expect(onBattlefield(bears)).toBe(false);
    expect(board.game.players[1]!.hasLost).toBe(true);
    expect(sba()).toBe(false);
  });
});

describe("attachment legality (test_attachment_legality.py, test_cr303_enchant_player.py)", () => {
  const protection = (quality: Parameters<typeof filter>[0]): Ability => keyword("Protection", filter(quality));

  it("protection from white knocks off a white Aura (CR 702.16c)", () => {
    const bears = play("Grizzly Bears");
    const aura = play("Pacifism");
    aura.attachedTo = bears.id;
    bears.attachments.push(aura.id);
    board.refresh();
    sba();
    expect(onBattlefield(aura)).toBe(true);
    board.script("Grizzly Bears", protection({ colors_any: ir("Color.WHITE") }));
    sba();
    expect(onBattlefield(aura)).toBe(false);
  });

  it("protection from artifacts unattaches Equipment, which stays (CR 702.16d)", () => {
    const bears = play("Grizzly Bears");
    const blade = play("Bonesplitter");
    blade.attachedTo = bears.id;
    bears.attachments.push(blade.id);
    sba();
    expect(blade.attachedTo).toBe(bears.id);
    board.script("Grizzly Bears", protection({ types_all: ir("CardType.ARTIFACT") }));
    sba();
    expect(onBattlefield(blade)).toBe(true);
    expect(blade.attachedTo).toBe(NO_OBJECT);
  });

  it("an Aura whose restriction wasn't read stays put", () => {
    board.script("Pacifism"); // no Enchant ability read
    const land = play("Forest");
    const aura = play("Pacifism");
    aura.attachedTo = land.id;
    land.attachments.push(aura.id);
    board.refresh();
    sba();
    expect(onBattlefield(aura)).toBe(true);
  });

  it("an Aura enchanting a player who has lost goes to the graveyard", () => {
    board.script("Pacifism", keyword("Enchant", { $: "PlayerFilter", scope: ir("PlayerScope.TARGET_PLAYER") }));
    const curse = play("Pacifism");
    curse.attachedPlayer = 1;
    board.refresh();
    sba();
    expect(onBattlefield(curse)).toBe(true);
    board.game.players[1]!.hasLost = true;
    sba();
    expect(onBattlefield(curse)).toBe(false);
  });
});

describe("storied (CR 702.195a, test_cr702_storied.py)", () => {
  beforeEach(() => board.script("Grizzly Bears", keyword("Storied")));

  it("three artifacts and a storied permanent give a story, for the rest of the game", () => {
    const bears = play("Grizzly Bears");
    const myrs = [play("Bonesplitter"), play("Bonesplitter"), play("Bonesplitter")];
    sba();
    expect(board.game.players[0]!.hasEnduringStory).toBe(true);
    for (const obj of [bears, ...myrs]) destroy(board.game, obj);
    sba();
    expect(board.game.players[0]!.hasEnduringStory).toBe(true);
  });

  it("two are not enough, and legendary permanents count", () => {
    play("Grizzly Bears");
    play("Bonesplitter");
    play("Bonesplitter");
    sba();
    expect(board.game.players[0]!.hasEnduringStory).toBe(false);
    play("Kenrith, the Returned King");
    sba();
    expect(board.game.players[0]!.hasEnduringStory).toBe(true);
  });

  it("without a storied permanent there is no story", () => {
    play("Bonesplitter");
    play("Bonesplitter");
    play("Bonesplitter");
    sba();
    expect(board.game.players[0]!.hasEnduringStory).toBe(false);
  });
});

describe("battle protectors (CR 704.5x-y, test_cr310_battle_protector.py)", () => {
  it("a Siege with defense and a protector is untouched", () => {
    const siege = play("Invasion of Gobakhan");
    addCounters(siege, "defense", 3);
    siege.protector = 1;
    sba();
    expect(onBattlefield(siege)).toBe(true);
  });

  it("a Siege with no possible protector goes to the graveyard", () => {
    const siege = play("Invasion of Gobakhan");
    addCounters(siege, "defense", 3);
    siege.protector = 1;
    board.game.players[1]!.hasLost = true;
    sba();
    expect(onBattlefield(siege)).toBe(false);
  });

  it("a Siege with nobody protecting it gets an opponent as protector", () => {
    const siege = play("Invasion of Gobakhan");
    addCounters(siege, "defense", 3);
    sba();
    expect(siege.protector).toBe(1);
  });
});

describe("soulbond (CR 702.95e)", () => {
  it("a pair ends when one creature leaves", () => {
    const a = play("Grizzly Bears");
    const b = play("Runeclaw Bear");
    a.pairedWith = b.id;
    b.pairedWith = a.id;
    a.pairController = 0;
    b.pairController = 0;
    sba();
    expect(a.pairedWith).toBe(b.id);
    destroy(board.game, b);
    sba();
    expect(a.pairedWith).toBe(NO_OBJECT);
  });
});

describe("the commander zone choice (CR 903.9a)", () => {
  it("a commander that died goes home at the next check, once", () => {
    const kenrith = play("Kenrith, the Returned King");
    kenrith.isCommander = true;
    const inGraveyard = moveObject(board.game, kenrith, Zone.GRAVEYARD, { toPlayer: 0 });
    expect(board.game.commandersAwaitingZoneChoice).toEqual([inGraveyard.id]);
    sba();
    expect(board.game.command.length).toBe(1);
    expect(board.game.players[0]!.graveyard.length).toBe(0);
    expect(board.game.commandersAwaitingZoneChoice).toEqual([]);
  });

  it("an agent can decline", () => {
    const kenrith = play("Kenrith, the Returned King");
    const inGraveyard = moveObject(board.game, kenrith, Zone.GRAVEYARD, { toPlayer: 0 });
    board.game.agents.set(0, { moveCommanderToCommandZone: () => false });
    board.game.commandersAwaitingZoneChoice = [inGraveyard.id];
    sba();
    expect(board.game.command.length).toBe(0);
    expect(board.game.commandersAwaitingZoneChoice).toEqual([]);
  });
});
