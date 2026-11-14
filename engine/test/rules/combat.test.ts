// Ported from tests/rules/test_combat.py, test_cr510_damage_division.py and
// test_block_requirements.py: attack and block legality, evasion, damage,
// first strike, trample, deathtouch, blocked-stays-blocked, requirements.
import { describe, expect, it } from "vitest";
import type { Ability } from "../../src/ir";
import { CardType, Phase, Step, Zone } from "../../src/kernel/enums";
import { player } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { NO_OBJECT } from "../../src/kernel/ids";
import {
  canAttack,
  canBlock,
  canBlockAtAll,
  checkRemovalFromCombat,
  dealCombatDamage,
  declareAttackers,
  declareBlockers,
  endCombat,
  enterAttacking,
  lethalDamage,
  type Combat,
} from "../../src/rules/cr500_turn_structure/combat";
import { BLOCKS_IF_ABLE, MUST_BE_BLOCKED } from "../../src/rules/cr500_turn_structure/restrictions";
import { checkStateBasedActions } from "../../src/rules/cr700_additional_rules/sba";
import { ir } from "../../src/rules/ir_read";
import { moveObject } from "../../src/rules/zones";
import { setAgent } from "./agent_util";
import { ALL_CREATURES, Board, CARDS, cardDef } from "./board";
import { execute, newResolution } from "../../src/rules/cr600_spells_and_abilities/resolve";

const kw = (keyword: string): Ability => ({ $: "Ability", kind: ir("AbilityKind.STATIC"), keyword, text: keyword });

function creature(name: string, power: number, toughness: number): string {
  CARDS[name] = cardDef(name, { types: CardType.CREATURE, power: String(power), toughness: String(toughness) });
  return name;
}

const BEARS = creature("Bears", 2, 2);
const GIANT = creature("Hill Giant", 3, 3);
const ANGEL = creature("Angel", 4, 4);
const SPIDER = creature("Spider", 2, 4);
const HAWK = creature("Nighthawk", 2, 3);
const RHOX = creature("Rhox", 5, 4);
const RATS = creature("Rats", 1, 1);
const KNIGHT = creature("Knight", 2, 2);
const BLADE = creature("Swiftblade", 1, 2);
const WALL = creature("Wall", 0, 4);
const PIKER = creature("Piker", 2, 1);

function combatBoard(scripts: Record<string, string[]> = {}): Board {
  const board = new Board();
  for (const [name, keywords] of Object.entries(scripts)) board.script(name, ...keywords.map(kw));
  board.game.activePlayer = 0;
  board.game.phase = Phase.COMBAT;
  return board;
}

function attackWith(board: Board, attacks: Map<number, number>, blocks: Map<number, number[]> = new Map()) {
  setAgent(board.game, 0, { declareAttackers: () => attacks });
  setAgent(board.game, 1, { declareBlockers: () => blocks });
}

function fight(board: Board, attacker: GameObject, blockers: GameObject[] = []) {
  attackWith(board, new Map([[attacker.id, 1]]), new Map(blockers.map((b) => [b.id, [attacker.id]])));
  declareAttackers(board.game);
  declareBlockers(board.game);
  dealCombatDamage(board.game);
  checkStateBasedActions(board.game);
}

const combat = (board: Board): Combat => board.game.combat as Combat;
const onBattlefield = (board: Board, obj: GameObject): boolean => board.game.battlefield.includes(obj.id);

describe("attack legality (CR 508.1a)", () => {
  it("an untapped creature can attack; tapped, summoning sick, defender or noncreature can't", () => {
    const board = combatBoard({ [WALL]: ["Defender"] });
    const bears = board.play(BEARS);
    expect(canAttack(board.game, bears)).toBe(true);
    bears.tapped = true;
    expect(canAttack(board.game, bears)).toBe(false);
    const sick = board.play(BEARS);
    sick.summoningSick = true;
    expect(canAttack(board.game, sick)).toBe(false);
    expect(canAttack(board.game, board.play(WALL))).toBe(false);
    expect(canAttack(board.game, board.play("Glorious Anthem"))).toBe(false);
  });

  it("haste beats summoning sickness (CR 302.6)", () => {
    const board = combatBoard({ [PIKER]: ["Haste"] });
    const goblin = board.play(PIKER);
    goblin.summoningSick = true;
    expect(canAttack(board.game, goblin)).toBe(true);
  });
});

