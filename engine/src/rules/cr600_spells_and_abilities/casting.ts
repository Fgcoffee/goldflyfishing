/**
 * Casting spells, activating abilities and playing lands (CR 601, 602, 305).
 * PORT: placeholder - mtgfish/rules/cr600_spells_and_abilities/cr601_casting.py
 * is the "Casting spells and activating abilities" item of ROADMAP "rules".
 * Until then every attempt is abandoned with a CastError, which the priority
 * loop treats as an action that did not happen (CR 601.2h).
 */

import type { Ability, Effect } from "../../ir";
import type { Game } from "../../kernel/game";
import type { GameObject } from "../../kernel/gameobject";
import type { ObjectId, PlayerId } from "../../kernel/ids";
import type { ManaCost } from "../mana/cost";
import type { Action } from "../cr100_game_concepts/priority";

/** An attempt that could not be completed; CR 601.2h has rewound it. */
export { CastError } from "./cast_error";
import { CastError } from "./cast_error";

export function castSpell(_game: Game, _player: PlayerId, _action: Action): void {
  throw new CastError("casting is not ported yet");
}

export function activateAbility(_game: Game, _player: PlayerId, _action: Action): void {
  throw new CastError("activating abilities is not ported yet");
}

export function playLand(_game: Game, _player: PlayerId, _action: Action): void {
  throw new CastError("playing lands is not ported yet");
}

// -- Activation costs and modes. Final signatures, published for
// rules/mana/plan.ts and the ManaPlanHost (Python `_can_pay_activation`,
// `activation_mana_cost`, `_pay_activation`, `modal_effect`, `legal_modes`,
// `choose_modes` in cr601_casting.py). PORT: bodies come with casting.

const notYet = (name: string): never => {
  throw new Error(`${name} is not ported yet (rules/cr600_spells_and_abilities/casting.ts)`);
};

/** Whether an ability's cost could be paid now, for an announced X (CR 107.3a). */
export function canPayActivation(_game: Game, _source: GameObject, _ability: Ability, _xValue = 0): boolean {
  return notYet("canPayActivation");
}

/** The mana part of an activation cost, after cost changes (CR 601.2f). */
export function activationManaCost(_game: Game, _source: GameObject, _ability: Ability): ManaCost {
  return notYet("activationManaCost");
}

/**
 * Pay an activation cost with X as announced (CR 107.3a, 601.2f-h); taps
 * through `actions.tap`. Throws CastError, having paid nothing, if it can't.
 */
export function payActivation(_game: Game, _source: GameObject, _ability: Ability, _xValue = 0): void {
  notYet("payActivation");
}

export { chooseModes, legalModes, modalEffect } from "./modes";
