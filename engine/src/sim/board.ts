/**
 * The board, as a person would look at it, sampled through a replay.
 * Ported from mtgfish/sim/board.py.
 *
 * The log says what happened; the film says what the table looked like. The
 * whole visible position is sampled at every event that could have changed
 * it, each sample tagged with the log entry it is current as of, so a viewer
 * can scrub one and the other follows.
 *
 * Kept small the same three ways as Python's: identity is stored once (a
 * permanent is an index into `film.cards`), identical boards collapse into
 * the earlier sample, and nothing is computed that the engine wouldn't
 * compute anyway. The recorder only reads: a watched game is the same game
 * as an unwatched one.
 */

import { phaseName, stepName } from "../kernel/enums";
import { EventKind, type GameEvent } from "../kernel/events";
import { permanents, type Game } from "../kernel/game";
import { ObjectKind, type GameObject } from "../kernel/gameobject";
import { NO_OBJECT, type ObjectId } from "../kernel/ids";
import type { Player } from "../rules/cr100_game_concepts/player";
import { isCreature } from "../rules/cr200_parts_of_a_card/characteristics";
import { typeLineText } from "../rules/cr200_parts_of_a_card/typeline";
import type { Combat } from "../rules/cr500_turn_structure/combat";
import { characteristicsOf } from "../rules/cr600_spells_and_abilities/layers";

/** Events after which the board may look different (Python `SAMPLE_AFTER`). */
export const SAMPLE_AFTER: ReadonlySet<number> = new Set([
  EventKind.ZONE_CHANGE,
  EventKind.ENTERS_BATTLEFIELD,
  EventKind.LEAVES_BATTLEFIELD,
  EventKind.DIES,
  EventKind.TOKEN_CREATED,
  EventKind.CEASED_TO_EXIST,
  EventKind.CAST_SPELL,
  EventKind.SPELL_RESOLVED,
  EventKind.ABILITY_RESOLVED,
  EventKind.PUT_ON_STACK,
  EventKind.COUNTERED,
  EventKind.FIZZLED,
  EventKind.TAPPED,
  EventKind.UNTAPPED,
  EventKind.COUNTER_ADDED,
  EventKind.COUNTER_REMOVED,
  EventKind.ATTACHED,
  EventKind.UNATTACHED,
  EventKind.TRANSFORMED,
  EventKind.PHASED_OUT,
  EventKind.PHASED_IN,
  EventKind.CONTROL_CHANGED,
  EventKind.DAMAGE_DEALT,
  EventKind.COMBAT_DAMAGE_DEALT,
  EventKind.LIFE_GAINED,
  EventKind.LIFE_LOST,
  EventKind.LIFE_SET,
  EventKind.POISON_ADDED,
  EventKind.DREW_CARD,
  EventKind.DISCARDED,
  EventKind.MILLED,
  EventKind.ATTACKERS_DECLARED,
  EventKind.BLOCKERS_DECLARED,
  EventKind.REMOVED_FROM_COMBAT,
  EventKind.TURN_BEGAN,
  EventKind.STEP_BEGAN,
  EventKind.STEP_ENDED,
  EventKind.PLAYER_LOST,
]);

/** A distinct printed card or token face the viewer has to draw. `oracle_id` is empty for a token. */
export interface BoardCardRef {
  readonly name: string;
  readonly oracle_id: string;
  readonly type_line: string;
  readonly mana_cost: string;
  readonly token: boolean;
}

/** One permanent, with everything needed to draw it. Field names as Python's JSON. */
export interface BoardCard {
  readonly id: number;
  readonly card: number;
  readonly tapped: boolean;
  readonly power: number | null;
  readonly toughness: number | null;
  readonly damage: number;
  readonly counters: Readonly<Record<string, number>>;
  readonly sick: boolean;
  /** The player this creature attacks, or -1. */
  readonly attacking: number;
  readonly attacking_permanent: number;
  readonly blocking: readonly number[];
  readonly token: boolean;
  readonly commander: boolean;
  readonly face_down: boolean;
  readonly phased_out: boolean;
  readonly attached_to: number;
  /** No ability of this card was read. */
  readonly unreadable: boolean;
}

export interface BoardSeat {
  readonly player: number;
  readonly life: number;
  readonly hand: number;
  readonly library: number;
  readonly graveyard: number;
  readonly exile: number;
  readonly poison: number;
  readonly out: boolean;
  readonly permanents: readonly BoardCard[];
  /** Hand and graveyard contents: empty in a replay, always (nobody at the table saw them). */
  readonly hand_cards: readonly BoardCard[];
  readonly graveyard_cards: readonly BoardCard[];
}

export interface BoardStackItem {
  readonly card: number;
  readonly controller: number;
  readonly text: string;
}

/** The whole visible position at one moment. */
export interface BoardSnapshot {
  /** The log entry this board is current as of. */
  readonly frame: number;
  readonly turn: number;
  readonly phase: string;
  readonly step: string;
  readonly active: number;
  readonly seats: readonly BoardSeat[];
  /** Top of the stack last. */
  readonly stack: readonly BoardStackItem[];
}

export interface BoardFilm {
  readonly cards: BoardCardRef[];
  readonly snapshots: BoardSnapshot[];
}

