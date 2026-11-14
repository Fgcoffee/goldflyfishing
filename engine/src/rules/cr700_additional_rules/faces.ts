/**
 * Faces, transforming and copying (CR 707, 709, 712, 715, 718). Ported from
 * mtgfish/rules/cr700_additional_rules/cr707_faces.py. layoutOf,
 * castableFaceIndices and playableLandFaceIndices are the rules session's
 * (42b6854); the rest is the parser session's.
 *
 * CR 712.18: transforming does not create a new object. Everywhere else an
 * object that changes what it is becomes a new one (CR 400.7); a
 * transforming permanent keeps its id, counters, damage, Auras and effects.
 *
 * CR 707.2: a copy takes only copiable values - the printed
 * characteristics as modified by copy and text-changing effects, nothing
 * from layers 2-7 (CR 613.2). A copy of a pumped creature isn't pumped.
 */

import type { Ability, Effect } from "../../ir";
import type { CardDef, FaceDef } from "../../kernel/card";
import { CardType, Layout, Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { agentFor, invalidateAllCharacteristics, logRecord, type Game } from "../../kernel/game";
import { newGameObject, ObjectKind, type GameObject } from "../../kernel/gameobject";
import { allocateObjectId, allocateTimestamp, NO_OBJECT, type PlayerId } from "../../kernel/ids";
import { emit } from "../emit";
import { ir } from "../ir_read";
import { hasPrototypeLine } from "../cr300_card_types/card_types";
import { characteristicsOf, layerFor } from "../cr600_spells_and_abilities/layers";
import {
  abilityTargetingEffects,
  candidatesFor,
  settleTargetChoices,
  targetingEffects,
  type Targets,
} from "../cr600_spells_and_abilities/targeting";

export { copiableCharacteristics } from "../cr600_spells_and_abilities/layers";

export function layoutOf(obj: GameObject): number {
  return (obj.card as CardDef | null)?.layout ?? Layout.NORMAL;
}

const facesOf = (obj: GameObject) => (obj.card as CardDef | null)?.faces ?? [];

/** Faces of a card that may be cast (CR 712.11, 712.12, 709, 715, 718.3, 722.3). */
export function castableFaceIndices(_game: Game, obj: GameObject): number[] {
  const faces = facesOf(obj);
  if (faces.length === 0) return [0];
  if (obj.prepareCopyOf !== NO_OBJECT) return [1]; // CR 722.3
  const layout = layoutOf(obj);
  if (layout === Layout.MODAL_DFC) {
    return faces.flatMap((f, i) => ((f.type_line.types & CardType.LAND) === 0 && f.has_mana_cost ? [i] : []));
  }
  if (layout === Layout.SPLIT || layout === Layout.ADVENTURE) return faces.flatMap((f, i) => (f.has_mana_cost ? [i] : []));
  if (layout === Layout.PROTOTYPE) {
    // CR 718.3: index 1 is the prototype face the card data doesn't print.
    return faces.length > 1 || hasPrototypeLine(faces[0]!) ? [0, 1] : [0];
  }
  return [0]; // CR 712.11
}

/** Faces that may be played as a land (CR 712.12). */
export function playableLandFaceIndices(_game: Game, obj: GameObject): number[] {
  const faces = facesOf(obj);
  if (faces.length === 0) return [];
  if (layoutOf(obj) === Layout.MODAL_DFC) return faces.flatMap((f, i) => ((f.type_line.types & CardType.LAND) !== 0 ? [i] : []));
  return (faces[0]!.type_line.types & CardType.LAND) !== 0 ? [0] : [];
}

/** CR 712.14: entering from anywhere but the stack means front face up. */
export function defaultBattlefieldFace(_obj: GameObject): number {
  return 0;
}

// ---------------------------------------------------------------------------
// Transforming (CR 701.28, 712)
// ---------------------------------------------------------------------------

/** CR 712.9: only these transform. A meld card's back is reached by melding. */
export const TRANSFORMABLE_LAYOUTS: ReadonlySet<number> = new Set([Layout.TRANSFORM, Layout.MODAL_DFC, Layout.DOUBLE_FACED_TOKEN]);

export function faceCount(obj: GameObject): number {
  return facesOf(obj).length;
}

export function otherFace(obj: GameObject): FaceDef | null {
  const faces = facesOf(obj);
  if (faces.length < 2) return null;
  return (obj.faceIndex < 2 ? faces[1 - obj.faceIndex] : faces[0]) ?? null;
}

const isSpellFace = (face: FaceDef): boolean => (face.type_line.types & (CardType.INSTANT | CardType.SORCERY)) !== 0;

/**
 * CR 712.9, 712.10, 712.16: a transforming kind of card, with another face
 * that isn't an instant or sorcery, on the battlefield and face up.
 */
export function canTransform(_game: Game, obj: GameObject): boolean {
  if (obj.zone !== Zone.BATTLEFIELD) return false;
  if (!TRANSFORMABLE_LAYOUTS.has(layoutOf(obj)) || faceCount(obj) < 2) return false;
  if (obj.faceDown) return false; // CR 712.16
  const other = otherFace(obj);
  return other !== null && !isSpellFace(other);
}

/** CR 712.14a: a double-faced card whose back face is a permanent. */
export function canEnterTransformed(obj: GameObject): boolean {
  if (!TRANSFORMABLE_LAYOUTS.has(layoutOf(obj)) || faceCount(obj) < 2) return false;
  return !isSpellFace(facesOf(obj)[1]!);
}

/**
 * Turn a double-faced permanent over (CR 701.28). Mutates it in place:
 * CR 712.18 says it is not a new object, so attachments, counters and
 * effects carry straight over.
 */
export function transform(game: Game, obj: GameObject): boolean {
  if (!canTransform(game, obj)) return false;
  obj.faceIndex = 1 - obj.faceIndex;
  invalidateAllCharacteristics(game);
  logRecord(game, `${characteristicsOf(game, obj).name || `object ${obj.id}`} transforms`, { kind: "transform", player: obj.controller });
  emit(game, event(EventKind.TRANSFORMED, { objectId: obj.id, player: obj.controller }));
  return true;
}

// ---------------------------------------------------------------------------
// Copying (CR 707)
// ---------------------------------------------------------------------------

/**
 * Make one permanent a copy of another (CR 707.1): a layer 1 continuous
 * effect, so it's reapplied on every computation and a later copy effect
 * overrides it by timestamp.
 */
export function copyPermanent(game: Game, copier: GameObject, original: GameObject): boolean {
  const effect: Effect = {
    $: "Effect",
    kind: ir("EffectKind.COPY_PERMANENT"),
    copy_source: original.id,
    text: `copy of ${characteristicsOf(game, original).name}`,
  };
  game.continuousEffects.push({
    effect,
    source: copier.id,
    controller: copier.controller,
    timestamp: allocateTimestamp(game.ids),
    layer: layerFor(effect),
    duration: 0,
    createdTurn: game.turn,
    ability: null,
    fromStaticAbility: false,
    isCda: false,
    expired: false,
  });
  invalidateAllCharacteristics(game);
  emit(game, event(EventKind.COPIED, { objectId: copier.id, source: original.id }));
  return true;
}

/**
 * CR 707.10c: the `Effect.keywords` entry on a COPY_SPELL whose text says
 * its controller "may choose new targets for the copy". Without it the copy
 * keeps the original's targets (CR 707.10).
 */
export const MAY_CHOOSE_NEW_TARGETS = "may choose new targets";

/**
 * Put a copy of a spell or ability onto the stack (CR 707.10). The copy
 * isn't cast, so nothing that triggers on casting sees it. It takes the
 * original's characteristics and choices: modes, targets, X.
 */
export function copySpell(
  game: Game,
  original: GameObject,
  options: { controller?: PlayerId | null; mayChooseNewTargets?: boolean } = {},
): GameObject | null {
  if (original.zone !== Zone.STACK) return null;
  // CR 707.10, 112.2: owned as well as controlled by whoever puts it on the stack.
  const owner = options.controller ?? original.controller;
  const copy = newGameObject(allocateObjectId(game.ids), {
    kind: original.ability === null ? ObjectKind.COPY : ObjectKind.ABILITY,
    owner,
    controller: owner,
    baseController: owner,
    zone: Zone.STACK,
    card: original.card,
    faceIndex: original.faceIndex,
    timestamp: allocateTimestamp(game.ids),
    ability: original.ability,
    source: original.source,
    // CR 707.2: the choices made when casting are copied too.
    targets: original.targets.map((t) => [...t]),
    chosenModes: [...original.chosenModes],
    xValue: original.xValue,
    // CR 715.3c, 718.3c, 720.3c: a copy of an Adventure, prototype or Omen is one too.
    castMode: original.castMode,
  });
  game.objects.set(copy.id, copy);
  game.stack.push(copy.id);
  invalidateAllCharacteristics(game);
  if (options.mayChooseNewTargets) chooseNewTargets(game, copy, owner);
  logRecord(game, `A copy of ${characteristicsOf(game, original).name || "a spell"} is put onto the stack`, { kind: "copy", player: owner });
  emit(game, event(EventKind.COPIED, { objectId: copy.id, source: original.id }));
  return copy;
}

interface NewTargetChooser {
  chooseNewTargets?(game: Game, controller: PlayerId, copy: number, current: Targets, candidates: Targets): Targets | null;
}

/**
 * CR 707.10c: the copy's controller may choose new targets, through the
 * agent's `chooseNewTargets`. Without one the original's targets stay,
 * always a legal answer. A choice is settled against the copy's own legal
 * candidates, as targets chosen on casting are.
 */
export function chooseNewTargets(game: Game, copy: GameObject, controller: PlayerId): void {
  if (copy.targets.length === 0) return;
  const agent = agentFor(game, controller) as NewTargetChooser | null | undefined;
  const chooser = agent?.chooseNewTargets;
  if (typeof chooser !== "function") return;
  const chosen = copy.chosenModes.length ? copy.chosenModes : null;
  const effects = copy.ability !== null ? abilityTargetingEffects(copy.ability as Ability, chosen) : targetingEffects(game, copy);
  if (effects.length !== copy.targets.length) return;
  const candidates = effects.map((effect) => candidatesFor(game, copy, effect, controller));
  const picked = chooser.call(agent, game, controller, copy.id, copy.targets, candidates);
  if (picked === null || picked === undefined || sameTargets(picked, copy.targets)) return;
  copy.targets = settleTargetChoices(effects, candidates, picked, { fillRequired: true });
  logRecord(game, "New targets are chosen for a copy", { kind: "copy", player: controller });
}

function sameTargets(a: Targets, b: Targets): boolean {
  return a.length === b.length && a.every((group, i) => group.length === b[i]!.length && group.every((id, j) => id === b[i]![j]));
}
