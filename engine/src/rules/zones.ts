/**
 * Moving objects between zones (CR 400.7): the new object remembers nothing.
 * Ported from `Game.move_object`, `_emit_zone_specific`, `_may_enter_the_
 * battlefield`, `_detach_all`, `_remove_from_zone`, `choose_protector` and
 * `eligible_protectors` in mtgfish/rules/kernel/game.py, with the small
 * helpers they call (forget_designations, carry_payment_record,
 * entering_counters, can_enter_transformed).
 *
 * Replacement effects apply before the move (CR 614), through
 * cr600_spells_and_abilities/replacement_hooks.ts until cr614 is ported.
 */

import { carryPerpetual } from "./cr700_additional_rules/digital_mechanics";
import { CardType, Layout, Zone, zoneName } from "../kernel/enums";
import type { CardDef } from "../kernel/card";
import { event, EventKind, replaced, type GameEvent } from "../kernel/events";
import {
  agentFor,
  invalidateAllCharacteristics,
  logRecord,
  opponents,
  player,
  zoneList,
  type Game,
} from "../kernel/game";
import { counterCount, newGameObject, ObjectKind, type GameObject } from "../kernel/gameobject";
import { allocateObjectId, allocateTimestamp, NO_OBJECT, NO_PLAYER, type ObjectId, type PlayerId } from "../kernel/ids";
import { addCounters } from "./cr100_game_concepts/actions";
import { hasKeyword, hasSubtype, hasType, type Characteristics } from "./cr200_parts_of_a_card/characteristics";
import { isPermanentType } from "./cr200_parts_of_a_card/typeline";
import { characteristicsOf, printedCharacteristics } from "./cr600_spells_and_abilities/layers";
import { replacementHooks } from "./cr600_spells_and_abilities/replacement_hooks";
import { emit } from "./emit";

export interface MoveOptions {
  /** Whose zone or control it goes to; owned zones always take the owner. */
  toPlayer?: PlayerId;
  toTop?: boolean;
  position?: number;
  /** Put it there face down (CR 708.3, 406.3), naming what did it. */
  faceDown?: string;
  /** Put a double-faced card onto the battlefield back face up (CR 712.14a). */
  transformed?: boolean;
}

/** Alternative costs whose spells are exiled as they leave the stack. */
const EXILED_WHEN_IT_LEAVES_THE_STACK: ReadonlySet<string> = new Set(["flashback", "jump-start", "harmonize"]);

/** cr300_card_types.CastMode values that a permanent keeps from its spell. */
const CAST_MODE_PROTOTYPE = 2;
const CAST_MODE_FLIPPED = 4;
const PERSISTS_ON_BATTLEFIELD: ReadonlySet<number> = new Set([CAST_MODE_PROTOTYPE, CAST_MODE_FLIPPED]);

/**
 * Move an object to another zone, creating a new object (CR 400.7). Returns
 * the new object - or the old one, unmoved, when the move was prevented or
 * the object may not go there. Callers must not keep using the old one.
 */
