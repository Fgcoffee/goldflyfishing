/**
 * Real parsed cards for board tests: the IR in schema/fixtures/ (exported by
 * `ir_export fixtures` from the names in schema/fixtures.txt), read the way
 * play() reads a deck's card documents. For tests whose point is how a real
 * card was read, where a scripted ability would hide the bug.
 */

import type { CardDocument } from "../../src/ir";
import { complete, faceAbilities } from "../../src/ir";
import type { Ability } from "../../src/ir";
import type { CardDef } from "../../src/kernel/card";
import { Zone } from "../../src/kernel/enums";
import { createObject, newGame, type AbilityProvider, type Game } from "../../src/kernel/game";
import type { GameObject } from "../../src/kernel/gameobject";
import { newPlayer } from "../../src/rules/cr100_game_concepts/player";
import { characteristicsOf } from "../../src/rules/cr600_spells_and_abilities/layers";
import type { Characteristics } from "../../src/rules/cr200_parts_of_a_card/characteristics";

const FILES = import.meta.glob("../../../schema/fixtures/*.json", { eager: true, import: "default" }) as Record<string, CardDocument>;
const BY_NAME = new Map<string, CardDocument>();
for (const doc of Object.values(FILES)) BY_NAME.set(doc.name, doc);
const CARDS = new Map<string, CardDef>();

/** The kernel CardDef of a fixture card, by name (one object per card). */
export function realCard(name: string): CardDef {
  let card = CARDS.get(name);
  if (card === undefined) {
    const doc = BY_NAME.get(name);
    if (doc === undefined) throw new Error(`${name} isn't in schema/fixtures.txt`);
    CARDS.set(name, (card = complete(doc.card) as unknown as CardDef));
  }
  return card;
}

/** Abilities from the fixtures' IR, as play() provides them. */
export const REAL_ABILITIES: AbilityProvider = {
  abilitiesFor(card: unknown, faceIndex: number): readonly Ability[] {
    const name = (card as CardDef | null)?.name;
    const face = name === undefined ? undefined : BY_NAME.get(name)?.faces.find((f) => f.face === faceIndex);
    return face === undefined ? [] : faceAbilities(face);
  },
};

/** A two-player board whose cards are read from real IR. */
export class RealBoard {
  readonly game: Game;
  constructor(players = 2) {
    this.game = newGame(
      Array.from({ length: players }, (_, i) => newPlayer(i)),
      { abilityProvider: REAL_ABILITIES },
    );
  }
  play(name: string, controller = 0): GameObject {
    const obj = createObject(this.game, realCard(name), controller, Zone.BATTLEFIELD);
    obj.summoningSick = false;
    this.game.epoch += 1;
    return obj;
  }
  hand(name: string, owner = 0): GameObject {
    const obj = createObject(this.game, realCard(name), owner, Zone.HAND);
    this.game.epoch += 1;
    return obj;
  }
  chars(obj: GameObject): Characteristics {
    return characteristicsOf(this.game, obj);
  }
}