describe("declaring attackers (CR 508.1)", () => {
  it("attacking taps the creature; vigilance doesn't (CR 508.1f, 702.20b)", () => {
    const board = combatBoard({ [ANGEL]: ["Flying", "Vigilance"] });
    const bears = board.play(BEARS);
    const angel = board.play(ANGEL);
    attackWith(board, new Map([[bears.id, 1], [angel.id, 1]]));
    declareAttackers(board.game);
    expect(bears.tapped).toBe(true);
    expect(angel.tapped).toBe(false);
    expect(combat(board).attacking.get(bears.id)).toBe(1);
    expect(combat(board).attacking.get(angel.id)).toBe(1);
  });

  it("an illegal attacker is not honoured; you can't attack yourself (CR 508.1d, 506.2)", () => {
    const board = combatBoard({ [WALL]: ["Defender"] });
    const wall = board.play(WALL);
    const bears = board.play(BEARS);
    attackWith(board, new Map([[wall.id, 1], [bears.id, 0]]));
    declareAttackers(board.game);
    expect(combat(board).attacking.size).toBe(0);
    expect(bears.tapped).toBe(false);
  });

  it("a goaded creature is forced to attack (CR 701.38)", () => {
    const board = combatBoard();
    const bears = board.play(BEARS);
    bears.goadedBy.add(1);
    attackWith(board, new Map());
    declareAttackers(board.game);
    expect(combat(board).attacking.has(bears.id)).toBe(true);
  });
});

describe("blocking (CR 509.1)", () => {
  it("evasion: flying, reach, and flying doesn't stop blocking ground creatures (CR 702.9b)", () => {
    const board = combatBoard({ [ANGEL]: ["Flying"], [SPIDER]: ["Reach"], [HAWK]: ["Flying"] });
    const flier = board.play(ANGEL, 0);
    expect(canBlock(board.game, board.play(BEARS, 1), flier)).toBe(false);
    expect(canBlock(board.game, board.play(HAWK, 1), flier)).toBe(true);
    expect(canBlock(board.game, board.play(SPIDER, 1), flier)).toBe(true);
    expect(canBlock(board.game, board.play(ANGEL, 1), board.play(BEARS, 0))).toBe(true);
  });

  it("tapped creatures can't block; summoning sick ones can (CR 509.1a, 302.6)", () => {
    const board = combatBoard();
    const tapped = board.play(BEARS, 1);
    tapped.tapped = true;
    const sick = board.play(BEARS, 1);
    sick.summoningSick = true;
    expect(canBlockAtAll(board.game, tapped)).toBe(false);
    expect(canBlockAtAll(board.game, sick)).toBe(true);
  });

  it("menace needs two blockers (CR 702.111b)", () => {
    const board = combatBoard({ [PIKER]: ["Menace"] });
    const piker = board.play(PIKER, 0);
    const first = board.play(BEARS, 1);
    attackWith(board, new Map([[piker.id, 1]]), new Map([[first.id, [piker.id]]]));
    declareAttackers(board.game);
    declareBlockers(board.game);
    expect(combat(board).blockers.get(piker.id) ?? []).toEqual([]);

    const again = combatBoard({ [PIKER]: ["Menace"] });
    const p2 = again.play(PIKER, 0);
    const a = again.play(BEARS, 1);
    const b = again.play(BEARS, 1);
    attackWith(again, new Map([[p2.id, 1]]), new Map([[a.id, [p2.id]], [b.id, [p2.id]]]));
    declareAttackers(again.game);
    declareBlockers(again.game);
    expect(combat(again).blockers.get(p2.id)).toHaveLength(2);
  });
});

