/**
 * Dungeons (CR 309) and venturing into them (CR 701.49). Ported from
 * mtgfish/rules/cr300_card_types/cr309_dungeons.py.
 *
 * A dungeon begins outside the game and is brought into the command zone by
 * venturing. Its owner's venture marker sits on one room at a time; each
 * room's triggered ability fires as the marker moves in. Reaching the
 * bottommost room, and letting its ability leave the stack, completes it.
 *
 * Python reads the room graph and parses each room's effect at game time
 * from the card database. The browser has neither, so the definitions come
 * from the IR export (`ir_export dungeons` -> `ir/dungeons.ts`). A room
 * whose effect the parser couldn't read keeps an UNPARSED effect: it still
 * triggers and sits on the stack, and does nothing when it resolves.
 */

import { DUNGEONS, type DungeonDef } from "../../ir/dungeons";
import { complete } from "../../ir/decode";
import type { Ability } from "../../ir";
import type { FaceDef } from "../../kernel/card";
import { Color, Zone } from "../../kernel/enums";
import { event, EventKind } from "../../kernel/events";
import { agentFor, createObject, invalidateAllCharacteristics, logRecord, player, type Game } from "../../kernel/game";
import { isLive, type GameObject } from "../../kernel/gameobject";
import { NO_OBJECT, type PlayerId } from "../../kernel/ids";
import { emit } from "../emit";
import { strs } from "../ir_read";
import { removeFromZone } from "../zones";

export type { DungeonDef } from "../../ir/dungeons";

/** CR 701.49d: whether the dungeon has the named quality - its name, or a subtype. */
export function hasQuality(dungeon: DungeonDef, quality: string): boolean {
  const wanted = quality.toLowerCase();
  if (dungeon.name.toLowerCase() === wanted) return true;
  return strs(dungeon.face.type_line.subtypes).some((s) => s.toLowerCase() === wanted);
}

interface DungeonGame {
  dungeons?: readonly DungeonDef[];
}

/** CR 309.2: the dungeon cards outside the game that players may use (the export's, unless set). */
export function availableDungeons(game: Game): readonly DungeonDef[] {
  return (game as Game & DungeonGame).dungeons ?? DUNGEONS;
}

/** Replace the available dungeons (tests, or a different card pool). */
export function setAvailableDungeons(game: Game, dungeons: readonly DungeonDef[]): void {
  (game as Game & DungeonGame).dungeons = dungeons;
}

/** The card object behind a dungeon in the command zone: a synthetic card carrying its room abilities. */
interface DungeonCard {
  readonly oracle_id: string;
  readonly name: string;
  readonly layout: number;
  readonly faces: readonly FaceDef[];
  readonly abilities: readonly Ability[];
  readonly face_abilities: readonly (readonly Ability[])[];
  readonly mana_value: number;
  readonly color_identity: Color;
  readonly commander_legal: boolean;
  readonly dungeon: DungeonDef;
}

/** CR 309.3: the one dungeon this player owns in the command zone. */
export function currentDungeon(game: Game, playerId: PlayerId): GameObject | null {
  const obj = game.objects.get(player(game, playerId).dungeon);
  if (obj === undefined || !isLive(obj) || obj.zone !== Zone.COMMAND) return null;
  return game.command.includes(obj.id) ? obj : null;
}

export function definitionOf(obj: GameObject): DungeonDef | null {
  return (obj.card as Partial<DungeonCard> | null)?.dungeon ?? null;
}

interface DungeonChooser {
  chooseDungeon?(game: Game, playerId: PlayerId, names: string[]): number | string;
  chooseDungeonRoom?(game: Game, playerId: PlayerId, names: string[]): number | string;
}

/** CR 309.2a: the player chooses (the agent's `chooseDungeon`); else the first, in the rules' order. */
function chooseDungeon(game: Game, playerId: PlayerId, options: readonly DungeonDef[]): DungeonDef {
  const agent = agentFor(game, playerId) as DungeonChooser | null | undefined;
  if (agent && typeof agent.chooseDungeon === "function") {
    const picked = agent.chooseDungeon(game, playerId, options.map((d) => d.name));
    if (typeof picked === "number" && picked >= 0 && picked < options.length) return options[picked]!;
    const byName = options.find((d) => d.name === picked);
    if (byName !== undefined) return byName;
  }
  return options[0]!;
}

/** CR 309.5a, 701.49b: at a fork the player chooses an arrow (`chooseDungeonRoom`); else the first printed. */
function chooseRoom(game: Game, playerId: PlayerId, dungeon: DungeonDef, exits: readonly number[]): number {
  if (exits.length === 1) return exits[0]!;
  const agent = agentFor(game, playerId) as DungeonChooser | null | undefined;
  if (agent && typeof agent.chooseDungeonRoom === "function") {
    const picked = agent.chooseDungeonRoom(game, playerId, exits.map((i) => dungeon.rooms[i]!.name));
    if (typeof picked === "number" && picked >= 0 && picked < exits.length) return exits[picked]!;
    const byName = exits.find((i) => dungeon.rooms[i]!.name === picked);
    if (byName !== undefined) return byName;
  }
  return exits[0]!;
}