export function moveObject(game: Game, obj: GameObject, toZoneIn: Zone, options: MoveOptions = {}): GameObject {
  const fromZone = obj.zone;
  const owner = obj.owner;
  const hooks = replacementHooks();
  let toZone = toZoneIn;
  let toPlayer = options.toPlayer;
  let faceDown = options.faceDown;
  const transformed = options.transformed ?? false;

  // CR 614: replacements apply before the move, so nothing is undone.
  let prospective: GameEvent = event(EventKind.ZONE_CHANGE, { objectId: obj.id, player: owner, fromZone, toZone });
  prospective = hooks.commanderZoneReplacement(game, prospective);
  if (fromZone === Zone.BATTLEFIELD && prospective.toZone === Zone.GRAVEYARD && counterCount(obj, "finality") > 0) {
    // CR 122.1: a finality counter exiles it instead.
    prospective = replaced(prospective, { toZone: Zone.EXILE });
  }
  if (fromZone === Zone.STACK && prospective.toZone !== Zone.EXILE && exiledAsItLeavesTheStack(game, obj)) {
    // CR 702.34a, 702.133a, 702.180a, 702.127a.
    prospective = replaced(prospective, { toZone: Zone.EXILE });
  }
  if (hooks.hasReplacements(game)) {
    const result = hooks.replace(game, prospective);
    if (result === null) return obj; // The move was prevented.
    prospective = result;
  }
  // CR 717.6: after the other replacements, whatever they produced.
  prospective = hooks.junkyardReplacement(game, obj, prospective);
  if (prospective.toZone !== null) toZone = prospective.toZone;
  if (toZone === Zone.COMMAND) toPlayer = owner;
  else if (toZone === Zone.LIBRARY || toZone === Zone.GRAVEYARD || toZone === Zone.HAND) toPlayer = owner; // CR 400.3

  // CR 708.4: a face-down spell becomes a face-down permanent; elsewhere
  // than the battlefield or exile, a card is face up.
  if (faceDown === undefined && obj.faceDown && fromZone === Zone.STACK && toZone === Zone.BATTLEFIELD) faceDown = obj.faceDownBy;
  if (toZone !== Zone.BATTLEFIELD && toZone !== Zone.EXILE) faceDown = undefined;
  const arrivesFaceDown = faceDown !== undefined;

  // CR 304.4, 307.4: an instant or sorcery stays where it is.
  if (toZone === Zone.BATTLEFIELD && !arrivesFaceDown && !mayEnterTheBattlefield(game, obj)) return obj;
  // CR 712.14a: a card that isn't double-faced, told to enter transformed, stays.
  if (transformed && toZone === Zone.BATTLEFIELD && !canEnterTransformed(obj)) return obj;

  // CR 303.4f-g: an Aura entering other than as a resolving Aura spell.
  const auraEntersUnattached =
    toZone === Zone.BATTLEFIELD && fromZone !== Zone.STACK && !arrivesFaceDown && hooks.auraGovernsEntry(game, obj);
  if (auraEntersUnattached && hooks.auraLegalHosts(game, obj, toPlayer ?? owner).length === 0) return obj;

  // CR 306.5b, 310.4b: loyalty and defense counters, read before the move.
  let entering: [string, number][] = [];
  if (toZone === Zone.BATTLEFIELD && !arrivesFaceDown) {
    const chars = characteristicsOf(game, obj);
    entering = enteringCounters(chars);
    if (obj.phyrexianPaidWithLife > 0 && hasKeyword(chars, "Compleated")) {
      // CR 702.150a: two fewer loyalty per Phyrexian symbol paid with life.
      const fewer = 2 * obj.phyrexianPaidWithLife;
      entering = entering.map(([kind, amount]) => [kind, kind === "loyalty" ? Math.max(0, amount - fewer) : amount]);
    }
  }

  const destinationPlayer = toPlayer ?? owner;
  forgetDesignations(game, obj.id);

  if (fromZone === Zone.BATTLEFIELD) {
    // CR 603.10a: read before anything attached to it falls off.
    obj.lastKnownCharacteristics = characteristicsOf(game, obj);
    if (game.departedBatch !== game.eventBatch) {
      game.departedBatch = game.eventBatch;
      game.departedInBatch = [];
    }
    game.departedInBatch.push(obj.id);
  }
  detachAll(game, obj);
  removeFromZone(game, obj);

  // CR 111.7: a token leaving the battlefield ceases to exist. Its events go
  // out while it still reads as where it was (CR 603.6e, 603.10a).
  if (obj.kind === ObjectKind.TOKEN && toZone !== Zone.BATTLEFIELD) {
    invalidateAllCharacteristics(game);
    emit(game, event(EventKind.ZONE_CHANGE, { objectId: obj.id, player: owner, fromZone, toZone, data: [obj.id] }));
    emitZoneSpecific(game, obj, fromZone, toZone, obj);
    obj.zone = toZone;
    invalidateAllCharacteristics(game);
    emit(game, event(EventKind.CEASED_TO_EXIST, { objectId: obj.id, player: owner }));
    return obj;
  }

  // CR 707.10f, 608.3f: a copy of a permanent spell becomes a token.
  const kind = obj.kind === ObjectKind.COPY && toZone === Zone.BATTLEFIELD ? ObjectKind.TOKEN : obj.kind;
  const onBattlefield = toZone === Zone.BATTLEFIELD;
  const created = newGameObject(allocateObjectId(game.ids), {
    kind,
    owner,
    controller: onBattlefield ? destinationPlayer : owner,
    baseController: onBattlefield ? destinationPlayer : owner,
    zone: toZone,
    card: obj.card,
    faceIndex: !onBattlefield ? 0 : transformed ? 1 : obj.faceIndex,
    timestamp: allocateTimestamp(game.ids),
    isCommander: obj.isCommander,
  });
  if (arrivesFaceDown) {
    created.faceDown = true;
    created.faceDownBy = faceDown ?? "";
  } else if (obj.faceDown && (fromZone === Zone.BATTLEFIELD || fromZone === Zone.STACK)) {
    // CR 708.9: revealed as it leaves.
    reveal(game, obj, `it left the ${zoneName(fromZone).toLowerCase()}`);
  }
  if (fromZone === Zone.STACK && toZone === Zone.BATTLEFIELD) {
    // CR 718.3b: a prototyped spell becomes a prototyped permanent.
    if (PERSISTS_ON_BATTLEFIELD.has(obj.castMode)) created.castMode = obj.castMode;
    // CR 603.4, 614.1c: the payment record is there before the event.
    carryPaymentRecord(obj, created);
  }
  if (onBattlefield) {
    created.enteredBattlefieldTurn = game.turn;
    created.summoningSick = true;
    // CR 310.9a: a battle's protector is chosen as it enters.
    chooseProtector(game, created);
  }

  // The old object keeps its old zone: last-known information (CR 603.6e).
  obj.supersededBy = created.id;
  created.previousId = obj.id;
  // MTG Arena's "perpetually": the changes follow the card to the new object.
  carryPerpetual(game, obj.id, created.id);

  game.objects.set(created.id, created);
  // KNOWN: CR 717.6a (arrived_in_command_zone, the Attraction junkyard pile)
  // is not ported yet.
  if (created.isCommander) {
    game.commanderOrigin.set(created.id, commanderIdentity(game, obj.id));
    // CR 903.9a: its owner chooses at the next state-based action check.
    if (toZone === Zone.GRAVEYARD || toZone === Zone.EXILE) game.commandersAwaitingZoneChoice.push(created.id);
  }
  const target = zoneList(game, toZone, destinationPlayer);
  if (options.position !== undefined) target.splice(options.position, 0, created.id);
  else if (options.toTop) target.unshift(created.id);
  else target.push(created.id);

  invalidateAllCharacteristics(game);
  if (auraEntersUnattached) hooks.auraAttachAsItEnters(game, created, hooks.auraLegalHosts(game, created, created.controller));
  // CR 122.6, 614.1c: entering with counters is having them put on it.
  for (const [counter, amount] of entering) addCounters(game, created, counter, amount, { source: created.id });
  // CR 406.7: exile to exile is not a zone change.
  if (!(fromZone === Zone.EXILE && toZone === Zone.EXILE)) {
    emit(game, event(EventKind.ZONE_CHANGE, { objectId: created.id, player: owner, fromZone, toZone, data: [obj.id] }));
  }
  emitZoneSpecific(game, created, fromZone, toZone, obj);
  return created;
}