describe("combat damage (CR 510)", () => {
  it("unblocked damages the player; blocked doesn't; trading creatures both die", () => {
    const board = combatBoard();
    fight(board, board.play(BEARS, 0));
    expect(player(board.game, 1).life).toBe(38);

    const traded = combatBoard();
    const attacker = traded.play(BEARS, 0);
    const blocker = traded.play(BEARS, 1);
    fight(traded, attacker, [blocker]);
    expect(player(traded.game, 1).life).toBe(40);
    expect(onBattlefield(traded, attacker)).toBe(false);
    expect(onBattlefield(traded, blocker)).toBe(false);
  });

  it("trample carries the excess over; without it nothing does (CR 702.19b, 510.1c)", () => {
    const board = combatBoard({ [RHOX]: ["Trample"] });
    fight(board, board.play(RHOX, 0), [board.play(BEARS, 1)]);
    expect(player(board.game, 1).life).toBe(37);

    const plain = combatBoard();
    const blocker = plain.play(BEARS, 1);
    fight(plain, plain.play(GIANT, 0), [blocker]);
    expect(player(plain.game, 1).life).toBe(40);
    expect(onBattlefield(plain, blocker)).toBe(false);
  });

  it("deathtouch makes 1 lethal, also for trample's assignment (CR 702.2b-c)", () => {
    const board = combatBoard({ [RATS]: ["Deathtouch"] });
    const angel = board.play(ANGEL, 1);
    fight(board, board.play(RATS, 0), [angel]);
    expect(onBattlefield(board, angel)).toBe(false);

    const both = combatBoard({ [RHOX]: ["Trample", "Deathtouch"] });
    const blocker = both.play(ANGEL, 1);
    fight(both, both.play(RHOX, 0), [blocker]);
    expect(player(both.game, 1).life).toBe(36);
    expect(onBattlefield(both, blocker)).toBe(false);
  });

  it("lifelink gains life (CR 702.15b)", () => {
    const board = combatBoard({ [HAWK]: ["Lifelink"] });
    fight(board, board.play(HAWK, 0));
    expect(player(board.game, 0).life).toBe(42);
    expect(player(board.game, 1).life).toBe(38);
  });

  it("first strike kills before the regular step; double strike deals twice (CR 510.4, 702.4b)", () => {
    const board = combatBoard({ [KNIGHT]: ["First strike"] });
    const knight = board.play(KNIGHT, 0);
    const bears = board.play(BEARS, 1);
    fight(board, knight, [bears]);
    expect(onBattlefield(board, bears)).toBe(false);
    expect(onBattlefield(board, knight)).toBe(true);

    const twice = combatBoard({ [BLADE]: ["Double strike"] });
    fight(twice, twice.play(BLADE, 0));
    expect(player(twice.game, 1).life).toBe(38);
  });

  it("the default division is lethal to each blocker in turn (CR 510.1c)", () => {
    const board = combatBoard({ [ANGEL]: ["Flying"], [HAWK]: ["Flying"], [SPIDER]: ["Reach"] });
    const attacker = board.play(ANGEL, 0);
    const first = board.play(HAWK, 1);
    const second = board.play(SPIDER, 1);
    attackWith(board, new Map([[attacker.id, 1]]), new Map([[first.id, [attacker.id]], [second.id, [attacker.id]]]));
    declareAttackers(board.game);
    declareBlockers(board.game);
    dealCombatDamage(board.game);
    checkStateBasedActions(board.game);
    expect(onBattlefield(board, first)).toBe(false);
    expect(onBattlefield(board, second)).toBe(true);
  });

  it("an illegal division from the agent is refused for the default (CR 510.1e)", () => {
    const board = combatBoard({ [RHOX]: ["Trample"] });
    const rhox = board.play(RHOX, 0);
    const bears = board.play(BEARS, 1);
    setAgent(board.game, 0, {
      declareAttackers: () => new Map([[rhox.id, 1]]),
      // All five to the player, none to the blocker: not lethal first.
      assignCombatDamage: () => new Map([[1, 5]]),
    });
    setAgent(board.game, 1, { declareBlockers: () => new Map([[bears.id, [rhox.id]]]) });
    declareAttackers(board.game);
    declareBlockers(board.game);
    dealCombatDamage(board.game);
    expect(player(board.game, 1).life).toBe(37);
  });

  it("a blocked creature whose blocker leaves deals nothing (CR 509.1h)", () => {
    const board = combatBoard();
    const attacker = board.play(BEARS, 0);
    const blocker = board.play(BEARS, 1);
    attackWith(board, new Map([[attacker.id, 1]]), new Map([[blocker.id, [attacker.id]]]));
    declareAttackers(board.game);
    declareBlockers(board.game);
    moveObject(board.game, blocker, Zone.GRAVEYARD);
    dealCombatDamage(board.game);
    expect(player(board.game, 1).life).toBe(40);
  });

  it("lethal damage counts damage already marked", () => {
    const board = combatBoard();
    const angel = board.play(ANGEL, 1);
    angel.damage = 3;
    expect(lethalDamage(board.game, angel)).toBe(1);
    expect(lethalDamage(board.game, angel, { marked: new Map([[angel.id, 5]]) })).toBe(1);
    expect(lethalDamage(board.game, board.play(ANGEL, 1), { deathtouch: true })).toBe(1);
  });
});