/** The board as it stood at a log entry: the last snapshot at or before it. */
export function atFrame(film: BoardFilm, frame: number): BoardSnapshot | null {
  let found: BoardSnapshot | null = null;
  for (const s of film.snapshots) {
    if (s.frame > frame) break;
    found = s;
  }
  return found;
}

/** Samples the board as a second observer (`playGame`'s `watch`). */
export class BoardRecorder {
  readonly film: BoardFilm = { cards: [], snapshots: [] };
  private readonly index = new Map<string, number>();
  /** Python `same_board_as`, by the JSON of seats and stack: frame, turn, phase and step are left out on purpose. */
  private previousKey: string | null = null;

  /** `openZones` records hands and graveyards; false for a replay, so leaking a hand must be asked for. */
  constructor(private readonly openZones = false) {}

  readonly watch = (game: Game, e: GameEvent): void => {
    if (SAMPLE_AFTER.has(e.kind)) this.sample(game);
  };

  /** Take one snapshot, unless it would repeat the last one. */
  sample(game: Game): void {
    const snapshot = this.snapshot(game);
    const key = JSON.stringify([snapshot.seats, snapshot.stack]);
    if (key === this.previousKey) return;
    this.film.snapshots.push(snapshot);
    this.previousKey = key;
  }

  private snapshot(game: Game): BoardSnapshot {
    const combat = game.combat as Combat | null;
    const stack: BoardStackItem[] = [];
    for (const id of game.stack) {
      const item = this.stackItem(game, id);
      if (item !== null) stack.push(item);
    }
    return {
      frame: game.log.entries.length,
      turn: game.turn,
      phase: phaseName(game.phase),
      step: stepName(game.step),
      active: game.activePlayer,
      seats: game.players.map((p) => this.seat(game, p, combat)),
      stack,
    };
  }

  private hidden(game: Game, ids: readonly ObjectId[]): BoardCard[] {
    if (!this.openZones) return [];
    const out: BoardCard[] = [];
    for (const id of ids) {
      const obj = game.objects.get(id);
      if (obj !== undefined) out.push(this.permanent(game, obj, null));
    }
    return out;
  }

  private seat(game: Game, p: Player, combat: Combat | null): BoardSeat {
    let exile = 0;
    for (const id of game.exile) if (game.objects.get(id)?.owner === p.id) exile += 1;
    return {
      player: p.id,
      life: p.life,
      hand: p.hand.length,
      library: p.library.length,
      graveyard: p.graveyard.length,
      exile,
      poison: p.poison,
      out: p.hasLost,
      permanents: permanents(game, p.id).map((obj) => this.permanent(game, obj, combat)),
      hand_cards: this.hidden(game, p.hand),
      graveyard_cards: this.hidden(game, p.graveyard),
    };
  }

  private permanent(game: Game, obj: GameObject, combat: Combat | null): BoardCard {
    const chars = characteristicsOf(game, obj);
    let attacking = -1;
    let attackingPermanent = -1;
    let blocking: number[] = [];
    if (combat !== null) {
      const defender = combat.attacking.get(obj.id);
      if (defender !== undefined) {
        attacking = defender;
        attackingPermanent = combat.attackingPermanent.get(obj.id) ?? -1;
      }
      const blocked = combat.blocking.get(obj.id);
      if (blocked !== undefined && blocked.length > 0) blocking = blocked.slice();
    }
    const counters: Record<string, number> = {};
    for (const [k, v] of obj.counters) if (v) counters[k] = v;
    return {
      id: obj.id,
      card: this.cardIndex(game, obj),
      tapped: obj.tapped,
      power: chars.power,
      toughness: chars.toughness,
      damage: obj.damage,
      counters,
      sick: obj.summoningSick && isCreature(chars),
      attacking,
      attacking_permanent: attackingPermanent,
      blocking,
      token: obj.kind === ObjectKind.TOKEN,
      commander: obj.isCommander,
      face_down: obj.faceDown,
      phased_out: obj.phasedOut,
      attached_to: obj.attachedTo !== NO_OBJECT && obj.attachedTo ? obj.attachedTo : -1,
      unreadable: chars.abilities.length > 0 && chars.abilities.every((a) => a.unparsed === true),
    };
  }

  private stackItem(game: Game, id: ObjectId): BoardStackItem | null {
    const obj = game.objects.get(id);
    if (obj === undefined) return null;
    return { card: this.cardIndex(game, obj), controller: obj.controller, text: obj.ability?.text ?? "" };
  }

  /** Intern a card, so a snapshot stores an integer and not a name. */
  private cardIndex(game: Game, obj: GameObject): number {
    const chars = characteristicsOf(game, obj);
    const card = obj.card as { name?: string; oracle_id?: string } | null;
    const ref: BoardCardRef = {
      name: chars.name || card?.name || "",
      oracle_id: card?.oracle_id ?? "",
      type_line: typeLineText(chars.typeLine),
      mana_cost: chars.hasManaCost ? (chars.manaCost.symbols ?? []).map((sym) => sym.text ?? "").join("") : "",
      token: obj.kind === ObjectKind.TOKEN,
    };
    const key = JSON.stringify(ref);
    let index = this.index.get(key);
    if (index === undefined) {
      index = this.film.cards.length;
      this.index.set(key, index);
      this.film.cards.push(ref);
    }
    return index;
  }
}
