/**
 * Resolving the top of the stack (CR 608). PORT: placeholder -
 * mtgfish/rules/cr600_spells_and_abilities/cr608_stack.py and resolve.py are
 * the "Resolving the stack" item of ROADMAP "rules". Nothing reaches the stack
 * until casting is ported, so reaching this says so plainly.
 */

import type { Ability } from "../../ir";
import { Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import type { Game } from "../../kernel/game";
import { newGameObject, ObjectKind, type GameObject } from "../../kernel/gameobject";
import { allocateObjectId, allocateTimestamp } from "../../kernel/ids";
import { emit } from "../emit";

export function resolveTop(_game: Game): void {
  throw new Error("resolving the stack is not ported yet (rules/cr600_spells_and_abilities/stack.ts)");
}

/**
 * Put an activated or triggered ability on the stack (CR 113.7), with its
 * targets and modes (CR 602.2b, 700.2a). Ported from cr608_stack.push_ability.
 */
export function pushAbility(
  game: Game,
  source: GameObject,
  ability: Ability,
  options: { targets?: readonly (readonly number[])[]; xValue?: number; chosenModes?: readonly number[] } = {},
): GameObject {
  const obj = newGameObject(allocateObjectId(game.ids), {
    kind: ObjectKind.ABILITY,
    owner: source.controller,
    controller: source.controller,
    zone: Zone.STACK,
    timestamp: allocateTimestamp(game.ids),
    ability,
    source: source.id,
    targets: (options.targets ?? []).map((g) => g.slice()),
    xValue: options.xValue ?? 0,
    chosenModes: (options.chosenModes ?? []).slice(),
  });
  game.objects.set(obj.id, obj);
  game.stack.push(obj.id);
  emit(game, event(EventKind.PUT_ON_STACK, { objectId: obj.id, player: source.controller, source: source.id }));
  return obj;
}