describe("leaving combat (CR 506.4, 511.3)", () => {
  it("end of combat clears everything", () => {
    const board = combatBoard();
    const bears = board.play(BEARS, 0);
    attackWith(board, new Map([[bears.id, 1]]));
    declareAttackers(board.game);
    expect(combat(board).attacking.has(bears.id)).toBe(true);
    endCombat(board.game);
    expect(combat(board).attacking.size).toBe(0);
  });

  it("a creature that changes controller leaves combat", () => {
    const board = combatBoard();
    const bears = board.play(BEARS, 0);
    attackWith(board, new Map([[bears.id, 1]]));
    declareAttackers(board.game);
    bears.controller = 1;
    expect(checkRemovalFromCombat(board.game)).toEqual([bears.id]);
    expect(combat(board).attacking.has(bears.id)).toBe(false);
    expect(combat(board).leftAttacking.get(bears.id)).toEqual([1, NO_OBJECT]);
  });

  it("put onto the battlefield attacking: what the ninja's returned creature attacked (CR 508.4, 702.49c)", () => {
    const board = combatBoard();
    board.game.step = Step.DECLARE_BLOCKERS;
    const bears = board.play(BEARS, 0);
    attackWith(board, new Map([[bears.id, 1]]));
    declareAttackers(board.game);
    const ninja = board.play(GIANT, 0);
    expect(enterAttacking(board.game, ninja, { like: [bears.id] })).toBe(true);
    expect(combat(board).attacking.get(ninja.id)).toBe(1);
    board.game.phase = Phase.POSTCOMBAT_MAIN;
    expect(enterAttacking(board.game, board.play(GIANT, 0))).toBe(false);
  });
});

describe("blocking requirements (CR 509.1c)", () => {
  it("a creature that blocks each combat if able is made to block", () => {
    const board = combatBoard({ [WALL]: [BLOCKS_IF_ABLE] });
    const bears = board.play(BEARS, 0);
    const wall = board.play(WALL, 1);
    attackWith(board, new Map([[bears.id, 1]]));
    declareAttackers(board.game);
    declareBlockers(board.game);
    expect(combat(board).blocking.get(wall.id)).toEqual([bears.id]);
  });

  it("'must be blocked if able' draws a blocker, the defender's own choice kept where it can be", () => {
    const board = combatBoard({ [PIKER]: [MUST_BE_BLOCKED] });
    const lure = board.play(PIKER, 0);
    const other = board.play(BEARS, 0);
    const blocker = board.play(BEARS, 1);
    attackWith(board, new Map([[lure.id, 1], [other.id, 1]]), new Map([[blocker.id, [other.id]]]));
    declareAttackers(board.game);
    declareBlockers(board.game);
    expect(combat(board).blocking.get(blocker.id)).toEqual([lure.id]);
  });

  it("a requirement never makes a creature block illegally (menace)", () => {
    const board = combatBoard({ [WALL]: [BLOCKS_IF_ABLE], [PIKER]: ["Menace"] });
    const piker = board.play(PIKER, 0);
    const wall = board.play(WALL, 1);
    attackWith(board, new Map([[piker.id, 1]]));
    declareAttackers(board.game);
    declareBlockers(board.game);
    expect(combat(board).blocking.has(wall.id)).toBe(false);
  });
});