/** "Dies" is battlefield to graveyard specifically (CR 700.4). */
function emitZoneSpecific(game: Game, obj: GameObject, fromZone: Zone, toZone: Zone, previous: GameObject | null): void {
  const data = previous !== null ? [previous.id] : [];
  if (toZone === Zone.BATTLEFIELD) {
    replacementHooks().applySelfEntryReplacements(game, obj);
    emit(game, event(EventKind.ENTERS_BATTLEFIELD, { objectId: obj.id, player: obj.controller, fromZone }));
    return;
  }
  if (fromZone === Zone.BATTLEFIELD) {
    emit(game, event(EventKind.LEAVES_BATTLEFIELD, { objectId: obj.id, player: obj.owner, toZone, data }));
    if (toZone === Zone.GRAVEYARD) emit(game, event(EventKind.DIES, { objectId: obj.id, player: obj.owner, data }));
  }
  if (toZone === Zone.GRAVEYARD) {
    emit(game, event(EventKind.PUT_INTO_GRAVEYARD, { objectId: obj.id, player: obj.owner, fromZone, toZone, data }));
  }
}

/** Whether a spell is exiled rather than going anywhere else. */
function exiledAsItLeavesTheStack(game: Game, obj: GameObject): boolean {
  if (EXILED_WHEN_IT_LEAVES_THE_STACK.has(obj.alternativeCostPaid.toLowerCase())) return true;
  // CR 702.127a: aftermath asks where it was cast from.
  return obj.castFrom === Zone.GRAVEYARD && hasKeyword(characteristicsOf(game, obj), "Aftermath");
}

/**
 * CR 304.4, 307.4: only a permanent card becomes a permanent. Any face will
 * do: which face it enters with is settled after the move (CR 712).
 */
function mayEnterTheBattlefield(game: Game, obj: GameObject): boolean {
  if (isPermanentType(characteristicsOf(game, obj).typeLine)) return true;
  const card = obj.card as Partial<CardDef> | null;
  return (card?.faces ?? []).some((f) => isPermanentType(f.type_line));
}