/** Put the venture marker on a room, which triggers its room ability (CR 309.4c). */
function moveMarker(game: Game, playerId: PlayerId, obj: GameObject, room: number): void {
  const p = player(game, playerId);
  p.ventureRoom = room;
  const name = definitionOf(obj)?.rooms[room]?.name ?? `room ${room + 1}`;
  logRecord(game, `${p.name} moves their venture marker to ${name}`, { kind: "dungeon", player: playerId });
  emit(game, event(EventKind.VENTURE_MARKER_MOVED, { objectId: obj.id, player: playerId, amount: room + 1 }));
}

/** CR 309.2a, 309.4a: bring a dungeon into the command zone, marker on its topmost room. */
function enterDungeon(game: Game, playerId: PlayerId, quality: string): GameObject | null {
  const options = availableDungeons(game).filter(
    (d) => d.mappable && (quality ? hasQuality(d, quality) : !d.entry_quality),
  );
  const p = player(game, playerId);
  if (options.length === 0) {
    logRecord(game, `${p.name} has no dungeon to venture into`, { kind: "unimplemented", player: playerId });
    return null;
  }
  const dungeon = chooseDungeon(game, playerId, options);
  const card: DungeonCard = {
    oracle_id: dungeon.oracle_id,
    name: dungeon.name,
    layout: 0,
    // The export writes only non-default fields (docs/ir-json.md); fill the rest.
    faces: [complete(dungeon.face) as unknown as FaceDef],
    abilities: dungeon.abilities,
    face_abilities: [],
    mana_value: 0,
    color_identity: Color.NONE,
    commander_legal: false,
    dungeon,
  };
  const obj = createObject(game, card, playerId, Zone.COMMAND);
  p.dungeon = obj.id;
  invalidateAllCharacteristics(game);
  logRecord(game, `${p.name} ventures into ${dungeon.name}`, { kind: "dungeon", player: playerId });
  moveMarker(game, playerId, obj, 0);
  return obj;
}

/**
 * CR 309.7: remove the dungeon from the game; its owner completes it. The
 * object is kept, superseded: a room ability may still read it (CR 701.49c).
 */
export function completeDungeon(game: Game, obj: GameObject): void {
  const p = player(game, obj.owner);
  const name = definitionOf(obj)?.name ?? "a dungeon";
  removeFromZone(game, obj);
  obj.supersededBy = obj.id;
  if (p.dungeon === obj.id) {
    p.dungeon = NO_OBJECT;
    p.ventureRoom = 0;
  }
  p.completedDungeons.push(name);
  invalidateAllCharacteristics(game);
  logRecord(game, `${p.name} completes ${name}`, { kind: "dungeon", player: p.id });
  emit(game, event(EventKind.DUNGEON_COMPLETED, { objectId: obj.id, player: p.id, data: [name] }));
}

/** CR 701.49: venture into the dungeon, or into [quality] (701.49d). */
export function venture(game: Game, playerId: PlayerId, quality = ""): void {
  if (player(game, playerId).hasLost) return;
  const obj = currentDungeon(game, playerId);
  const dungeon = obj !== null ? definitionOf(obj) : null;
  if (obj !== null && dungeon !== null) {
    const exits = dungeon.rooms[player(game, playerId).ventureRoom]?.leads_to ?? [];
    if (exits.length) {
      // CR 701.49b: follow an arrow.
      moveMarker(game, playerId, obj, chooseRoom(game, playerId, dungeon, exits));
      emit(game, event(EventKind.DUNGEON_VENTURED, { player: playerId, amount: 1 }));
      return;
    }
    // CR 701.49c: from the bottommost room, complete it and start another.
    completeDungeon(game, obj);
  }
  // CR 701.49a: enter a new dungeon.
  if (enterDungeon(game, playerId, quality) !== null) emit(game, event(EventKind.DUNGEON_VENTURED, { player: playerId, amount: 1 }));
}

/**
 * CR 704.5t: dungeons whose owner's marker is on the bottommost room and
 * that aren't the source of a room ability waiting or on the stack.
 */
export function finishedDungeons(game: Game, isSourceOnTheStack: (game: Game, obj: GameObject) => boolean): GameObject[] {
  const done: GameObject[] = [];
  for (const p of game.players) {
    const obj = currentDungeon(game, p.id);
    if (obj === null) continue;
    const dungeon = definitionOf(obj);
    if (dungeon === null || p.ventureRoom !== dungeon.bottommost) continue;
    if (isSourceOnTheStack(game, obj)) continue;
    done.push(obj);
  }
  return done;
}
