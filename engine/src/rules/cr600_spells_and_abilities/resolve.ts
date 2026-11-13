/**
 * Carrying out effects as a spell or ability resolves (CR 608). The
 * Resolution record is ported from mtgfish/rules/cr600_spells_and_abilities/
 * resolve.py; the executors are the "Resolving the stack" item of ROADMAP
 * "rules". Signatures here are final, published for rules/mana and match/.
 */

import type { Effect } from "../../ir";
import type { Color } from "../../kernel/enums";
import type { Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import { NO_PLAYER, type ObjectId, type PlayerId } from "../../kernel/ids";

/** What one resolution knows as it carries out its effects. */
export interface Resolution {
  game: Game;
  source: ObjectId;
  controller: PlayerId;
  stackObject: GameObject | null;
  targets: readonly (readonly number[])[];
  xValue: number;
  eventAmount: number;
  /** What the resolution last acted on: "it", "that creature" (CR 608.2c). */
  remembered: ObjectId[];
  targetIndex: number;
  chosenModes: readonly number[];
  dieResults: readonly number[];
  /** The colour chosen for "add one mana of any color". */
  manaColor: Color;
  thatPlayer: PlayerId;
  /** Events this resolution caused, by kind: "for each ... this way". */
  thisWay: Map<number, number>;
  /** Whether the last "you may" was carried out. */
  didIt: boolean | null;
  pile: ObjectId[];
  triggerEvent: unknown;
  chosenPlayers: PlayerId[];
  unregenerable: boolean;
}

export function newResolution(
  game: Game,
  source: ObjectId,
  controller: PlayerId,
  fields: Partial<Omit<Resolution, "game" | "source" | "controller">> = {},
): Resolution {
  return {
    game,
    source,
    controller,
    stackObject: null,
    targets: [],
    xValue: 0,
    eventAmount: 0,
    remembered: [],
    targetIndex: 0,
    chosenModes: [],
    dieResults: [],
    manaColor: 0,
    thatPlayer: NO_PLAYER,
    thisWay: new Map(),
    didIt: null,
    pile: [],
    triggerEvent: null,
    chosenPlayers: [],
    unregenerable: false,
    ...fields,
  };
}

const notYet = (name: string): never => {
  throw new Error(`${name} is not ported yet (rules/cr600_spells_and_abilities/resolve.ts)`);
};

/**
 * Resolve a mana ability at once, without the stack (CR 605.3b). With
 * `tapped` ({T} in its cost), the permanent was tapped for mana (CR 106.12a).
 * PORT: placeholder.
 */
export function resolveManaAbility(_resolution: Resolution, _effects: readonly Effect[], _options: { tapped: boolean }): void {
  notYet("resolveManaAbility");
}

/** The colours "one mana of any color" may be, for this controller (CR 903.4). PORT: placeholder. */
export function manaColorChoices(_game: Game, _controller: PlayerId, _effect: Effect): Color[] {
  return notYet("manaColorChoices");
}