/** CR 712.14a: a double-faced card whose back face is a permanent. */
function canEnterTransformed(obj: GameObject): boolean {
  const card = obj.card as Partial<CardDef> | null;
  const faces = card?.faces ?? [];
  const transformable = card?.layout === Layout.TRANSFORM || card?.layout === Layout.MODAL_DFC || card?.layout === Layout.DOUBLE_FACED_TOKEN;
  const back = faces[1];
  if (!transformable || back === undefined) return false;
  return (back.type_line.types & (CardType.INSTANT | CardType.SORCERY)) === 0;
}

export function removeFromZone(game: Game, obj: GameObject): void {
  const contents = zoneList(game, obj.zone, obj.owner);
  const at = contents.indexOf(obj.id);
  if (at >= 0) contents.splice(at, 1);
}

/** Unattach everything attached to this object, and detach it from its host. */
export function detachAll(game: Game, obj: GameObject): void {
  if (obj.attachedTo !== NO_OBJECT) {
    const host = game.objects.get(obj.attachedTo);
    if (host !== undefined) {
      const at = host.attachments.indexOf(obj.id);
      if (at >= 0) host.attachments.splice(at, 1);
    }
    obj.attachedTo = NO_OBJECT;
  }
  obj.attachedPlayer = NO_PLAYER;
  for (const id of obj.attachments) {
    const attachment = game.objects.get(id);
    if (attachment !== undefined) attachment.attachedTo = NO_OBJECT;
  }
  obj.attachments = [];
}

/**
 * CR 400.7, 716.2b, 719.3b, 722.3c: solved, class level and prepared last
 * only while the permanent is on the battlefield.
 */
export function forgetDesignations(game: Game, id: ObjectId): void {
  if (id === NO_OBJECT) return;
  game.solvedPermanents.delete(id);
  game.classLevels.delete(id);
  game.preparedPermanents.delete(id);
}

/** Give the permanent a spell becomes the record of how it was paid for. */
export function carryPaymentRecord(spell: GameObject, permanent: GameObject): void {
  permanent.additionalCostsPaid = spell.additionalCostsPaid.slice();
  permanent.alternativeCostPaid = spell.alternativeCostPaid;
  permanent.manaSpent = spell.manaSpent;
  permanent.manaSpentColors = spell.manaSpentColors;
  permanent.phyrexianPaidWithLife = spell.phyrexianPaidWithLife;
  permanent.chosenPlayer = spell.chosenPlayer;
  permanent.wasCast = spell.wasCast;
}

/** CR 306.5b, 310.4b: the counters a permanent of this type enters with. */
export function enteringCounters(chars: Characteristics): [string, number][] {
  const out: [string, number][] = [];
  if (hasType(chars, CardType.PLANESWALKER) && chars.loyalty !== null) out.push(["loyalty", chars.loyalty]);
  if (hasType(chars, CardType.BATTLE) && chars.defense !== null) out.push(["defense", chars.defense]);
  return out;
}

/** A face-down object revealed as it leaves (cr708_face_down.reveal). */
function reveal(game: Game, obj: GameObject, why: string): void {
  const name = obj.card !== null ? printedCharacteristics(game, obj).name : "";
  logRecord(game, `face-down #${obj.id} is revealed: ${name} (${why})`, { kind: "reveal", player: obj.owner });
}

/** All incarnations of a commander resolve to one id (CR 903.10a). */
export function commanderIdentity(game: Game, id: ObjectId): ObjectId {
  return game.commanderOrigin.get(id) ?? id;
}

/** A battle's eligible protectors (CR 310.9a, 310.12a). */
export function eligibleProtectors(game: Game, obj: GameObject): PlayerId[] {
  const chars = characteristicsOf(game, obj);
  if (!hasType(chars, CardType.BATTLE)) return [];
  if (hasSubtype(chars, "Siege")) return opponents(game, obj.controller);
  return player(game, obj.controller).hasLost ? [] : [obj.controller];
}

interface ProtectorChooser {
  chooseProtector?(game: Game, playerId: PlayerId, battle: ObjectId, eligible: PlayerId[]): PlayerId;
}

/**
 * Designate a battle's protector (CR 310.9a, 310.11). Returns whether one
 * could be chosen; the SBAs bin a battle with none (CR 704.5x).
 */
export function chooseProtector(game: Game, obj: GameObject): boolean {
  const eligible = eligibleProtectors(game, obj);
  if (eligible.length === 0) {
    obj.protector = NO_PLAYER;
    return false;
  }
  const agent = agentFor(game, obj.controller) as ProtectorChooser | undefined;
  const picked = agent?.chooseProtector?.(game, obj.controller, obj.id, eligible.slice());
  obj.protector = picked !== undefined && eligible.includes(picked) ? picked : (eligible[0] as PlayerId);
  return true;
}