describe("costs to attack (CR 508.1c-j)", () => {
  // "Creatures can't attack you unless their controller pays {2} for each
  // creature they control that's attacking you" - here a flat {2} per creature.
  const prison: Ability = {
    $: "Ability",
    kind: ir("AbilityKind.STATIC"),
    text: "Ghostly Prison",
    effects: [
      {
        $: "Effect",
        kind: ir("EffectKind.UNLESS_PAYS"),
        players: { $: "PlayerFilter", scope: ir("PlayerScope.CONTROLLER_OF") },
        pay_cost: {
          $: "Cost",
          components: [{ $: "CostComponent", kind: ir("CostKind.MANA"), mana: { $: "ManaCost", symbols: [{ $: "ManaSymbol", kind: ir("ManaSymbolKind.GENERIC"), generic: 2 }] } }],
        },
        children: [
          {
            $: "Effect",
            kind: ir("EffectKind.RESTRICTION"),
            restrictions: [
              {
                $: "Restriction",
                act: ir("Act.ATTACK_PLAYER"),
                subject: { $: "ObjectFilter", types_all: ir("CardType.CREATURE") },
                players: { $: "PlayerFilter", scope: ir("PlayerScope.YOU") },
              },
            ],
          },
        ],
      },
    ],
  };

  it("an attacker whose tax can't be paid doesn't attack, and isn't tapped", () => {
    const board = combatBoard();
    board.script("Glorious Anthem", prison);
    board.play("Glorious Anthem", 1);
    const bears = board.play(BEARS, 0);
    attackWith(board, new Map([[bears.id, 1]]));
    declareAttackers(board.game);
    expect(combat(board).attacking.has(bears.id)).toBe(false);
    expect(bears.tapped).toBe(false);
  });

  it("paid from untapped lands, it attacks", () => {
    const board = combatBoard();
    board.script("Glorious Anthem", prison);
    board.play("Glorious Anthem", 1);
    const lands = [board.play("Forest", 0), board.play("Forest", 0)];
    const bears = board.play(BEARS, 0);
    attackWith(board, new Map([[bears.id, 1]]));
    declareAttackers(board.game);
    expect(combat(board).attacking.has(bears.id)).toBe(true);
    expect(lands.every((l) => l.tapped)).toBe(true);
  });

  it("a goaded creature is never forced to pay to attack (CR 508.1d)", () => {
    const board = combatBoard();
    board.script("Glorious Anthem", prison);
    board.play("Glorious Anthem", 1);
    board.play("Forest", 0);
    board.play("Forest", 0);
    const bears = board.play(BEARS, 0);
    bears.goadedBy.add(1);
    attackWith(board, new Map());
    declareAttackers(board.game);
    expect(combat(board).attacking.has(bears.id)).toBe(false);
  });
});

describe("BLOCKS_SOURCE_IF_ABLE (CR 509.1c)", () => {
  it("the creature acted on must block the source this turn", () => {
    const board = combatBoard();
    const source = board.play(BEARS, 0);
    const blocker = board.play(BEARS, 1);
    const other = board.play(GIANT, 0);
    execute(newResolution(board.game, source.id, 0, { targets: [[blocker.id]] }), [
      { $: "Effect", kind: ir("EffectKind.BLOCKS_SOURCE_IF_ABLE"), targets: ALL_CREATURES, is_targeted: true, duration: ir("Duration.END_OF_TURN") },
    ]);
    attackWith(board, new Map([[source.id, 1], [other.id, 1]]), new Map([[blocker.id, [other.id]]]));
    declareAttackers(board.game);
    declareBlockers(board.game);
    expect(combat(board).blocking.get(blocker.id)).toEqual([source.id]);
  });
});
